"""
matthew/vitrine.py — la vitrine publique : regarder Matthew apprendre, sans accès à rien.

    python -m matthew.vitrine replay      rejoue les scénarios de démonstration sur le champion
    python -m matthew.vitrine build DIR   écrit le site (page + data/vitrine.json + og.png) dans DIR
    python -m matthew.vitrine publish     replay + build dans le dépôt de la vitrine, puis git push
                                          (GitHub Pages republie tout seul à chaque push)
    python -m matthew.vitrine flag "…"    lever le drapeau ; run : publier s'il est levé
    python -m matthew.vitrine hooks       crochets Git : chaque commit ou fusion lève le drapeau
    python -m matthew.vitrine schedule    tâche de rattrapage toutes les 30 minutes
    python -m matthew.vitrine setup       une seule fois, par l'utilisateur : crée le dépôt GitHub
                                          public depuis D:\\DEV\\matthew-vitrine et active Pages

CE QUI SORT — une liste blanche, construite champ par champ, jamais une copie :
  - des agrégats : score du champion, précision par outil, outils couverts, nombre
    d'exemples, tokens, dates et issues des cycles, nom du modèle de base ;
  - des scénarios de démonstration tirés de l'amorçage SYNTHÉTIQUE (demandes inventées
    par le professeur ouvert), avec l'outil que Matthew a choisi chaque jour.

CE QUI NE SORT JAMAIS — une demande, une réponse ou un résultat d'outil du journal réel,
un chemin local, une clé. check_public() le prouve avant chaque publication : clés
autorisées par chemin, chaque texte de scénario présent dans l'amorçage et absent du
journal, chaque nom d'outil connu, scrub() sans effet ; sinon rien n'est écrit.

Les scénarios rejoués chaque jour sont FIXES (memory/matthew/vitrine/showcase.json) :
on compare le même examen à J-7 et aujourd'hui.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from matthew import NAME, config, data_dir
from matthew.client import Client, ClientError, to_openai_tools, usage_summary
from matthew.dataset import synthetic_records
from matthew.journal import Journal, scrub
from matthew.teacher import SYSTEM

SITE = Path(__file__).resolve().parent / "vitrine_site"
SCHEMA = "matthew.vitrine/1"
SHOWCASE_SIZE = 16

#: Libellé public et domaine de chaque outil (un outil inconnu tombe dans « Autres »).
TOOLS = {
    "system_status": ("État du PC", "Système"), "manage_monitor": ("Écrans", "Système"),
    "computer_settings": ("Réglages du PC", "Système"), "local_health_check": ("Diagnostic", "Système"),
    "shutdown_jarvis": ("S'éteindre", "Système"), "close_camera": ("Fermer la caméra", "Système"),
    "screen_process": ("Lire l'écran", "Système"), "game_updater": ("Mises à jour de jeux", "Système"),
    "open_app": ("Ouvrir une appli", "Bureau"), "desktop_control": ("Bureau Windows", "Bureau"),
    "computer_control": ("Souris et clavier", "Bureau"), "browser_control": ("Navigateur", "Bureau"),
    "file_controller": ("Fichiers", "Bureau"), "file_processor": ("Lire un document", "Bureau"),
    "undo": ("Annuler", "Bureau"), "pilot": ("Pilote du PC", "Bureau"),
    "web_search": ("Recherche web", "Infos"), "weather_report": ("Météo", "Infos"),
    "youtube_video": ("YouTube", "Infos"), "flight_finder": ("Vols", "Infos"),
    "open_itinerary": ("Itinéraire", "Infos"), "briefing": ("Briefing du matin", "Infos"),
    "send_message": ("Envoyer un message", "Communication"), "reminder": ("Rappels", "Communication"),
    "meeting": ("Réunions", "Communication"),
    "save_memory": ("Retenir", "Mémoire"), "recall_memory": ("Se souvenir", "Mémoire"),
    "recall_episodes": ("Conversations passées", "Mémoire"), "forget_about": ("Oublier", "Mémoire"),
    "goals": ("Objectifs", "Mémoire"), "self_state": ("État intérieur", "Mémoire"),
    "code_helper": ("Aide au code", "Code"), "code_studio": ("Code Studio", "Code"),
    "repository_review": ("Revue de dépôt", "Code"),
    "capabilities": ("Ses capacités", "Noyau"), "usage_report": ("Consommation", "Noyau"),
    "quotas": ("Quotas", "Noyau"), "incidents": ("Incidents", "Noyau"),
    "extensions": ("Extensions", "Noyau"), "mcp": ("Connecteurs MCP", "Noyau"),
    "skills": ("Compétences", "Noyau"), "portable": ("Mode clé USB", "Noyau"),
    "matthew": ("Apprendre", "Noyau"),
}
DOMAINS = ["Système", "Bureau", "Infos", "Communication", "Mémoire", "Code", "Noyau", "Autres"]
TOOLS_EN = {
    "system_status": "PC status", "manage_monitor": "Displays", "computer_settings": "PC settings",
    "local_health_check": "Diagnostics", "shutdown_jarvis": "Shut down", "close_camera": "Close camera",
    "screen_process": "Read the screen", "game_updater": "Game updates", "open_app": "Open an app",
    "desktop_control": "Windows desktop", "computer_control": "Mouse and keyboard", "browser_control": "Browser",
    "file_controller": "Files", "file_processor": "Read a document", "undo": "Undo", "pilot": "PC pilot",
    "web_search": "Web search", "weather_report": "Weather", "youtube_video": "YouTube", "flight_finder": "Flights",
    "open_itinerary": "Directions", "briefing": "Morning briefing", "send_message": "Send a message",
    "reminder": "Reminders", "meeting": "Meetings", "save_memory": "Remember", "recall_memory": "Recall",
    "recall_episodes": "Past conversations", "forget_about": "Forget", "goals": "Goals", "self_state": "Inner state",
    "code_helper": "Code helper", "code_studio": "Code Studio", "repository_review": "Repo review",
    "capabilities": "Its capabilities", "usage_report": "Usage", "quotas": "Quotas", "incidents": "Incidents",
    "extensions": "Extensions", "mcp": "MCP connectors", "skills": "Skills", "portable": "USB-stick mode",
    "matthew": "Learning",
}
NO_TOOL = ("__chat__", "__none__")
#: Identifiants publics : aucun ne doit trahir l'assistant hôte.
PUBLIC_IDS = {"shutdown_jarvis": "shutdown"}


def pub(name: str | None) -> str | None:
    return PUBLIC_IDS.get(name, name) if name else name


def vdir() -> Path:
    d = data_dir() / "vitrine"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _read(path: Path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


# ── Scénarios de démonstration ───────────────────────────────────────────────

def _held_out(user: str, share: float) -> bool:
    """Même règle que dataset.split() pour l'amorçage : dans l'évaluation, jamais appris."""
    from matthew.dataset import _key
    return int(hashlib.sha1(_key(user).encode()).hexdigest()[:8], 16) / 0xFFFFFFFF < share


def showcase(root: Path | None = None) -> list[dict]:
    """Les scénarios fixes. Choisis une fois (un par outil, dans l'ordre stable d'un
    hachage), complétés seulement quand de nouveaux outils sont amorcés."""
    root = root or data_dir()
    cfg = config()
    path = root / "vitrine" / "showcase.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    cur = _read(path, [])
    have = {s["target"] for s in cur}
    by_tool: dict[str, list[dict]] = {}
    for r in synthetic_records(root):
        t = r.get("target", "")
        text = r["messages"][1]["content"]
        if t.startswith("__") or t in have or scrub(text) != text or BRAND.search(text):
            continue                    # (une adresse inventée bloquerait toute la publication)
        by_tool.setdefault(t, []).append(r)
    for t in sorted(by_tool):
        if len(cur) >= SHOWCASE_SIZE * 3:
            break
        # De préférence un exemple du hold-out (jamais vu à l'entraînement) : même tirage
        # que dataset.split(). Sinon un exemple d'entraînement, et la page le dit.
        def held(r):
            return _held_out(r["messages"][1]["content"], cfg["eval_share"])
        r = min(by_tool[t], key=lambda r: (not held(r), hashlib.sha1(r["episode"].encode()).hexdigest()))
        cur.append({"id": hashlib.sha1(r["episode"].encode()).hexdigest()[:8], "target": t,
                    "request": r["messages"][1]["content"], "tools": r["tools"], "heldout": held(r)})
    # Conversation et refus : 4 de chaque, pour comparer les RÉPONSES d'une nuit à l'autre.
    for kind in NO_TOOL:
        taken = {s["request"] for s in cur}
        pool = [r for r in synthetic_records(root) if r.get("target") == kind
                and r["messages"][1]["content"] not in taken
                and scrub(r["messages"][1]["content"]) == r["messages"][1]["content"]
                and not BRAND.search(r["messages"][1]["content"])]
        pool.sort(key=lambda r: (not _held_out(r["messages"][1]["content"], cfg["eval_share"]),
                                 hashlib.sha1(r["episode"].encode()).hexdigest()))
        for r in pool[:max(0, 4 - sum(1 for s in cur if s["target"] == kind))]:
            cur.append({"id": hashlib.sha1(r["episode"].encode()).hexdigest()[:8], "target": kind,
                        "request": r["messages"][1]["content"], "tools": r["tools"],
                        "heldout": _held_out(r["messages"][1]["content"], cfg["eval_share"])})
    path.write_text(json.dumps(cur, ensure_ascii=False, indent=1), encoding="utf-8")
    return cur


def public_answer(text: str) -> str | None:
    """Une réponse de Matthew publiable : courte, sans secret, sans le nom de l'hôte."""
    t = re.sub(r"\s+", " ", re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)).strip()
    t = re.sub(r"(?i)\bjarvis\b", "Matthew", t)[:220]
    if not t or scrub(t) != t or BRAND.search(t):
        return None
    return t


