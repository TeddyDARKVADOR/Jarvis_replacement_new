"""
e2e/launch.py — (interne) démarre JARVIS dans le bac à sable, sur le port donné.

    python -m e2e.launch --port 23456 --mode headless

Lancé par e2e/sandbox.py, jamais à la main : il tourne dans la COPIE du dépôt.
`headless` est exactement la commande d'Oracle (server.run_headless), à un
détail près, le port du tableau de bord : dashboard/server.py lit sa constante
PORT au moment de servir, on la change donc avant, comme run_headless change
déjà _ensure_network_access. main.py, dashboard/ et core/ ne sont pas touchés.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(prog="python -m e2e.launch")
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--mode", choices=("headless",), default="headless")
    args = ap.parse_args()

    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    if not (root / "config" / "api_key.json").exists() or (root / ".git").exists():
        print("e2e.launch : refusé — ce n'est pas un bac à sable (lancez python -m e2e).", flush=True)
        return 2

    import dashboard.server as dserver
    dserver.PORT = args.port

    sys.argv = ["server.run_headless"]
    from server import run_headless
    return run_headless.main()


if __name__ == "__main__":
    raise SystemExit(main())
