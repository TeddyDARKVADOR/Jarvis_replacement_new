"""
resilience/ — aucun appel Gemini ne pend, aucun modèle en panne n'éteint une
fonction, et la voix Live a un modèle de secours.

Adapté de `core/gemini.py` de Mark LV (FatihMakes, CC BY-NC 4.0, la licence de
ce dépôt) : l'échelle de modèles, les délais imposés et les mises au repos sont
les siens. Ce qui change : Mark LV a réécrit seize fichiers pour qu'ils passent
par son module ; ici `actions/` est figé (contrat V2), alors `install()`
enveloppe `Models.generate_content` du SDK. Les onze appels existants
(actions/, main.py, cognition/, core/code_workspace/) en profitent sans être
touchés. Le tour Live « jetable » de Mark LV n'est pas repris : la cognition a
déjà sa propre chaîne hors Gemini (cognition/providers.py).

Paquet racine au sens du contrat : main.py l'attache par un hook (`attach`).
Supprimer le dossier rend JARVIS identique à avant : pas de délai, pas de
repli, un seul modèle Live.

CE QUE FAIT L'ENVELOPPE
  1. Délai : sans délai fixé par l'appelant, le SDK attend indéfiniment. On en
     impose un (30 s, 90 s pour une image ou un long texte ; jamais moins de
     10 s, le minimum que l'API accepte).
  2. Échelle : le modèle demandé d'abord, puis ceux de son échelon (rapide,
     raisonnement, recherche ancrée), dans l'ordre mesuré sur notre clé.
  3. Repos : quota du jour épuisé (429 « PerDay ») → jusqu'à minuit, heure du
     Pacifique ; de la minute → 1 min ; autre 429 → 5 min ; ne répond pas (503, 504, délai
     dépassé) → 30 min ; absent ou interdit à cette clé (404, 403) → 6 h. Un
     modèle au repos n'est pas réessayé à chaque appel : c'est là que le temps
     se perdait.
  Une erreur qui tient à la requête (argument invalide, contenu bloqué) remonte
  telle quelle : un autre modèle n'y changerait rien.

Mesuré le 2026-10-13 sur notre clé (« Reply with the single word: ok ») :
    gemini-2.5-flash          0.42 s    gemini-3-flash-preview   1.42 s
    gemini-2.5-flash-lite     0.67 s    gemini-3.6-flash         1.61 s
    gemini-flash-lite-latest  0.70 s    gemini-3.5-flash         3.73 s
    gemini-3.5-flash-lite     0.73 s    gemini-3.1-flash-lite   10.15 s
    gemini-3.7-flash, gemini-3.8-flash, gemini-flash-latest : 503
Les modèles en 503 restent en bas plutôt que supprimés : en bonne santé, ce
sont de vrais échelons ; en panne, le repos les écarte après un essai.
"""
from __future__ import annotations

import functools
import threading
import time

FAST, SMART, SEARCH = "fast", "smart", "search"

LADDERS: dict[str, tuple[str, ...]] = {
    FAST: ("gemini-2.5-flash-lite", "gemini-flash-lite-latest",
           "gemini-3.5-flash-lite", "gemini-2.5-flash", "gemini-3.1-flash-lite",
           "gemini-3-flash-preview", "gemini-3.6-flash",
           "gemini-3.7-flash", "gemini-3.8-flash", "gemini-flash-latest"),
    SMART: ("gemini-2.5-flash", "gemini-3-flash-preview", "gemini-3.6-flash",
            "gemini-3.5-flash", "gemini-3.7-flash", "gemini-3.8-flash",
            "gemini-flash-latest", "gemini-2.5-flash-lite", "gemini-3.5-flash-lite"),
    # L'ancrage Google exige un modèle texte REST qui le prend en charge.
    SEARCH: ("gemini-2.5-flash", "gemini-3.5-flash", "gemini-3-flash-preview",
             "gemini-2.5-flash-lite", "gemini-flash-latest"),
}

#: La conversation et UN secours. Les modèles Live ne sont pas interchangeables
#: (champs de config, comportement) ; la clé expose aussi transcribe-live et
#: live-translate, qui ne sont pas des assistants. Le second est le modèle
#: audio natif que Mark LV utilisait avant 3.1, connu pour fonctionner.
LIVE_MODELS: tuple[str, ...] = (
    "models/gemini-3.1-flash-live-preview",
    "models/gemini-2.5-flash-native-audio-preview-12-2025",
)

