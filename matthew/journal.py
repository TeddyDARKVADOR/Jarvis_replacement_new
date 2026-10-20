"""
matthew/journal.py — le carnet de Matthew : un épisode par tour de conversation.

main.py prévient déjà la cognition à chaque tour ; Tap s'y greffe sans rien
changer. Dans la session Live, l'ordre d'arrivée est : les outils PENDANT le
tour, puis la transcription de l'utilisateur et la réponse à turn_complete.
Un épisode = la demande + les outils appelés (arguments et résultat réels)
+ la réponse, marquée origin="gemini" : elle sert à comparer, jamais de cible.

Avant d'écrire, tout passe par scrub() : clés d'API, jetons, e-mails,
téléphones, IBAN et numéros de carte sont remplacés. Un fichier par jour :
journal/AAAA-MM-JJ.jsonl. Le fichier PAUSED coupe la collecte.

Les corrections (« la bonne réponse était… ») sont écrites par l'utilisateur :
ce sont les meilleures données qui existent, origin="human".
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from matthew import EPISODE_SCHEMA, data_dir, paused

_SECRETS = [
    (re.compile(r"AIza[0-9A-Za-z_\-]{30,}"), "<clé>"),
    (re.compile(r"\b(?:sk|gsk|pk|rk)[-_][0-9A-Za-z_\-]{16,}"), "<clé>"),
    (re.compile(r"\bgh[pousr]_[0-9A-Za-z]{20,}"), "<clé>"),
    (re.compile(r"\beyJ[0-9A-Za-z_\-]{10,}\.[0-9A-Za-z_\-]{10,}\.[0-9A-Za-z_\-]+"), "<jeton>"),
    (re.compile(r"(?i)\b(password|mot de passe|mdp|token|secret|api[_ ]?key)\s*[:=]\s*\S+"), r"\1=<masqué>"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "<e-mail>"),
    (re.compile(r"\b[A-Z]{2}\d{2}(?: ?[0-9A-Z]{4}){3,7}\b"), "<iban>"),
    (re.compile(r"\b(?:\d[ -]?){13,19}\b"), "<numéro>"),
    (re.compile(r"(?<!\d)(?:\+33 ?|0)[1-9](?:[ .-]?\d{2}){4}(?!\d)"), "<téléphone>"),
]
MAX_RESULT = 1500


def scrub(value):
    """Masque les secrets et les coordonnées, récursivement."""
    if isinstance(value, str):
        for rx, repl in _SECRETS:
            value = rx.sub(repl, value)
        return value
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub(v) for v in value]
    return value


class Journal:
    def __init__(self, root: Path | None = None, clock=time.time):
        self.root = Path(root) if root else data_dir()
        self.root.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        self._lock = threading.Lock()

    @property
    def last_path(self) -> Path:
        return self.root / "last.json"

    def _day_file(self, ts: float) -> Path:
        d = self.root / "journal"
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{datetime.fromtimestamp(ts).strftime('%Y-%m-%d')}.jsonl"

    def append(self, record: dict) -> dict | None:
        if paused() and self.root == data_dir():
            return None
        record = scrub(record)
        with self._lock:
            with self._day_file(record["ts"]).open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            if record.get("kind") == "episode":
                self.last_path.write_text(json.dumps({"id": record["id"], "ts": record["ts"]}),
                                          encoding="utf-8")
        return record

    def episode(self, *, session: str, user: str, tools: list[dict], reply: str,
                origin: str = "gemini") -> dict | None:
        ts = self._clock()
        return self.append({"schema": EPISODE_SCHEMA, "kind": "episode",
                            "id": uuid.uuid4().hex[:12], "ts": ts, "session": session,
                            "user": user.strip(), "tools": tools,
                            "reply": {"text": reply.strip(), "origin": origin}})

    def correction(self, text: str, of: str | None = None) -> dict | None:
        """La bonne réponse, dite par l'utilisateur, pour l'épisode `of` (le dernier sinon)."""
        if of is None:
            try:
                of = json.loads(self.last_path.read_text(encoding="utf-8"))["id"]
            except (OSError, ValueError, KeyError):
                return None
        return self.append({"schema": EPISODE_SCHEMA, "kind": "correction", "of": of,
                            "ts": self._clock(), "text": text.strip(), "origin": "human"})

    def forget_last(self) -> bool:
        """Retire le dernier épisode (vie privée) : il ne sera jamais appris."""
        try:
            last = json.loads(self.last_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        self.append({"schema": EPISODE_SCHEMA, "kind": "forget", "of": last["id"],
                     "ts": self._clock()})
        return True

    def records(self, since: float = 0.0):
        d = self.root / "journal"
        for f in sorted(d.glob("*.jsonl")) if d.exists() else []:
            for line in f.read_text(encoding="utf-8").splitlines():
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("ts", 0) >= since:
                    yield r

    def save_tools(self, declarations: list[dict]) -> None:
        """Les déclarations d'outils courantes (format Gemini), écrites si elles changent."""
        blob = json.dumps(declarations, ensure_ascii=False, sort_keys=True)
        digest = hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]
        path = self.root / "tools.json"
        try:
            if json.loads(path.read_text(encoding="utf-8")).get("digest") == digest:
                return
        except (OSError, ValueError):
            pass
        path.write_text(json.dumps({"digest": digest, "tools": declarations}, ensure_ascii=False),
                        encoding="utf-8")

    def tools(self) -> list[dict]:
        try:
            return json.loads((self.root / "tools.json").read_text(encoding="utf-8"))["tools"]
        except (OSError, ValueError, KeyError):
            return []


