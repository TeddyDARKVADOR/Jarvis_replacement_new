"""
cognition/store.py — la base SQLite et son schéma. Rien d'autre.

Un seul fichier, `memory/cognition.db` (déjà couvert par `memory/*.db` dans
.gitignore), bibliothèque standard uniquement : SQLite + FTS5 sont livrés avec
Python. Exportable, inspectable avec n'importe quel client SQLite, supprimable
sans conséquence pour la V1.

Le schéma est versionné par `PRAGMA user_version` : une migration est une
fonction ajoutée à `_MIGRATIONS`, jamais une modification d'une précédente.
"""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB = BASE_DIR / "memory" / "cognition.db"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


_V1 = """
CREATE TABLE events (
  id          INTEGER PRIMARY KEY,
  ts          TEXT NOT NULL,
  session_id  TEXT,
  kind        TEXT NOT NULL,      -- user_turn|assistant_turn|tool_result|memory_write|deletion|system
  actor       TEXT,
  summary     TEXT,
  payload     TEXT,               -- JSON, sans secrets
  outcome     TEXT                -- ok|error|NULL
);
CREATE INDEX events_ts ON events(ts);
CREATE INDEX events_session ON events(session_id);

CREATE TABLE memories (
  id             TEXT PRIMARY KEY,
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL,
  kind           TEXT NOT NULL,
  slot           TEXT,            -- clé logique ("relationships/sister") : ce qui peut se contredire
  content        TEXT NOT NULL,
  source         TEXT NOT NULL,
  confidence     REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
  importance     REAL NOT NULL CHECK (importance BETWEEN 0 AND 1),
  status         TEXT NOT NULL,
  valid_from     TEXT,
  valid_to       TEXT,
  affect_valence REAL,
  affect_arousal REAL,
  retention      TEXT NOT NULL DEFAULT 'normal',
  last_accessed  TEXT,
  access_count   INTEGER NOT NULL DEFAULT 0,
  event_id       INTEGER REFERENCES events(id) ON DELETE SET NULL
);
CREATE INDEX memories_slot ON memories(slot);
CREATE INDEX memories_kind ON memories(kind, status);

CREATE VIRTUAL TABLE memories_fts USING fts5(
  content, slot, content='memories', content_rowid='rowid',
  tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER memories_ai AFTER INSERT ON memories BEGIN
  INSERT INTO memories_fts(rowid, content, slot) VALUES (new.rowid, new.content, coalesce(new.slot, ''));
END;
CREATE TRIGGER memories_ad AFTER DELETE ON memories BEGIN
  INSERT INTO memories_fts(memories_fts, rowid, content, slot)
  VALUES ('delete', old.rowid, old.content, coalesce(old.slot, ''));
END;
CREATE TRIGGER memories_au AFTER UPDATE OF content, slot ON memories BEGIN
  INSERT INTO memories_fts(memories_fts, rowid, content, slot)
  VALUES ('delete', old.rowid, old.content, coalesce(old.slot, ''));
  INSERT INTO memories_fts(rowid, content, slot) VALUES (new.rowid, new.content, coalesce(new.slot, ''));
END;

CREATE TABLE memory_links (
  src      TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
  dst      TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
  relation TEXT NOT NULL,         -- supersedes|contradicts|cites|about
  PRIMARY KEY (src, dst, relation)
);

CREATE TABLE constitution_versions (
  version    TEXT PRIMARY KEY,
  ts         TEXT NOT NULL,
  sha256     TEXT NOT NULL,
  rationale  TEXT
);
"""

# Phase 3–4 : état émotionnel persistant, journal de ses causes, métadonnées.
_V2 = """
CREATE TABLE affect_state (
  var        TEXT PRIMARY KEY,
  value      REAL NOT NULL,
  updated_at REAL NOT NULL          -- secondes epoch : la décroissance en dépend
);
CREATE TABLE affect_log (
  id        INTEGER PRIMARY KEY,
  ts        TEXT NOT NULL,
  var       TEXT NOT NULL,
  before    REAL NOT NULL,
  after     REAL NOT NULL,
  rule      TEXT NOT NULL,          -- l'événement qui l'a causé (affect.RULES)
  event_id  INTEGER REFERENCES events(id) ON DELETE SET NULL
);
CREATE INDEX affect_log_ts ON affect_log(ts);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
"""

