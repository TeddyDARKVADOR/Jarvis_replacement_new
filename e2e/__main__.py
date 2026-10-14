"""
python -m e2e — JARVIS de bout en bout (voir e2e/__init__.py).

    python -m e2e                    serveur, puis HUD
    python -m e2e --only serveur     une famille : serveur · hud
    python -m e2e --strict           la CI : un scénario ignoré faute de dépendance est rouge
    python -m e2e --keep             garde le bac à sable et les journaux de JARVIS
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from e2e.checks import Report  # noqa: E402

FAMILIES = ("serveur", "hud")


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(prog="python -m e2e", description="JARVIS de bout en bout, sans clé ni réseau.")
    ap.add_argument("--only", choices=FAMILIES, action="append", help="une famille (répétable)")
    ap.add_argument("--strict", action="store_true", help="un scénario ignoré faute de dépendance compte rouge")
    ap.add_argument("--keep", action="store_true", help="garder le bac à sable et les journaux")
    ap.add_argument("--workdir", help="dossier de travail (défaut : un dossier temporaire) ; gardé à la fin")
    ap.add_argument("--verbose", action="store_true", help="la pile d'appels de chaque échec")
    args = ap.parse_args(argv)

    families = args.only or list(FAMILIES)
    if args.workdir:
        work = Path(args.workdir).resolve()
        work.mkdir(parents=True, exist_ok=True)
        args.keep = True
    else:
        work = Path(tempfile.mkdtemp(prefix="jarvis-e2e-"))
    report = Report(strict=args.strict, verbose=args.verbose)
    print(f"JARVIS e2e — {', '.join(families)} — bac à sable {work}", flush=True)
    t0 = time.monotonic()
    ctx = None
    try:
        if "serveur" in families:
            from e2e import server_flows
            ctx = server_flows.run(report, work / "serveur")
        if "hud" in families:
            from e2e import hud_flows
            hud_flows.run(report, work / "hud")
    finally:
        failed = len(report.rows) - report.passed
        if failed and ctx is not None and ctx.jarvis is not None:
            print("\n  — fin du journal de JARVIS —", flush=True)
            for line in ctx.jarvis.log_tail(40).splitlines():
                print(f"  | {line}")
        if args.keep or os.environ.get("E2E_KEEP"):
            print(f"\n  bac à sable gardé : {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)
    print(f"\n  {report.passed}/{len(report.rows)} passed  ({time.monotonic() - t0:.0f} s)", flush=True)
    return 0 if report.passed == len(report.rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