def replay(client: Client | None = None, root: Path | None = None, day: str | None = None) -> dict:
    """Rejoue chaque scénario sur le champion ; écrit days/AAAA-MM-JJ.json."""
    root = root or data_dir()
    cfg = config()
    decls = {d["name"]: d for d in Journal(root).tools()}
    if client is None:
        from matthew.flywheel import SERVE_EXTRA
        client = Client(cfg["endpoint"], cfg["serve_model"], purpose="matthew", extra=SERVE_EXTRA,
                        usage_root=root, timeout=120)
    day = day or datetime.now().strftime("%Y-%m-%d")
    out = {}
    for s in showcase(root):
        tools = to_openai_tools([decls[n] for n in s["tools"] if n in decls])
        try:
            msg = client.chat([{"role": "system", "content": SYSTEM}, {"role": "user", "content": s["request"]}],
                              tools or None, temperature=0.0, max_tokens=300)
        except ClientError:
            continue
        calls = [(c.get("function") or {}).get("name") for c in msg.get("tool_calls") or []]
        no_tool = s["target"] in NO_TOOL
        ok = (not calls and bool((msg.get("content") or "").strip())) if no_tool else calls[:1] == [s["target"]]
        out[s["id"]] = {"chosen": calls[0] if calls else None, "ok": ok, "ms": int(msg.get("_latency", 0) * 1000)}
        if no_tool and not calls:
            out[s["id"]]["answer"] = public_answer(msg.get("content", ""))
    if out:
        d = root / "vitrine" / "days"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{day}.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return out


