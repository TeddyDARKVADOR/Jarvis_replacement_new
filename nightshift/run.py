"""
nightshift/run.py — lancer les suites, lire leurs résultats, écrire le rapport.

    python -m nightshift.run --out DIR [--previous FICHIER] [--suites a,b]
                             [--no-ruff] [--github-output] [--fail-on-regression]
    python -m nightshift.run --list

Chaque suite tourne dans son propre processus, avec un délai. Sa sortie est
LUE (« N/M », lignes PASS / FAIL / ok / OK / ÉCHEC), jamais exécutée. Une suite
qui plante est « en erreur » ; une suite qui dépasse son délai est tuée avec
ses enfants et marquée « délai dépassé ». Le banc, lui, ne plante jamais : au
pire il écrit un rapport « failed » qui dit pourquoi.

Les suites reçoivent un environnement sans clé : toute variable dont le nom
finit par API_KEY, TOKEN, SECRET ou PASSWORD est retirée. Elles sont faites
pour tourner sans clé ni réseau ; ce banc s'en assure.

Ce module observe. La comparaison avec la nuit précédente est dans compare.py,
la mise en forme dans report.py.
"""
from __future__ import annotations

import argparse
import json
import os
import platform as _platform
import re
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nightshift import SCHEMA
from nightshift import compare as C
from nightshift import metrics as M
from nightshift import report as R
from nightshift.suites import Suite, by_name, platform_name

REPO = Path(__file__).resolve().parent.parent

#: Ce qu'on garde de la sortie d'une suite : de quoi diagnostiquer, pas plus.
MAX_OUTPUT = 400_000
TAIL_LINES = 12
MAX_FAILURES = 20

_SECRET_NAME = re.compile(r"(API_KEY|TOKEN|SECRET|PASSWORD)$", re.IGNORECASE)
#: Des formes de clés connues, effacées de tout texte qui entre dans un rapport.
_SECRET_VALUE = re.compile(
    r"AIza[0-9A-Za-z_\-]{30,}|gsk_[0-9A-Za-z]{20,}|sk-[0-9A-Za-z_\-]{20,}|gh[pousr]_[0-9A-Za-z]{30,}"
    r"|(?i:bearer)\s+[0-9A-Za-z._\-]{20,}")


# ── lecture d'une sortie ─────────────────────────────────────────────────────

# Les lignes arrivent ici déjà débarrassées de leurs espaces de fin (rstrip) :
# aucun motif ne finit par « \s*$ », qui coûterait des retours arrière.
#: [PASS] nom  détail · [FAIL] … · [SKIP] … (la plupart des suites)
#: [  ok  ] nom · [ FAIL ] nom - détail (client_desktop, routage)
_BRACKET = re.compile(r"^\s*\[( *)(PASS|FAIL|SKIP|ok)( *)\] +(\S.*)$")
#: « OK  nom » · « ÉCHEC nom — info » (resilience, extensions, offline)
_WORD = re.compile(r"^ {2}(OK {2}|ÉCHEC )(\S.*)$")
#: « token_usage selftest: PASS » · « … selftest: FAIL: raison »
_WHOLE = re.compile(r"^\S+ selftest: (PASS|FAIL)\b:?(.*)$")
#: la ligne de score : « 64/64 passed », « 35/35 checks passed. », « resilience : 13/13 »,
#: « 35/35 », « 33/33 passed, FAILED: … »
_SCORE = re.compile(r"^ *(?:[\w.]+ : )?(\d+) */ *(\d+)(?: +(?:checks +)?passed\b.*)?$")
_NUMBERED = re.compile(r"^(\d+[a-z]?) +(\S.*)$")


