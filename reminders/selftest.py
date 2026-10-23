"""
reminders/selftest.py — un rappel arrive, une fois, là où il doit.

    python -m reminders.selftest

Sans planificateur, sans réseau, sans toucher memory/reminders.json : tout se
passe dans un dossier temporaire.
"""
from __future__ import annotations

import ast
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import reminders as R

_results: list[tuple[str, bool, str]] = []


def check(name: str):
    def deco(fn):
        try:
            _results.append((name, True, str(fn() or "")))
        except AssertionError as e:
            _results.append((name, False, str(e)))
        except Exception as e:
            _results.append((name, False, f"{type(e).__name__}: {e}"))
        return fn
    return deco


class _UI:
    def __init__(self):
        self.content, self.log = [], []

    def show_content(self, title, text):
        self.content.append((title, text))

    def write_log(self, line):
        self.log.append(line)


class _MatthewAssistant:
    def __init__(self):
        self.ui, self.said, self.session, self._dashboard = _UI(), [], object(), None

    def plugin_say(self, text):
        self.said.append(text)


@check("1 inscrit, échu, livré une seule fois")
def _cycle():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "r.json"
        now = datetime.now()
        R.add("JARVISReminder_a", now - timedelta(seconds=3), "Appeler maman", path=path)
        R.add("JARVISReminder_b", now + timedelta(hours=1), "Plus tard", path=path)
        R.add("JARVISReminder_old", now - timedelta(days=1), "Trop vieux", path=path)
        due = R.due(now, path=path)
        assert [rid for rid, _ in due] == ["JARVISReminder_a"], due
        j = _MatthewAssistant()
        saved = R._may_speak
        R._may_speak = lambda: True            # la politique permet de parler
        try:
            how = R.deliver(j, *due[0])
        finally:
            R._may_speak = saved
        assert j.ui.content == [("RAPPEL", "Appeler maman")], j.ui.content
        assert j.said and "Appeler maman" in j.said[0], j.said
        R.mark_delivered("JARVISReminder_a", how, path=path)
        assert R.due(now, path=path) == [], "livré deux fois"
        assert [rid for rid, _ in R.upcoming(path=path)] == ["JARVISReminder_b"]
        return f"livré par : {how} ; le rappel de la veille n'est pas rejoué"


@check("2 la tâche planifiée se tait quand Matthew a déjà livré")
def _system_task_skips():
    import actions.reminder as AR
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        (base / "memory").mkdir()
        saved_base, saved_scripts = AR._base_dir, AR._scripts_dir
        AR._base_dir = lambda: base
        AR._scripts_dir = lambda: base
        try:
            script = AR._write_notify_script("JARVISReminder_x", "Test", "windows")
        finally:
            AR._base_dir, AR._scripts_dir = saved_base, saved_scripts
        body = script.read_text(encoding="utf-8")
        ast.parse(body)
        assert "time.sleep(20)" in body and "JARVISReminder_x" in body
        R.add("JARVISReminder_x", datetime.now(), "Test", path=base / "memory" / "reminders.json")
        R.mark_delivered("JARVISReminder_x", "hud", path=base / "memory" / "reminders.json")
        fast = body.replace("time.sleep(20)", "time.sleep(0)")
        script.write_text(fast, encoding="utf-8")
        proc = subprocess.run([sys.executable, str(script)], capture_output=True, timeout=30)
        assert proc.returncode == 0, proc.stderr[-300:]
        assert not script.exists(), "le script ne s'est pas effacé"
        return "script valide, sortie silencieuse et auto-effacement"


@check("3 quand la politique interdit de parler, le rappel s'affiche sans voix")
def _quiet():
    j = _MatthewAssistant()
    saved = R._may_speak
    R._may_speak = lambda: False           # réunion, sommeil…
    try:
        how = R.deliver(j, "x", {"message": "Réunion"})
    finally:
        R._may_speak = saved
    assert how == "hud" and not j.said, (how, j.said)
    return "HUD seul, aucune phrase envoyée à Gemini"


@check("4 avec un téléphone appairé, une notification part sur /ws")
def _phone():
    import asyncio

    from server import notify as N
    sent: list[dict] = []

    class _Dash:
        _clients = {object()}                 # un client connecté

        async def broadcast(self, msg):
            sent.append(msg)

    async def scenario():
        N.reset()
        j = _MatthewAssistant()
        j._dashboard = _Dash()
        j.session = None                      # pas de session : pas de voix
        saved_attach_thread = R._thread
        R._thread = type("T", (), {"is_alive": lambda self: True})()   # pas de boucle de fond
        try:
            R.attach(j)                       # lie le hub depuis CETTE boucle
        finally:
            R._thread = saved_attach_thread
        how = R.deliver(j, "x", {"message": "Prendre le train"})
        await asyncio.sleep(0.05)
        return how

    try:
        how = asyncio.run(scenario())
    finally:
        N.reset()
    notes = [m for m in sent if m.get("type") == "notification"]
    assert "téléphone" in how and notes and notes[0]["text"] == "Prendre le train", (how, sent)
    return f"livré par : {how} ; priorité {notes[0]['priority']}"


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    width = max(len(n) for n, _, _ in _results)
    print()
    for name, ok, detail in _results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name:<{width}}  {detail}")
    failed = sum(not ok for _, ok, _ in _results)
    print(f"\n  {len(_results) - failed}/{len(_results)} passed\n")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
