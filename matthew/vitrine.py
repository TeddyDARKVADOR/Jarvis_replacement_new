"""
matthew/vitrine.py — la vitrine publique : regarder Matthew apprendre, sans accès à rien.

    python -m matthew.vitrine replay      rejoue les scénarios de démonstration sur le champion
    python -m matthew.vitrine build DIR   écrit le site (page + data/vitrine.json + og.png) dans DIR
    python -m matthew.vitrine publish     replay + build dans le dépôt de la vitrine, puis git push
                                          (GitHub Pages republie tout seul à chaque push)
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
    "shutdown_jarvis": ("Arrêt de JARVIS", "Système"), "close_camera": ("Fermer la caméra", "Système"),
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
    "capabilities": ("Ses capacités", "JARVIS"), "usage_report": ("Consommation", "JARVIS"),
    "quotas": ("Quotas", "JARVIS"), "incidents": ("Incidents", "JARVIS"),
    "extensions": ("Extensions", "JARVIS"), "mcp": ("Connecteurs MCP", "JARVIS"),
    "skills": ("Compétences", "JARVIS"), "portable": ("Mode clé USB", "JARVIS"),
    "matthew": ("Apprendre à Matthew", "JARVIS"),
}
DOMAINS = ["Système", "Bureau", "Infos", "Communication", "Mémoire", "Code", "JARVIS", "Autres"]


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
        if t.startswith("__") or t in have or scrub(r["messages"][1]["content"]) != r["messages"][1]["content"]:
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
    path.write_text(json.dumps(cur, ensure_ascii=False, indent=1), encoding="utf-8")
    return cur


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
        out[s["id"]] = {"chosen": calls[0] if calls else None, "ok": calls[:1] == [s["target"]],
                        "ms": int(msg.get("_latency", 0) * 1000)}
    if out:
        d = root / "vitrine" / "days"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{day}.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return out


# ── Données publiques ────────────────────────────────────────────────────────

def _score(s: dict | None) -> dict | None:
    if not s:
        return None
    return {k: (round(float(s[k]), 3) if isinstance(s.get(k), (int, float)) else None)
            for k in ("composite", "tool_name", "tool_args", "irrelevance", "valid", "latency_p50")}


def _day(stamp: str) -> str:
    return f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}" if re.match(r"^\d{8}", stamp or "") else ""


def gather(root: Path | None = None) -> dict:
    """Construit les données publiques champ par champ (rien n'est recopié tel quel)."""
    root = root or data_dir()
    j = Journal(root)
    decls = [d["name"] for d in j.tools()]
    syn = [r for r in synthetic_records(root) if not r.get("target", "").startswith("__")]
    per_ex: dict[str, int] = {}
    for r in syn:
        per_ex[r["target"]] = per_ex.get(r["target"], 0) + 1
    last = _read(root / "last_eval.json", {})
    per_tool = last.get("per_tool") or {}
    tools = []
    for name in decls:
        label, dom = TOOLS.get(name, (name.replace("_", " "), "Autres"))
        acc = per_tool.get(name)
        tools.append({"id": name, "label": label, "domain": dom, "examples": per_ex.get(name, 0),
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
            history.append({"date": d, "score": _score(ev["candidate"]), "promoted": bool(ev.get("promote")),
                            "examples": int(data.get("train", 0)) + int(data.get("eval", 0))})
    champ = _read(root / "champion.json", None)
    days = {}
    for f in sorted((root / "vitrine" / "days").glob("*.json")):
        days[f.stem] = _read(f, {})
    show = []
    for s in showcase(root):
        show.append({"id": s["id"], "request": s["request"], "target": s["target"],
                     "heldout": bool(s.get("heldout")),
                     "results": {d: {"chosen": v[s["id"]]["chosen"], "ok": bool(v[s["id"]]["ok"]),
                                     "ms": int(v[s["id"]].get("ms", 0))}
                                 for d, v in days.items() if s["id"] in v}})
    toks = usage_summary(root)
    return {
        "schema": SCHEMA, "name": NAME, "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "champion": {"since": _day(champ.get("since", "")), "base": str(champ.get("base", ""))[:60],
                     "score": _score(champ.get("score"))} if champ else None,
        "coverage": {"tools_total": len(decls), "tools_covered": len(per_ex), "examples": len(syn)},
        "tools": tools, "domains": DOMAINS, "history": history, "cycles": cycles[-60:],
        "tokens": {"matthew": sum(v["in"] + v["out"] for k, v in toks.items() if k == "matthew"),
                   "school": sum(v["in"] + v["out"] for k, v in toks.items() if k == "teacher")},
        "showcase": show,
    }


# ── Le garde-fou ─────────────────────────────────────────────────────────────

ALLOWED = {
    "": {"schema", "name", "generated", "champion", "coverage", "tools", "domains", "history", "cycles",
         "tokens", "showcase"},
    "champion": {"since", "base", "score"},
    "score": {"composite", "tool_name", "tool_args", "irrelevance", "valid", "latency_p50"},
    "coverage": {"tools_total", "tools_covered", "examples"},
    "tools[]": {"id", "label", "domain", "examples", "accuracy"},
    "history[]": {"date", "score", "promoted", "examples"},
    "cycles[]": {"date", "outcome"},
    "tokens": {"matthew", "school"},
    "showcase[]": {"id", "request", "target", "heldout", "results"},
    "result": {"chosen", "ok", "ms"},
}
_SAFE = re.compile(r"^[\w .:/+'’«»()\-éèêàâîïôûùçÉÀ%,]{0,80}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}( \d{2}:\d{2})?$")


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
    known = {d["name"] for d in j.tools()} | set(TOOLS)
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
        if scrub(v) != v:
            fail(path, "contient un secret ou une coordonnée")
        if v.strip() in private:
            fail(path, "texte du journal réel")
        if not free and ("\\" in v or re.search(r"[A-Za-z]:/", v)):
            fail(path, "ressemble à un chemin local")
        if not free and not _SAFE.match(v):
            fail(path, f"texte hors gabarit {v[:40]!r}")

    def walk(v, path: str, ctx: str):
        nonlocal count
        count += 1
        if isinstance(v, dict):
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
                child = {"tools": "tools[]", "history": "history[]", "cycles": "cycles[]",
                         "showcase": "showcase[]", "results": "results", "score": "score",
                         "champion": "champion", "coverage": "coverage", "tokens": "tokens"}.get(k, "leaf")
                walk(x, f"{path}.{k}", child if isinstance(x, (dict, list)) else
                     ("request" if k == "request" else "leaf"))
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
              *[h["date"] for h in data["history"]], *[c["date"] for c in data["cycles"]]):
        if d and not _DATE.match(d):
            fail("vitrine", f"date mal formée {d!r}")
    for t in data["tools"]:
        if t["id"] not in known:
            fail("vitrine.tools", f"outil inconnu {t['id']!r}")
        if t["domain"] not in DOMAINS:
            fail("vitrine.tools", f"domaine inconnu {t['domain']!r}")
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
    (out / ".nojekyll").write_text("", encoding="utf-8")
    (out / "data").mkdir(exist_ok=True)
    (out / "data" / "vitrine.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    og = og_image(data, out / "og.png")
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
    _git(repo, "add", "-A", runner=runner)
    st = _git(repo, "status", "--porcelain", runner=runner)
    if not (st.stdout or "").strip():
        return {"ok": True, "pushed": False, "reason": "rien de nouveau", **res}
    _git(repo, "commit", "-m", f"Vitrine du {datetime.now().strftime('%Y-%m-%d %H:%M')}", runner=runner)
    push = _git(repo, "push", "origin", "HEAD", runner=runner)
    return {"ok": push.returncode == 0, "pushed": push.returncode == 0,
            "reason": (push.stderr or "")[-200:], **res}


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
    b = sub.add_parser("build")
    b.add_argument("out")
    p = sub.add_parser("publish")
    p.add_argument("--no-replay", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "replay":
        out = replay()
    elif a.cmd == "setup":
        out = setup(Path(a.repo or config()["vitrine_repo"]))
    elif a.cmd == "build":
        out = build(Path(a.out))
    else:
        out = publish(do_replay=not a.no_replay)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