def _split(rest: str, dash: bool, word: bool) -> tuple[str, str, str]:
    """« 21 chaîne : clé refusée  détail » → (« 21 », « chaîne : clé refusée », « détail »).
    Un contrôle sans numéro a son libellé pour identifiant."""
    m = _NUMBERED.match(rest)
    cid, text = (m.group(1), m.group(2)) if m else ("", rest)
    parts = re.split(r"\s{2,}", text, maxsplit=1)
    label, detail = parts[0], (parts[1] if len(parts) > 1 else "")
    if dash and " - " in label:
        label, _, more = label.partition(" - ")
        detail = f"{more} {detail}".strip()
    if word and " — " in label:
        label, _, more = label.partition(" — ")
        detail = f"{more} {detail}".strip()
    label = label.strip()
    return (cid or label), label, detail.strip()


def parse_output(text: str) -> dict:
    """Le score et les contrôles d'une sortie de suite. Ne lève jamais.

    → {"passed", "total", "checks": [{"id", "label", "ok", "detail"}]}
    `passed`/`total` viennent de la ligne de score si elle existe, sinon du
    décompte des contrôles lus, sinon None."""
    checks: list[dict] = []
    score = None
    seen: set[str] = set()
    for raw in (text or "").splitlines():
        line = raw.rstrip()
        m = _BRACKET.match(line)
        if m:
            inner, mark, rest = (m.group(1) or m.group(3)), m.group(2), m.group(4)
            cid, label, detail = _split(rest, dash=bool(inner), word=False)
            ok = mark != "FAIL"
        else:
            m = _WORD.match(line)
            if m:
                cid, label, detail = _split(m.group(2), dash=False, word=True)
                ok = m.group(1).startswith("OK")
            else:
                m = _WHOLE.match(line)
                if m:
                    cid, label, detail, ok = "*", "suite", m.group(2).strip(), m.group(1) == "PASS"
                else:
                    s = _SCORE.match(line)
                    if s:
                        score = (int(s.group(1)), int(s.group(2)))
                    continue
        if cid in seen:
            continue
        seen.add(cid)
        checks.append({"id": cid, "label": label[:160], "ok": ok, "detail": redact(detail)[:200]})
    if score is not None:
        passed, total = score
    elif checks:
        passed, total = sum(c["ok"] for c in checks), len(checks)
    else:
        passed = total = None
    return {"passed": passed, "total": total, "checks": checks}


def redact(text: str) -> str:
    """Efface ce qui ressemble à une clé, et le dossier personnel (remplacé par ~).
    Le dépôt est public : un rapport aussi."""
    text = _SECRET_VALUE.sub("[secret effacé]", str(text or ""))
    home = str(Path.home())
    return text.replace(home, "~") if len(home) > 1 else text


def _tail(text: str, n: int = TAIL_LINES) -> list[str]:
    lines = [redact(ln.rstrip())[:200] for ln in (text or "").splitlines() if ln.strip()]
    return lines[-n:]


# ── exécution ────────────────────────────────────────────────────────────────

def suite_env() -> dict[str, str]:
    """L'environnement d'une suite : celui du banc, sans clé, en UTF-8, sans écran."""
    env = {k: v for k, v in os.environ.items() if not _SECRET_NAME.search(k)}
    env.update(PYTHONIOENCODING="utf-8", PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    return env


def _kill_tree(proc: subprocess.Popen) -> None:
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, timeout=30)
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except Exception:
        pass
    try:
        proc.kill()
    except Exception:
        pass


def execute(argv: list[str], cwd: Path, env: dict, timeout_s: float) -> tuple[int | None, str, bool]:
    """(code de sortie ou None, sortie, délai dépassé). Ne lève jamais.

    Le processus a son propre groupe : au délai, on tue l'arbre entier, pas
    seulement le parent (une suite peut lancer des sous-processus)."""
    kw: dict = {}
    if os.name == "nt":
        kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    try:
        proc = subprocess.Popen(argv, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **kw)
    except Exception as e:
        return None, f"{type(e).__name__}: {e}", False
    try:
        out, _ = proc.communicate(timeout=timeout_s)
        return proc.returncode, _decode(out), False
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        try:
            out, _ = proc.communicate(timeout=10)
        except Exception:
            out = b""
        return proc.returncode, _decode(out), True
    except Exception as e:
        _kill_tree(proc)
        return None, f"{type(e).__name__}: {e}", False


