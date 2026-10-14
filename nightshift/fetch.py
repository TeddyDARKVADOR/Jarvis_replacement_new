"""
nightshift/fetch.py — côté PC : le dernier rapport de la nuit, lu sans jeton.

    latest(...) -> dict | None     le rapport (frais ou en cache), None s'il n'y en a aucun
    status(...) -> dict            pour l'onglet NUIT : rapport, âge, provenance, erreur

Lit https://raw.githubusercontent.com/<dépôt>/nightshift-data/latest.json. Le
dépôt est public : aucun jeton n'est envoyé, jamais (le selftest le vérifie sur
la requête elle-même). Le dernier rapport valide est gardé dans
memory/nightshift.json avec son ETag : une relecture qui n'a pas changé coûte
une réponse 304 vide, et hors-ligne c'est la copie qui répond. Délai court
(3 s). Ne lève jamais.

Le rapport est une DONNÉE, pas une consigne : il est validé (schéma, types,
taille) et le briefing n'en reprend que des champs structurés et filtrés.

Le dépôt lu : JARVIS_NIGHTSHIFT_REPO (« propriétaire/nom ») s'il est défini,
sinon le remote origin de ce clone (lu dans .git/config, sans lancer git),
sinon le dépôt d'origine du projet.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

from nightshift import DATA_BRANCH, DEFAULT_REPO, SCHEMA
from nightshift import state

REPO_DIR = Path(__file__).resolve().parent.parent
MAX_BYTES = 1_000_000
MAX_AGE_S = 1800
_SLUG = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_STATUSES = ("success", "partial_success", "failed")


def repo_slug(repo_dir: Path = REPO_DIR) -> str:
    env = os.environ.get("JARVIS_NIGHTSHIFT_REPO", "").strip()
    if _SLUG.match(env):
        return env
    try:
        cfg = (repo_dir / ".git" / "config").read_text(encoding="utf-8", errors="replace")
        block = re.search(r'\[remote "origin"\]([^\[]*)', cfg)
        url = re.search(r"^\s*url\s*=\s*(\S+)", block.group(1), re.MULTILINE) if block else None
        if url:
            u = url.group(1).rstrip("/")
            u = u[:-4] if u.endswith(".git") else u
            m = re.search(r"github\.com[:/]([^/\s:]+/[^/\s]+)$", u)
            if m and _SLUG.match(m.group(1)):
                return m.group(1)
    except (OSError, AttributeError):
        pass
    return DEFAULT_REPO


def url_for(slug: str) -> str:
    return f"https://raw.githubusercontent.com/{slug}/{DATA_BRANCH}/latest.json"


def valid(report) -> bool:
    """Un rapport jarvis.nightly/1 dont les champs lus par JARVIS ont le bon type."""
    if not isinstance(report, dict) or report.get("schema_version") != SCHEMA:
        return False
    if report.get("status") not in _STATUSES:
        return False
    if not all(isinstance(report.get(k), str) for k in ("run_id", "summary")):
        return False
    if not all(isinstance(report.get(k, []), list) for k in ("suites", "regressions", "repaired")):
        return False
    return all(isinstance(s, dict) and isinstance(s.get("name"), str) for s in report["suites"])


def _get(url: str, etag: str, timeout: float, opener) -> tuple[int, bytes, str]:
    headers = {"User-Agent": "jarvis-nightshift/1", "Accept": "application/json"}
    if etag:
        headers["If-None-Match"] = etag
    req = urllib.request.Request(url, headers=headers)
    try:
        with opener(req, timeout=timeout) as resp:
            return int(getattr(resp, "status", 200)), resp.read(MAX_BYTES + 1), resp.headers.get("ETag", "") or ""
    except urllib.error.HTTPError as e:   # 304, 404… arrivent ici avec urllib
        return e.code, b"", ""


def status(timeout: float = 3.0, max_age_s: float = MAX_AGE_S, force: bool = False, opener=None,
           path: Path | str | None = None, now: float | None = None) -> dict:
    """Le rapport et d'où il vient. Le cache répond seul s'il a moins de
    `max_age_s` (sauf `force`). Ne lève jamais."""
    opener = opener or urllib.request.urlopen
    now = time.time() if now is None else now
    slug = repo_slug()
    url = url_for(slug)
    cache = state.load(path).get("cache") or {}
    cached = cache.get("report") if cache.get("url") == url and valid(cache.get("report")) else None
    out = {"report": cached, "repo": slug, "url": url, "fetched_at": cache.get("fetched_at") if cached else None,
           "from_cache": bool(cached), "error": None}
    try:
        age = now - float(cache.get("fetched_at") or 0)
    except (TypeError, ValueError):
        age = float("inf")
    if cached and not force and age < max_age_s:
        return out
    try:
        code, body, etag = _get(url, cache.get("etag", "") if cached else "", timeout, opener)
        if code == 304 and cached:
            state.update("cache", {"fetched_at": now}, path)
            out["fetched_at"] = now
        elif code == 404:
            out["error"] = "pas encore de rapport (branche nightshift-data absente)"
        elif code != 200:
            out["error"] = f"HTTP {code}"
        elif len(body) > MAX_BYTES:
            out["error"] = "rapport trop gros : ignoré"
        else:
            report = json.loads(body.decode("utf-8"))
            if not valid(report):
                out["error"] = "rapport invalide (schéma) : ignoré"
            else:
                state.update("cache", {"report": report, "etag": etag, "fetched_at": now, "url": url}, path)
                out.update(report=report, fetched_at=now, from_cache=False)
    except Exception as e:
        out["error"] = f"hors-ligne ou injoignable ({type(e).__name__})"
    return out


def latest(**kw) -> dict | None:
    return status(**kw)["report"]