DEFAULT_TIMEOUT_MS = 30_000
LONG_TIMEOUT_MS = 90_000
MIN_TIMEOUT_MS = 10_000          # l'API refuse en dessous : « Minimum allowed deadline is 10s »
LONG_TEXT_CHARS = 20_000

QUOTA_REST_S = 5 * 60
UNAVAILABLE_REST_S = 30 * 60
GONE_REST_S = 6 * 60 * 60

_cooldown: dict[str, float] = {}
_lock = threading.Lock()
_installed = False
_log = print                     # remplacé par le journal du HUD dans attach()

# ── plusieurs clés Gemini : multiplier le quota en basculant de clé ───────────
# Plusieurs comptes Gemini → plusieurs clés. Quand une clé épuise son quota du
# jour (429 « PerDay »), on la met au repos jusqu'à minuit (Pacifique) et on
# passe à la suivante. Les repos de modèles (_cooldown) étant nommés par modèle
# et non par clé, on les efface au changement de clé : la nouvelle clé repart
# avec tous ses modèles. Avec une seule clé, rien ne change (V1 intacte).
_gemini_keys: list[str] = []
_key_cooldown: dict[str, float] = {}
_current_key: str | None = None


def set_gemini_keys(keys: list[str]) -> None:
    """Déclare le pool ordonné de clés (sans doublon ni vide). Rejouée à chaque
    connexion depuis main._get_api_key : un ajout de clé est pris à chaud."""
    global _gemini_keys
    clean: list[str] = []
    for k in keys or []:
        k = str(k or "").strip()
        if k and k not in clean:
            clean.append(k)
    with _lock:
        _gemini_keys = clean
        for dead in [k for k in _key_cooldown if k not in clean]:
            _key_cooldown.pop(dead, None)


def current_gemini_key() -> str | None:
    """La clé active : la première qui ne se repose pas. Si toutes se reposent,
    celle dont le repos finit le plus tôt (on tente quand même). Au changement
    de clé, les repos de modèles sont effacés (ils valaient pour l'ancienne)."""
    global _current_key
    with _lock:
        if not _gemini_keys:
            return None
        now = time.monotonic()
        fresh = [k for k in _gemini_keys if _key_cooldown.get(k, 0.0) <= now]
        chosen = fresh[0] if fresh else min(_gemini_keys, key=lambda k: _key_cooldown.get(k, 0.0))
        if chosen != _current_key:
            _current_key = chosen
            _cooldown.clear()
        return chosen


def rest_gemini_key(seconds: float, key: str | None = None) -> None:
    """Met une clé (la courante par défaut) au repos pour `seconds`."""
    with _lock:
        k = key or _current_key
        if k:
            _key_cooldown[k] = time.monotonic() + seconds


def gemini_keys_status() -> list[dict]:
    """Pour l'affichage : chaque clé masquée, si elle se repose et jusqu'à quand."""
    now = time.monotonic()
    out = []
    with _lock:
        for k in _gemini_keys:
            until = _key_cooldown.get(k, 0.0)
            tail = k[-4:] if len(k) >= 4 else k
            out.append({"key": f"…{tail}", "active": k == _current_key,
                        "resting": until > now, "in_s": max(0.0, until - now)})
    return out


# ── classement des erreurs ───────────────────────────────────────────────────

def is_quota_error(err: str) -> bool:
    return "429" in err or "RESOURCE_EXHAUSTED" in err or "quota" in err.lower()


def is_unavailable_error(err: str) -> bool:
    low = err.lower()
    return ("503" in err or "504" in err or "unavailable" in low
            or "deadline_exceeded" in low or "timed out" in low or "timeout" in low)


def is_gone_error(err: str) -> bool:
    low = err.lower()
    return ("404" in err or "403" in err or "not found" in low
            or "is not supported" in low or "permission" in low)


def seconds_until_pacific_midnight(now=None) -> float:
    """Les quotas journaliers de Gemini repartent à minuit, heure du Pacifique."""
    from datetime import datetime, timedelta
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("America/Los_Angeles")
    except Exception:                                  # sans base de fuseaux : UTC−8, au pire une heure trop tard
        from datetime import timezone
        tz = timezone(timedelta(hours=-8))
    now = now or datetime.now(tz)
    nxt = (now + timedelta(days=1)).replace(hour=0, minute=1, second=0, microsecond=0)
    return max(60.0, (nxt - now).total_seconds())


