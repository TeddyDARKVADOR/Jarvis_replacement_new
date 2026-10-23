"""
pilot/brain.py — le modèle qui regarde l'écran et choisit l'action suivante.

Une requête par étape, sans état : la consigne, l'historique des actions (en
texte), l'observation (éléments numérotés) et la capture. Le modèle répond par
UN appel de fonction (mode ANY). Sans état, la requête se sérialise telle
quelle : sur un PC relié, Oracle l'exécute avec sa clé (server/assist.py,
POST /api/pilot/step) — le PC n'en a pas.

Gemini (REST, bibliothèque standard), dans l'ordre de MODELS : les Flash-Lite
d'abord (500 requêtes gratuites par jour et par modèle), le Flash à 20 par
jour en dernier. Un 429 « PerDay » écarte le modèle jusqu'à minuit (heure du
Pacifique) sans attendre ; 429 de la minute et 503 : réessayés. L'outil
Computer Use de Google répond aussi avec la clé gratuite (vérifié le
2026-10-16), mais nos propres fonctions désignent les éléments de l'arbre
d'accessibilité par leur numéro, plus sûr que des coordonnées, et valables
pour tout modèle multimodal.
"""
from __future__ import annotations

import base64
import json
import re
import time
import urllib.error
import urllib.request

URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
# Ordre fixé par le quota gratuit, lu dans les erreurs 429 de Google le 2026-10-16 : chaque modèle a son
# propre compteur par jour — gemini-3.5-flash (et 3.6/3.7/3.8, 3-flash-preview) : 20 requêtes ; les
# Flash-Lite : 500. Sur une étape réelle du pilote (capture d'une messagerie), 2.5-flash-lite et 2.5-flash
# ont choisi la bonne action en 1 à 3 s, 3.5-flash-lite aussi (et demande confirmation avant d'envoyer),
# 3.1-flash-lite juste mais lent (12-20 s, 503 fréquents). Le modèle à 20 par jour vient en dernier.
MODELS = ("gemini-2.5-flash-lite", "gemini-3.5-flash-lite", "gemini-2.5-flash", "gemini-3.1-flash-lite",
          "gemini-3.5-flash")

SYSTEM = """You operate this computer for the user, like a careful human assistant, one action at a time.
Each step you get: the task, what you already did, the foreground window, the list of UI elements of that
window (from the OS accessibility tree, numbered, with positions 0-1000 on the screenshot) and a screenshot.

How to work:
- Prefer the most reliable path: an app's deep link or URL (call url_schemes / find_apps / visited_sites
  first when it helps, then open), keyboard shortcuts you are sure of, then elements by number, and only
  then clicks by coordinates on the screenshot.
- Act on elements by their number whenever the element is listed; use x,y (0-1000) only for what the list
  does not contain.
- When the user names a kind of site or tool (« mon site UML », « mon outil de diagrammes »), call
  visited_sites to find the one they already use — try several words (the category in both languages,
  « diagram », and the names of tools of that kind you know) — and open the most used one.
- After each action, check on the new screenshot that it worked before going on. If something unexpected
  appears (dialog, login, error), deal with it or stop with fail and say what you saw.
- Never type passwords, payment data or one-time codes; stop with need_info instead.
- Before anything irreversible or that others will see (sending a message or mail, posting, deleting,
  paying, submitting a form, accepting an invite), call confirm with a precise summary of exactly what will
  happen (recipient and full text for a message). Only proceed if the answer is yes.
- Never replace the existing content of a document or a field that already has text: click at the right
  place and type, or select precisely what must change.
- Finish with done (what you did, in the user's language) as soon as the task is complete, or fail / need_info.
Keep the user's language for every text you write to them."""

