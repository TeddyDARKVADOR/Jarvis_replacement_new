"""
nightshift/metrics.py — Matthew s'améliore-t-il ? Trois indicateurs (phase 4).

    measure(repo) -> dict             les trois mesures, chacune dans son propre processus
    compare(now, before) -> list      ce qui a baissé, augmenté ou changé
    python -m nightshift.metrics      les mesures du dépôt courant, en JSON

  eval_lab   les scénarios du corpus de référence (legacy) et de régression :
             taux de réussite par surface, et les échecs par cause (la propriété
             violée, ex. NO_DELIVERY_WITHOUT_SINK) ;
  cognition  l'évaluation déterministe de l'architecture (cognition.evaluate,
             sans clé ni réseau ; vérifié identique d'un passage à l'autre) : les
             valeurs de l'architecture complète, pas celles des ablations ;
  contexte   le coût fixe de chaque connexion Gemini Live : les caractères du
             prompt système (core/prompt.txt) et des déclarations d'outils
             telles que main.py les envoie (_compact_tool_declarations), en mode
             serveur et en mode local.

Les tests disent « rien n'est cassé », pas « c'est mieux ». Un correctif qui fait
baisser un indicateur — taux eval_lab en baisse, coût en contexte en hausse, une
valeur de cognition qui change (son sens dépend de la ligne : « questions
justes » doit monter, « faux souvenirs » rester bas) — est marqué « à relire de
près », même si les suites sont vertes.

COMMENT, SANS IMPORTER Matthew (contrat V2)
Chaque mesure tourne dans un processus à part, comme une suite : eval_lab par sa
propre commande ; cognition et contexte par une sonde (python -m nightshift.metrics
--probe …) qui charge le code du dépôt à l'exécution, comme server.run_headless.
Le paquet nightshift, lui, n'importe rien de Matthew. Ne lève jamais : une mesure
impossible est « indisponible », avec la raison.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MARK = "NIGHTSHIFT_METRIC "
CORPORA = ("legacy", "regressions")
CHARS_PER_TOKEN = 3.5


def _run(argv: list[str], repo: Path, timeout: float) -> tuple[int | None, str]:
    try:
        p = subprocess.run(argv, cwd=str(repo), capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout,
                           env={**os.environ, "PYTHONIOENCODING": "utf-8", "QT_QPA_PLATFORM": "offscreen"})
        return p.returncode, p.stdout + p.stderr
    except subprocess.TimeoutExpired:
        return None, "délai dépassé"
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def _last_tail(text: str) -> str:
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    return (lines[-1] if lines else "aucune sortie")[:200]


# ── eval_lab ─────────────────────────────────────────────────────────────────

def eval_lab(repo: Path = REPO, python: str | None = None, timeout: float = 300) -> dict:
    out: dict = {"available": True, "corpora": {}}
    for corpus in CORPORA:
        run = f"nightshift-{corpus}"
        code, text = _run([python or sys.executable, "-m", "eval_lab", "run", "--corpus", corpus, "--run", run,
                           "--fresh", "--lean"], repo, timeout)
        results = repo / "eval_lab" / "runs" / run / "results.jsonl"
        if code is None or not results.is_file():
            out["corpora"][corpus] = {"available": False, "reason": _last_tail(text) if code is not None else text}
            continue
        verdicts, causes = Counter(), Counter()
        surfaces: dict[str, Counter] = {}
        for line in results.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            v = rec.get("verdict", "?")
            verdicts[v] += 1
            surfaces.setdefault(rec.get("surface", "?"), Counter())[v] += 1
            if v != "PASS":
                for p in rec.get("problems") or [{"property": rec.get("error") or v}]:
                    if p.get("kind", "property") != "warning":
                        causes[str(p.get("property") or p.get("kind") or v)[:80]] += 1
        total = sum(verdicts.values())
        out["corpora"][corpus] = {
            "available": True, "total": total, "pass": verdicts["PASS"],
            "rate": round(verdicts["PASS"] / total, 4) if total else None,
            "by_verdict": dict(verdicts), "causes": dict(causes.most_common(10)),
            "by_surface": {s: {"pass": c["PASS"], "total": sum(c.values())} for s, c in sorted(surfaces.items())}}
    if not any(c.get("available") for c in out["corpora"].values()):
        out = {"available": False, "reason": "; ".join(f"{k} : {v.get('reason')}" for k, v in out["corpora"].items())}
    return out


# ── les sondes (processus à part) ────────────────────────────────────────────

def _probe(name: str, repo: Path, python: str | None, timeout: float) -> dict:
    code, text = _run([python or sys.executable, "-m", "nightshift.metrics", "--probe", name], repo, timeout)
    for line in reversed(text.splitlines()):
        if line.startswith(MARK):
            try:
                return json.loads(line[len(MARK):])
            except ValueError:
                break
    return {"available": False, "reason": text if code is None else _last_tail(text)}


def _probe_cognition() -> dict:
    """Dans le processus de la sonde : cognition.evaluate.run(), valeurs de l'architecture complète."""
    report = importlib.import_module("cognition.evaluate").run()
    values = {}
    for section, body in report.items():
        if section == "date" or not isinstance(body, dict):
            continue
        variants = [v for v in body.values() if isinstance(v, dict)]
        if variants and len(variants) == len(body):          # un tableau : la 1re colonne est l'architecture complète
            first = next(iter(body))
            for row, val in body[first].items():
                if not str(row).startswith("_"):
                    values[f"{section} · {row}"] = str(val)
        else:
            for row, val in body.items():
                if not str(row).startswith("_") and not isinstance(val, (dict, list)):
                    values[f"{section} · {row}"] = str(val)
    return {"available": True, "values": values}


