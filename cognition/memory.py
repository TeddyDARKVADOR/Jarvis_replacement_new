"""
cognition/memory.py — événements et souvenirs, avec provenance.

Deux couches, comme dans le document de recherche (§4.4) :

  events    le journal brut, en ajout seul : chaque tour de parole, chaque
            résultat d'outil, chaque écriture et chaque suppression. Les tours
            bruts sont éphémères (purge à RAW_TURN_DAYS) : ce sont les données
            les plus sensibles et les moins utiles à long terme.
  memories  ce que JARVIS « sait » : épisodes de session, faits, et plus tard
            réflexions et procédures. Chaque ligne porte source, confiance,
            importance, statut, validité temporelle.

Le rappel suit Generative Agents (Park et al. 2023, §4.1) :
    score = récence + importance + pertinence, chacune min-max dans [0, 1],
    récence = 0.995 ** heures depuis le dernier accès.
La pertinence est ici le BM25 de FTS5 (lexical, local, gratuit) ; des
embeddings pourront la remplacer sans toucher au reste.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import policy
from .store import Store

RAW_TURN_DAYS = 30
RECENCY_DECAY = 0.995          # par heure, comme Park et al.
#: Un souvenir sans mot commun avec la requête n'entre au rappel qu'au-delà.
SEMANTIC_RECALL = 0.38
_LEGACY_CATEGORIES = ("identity", "preferences", "projects", "relationships",
                      "wishes", "notes")
_TEXT_MAX = 2000


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        d = datetime.fromisoformat(ts)
    except ValueError:
        try:
            d = datetime.strptime(ts, "%Y-%m-%d")
        except ValueError:
            return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _fts_query(text: str) -> str:
    """Requête FTS5 sûre : chaque mot entre guillemets, réunis par OR."""
    words = policy.content_words(text)
    if not words:
        # Les mots vides de la preuve (« name », « user »…) ne le sont pas pour
        # une recherche : « name » doit trouver « name: Teddy », pas rendre les
        # plus récents dans un ordre arbitraire (cognition selftest n°59).
        words = [w for w in re.findall(r"\w{3,}", policy._fold(text or ""))]
    words = list(dict.fromkeys(words))[:12]
    return " OR ".join(f'"{w}"' for w in words)


class Memory:
    def __init__(self, store: Store, now=None):
        self.store = store
        self._now = now or (lambda: datetime.now(timezone.utc))

    def _ts(self) -> str:
        return self._now().isoformat(timespec="seconds")

    # ── Événements ───────────────────────────────────────────────────────────

    def log_event(self, kind: str, *, session_id: str | None = None, actor: str | None = None,
                  summary: str = "", payload: dict | None = None,
                  outcome: str | None = None) -> int:
        cur = self.store.execute(
            "INSERT INTO events(ts, session_id, kind, actor, summary, payload, outcome) "
            "VALUES (?,?,?,?,?,?,?)",
            (self._ts(), session_id, kind, actor, (summary or "")[:_TEXT_MAX],
             json.dumps(payload, ensure_ascii=False)[:_TEXT_MAX] if payload else None,
             outcome))
        return int(cur.lastrowid)

    def recent_user_turns(self, session_id: str | None, limit: int = 8) -> list[str]:
        rows = self.store.query(
            "SELECT summary FROM events WHERE kind='user_turn' AND session_id IS ? "
            "ORDER BY id DESC LIMIT ?", (session_id, limit))
        return [r["summary"] for r in reversed(rows)]

    def purge_raw_turns(self, days: int = RAW_TURN_DAYS) -> int:
        cutoff = (self._now() - timedelta(days=days)).isoformat(timespec="seconds")
        cur = self.store.execute(
            "DELETE FROM events WHERE kind IN ('user_turn','assistant_turn') AND ts < ?",
            (cutoff,))
        return cur.rowcount

    # ── Écriture ─────────────────────────────────────────────────────────────

    def add(self, content: str, *, kind: str, source: str, slot: str | None = None,
            importance: float = 0.5, confidence: float | None = None,
            event_id: int | None = None, valence: float | None = None,
            arousal: float | None = None, retention: str = "normal") -> str:
        """Ajoute un souvenir. Renvoie son id (existant si doublon exact).

        Sur un `slot` déjà occupé par une autre valeur active :
          - source au moins aussi fiable -> l'ancienne passe `superseded`
            (valid_to = maintenant), lien `supersedes`. Rien n'est effacé.
          - source moins fiable (le modèle contre l'utilisateur) -> la nouvelle
            reste `proposed`, l'ancienne passe `contested`, lien `contradicts`.
        """
        content = (content or "").strip()[:_TEXT_MAX]
        if not content:
            raise ValueError("souvenir vide")
        if kind not in policy.KINDS:
            raise ValueError(f"type inconnu : {kind!r}")
        status, conf = policy.initial_status(source, confidence)
        ts = self._ts()
        importance = max(0.0, min(1.0, float(importance)))

        with self.store.transaction() as c:
            current = []
            if slot:
                current = c.execute(
                    "SELECT id, content, source, confidence, status FROM memories "
                    "WHERE slot=? AND status IN ('proposed','active','validated','contested')",
                    (slot,)).fetchall()
                for row in current:
                    if row["content"].strip().lower() == content.lower():
                        # Même valeur redite : on la renforce, sans doublon. Une
                        # source plus fiable la promeut (proposed -> active).
                        # Seule une PREUVE promeut (proposed -> active) : ce que
                        # l'utilisateur a dit, ou un outil. Une réimportation
                        # de long_term.json, où la V1 a recopié l'écriture du
                        # modèle, n'en est pas une : sinon le fait non vérifié
                        # serait « blanchi » par un aller-retour dans la V1.
                        promotes = source in policy.EVIDENCE_SOURCES
                        if promotes and row["status"] in ("proposed", "contested"):
                            c.execute(
                                "UPDATE memories SET updated_at=?, confidence=max(confidence, ?), "
                                "status='active', source=? WHERE id=?",
                                (ts, conf, source, row["id"]))
                        elif promotes:
                            c.execute("UPDATE memories SET updated_at=?, "
                                      "confidence=max(confidence, ?) WHERE id=?",
                                      (ts, conf, row["id"]))
                        return row["id"]

            mid = uuid.uuid4().hex[:16]
            replaces = [r for r in current if r["status"] != "proposed"]
            stronger = all(policy.can_supersede(source, r["source"]) for r in replaces)
            if replaces and not stronger:
                status = "proposed"
            c.execute(
                "INSERT INTO memories(id, created_at, updated_at, kind, slot, content, source, "
                "confidence, importance, status, valid_from, affect_valence, affect_arousal, "
                "retention, event_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (mid, ts, ts, kind, slot, content, source, conf, importance, status, ts,
                 valence, arousal, retention, event_id))
            for r in replaces:
                if stronger:
                    c.execute("UPDATE memories SET status='superseded', valid_to=?, updated_at=? "
                              "WHERE id=?", (ts, ts, r["id"]))
                    c.execute("INSERT OR IGNORE INTO memory_links VALUES (?,?, 'supersedes')",
                              (mid, r["id"]))
                else:
                    c.execute("UPDATE memories SET status='contested', updated_at=? WHERE id=?",
                              (ts, r["id"]))
                    c.execute("INSERT OR IGNORE INTO memory_links VALUES (?,?, 'contradicts')",
                              (mid, r["id"]))
            return mid

    def set_status(self, memory_id: str, status: str) -> bool:
        if status not in policy.STATUSES:
            raise ValueError(status)
        cur = self.store.execute("UPDATE memories SET status=?, updated_at=? WHERE id=?",
                                 (status, self._ts(), memory_id))
        return cur.rowcount > 0

    def forget(self, memory_id: str | None = None, *, slot: str | None = None,
               session_id: str | None = None) -> int:
        """Suppression RÉELLE : la ligne, son index plein texte, ses liens.

        Il reste une trace `deletion` dans le journal, sans le contenu : on
        peut prouver qu'on a oublié sans garder ce qu'on a oublié.
        """
        with self.store.transaction() as c:
            if memory_id:
                rows = c.execute("SELECT id, slot FROM memories WHERE id=?", (memory_id,)).fetchall()
            elif slot:
                rows = c.execute("SELECT id, slot FROM memories WHERE slot=?", (slot,)).fetchall()
            else:
                return 0
            for r in rows:
                c.execute("DELETE FROM memories WHERE id=?", (r["id"],))
            if rows:
                c.execute(
                    "INSERT INTO events(ts, session_id, kind, actor, summary) VALUES (?,?,?,?,?)",
                    (self._ts(), session_id, "deletion", "user",
                     f"{len(rows)} souvenir(s) supprimé(s)"))
            return len(rows)

    def about(self, subject: str, limit: int = 100) -> list[dict]:
        """Tous les souvenirs qui mentionnent le sujet, quels que soient leur type
        et leur statut (y compris les épisodes et les réflexions)."""
        q = _fts_query(subject)
        if not q:
            return []
        return [dict(r) for r in self.store.query(
            "SELECT m.* FROM memories_fts JOIN memories m ON m.rowid = memories_fts.rowid "
            "WHERE memories_fts MATCH ? LIMIT ?", (q, limit))]

    def forget_about(self, subject: str, session_id: str | None = None) -> int:
        """« Oublie ce que je t'ai dit sur ma sœur » : la clé ne suffit pas. Le
        résumé de session qui la cite, la déduction faite à son sujet, la
        réflexion qui l'utilise doivent partir aussi (évaluation du 2026-10-13 :
        2 traces restaient après la suppression de la seule clé)."""
        rows = self.about(subject)
        for r in rows:
            self.forget(r["id"], session_id=session_id)
        return len(rows)

    # ── Lecture ──────────────────────────────────────────────────────────────

    def get(self, memory_id: str) -> dict | None:
        rows = self.store.query("SELECT * FROM memories WHERE id=?", (memory_id,))
        return dict(rows[0]) if rows else None

    def by_slot(self, slot: str) -> list[dict]:
        return [dict(r) for r in self.store.query(
            "SELECT * FROM memories WHERE slot=? ORDER BY created_at", (slot,))]

    def recent(self, kind: str, limit: int = 3) -> list[dict]:
        return [dict(r) for r in self.store.query(
            "SELECT * FROM memories WHERE kind=? AND status IN ('active','validated') "
            "ORDER BY created_at DESC, rowid DESC LIMIT ?", (kind, limit))]

    def _semantic(self, query: str, embedder, statuses: str, kind_sql: str,
                  params: list) -> dict[str, float]:
        """id → similarité de sens avec la requête, pour les souvenirs vectorisés."""
        if not embedder or not (query or "").strip():
            return {}
        try:
            got = (embedder.embed_query(query) if hasattr(embedder, "embed_query")
                   else (lambda r: (r[0], r[1][0]) if r else None)(embedder.embed([query])))
        except Exception:
            got = None
        if not got:
            return {}
        from .embeddings import cosine, unpack
        model, qv = got
        rows = self.store.query(
            f"SELECT m.id, e.vec FROM memory_embeddings e JOIN memories m ON m.id = e.memory_id "
            f"WHERE e.model = ? AND m.status IN ({statuses}){kind_sql}", [model] + params)
        return {r["id"]: cosine(qv, unpack(r["vec"])) for r in rows}

    def search(self, query: str, *, limit: int = 6, kinds: tuple[str, ...] | None = None,
               touch: bool = True, weights: tuple[float, float, float] = (1.0, 1.0, 1.0),
               embedder=None) -> list[dict]:
        """Rappel : récence + importance + pertinence, chacune min-max (Park 2023).

        Une requête vide renvoie les plus récents. Les souvenirs `proposed` sont
        rendus avec `verified=False` : à l'appelant de les présenter comme tels.

        Avec un `embedder` (cognition/embeddings.py), la pertinence est le
        maximum de la pertinence lexicale (BM25, min-max) et de la similarité
        de sens : « infirmière » retrouve « nurse », qui n'a aucun mot commun.
        """
        statuses = ",".join(f"'{s}'" for s in policy.RECALLABLE)
        kind_sql, params = "", []
        if kinds:
            kind_sql = " AND m.kind IN (%s)" % ",".join("?" * len(kinds))
            params = list(kinds)
        q = _fts_query(query)
        if q:
            rows = self.store.query(
                f"SELECT m.*, m.rowid AS _rid, bm25(memories_fts) AS bm FROM memories_fts "
                f"JOIN memories m ON m.rowid = memories_fts.rowid "
                f"WHERE memories_fts MATCH ? AND m.status IN ({statuses}){kind_sql} "
                f"LIMIT 200", [q] + params)
        else:
            rows = self.store.query(
                f"SELECT m.*, m.rowid AS _rid, 0.0 AS bm FROM memories m WHERE m.status IN ({statuses}){kind_sql} "
                f"ORDER BY m.created_at DESC, m.rowid DESC LIMIT 200", params)
        rows = [dict(r) for r in rows]
        sims = self._semantic(query, embedder, statuses, kind_sql, params) if q else {}
        known = {r["id"] for r in rows}
        extra = [i for i, sim in sims.items() if i not in known and sim >= SEMANTIC_RECALL]
        if extra:
            marks = ",".join("?" * len(extra))
            for r in self.store.query(f"SELECT m.*, m.rowid AS _rid, NULL AS bm FROM memories m "
                                      f"WHERE m.id IN ({marks})", extra):
                rows.append(dict(r))
        if not rows:
            return []

        now = self._now()
        cands = []
        lex = [-float(r["bm"]) for r in rows if r["bm"] is not None]
        lo, hi = (min(lex), max(lex)) if lex else (0.0, 0.0)
        for r in rows:
            last = _parse(r["last_accessed"]) or _parse(r["created_at"]) or now
            hours = max(0.0, (now - last).total_seconds() / 3600)
            lex_n = 0.0 if r["bm"] is None else ((-float(r["bm"]) - lo) / (hi - lo) if hi > lo else 1.0)
            cands.append({"row": r, "recency": RECENCY_DECAY ** hours,
                          "importance": r["importance"],
                          "relevance_n": max(lex_n, sims.get(r["id"], 0.0))})

        def _norm(key: str) -> None:
            vals = [c[key] for c in cands]
            lo, hi = min(vals), max(vals)
            for c in cands:
                c[key + "_n"] = (c[key] - lo) / (hi - lo) if hi > lo else 1.0

        for k in ("recency", "importance"):
            _norm(k)
        for c in cands:
            # Poids (récence, importance, pertinence) : tous à 1 comme chez Park ;
            # l'évaluation (evaluate.py) les fait varier pour ses ablations.
            c["score"] = (weights[0] * c["recency_n"] + weights[1] * c["importance_n"]
                          + weights[2] * c["relevance_n"])
        # À score égal, le plus récemment écrit d'abord : created_at est à la
        # seconde, et sans départage l'ordre dépendait du plan de SQLite.
        cands.sort(key=lambda c: (c["score"], c["row"]["_rid"]), reverse=True)

        out = []
        for c in cands[:max(1, limit)]:
            row = c["row"]
            row.pop("bm", None)
            row.pop("_rid", None)
            row["score"] = round(c["score"], 4)
            row["verified"] = row["status"] in ("active", "validated")
            out.append(row)
        if touch and out:
            ts = self._ts()
            with self.store.transaction() as conn:
                conn.executemany(
                    "UPDATE memories SET last_accessed=?, access_count=access_count+1 WHERE id=?",
                    [(ts, r["id"]) for r in out])
        return out

    def counts(self) -> dict:
        rows = self.store.query("SELECT kind, status, count(*) n FROM memories GROUP BY kind, status")
        out: dict = {}
        for r in rows:
            out.setdefault(r["kind"], {})[r["status"]] = r["n"]
        return out

    # ── V1 ───────────────────────────────────────────────────────────────────

    def import_legacy(self, path: Path) -> int:
        """Lit long_term.json SANS jamais l'écrire. Idempotent.

        Les entrées V1 deviennent des faits `legacy_import` (0.7). Celles déjà
        importées avec la même valeur ne bougent pas ; une valeur changée côté
        V1 remplace l'ancienne (même fiabilité de source), avec historique.
        """
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return 0
        if not isinstance(data, dict):
            return 0
        added = 0
        for cat in _LEGACY_CATEGORIES:
            items = data.get(cat)
            if not isinstance(items, dict):
                continue
            for key, entry in items.items():
                val = entry.get("value") if isinstance(entry, dict) else entry
                val = str(val or "").strip()
                if not val:
                    continue
                slot = slot_for(cat, key)
                text = f"{key.replace('_', ' ')}: {val}"
                existing = self.by_slot(slot)
                if any(m["content"].strip().lower() == text.lower() for m in existing):
                    continue          # déjà connu, quelle qu'en soit la provenance
                before = {m["id"] for m in existing}
                mid = self.add(text, kind="semantic", source="legacy_import", slot=slot,
                               importance=0.8 if cat == "identity" else 0.6)
                if mid not in before:
                    added += 1
        return added


def slot_for(category: str, key: str) -> str:
    key = re.sub(r"\s+", "_", (key or "").strip().lower())
    return f"{(category or 'notes').strip().lower()}/{key}"