class Tap:
    """Assemble les épisodes : outils pendant le tour, puis demande, puis réponse."""

    def __init__(self, journal: Journal, session_of=lambda: ""):
        self.journal = journal
        self._session_of = session_of
        self._tools: list[dict] = []
        self._user = ""
        self._lock = threading.Lock()

    @classmethod
    def install(cls, jarvis) -> "Tap":
        """Se greffe sur une instance de JarvisLive, dans TOUS les modes (local,
        serveur Oracle, sans écran) : main.py écrit « You: … » puis « <nom>: … »
        dans l'interface à chaque fin de tour, et chaque outil passe par
        _execute_tool. Rien n'est modifié dans main.py ; ce qui est affiché et
        renvoyé à Gemini reste identique."""
        session = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        tap = cls(Journal(), session_of=lambda: session)
        ui = jarvis.ui
        orig_log, orig_exec = ui.write_log, jarvis._execute_tool

        def write_log(text, *a, **kw):
            try:
                tap.on_log_line(str(text), getattr(jarvis, "_asst_name", "JARVIS"))
            except Exception:
                pass
            return orig_log(text, *a, **kw)

        async def execute_tool(fc):
            fr = await orig_exec(fc)
            try:
                resp = getattr(fr, "response", None) or {}
                tap.on_tool(fc.name, dict(fc.args or {}), resp.get("result", ""))
            except Exception:
                pass
            return fr

        ui.write_log, jarvis._execute_tool = write_log, execute_tool
        jarvis._matthew = tap
        try:
            tap.journal.save_tools(collect_declarations(jarvis))
        except Exception:
            pass
        return tap

    def on_log_line(self, line: str, asst_name: str) -> None:
        if line.startswith("You: "):
            self.on_turn("user", line[5:])
        elif asst_name and line.startswith(f"{asst_name}: "):
            self.on_turn("assistant", line[len(asst_name) + 2:])

    def on_tool(self, name: str, args: dict, result) -> None:
        text = str(result or "")
        ok = not re.match(r"(?i)(tool '.*' failed|unknown tool|l'outil .* a échoué|erreur)", text)
        with self._lock:
            self._tools.append({"name": name, "args": dict(args or {}),
                                "result": text[:MAX_RESULT], "ok": ok})

    def on_turn(self, role: str, text: str) -> None:
        text = (text or "").strip()
        if role == "user":
            with self._lock:
                self._user = f"{self._user} {text}".strip() if self._user else text
            return
        with self._lock:
            user, tools = self._user, self._tools
            self._user, self._tools = "", []
        if user or tools:
            self.journal.episode(session=self._session_of(), user=user, tools=tools, reply=text)


def collect_declarations(jarvis) -> list[dict]:
    """Toutes les déclarations d'outils que Gemini reçoit : main.TOOL_DECLARATIONS
    + actions + plugins. Lu sur l'instance, sans importer main."""
    import sys
    out = list(getattr(sys.modules.get(type(jarvis).__module__), "TOOL_DECLARATIONS", []) or [])
    for reg in ("_action_registry", "_plugin_registry"):
        try:
            out += list(getattr(jarvis, reg).get_tool_declarations())
        except Exception:
            pass
    seen, uniq = set(), []
    for d in out:
        if isinstance(d, dict) and d.get("name") and d["name"] not in seen:
            seen.add(d["name"])
            uniq.append(d)
    return uniq
