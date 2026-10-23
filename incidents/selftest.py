"""
incidents/selftest.py — les erreurs réelles : comptées, anonymisées, partagées seulement si on le veut.

    python -m incidents.selftest

L'état est mis dans un dossier jetable ; l'envoi est testé vers un dépôt git local.
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
import tempfile
import threading
from datetime import datetime
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import incidents as I

ROOT = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp(prefix="matthew-assistant-incidents-"))
I.STATE = TMP / "incidents.json"          # « python -m » importe le paquet avant ce fichier : affectation directe
_results: list[tuple[str, bool, str]] = []


def check(name: str):
    def deco(fn):
        try:
            I.STATE.unlink()
        except OSError:
            pass
        try:
            _results.append((name, True, str(fn() or "")))
        except AssertionError as e:
            _results.append((name, False, str(e)))
        except Exception as e:
            _results.append((name, False, f"{type(e).__name__}: {e}"))
        return fn
    return deco


@check("1 contrat V2 : incidents/ n'importe rien de Matthew au chargement")
def _contract():
    banned = ("main", "core", "actions", "plugins", "cognition", "server", "dashboard", "client_desktop", "memory",
              "nightshift")
    for f in ROOT.glob("*.py"):
        for node in ast.parse(f.read_text(encoding="utf-8")).body:
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            assert not [n for n in names if n.split(".")[0] in banned], (f.name, names)
    return "bibliothèque standard ; la config lue sous garde"


@check("2 anonymisation : clés, e-mails, adresses, chemins et fichiers personnels, nombres, citations")
def _generic():
    nasty = (r"Failed to open C:\Users\teddy\Documents\CV confidentiel.docx with key AIzaSyA1234567890abcdefghijklmnop "
             r"and Bearer abcdef1234567890 at https://api.groq.com/v1/x?token=abc for teddy@gmail.com after 3.2 s "
             "« Bonjour Paul » /home/teddy/notes perso.txt gsk_" + "k" * 30)
    out = I.generic(nasty)
    for leak in ("teddy", "confidentiel", "AIza", "abcdef123", "token=abc", "gmail", "Paul", "notes", "gsk_", "3.2"):
        assert leak not in out, (leak, out)
    assert "api.groq.com" in out, "le service en cause reste lisible"
    assert I.generic(r"C:\DEV\Jarvis_replacement_new\meeting\capture.py line 12") == "meeting/capture.py line N"
    return out[:110]


@check("3 regroupement : même erreur à des valeurs près = une signature, comptée par jour")
def _group():
    d = datetime(2026, 10, 16, 10)
    a = I.record("log", "", "", "Briefing phase 2 failed: timed out after 4.0s", now=d)
    b = I.record("log", "", "", "Briefing phase 2 failed: timed out after 5.2s", now=d)
    c = I.record("log", "", "", "quota exceeded for gemini", now=datetime(2026, 10, 17, 9))
    assert a == b != c, (a, b, c)
    st = json.loads(I.STATE.read_text(encoding="utf-8"))
    assert st["items"][a]["days"] == {"2026-10-16": 2}
    return f"2 occurrences → {a} ; une autre erreur → {c}"


@check("4 capture : exception d'un fil (fichier:fonction du dépôt), lignes d'erreur du journal seulement")
def _capture():
    I.install_hooks()
    from incidents import selftest_helper_boom  # noqa: F401 — module de test : lève dans un fil
    t = threading.Thread(target=selftest_helper_boom.boom)
    t.start()
    t.join()
    seen = []

    class _UI:
        def write_log(self, text):
            seen.append(text)
    j = type("J", (), {"ui": _UI()})()
    I.attach(j)
    j.ui.write_log("SYS: Briefing phase 2 failed: timeout")
    j.ui.write_log("SYS: Réunion « point » : 2 rappels")
    assert seen == ["SYS: Briefing phase 2 failed: timeout", "SYS: Réunion « point » : 2 rappels"], "le journal passe intact"
    rows = I.digest()["incidents"]
    kinds = {(r["kind"], r["where"]) for r in rows}
    assert ("thread", "incidents/selftest_helper_boom.py:boom") in kinds, kinds
    assert any(r["kind"] == "log" and "failed" in r["message"] for r in rows)
    assert not any("rappels" in r["message"] for r in rows), "une ligne normale n'est pas une erreur"
    return f"{len(rows)} signatures : exception du fil + ligne « failed » ; les autres lignes ignorées"


@check("5 résumé partageable : 7 jours, le plus fréquent d'abord, aucun échantillon")
def _digest():
    now = datetime(2026, 10, 16, 12)
    for _ in range(3):
        I.record("log", "", "", "ERR: tts failed", now=now)
    I.record("log", "", "", "ERR: old one", now=datetime(2026, 10, 1))
    d = I.digest(now)
    assert [r["message"] for r in d["incidents"]] == ["ERR: tts failed"] and d["incidents"][0]["count"] == 3, d
    assert set(d["incidents"][0]) == {"signature", "kind", "exc", "where", "message", "count", "days", "last"}
    return f"{d['since']} → {d['until']} : « ERR: tts failed » ×3 ; l'erreur du 1er écartée"


@check("6 envoi : incidents/latest.json poussé sur nightshift-data (dépôt local), rien sinon")
def _push():
    bare, work = TMP / "remote.git", TMP / "work"
    git = lambda *a, cwd=None: subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True)   # noqa: E731
    git("init", "-q", "--bare", str(bare))
    seed = TMP / "seed"
    seed.mkdir()
    git("init", "-q", cwd=seed)
    git("checkout", "-q", "--orphan", "nightshift-data", cwd=seed)
    (seed / "state.json").write_text("{}")
    git("add", "-A", cwd=seed)
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init", cwd=seed)
    git("push", "-q", str(bare), "HEAD:nightshift-data", cwd=seed)
    work.mkdir()
    git("init", "-q", cwd=work)
    git("remote", "add", "origin", str(bare), cwd=work)
    I.record("log", "", "", "ERR: tts failed", now=datetime.now())
    assert I.push(work) is None
    shown = git("--git-dir", str(bare), "show", "nightshift-data:incidents/latest.json").stdout
    data = json.loads(shown)
    assert data["schema"] == I.SCHEMA and data["incidents"][0]["message"] == "ERR: tts failed"
    assert I.push(work) is None, "rien de neuf : aucun commit vide"
    log = git("--git-dir", str(bare), "log", "--oneline", "nightshift-data").stdout.strip().splitlines()
    assert len(log) == 2, log
    return "poussé une fois ; le second envoi identique n'a rien commité"


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    width = max(len(n) for n, _, _ in _results)
    failed = 0
    print()
    for name, ok, detail in _results:
        failed += not ok
        print(f"  [{'PASS' if ok else 'FAIL'}] {name:<{width}}  {detail}")
    print(f"\n  {len(_results) - failed}/{len(_results)} passed\n")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
