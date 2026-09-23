"""
eval_lab/cli.py — the lab's commands.

    python -m eval_lab import-legacy            existing tests -> corpus/legacy/*.jsonl
    python -m eval_lab run [--corpus legacy] [--surface S] [--run NAME] [--fresh]
                           [--shard K/N]
    python -m eval_lab status [--run NAME]      verdict counts of a run
    python -m eval_lab generate --strategy S [--count N] [--seed K] [--name NAME]
                                                 strategies: pairwise, pairwise-routing,
                                                 boundary, sequence, adversarial, rare, all
    python -m eval_lab fuzz [--seeds legacy,generated] [--count N] [--seed K] [--run NAME]
                                                 coverage-guided mutation + relations
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
CORPUS = HERE / "corpus"
RUNS = HERE / "runs"

if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def _corpus_files(name: str) -> list[Path]:
    path = Path(name)
    if path.is_file():
        return [path]
    folder = CORPUS / name if not path.is_dir() else path
    files = sorted(folder.glob("*.jsonl"))
    if not files:
        raise SystemExit(f"aucun corpus sous {folder}")
    return files


def iter_corpus(name: str, surface: str | None = None):
    from .runner import load_jsonl
    for f in _corpus_files(name):
        for s in load_jsonl(f):
            if surface is None or s.get("surface") == surface:
                yield s


def cmd_import_legacy(args) -> int:
    from .legacy import import_all
    counts = import_all()
    for name, n in counts.items():
        print(f"  {name:<8} {n:>5} scenarios")
    print(f"  total    {sum(counts.values()):>5}")
    return 0


def _peak_mb() -> float | None:
    """Peak working set of this process, if psutil is there. Optional on
    purpose: the lab must not add a dependency to measure itself."""
    try:
        import psutil
        mi = psutil.Process().memory_info()
        return round(getattr(mi, "peak_wset", mi.rss) / 2 ** 20, 1)
    except Exception:
        return None


def cmd_run(args) -> int:
    from .coverage import Coverage
    from .runner import run
    k, n = (int(x) for x in args.shard.split("/"))
    tag = f".{k}-{n}" if n > 1 else ""
    out = RUNS / args.run / f"results{tag}.jsonl"
    cov = Coverage()
    tiers = {"fast": ("fast",), "full": ("full",), "all": ("fast", "full")}[args.mode]
    summary = run(iter_corpus(args.corpus, args.surface), out, resume=not args.fresh, shard=(k, n),
                  on_result=lambda s, rec: cov.add(s, rec), keep_pass_traces=not args.lean, tiers=tiers)
    meta = {"shard": [k, n], "seconds": round(summary.seconds, 2), "counts": summary.counts,
            "skipped": summary.skipped, "peak_mb": _peak_mb(), "bytes": out.stat().st_size,
            "coverage": cov.metrics()}
    (RUNS / args.run / f"meta{tag}.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    if n == 1:
        (RUNS / args.run / "coverage.json").write_text(json.dumps(meta["coverage"], indent=1), encoding="utf-8")
    print(f"  {summary.line()}")
    print(f"  -> {out.relative_to(REPO)}  ({meta['bytes'] / 2 ** 20:.1f} Mo, pic {meta['peak_mb']} Mo)")
    bad = summary.counts["FAIL"] + summary.counts["CRASH"] + summary.counts["INFRA"] + summary.counts["INVALID"]  # INCONCLUSIVE is not bad
    return 1 if bad else 0


def cmd_status(args) -> int:
    from .runner import load_jsonl
    files = sorted((RUNS / args.run).glob("results*.jsonl"))
    if not files:
        print("aucun resultat")
        return 1
    verdicts, warn = Counter(), Counter()
    by_surface: dict[str, Counter] = {}
    for f in files:
        for rec in load_jsonl(f):
            verdicts[rec["verdict"]] += 1
            by_surface.setdefault(rec["surface"], Counter())[rec["verdict"]] += 1
            for p in rec.get("problems") or []:
                if p["kind"] == "warning":
                    warn[p["property"]] += 1
    print(f"  {sum(verdicts.values())} essais : " + " ".join(f"{k}={v}" for k, v in sorted(verdicts.items())))
    for surface, c in sorted(by_surface.items()):
        print(f"    {surface:<10} " + " ".join(f"{k}={v}" for k, v in sorted(c.items())))
    for name, n in warn.most_common():
        print(f"  [a trancher] {name} : {n}")
    return 0


def cmd_generate(args) -> int:
    from .generate import STRATEGIES
    from .scenario import dumps
    names = list(STRATEGIES) if args.strategy == "all" else [args.strategy]
    dest = CORPUS / "generated"
    dest.mkdir(parents=True, exist_ok=True)
    total = 0
    for name in names:
        items, ids = [], set()
        for sc_ in STRATEGIES[name](args.count, args.seed):
            if sc_["id"] not in ids:          # the id is the dedup
                ids.add(sc_["id"])
                items.append(sc_)
        out = dest / f"{args.name or name}.jsonl"
        out.write_text("".join(dumps(x) + "\n" for x in items), encoding="utf-8", newline="\n")
        total += len(items)
        print(f"  {name:<18} {len(items):>7} scenarios uniques -> {out.relative_to(REPO)}")
    print(f"  total {total}")
    return 0


def cmd_fuzz(args) -> int:
    import time as _t
    from .coverage import fuzz
    from .scenario import dumps
    seeds = [s for name in args.seeds.split(",") for s in iter_corpus(name)
             if s["surface"] != "face"]       # face: exhaustive matrix, see README
    run_dir = RUNS / args.run
    run_dir.mkdir(parents=True, exist_ok=True)
    counts = Counter()
    t0 = _t.perf_counter()
    with (run_dir / "scenarios.jsonl").open("w", encoding="utf-8") as fs, \
            (run_dir / "results.jsonl").open("w", encoding="utf-8") as fr:
        def sink(s, rec):
            counts[rec["verdict"]] += 1
            fs.write(dumps(s) + "\n")
            fr.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
        cov = fuzz(seeds, args.count, args.seed, sink)
    secs = _t.perf_counter() - t0
    m = cov.metrics()
    (run_dir / "coverage.json").write_text(json.dumps(m, indent=1), encoding="utf-8")
    print(f"  {sum(counts.values())} enfants de {len(seeds)} graines en {secs:.1f} s "
          f"({sum(counts.values()) / secs:.0f}/s) : " + " ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print(f"  uniques {m['unique']}  doublons {m['duplicate_rate']:.1%}  invalides {m['invalid_rate']:.1%}  "
          f"nouveaute moyenne {m['mean_novelty']:.3f}  cellules politique {cov.policy_cells()[0]}/24")
    print(f"  traits : " + " ".join(f"{k}={v}" for k, v in sorted(m['features'].items())))
    return 0


SCALE_MIX = (("pairwise", 0.3), ("pairwise-routing", 0.2), ("sequence", 0.35), ("rare", 0.15))


def cmd_scale(args) -> int:
    """One rung of the ladder: generate `count` without any LLM, run it in
    `procs` shards in parallel (separate processes, no coordination beyond the
    id hash), then aggregate and triage. Everything a rung costs is printed."""
    import subprocess
    import time as _t
    from .coverage import Coverage
    from .generate import STRATEGIES, adversarial, boundary
    from .scenario import dumps
    name = f"scale-{args.count}"
    run_dir = RUNS / name
    run_dir.mkdir(parents=True, exist_ok=True)
    corpus = run_dir / "scenarios.jsonl"
    t0 = _t.perf_counter()
    ids, written = set(), 0
    with corpus.open("w", encoding="utf-8") as fh:
        for strat, share in SCALE_MIX:
            for s in STRATEGIES[strat](int(args.count * share), args.seed):
                if s["id"] not in ids:
                    ids.add(s["id"])
                    fh.write(dumps(s) + "\n")
                    written += 1
        for s in list(boundary(args.seed)) + list(adversarial(args.seed)):
            if s["id"] not in ids:
                ids.add(s["id"])
                fh.write(dumps(s) + "\n")
                written += 1
    gen_s = _t.perf_counter() - t0
    for old in run_dir.glob("results*.jsonl"):
        old.unlink()
    t1 = _t.perf_counter()
    procs = [subprocess.Popen([sys.executable, "-m", "eval_lab", "run", "--corpus", str(corpus),
                               "--run", name, "--shard", f"{k}/{args.procs}", "--lean", "--fresh"],
                              cwd=REPO, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
             for k in range(args.procs)]
    errors = [p.communicate()[1].decode(errors="replace")[-300:] for p in procs]
    run_s = _t.perf_counter() - t1
    if any(p.returncode not in (0, 1) for p in procs):
        print("  une tranche a plante :", [e for e in errors if e])
        return 2
    metas = [json.loads(f.read_text(encoding="utf-8")) for f in sorted(run_dir.glob("meta.*.json"))]
    counts = Counter()
    for m in metas:
        counts.update(m["counts"])
    cov = Coverage()
    from .runner import load_jsonl
    recs = {r["id"]: r for f in run_dir.glob("results*.jsonl") for r in load_jsonl(f)}
    for s in load_jsonl(corpus):
        cov.add(s, recs.get(s["id"]))
    cm = cov.metrics()
    (run_dir / "coverage.json").write_text(json.dumps(cm, indent=1), encoding="utf-8")
    from .regressions import known_signatures
    from .triage import triage
    t2 = _t.perf_counter()
    tri = triage(run_dir, [corpus], known_signatures(), minimise_top=10)
    (run_dir / "triage.json").write_text(json.dumps(tri, ensure_ascii=False, indent=1), encoding="utf-8")
    tri_s = _t.perf_counter() - t2
    by_class = Counter(c["class"] for c in tri["clusters"])
    total = sum(counts.values())
    row = {
        "scenarios": written, "generation_s": round(gen_s, 1), "run_s": round(run_s, 1),
        "trials_per_s": round(total / run_s), "procs": args.procs,
        "peak_mb_per_proc": max((m["peak_mb"] or 0) for m in metas),
        "results_mb": round(sum(m["bytes"] for m in metas) / 2 ** 20, 1),
        "corpus_mb": round(corpus.stat().st_size / 2 ** 20, 1),
        "verdicts": dict(counts), "duplicate_rate": cm["duplicate_rate"],
        "invalid_rate": cm["invalid_rate"], "mean_novelty": cm["mean_novelty"],
        "features": sum(cm["features"].values()), "clusters": len(tri["clusters"]),
        "new_candidates": by_class["jarvis-candidate"] + by_class["jarvis-crash"],
        "known": by_class["known"], "disputed": by_class["oracle-disputed"],
        "triage_s": round(tri_s, 1), "llm_calls": 0, "llm_cost": 0.0,
    }
    (run_dir / "scale.json").write_text(json.dumps(row, indent=1), encoding="utf-8")
    for k_, v_ in row.items():
        print(f"  {k_:<18} {v_}")
    return 0


def cmd_llm(args) -> int:
    """Opus 5.5 campaign -> validated scenarios -> run -> frontiers -> triage."""
    import random
    from .llm import AnthropicProvider, Cassette, LLMUnavailable, campaign
    from .mutate import frontier
    from .regressions import known_signatures
    from .scenario import dumps
    from .triage import triage
    run_dir = RUNS / args.run
    run_dir.mkdir(parents=True, exist_ok=True)
    cassette_path = Path(args.cassette) if args.cassette else run_dir / "cassette.jsonl"
    inner = None
    if not args.replay:
        try:
            inner = AnthropicProvider(effort=args.effort)
        except LLMUnavailable as e:
            print(f"  SDK absent : {e}  (ou --replay pour rejouer une cassette)")
            return 2
    provider = Cassette(cassette_path, inner)
    seeds = [s for s in iter_corpus("legacy") if s["surface"] != "face"]
    # What is already known — regressions, and every cluster of the runs named
    # in --baseline — is kept as a reference and never counted as a discovery.
    from .regressions import load as load_regressions
    known_sigs, known_desc = set(known_signatures()), []
    for s in load_regressions():
        r = s["lineage"]["regression"]
        known_desc.append(f"{r['id']} : {r['title']}")
    for b in filter(None, args.baseline.split(",")):
        tri_path = RUNS / b / "triage.json"
        if tri_path.exists():
            for c in json.loads(tri_path.read_text(encoding="utf-8"))["clusters"]:
                known_sigs.add(c["signature"])
                known_desc.append(f"{c.get('first_invariant')} : {str(c.get('example'))[:100]}")
    known_desc = list(dict.fromkeys(known_desc))
    for f in ("scenarios.jsonl", "results.jsonl", "transcript.jsonl"):
        (run_dir / f).unlink(missing_ok=True)
    failing: list[dict] = []
    with (run_dir / "scenarios.jsonl").open("w", encoding="utf-8") as fs, \
            (run_dir / "results.jsonl").open("w", encoding="utf-8") as fr:
        def sink(s, rec):
            fs.write(dumps(s) + "\n")
            fr.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
            if rec["verdict"] not in ("PASS", "INVALID"):
                failing.append(s)
        usage, total = campaign(provider, mode=args.mode, batches=args.batches, per_batch=args.per_batch,
                                seed_corpus=seeds, known=known_desc, max_usd=args.max_usd, sink=sink,
                                transcript=run_dir / "transcript.jsonl")
    for note in getattr(inner, "notes", []):
        print(f"  NOTE : {note}")
    rng = random.Random(0)
    fronts = {s["id"]: frontier(s, rng) for s in failing[:args.frontiers]}
    (run_dir / "frontiers.json").write_text(json.dumps(fronts, ensure_ascii=False, indent=1), encoding="utf-8")
    tri = triage(run_dir, _run_corpora(run_dir), known_sigs)
    (run_dir / "triage.json").write_text(json.dumps(tri, ensure_ascii=False, indent=1), encoding="utf-8")
    # Novelty against the 1 709 (the reference corpus), then against the
    # 1 709 + everything the non-LLM generators produced.
    from .coverage import Coverage
    base = Coverage()
    for s in seeds:
        base.add(s)
    accepted = total.accepted
    new_vs_legacy = [s for s in accepted if base.add(s) > 0]
    wide = Coverage()
    for s in list(seeds) + [x for x in iter_corpus("generated")]:
        wide.add(s)
    new_vs_all = [s for s in accepted if wide.add(s) > 0]
    by_surface = Counter(s["surface"] for s in accepted)
    novelty = {"accepted": len(accepted), "by_surface": dict(by_surface),
               "new_vs_1709": len(new_vs_legacy), "new_vs_1709_and_generated": len(new_vs_all),
               "mean_novelty_at_intake": round(sum(s["lineage"]["generator"].get("novelty", 0) for s in accepted)
                                               / max(1, len(accepted)), 4)}
    (run_dir / "novelty.json").write_text(json.dumps(novelty, indent=1), encoding="utf-8")
    acc = len(total.accepted)
    print(f"  appels {usage.calls} (echecs {usage.failures})  cout {usage.usd:.3f} $  "
          f"tokens in {usage.input} out {usage.output} cache lu {usage.cache_read} ecrit {usage.cache_write}")
    print(f"  proposes {total.proposed}  acceptes {acc}  invalides {total.invalid}  illisibles {total.unparseable}  "
          f"doublons {total.duplicate}  sans nouveaute {total.stale}  "
          f"(taux d'invalidite {total.invalid / max(1, total.proposed):.1%})")
    print(f"  nouveaute : {novelty['new_vs_1709']}/{len(accepted)} apportent un trait absent des 1 709, "
          f"{novelty['new_vs_1709_and_generated']} absent aussi des corpus generes ; par surface {dict(by_surface)}")
    print(f"  echecs {len(failing)} -> {len(tri['clusters'])} clusters ; frontieres calculees pour {len(fronts)}")
    for c in tri["clusters"]:
        print(f"  {c['cluster']} {c['class']:<18} x{c['size']:<4} {c.get('first_invariant')}")
    return 0


def cmd_grade(args) -> int:
    from .grader import grade, texts_of
    from .llm import AnthropicProvider, Cassette, LLMUnavailable
    from .runner import load_jsonl
    run_dir = RUNS / args.run
    results = [r for f in sorted(run_dir.glob("results*.jsonl")) for r in load_jsonl(f)]
    texts = texts_of(results)
    inner = None
    if not args.replay:
        try:
            inner = AnthropicProvider(effort="medium")
        except LLMUnavailable as e:
            print(f"  SDK absent : {e}")
            return 2
    counts = grade(Cassette(run_dir / "grade-cassette.jsonl", inner), texts, run_dir / "grades.jsonl",
                   limit=args.limit)
    print(f"  {len(texts)} phrases distinctes ({sum(len(v) for v in texts.values())} essais) : {counts}")
    print(f"  -> {(run_dir / 'grades.jsonl').relative_to(REPO)} (avis, jamais un verdict)")
    return 0


def cmd_real(args) -> int:
    from . import real
    if args.action == "seed":
        print(f"  {real.write_seed()} scenarios REAL ecrits -> {real.CORPUS.relative_to(REPO)}")
        return 0
    if args.action == "list":
        for s in real.load():
            print(f"  {s['id']}  [{s['family']}]\n      {s['notes']}\n")
        return 0
    out = real.record(RUNS, args.id, args.verdict, args.note)
    print(f"  {args.id} {args.verdict} -> {out.relative_to(REPO)}")
    return 0


def _run_corpora(run_dir: Path) -> list[Path]:
    """Every file a run's scenario ids may come from."""
    files = sorted((CORPUS).rglob("*.jsonl"))
    if (run_dir / "scenarios.jsonl").exists():
        files.append(run_dir / "scenarios.jsonl")
    return files