def _probe_context() -> dict:
    """Dans le processus de la sonde : les déclarations d'outils telles que main.py les envoie.

    Le même démarrage que server.run_headless : le remplaçant de carte son d'abord,
    puis main, puis MatthewAssistantLive sur l'interface sans écran. Rien n'est envoyé à Gemini."""
    audio = importlib.import_module("server.audio_bridge")
    headless = importlib.import_module("server.headless_ui")
    sys.modules["sounddevice"] = audio.build_module(audio.AudioHub(), log=lambda m: None)
    main = importlib.import_module("main")
    matthew_assistant = main.MatthewAssistantLive(headless.HeadlessUI(echo=False))
    decls = (main.TOOL_DECLARATIONS + matthew_assistant._action_registry.get_tool_declarations()
             + matthew_assistant._plugin_registry.get_tool_declarations())
    out = {"available": True}
    for mode, local in (("server", False), ("local", True)):
        compact = main._compact_tool_declarations(decls, local_mode=local)
        chars = len(json.dumps(compact, ensure_ascii=False))
        out[mode] = {"tools": len(compact), "chars": chars}
    prompt = (Path(main.__file__).resolve().parent / "core" / "prompt.txt").read_text(encoding="utf-8")
    out["system_prompt_chars"] = len(prompt)
    out["estimated_tokens_local"] = int((out["local"]["chars"] + len(prompt)) / CHARS_PER_TOKEN)
    return out


def measure(repo: Path = REPO, python: str | None = None, timeout: float = 300) -> dict:
    """Les trois indicateurs. Ne lève jamais."""
    out = {}
    for name, fn in (("eval_lab", lambda: eval_lab(repo, python, timeout)),
                     ("cognition", lambda: _probe("cognition", repo, python, timeout)),
                     ("context", lambda: _probe("context", repo, python, timeout))):
        try:
            out[name] = fn()
        except Exception as e:
            out[name] = {"available": False, "reason": f"{type(e).__name__}: {e}"[:200]}
    return out


# ── comparer deux nuits, ou avant/après un patch ─────────────────────────────

def compare(now: dict | None, before: dict | None) -> list[dict]:
    """Ce qui a baissé (eval_lab), augmenté (coût en contexte) ou changé (cognition)."""
    now, before = now or {}, before or {}
    out: list[dict] = []
    for corpus, c in ((now.get("eval_lab") or {}).get("corpora") or {}).items():
        b = ((before.get("eval_lab") or {}).get("corpora") or {}).get(corpus) or {}
        if c.get("available") and b.get("available"):
            fails_now, fails_before = c["total"] - c["pass"], b["total"] - b["pass"]
            if fails_now > fails_before:
                new_causes = sorted(set(c.get("causes") or {}) - set(b.get("causes") or {}))
                out.append({"metric": f"eval_lab {corpus}", "verdict": "baisse",
                            "before": f"{b['pass']}/{b['total']}", "now": f"{c['pass']}/{c['total']}",
                            "detail": ("nouvelles causes : " + ", ".join(new_causes)) if new_causes else ""})
    ctx, bctx = now.get("context") or {}, before.get("context") or {}
    if ctx.get("available") and bctx.get("available"):
        for mode in ("server", "local"):
            a, b = (ctx.get(mode) or {}).get("chars"), (bctx.get(mode) or {}).get("chars")
            if a and b and a > b:
                out.append({"metric": f"coût en contexte ({mode})", "verdict": "hausse", "before": b, "now": a,
                            "detail": f"+{a - b} caractères (+{(a - b) / b:.1%}) envoyés à chaque connexion"})
        a, b = ctx.get("system_prompt_chars"), bctx.get("system_prompt_chars")
        if a and b and a > b:
            out.append({"metric": "prompt système", "verdict": "hausse", "before": b, "now": a,
                        "detail": f"+{a - b} caractères"})
    cog, bcog = now.get("cognition") or {}, before.get("cognition") or {}
    if cog.get("available") and bcog.get("available"):
        for key, val in (cog.get("values") or {}).items():
            old = (bcog.get("values") or {}).get(key)
            if old is not None and old != val:
                out.append({"metric": f"cognition · {key.split(' · ', 1)[-1]}", "verdict": "changé",
                            "before": old, "now": val, "detail": key.split(" · ", 1)[0]})
    return out


def headline(m: dict | None) -> str:
    """« eval_lab 76/77 et 12/12 · coût 23 456 car. (local) » — pour le résumé et l'onglet."""
    m = m or {}
    parts = []
    corpora = (m.get("eval_lab") or {}).get("corpora") or {}
    scores = [f"{c['pass']}/{c['total']}" for c in corpora.values() if c.get("available")]
    if scores:
        parts.append("eval_lab " + " et ".join(scores))
    ctx = m.get("context") or {}
    if ctx.get("available"):
        parts.append(f"coût en contexte {ctx['local']['chars']} car. (≈ {ctx.get('estimated_tokens_local')} tokens)")
    return " · ".join(parts)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m nightshift.metrics")
    ap.add_argument("--probe", choices=("cognition", "context"), help="(interne) une sonde, dans ce processus")
    args = ap.parse_args(argv)
    if args.probe:
        sys.path.insert(0, str(REPO))
        try:
            result = _probe_cognition() if args.probe == "cognition" else _probe_context()
        except Exception as e:
            result = {"available": False, "reason": f"{type(e).__name__}: {e}"[:200]}
        print(MARK + json.dumps(result, ensure_ascii=False), flush=True)
        return 0
    print(json.dumps(measure(), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
