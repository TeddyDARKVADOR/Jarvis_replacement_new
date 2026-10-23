"""
cognition/reflection.py — synthèses citées, jamais promues sans preuve (phase 3).

D'après Generative Agents (Park et al. 2023, §4.2) : quand assez d'importance
s'est accumulée, on montre au modèle les souvenirs récents et on lui demande
quelques « insights », CHACUN AVEC LES SOUVENIRS QUI LE JUSTIFIENT.

Ce qui change par rapport à Park, à cause de Huang et al. (ICLR 2024 : sans
retour externe, une réflexion n'est pas plus fiable que le reste) :

  - une réflexion est un souvenir `reflective`, source `reflection`, donc
    `proposed` et plafonnée à 0.5 de confiance (policy.py). Elle n'entre jamais
    dans le prompt comme un fait ; le rappel la marque UNVERIFIED ;
  - une citation vers un souvenir inexistant invalide l'insight entier : un
    modèle qui cite ce qui n'existe pas est en train d'inventer ;
  - le texte de l'insight n'est jamais réinjecté dans une réflexion suivante
    comme preuve : on ne cite que des épisodes et des faits.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

THRESHOLD = 1.5             # somme d'importance des épisodes depuis la dernière réflexion
MIN_INTERVAL_S = 12 * 3600  # au plus deux réflexions par jour
MAX_SOURCES = 20
MAX_INSIGHTS = 3

PROMPT = """Below are memories of an AI assistant (Matthew) about its user and their conversations, each with an id.

{memories}

What are up to {n} high-level insights about the user's habits, goals or needs that these memories support?
Rules: each insight must cite the ids of the memories that support it; do not state anything the memories do not support; prefer fewer, solid insights over many weak ones.
Answer ONLY with JSON: [{{"insight": "...", "cites": ["m1", "m4"]}}]"""


def due(memory, meta_get, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    last = meta_get("last_reflection_at")
    since = last or "0000"
    if last:
        try:
            if (now - datetime.fromisoformat(last)).total_seconds() < MIN_INTERVAL_S:
                return False
        except ValueError:
            pass
    rows = memory.store.query(
        "SELECT coalesce(sum(importance), 0) s FROM memories "
        "WHERE kind='episodic' AND status IN ('active','validated') AND created_at > ?", (since,))
    return rows[0]["s"] >= THRESHOLD


def sources(memory) -> list[dict]:
    return [dict(r) for r in memory.store.query(
        "SELECT id, kind, content, created_at FROM memories "
        "WHERE kind IN ('episodic','semantic') AND status IN ('active','validated') "
        "ORDER BY created_at DESC, rowid DESC LIMIT ?", (MAX_SOURCES,))]


def parse(text: str, alias: dict[str, str]) -> tuple[list[dict], int]:
    """(insights valides, nombre rejetés). Tolère des balises ``` autour."""
    m = re.search(r"\[.*\]", text or "", re.S)
    if not m:
        return [], 0
    try:
        items = json.loads(m.group(0))
    except ValueError:
        return [], 0
    good, rejected = [], 0
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            rejected += 1
            continue
        insight = str(it.get("insight", "")).strip()
        cites = [str(c).strip() for c in (it.get("cites") or [])]
        if not insight or not cites or any(c not in alias for c in cites):
            rejected += 1
            continue
        good.append({"insight": insight[:400], "cites": [alias[c] for c in dict.fromkeys(cites)]})
    return good[:MAX_INSIGHTS], rejected


def run(memory, generate, meta_set, now: datetime | None = None) -> dict:
    """Une passe de réflexion. `generate(prompt) -> str` (la chaîne de secours).
    Renvoie un compte rendu ; ne lève pas pour une réponse mauvaise."""
    now = now or datetime.now(timezone.utc)
    src = sources(memory)
    if len(src) < 3:
        return {"skipped": "trop peu de souvenirs"}
    alias = {f"m{i + 1}": r["id"] for i, r in enumerate(src)}
    listing = "\n".join(f"{a}: [{r['created_at'][:10]}] {r['content'][:220]}"
                        for a, r in zip(alias, src))
    text = generate(PROMPT.format(memories=listing, n=MAX_INSIGHTS))
    insights, rejected = parse(text, alias)
    stored = []
    for ins in insights:
        mid = memory.add(ins["insight"], kind="reflective", source="reflection", importance=0.6)
        with memory.store.transaction() as c:
            c.executemany("INSERT OR IGNORE INTO memory_links VALUES (?,?, 'cites')",
                          [(mid, dst) for dst in ins["cites"]])
        stored.append(mid)
    meta_set("last_reflection_at", now.isoformat(timespec="seconds"))
    return {"stored": len(stored), "rejected": rejected, "ids": stored}