def _decode(data: bytes | None) -> str:
    text = (data or b"").decode("utf-8", errors="replace")
    return text[-MAX_OUTPUT:]


def _precheck(python: str, modules: tuple[str, ...], cwd: Path, env: dict) -> tuple[bool, str]:
    """Les modules qu'il faut à une suite s'importent-ils ? (False, dernière ligne d'erreur) sinon."""
    mods = [m for m in modules if re.fullmatch(r"[\w.]+", m)]
    code, out, timed_out = execute([python, "-c", "import " + ", ".join(mods)], cwd, env, 120)
    if code == 0 and not timed_out:
        return True, ""
    last = _tail(out, 1)
    return False, (last[0] if last else "import impossible")


def output_name(suite_name: str) -> str:
    """Le nom de fichier de la sortie brute d'une suite (outputs/<nom>.log)."""
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", suite_name).strip("_") + ".log"


def run_suite(suite: Suite, repo: Path = REPO, platform: str | None = None,
              python: str | None = None, timeout_s: float | None = None,
              output_dir: Path | None = None) -> dict:
    """Lance une suite et dit ce qu'elle a donné. Ne lève jamais.

    status : ok · failing (des contrôles rouges) · error (plantage, rien de
    lisible) · timeout · skipped (sans objet ici, ou dépendance absente).
    compare.py affine ensuite failing en regression / known_failure."""
    platform = platform or platform_name()
    out: dict = {"name": suite.name, "platform": platform, "passed": None, "total": None,
                 "status": "skipped", "failures": [], "failed": [], "duration_s": 0.0}
    try:
        if platform not in suite.platforms:
            return {**out, "reason": f"sans objet sur {platform}"}
        py = python or (os.environ.get(suite.python_env) if suite.python_env else "") or sys.executable
        env = suite_env()
        if suite.requires:
            ok, why = _precheck(py, suite.requires, repo, env)
            if not ok:
                return {**out, "reason": f"dépendance absente : {why}"}
        t0 = time.monotonic()
        code, text, timed_out = execute([py, *suite.argv], repo, env, timeout_s or suite.timeout_s)
        out["duration_s"] = round(time.monotonic() - t0, 1)
        parsed = parse_output(text)
        out["passed"], out["total"] = parsed["passed"], parsed["total"]
        fails = [c for c in parsed["checks"] if not c["ok"]]
        if timed_out:
            out["status"] = "timeout"
            out["reason"] = f"délai dépassé ({int(timeout_s or suite.timeout_s)} s) : processus tué"
        elif code is None:
            out["status"], out["reason"] = "error", text[:200]
        elif fails or (code != 0 and parsed["total"] and (parsed["passed"] or 0) < parsed["total"]):
            out["status"] = "failing"
            if not fails:
                fails = [{"id": "?", "label": "contrôle non identifié dans la sortie", "ok": False,
                          "detail": ""}]
        elif code != 0:
            out["status"] = "error"
            last = _tail(text, 1)
            out["reason"] = f"code {code} : {last[0] if last else 'aucune sortie'}"
        else:
            out["status"] = "ok"
        out["failed"] = [{"id": c["id"], "label": c["label"], "detail": c["detail"]}
                         for c in fails[:MAX_FAILURES]]
        out["failures"] = [_failure_text(c) for c in fails[:MAX_FAILURES]]
        if out["status"] in ("error", "timeout"):
            out["tail"] = _tail(text)
        if output_dir is not None and out["status"] != "ok":
            # La sortie brute d'une suite rouge : le symptôme exact pour le contexte
            # de la phase 2. Artefact du run seulement, jamais dans le rapport public.
            try:
                output_dir.mkdir(parents=True, exist_ok=True)
                (output_dir / output_name(suite.name)).write_text(redact(text), encoding="utf-8")
            except OSError:
                pass
        return out
    except Exception as e:  # le banc ne plante pas : la suite est « en erreur », avec la cause
        return {**out, "status": "error", "reason": f"banc : {type(e).__name__}: {e}"[:200]}