def quota_rest(err: str) -> float:
    """Combien de temps laisser un modèle au repos après un 429. Le corps de l'erreur nomme le quota
    (« GenerateRequestsPerDayPerProjectPerModel-FreeTier », valeur 20 pour gemini-3.5-flash, mesuré le
    2026-10-16) : un quota du jour ne revient qu'à minuit (Pacifique) — le réessayer toutes les 5 min ne
    faisait que perdre du temps ; un quota de la minute revient en une minute."""
    if "PerDay" in err:
        return seconds_until_pacific_midnight()
    if "PerMinute" in err:
        return 60.0
    return QUOTA_REST_S


def _bare(model: str) -> str:
    return str(model or "").removeprefix("models/")


def rest(model: str, seconds: float) -> None:
    with _lock:
        _cooldown[_bare(model)] = time.monotonic() + seconds


def resting(model: str) -> bool:
    with _lock:
        until = _cooldown.get(_bare(model), 0.0)
        if until and time.monotonic() < until:
            return True
        _cooldown.pop(_bare(model), None)
        return False


def classify(model: str, err: str) -> str | None:
    """Met le modèle au repos selon l'erreur. Renvoie la raison, ou None quand
    l'erreur tient à la requête et qu'un autre modèle n'y changerait rien."""
    if is_quota_error(err):
        rest(model, quota_rest(err))
        return "quota"
    if is_gone_error(err):
        rest(model, GONE_REST_S)
        return "absent"
    if is_unavailable_error(err):
        rest(model, UNAVAILABLE_REST_S)
        return "ne répond pas"
    return None


# ── échelle des appels ponctuels ─────────────────────────────────────────────

def _uses_search(config) -> bool:
    tools = (config.get("tools") if isinstance(config, dict)
             else getattr(config, "tools", None)) or []
    for t in tools:
        if isinstance(t, dict):
            if t.get("google_search") is not None or t.get("googleSearch") is not None:
                return True
        elif getattr(t, "google_search", None) is not None:
            return True
    return False


def tier_of(model: str, config=None) -> str | None:
    """L'échelon d'un modèle demandé ; None = pas d'échelle (image, TTS…)."""
    m = _bare(model)
    if not m.startswith("gemini-") or any(k in m for k in ("image", "tts", "embedding", "live", "audio")):
        return None
    if config is not None and _uses_search(config):
        return SEARCH
    return FAST if "lite" in m else SMART


def ladder_for(model: str, config=None) -> list[str]:
    """Le modèle demandé d'abord, puis son échelon, sans ceux qui se reposent.
    Si tout se repose, l'échelle entière : refuser d'essayer n'est jamais mieux."""
    tier = tier_of(model, config)
    if tier is None:
        return [model]
    want = _bare(model)
    order = [model] + [m for m in LADDERS[tier] if m != want]
    awake = [m for m in order if not resting(m) and _quota("allowed", m) is not False]
    return awake or order


def _quota(fn: str, *args):
    """Le registre commun des quotas (quota/), s'il est là ; None sinon — resilience garde alors ses repos seuls."""
    try:
        import quota
        return getattr(quota, fn)(*args)
    except Exception:
        return None


def _is_long(contents) -> bool:
    items = contents if isinstance(contents, (list, tuple)) else [contents]
    size = 0
    for it in items:
        if isinstance(it, str):
            size += len(it)
        elif isinstance(it, (list, tuple)):
            if _is_long(it):
                return True
        else:
            return True              # image, fichier, Part : plus long à traiter
    return size > LONG_TEXT_CHARS


def with_timeout(config, ms: int):
    """La config avec un délai, sauf si l'appelant en a déjà fixé un."""
    ms = max(MIN_TIMEOUT_MS, int(ms))
    if config is None:
        return {"http_options": {"timeout": ms}}
    if isinstance(config, dict):
        ho = config.get("http_options") or {}
        ho = dict(ho) if isinstance(ho, dict) else {"timeout": getattr(ho, "timeout", None)}
        if ho.get("timeout"):
            return config
        ho["timeout"] = ms
        return {**config, "http_options": ho}
    ho = getattr(config, "http_options", None)
    if ho is not None and getattr(ho, "timeout", None):
        return config
    try:
        from google.genai import types
        new_ho = ho.model_copy(update={"timeout": ms}) if ho is not None else types.HttpOptions(timeout=ms)
        return config.model_copy(update={"http_options": new_ho})
    except Exception:
        return config


