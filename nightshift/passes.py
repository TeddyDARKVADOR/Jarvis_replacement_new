"""
nightshift/passes.py — plusieurs passes par nuit, chacune sur un problème différent.

    python -m nightshift.passes --schedule "17 3 * * *" [--github-output]

LES PASSES
    Le workflow se déclenche trois fois par nuit (heure de Paris). Chaque
    déclenchement est une passe complète : veille, une tâche, proposition,
    validation, publication, rapport. La mémoire (state.json) est poussée à la
    fin de chaque passe et relue au début de la suivante : une idée déjà tentée
    au même commit, ou dont la PR attend sa relecture, est passée — la passe
    suivante prend donc le problème suivant de la liste.

        passe 1   01 h 17
        passe 2   03 h 17
        passe 3   05 h 17

    Deux heures d'écart : la somme des délais des jobs (110 min) tient dedans,
    même si GitHub démarre une passe en retard (les déclenchements planifiés
    le sont souvent de plusieurs dizaines de minutes) ; et le groupe de
    concurrence du workflow les fait de toute façon passer l'une après l'autre.

    La passe est reconnue à sa ligne cron (github.event.schedule), jamais à
    l'heure réelle de démarrage, que le retard de GitHub fausserait.

NIGHTLY_PASSES (variable du dépôt, 1 à 3, défaut 3) : les passes au-delà ne
tournent pas du tout — pas de veille, pas de rapport. NIGHTLY_PASSES=1 garde
une seule passe, à 01 h 17. Un lancement à la main (workflow_dispatch) est
toujours une passe, hors de ce compte.

Le budget (NIGHTLY_MAX_LLM_CALLS, NIGHTLY_MAX_TOKENS) est par passe. Groq
gratuit : 200 000 tokens par jour ; 3 passes au plafond de 60 000 = 180 000.
"""
from __future__ import annotations

import argparse
import os
import sys

#: La ligne cron de chaque passe, dans l'ordre. Doit rester identique à
#: on.schedule de .github/workflows/nightly.yml (vérifié par le selftest).
SCHEDULES = ("17 1 * * *", "17 3 * * *", "17 5 * * *")
DEFAULT_PASSES = 3


def configured(value: str | None) -> int:
    """NIGHTLY_PASSES lue et bornée : 1 à len(SCHEDULES), défaut 3."""
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        return DEFAULT_PASSES
    return max(1, min(len(SCHEDULES), n))


def resolve(schedule: str | None, passes: str | None) -> dict:
    """{"n": numéro de passe ou 0 (manuel), "of": passes configurées, "enabled": bool, "slot": "01 h 17"}."""
    of = configured(passes)
    sched = " ".join(str(schedule or "").split())
    if sched not in SCHEDULES:
        return {"n": 0, "of": of, "enabled": True, "slot": "manuel"}
    n = SCHEDULES.index(sched) + 1
    hour, minute = sched.split()[1], sched.split()[0]
    return {"n": n, "of": of, "enabled": n <= of, "slot": f"{int(hour):02d} h {int(minute):02d}"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m nightshift.passes")
    ap.add_argument("--schedule", default="", help="github.event.schedule (vide : lancement manuel)")
    ap.add_argument("--passes", default=os.environ.get("NIGHTLY_PASSES"), help="NIGHTLY_PASSES")
    ap.add_argument("--github-output", action="store_true")
    args = ap.parse_args(argv)
    p = resolve(args.schedule, args.passes)
    if not p["n"]:
        print("lancement manuel")
    elif p["enabled"]:
        print(f"passe {p['n']}/{p['of']} ({p['slot']})")
    else:
        print(f"passe {p['n']} ({p['slot']}) : NIGHTLY_PASSES={p['of']}, rien ne tourne")
    if args.github_output and os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(f"n={p['n']}\nof={p['of']}\nenabled={'true' if p['enabled'] else 'false'}\n")
    return 0


if __name__ == "__main__":
    if __package__ in (None, ""):
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    raise SystemExit(main())
