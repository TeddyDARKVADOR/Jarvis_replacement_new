"""
nightshift/compare.py — cette nuit contre la précédente.

Pour chaque contrôle rouge cette nuit :

  known          il est dans les échecs connus de cette plateforme (suites.py) :
                 affiché, jamais compté comme une régression ;
  regression     il n'était pas rouge la veille alors que la suite avait tourné
                 (il passait, ou il vient d'apparaître) : c'est cette nuit qui
                 l'a cassé — avec le dernier commit vert et les commits suspects ;
  preexisting    il était déjà rouge la veille ;
  unreferenced   la veille ne permet pas de le dire : pas de rapport, ou la
                 suite n'avait pas tourné (ignorée, plantée). Peut-être nouveau,
                 peut-être ancien — le rapport ne tranche pas à sa place.

Un contrôle rouge la veille qui ne l'est plus est « réparé ». Une suite qui ne
s'exécute plus (erreur, délai) alors qu'elle tournait la veille est une
régression de la suite entière (contrôle « * »), et l'inverse une réparation.

« La veille », c'est le dernier rapport qui a des résultats de suites
(baseline.json sur la branche nightshift-data) : une nuit ratée ne fait pas
oublier ce qui passait avant elle.

Pur : ni fichier, ni réseau, ni git. run.py lui passe ce qu'il faut, le
selftest lui passe des rapports simulés.
"""
from __future__ import annotations

from collections import Counter

#: La suite a tourné et rendu des contrôles : on peut comparer contrôle par contrôle.
RAN = ("ok", "failing", "known_failure", "regression")
#: La suite n'a rien rendu de lisible.
BROKEN = ("error", "timeout")
#: Ce qui compte comme vert dans un rapport.
GREEN = ("ok", "known_failure", "skipped")


def outcome(suite: dict | None) -> str | None:
    """Ce que la suite a fait, avant classement : ok, failing, error, timeout ou
    skipped. L'état classé (status) dit « regression » aussi bien pour un
    contrôle cassé que pour une suite qui ne s'exécute plus : la nuit suivante
    doit savoir laquelle des deux. Un rapport sans `outcome` donne son état."""
    return (suite or {}).get("outcome") or (suite or {}).get("status")


def failed_ids(suite: dict | None) -> set[str]:
    return {str(f.get("id")) for f in (suite or {}).get("failed") or []
            if isinstance(f, dict) and f.get("id")}


def _regression(suite: str, check: str, label: str, commit: str, previous_commit: str,
                suspects: list[str] | None) -> dict:
    return {"suite": suite, "check": check, "label": label, "since_commit": commit,
            "last_green_commit": previous_commit or None,
            "suspects": list(suspects) if suspects is not None else None}


def classify(suites: list[dict], previous: dict | None, known: dict[str, dict[str, str]],
             commit: str = "", previous_commit: str = "", suspects: list[str] | None = None) -> dict:
    """Classe chaque échec de la nuit. Ne modifie pas ses arguments.

    → {"suites": suites avec leur état final et la classe de chaque échec,
       "regressions": [...], "repaired": [...], "preexisting": [...],
       "unreferenced": [...]}

    État final d'une suite : regression > error / timeout > failing (déjà rouge
    ou sans référence) > known_failure > ok ; skipped reste skipped."""
    prev = {s.get("name"): s for s in (previous or {}).get("suites") or [] if isinstance(s, dict)}
    found = {"regressions": [], "repaired": [], "preexisting": [], "unreferenced": []}
    out: list[dict] = []
    for s in suites:
        s = dict(s)
        name = s.get("name", "?")
        k = known.get(name, {})
        p = prev.get(name)
        p_ran = bool(p) and outcome(p) in RAN

        def regression(check: str, label: str) -> None:
            found["regressions"].append(_regression(name, check, label, commit, previous_commit, suspects))

        s["failed"] = _classify_failures(s, k, failed_ids(p) if p_ran else None, regression, found)
        status = s["outcome"] = s.get("status")
        if status in BROKEN and p_ran:
            why = "délai dépassé" if status == "timeout" else "plantage"
            regression("*", f"la suite ne s'exécute plus ({why})")
        s["status"] = _final_status(status, s["failed"], broke_since_yesterday=status in BROKEN and p_ran)
        if p and status in RAN:
            found["repaired"] += _repaired(name, p, failed_ids(s), k)
        out.append(s)
    return {"suites": out, **found}


def _classify_failures(suite: dict, known: dict[str, str], yesterday: set[str] | None,
                       regression, found: dict) -> list[dict]:
    """La classe de chaque échec : known, regression, preexisting ou unreferenced.
    `yesterday` est l'ensemble des échecs de la veille, None si la veille ne
    permet pas de comparer (pas de rapport, suite non lancée ou plantée)."""
    failed = []
    for f in suite.get("failed") or []:
        f = dict(f)
        cid = str(f.get("id"))
        if cid in known:
            f["class"], f["known_reason"] = "known", known[cid]
        elif yesterday is not None and cid not in yesterday:
            f["class"] = "regression"
            regression(cid, f.get("label", ""))
        else:
            f["class"] = "preexisting" if yesterday is not None else "unreferenced"
            found[f["class"]].append({"suite": suite.get("name", "?"), "check": cid, "label": f.get("label", "")})
        failed.append(f)
    return failed


def _final_status(status: str | None, failed: list[dict], broke_since_yesterday: bool) -> str | None:
    if status in BROKEN:
        return "regression" if broke_since_yesterday else status
    if status == "skipped":
        return status
    classes = {f["class"] for f in failed}
    if "regression" in classes:
        return "regression"
    if classes & {"preexisting", "unreferenced"}:
        return "failing"
    return "known_failure" if failed else "ok"


def _repaired(name: str, yesterday: dict, now_failed: set[str], known: dict[str, str]) -> list[dict]:
    """Ce qui était rouge la veille et ne l'est plus — la suite entière comprise."""
    labels = {str(f.get("id")): f.get("label", "") for f in yesterday.get("failed") or []
              if isinstance(f, dict)}
    out = [{"suite": name, "check": cid, "label": labels.get(cid, ""), "was_known": cid in known}
           for cid in sorted(failed_ids(yesterday) - now_failed)]
    if outcome(yesterday) in BROKEN:
        out.append({"suite": name, "check": "*", "label": "la suite s'exécute de nouveau", "was_known": False})
    return out


def lint_new(lint: dict, previous: dict | None) -> list[dict]:
    """Les remarques de ruff qui n'étaient pas là la veille (même code, même
    fichier, même message ; le numéro de ligne bouge avec chaque édition).
    Sans remarques de la veille à comparer : aucune n'est « nouvelle »."""
    prev = (previous or {}).get("lint") or {}
    if not lint.get("available") or not prev.get("available"):
        return []

    def key(f: dict) -> tuple:
        return (f.get("code"), f.get("path"), f.get("message"))

    before = Counter(key(f) for f in prev.get("findings") or [] if isinstance(f, dict))
    new = []
    for f in lint.get("findings") or []:
        if before[key(f)] > 0:
            before[key(f)] -= 1
        else:
            new.append(f)
    return new


def is_green(report: dict | None) -> bool:
    return bool(report) and report.get("status") == "success"


def green_streak(green: bool, previous: dict | None) -> int:
    """Nuits vertes d'affilée, celle-ci comprise (0 si elle est rouge)."""
    if not green:
        return 0
    if is_green(previous):
        try:
            return max(1, int(previous.get("green_streak") or 1)) + 1
        except (TypeError, ValueError):
            return 2
    return 1