def cmd_triage(args) -> int:
    from .regressions import known_signatures
    from .triage import triage
    run_dir = RUNS / args.run
    tri = triage(run_dir, _run_corpora(run_dir), known_signatures(), minimise_top=args.minimise)
    (run_dir / "triage.json").write_text(json.dumps(tri, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  {tri['failures']} echecs sur {tri['trials']} essais -> {len(tri['clusters'])} clusters")
    for c in tri["clusters"]:
        mini = f"  min {c['original_size']}->{c['minimal_size']} o" if c.get("minimal") else ""
        print(f"  {c['cluster']} {c['class']:<18} x{c['size']:<5} {c.get('first_invariant')}{mini}")
    return 0


def cmd_regressions(args) -> int:
    from . import regressions as rg
    if args.action == "list":
        for s in rg.load():
            r = s["lineage"]["regression"]
            print(f"  {r['id']} [{r['status']}] {r['title']}  ({r['component']}, decouvert {r['discovered']['commit']})")
        return 0
    if args.action == "check":
        rows = rg.check()
        for r in rows:
            print(f"  {r['id']} [{r['status']}] {r['verdict']:<5} {r['state']}  — {r['title']}")
        return 1 if any(r["state"] == "REGRESSED" for r in rows) else 0
    # promote
    tri = json.loads((RUNS / args.run / "triage.json").read_text(encoding="utf-8"))
    c = next((x for x in tri["clusters"] if x["cluster"] == args.cluster), None)
    if c is None:
        raise SystemExit(f"cluster {args.cluster} introuvable dans {args.run}")
    s = rg.promote(c, title=args.title, component=args.component, run=args.run, status=args.status)
    print(f"  {s['lineage']['regression']['id']} promu depuis {args.cluster} -> {s['id']}")
    return 0


def cmd_report(args) -> int:
    from . import regressions as rg
    from .report import render
    from .runner import load_jsonl
    run_dir = RUNS / args.run
    tri = json.loads((run_dir / "triage.json").read_text(encoding="utf-8"))
    cov_path = run_dir / "coverage.json"
    cov = json.loads(cov_path.read_text(encoding="utf-8")) if cov_path.exists() else None
    results = [r for f in sorted(run_dir.glob("results*.jsonl")) for r in load_jsonl(f)]
    out = run_dir / "report.md"
    out.write_text(render(args.run, tri, cov, results, rg.load()), encoding="utf-8")
    print(f"  -> {out.relative_to(REPO)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m eval_lab", description="EVAL LAB V2")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("import-legacy", help="canoniser les tests existants").set_defaults(fn=cmd_import_legacy)

    r = sub.add_parser("run", help="executer un corpus")
    r.add_argument("--corpus", default="legacy")
    r.add_argument("--surface", default=None)
    r.add_argument("--run", default="latest")
    r.add_argument("--fresh", action="store_true", help="ignorer les resultats existants")
    r.add_argument("--shard", default="0/1", help="K/N : ne garder que la tranche K sur N")
    r.add_argument("--lean", action="store_true", help="ne pas stocker la trace des PASS")
    r.add_argument("--mode", choices=["fast", "full", "all"], default="all",
                   help="fast : decisions pures ; full : routeur reel, boucle et threads ; REAL jamais ici")
    r.set_defaults(fn=cmd_run)

    sc_ = sub.add_parser("scale", help="generer N, executer en P tranches paralleles, mesurer")
    sc_.add_argument("--count", type=int, default=10000)
    sc_.add_argument("--procs", type=int, default=4)
    sc_.add_argument("--seed", type=int, default=1)
    sc_.set_defaults(fn=cmd_scale)

    g = sub.add_parser("generate", help="generer un corpus sans LLM")
    g.add_argument("--strategy", required=True)
    g.add_argument("--count", type=int, default=1000)
    g.add_argument("--seed", type=int, default=1)
    g.add_argument("--name", default=None)
    g.set_defaults(fn=cmd_generate)

    fz = sub.add_parser("fuzz", help="mutation guidee par la couverture")
    fz.add_argument("--seeds", default="legacy,generated")
    fz.add_argument("--count", type=int, default=10000)
    fz.add_argument("--seed", type=int, default=1)
    fz.add_argument("--run", default="fuzz")
    fz.set_defaults(fn=cmd_fuzz)

    lm = sub.add_parser("llm", help="Opus 5.5 : generer ou chercher des contre-exemples (budget en $)")
    lm.add_argument("--mode", choices=["generate", "adversary"], default="adversary")
    lm.add_argument("--batches", type=int, default=5)
    lm.add_argument("--per-batch", type=int, default=10)
    lm.add_argument("--max-usd", type=float, default=2.0)
    lm.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"])
    lm.add_argument("--cassette", default=None, help="fichier d'enregistrement / de rejeu")
    lm.add_argument("--replay", action="store_true", help="rejouer la cassette, aucun appel reseau")
    lm.add_argument("--frontiers", type=int, default=20)
    lm.add_argument("--run", default="llm")
    lm.add_argument("--baseline", default="baseline,baseline-fuzz",
                    help="runs dont les clusters sont deja connus (references, pas des decouvertes)")
    lm.set_defaults(fn=cmd_llm)

    gr = sub.add_parser("grade", help="grader LLM des phrases dites a l'utilisateur (avis, jamais un verdict)")
    gr.add_argument("--run", default="full1")
    gr.add_argument("--limit", type=int, default=50)
    gr.add_argument("--replay", action="store_true")
    gr.set_defaults(fn=cmd_grade)

    re_ = sub.add_parser("real", help="suite REAL : seed | list | record (execution humaine)")
    re_.add_argument("action", choices=["seed", "list", "record"])
    re_.add_argument("--id")
    re_.add_argument("--verdict", choices=["PASS", "FAIL", "SKIP"])
    re_.add_argument("--note", default="")
    re_.set_defaults(fn=cmd_real)

    tr = sub.add_parser("triage", help="regrouper les echecs, classer, minimiser")
    tr.add_argument("--run", default="latest")
    tr.add_argument("--minimise", type=int, default=30, help="minimiser les N premiers clusters")
    tr.set_defaults(fn=cmd_triage)

    rg = sub.add_parser("regressions", help="corpus de regression : list | check | promote")
    rg.add_argument("action", choices=["list", "check", "promote"])
    rg.add_argument("--run", default="latest")
    rg.add_argument("--cluster")
    rg.add_argument("--title", default="")
    rg.add_argument("--component", default="")
    rg.add_argument("--status", default="open", choices=["open", "fixed"])
    rg.set_defaults(fn=cmd_regressions)

    rp = sub.add_parser("report", help="rapport humain d'un run (apres triage)")
    rp.add_argument("--run", default="latest")
    rp.set_defaults(fn=cmd_report)

    st = sub.add_parser("status", help="compter les verdicts d'un run")
    st.add_argument("--run", default="latest")
    st.set_defaults(fn=cmd_status)
    return p


def main(argv=None) -> int:
    # Redirected on Windows, stdout is cp1252: an arrow in a detail must not
    # turn a green run into a crash (server/selftest.py has exactly that bug).
    try:
        sys.stdout.reconfigure(errors="replace")
    except AttributeError:
        pass
    args = build_parser().parse_args(argv)
    return args.fn(args)