# Phase 5 : objectifs persistants et leur historique.
_V3 = """
CREATE TABLE goals (
  id                TEXT PRIMARY KEY,
  title             TEXT NOT NULL,
  kind              TEXT NOT NULL,      -- user|reliability|learning|mastery|continuity
  origin            TEXT NOT NULL,      -- matthew_assistant|user
  justification     TEXT NOT NULL,
  priority          INTEGER NOT NULL CHECK (priority BETWEEN 1 AND 5),
  status            TEXT NOT NULL,      -- proposed|active|blocked|done|cancelled|expired|withdrawn
  success_criteria  TEXT NOT NULL,
  deadline          TEXT,
  budget_actions    INTEGER NOT NULL,
  spent_actions     INTEGER NOT NULL DEFAULT 0,
  closed_reason     TEXT,
  created_at        TEXT NOT NULL,
  updated_at        TEXT NOT NULL,
  approved_at       TEXT
);
CREATE INDEX goals_status ON goals(status);
CREATE TABLE goal_events (
  id       INTEGER PRIMARY KEY,
  goal_id  TEXT NOT NULL REFERENCES goals(id),
  ts       TEXT NOT NULL,
  kind     TEXT NOT NULL,               -- proposed|progress|active|blocked|done|...
  note     TEXT,
  actor    TEXT NOT NULL                -- model|user|system
);
"""

# Phase 6 : attentes de réussite des outils, verdicts de l'utilisateur sur les
# souvenirs. Les deux sont des paires (prédiction, résultat) : la calibration.
_V4 = """
CREATE TABLE tool_outcomes (
  id         INTEGER PRIMARY KEY,
  ts         TEXT NOT NULL,
  tool       TEXT NOT NULL,
  predicted  REAL NOT NULL,          -- attente AVANT le résultat
  outcome    INTEGER NOT NULL        -- 1 réussi, 0 échoué
);
CREATE INDEX tool_outcomes_tool ON tool_outcomes(tool, id);
CREATE TABLE memory_reviews (
  id         INTEGER PRIMARY KEY,
  ts         TEXT NOT NULL,
  memory_id  TEXT NOT NULL,          -- pas de clé étrangère : « forget » efface le souvenir
  source     TEXT NOT NULL,
  confidence REAL NOT NULL,          -- la confiance qu'il avait au moment du verdict
  verdict    TEXT NOT NULL           -- validate|reject|forget
);
"""

# Phase 7 : autonomie contrôlée. Chaque séance et chaque proposition, permise
# ou refusée, est gardée : c'est le journal d'audit.
_V5 = """
ALTER TABLE goals ADD COLUMN autonomy INTEGER NOT NULL DEFAULT 0;
CREATE TABLE autonomy_runs (
  id          INTEGER PRIMARY KEY,
  goal_id     TEXT NOT NULL REFERENCES goals(id),
  started_at  TEXT NOT NULL,
  ended_at    TEXT,
  status      TEXT NOT NULL,         -- running|done|stopped|refused|needs_user|killed|error
  stop_reason TEXT,
  steps       INTEGER NOT NULL DEFAULT 0,
  question    TEXT                   -- ce que Matthew demande à l'utilisateur
);
CREATE TABLE autonomy_steps (
  id        INTEGER PRIMARY KEY,
  run_id    INTEGER NOT NULL REFERENCES autonomy_runs(id),
  ts        TEXT NOT NULL,
  proposal  TEXT,                    -- ce que le planificateur a proposé, tel quel
  decision  TEXT NOT NULL,           -- executed|refused|internal
  action    TEXT,
  args      TEXT,
  result    TEXT,
  reason    TEXT
);
"""

# v6 — vecteurs des souvenirs (cognition/embeddings.py). Le déclencheur garde
# l'oubli RÉEL : effacer un souvenir efface son vecteur, quel que soit le chemin.
_V6 = """
CREATE TABLE memory_embeddings (
  memory_id TEXT PRIMARY KEY,
  model     TEXT NOT NULL,
  dim       INTEGER NOT NULL,
  vec       BLOB NOT NULL,
  ts        TEXT NOT NULL
);
CREATE TRIGGER memory_embeddings_forget AFTER DELETE ON memories
BEGIN
  DELETE FROM memory_embeddings WHERE memory_id = old.id;
END;
"""