TOOLS = [
    {"name": "open", "description": "Open a URL, a deep link (scheme: from url_schemes), a file path or an installed app by name.",
     "parameters": {"type": "OBJECT", "properties": {"target": {"type": "STRING"}}, "required": ["target"]}},
    {"name": "find_apps", "description": "Installed apps whose name matches.",
     "parameters": {"type": "OBJECT", "properties": {"query": {"type": "STRING"}}, "required": ["query"]}},
    {"name": "url_schemes", "description": "URL schemes registered on this computer that match (e.g. teams → msteams, ms-teams).",
     "parameters": {"type": "OBJECT", "properties": {"query": {"type": "STRING"}}, "required": ["query"]}},
    {"name": "visited_sites", "description": "Websites the user already visited that match words (local browser history), most used first.",
     "parameters": {"type": "OBJECT", "properties": {"query": {"type": "STRING"}}, "required": ["query"]}},
    {"name": "focus_window", "description": "Bring the window whose title contains this text to the front.",
     "parameters": {"type": "OBJECT", "properties": {"title": {"type": "STRING"}}, "required": ["title"]}},
    {"name": "click", "description": "Click an element by number, or a point x,y (0-1000 on the screenshot).",
     "parameters": {"type": "OBJECT", "properties": {
         "element": {"type": "INTEGER"}, "x": {"type": "NUMBER"}, "y": {"type": "NUMBER"},
         "double": {"type": "BOOLEAN"}, "right": {"type": "BOOLEAN"}}}},
    {"name": "type_text", "description": "Type text, into an element by number (empty field) or at the current cursor. submit=true presses Enter after.",
     "parameters": {"type": "OBJECT", "properties": {
         "text": {"type": "STRING"}, "element": {"type": "INTEGER"}, "submit": {"type": "BOOLEAN"}},
         "required": ["text"]}},
    {"name": "press_keys", "description": "Press a key or a shortcut: enter, tab, escape, ctrl+l, ctrl+shift+n, alt+f4…",
     "parameters": {"type": "OBJECT", "properties": {"keys": {"type": "STRING"}}, "required": ["keys"]}},
    {"name": "scroll", "description": "Scroll up or down, over an element by number or the window centre.",
     "parameters": {"type": "OBJECT", "properties": {
         "direction": {"type": "STRING", "description": "up | down"}, "amount": {"type": "INTEGER"},
         "element": {"type": "INTEGER"}}, "required": ["direction"]}},
    {"name": "wait", "description": "Wait for something to load (1-10 s).",
     "parameters": {"type": "OBJECT", "properties": {"seconds": {"type": "NUMBER"}}}},
    {"name": "confirm", "description": "Ask the user to approve an irreversible or visible-to-others action. Returns yes or no.",
     "parameters": {"type": "OBJECT", "properties": {"summary": {"type": "STRING"}}, "required": ["summary"]}},
    {"name": "done", "description": "The task is complete. Summary for the user, in their language.",
     "parameters": {"type": "OBJECT", "properties": {"summary": {"type": "STRING"}}, "required": ["summary"]}},
    {"name": "fail", "description": "Stop: the task cannot be done. Say why, in the user's language.",
     "parameters": {"type": "OBJECT", "properties": {"reason": {"type": "STRING"}}, "required": ["reason"]}},
    {"name": "need_info", "description": "Stop and ask the user one question (missing information, password, choice).",
     "parameters": {"type": "OBJECT", "properties": {"question": {"type": "STRING"}}, "required": ["question"]}},
]


class BrainError(RuntimeError):
    pass