# ── Le journal des améliorations ─────────────────────────────────────────────

AREAS = ["IA", "Interface", "Voix", "Fiabilité", "Système"]
_AREA_OF = [("IA", ("matthew/", "cognition/", "eval_lab/", "offline/llm")),
            ("Interface", ("client_desktop/", "dashboard/", "local_hud", "client-android/")),
            ("Voix", ("offline/", "meeting/", "server/audio", "core/wake")),
            ("Fiabilité", ("selftest", "nightshift/", "incidents/", "resilience/", "capabilities/", "quota/"))]


def clean_title(subject: str) -> str | None:
    """Un sujet de commit publiable, ou None. Le nom de l'assistant hôte devient « Matthew »."""
    # Secrets et coordonnées cherchés sur le texte D'ORIGINE : retirer « @ » d'abord
    # ferait passer une adresse e-mail (constaté par le selftest).
    if scrub(subject or "") != (subject or "") or re.search(r"[A-Za-z]:[/\\]|https?://", subject or ""):
        return None
    t = re.sub(r"(?i)\bjarvis\b", "Matthew", subject or "").strip()
    t = re.sub(r"(?i)mark\s*l?iii", "le serveur", t)
    t = re.sub(r"[\"`*_<>\[\]{}|~^$#@\\]", "", t)
    t = re.sub(r"\s+", " ", t)[:110].strip(" .'")
    if len(t) < 6 or scrub(t) != t or re.search(r"[A-Za-z]:/|https?://", t) or BRAND.search(t) \
            or not _TITLE.match(t):
        return None
    return t


def changes(repo: Path | None = None, limit: int = 80, runner=subprocess.run) -> list[dict]:
    """Les dernières améliorations du code (commits hors fusions), classées par domaine."""
    from matthew import REPO
    try:
        cp = runner(["git", "-C", str(repo or REPO), "log", "--no-merges", f"-n{limit * 2}", "--date=short",
                     "--pretty=format:@@%ad|%s", "--name-only"], capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return []
    out = []
    for block in (cp.stdout or "").split("@@")[1:]:
        lines = [l for l in block.splitlines() if l.strip()]
        if not lines:
            continue
        date, _, subject = lines[0].partition("|")
        title = clean_title(subject)
        if not title or not _DATE.match(date):
            continue
        votes: dict[str, int] = {}
        for f in lines[1:]:
            if f.startswith("capabilities/graphs/"):
                continue
            a = next((a for a, keys in _AREA_OF if any(k in f for k in keys)), "Système")
            votes[a] = votes.get(a, 0) + 1
        area = max(votes, key=votes.get) if votes else "Système"
        out.append({"date": date, "title": title, "area": area})
        if len(out) >= limit:
            break
    return out


# ── Données publiques ────────────────────────────────────────────────────────

def _score(s: dict | None) -> dict | None:
    if not s:
        return None
    return {k: (round(float(s[k]), 3) if isinstance(s.get(k), (int, float)) else None)
            for k in ("composite", "tool_name", "tool_args", "irrelevance", "valid", "latency_p50")}


def _day(stamp: str) -> str:
    return f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}" if re.match(r"^\d{8}", stamp or "") else ""


