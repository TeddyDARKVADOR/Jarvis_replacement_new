"""
eval_lab/report.py — what a person reads after a run.

Written from triage.json (+ coverage.json when the run has one) into
runs/<run>/report.md. The order is the order of what to do next: bugs of
JARVIS first, then disagreements to settle, then the lab's own problems.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

CLASS_TITLE = {
    "jarvis-crash": "JARVIS a leve une exception",
    "jarvis-candidate": "Candidats bugs JARVIS (propriete ou relation du projet violee)",
    "legacy-regression": "Tests existants qui echouent",
    "oracle-disputed": "Desaccords avec un oracle ecrit par le labo — a trancher",
    "unstable": "Instables",
    "known": "Deja connus (corpus de regression)",
    "lab": "Problemes du labo (INFRA)",
    "generator": "Scenarios invalides (generateur)",
}


def _components(source: str | None) -> str:
    files = re.findall(r"[\w/]+\.(?:py|mjs|kt)", source or "")
    return ", ".join(dict.fromkeys(files)) or "?"


def _block(obj) -> str:
    return "```json\n" + json.dumps(obj, ensure_ascii=False, indent=1)[:1800] + "\n```"


def render(run: str, tri: dict, coverage: dict | None, results: list[dict],
           regressions: list[dict]) -> str:
    v = Counter(r["verdict"] for r in results)
    warnings = Counter(p["property"] for r in results for p in r.get("problems") or []
                       if p["kind"] == "warning")
    reg_by_sig = {s["lineage"]["regression"]["signature"]: s["lineage"]["regression"]["id"]
                  for s in regressions}
    cl = tri["clusters"]
    by_class = Counter(c["class"] for c in cl)
    L = [f"# EVAL LAB — rapport du run `{run}`", ""]
    L += ["## Totaux", "",
          "| | |", "|---|---|",
          f"| essais executes | {len(results)} |",
          f"| valides | {len(results) - v['INVALID']} |",
          f"| invalides | {v['INVALID']} |",
          f"| PASS | {v['PASS']} |",
          f"| FAIL | {v['FAIL']} |",
          f"| CRASH | {v['CRASH']} |",
          f"| INFRA (labo) | {v['INFRA']} |",
          f"| clusters | {len(cl)} |",
          f"| dont nouveaux candidats JARVIS | {by_class['jarvis-candidate'] + by_class['jarvis-crash']} |",
          f"| dont connus | {by_class['known']} |",
          f"| reproducteurs minimaux | {sum(1 for c in cl if c.get('minimal'))} |", ""]
    if coverage:
        L += ["## Couverture", "",
              f"- scenarios uniques : {coverage['unique']} (doublons {coverage['duplicate_rate']:.1%}, "
              f"invalides {coverage['invalid_rate']:.1%})",
              f"- nouveaute moyenne : {coverage['mean_novelty']:.3f} — proche de 0 = l'espace est sature, "
              "generer plus n'apprendra rien",
              "- traits couverts : " + ", ".join(f"{k} {n}" for k, n in sorted(coverage["features"].items())),
              f"- traits vus une seule fois (etats rares) : {coverage['rare_features']}", ""]
    if warnings:
        L += ["## A trancher (proprietes *soft*)", "",
              "Le code suggere ces promesses sans les ecrire. Ce ne sont pas des bugs tant qu'une "
              "personne ne les a pas durcies dans `properties.py` / `mutate.py`.", ""]
        L += [f"- **{k}** : {n} essais" for k, n in warnings.most_common()] + [""]
    L += ["## Clusters", "",
          "| id | classe | taille | premier invariant | exemple |", "|---|---|---|---|---|"]
    for c in cl:
        ex = str(c.get("example") or "").replace("|", "/").replace("\n", " ")[:90]
        L.append(f"| {c['cluster']} | {c['class']} | {c['size']} | {c.get('first_invariant')} | {ex} |")
    L.append("")
    for cls in CLASS_TITLE:
        group = [c for c in cl if c["class"] == cls]
        if not group or cls in ("lab", "generator"):
            continue
        L += [f"## {CLASS_TITLE[cls]}", ""]
        for c in group:
            m = c.get("minimal")
            L += [f"### {c['cluster']} — {c.get('first_invariant')} ({c['size']} essais)", "",
                  f"- signature : `{c['signature']}`",
                  f"- composant probable : {_components(c.get('source'))}",
                  f"- source de la promesse : {c.get('source') or 'oracle du scenario'}",
                  f"- familles : {', '.join(c['families'])}",
                  f"- regression : {reg_by_sig.get(c['signature'], '— (non promu)')}",
                  f"- constate : {c.get('example')}", ""]
            if m:
                L += [f"Reproducteur minimal ({c['minimal_size']} octets, depuis {c['original_size']}, "
                      f"{c['minimise_runs']} executions) — `{m['id']}` :", "", _block(sc_view(m)), ""]
    lab = [c for c in cl if c["class"] in ("lab", "generator")]
    if lab:
        L += ["## Problemes du labo et du generateur", "",
              "Jamais comptes comme des bugs de JARVIS.", ""]
        L += [f"- {c['cluster']} [{c['class']}] x{c['size']} : {str(c.get('example'))[:160]}" for c in lab]
    return "\n".join(L) + "\n"


def sc_view(s: dict) -> dict:
    return {k: s[k] for k in ("surface", "world", "stimulus", "events", "expected", "forbidden")
            if s.get(k)}