def build_request(task: str, history: list[str], observation_text: str, image: bytes, hints: list[str]) -> dict:
    """Le corps de la requête Gemini — sérialisable, identique en local et via Oracle. Pur."""
    text = [f"TASK: {task}"]
    if hints:
        text.append("Approaches that worked before for similar tasks (hints only — check the screen):\n- "
                    + "\n- ".join(hints))
    text.append("DONE SO FAR:\n" + ("\n".join(history[-14:]) if history else "nothing yet"))
    text.append("NOW:\n" + observation_text)
    text.append("Choose the single next action.")
    parts: list[dict] = [{"text": "\n\n".join(text)}]
    if image:
        parts.append({"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(image).decode("ascii")}})
    return {"system_instruction": {"parts": [{"text": SYSTEM}]},
            "contents": [{"role": "user", "parts": parts}],
            "tools": [{"function_declarations": TOOLS}],
            "tool_config": {"function_calling_config": {"mode": "ANY"}},
            "generationConfig": {"temperature": 0.1}}


def parse(response: dict) -> tuple[str, dict]:
    """(fonction, arguments) du premier appel. Pur."""
    try:
        parts = response["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError, TypeError):
        raise BrainError(f"réponse sans action : {json.dumps(response)[:300]}")
    for p in parts:
        call = p.get("functionCall") or p.get("function_call")
        if call and call.get("name"):
            return str(call["name"]), dict(call.get("args") or {})
    text = " ".join(p.get("text", "") for p in parts).strip()
    raise BrainError(f"le modèle n'a choisi aucune action : {text[:200]}")


RETRY_PAUSES = (2.0, 5.0, 0.0)
_spent: dict[str, float] = {}          # modèle → fin du repos (quota du jour épuisé)


def _until_pacific_midnight() -> float:
    from datetime import datetime, timedelta
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo("America/Los_Angeles"))
    except Exception:
        from datetime import timezone
        now = datetime.now(timezone(timedelta(hours=-8)))
    nxt = (now + timedelta(days=1)).replace(hour=0, minute=1, second=0, microsecond=0)
    return max(60.0, (nxt - now).total_seconds())


def _quota(fn: str, *args):
    """Le registre commun des quotas (quota/) s'il est là ; None sinon."""
    try:
        import quota
        return getattr(quota, fn)(*args)
    except Exception:
        return None


def retry_delay(raw: bytes) -> float | None:
    """Le « retryDelay » d'une erreur 429 de Google (« 37s »), en secondes. Pur."""
    m = re.search(rb'"retryDelay"\s*:\s*"(\d+(?:\.\d+)?)s"', raw or b"")
    return float(m.group(1)) if m else None


def call(body: dict, key: str, models: tuple[str, ...] = MODELS, timeout: float = 90.0, transport=None,
         sleep=time.sleep) -> dict:
    """La requête, avec la clé — en local, ou sur Oracle pour un PC relié."""
    errors = []
    req = json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json", "x-goog-api-key": key}
    for model in models:
        if _spent.get(model, 0) > time.time() or _quota("allowed", model) is False:
            errors.append(f"{model} : quota épuisé (registre)")
            continue
        for attempt, pause in enumerate(RETRY_PAUSES):
            try:
                status, raw = (transport or _post)(URL.format(model=model), headers, req, timeout)
            except Exception as e:
                errors.append(f"{model} : {type(e).__name__}")
                break
            if status == 200:
                data = json.loads(raw.decode("utf-8"))
                data["_model"] = model
                _quota("note_ok", model)
                return data
            if status in (400, 401, 403):        # requête ou clé refusée : ni un autre essai ni un autre modèle
                raise BrainError(f"{model} : HTTP {status} {raw[:200].decode('utf-8', 'replace')}")
            if status == 429 and b"PerDay" in (raw or b""):
                # Quota du JOUR (20 requêtes pour gemini-3.5-flash, mesuré) : ni attendre ni réessayer — le
                # modèle est écarté jusqu'à minuit, heure du Pacifique, et le suivant prend la main.
                _spent[model] = time.time() + _until_pacific_midnight()
                _quota("note_error", model, raw.decode("utf-8", "replace"))
                errors.append(f"{model} : quota du jour épuisé")
                break
            if status == 429 and model != models[-1]:
                # Quota de la minute : un autre modèle a son propre compteur — on y passe tout de suite
                # plutôt que d'attendre ; le registre garde ce modèle au repos une minute.
                _quota("note_error", model, raw.decode("utf-8", "replace"))
                errors.append(f"{model} : quota de la minute")
                break
            if status in (429, 500, 502, 503, 504) and attempt < len(RETRY_PAUSES) - 1:
                # Surcharge passagère (503 mesurés le 2026-10-16) : on réessaie. Dernier modèle en 429 : on
                # attend le délai demandé (retryDelay), jusqu'à une minute.
                wait = retry_delay(raw) if status == 429 else None
                sleep(wait if wait is not None and wait <= 60 else pause)
                continue
            if status == 429:
                _quota("note_error", model, raw.decode("utf-8", "replace"))
            errors.append(f"{model} : HTTP {status}")
            break
    raise BrainError(" ; ".join(errors) or "aucun modèle")


def _post(url: str, headers: dict, body: bytes, timeout: float) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "openai/gpt-oss-120b"
_TYPES = {"OBJECT": "object", "STRING": "string", "INTEGER": "integer", "NUMBER": "number", "BOOLEAN": "boolean"}


def _schema(s: dict) -> dict:
    """Le schéma des fonctions au format Gemini → JSON Schema (OpenAI, Groq). Pur."""
    out = {k: v for k, v in s.items() if k not in ("type", "properties")}
    out["type"] = _TYPES.get(str(s.get("type", "OBJECT")).upper(), "string")
    if "properties" in s:
        out["properties"] = {k: _schema(v) for k, v in s["properties"].items()}
    return out


def to_openai(body: dict) -> dict:
    """La requête du pilote pour un modèle sans vision (Groq) : même consigne, même écran en texte, pas
    d'image — il travaille sur la liste des éléments d'accessibilité. Pur."""
    text = "\n".join(p.get("text", "") for p in body["contents"][0]["parts"] if "text" in p)
    text += "\n\n(No screenshot in this step: rely on the numbered UI elements; use coordinates only from them.)"
    tools = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                "parameters": _schema(t["parameters"])}}
             for t in body["tools"][0]["function_declarations"]]
    return {"model": GROQ_MODEL, "temperature": 0.1, "tool_choice": "required", "tools": tools,
            "messages": [{"role": "system", "content": body["system_instruction"]["parts"][0]["text"]},
                         {"role": "user", "content": text}]}


