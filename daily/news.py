"""
daily/news.py — ce qui est neuf sur les sujets suivis, sans doublon, avec ses sources.

Les sujets sont ceux de l'outil manage_monitor (la même liste, dans la
mémoire). Pour chacun, les 5 premiers articles ; puis les articles qui
racontent la même chose (mêmes mots du titre, ou même adresse) sont regroupés
en UNE nouvelle, qui garde toutes ses sources. Une nouvelle déjà annoncée un
autre jour ne revient pas (son empreinte est gardée 14 jours).
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Callable

SIMILAR = 0.5
STOP = {"les", "des", "une", "dans", "pour", "avec", "sur", "est", "qui", "que", "par", "aux", "son", "ses", "plus",
        "the", "and", "for", "with", "from", "that", "this", "after", "into", "over", "new", "says"}


def words(title: str) -> set[str]:
    t = unicodedata.normalize("NFKD", title.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return {w for w in re.findall(r"[a-z0-9]{3,}", t) if w not in STOP}


def similar(a: set[str], b: set[str]) -> bool:
    return bool(a and b) and len(a & b) / len(a | b) >= SIMILAR


def cluster(items: list[dict]) -> list[dict]:
    """Regroupe les articles qui parlent de la même chose. Pur."""
    groups: list[dict] = []
    for it in items:
        w, url = words(it.get("title", "")), (it.get("url") or "").split("?")[0]
        for g in groups:
            if (url and url in g["urls"]) or similar(w, g["words"]):
                g["words"] |= w
                g["urls"].add(url)
                g["topics"].add(it.get("topic", ""))
                if it.get("source") and it["source"] not in [s for s, _ in g["sources"]]:
                    g["sources"].append((it["source"], url))
                break
        else:
            groups.append({"title": it.get("title", "").strip(), "snippet": (it.get("snippet") or "")[:200],
                           "words": set(w), "urls": {url}, "topics": {it.get("topic", "")},
                           "sources": [(it.get("source") or "?", url)]})
    for g in groups:
        g["key"] = hashlib.sha1(" ".join(sorted(g["words"])).encode()).hexdigest()[:12]
    return sorted(groups, key=lambda g: len(g["sources"]), reverse=True)   # reprise partout = plus important


def fresh(groups: list[dict], seen: dict[str, str]) -> list[dict]:
    """Sans ce qui a déjà été annoncé (même empreinte, ou titre presque identique). Pur."""
    out = []
    for g in groups:
        if g["key"] in seen:
            continue
        out.append(g)
    return out


def gather(topics: list[str], search: Callable[[str, int], list[dict]], per_topic: int = 5) -> tuple[list[dict], list[str]]:
    items, errors = [], []
    for topic in topics:
        try:
            for r in search(topic, per_topic) or []:
                items.append({**r, "topic": topic})
        except Exception as e:
            errors.append(f"{topic} : {type(e).__name__}")
    return items, errors
