"""
nightshift/ci.py — les contrôles de la CI (.github/workflows/ci.yml), en plus des suites.

    python -m nightshift.ci gate REPORT.json     les suites d'un rapport : rouge hors échec connu → échec
    python -m nightshift.ci audit [--since REF]  les commits « nightshift: » de main restent dans la zone
    python -m nightshift.ci data                 chaque JSON, TOML et YAML suivi par Git se lit
    python -m nightshift.ci compile              chaque .py suivi par Git compile (Python de la CI)

Depuis le 2026-10-21, la nuit fusionne seule sur main ce qu'elle a entièrement
prouvé (NIGHTLY_AUTO_MERGE). La CI vérifie après coup, dans un autre
environnement que la nuit (Windows avec toutes les dépendances du PC, Python
3.11 sous Linux), et la publication ne fusionne plus rien tant que la CI de main
n'est pas verte : un correctif qui casse ailleurs arrête les suivants.

Rien n'est exécuté de ce qu'on lit : les rapports et les commits sont des données.
"""
from __future__ import annotations

import argparse
import json
import py_compile
import subprocess
import sys
import tomllib
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nightshift import zone

REPO = Path(__file__).resolve().parent.parent
SUBJECT = "nightshift: "            # le sujet des commits fusionnés par la nuit (publication, nightly.yml)
MAX_LINES = 40                      # NIGHTLY_AUTO_MERGE_MAX_LINES par défaut


# ── gate : le verdict des suites ─────────────────────────────────────────────

def gate(report: dict) -> list[str]:
    """Ce qui rend la CI rouge : une suite en erreur ou hors délai, un contrôle rouge qui n'est pas connu."""
    if not report.get("suites"):
        return ["aucune suite n'a tourné"]
    bad = []
    for s in report["suites"]:
        status = s.get("outcome") or s.get("status")
        if status in ("error", "timeout"):
            bad.append(f"{s['name']} : {status} ({(s.get('reason') or '').strip()[:120]})")
        for f in s.get("failed") or []:
            if f.get("class") != "known":
                bad.append(f"{s['name']} n°{f.get('id')} : {(f.get('label') or '')[:100]}")
    return bad


# ── audit : les commits de la nuit ───────────────────────────────────────────

def _git(*args: str, repo: Path = REPO) -> str:
    p = subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=60)
    if p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:2])} : {p.stderr.strip()[:200]}")
    return p.stdout


def nightshift_commits(since: str | None = None, repo: Path = REPO) -> list[dict]:
    """Les commits de main dont le sujet commence par « nightshift: » : sha, sujet, fichiers, lignes changées."""
    rng = [f"{since}..HEAD"] if since else ["HEAD"]
    out = []
    for line in _git("log", "--format=%H%x09%s", *rng, repo=repo).splitlines():
        sha, _, subject = line.partition("\t")
        if not subject.startswith(SUBJECT):
            continue
        files, changed = [], 0
        for row in _git("show", "--numstat", "--format=", sha, repo=repo).splitlines():
            parts = row.split("\t")
            if len(parts) == 3:
                files.append(parts[2])
                changed += sum(int(x) for x in parts[:2] if x.isdigit())
        out.append({"sha": sha[:10], "subject": subject, "files": files, "changed": changed})
    return out


def audit(commits: list[dict], max_lines: int = MAX_LINES) -> list[str]:
    """Un commit de la nuit hors zone, qui touche un test, ou plus long que le plafond : c'est une faille."""
    bad = []
    for c in commits:
        for f in c["files"]:
            ok, why = zone.allowed(f)
            if not ok:
                bad.append(f"{c['sha']} « {c['subject'][:60]} » : {why}")
        if not c["files"]:
            bad.append(f"{c['sha']} « {c['subject'][:60]} » : aucun fichier (commit vide ?)")
        if c["changed"] > max_lines:
            bad.append(f"{c['sha']} : {c['changed']} lignes changées (plafond {max_lines})")
    return bad


# ── data : les fichiers de données se lisent ─────────────────────────────────

def tracked(*patterns: str, repo: Path = REPO) -> list[str]:
    return [f for f in _git("ls-files", "-z", "--", *patterns, repo=repo).split("\0") if f]


def data_errors(repo: Path = REPO) -> list[str]:
    import yaml                      # pyyaml : installé par la CI comme par la nuit
    bad = []
    for rel in tracked("*.json", "*.toml", "*.yml", "*.yaml", repo=repo):
        path = repo / rel
        try:
            text = path.read_text(encoding="utf-8")
            if rel.endswith(".json"):
                json.loads(text)
            elif rel.endswith(".toml"):
                tomllib.loads(text)
            else:
                list(yaml.safe_load_all(text))
        except FileNotFoundError:
            continue                 # supprimé dans l'arbre de travail
        except Exception as e:
            bad.append(f"{rel} : {type(e).__name__}: {str(e).splitlines()[0][:120] if str(e) else ''}")
    return bad


# ── compile : tout le code compile avec ce Python ────────────────────────────

def compile_errors(repo: Path = REPO) -> list[str]:
    # Le bytecode va dans un dossier jetable : écrit dans les __pycache__ du dépôt, un .pyc
    # « hash non vérifié » masquait ensuite les modifications des sources (vu le 2026-10-21).
    import tempfile
    bad = []
    out = Path(tempfile.mkdtemp(prefix="jarvis-ci-compile-")) / "x.pyc"
    for rel in tracked("*.py", repo=repo):
        try:
            py_compile.compile(str(repo / rel), cfile=str(out), doraise=True)
        except FileNotFoundError:
            continue
        except py_compile.PyCompileError as e:
            bad.append(f"{rel} : {str(e.msg).strip().splitlines()[-1][:160]}")
    return bad


def _finish(title: str, bad: list[str], ok: str) -> int:
    if bad:
        print(f"{title} : {len(bad)} problème(s)")
        for b in bad:
            print(f"  ✗ {b}")
        return 1
    print(f"{title} : {ok}")
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(prog="python -m nightshift.ci")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gate")
    g.add_argument("report")
    a = sub.add_parser("audit")
    a.add_argument("--since", help="ne regarder que les commits après cette référence")
    a.add_argument("--max-lines", type=int, default=MAX_LINES)
    sub.add_parser("data")
    sub.add_parser("compile")
    args = ap.parse_args(argv)
    if args.cmd == "gate":
        report = json.loads(Path(args.report).read_text(encoding="utf-8"))
        n = len(report.get("suites") or [])
        return _finish("Suites", gate(report), f"{n} suites, aucun échec hors liste connue")
    if args.cmd == "audit":
        commits = nightshift_commits(args.since)
        return _finish("Commits de la nuit", audit(commits, args.max_lines),
                       f"{len(commits)} commit(s) « {SUBJECT.strip()} », tous dans la zone et sous le plafond")
    if args.cmd == "data":
        return _finish("Fichiers de données", data_errors(), f"{len(tracked('*.json', '*.toml', '*.yml', '*.yaml'))} lus")
    return _finish(f"Compilation (Python {sys.version.split()[0]})", compile_errors(),
                   f"{len(tracked('*.py'))} fichiers")


if __name__ == "__main__":
    raise SystemExit(main())