def _client_has_timeout(models_obj) -> bool:
    try:
        return bool(models_obj._api_client._http_options.timeout)
    except Exception:
        return False


def _prepare(self, model, contents, config):
    if not _client_has_timeout(self):
        config = with_timeout(config, LONG_TIMEOUT_MS if _is_long(contents) else DEFAULT_TIMEOUT_MS)
    return ladder_for(model, config), config


def _failed(model: str, e: Exception, remaining: int) -> bool:
    """Journalise un échec ; True s'il faut passer à l'échelon suivant."""
    reason = classify(model, f"{type(e).__name__}: {e}")    # ReadTimeout a parfois un str() vide
    _quota("note_error", _bare(model), str(e))
    if reason is None or remaining == 0:
        return False
    _log(f"SYS: Gemini {_bare(model)} — {reason}, j'essaie le suivant.")
    return True


def wrap_sync(original):
    @functools.wraps(original)
    def generate_content(self, *, model, contents, config=None, **kw):
        ladder, config = _prepare(self, model, contents, config)
        for i, m in enumerate(ladder):
            try:
                out = original(self, model=m, contents=contents, config=config, **kw)
                _quota("note_ok", _bare(m))
                return out
            except Exception as e:
                if not _failed(m, e, len(ladder) - i - 1):
                    raise
    generate_content.__resilience__ = True
    return generate_content


def wrap_async(original):
    @functools.wraps(original)
    async def generate_content(self, *, model, contents, config=None, **kw):
        ladder, config = _prepare(self, model, contents, config)
        for i, m in enumerate(ladder):
            try:
                out = await original(self, model=m, contents=contents, config=config, **kw)
                _quota("note_ok", _bare(m))
                return out
            except Exception as e:
                if not _failed(m, e, len(ladder) - i - 1):
                    raise
    generate_content.__resilience__ = True
    return generate_content


def install() -> bool:
    """Enveloppe le SDK une seule fois. Sans SDK, ne fait rien."""
    global _installed
    if _installed:
        return True
    try:
        from google.genai import models
    except Exception:
        return False
    if not getattr(models.Models.generate_content, "__resilience__", False):
        models.Models.generate_content = wrap_sync(models.Models.generate_content)
    if not getattr(models.AsyncModels.generate_content, "__resilience__", False):
        models.AsyncModels.generate_content = wrap_async(models.AsyncModels.generate_content)
    _installed = True
    return True


# ── voix Live ────────────────────────────────────────────────────────────────

def live_model(default: str) -> str:
    """Le modèle Live à ouvrir : le premier qui ne se repose pas. `default`
    (LIVE_MODEL de main.py) mène toujours."""
    order = [default] + [m for m in LIVE_MODELS if _bare(m) != _bare(default)]
    for m in order:
        if not resting(m):
            return m
    return order[0]


def note_live_failure(model: str, err: str) -> bool:
    """True quand l'échec justifie le modèle suivant. Seuls le quota et l'accès
    font avancer : une clé refusée ou un réseau coupé ne sont pas la faute du
    modèle, et changer de modèle ne ferait que heurter le même mur."""
    if is_quota_error(err):
        rest(model, quota_rest(err))
        # Mur du jour (429 PerDay) : la clé courante est épuisée jusqu'à minuit.
        # Avec plusieurs clés, on la met au repos pour basculer sur la suivante.
        if "PerDay" in err and len(_gemini_keys) > 1:
            rest_gemini_key(seconds_until_pacific_midnight())
            _log("SYS: quota du jour atteint sur cette clé Gemini — bascule sur une autre clé.")
    elif is_gone_error(err) and "API key" not in err:
        rest(model, GONE_REST_S)
    else:
        return False
    _log(f"SYS: voix {_bare(model)} indisponible — bascule sur le modèle de secours.")
    return True


def status() -> list[dict]:
    now = time.monotonic()
    with _lock:
        return [{"model": m, "rest_s": round(t - now)} for m, t in _cooldown.items() if t > now]


def attach(jarvis) -> None:
    """Hook de main.py : enveloppe le SDK et donne à JarvisLive le choix du
    modèle Live. Ne lève jamais."""
    global _log
    ui = getattr(jarvis, "ui", None)
    if ui is not None and hasattr(ui, "write_log"):
        _log = ui.write_log
    install()
    jarvis._live_model = live_model
    jarvis._note_live_failure = note_live_failure
