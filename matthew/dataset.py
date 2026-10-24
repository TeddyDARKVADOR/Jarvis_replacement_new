"""
matthew/dataset.py — du journal aux fichiers d'entraînement.

    train.jsonl / eval.jsonl   {"messages": [...], "tools": [...], "origin", "ts"}
                               format chat OpenAI : Unsloth, TRL et Axolotl le
                               passent à apply_chat_template(messages, tools=…)
    dpo.jsonl                  {"prompt": [...], "chosen", "rejected"} quand une
                               correction humaine contredit la réponse du professeur
    manifest.json              comptes par origine ; gemini_targets doit valoir 0

Priorité : correction humaine > réécriture du professeur. Une réponse Gemini
n'entre jamais, une demande oubliée (forget) non plus. Doublons retirés sur la
demande normalisée (la plus récente gagne). L'évaluation prend les exemples
LES PLUS RÉCENTS (hold-out temporel) : on mesure sur ce qui vient, pas sur un
tirage qui ressemble à l'entraînement.
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime
from pathlib import Path

from collections import Counter

from matthew import TRAINABLE_ORIGINS, config
from matthew import augment as A
from matthew.client import to_openai_tools
from matthew.journal import Journal
from matthew.teacher import SYSTEM, School


#: Les clés déjà évaluées et déjà apprises (empreintes, jamais le texte) : l'évaluation reste figée.
LEDGER = "eval_ledger.json"


def _key(text: str) -> str:
    return hashlib.sha1(" ".join(text.casefold().split()).encode("utf-8")).hexdigest()


def collect(journal: Journal, teacher_records) -> tuple[list[dict], list[dict]]:
    episodes, corrections, forgotten = {}, {}, set()
    for r in journal.records():
        kind = r.get("kind")
        if kind == "episode":
            episodes[r["id"]] = r
        elif kind == "correction" and r.get("text"):
            corrections[r["of"]] = r
        elif kind == "forget":
            forgotten.add(r["of"])
    taught = {t["episode"]: t for t in teacher_records if t.get("status") == "ok"}
    decls = {d["name"]: d for d in journal.tools()}

    def tools_for(names):
        return to_openai_tools([decls[n] for n in names if n in decls])

    examples, pairs = [], []
    for eid, ep in episodes.items():
        if eid in forgotten or not ep.get("user"):
            continue
        t = taught.get(eid)
        fix = corrections.get(eid)
        if fix:
            if t and any(m.get("tool_calls") for m in t["messages"]):
                msgs = t["messages"][:-1] + [{"role": "assistant", "content": fix["text"]}]
                names = t["tools"]
            else:
                msgs = [{"role": "system", "content": SYSTEM},
                        {"role": "user", "content": ep["user"]},
                        {"role": "assistant", "content": fix["text"]}]
                names = []
            examples.append({"messages": msgs, "tools": tools_for(names), "origin": "human",
                             "ts": ep["ts"], "episode": eid, "key": _key(ep["user"])})
            if t and t["messages"][-1]["content"].strip() != fix["text"].strip():
                pairs.append({"prompt": t["messages"][:-1], "chosen": fix["text"],
                              "rejected": t["messages"][-1]["content"], "tools": tools_for(t["tools"])})
        elif t:
            examples.append({"messages": t["messages"], "tools": tools_for(t["tools"]),
                             "origin": "teacher", "teacher": t["teacher"], "ts": ep["ts"],
                             "episode": eid, "key": _key(ep["user"])})
    # Amorçage (bootstrap.py) : demandes inventées et vérifiées par le professeur ouvert.
    for t in synthetic_records(journal.root):
        user = t["messages"][1]["content"]
        examples.append({"messages": t["messages"], "tools": tools_for(t["tools"]),
                         "origin": "teacher", "teacher": t["teacher"], "synthetic": True,
                         "target": t.get("target", ""), "ts": t["ts"], "episode": t["episode"],
                         "key": _key(user)})
    # Doublons : la plus récente, et à date égale la correction humaine.
    best: dict[str, dict] = {}
    for ex in sorted(examples, key=lambda e: (e["ts"], e["origin"] == "human")):
        best[ex["key"]] = ex
    return sorted(best.values(), key=lambda e: e["ts"]), pairs


def teacher_records(root: Path):
    d = Path(root) / "teacher"
    for f in sorted(d.glob("*.jsonl")) if d.exists() else []:
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                yield json.loads(line)
            except ValueError:
                pass


def synthetic_records(root: Path):
    path = Path(root) / "synthetic" / "records.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("status") == "ok" and r.get("messages"):
            yield r


def split(examples: list[dict], share: float, ledger: dict | None = None) -> tuple[list[dict], list[dict]]:
    """Réel : les plus récents en évaluation (hold-out temporel). Synthétique : un
    tirage stable de `share` PAR OUTIL visé, pour que chaque outil soit évalué
    (par date, les derniers outils amorcés seraient tous en évaluation).

    `ledger` ({"eval": [...], "trained": [...]} de clés) fige l'évaluation d'une
    construction à l'autre : un cas déjà évalué le reste toujours, et un cas à ajouter
    est pris de préférence parmi ceux qu'aucun entraînement n'a vus. Sans lui, un cas
    appris par le champion pouvait passer en évaluation au tour suivant (le champion
    jugé sur sa copie) et les notes de deux semaines ne se comparaient pas."""
    held = set((ledger or {}).get("eval", ()))
    seen = set((ledger or {}).get("trained", ()))

    def pick(group: list[dict], k: int) -> tuple[list[dict], list[dict]]:
        """`group` dans l'ordre de préférence : déjà évalués, puis jamais vus, puis appris."""
        ev = [e for e in group if e["key"] in held]
        for e in ([e for e in group if e["key"] not in held and e["key"] not in seen]
                  + [e for e in group if e["key"] not in held and e["key"] in seen]):
            if len(ev) >= k:
                break
            ev.append(e)
        taken = {id(e) for e in ev}
        return [e for e in group if id(e) not in taken], ev

    real = [e for e in examples if not e.get("synthetic")]
    syn = [e for e in examples if e.get("synthetic")]
    n_eval = int(len(real) * share) if len(real) >= 5 else 0
    if len(real) >= 5:
        n_eval = max(1, n_eval)
    train, evals = pick(real[::-1], n_eval)
    # Par cible (outil, conversation, refus) : au moins un tiers de ses exemples, jusqu'à 3,
    # pour que chaque outil soit noté (18 exemples d'évaluation au premier champion : trop peu).
    by: dict[str, list[dict]] = {}
    for e in syn:
        by.setdefault(e.get("target", ""), []).append(e)
    for group in by.values():
        group.sort(key=lambda e: hashlib.sha1(e["key"].encode()).hexdigest())
        k = max(round(share * len(group)), min(3, len(group) // 3))
        tr, ev = pick(group, k)
        evals += ev
        train += tr
    by_ts = lambda e: e.get("ts", 0)
    return sorted(train, key=by_ts), sorted(evals, key=by_ts)


def _ledger(root: Path) -> dict:
    try:
        return json.loads((Path(root) / LEDGER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"eval": [], "trained": []}


def build(journal: Journal | None = None, school: School | None = None, out: Path | None = None,
          cfg: dict | None = None, clock=time.time, embed="auto") -> dict:
    """`embed` : fonction de plongements pour le dédoublonnage par le sens ;
    "auto" = Ollama (qwen3-embedding) s'il répond, None = pas de dédoublonnage."""
    cfg = cfg or config()
    journal = journal or Journal()
    records = list(school.records()) if school else list(teacher_records(journal.root))
    examples, pairs = collect(journal, records)
    bad = [e for e in examples if e["origin"] not in TRAINABLE_ORIGINS]
    if bad:
        raise AssertionError(f"{len(bad)} exemple(s) d'origine non autorisée")
    # Le banc scellé n'est jamais appris : tout exemple trop proche d'un de ses cas est écarté
    # (un banc modifié depuis son scellement arrête tout : SealError).
    from matthew.bench import Contamination, user_text
    near = Contamination(cfg.get("bench_cases"))
    kept = [e for e in examples if not near(user_text(e["messages"]))]
    n_bench = len(examples) - len(kept)
    examples = kept
    base = len(examples)
    if embed == "auto":
        embed = A.ollama_embedder() if cfg.get("dedup", True) else None
    examples, n_dup = A.dedup(examples, embed)
    examples, n_bal = A.balance(examples)
    ledger = _ledger(journal.root)
    train, evals = split(examples, cfg["eval_share"], ledger)
    (journal.root / LEDGER).write_text(json.dumps(
        {"eval": sorted(set(ledger["eval"]) | {e["key"] for e in evals}),
         "trained": sorted(set(ledger["trained"]) | {e["key"] for e in train})}), encoding="utf-8")
    # Augmentations : l'entraînement reçoit pertinence + masquage ; l'évaluation,
    # la pertinence seulement (pour la mesurer), jamais le masquage.
    n_irr = len(A.irrelevance(train)) + len(A.irrelevance(evals))
    train = train + A.irrelevance(train) + A.mask(train)
    evals = evals + A.irrelevance(evals)
    stamp = datetime.fromtimestamp(clock()).strftime("%Y%m%d-%H%M%S")
    out = Path(out) if out else journal.root / "datasets" / stamp
    out.mkdir(parents=True, exist_ok=True)

    def dump(name, rows):
        with (out / name).open("w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps({k: v for k, v in r.items() if k != "key"},
                                   ensure_ascii=False) + "\n")
    dump("train.jsonl", train)
    dump("eval.jsonl", evals)
    dump("dpo.jsonl", pairs)
    origins: dict[str, int] = {}
    for e in examples:
        origins[e["origin"]] = origins.get(e["origin"], 0) + 1
    manifest = {"schema": "matthew.dataset/1", "created": stamp, "dir": str(out),
                "train": len(train), "eval": len(evals), "dpo": len(pairs),
                "origins": origins, "gemini_targets": 0, "base": base,
                "dropped_duplicates": n_dup, "dropped_balance": n_bal, "dropped_bench": n_bench,
                "irrelevance": n_irr, "masked": sum(1 for e in train if e.get("aug") == "masked"),
                "per_target": dict(sorted(Counter(A.target(e) for e in examples).items())),
                "synthetic": sum(1 for e in examples if e.get("synthetic")),
                "with_tools": sum(1 for e in examples if any(m.get("tool_calls") for m in e["messages"])),
                "last_ts": examples[-1]["ts"] if examples else 0}
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return manifest
