"""
Gardien d'ejection du mode portable.

Lance JARVIS comme sous-processus et surveille la presence de la cle USB, ancree
sur un FICHIER precis de la cle (le lanceur, passe par --anchor). Si la cle
disparait pendant que JARVIS tourne (ejection brutale), on arrete JARVIS tout de
suite : mieux vaut un arret net qu'une ecriture a moitie faite sur une cle absente.

Usage (pose par Lancer-JARVIS.bat) :
    python eject_guard.py --anchor "E:\\JARVIS\\Lancer-JARVIS.bat" -- \
        "<python>" "<app>\\main.py" --local

Sans "-- <commande>", le script ne fait rien et sort en 0 : l'import et un appel
a vide sont donc sans effet (la sonde de verification s'en sert).
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

#: la cle est jugee partie apres ce delai sans que l'ancre reponde (secondes)
GRACE_SECONDS = 4.0
POLL_SECONDS = 2.0


def _anchor_present(anchor: Path, size: int | None) -> bool:
    """Vrai si l'ancre repond encore. Une cle arrachee leve OSError (ENODEV)."""
    try:
        st = anchor.stat()
    except OSError:
        return False
    # Un autre disque remappe sur la meme lettre avec un fichier homonyme aurait
    # une taille differente : on l'ecarte.
    if size is not None and st.st_size != size:
        return False
    return True


def _kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                       capture_output=True)
    else:
        proc.terminate()
    try:
        proc.wait(timeout=10)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def guard(anchor: Path, cmd: list[str]) -> int:
    """Lance cmd, surveille l'ancre, renvoie le code de sortie de l'enfant
    (ou 99 si la cle a ete retiree)."""
    try:
        anchor_size = anchor.stat().st_size
    except OSError:
        anchor_size = None

    proc = subprocess.Popen(cmd)
    missing_since: float | None = None
    try:
        while True:
            code = proc.poll()
            if code is not None:
                return code
            if _anchor_present(anchor, anchor_size):
                missing_since = None
            else:
                now = time.monotonic()
                if missing_since is None:
                    missing_since = now
                elif now - missing_since >= GRACE_SECONDS:
                    # cle confirmee absente : on arrete JARVIS
                    _kill_tree(proc)
                    return 99
            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        _kill_tree(proc)
        return proc.poll() if proc.poll() is not None else 130


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    anchor: Path | None = None
    cmd: list[str] = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--anchor" and i + 1 < len(argv):
            anchor = Path(argv[i + 1])
            i += 2
        elif a == "--":
            cmd = argv[i + 1:]
            break
        else:
            i += 1
    if not cmd:
        # rien a lancer : sans effet (import / sonde)
        return 0
    if anchor is None:
        # pas d'ancre : on lance quand meme, sans surveillance
        return subprocess.call(cmd)
    return guard(anchor, cmd)


if __name__ == "__main__":
    raise SystemExit(main())