def call_groq(body: dict, key: str, timeout: float = 60.0, transport=None) -> dict:
    """Le secours sans vision, rendu au format Gemini pour que parse() le lise."""
    status, raw = (transport or _post)(GROQ_URL, {"Content-Type": "application/json", "Authorization": f"Bearer {key}",
                                                  "User-Agent": "matthew-assistant-pilot/1"},  # urllib par défaut : 403 (Cloudflare)
                                       json.dumps(to_openai(body)).encode("utf-8"), timeout)
    if status != 200:
        raise BrainError(f"groq : HTTP {status}")
    msg = json.loads(raw.decode("utf-8"))["choices"][0]["message"]
    calls = msg.get("tool_calls") or []
    if not calls:
        raise BrainError(f"groq : aucune action ({(msg.get('content') or '')[:120]})")
    fn = calls[0]["function"]
    args = json.loads(fn.get("arguments") or "{}")
    return {"candidates": [{"content": {"parts": [{"functionCall": {"name": fn["name"], "args": args}}]}}],
            "_model": f"groq/{GROQ_MODEL}"}


OLLAMA_CHAT = "http://127.0.0.1:11434/api/chat"
#: Ollama Cloud (modèles « -cloud », servis par Ollama, appelés par l'Ollama local de l'utilisateur) : plan
#: gratuit à crédits mensuels, une requête à la fois. Mesuré le 2026-10-16 sur une étape réelle : gemma4:31b
#: (vision et outils) a choisi la bonne action en 0,5 s.
OLLAMA_VISION = "gemma4:31b-cloud"