# v7 — mémoire commune entre PC (cognition/sync.py). Chaque changement d'un
# souvenir passe par sync_outbox, capturé ici par des déclencheurs : aucun
# chemin d'écriture ne peut l'oublier. Pendant l'application de ce qu'un autre
# PC a écrit (sync_state.applying = 1), rien n'est recapturé : pas d'écho.
_V7 = """
CREATE TABLE sync_outbox (
  id        INTEGER PRIMARY KEY,
  memory_id TEXT NOT NULL,             -- ou le sujet, pour forget_subject
  op        TEXT NOT NULL,             -- upsert|delete|forget_subject
  ts        TEXT NOT NULL
);
CREATE TABLE sync_state (k TEXT PRIMARY KEY, v TEXT);
-- Ce qui a été oublié, pour qu'un autre PC ne le rende jamais : un id effacé,
-- ou un sujet oublié (« Ayse ») avec sa date.
CREATE TABLE sync_tombstones (key TEXT PRIMARY KEY, kind TEXT NOT NULL, ts TEXT NOT NULL);
CREATE TRIGGER sync_memory_tombstone AFTER DELETE ON memories
BEGIN
  INSERT OR REPLACE INTO sync_tombstones(key, kind, ts)
  VALUES ('id:' || old.id, 'id', strftime('%Y-%m-%dT%H:%M:%S', 'now'));
END;
CREATE TRIGGER sync_memory_insert AFTER INSERT ON memories
WHEN NOT EXISTS (SELECT 1 FROM sync_state WHERE k = 'applying' AND v = '1')
BEGIN
  INSERT INTO sync_outbox(memory_id, op, ts) VALUES (new.id, 'upsert', strftime('%Y-%m-%dT%H:%M:%S', 'now'));
END;
CREATE TRIGGER sync_memory_update
AFTER UPDATE OF content, kind, source, slot, confidence, importance, status, valid_from, valid_to, retention ON memories
WHEN NOT EXISTS (SELECT 1 FROM sync_state WHERE k = 'applying' AND v = '1')
BEGIN
  INSERT INTO sync_outbox(memory_id, op, ts) VALUES (new.id, 'upsert', strftime('%Y-%m-%dT%H:%M:%S', 'now'));
END;
CREATE TRIGGER sync_memory_delete AFTER DELETE ON memories
WHEN NOT EXISTS (SELECT 1 FROM sync_state WHERE k = 'applying' AND v = '1')
BEGIN
  INSERT INTO sync_outbox(memory_id, op, ts) VALUES (old.id, 'delete', strftime('%Y-%m-%dT%H:%M:%S', 'now'));
END;
"""

_MIGRATIONS = [_V1, _V2, _V3, _V4, _V5, _V6, _V7]


class Store:
    """Une connexion partagée, protégée par un verrou.

    SQLite en mode WAL : les lectures (prompt, rappel) ne bloquent pas
    l'écriture du fil de fond. `check_same_thread=False` + verrou explicite,
    parce que les hooks arrivent de la boucle asyncio et des exécuteurs.
    """

    def __init__(self, path: Path | str = DEFAULT_DB):
        self.path = Path(path) if str(path) != ":memory:" else path
        if isinstance(self.path, Path):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False,
                                    isolation_level=None, timeout=5.0)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        if isinstance(self.path, Path):
            self.conn.execute("PRAGMA journal_mode = WAL")
            self.conn.execute("PRAGMA synchronous = NORMAL")
        self._migrate()

    def _migrate(self) -> None:
        with self.lock:
            version = self.conn.execute("PRAGMA user_version").fetchone()[0]
            for i, script in enumerate(_MIGRATIONS[version:], start=version + 1):
                self.conn.executescript("BEGIN;" + script + f"PRAGMA user_version = {i};COMMIT;")

    @property
    def schema_version(self) -> int:
        return self.conn.execute("PRAGMA user_version").fetchone()[0]

    def execute(self, sql: str, params=()) -> sqlite3.Cursor:
        with self.lock:
            return self.conn.execute(sql, params)

    def query(self, sql: str, params=()) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, params).fetchall()

    def transaction(self):
        return _Tx(self)

    def close(self) -> None:
        with self.lock:
            self.conn.close()


class _Tx:
    def __init__(self, store: Store):
        self.store = store

    def __enter__(self) -> sqlite3.Connection:
        self.store.lock.acquire()
        self.store.conn.execute("BEGIN")
        return self.store.conn

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            self.store.conn.execute("ROLLBACK" if exc_type else "COMMIT")
        finally:
            self.store.lock.release()