def _failure_text(c: dict) -> str:
    return c["label"] if c["id"] == c["label"] else f"{c['id']} {c['label']}"


# ── ruff ─────────────────────────────────────────────────────────────────────

def run_ruff(repo: Path = REPO, python: str | None = None) -> dict:
    """Les remarques de ruff selon pyproject.toml (que les vrais bugs probables).
    Ruff absent → {"available": False}. Ne lève jamais."""
    base = {"tool": "ruff", "available": False}
    # Absolu, sinon relative_to() échoue pour un dépôt donné en « . » et chaque remarque
    # ne garde que son nom de fichier : les 4 remarques de toujours passent pour
    # nouvelles (run 38562012866, 2026-10-14 : un bon correctif rejeté pour ça).
    repo = Path(repo).resolve()
    cmd = [python or sys.executable, "-m", "ruff"]
    code, version, _ = execute(cmd + ["--version"], repo, suite_env(), 60)
    if code != 0:
        return {**base, "reason": "ruff absent (pip install ruff)"}
    code, text, timed_out = execute(cmd + ["check", ".", "--output-format", "json", "--exit-zero",
                                           "--no-cache"], repo, suite_env(), 300)
    try:
        start = text.index("[")
        data = json.loads(text[start:])
    except Exception:
        return {**base, "reason": "sortie de ruff illisible" + (" (délai dépassé)" if timed_out else "")}
    findings = []
    for d in data if isinstance(data, list) else []:
        try:
            path = Path(d.get("filename", "")).resolve()
            rel = path.relative_to(repo).as_posix() if path.is_relative_to(repo) else path.name
            findings.append({"code": d.get("code") or "syntax", "path": rel,
                             "line": int((d.get("location") or {}).get("row") or 0),
                             "message": redact(d.get("message", ""))[:200]})
        except Exception:
            continue
    findings.sort(key=lambda f: (f["path"], f["line"], f["code"]))
    return {"tool": "ruff", "available": True, "version": version.split()[-1] if version.split() else "",
            "count": len(findings), "findings": findings[:200]}


# ── contexte : git, machine, run ─────────────────────────────────────────────

def _git(repo: Path, *args: str) -> str | None:
    try:
        p = subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=30)
        return p.stdout.strip() if p.returncode == 0 else None
    except Exception:
        return None


def git_info(repo: Path = REPO) -> dict:
    full = os.environ.get("GITHUB_SHA") or _git(repo, "rev-parse", "HEAD") or ""
    ref = os.environ.get("GITHUB_REF_NAME") or _git(repo, "rev-parse", "--abbrev-ref", "HEAD") or ""
    return {"commit": full[:7], "commit_full": full, "ref": ref}


def suspects(repo: Path, since: str, until: str, limit: int = 10) -> list[str] | None:
    """Les commits entre le dernier rapport et celui-ci (« abc1234 sujet »).
    None si l'historique ne permet pas de le dire (commit inconnu ici)."""
    if not (since and until and re.fullmatch(r"[0-9a-f]{7,40}", since)
            and re.fullmatch(r"[0-9a-f]{7,40}", until)):
        return None
    if _git(repo, "cat-file", "-e", f"{since}^{{commit}}") is None:
        return None
    out = _git(repo, "log", f"--max-count={limit}", "--format=%h %s", f"{since}..{until}")
    return [redact(ln)[:120] for ln in out.splitlines()] if out is not None else None