def gather(root: Path | None = None, next_run=None) -> dict:
    """Construit les données publiques champ par champ (rien n'est recopié tel quel).
    `next_run` : la prochaine nuit (flywheel.next_run par défaut, lue dans le Planificateur)."""
    root = root or data_dir()
    j = Journal(root)
    decls = [d["name"] for d in j.tools()]
    syn = [r for r in synthetic_records(root) if not r.get("target", "").startswith("__")]
    per_ex: dict[str, int] = {}
    learned_on: dict[str, str] = {}
    for r in syn:
        per_ex[r["target"]] = per_ex.get(r["target"], 0) + 1
        d = datetime.fromtimestamp(float(r.get("ts", 0))).strftime("%Y-%m-%d")
        if r["target"] not in learned_on or d < learned_on[r["target"]]:
            learned_on[r["target"]] = d
    last = _read(root / "last_eval.json", {})
    per_tool = last.get("per_tool") or {}
    tools = []
    for name in decls:
        label, dom = TOOLS.get(name, (name.replace("_", " "), "Autres"))
        acc = per_tool.get(name)
        tools.append({"id": pub(name), "label": label, "label_en": TOOLS_EN.get(name, label), "domain": dom,
                      "learned_on": learned_on.get(name), "examples": per_ex.get(name, 0),
                      "accuracy": round(float(acc), 3) if isinstance(acc, (int, float)) else None})
    history, cycles = [], []
    for f in sorted((root / "reports").glob("*.json")):
        rep = _read(f, {})
        d = _day(rep.get("at", ""))
        if not d:
            continue
        cycles.append({"date": d, "outcome": str(rep.get("outcome", ""))[:40]})
        ev = next((s for s in rep.get("steps", []) if s.get("step") == "évaluation"), None)
        data = next((s for s in rep.get("steps", []) if s.get("step") == "données"), {})
        if ev and ev.get("candidate"):
            pt = ev["candidate"].get("per_tool") or {}
            history.append({"date": d, "score": _score(ev["candidate"]), "promoted": bool(ev.get("promote")),
                            "examples": int(data.get("train", 0)) + int(data.get("eval", 0)),
                            "per_tool": {pub(t): round(float(v), 3) for t, v in pt.items()
                                         if not t.startswith("__") and isinstance(v, (int, float))}})
    champ = _read(root / "champion.json", None)
    days = {}
    for f in sorted((root / "vitrine" / "days").glob("*.json")):
        days[f.stem] = _read(f, {})
    show = []
    for s in showcase(root):
        show.append({"id": s["id"], "request": s["request"], "target": pub(s["target"]),
                     "heldout": bool(s.get("heldout")),
                     "results": {d: {"chosen": pub(v[s["id"]]["chosen"]), "ok": bool(v[s["id"]]["ok"]),
                                     "ms": int(v[s["id"]].get("ms", 0)),
                                     "answer": public_answer(v[s["id"]].get("answer") or "")}
                                 for d, v in days.items() if s["id"] in v}})
    toks = usage_summary(root)
    live = _read(root / "vitrine" / "live.json", None)
    if live and time.time() - float(live.get("at", 0)) < 3 * 3600:
        live = {"phase": str(live.get("phase", ""))[:20],
                "epoch": round(float(live["epoch"]), 2) if isinstance(live.get("epoch"), (int, float)) else None,
                "epochs": int(live.get("epochs") or 0),
                "losses": [round(float(x), 4) for x in (live.get("losses") or [])][-60:],
                "since": str(live.get("since", ""))[:16],
                **{k: int(live[k]) for k in ("done", "total", "right") if isinstance(live.get(k), int)}}
    else:
        live = None
    if next_run is None:
        from matthew.flywheel import next_run
    try:
        nxt = next_run()
    except Exception:
        nxt = None
    nxt = {"at": nxt.get("at") if _ISO.match(str(nxt.get("at") or "")) else None,
           "enabled": bool(nxt.get("enabled"))} if isinstance(nxt, dict) else None
    return {
        "schema": SCHEMA, "name": NAME, "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "champion": {"since": _day(champ.get("since", "")), "base": str(champ.get("base", ""))[:60],
                     "score": _score(champ.get("score"))} if champ else None,
        "coverage": {"tools_total": len(decls), "tools_covered": len(per_ex), "examples": len(syn)},
        "tools": tools, "domains": DOMAINS, "history": history, "cycles": cycles[-60:],
        "tokens": {"matthew": sum(v["in"] + v["out"] for k, v in toks.items() if k == "matthew"),
                   "school": sum(v["in"] + v["out"] for k, v in toks.items() if k == "teacher")},
        "showcase": show,
        "changes": changes(),
        "live": live,
        "next": nxt,
        "counters": counters(root, cycles, toks),
        "specialties": _specialties(root),
    }


def _specialties(root: Path) -> list[dict]:
    """Direction 3 : chaque spécialité, son score, son seuil, sa référence (agrégats seulement)."""
    try:
        from matthew.specialist import public
        return public(root)
    except Exception:
        return []


