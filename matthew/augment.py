"""
matthew/augment.py — rendre le jeu de données plus instructif, sans un appel de plus.

    irrelevance(rows)   ~10 % des exemples à outil, recopiés SANS l'outil juste dans
                        la liste : la bonne réponse devient « je ne peux pas », sans
                        appel. Hammer (arXiv 2410.04587, §4.2, §5.6) : retirer la
                        fonction correcte, cible vide ; ~10 % donne le meilleur
                        compromis — plus améliore la détection mais coûte en
                        précision. Pour nous : si le routeur rate le bon outil,
                        Matthew dit qu'il ne peut pas au lieu d'appeler n'importe quoi.
    mask(rows)          ~15 % des exemples à outil : noms d'outils et de paramètres
                        remplacés par des chaînes aléatoires, descriptions gardées,
                        appels réécrits en conséquence (Hammer §4.1). Matthew apprend
                        à lire la description, pas à reconnaître un nom : il résiste
                        mieux aux plugins nouveaux ou renommés. Un taux élevé ralentit
                        l'apprentissage des outils connus (§5.5) : on reste bas.
    dedup(rows, embed)  quasi-doublons retirés par le sens (cosinus des plongements de
                        la demande ≥ 0,93), glouton, corrections humaines jamais
                        retirées (POLLUX, MergeIT : la diversité compte plus que le volume).
    balance(rows)       aucun outil ne pèse plus de 3 fois la médiane (au moins 30).

Tout est déterministe (hachage de la demande) : deux constructions du même journal
donnent le même jeu.
"""
from __future__ import annotations

import copy
import hashlib
import math
import statistics

IRRELEVANT_REPLIES = [
    "Je n'ai pas d'outil pour faire ça pour le moment.",
    "Désolé, je ne peux pas faire ça avec ce dont je dispose.",
    "Ça, je ne sais pas encore le faire, désolé.",
    "Je n'ai pas ce qu'il faut pour m'en charger, malheureusement.",
    "Je ne peux pas m'en occuper pour l'instant.",
]
IRRELEVANCE_SHARE = 0.10
MASK_SHARE = 0.15
DUP_COSINE = 0.93


def _h(text: str, salt: str) -> float:
    return int(hashlib.sha1(f"{salt}:{text}".encode()).hexdigest()[:8], 16) / 0xFFFFFFFF


def _user(row: dict) -> str:
    return next((m["content"] for m in row["messages"] if m["role"] == "user"), "")


def _called(row: dict) -> list[str]:
    return [c["function"]["name"] for m in row["messages"] for c in m.get("tool_calls") or []]


def target(row: dict) -> str:
    """L'outil visé par l'exemple, ou « __chat__ »."""
    called = _called(row)
    return called[0] if called else "__chat__"


def irrelevance(rows: list[dict], share: float = IRRELEVANCE_SHARE) -> list[dict]:
    out = []
    for r in rows:
        used = set(_called(r))
        if not used or r.get("aug") or _h(_user(r), "irr") >= share:
            continue
        tools = [t for t in r.get("tools") or [] if t["function"]["name"] not in used]
        if not tools:
            continue
        sys_user = [m for m in r["messages"] if m["role"] in ("system", "user")][:2]
        reply = IRRELEVANT_REPLIES[int(_h(_user(r), "rep") * len(IRRELEVANT_REPLIES))]
        out.append({**r, "messages": sys_user + [{"role": "assistant", "content": reply}],
                    "tools": tools, "aug": "irrelevance"})
    return out


def _rand(prefix: str, seed: str) -> str:
    return f"{prefix}_{hashlib.sha1(seed.encode()).hexdigest()[:6]}"


def mask(rows: list[dict], share: float = MASK_SHARE) -> list[dict]:
    out = []
    for r in rows:
        if not _called(r) or r.get("aug") or _h(_user(r), "mask") >= share:
            continue
        seed = _user(r)
        r2 = copy.deepcopy(r)
        names, params = {}, {}
        for t in r2.get("tools") or []:
            fn = t["function"]
            old = fn["name"]
            names[old] = _rand("fn", f"{seed}:{old}")
            fn["name"] = names[old]
            props = (fn.get("parameters") or {}).get("properties") or {}
            pmap = {p: _rand("p", f"{seed}:{old}:{p}") for p in props}
            params[old] = pmap
            if props:
                fn["parameters"]["properties"] = {pmap[p]: v for p, v in props.items()}
                fn["parameters"]["required"] = [pmap.get(p, p) for p in fn["parameters"].get("required") or []]
        for m in r2["messages"]:
            for c in m.get("tool_calls") or []:
                f = c["function"]
                old = f["name"]
                f["name"] = names.get(old, old)
                args = f.get("arguments") or {}
                if isinstance(args, dict):
                    f["arguments"] = {params.get(old, {}).get(k, k): v for k, v in args.items()}
            if m["role"] == "tool" and m.get("name") in names:
                m["name"] = names[m["name"]]
        r2["aug"] = "masked"
        out.append(r2)
    return out


def _cos(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def dedup(rows: list[dict], embed, threshold: float = DUP_COSINE) -> tuple[list[dict], int]:
    """`embed(list[str]) -> list[vecteur]` ; None ou erreur → rien n'est retiré."""
    if embed is None or not rows:
        return rows, 0
    try:
        vecs = embed([_user(r) for r in rows])
    except Exception:
        return rows, 0
    kept, kept_vecs, dropped = [], [], 0
    order = sorted(range(len(rows)), key=lambda i: rows[i]["origin"] != "human")
    for i in order:
        v = vecs[i]
        if rows[i]["origin"] != "human" and any(_cos(v, k) >= threshold for k in kept_vecs):
            dropped += 1
            continue
        kept.append(rows[i])
        kept_vecs.append(v)
    kept.sort(key=lambda r: r["ts"])
    return kept, dropped


def balance(rows: list[dict], factor: float = 3.0, floor: int = 30) -> tuple[list[dict], int]:
    by: dict[str, list[dict]] = {}
    for r in rows:
        by.setdefault(target(r), []).append(r)
    if not by:
        return rows, 0
    cap = max(floor, int(factor * statistics.median(len(v) for v in by.values())))
    out, dropped = [], 0
    for v in by.values():
        keep = sorted(v, key=lambda r: (r["origin"] != "human", _h(_user(r), "bal")))[:cap]
        dropped += len(v) - len(keep)
        out += keep
    out.sort(key=lambda r: r["ts"])
    return out, dropped


def ollama_embedder(model: str = "qwen3-embedding:0.6b", base: str = "http://127.0.0.1:11434"):
    """Plongements par Ollama (déjà installé pour la cognition) ; None s'il ne répond pas."""
    import requests
    try:
        names = {m["name"] for m in requests.get(f"{base}/api/tags", timeout=2).json().get("models", [])}
    except Exception:
        return None
    if model not in names and f"{model}:latest" not in names:
        return None

    def embed(texts: list[str]) -> list[list[float]]:
        out = []
        for i in range(0, len(texts), 64):
            r = requests.post(f"{base}/api/embed", json={"model": model, "input": texts[i:i + 64]}, timeout=120)
            r.raise_for_status()
            out += r.json()["embeddings"]
        return out
    return embed