def environment() -> dict:
    env = {"runner": "github" if os.environ.get("GITHUB_ACTIONS") == "true" else "local",
           "os": _os_name(), "python": _platform.python_version()}
    if os.environ.get("ImageOS"):
        env["image"] = f"{os.environ['ImageOS']} {os.environ.get('ImageVersion', '')}".strip()
    return env


def _os_name() -> str:
    if sys.platform.startswith("linux"):
        try:
            for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"')
        except OSError:
            pass
    return f"{_platform.system()} {_platform.release()}"


def run_meta() -> dict:
    gh_run = os.environ.get("GITHUB_RUN_ID", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    return {"run_id": gh_run or f"local-{datetime.now():%Y%m%d-%H%M%S}",
            "run_url": f"{server}/{repo}/actions/runs/{gh_run}" if gh_run and repo else None,
            "repository": repo or None,
            "event": os.environ.get("GITHUB_EVENT_NAME") or "local"}


def now() -> datetime:
    """L'heure de Paris avec son décalage ; l'heure locale si le fuseau manque."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Europe/Paris")).replace(microsecond=0)
    except Exception:
        return datetime.now().astimezone().replace(microsecond=0)


def load_previous(path: str | None) -> dict | None:
    """Le rapport précédent, s'il existe et parle le même schéma. None sinon."""
    if not path:
        return None
    try:
        p = Path(path)
        if not p.is_file() or p.stat().st_size == 0 or p.stat().st_size > 2_000_000:
            return None
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and data.get("schema_version") == SCHEMA else None
    except Exception:
        return None


# ── la nuit ──────────────────────────────────────────────────────────────────

def night(suites: list[Suite], previous: dict | None, repo: Path = REPO, ruff: bool = True,
          echo=print, output_dir: Path | None = None, metrics: bool = True) -> dict:
    """Lance les suites et ruff, et renvoie le rapport complet (report.build)."""
    started = now()
    plat = platform_name()
    git = git_info(repo)
    results = []
    for i, s in enumerate(suites, 1):
        r = run_suite(s, repo, plat, output_dir=output_dir)
        results.append(r)
        score = f"{r['passed']}/{r['total']}" if r["total"] is not None else "—"
        echo(f"  [{i:2}/{len(suites)}] {s.name:36} {score:>7}  {r['duration_s']:6.1f} s")
    lint = run_ruff(repo) if ruff else {"tool": "ruff", "available": False, "reason": "désactivé (--no-ruff)"}
    measured = M.measure(repo) if ruff and metrics else None   # phase 4 : les indicateurs, avec ruff
    prev_commit = (previous or {}).get("commit_full") or (previous or {}).get("commit") or ""
    sus = suspects(repo, prev_commit, git["commit_full"]) if previous else None
    report = R.build(meta=run_meta(), git=git, env=environment(), started_at=started, finished_at=now(),
                     suites=results, lint=lint, previous=previous, suspects=sus,
                     known={s.name: s.known(plat) for s in suites}, metrics=measured)
    echo("")
    for r in report["suites"]:   # l'état final, une fois comparé à la veille
        echo(f"  {r['status']:13} {r['name']:36}" + (f"  {r['reason']}" if r.get("reason") else ""))
    return report


def strict_failures(report: dict) -> list[str]:
    """Ce qui empêche la CI d'être verte : tout ce qui n'est pas « ok ».

    Plus exigeant que la nuit : un échec « connu » compte, une suite ignorée
    faute de dépendance compte (la CI installe tout), ruff absent ou bavard
    compte. Seule une suite sans objet sur cette plateforme est admise."""
    bad = []
    for s in report.get("suites") or []:
        out = C.outcome(s)
        if out == "ok" or (out == "skipped" and str(s.get("reason", "")).startswith("sans objet")):
            continue
        detail = s.get("reason") or "; ".join(s.get("failures") or [])
        bad.append(f"{s.get('name')} : {out}" + (f" — {detail}" if detail else ""))
    lint = report.get("lint") or {}
    if not lint.get("available"):
        bad.append(f"ruff : {lint.get('reason') or 'non lancé'}")
    elif lint.get("count"):
        bad += [f"ruff : {f['path']}:{f['line']} {f['code']} {f['message']}" for f in lint.get("findings") or []]
    if report.get("status") == "failed":
        bad.append(f"banc : {report.get('summary', 'rapport en échec')}")
    return bad


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(prog="python -m nightshift.run",
                                 description="Le veilleur de nuit : suites + ruff, comparés à la nuit précédente.")
    ap.add_argument("--out", help="dossier où écrire report.json et report.md")
    ap.add_argument("--previous", help="rapport précédent (baseline.json de la branche nightshift-data)")
    ap.add_argument("--suites", default="", help="noms séparés par des virgules (défaut : toutes)")
    ap.add_argument("--except", dest="skip", default="", help="noms à ne pas lancer (la CI lance e2e à part, en strict)")
    ap.add_argument("--no-ruff", action="store_true", help="ne pas lancer ruff")
    ap.add_argument("--github-output", action="store_true",
                    help="écrire regression=… et status=… dans $GITHUB_OUTPUT")
    ap.add_argument("--fail-on-regression", action="store_true",
                    help="code de sortie 1 s'il y a une régression (run rouge, e-mail de GitHub)")
    ap.add_argument("--list", action="store_true", help="lister les suites et quitter")
    ap.add_argument("--strict", action="store_true",
                    help="la CI : code 1 si une suite n'est pas verte (échecs connus compris) ou si ruff remarque quoi que ce soit")
    ap.add_argument("--no-metrics", action="store_true", help="ne pas mesurer les indicateurs (eval_lab, cognition, contexte)")
    args = ap.parse_args(argv)

    if args.list:
        plat = platform_name()
        for s in by_name():
            known = s.known(plat)
            print(f"{s.name:36} {s.timeout_s:4d} s  {'' if plat in s.platforms else '(sans objet) '}"
                  + (f"connus : {', '.join(sorted(known))}  " if known else "") + (s.linux if plat == "linux" else ""))
        return 0

    try:
        names = [n.strip() for n in args.suites.split(",") if n.strip()]
        skip = [n.strip() for n in args.skip.split(",") if n.strip()]
        by_name(skip)                                # une faute de frappe lève, ici aussi
        selected = [s for s in by_name(names) if s.name not in skip]
    except ValueError as e:
        print(f"nightshift.run : {e}", file=sys.stderr)
        return 2

    previous = load_previous(args.previous)
    print(f"JARVIS Night Shift — {len(selected)} suite(s) sur {platform_name()}"
          + ("" if previous else " — pas de rapport précédent"))
    try:
        report = night(selected, previous, ruff=not args.no_ruff,
                       output_dir=Path(args.out) / "outputs" if args.out else None,
                       metrics=not args.no_metrics)
    except Exception as e:  # dernier filet : un rapport « failed » plutôt qu'un plantage
        report = R.failed(run_meta(), git_info(), f"banc : {type(e).__name__}: {e}", started_at=now())
    md = R.to_markdown(report)
    if args.out:
        R.write(report, md, Path(args.out))
    R.step_summary(md)
    if args.github_output and os.environ.get("GITHUB_OUTPUT"):
        try:
            with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
                f.write(f"regression={'true' if report['regressions'] else 'false'}\n")
                f.write(f"status={report['status']}\n")
        except OSError:
            pass
    print(f"\n{report['summary']}")
    if args.strict:
        bad = strict_failures(report)
        print("\nCI : tout est vert." if not bad else "\nCI : rouge —\n" + "\n".join(f"  ✗ {b}" for b in bad))
        if bad:
            return 1
    if report["status"] == "failed":
        return 2
    return 1 if (args.fail_on_regression and report["regressions"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