def call_ollama(body: dict, model: str = OLLAMA_VISION, timeout: float = 90.0, transport=None) -> dict:
    """Le secours avec vision par l'Ollama local (son nuage). Rendu au format Gemini pour parse()."""
    oa = to_openai(body)
    user = {"role": "user", "content": oa["messages"][1]["content"]}
    image = next((p["inline_data"]["data"] for p in body["contents"][0]["parts"] if "inline_data" in p), None)
    if image:
        user["images"] = [image]
        user["content"] = user["content"].replace("(No screenshot in this step: rely on the numbered UI elements; "
                                                  "use coordinates only from them.)", "")
    req = {"model": model, "messages": [oa["messages"][0], user], "tools": oa["tools"], "stream": False,
           "options": {"temperature": 0.1}}
    status, raw = (transport or _post)(OLLAMA_CHAT, {"Content-Type": "application/json"},
                                       json.dumps(req).encode("utf-8"), timeout)
    if status != 200:
        raise BrainError(f"ollama {model} : HTTP {status} {raw[:120].decode('utf-8', 'replace')}")
    msg = json.loads(raw.decode("utf-8")).get("message") or {}
    calls = msg.get("tool_calls") or []
    if not calls:
        raise BrainError(f"ollama {model} : aucune action")
    fn = calls[0]["function"]
    args = fn.get("arguments") or {}
    if isinstance(args, str):
        args = json.loads(args or "{}")
    return {"candidates": [{"content": {"parts": [{"functionCall": {"name": fn["name"], "args": args}}]}}],
            "_model": f"ollama/{model}"}


def step(body: dict, gemini_key: str, groq_key: str = "", models: tuple[str, ...] = MODELS, transport=None,
         ollama_model: str = OLLAMA_VISION) -> dict:
    """Gemini (vision) ; épuisé ou en panne → Ollama Cloud (vision) → Groq (l'écran en texte). En local et sur
    Oracle (où Ollama est absent : le refus de connexion est immédiat)."""
    errors = []
    try:
        if not gemini_key:
            raise BrainError("pas de clé Gemini (gemini_api_key)")
        return call(body, gemini_key, models, transport=transport)
    except BrainError as e:
        if " HTTP 400" in str(e) or "HTTP 401" in str(e) or "HTTP 403" in str(e):
            raise
        errors.append(str(e))
    if ollama_model:
        try:
            return call_ollama(body, ollama_model, transport=transport)
        except Exception as e:
            errors.append(f"ollama : {type(e).__name__}: {e}"[:200])
    if groq_key:
        try:
            return call_groq(body, groq_key, transport=transport)
        except Exception as e:
            errors.append(f"{e}"[:200])
    raise BrainError(" ; ".join(errors))


class LocalBrain:
    """Mode local : les clés de config/api_keys.json — Gemini, et Groq en secours sans vision."""

    def __init__(self, key: str, models: tuple[str, ...] = MODELS, transport=None, groq_key: str = "",
                 ollama_model: str = OLLAMA_VISION):
        self.key, self.models, self.transport, self.groq_key = key, models, transport, groq_key
        self.ollama_model = ollama_model

    def step(self, body: dict) -> dict:
        return step(body, self.key, self.groq_key, self.models, transport=self.transport,
                    ollama_model=self.ollama_model)


class RemoteBrain:
    """PC relié : Oracle fait l'appel (POST /api/pilot/step)."""

    def __init__(self, http_session, timeout: float = 120.0, opener=urllib.request.urlopen):
        self.http_session, self.timeout, self.opener = http_session, timeout, opener

    def step(self, body: dict) -> dict:
        sess = self.http_session()
        if not sess:
            raise BrainError("Oracle n'est pas joignable")
        base, bearer = sess
        req = urllib.request.Request(f"{base.rstrip('/')}/api/pilot/step", data=json.dumps({"body": body}).encode(),
                                     method="POST", headers={"Content-Type": "application/json",
                                                             "Authorization": f"Bearer {bearer}"})
        try:
            with self.opener(req, timeout=self.timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise BrainError(f"Oracle : HTTP {e.code} {e.read()[:200].decode('utf-8', 'replace')}") from e
