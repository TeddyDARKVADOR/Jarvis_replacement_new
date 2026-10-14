"""
nightshift/briefing.py — une phrase sur la nuit au briefing du matin, une seule fois.

    clause(report, seen, now)  pur : le texte à ajouter et l'état à retenir
    prefetch()                 lecture du rapport en fond, dès le démarrage
    clause_now()               ce que main.py ajoute au briefing (n'attend pas plus d'1 s)

QUAND SE TAIRE
  - pas de rapport (hors-ligne, branche nightshift-data pas encore créée) ;
  - ce rapport a déjà été annoncé ;
  - rien de nouveau : pas de régression, de réparation, de suite cassée ni de
    nouvelle remarque ruff — en particulier tout vert la nuit d'avant aussi.
    L'onglet NUIT montre tout, toujours.
QUAND PARLER
  - une régression, une suite en erreur ou hors délai, une réparation, une
    nouvelle remarque ruff, un échec sans référence (suite qui tourne pour la
    première fois), un correctif validé (PR ouverte, ou dry run), le retour au
    vert, la première nuit ;
  - le dernier rapport a plus de 48 h : le veilleur ne tourne plus (GitHub
    désactive une planification après 60 jours sans activité du dépôt).

Le texte est construit à partir des champs structurés du rapport — nombres,
noms de suites, numéros de contrôles, commits — chacun filtré. Jamais à partir
d'un texte libre du rapport : c'est une donnée, pas une consigne pour Gemini.
"""
from __future__ import annotations

import re
import threading
import time
from datetime import datetime

from nightshift import fetch, state
from nightshift.suites import short

STALE_S = 48 * 3600
WAIT_S = 1.0

_SUITE = re.compile(r"^[a-z0-9_.]{1,60}( --selftest)?$")
_CHECK = re.compile(r"^\d{1,3}[a-z]?$")
_COMMIT = re.compile(r"^[0-9a-f]{7,40}$")
_RUFF = re.compile(r"^[A-Z]{1,3}\d{3}$")
_PATH = re.compile(r"^[a-z0-9_./-]{1,80}\.py$")

_ready = threading.Event()
_started = False
_start_lock = threading.Lock()


def _suite(name) -> str:
    return short(name) if isinstance(name, str) and _SUITE.match(name) else "une suite"


def _ref(x: dict) -> str:
    check = str(x.get("check", ""))
    return f"{_suite(x.get('suite'))} n°{check}" if _CHECK.match(check) else _suite(x.get("suite"))


def _since(x: dict) -> str:
    c = str(x.get("since_commit") or "")
    return f" depuis le commit {c[:7]}" if _COMMIT.match(c) else ""


def _task(t: dict) -> str:
    if t.get("source") == "ruff":
        code, path = str(t.get("code", "")), str(t.get("path", ""))
        return (f"{code} dans {path}" if _RUFF.match(code) and _PATH.match(path) else "une remarque ruff")
    return _ref({"suite": t.get("suite"), "check": t.get("check")})


def _list(items: list[str], limit: int = 2) -> str:
    more = f" et {len(items) - limit} autre(s)" if len(items) > limit else ""
    return ", ".join(items[:limit]) + more


def sentence(r: dict) -> str:
    """Les faits de la nuit en une phrase, ou "" s'il n'y a rien de nouveau."""
    if r.get("status") == "failed":
        return "« le veilleur n'a rien pu mesurer cette nuit : voir l'onglet NUIT »."
    suites = [s for s in r.get("suites") or [] if isinstance(s, dict)]
    ran = [s for s in suites if s.get("status") != "skipped"]
    green = [s for s in ran if s.get("status") in ("ok", "known_failure")]
    regs = [x for x in r.get("regressions") or [] if isinstance(x, dict)]
    reps = [x for x in r.get("repaired") or [] if isinstance(x, dict)]
    broken = [s for s in ran if s.get("status") in ("error", "timeout")]
    new_lint = (r.get("lint") or {}).get("new") or []
    first = r.get("previous") is None
    unref = [] if first else [x for x in r.get("unreferenced") or [] if isinstance(x, dict)]
    back_to_green = r.get("status") == "success" and r.get("green_streak") == 1 and not first
    task = r.get("task") if isinstance(r.get("task"), dict) else {}
    fixed = task.get("outcome") == "patch_validated"
    if not (regs or reps or broken or new_lint or unref or first or back_to_green or fixed):
        return ""
    parts = [f"{len(green)}/{len(ran)} suites vertes"]
    if first:
        parts[0] = "première nuit du veilleur : " + parts[0]
    if regs:
        parts.append("régression : " + _list([_ref(x) + _since(x) for x in regs]))
    if broken:
        parts.append("en erreur : " + _list([_suite(s.get("name")) for s in broken]))
    if unref:
        parts.append("rouge sans référence : " + _list([_ref(x) for x in unref]))
    if reps:
        parts.append("réparé : " + _list([_ref(x) for x in reps]))
    if new_lint and isinstance(new_lint, list):
        parts.append(f"{len(new_lint)} nouvelle(s) remarque(s) ruff")
    if fixed:
        parts.append(("une PR attend d'être relue : " if r.get("pull_request_url")
                      else "correctif validé, sans PR (dry run) : ") + _task(task))
    if back_to_green:
        parts.append("tout est revenu au vert")
    return "« " + " ; ".join(parts) + " »."


def _timestamp(text) -> float | None:
    try:
        return datetime.fromisoformat(str(text)).timestamp()
    except (TypeError, ValueError):
        return None


def clause(report: dict | None, seen: dict, now: float | None = None) -> tuple[str, dict]:
    """(texte à ajouter au briefing, état à retenir). Pur."""
    if not report:
        return "", {}
    run_id = str(report.get("run_id", ""))
    now = time.time() if now is None else now
    finished = _timestamp(report.get("finished_at"))
    if finished is not None and now - finished > STALE_S:
        if seen.get("stale_warned") == run_id:
            return "", {}
        day = datetime.fromtimestamp(finished).strftime("%d/%m")
        return (f" Also mention, in one short sentence, that the overnight checks of your own code have "
                f"not run since {day}: worth a look at the repository's Actions tab.",
                {"stale_warned": run_id, "announced": run_id})
    if seen.get("announced") == run_id:
        return "", {}
    text = sentence(report)
    if not text:
        return "", {"announced": run_id}
    return (f" Also mention, in one short sentence, last night's automated checks of your own code: {text}",
            {"announced": run_id})


def prefetch() -> None:
    """Lit le rapport en fond, une fois par processus : le briefing n'attend pas le réseau."""
    global _started
    with _start_lock:
        if _started:
            return
        _started = True

    def work() -> None:
        try:
            fetch.status(timeout=4.0)
        except Exception:
            pass
        finally:
            _ready.set()

    threading.Thread(target=work, name="nightshift-prefetch", daemon=True).start()


def clause_now(path=None, now: float | None = None) -> str:
    """Le texte que main.py ajoute à la phase 1 du briefing, ou "". Ne lève jamais.

    Attend le préchargement au plus WAIT_S, puis lit le rapport en cache. Ce
    qui est annoncé est noté : le même rapport ne l'est pas deux fois."""
    try:
        if _started:
            _ready.wait(WAIT_S)
        else:
            fetch.status(timeout=WAIT_S, path=path)
        data = state.load(path)
        report = (data.get("cache") or {}).get("report")
        text, remember = clause(report if fetch.valid(report) else None, data.get("briefing") or {}, now)
        if remember:
            state.update("briefing", remember, path)
        return text
    except Exception:
        return ""
