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
        if _spent.get(model, 0) > time.time():
            errors.append(f"{model} : quota du jour épuisé")
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
                return data
            if status in (400, 401, 403):        # requête ou clé refusée : ni un autre essai ni un autre modèle
                raise BrainError(f"{model} : HTTP {status} {raw[:200].decode('utf-8', 'replace')}")
            if status == 429 and b"PerDay" in (raw or b""):
                # Quota du JOUR (20 requêtes pour gemini-3.5-flash, mesuré) : ni attendre ni réessayer — le
                # modèle est écarté jusqu'à minuit, heure du Pacifique, et le suivant prend la main.
                _spent[model] = time.time() + _until_pacific_midnight()
                errors.append(f"{model} : quota du jour épuisé")
                break
            if status in (429, 500, 502, 503, 504) and attempt < len(RETRY_PAUSES) - 1:
                # Surcharge passagère (503 mesurés le 2026-10-16) : on réessaie. Un 429 dit combien attendre
                # (retryDelay) : jusqu'à une minute, on attend ce délai plutôt que de griller le modèle suivant.
                wait = retry_delay(raw) if status == 429 else None
                sleep(wait if wait is not None and wait <= 60 else pause)
                continue
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


class LocalBrain:
    """Mode local : la clé Gemini de config/api_keys.json."""

    def __init__(self, key: str, models: tuple[str, ...] = MODELS, transport=None):
        self.key, self.models, self.transport = key, models, transport

    def step(self, body: dict) -> dict:
        if not self.key:
            raise BrainError("pas de clé Gemini (gemini_api_key)")
        return call(body, self.key, self.models, transport=self.transport)


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
