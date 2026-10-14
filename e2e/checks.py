"""
e2e/checks.py — un contrôle, une ligne, au format que la CI et la nuit lisent.

    [PASS] 7 libellé  détail  (1.2 s)
    [FAIL] 8 libellé  la raison
    [SKIP] 9 libellé  sans objet sous Windows : …

Deux façons de ne pas tester, à ne pas confondre :
  NotApplicable — le scénario n'a pas de sens ici (SIGTERM sous Windows) :
                  toujours [SKIP], la CI l'admet ;
  Skip          — il manque quelque chose pour le jouer (Playwright absent) :
                  [SKIP] à la main, [FAIL] avec --strict (la CI installe tout).
"""
from __future__ import annotations

import time
import traceback


class Skip(Exception):
    """Il manque de quoi jouer le scénario."""


class NotApplicable(Exception):
    """Le scénario n'a pas de sens sur cette plateforme."""


def _one_line(text: str, limit: int = 400) -> str:
    return " | ".join(ln.strip() for ln in str(text).splitlines() if ln.strip())[:limit]


class Report:
    def __init__(self, strict: bool = False, verbose: bool = False) -> None:
        self.strict = strict
        self.verbose = verbose
        self.rows: list[tuple[str, str]] = []

    def run(self, cid: str, label: str, fn) -> bool:
        t0 = time.monotonic()
        try:
            detail, mark = fn() or "", "PASS"
        except NotApplicable as e:
            detail, mark = f"sans objet : {e}", "SKIP"
        except Skip as e:
            detail, mark = f"ignoré : {e}", ("FAIL" if self.strict else "SKIP")
        except AssertionError as e:
            detail, mark = str(e) or "assertion fausse", "FAIL"
        except Exception as e:
            detail, mark = f"{type(e).__name__}: {e}", "FAIL"
            if self.verbose:
                traceback.print_exc()
        took = time.monotonic() - t0
        print(f"  [{mark}] {cid} {' '.join(label.split())}  {_one_line(detail)}  ({took:.1f} s)", flush=True)
        self.rows.append((cid, mark))
        return mark != "FAIL"

    def not_run(self, cid: str, label: str, why: str) -> None:
        """Un contrôle qui ne peut pas être joué parce que ce qui le précède a échoué :
        compté, et rouge, pour que le score garde toujours le même total."""
        self.run(cid, label, lambda: (_ for _ in ()).throw(AssertionError(f"pas joué : {why}")))

    @property
    def passed(self) -> int:
        return sum(1 for _, mark in self.rows if mark != "FAIL")