def counters(root: Path, cycles: list[dict], toks: dict) -> dict:
    """Les compteurs vivants : tokens de Matthew, heures de GPU d'entraînement, nuits."""
    secs = 0.0
    for log in list((root / "runs").glob("*/train.log")) + list((root / "runs").glob("*/smoke.log")):
        try:
            m = re.findall(r"'train_runtime': '?([\d.]+)", log.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        if m:
            secs += float(m[-1])
    nights = sorted({c["date"] for c in cycles})
    streak = 0
    if nights:
        from datetime import date, timedelta
        day = date.fromisoformat(nights[-1])
        have = set(nights)
        while day.isoformat() in have:
            streak += 1
            day -= timedelta(days=1)
    return {"tokens": int(sum(v["in"] + v["out"] for k, v in toks.items() if k == "matthew" or k.startswith("gateway:"))),
            "gpu_hours": round(secs / 3600, 2), "nights": len(nights), "streak": streak}


# ── Le garde-fou ─────────────────────────────────────────────────────────────

ALLOWED = {
    "": {"schema", "name", "generated", "champion", "coverage", "tools", "domains", "history", "cycles",
         "tokens", "showcase", "changes", "live", "next", "counters", "specialties"},
    "specialties[]": {"id", "label", "label_en", "score", "floor", "reference", "n", "delegated"},
    "live": {"phase", "epoch", "epochs", "losses", "since", "done", "total", "right"},
    "next": {"at", "enabled"},
    "champion": {"since", "base", "score"},
    "score": {"composite", "tool_name", "tool_args", "irrelevance", "valid", "latency_p50"},
    "coverage": {"tools_total", "tools_covered", "examples"},
    "tools[]": {"id", "label", "label_en", "domain", "learned_on", "examples", "accuracy"},
    "counters": {"tokens", "gpu_hours", "nights", "streak"},
    "history[]": {"date", "score", "promoted", "examples", "per_tool"},
    "cycles[]": {"date", "outcome"},
    "changes[]": {"date", "title", "area"},
    "tokens": {"matthew", "school"},
    "showcase[]": {"id", "request", "target", "heldout", "results"},
    "result": {"chosen", "ok", "ms", "answer"},
}
_SAFE = re.compile(r"^[\w .:/+'’«»()\-éèêàâîïôûùçÉÀ%,]{0,80}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}( \d{2}:\d{2})?$")
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$")     # la prochaine nuit, avec son fuseau
PHASES = {"prep", "entraînement", "export", "import", "exam", "duel"}


#: La vitrine présente Matthew seul : l'assistant qui l'héberge n'est jamais nommé.
BRAND = re.compile(r"jarvis|mark\s*l?iii", re.I)
_TITLE = re.compile(r"^[\w .:/+'’«»()\-%,—→&!?·=]{3,110}$")


class PrivacyError(AssertionError):
    pass


def check_public(data: dict, root: Path | None = None) -> int:
    """Lève PrivacyError si quoi que ce soit sort de la liste blanche. Rend le nombre de valeurs vues.

    Passe 1, la forme : chaque objet n'a que les clés autorisées à sa place ; chaque texte
    est sans secret (scrub), n'est pas un texte du journal réel, n'a pas l'air d'un chemin
    local et tient dans un gabarit court — sauf les demandes de démonstration.
    Passe 2, le fond : outils connus, identifiants, dates, et chaque demande de démonstration
    présente telle quelle dans l'amorçage synthétique."""
    root = root or data_dir()
    j = Journal(root)
    known = {pub(n) for n in {d["name"] for d in j.tools()} | set(TOOLS)} | set(NO_TOOL)
    synthetic = {r["messages"][1]["content"] for r in synthetic_records(root)}
    private = set()
    for r in j.records():
        for text in (r.get("user"), (r.get("reply") or {}).get("text"), r.get("text"),
                     *[t.get("result") for t in r.get("tools") or []]):
            if text and str(text).strip():
                private.add(str(text).strip())
    count = 0

    def fail(path, why):
        raise PrivacyError(f"vitrine refusée — {path} : {why}")

    def text_ok(v: str, path: str, free: bool):
        if BRAND.search(v):
            fail(path, "nomme l'assistant hôte")
        if scrub(v) != v:
            fail(path, "contient un secret ou une coordonnée")
        if v.strip() in private:
            fail(path, "texte du journal réel")
        if not free and ("\\" in v or re.search(r"[A-Za-z]:/", v)):
            fail(path, "ressemble à un chemin local")
        if not free and not (_TITLE if path.endswith(".title") else _SAFE).match(v):
            fail(path, f"texte hors gabarit {v[:40]!r}")

    def walk(v, path: str, ctx: str):
        nonlocal count
        count += 1
        if isinstance(v, dict):
            if ctx == "per_tool":                       # {outil public: précision}
                for t, x in v.items():
                    if t not in known or not isinstance(x, (int, float)):
                        fail(path, f"précision par outil invalide {t!r}")
                return
            if ctx == "results":                        # {jour: résultat}
                for day, r in v.items():
                    if not _DATE.match(day):
                        fail(path, f"clé de jour {day!r}")
                    walk(r, f"{path}.{day}", "result")
                return
            allowed = ALLOWED.get(ctx)
            if allowed is None:
                fail(path, f"objet inattendu ({ctx})")
            for k, x in v.items():
                if k not in allowed:
                    fail(path, f"champ non autorisé {k!r}")
                child = {"tools": "tools[]", "history": "history[]", "cycles": "cycles[]", "changes": "changes[]",
                         "showcase": "showcase[]", "results": "results", "score": "score",
                         "champion": "champion", "coverage": "coverage", "tokens": "tokens",
                         "live": "live", "next": "next", "per_tool": "per_tool",
                         "counters": "counters", "specialties": "specialties[]"}.get(k, "leaf")
                walk(x, f"{path}.{k}", child if isinstance(x, (dict, list)) else
                     ("request" if k in ("request", "answer") else "leaf"))
        elif isinstance(v, list):
            for i, x in enumerate(v):
                walk(x, f"{path}[{i}]", ctx)
        elif isinstance(v, str):
            text_ok(v, path, free=(ctx == "request"))
        elif not (v is None or isinstance(v, (bool, int, float))):
            fail(path, f"type {type(v).__name__}")

    walk(data, "vitrine", "")

    # Passe 2 : le fond.
    for d in (data.get("generated", ""), (data.get("champion") or {}).get("since", ""),
              (data.get("live") or {}).get("since", ""),
              *[h["date"] for h in data["history"]], *[c["date"] for c in data["cycles"]],
              *[c["date"] for c in data.get("changes", [])],
              *[t.get("learned_on") or "" for t in data["tools"]]):
        if d and not _DATE.match(d):
            fail("vitrine", f"date mal formée {d!r}")
    nxt = data.get("next") or {}
    if nxt.get("at") is not None and not _ISO.match(str(nxt["at"])):
        fail("vitrine.next", f"date mal formée {nxt['at']!r}")
    if data.get("live") and data["live"].get("phase") not in PHASES:
        fail("vitrine.live", f"phase inconnue {data['live'].get('phase')!r}")
    for c in data.get("changes", []):
        if c["area"] not in AREAS:
            fail("vitrine.changes", f"domaine inconnu {c['area']!r}")
    for t in data["tools"]:
        if t["id"] not in known:
            fail("vitrine.tools", f"outil inconnu {t['id']!r}")
        if t["domain"] not in DOMAINS:
            fail("vitrine.tools", f"domaine inconnu {t['domain']!r}")
    from matthew.specialist import SPECIALTIES
    for sp in data.get("specialties", []):
        if sp["id"] not in SPECIALTIES or (sp["label"], sp["label_en"]) != SPECIALTIES[sp["id"]][:2]:
            fail("vitrine.specialties", f"spécialité inconnue {sp['id']!r}")
        for k in ("score", "floor", "reference"):
            if sp.get(k) is not None and not (isinstance(sp[k], (int, float)) and 0 <= sp[k] <= 1):
                fail("vitrine.specialties", f"{k} hors de [0, 1]")
    for s in data["showcase"]:
        if not re.fullmatch(r"[0-9a-f]{8}", s["id"]):
            fail("vitrine.showcase", f"identifiant {s['id']!r}")
        if s["target"] not in known:
            fail("vitrine.showcase", f"outil inconnu {s['target']!r}")
        if s["request"] not in synthetic:
            fail("vitrine.showcase", "demande absente de l'amorçage synthétique")
        for r in s["results"].values():
            if r["chosen"] is not None and r["chosen"] not in known:
                fail("vitrine.showcase", f"outil choisi inconnu {r['chosen']!r}")
    return count


# ── Le site ──────────────────────────────────────────────────────────────────

SITE_URL = "https://teddydarkvador.github.io/matthew-vitrine/"


def atom(data: dict) -> str:
    """Flux Atom : les améliorations et les promotions, sans autre champ que la liste blanche."""
    from xml.sax.saxutils import escape
    entries = [(c["date"], f"{c['area']} — {c['title']}") for c in data.get("changes", [])]
    entries += [(h["date"], f"Nouveau champion : score {h['score']['composite']:.2f}")
                for h in data.get("history", []) if h.get("promoted") and (h.get("score") or {}).get("composite") is not None]
    entries.sort(key=lambda e: e[0], reverse=True)
    items = []
    for date, title in entries[:50]:
        eid = hashlib.sha1(f"{date}|{title}".encode()).hexdigest()[:16]
        items.append(f"<entry><id>urn:matthew:{eid}</id><title>{escape(title)}</title>"
                     f"<updated>{date}T00:00:00Z</updated><link href=\"{SITE_URL}\"/></entry>")
    updated = (entries[0][0] if entries else data["generated"][:10]) + "T00:00:00Z"
    return ('<?xml version="1.0" encoding="utf-8"?>\n<feed xmlns="http://www.w3.org/2005/Atom">'
            f"<title>Matthew — les améliorations</title><id>{SITE_URL}</id><updated>{updated}</updated>"
            f'<link href="{SITE_URL}"/><link rel="self" href="{SITE_URL}feed.xml"/>'
            "<author><name>Matthew</name></author>" + "".join(items) + "</feed>\n")


def og_image(data: dict, path: Path) -> bool:
    """Carte de partage 1200×630 (Pillow) : le score et la couverture du jour."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return False
    W, H = 1200, 630
    img = Image.new("RGB", (W, H), (4, 10, 20))
    dr = ImageDraw.Draw(img)
    for r in range(260, 20, -12):            # halo du noyau
        c = int(18 + (260 - r) / 260 * 70)
        dr.ellipse([900 - r, 315 - r, 900 + r, 315 + r], outline=(0, c, c + 30), width=2)
    dr.ellipse([860, 275, 940, 355], fill=(80, 230, 255))

    def font(size, bold=False):
        for name in (("segoeuib.ttf" if bold else "segoeui.ttf"), "arial.ttf", "DejaVuSans.ttf"):
            try:
                return ImageFont.truetype(name, size)
            except OSError:
                continue
        return ImageFont.load_default()
    ch = data.get("champion") or {}
    sc = (ch.get("score") or {}).get("composite")
    cov = data["coverage"]
    dr.text((70, 80), "MATTHEW", font=font(84, True), fill=(120, 235, 255))
    dr.text((72, 185), "Une IA qui apprend chaque nuit — en public", font=font(36), fill=(200, 225, 235))
    dr.text((72, 300), f"Score {sc:.2f}" if sc is not None else "Premier entraînement en cours",
            font=font(64, True), fill=(255, 255, 255))
    dr.text((72, 390), f"{cov['tools_covered']}/{cov['tools_total']} outils appris · {cov['examples']} exemples vérifiés",
            font=font(32), fill=(160, 200, 215))
    dr.text((72, 540), f"Mis à jour le {data['generated'][:10]}", font=font(26), fill=(110, 150, 165))
    img.save(path, optimize=True)
    return True


BROWSERS = [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe"]


def og_screenshot(out: Path, timeout: float = 120) -> bool:
    """og.png = une capture de la scène 3D du jour (Edge ou Chrome sans interface, rendu
    logiciel), servie depuis un petit serveur local le temps de la photo. Sinon la carte texte."""
    import functools
    import shutil as sh
    import tempfile
    import threading
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
    exe = next((b for b in BROWSERS if Path(b).exists()), None) or sh.which("msedge") or sh.which("chrome")
    if not exe:
        return False

    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(out)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    shot = Path(tempfile.mkdtemp(prefix="matthew-og-")) / "og.png"
    try:
        subprocess.run([exe, "--headless=new", "--use-angle=swiftshader", "--enable-unsafe-swiftshader",
                        "--hide-scrollbars", f"--user-data-dir={shot.parent / 'profil'}", "--window-size=1200,630",
                        "--virtual-time-budget=25000", f"--screenshot={shot}",
                        f"http://127.0.0.1:{httpd.server_address[1]}/?og=1"],
                       capture_output=True, timeout=timeout)
        if not shot.exists() or shot.stat().st_size < 30_000:
            return False
        from PIL import Image
        Image.open(shot).convert("RGB").save(out / "og.png", optimize=True)
        return True
    except (OSError, subprocess.TimeoutExpired, ImportError):
        return False
    finally:
        httpd.shutdown()
        sh.rmtree(shot.parent, ignore_errors=True)


def bust_cache(out: Path) -> dict:
    """style.css et app.js appelés avec l'empreinte de leur contenu (?v=…) : GitHub Pages
    autorise 10 min de cache, et une page neuve avec l'ancien CSS / JS s'affichait sans
    décor ni scène (constaté le 2026-10-21)."""
    page = out / "index.html"
    html = page.read_text(encoding="utf-8")
    tags = {}
    for name, attr in (("style.css", "href"), ("app.js", "src")):
        tags[name] = hashlib.sha1((out / name).read_bytes()).hexdigest()[:10]
        html = re.sub(rf'{attr}="{re.escape(name)}(\?v=\w+)?"', f'{attr}="{name}?v={tags[name]}"', html)
    page.write_text(html, encoding="utf-8")
    return tags


def build(out: Path, root: Path | None = None) -> dict:
    """Écrit le site complet dans `out`. Rien n'est écrit si le garde-fou refuse."""
    root = root or data_dir()
    data = gather(root)
    n = check_public(data, root)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    for f in SITE.iterdir():
        if f.is_file():
            shutil.copy2(f, out / f.name)
        elif f.is_dir():                                 # assets/ : modèles et ciel (CC0)
            shutil.copytree(f, out / f.name, dirs_exist_ok=True)
    bust_cache(out)
    (out / ".nojekyll").write_text("", encoding="utf-8")
    (out / "data").mkdir(exist_ok=True)
    (out / "data" / "vitrine.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    og = og_image(data, out / "og.png")
    (out / "feed.xml").write_text(atom(data), encoding="utf-8")
    return {"fields_checked": n, "tools": len(data["tools"]), "showcase": len(data["showcase"]),
            "history": len(data["history"]), "og": og}


def _git(repo: Path, *args, runner=subprocess.run):
    return runner(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=180)


def publish(repo: Path | None = None, cfg: dict | None = None, runner=subprocess.run,
            do_replay: bool = True) -> dict:
    """replay → build dans le clone local du dépôt public → commit → push."""
    cfg = cfg or config()
    repo = Path(repo or cfg.get("vitrine_repo") or "")
    if not (repo / ".git").exists():
        return {"ok": False, "reason": f"pas de dépôt git de vitrine à {repo}"}
    if do_replay:
        try:
            replay()
        except Exception as e:                     # Ollama éteint : on publie quand même les agrégats
            print(f"[Vitrine] rejeu impossible : {e}")
    res = build(repo)
    res["og_scene"] = og_screenshot(repo)              # la vraie scène du jour, sinon la carte texte
    _git(repo, "add", "-A", runner=runner)
    st = _git(repo, "status", "--porcelain", runner=runner)
    if not (st.stdout or "").strip():
        return {"ok": True, "pushed": False, "reason": "rien de nouveau", **res}
    _git(repo, "commit", "-m", f"Vitrine du {datetime.now().strftime('%Y-%m-%d %H:%M')}", runner=runner)
    push = _git(repo, "push", "origin", "HEAD", runner=runner)
    return {"ok": push.returncode == 0, "pushed": push.returncode == 0,
            "reason": (push.stderr or "")[-200:], **res}


# ── Le drapeau : publier dès qu'il y a une amélioration ──────────────────────
#
# Tout ce qui améliore Matthew lève le drapeau (flag) : un commit ou une fusion dans le
# dépôt (crochets Git post-commit / post-merge, posés par `hooks`), un cycle de la boucle
# de nuit (flywheel), y compris quand Night Shift fait entrer un correctif. Le lanceur
# (`run`) ne publie que si le drapeau est levé, au plus une fois toutes les MIN_GAP_S,
# et ne le baisse qu'après un push réussi. Une tâche planifiée toutes les 30 minutes
# rattrape un drapeau resté levé (réseau coupé, PC en veille, publication trop proche).

MIN_GAP_S = 300
HOOK_MARK = "# matthew-vitrine"


def _flag_path(root: Path | None = None) -> Path:
    return (root or data_dir()) / "vitrine" / "FLAG"


def flag(reason: str, root: Path | None = None) -> dict:
    """Lève le drapeau (les raisons s'accumulent jusqu'à la publication)."""
    path = _flag_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    cur = _read(path, {"since": time.time(), "reasons": []})
    cur["reasons"] = (cur.get("reasons", []) + [f"{datetime.now():%Y-%m-%d %H:%M} {reason[:60]}"])[-20:]
    path.write_text(json.dumps(cur, ensure_ascii=False), encoding="utf-8")
    return cur


def run(root: Path | None = None, cfg: dict | None = None, publisher=None, clock=time.time) -> dict:
    """Publie si le drapeau est levé ; sinon ne fait rien. Ne lève jamais d'exception."""
    root = root or data_dir()
    cfg = cfg or config()
    path, state_path = _flag_path(root), root / "vitrine" / "last_publish.json"
    if not path.exists():
        return {"ran": False, "reason": "drapeau baissé"}
    if not cfg.get("vitrine_publish"):
        return {"ran": False, "reason": "vitrine_publish=false"}
    last = _read(state_path, {}).get("at", 0)
    if clock() - last < MIN_GAP_S:
        return {"ran": False, "reason": f"publiée il y a {int(clock() - last)} s ; la tâche planifiée reprendra"}
    lock = root / "vitrine" / "publish.lock"
    if lock.exists() and clock() - lock.stat().st_mtime < 1800:
        return {"ran": False, "reason": "publication déjà en cours"}
    lock.write_text(str(clock()), encoding="utf-8")
    try:
        flagged = _read(path, {})
        try:
            res = (publisher or (lambda: publish(Path(cfg["vitrine_repo"]), cfg)))()
        except Exception as e:
            res = {"ok": False, "reason": f"{type(e).__name__}: {e}"}
        if res.get("ok"):
            # Baissé seulement s'il n'a pas été relevé pendant la publication.
            if _read(path, {}) == flagged:
                path.unlink(missing_ok=True)
            state_path.write_text(json.dumps({"at": clock(), "reasons": flagged.get("reasons", []),
                                              "pushed": res.get("pushed")}), encoding="utf-8")
        return {"ran": True, **{k: res.get(k) for k in ("ok", "pushed", "reason")}}
    finally:
        lock.unlink(missing_ok=True)


def spawn() -> None:
    """Lance `run` en arrière-plan, détaché : un commit ne doit pas attendre la publication."""
    from matthew import REPO
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen([sys.executable, "-m", "matthew.vitrine", "run"], cwd=str(REPO),
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                     creationflags=flags, close_fds=True)


def install_hooks(repo: Path | None = None) -> list[str]:
    """Crochets Git post-commit et post-merge : lever le drapeau et publier en fond.
    Un crochet déjà présent est gardé : notre ligne s'ajoute à la fin."""
    from matthew import REPO
    hooks = Path(repo or REPO) / ".git" / "hooks"
    py = sys.executable.replace("\\", "/")
    line = f'"{py}" -m matthew.vitrine flag "{{kind}}" --spawn >/dev/null 2>&1 || true  {HOOK_MARK}\n'
    done = []
    for name, kind in (("post-commit", "code : nouveau commit"), ("post-merge", "code : fusion")):
        f = hooks / name
        text = f.read_text(encoding="utf-8") if f.exists() else "#!/bin/sh\n"
        if HOOK_MARK not in text:
            f.write_text(text.rstrip("\n") + "\n" + line.replace("{kind}", kind), encoding="utf-8")
        done.append(str(f))
    return done


def schedule(minutes: int = 30, runner=subprocess.run) -> dict:
    """Tâche Windows « Matthew vitrine » : toutes les N minutes, publier si le drapeau est levé."""
    from matthew import REPO
    cmd_file = data_dir() / "vitrine" / "run.cmd"
    cmd_file.write_text("\r\n".join(["@echo off", f'cd /d "{REPO}"', "set PYTHONIOENCODING=utf-8",
                                      f'"{sys.executable}" -m matthew.vitrine run >> "{data_dir() / "vitrine" / "run.log"}" 2>&1',
                                      ""]), encoding="utf-8")
    cp = runner(["schtasks", "/Create", "/TN", "Matthew vitrine", "/SC", "MINUTE", "/MO", str(minutes),
                 "/TR", f'"{cmd_file}"', "/F"], capture_output=True, text=True)
    return {"ok": cp.returncode == 0, "out": (cp.stdout or cp.stderr).strip()[:200]}


def setup(repo: Path, name: str = "matthew-vitrine", runner=subprocess.run) -> dict:
    """Première mise en ligne, À LANCER PAR L'UTILISATEUR (c'est elle qui rend public) :
    crée le dépôt GitHub public depuis le clone local, pousse, active GitHub Pages."""
    repo = Path(repo)
    if not (repo / ".git").exists():
        return {"ok": False, "reason": f"pas de dépôt local à {repo} : python -m matthew.vitrine build {repo}"}
    who = runner(["gh", "api", "user", "--jq", ".login"], capture_output=True, text=True, timeout=60)
    owner = (who.stdout or "").strip()
    if not owner:
        return {"ok": False, "reason": "gh n'est pas connecté (gh auth login)"}
    steps = [["gh", "repo", "create", f"{owner}/{name}", "--public", "--source", str(repo), "--push",
              "--description", "Matthew, l'IA personnelle de JARVIS, apprend chaque nuit — en public, en lecture seule."],
             ["gh", "api", "-X", "POST", f"repos/{owner}/{name}/pages",
              "-f", "source[branch]=main", "-f", "source[path]=/"]]
    for cmd in steps:
        cp = runner(cmd, capture_output=True, text=True, timeout=180)
        if cp.returncode != 0 and "already" not in (cp.stderr or "").lower():
            return {"ok": False, "step": cmd[1:3], "reason": (cp.stderr or cp.stdout)[-300:]}
    return {"ok": True, "url": f"https://{owner.lower()}.github.io/{name}/",
            "note": "GitHub Pages met 1 à 2 minutes à publier la première fois."}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="matthew.vitrine")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("replay")
    s = sub.add_parser("setup", help="première mise en ligne (crée le dépôt public, active Pages)")
    s.add_argument("--repo", default=None)
    f = sub.add_parser("flag", help="lever le drapeau (une amélioration vient d'arriver)")
    f.add_argument("reason")
    f.add_argument("--spawn", action="store_true", help="et lancer la publication en fond")
    sub.add_parser("run", help="publier si le drapeau est levé")
    sub.add_parser("hooks", help="poser les crochets Git post-commit / post-merge")
    sub.add_parser("schedule", help="tâche planifiée de rattrapage, toutes les 30 min")
    b = sub.add_parser("build")
    b.add_argument("out")
    p = sub.add_parser("publish")
    p.add_argument("--no-replay", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "replay":
        out = replay()
    elif a.cmd == "setup":
        out = setup(Path(a.repo or config()["vitrine_repo"]))
    elif a.cmd == "flag":
        out = flag(a.reason)
        if a.spawn:
            spawn()
    elif a.cmd == "run":
        out = run()
    elif a.cmd == "hooks":
        out = install_hooks()
    elif a.cmd == "schedule":
        out = schedule()
    elif a.cmd == "build":
        out = build(Path(a.out))
    else:
        out = publish(do_replay=not a.no_replay)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
