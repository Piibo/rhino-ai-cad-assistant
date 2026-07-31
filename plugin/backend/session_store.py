"""SQLite-backed persistence for sessions, messages, study events, snapshots.

Database file lives next to config.json (``rhaino/plugin.db``). Uses WAL
mode so the MCP-bridge can read while the panel writes. A ``threading.Lock``
serialises writes from multiple background threads (WebSocket workers,
study-event logger, MCP panel tools).
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator, Optional

from . import schemas
from .config import _CONFIG_DIR

logger = logging.getLogger("FurniturePlugin.Store")

DB_PATH = os.path.join(_CONFIG_DIR, "plugin.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id               TEXT PRIMARY KEY,
    title            TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    use_mode         TEXT NOT NULL DEFAULT 'normal',
    participant_id   TEXT,
    rhino_doc_path   TEXT,
    condition        TEXT NOT NULL DEFAULT 'basis'
);

CREATE TABLE IF NOT EXISTS messages (
    id           TEXT PRIMARY KEY,
    session_id   TEXT NOT NULL,
    role         TEXT NOT NULL,
    content_json TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    model        TEXT,
    stop_reason  TEXT,
    modality     TEXT,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);
CREATE INDEX IF NOT EXISTS idx_messages_session
    ON messages(session_id, created_at);

CREATE TABLE IF NOT EXISTS study_events (
    id             TEXT PRIMARY KEY,
    session_id     TEXT NOT NULL,
    participant_id TEXT,
    event_type     TEXT NOT NULL,
    payload_json   TEXT NOT NULL,
    timestamp      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_study_events_session
    ON study_events(session_id, timestamp);

CREATE TABLE IF NOT EXISTS snapshots (
    id           TEXT PRIMARY KEY,
    session_id   TEXT NOT NULL,
    path         TEXT NOT NULL,
    mime_type    TEXT NOT NULL DEFAULT 'image/png',
    width        INTEGER,
    height       INTEGER,
    captured_at  TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);
CREATE INDEX IF NOT EXISTS idx_snapshots_session
    ON snapshots(session_id, captured_at);

CREATE TABLE IF NOT EXISTS exposed_parameters (
    session_id   TEXT NOT NULL,
    name         TEXT NOT NULL,
    spec_json    TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    PRIMARY KEY (session_id, name),
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

CREATE TABLE IF NOT EXISTS active_structure_contexts (
    session_id    TEXT PRIMARY KEY,
    context_json  TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

CREATE TABLE IF NOT EXISTS variants (
    session_id      TEXT NOT NULL,
    variant_id      TEXT NOT NULL,
    name            TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    layer_name      TEXT NOT NULL,
    thumbnail_json  TEXT,
    is_active       INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    PRIMARY KEY (session_id, variant_id),
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);
CREATE INDEX IF NOT EXISTS idx_variants_session
    ON variants(session_id, created_at);

-- Study-mode tables (Studienartefakt-Spec §2.4). study_sessions, consent,
-- events, parameter_changes, model_states and tool_calls are all actively
-- written (see log_tool_call / log_parameter_change / log_model_state).

CREATE TABLE IF NOT EXISTS study_sessions (
    id               TEXT PRIMARY KEY,
    session_id       TEXT NOT NULL UNIQUE,
    participant_code TEXT NOT NULL,
    condition        TEXT NOT NULL,
    task_variant     TEXT NOT NULL,
    setting          TEXT NOT NULL DEFAULT 'privat',
    is_pilot         INTEGER NOT NULL DEFAULT 0,
    order_index      INTEGER NOT NULL,
    status           TEXT NOT NULL DEFAULT 'active',
    created_at       TEXT NOT NULL,
    ended_at         TEXT,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);
CREATE INDEX IF NOT EXISTS idx_study_sessions_participant
    ON study_sessions(participant_code, order_index);

CREATE TABLE IF NOT EXISTS consent (
    id               TEXT PRIMARY KEY,
    study_session_id TEXT NOT NULL,
    text_hash        TEXT NOT NULL,
    confirmed_at     TEXT NOT NULL,
    checkboxes_json  TEXT NOT NULL,
    FOREIGN KEY (study_session_id) REFERENCES study_sessions(id)
);
CREATE INDEX IF NOT EXISTS idx_consent_study_session
    ON consent(study_session_id);

CREATE TABLE IF NOT EXISTS events (
    id               TEXT PRIMARY KEY,
    study_session_id TEXT NOT NULL,
    event_type       TEXT NOT NULL,
    payload_json     TEXT NOT NULL DEFAULT '{}',
    created_at       TEXT NOT NULL,
    FOREIGN KEY (study_session_id) REFERENCES study_sessions(id)
);
CREATE INDEX IF NOT EXISTS idx_events_study_session
    ON events(study_session_id, created_at);

CREATE TABLE IF NOT EXISTS parameter_changes (
    id               TEXT PRIMARY KEY,
    session_id       TEXT NOT NULL,
    parameter_name   TEXT NOT NULL,
    old_value        REAL,
    new_value        REAL NOT NULL,
    source           TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

CREATE TABLE IF NOT EXISTS model_states (
    id                      TEXT PRIMARY KEY,
    session_id              TEXT NOT NULL,
    label                   TEXT,
    trigger                 TEXT NOT NULL,
    triggering_tool_call_id TEXT,
    rhino_objects_json      TEXT NOT NULL DEFAULT '{}',
    viewport_path           TEXT,
    created_at              TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

CREATE TABLE IF NOT EXISTS tool_calls (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,
    message_id  TEXT,
    tool_name   TEXT NOT NULL,
    args_json   TEXT,
    result_json TEXT,
    started_at  TEXT NOT NULL,
    duration_ms INTEGER,
    FOREIGN KEY (session_id) REFERENCES sessions(id),
    FOREIGN KEY (message_id) REFERENCES messages(id)
);

-- Per-Bedingungs-Survey (§2.4 / §2.5; fragebogen-spec §10.1). Heißt aus
-- historischen Gründen ``agency_survey``, trägt aber den gesamten
-- Per-Bedingungs-Block der Lean-Variante: Mini-CSI (6), Agency (4),
-- Werkzeug-Items mit „nicht genutzt“-Flags, W-OPEN1 und R1. Eine Zeile
-- pro Bedingung (= pro study_session, 1:1 by design).
CREATE TABLE IF NOT EXISTS agency_survey (
    id               TEXT PRIMARY KEY,
    study_session_id TEXT NOT NULL,
    condition        TEXT NOT NULL,
    fragebogen_version_hash TEXT,
    self_efficacy    INTEGER,
    control          INTEGER,
    autonomy         INTEGER,
    ownership        INTEGER,
    csi_exploration  INTEGER,
    csi_expressiveness INTEGER,
    csi_immersion    INTEGER,
    csi_enjoyment    INTEGER,
    csi_results_worth_effort INTEGER,
    csi_collaboration INTEGER,
    tool_text        INTEGER,
    tool_text_unused INTEGER,
    tool_image_ref   INTEGER,
    tool_image_ref_unused INTEGER,
    tool_viewport_feedback INTEGER,
    tool_viewport_feedback_unused INTEGER,
    tool_selection_badge INTEGER,
    tool_selection_badge_unused INTEGER,
    tool_pick        INTEGER,
    tool_pick_unused INTEGER,
    tool_slider      INTEGER,
    tool_slider_unused INTEGER,
    tool_call_cards  INTEGER,
    tool_call_cards_unused INTEGER,
    tool_lock        INTEGER,
    tool_lock_unused INTEGER,
    tool_variants    INTEGER,
    tool_variants_unused INTEGER,
    tool_measure     INTEGER,                -- dormant seit 15.06.2026: Measure aus Studienumfang entfernt; Spalten bleiben fuer Bestands-DBs erhalten, werden nicht mehr befuellt
    tool_measure_unused INTEGER,             -- dormant seit 15.06.2026 (s.o.)
    tool_sketch      INTEGER,
    tool_sketch_unused INTEGER,
    tool_dialog_offers INTEGER,
    tool_dialog_offers_unused INTEGER,
    collab_editor_trap INTEGER,
    collab_system_competence INTEGER,
    collab_expression_limit INTEGER,
    tools_unused_json TEXT NOT NULL DEFAULT '[]',
    tools_unused_freetext TEXT NOT NULL DEFAULT '',
    reflection_freetext TEXT NOT NULL DEFAULT '',
    notes            TEXT NOT NULL DEFAULT '',
    submitted_at     TEXT NOT NULL,
    FOREIGN KEY (study_session_id) REFERENCES study_sessions(id)
);
CREATE INDEX IF NOT EXISTS idx_agency_survey_study_session
    ON agency_survey(study_session_id);

-- Vergleichsblock V1–V4 + Schlussfragen S1–S3 (fragebogen-spec §10.1).
-- Einmal pro Termin, referenziert den letzten der beiden Durchgänge.
CREATE TABLE IF NOT EXISTS final_survey (
    id               TEXT PRIMARY KEY,
    study_session_id TEXT NOT NULL,
    fragebogen_version_hash TEXT,
    preference_choice TEXT,
    preference_freetext TEXT NOT NULL DEFAULT '',
    v2_control       INTEGER,
    v2_expressiveness INTEGER,
    v2_exploration   INTEGER,
    v2_speed         INTEGER,
    v2_trust         INTEGER,
    v2_ownership     INTEGER,
    tool_importance_rank_1 TEXT,
    tool_importance_rank_2 TEXT,
    tool_importance_rank_3 TEXT,
    hybrid_mode_freetext TEXT NOT NULL DEFAULT '',
    surprise_freetext TEXT NOT NULL DEFAULT '',
    missing_freetext TEXT NOT NULL DEFAULT '',
    wish_freetext    TEXT NOT NULL DEFAULT '',
    submitted_at     TEXT NOT NULL,
    FOREIGN KEY (study_session_id) REFERENCES study_sessions(id)
);
CREATE INDEX IF NOT EXISTS idx_final_survey_study_session
    ON final_survey(study_session_id);

-- Demografie-Block D1–D11 (fragebogen-spec §10.1). Einmal pro
-- Teilnehmenden-Lane (participant_code + is_pilot), erhoben nach dem
-- Consent des ersten Laufs. participant_code ist denormalisiert, damit
-- der Export beider Läufe den Datensatz findet.
CREATE TABLE IF NOT EXISTS participant_demographics (
    id               TEXT PRIMARY KEY,
    study_session_id TEXT NOT NULL,
    participant_code TEXT NOT NULL,
    is_pilot         INTEGER NOT NULL DEFAULT 0,
    fragebogen_version_hash TEXT,
    age_range        TEXT,
    gender           TEXT,
    field            TEXT,
    design_experience_years TEXT,
    cad_experience_years TEXT,
    rhino_self_assessment INTEGER,
    grasshopper_self_assessment INTEGER,
    other_cad_tools_json TEXT NOT NULL DEFAULT '[]',
    genai_usage_frequency TEXT,
    genai_design_tools_json TEXT NOT NULL DEFAULT '[]',
    furniture_design_experience TEXT,
    submitted_at     TEXT NOT NULL,
    FOREIGN KEY (study_session_id) REFERENCES study_sessions(id)
);
CREATE INDEX IF NOT EXISTS idx_demographics_participant
    ON participant_demographics(participant_code, is_pilot);

-- Lock-Panel state (Studienartefakt-Spec §1.1, P5).
-- One row per (session, object_id). The session_id scope keeps locks
-- per chat run so locking carries over reloads but doesn't bleed
-- between unrelated work. Optional ``note`` lets the designer label
-- what about the object is off-limits ("Sitz darf nicht skaliert
-- werden").
CREATE TABLE IF NOT EXISTS locked_objects (
    session_id   TEXT NOT NULL,
    object_id    TEXT NOT NULL,
    object_name  TEXT NOT NULL DEFAULT '',
    note         TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    PRIMARY KEY (session_id, object_id),
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);
CREATE INDEX IF NOT EXISTS idx_locked_objects_session
    ON locked_objects(session_id, created_at);
"""

_MIGRATIONS = [
    ("messages", "modality", "TEXT"),
    ("sessions", "condition", "TEXT NOT NULL DEFAULT 'basis'"),
    ("study_sessions", "status", "TEXT NOT NULL DEFAULT 'active'"),
    ("study_sessions", "ended_at", "TEXT"),
    # Lean-Fragebogen (fragebogen-spec §10.1): agency_survey trägt den
    # gesamten Per-Bedingungs-Block. Bestands-DBs bekommen die Spalten
    # per idempotentem ALTER TABLE nachgezogen.
    ("agency_survey", "fragebogen_version_hash", "TEXT"),
    ("agency_survey", "csi_exploration", "INTEGER"),
    ("agency_survey", "csi_expressiveness", "INTEGER"),
    ("agency_survey", "csi_immersion", "INTEGER"),
    ("agency_survey", "csi_enjoyment", "INTEGER"),
    ("agency_survey", "csi_results_worth_effort", "INTEGER"),
    ("agency_survey", "csi_collaboration", "INTEGER"),
    ("agency_survey", "tool_text", "INTEGER"),
    ("agency_survey", "tool_text_unused", "INTEGER"),
    ("agency_survey", "tool_image_ref", "INTEGER"),
    ("agency_survey", "tool_image_ref_unused", "INTEGER"),
    ("agency_survey", "tool_viewport_feedback", "INTEGER"),
    ("agency_survey", "tool_viewport_feedback_unused", "INTEGER"),
    ("agency_survey", "tool_selection_badge", "INTEGER"),
    ("agency_survey", "tool_selection_badge_unused", "INTEGER"),
    ("agency_survey", "tool_pick", "INTEGER"),
    ("agency_survey", "tool_pick_unused", "INTEGER"),
    ("agency_survey", "tool_slider", "INTEGER"),
    ("agency_survey", "tool_slider_unused", "INTEGER"),
    ("agency_survey", "tool_call_cards", "INTEGER"),
    ("agency_survey", "tool_call_cards_unused", "INTEGER"),
    ("agency_survey", "tool_lock", "INTEGER"),
    ("agency_survey", "tool_lock_unused", "INTEGER"),
    ("agency_survey", "tool_variants", "INTEGER"),
    ("agency_survey", "tool_variants_unused", "INTEGER"),
    # dormant (Measure am 15.06.2026 aus dem Studienumfang entfernt): die
    # Spalten stehen im CREATE TABLE und werden hier fuer Bestands-DBs analog
    # zu den Lock-Spalten nachgezogen, damit CREATE und Migration konsistent
    # bleiben; sie werden nicht befuellt oder gelesen.
    ("agency_survey", "tool_measure", "INTEGER"),
    ("agency_survey", "tool_measure_unused", "INTEGER"),
    ("agency_survey", "tool_sketch", "INTEGER"),
    ("agency_survey", "tool_sketch_unused", "INTEGER"),
    ("agency_survey", "tools_unused_json", "TEXT NOT NULL DEFAULT '[]'"),
    ("agency_survey", "tools_unused_freetext", "TEXT NOT NULL DEFAULT ''"),
    ("agency_survey", "reflection_freetext", "TEXT NOT NULL DEFAULT ''"),
    # lean-v2: W8 (Rückfragen/Auswahlangebote) + Z1/Z2 (Zusammenarbeit mit der
    # KI, invers).
    ("agency_survey", "tool_dialog_offers", "INTEGER"),
    ("agency_survey", "tool_dialog_offers_unused", "INTEGER"),
    ("agency_survey", "collab_editor_trap", "INTEGER"),
    # Z3 Systemkompetenz-Item (02.07.2026, Fragebogen-Check nach dem Pilot)
    ("agency_survey", "collab_system_competence", "INTEGER"),
    ("agency_survey", "collab_expression_limit", "INTEGER"),
]


class SessionStore:
    """Thread-safe SQLite wrapper around the plugin data store."""

    def __init__(self, db_path: str = DB_PATH):
        self._db_path = db_path
        self._write_lock = threading.Lock()
        self._init_db()

    @property
    def db_path(self) -> str:
        """Filesystem path of the backing SQLite file (read-only)."""
        return self._db_path

    # -- internals ----------------------------------------------------------

    def _init_db(self) -> None:
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.executescript(_SCHEMA)
            self._apply_migrations(conn)
            conn.commit()

    def _apply_migrations(self, conn: sqlite3.Connection) -> None:
        for table, column, decl in _MIGRATIONS:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
            except sqlite3.OperationalError as e:
                if "duplicate column name" not in str(e).lower():
                    raise

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, check_same_thread=False, timeout=10)
        conn.row_factory = sqlite3.Row
        # foreign_keys is a PER-CONNECTION pragma (defaults OFF, not persisted
        # like journal_mode=WAL). Setting it only in _init_db left every real
        # read/write connection with FK enforcement off, so the schema's FOREIGN
        # KEY clauses were decorative and orphaned study rows (study_session →
        # missing session, consent → missing study_session) could be inserted
        # silently and break the export joins. Enforce on every connection.
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        with self._write_lock:
            conn = self._connect()
            try:
                yield conn
                conn.commit()
            finally:
                conn.close()

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    # -- sessions -----------------------------------------------------------

    def create_session(self, session: schemas.Session) -> schemas.Session:
        with self._write() as conn:
            conn.execute(
                """INSERT INTO sessions
                   (id, title, created_at, use_mode, participant_id,
                    rhino_doc_path, condition)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    session.id,
                    session.title,
                    session.created_at.isoformat(),
                    session.use_mode,
                    session.participant_id,
                    session.rhino_doc_path,
                    session.condition,
                ),
            )
        return session

    def get_session(self, session_id: str) -> Optional[schemas.Session]:
        with self._read() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        return _row_to_session(row) if row else None

    def list_sessions(self, limit: int = 100) -> list[schemas.Session]:
        with self._read() as conn:
            rows = conn.execute(
                "SELECT * FROM sessions ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_row_to_session(r) for r in rows]

    def update_session_title(self, session_id: str, title: str) -> None:
        with self._write() as conn:
            conn.execute(
                "UPDATE sessions SET title = ? WHERE id = ?",
                (title, session_id),
            )

    # -- messages -----------------------------------------------------------

    def add_message(self, message: schemas.Message) -> schemas.Message:
        content_json = json.dumps(
            [b.model_dump(mode="json") for b in message.content],
            ensure_ascii=False,
        )
        modality = message.modality
        if modality is None and message.role == "user":
            modality = _derive_modality(message.content)
        modality_json = (
            json.dumps(modality, ensure_ascii=False) if modality is not None else None
        )
        with self._write() as conn:
            conn.execute(
                """INSERT INTO messages
                   (id, session_id, role, content_json, created_at, model, stop_reason, modality)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    message.id,
                    message.session_id,
                    message.role,
                    content_json,
                    message.created_at.isoformat(),
                    message.model,
                    message.stop_reason,
                    modality_json,
                ),
            )
        message.modality = modality
        return message

    def list_messages(self, session_id: str) -> list[schemas.Message]:
        with self._read() as conn:
            rows = conn.execute(
                """SELECT * FROM messages
                   WHERE session_id = ?
                   ORDER BY created_at ASC""",
                (session_id,),
            ).fetchall()
        # Per-row guard: one schema-drifted/corrupt persisted block must not make
        # the whole session history fail to load (500). Skip the bad row instead.
        out: list[schemas.Message] = []
        for r in rows:
            try:
                out.append(_row_to_message(r))
            except Exception as e:
                logger.warning("skipping unparseable message row: %s", e)
        return out

    def latest_message(self, session_id: str) -> Optional[schemas.Message]:
        with self._read() as conn:
            row = conn.execute(
                """SELECT * FROM messages
                   WHERE session_id = ?
                   ORDER BY created_at DESC LIMIT 1""",
                (session_id,),
            ).fetchone()
        return _row_to_message(row) if row else None

    # -- study events -------------------------------------------------------

    def log_event(self, event: schemas.StudyEvent) -> schemas.StudyEvent:
        with self._write() as conn:
            conn.execute(
                """INSERT INTO study_events
                   (id, session_id, participant_id, event_type, payload_json, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    event.id,
                    event.session_id,
                    event.participant_id,
                    event.event_type,
                    json.dumps(event.payload, ensure_ascii=False),
                    event.timestamp.isoformat(),
                ),
            )
        return event

    def list_events(self, session_id: str) -> list[schemas.StudyEvent]:
        with self._read() as conn:
            rows = conn.execute(
                """SELECT * FROM study_events
                   WHERE session_id = ?
                   ORDER BY timestamp ASC""",
                (session_id,),
            ).fetchall()
        out: list[schemas.StudyEvent] = []
        for r in rows:
            try:
                out.append(_row_to_event(r))
            except Exception as e:
                logger.warning("skipping unparseable event row: %s", e)
        return out

    # -- snapshots ----------------------------------------------------------

    def add_snapshot(
        self,
        snapshot_id: str,
        session_id: str,
        path: str,
        captured_at: str,
        mime_type: str = "image/png",
        width: Optional[int] = None,
        height: Optional[int] = None,
    ) -> None:
        with self._write() as conn:
            conn.execute(
                """INSERT INTO snapshots
                   (id, session_id, path, mime_type, width, height, captured_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (snapshot_id, session_id, path, mime_type, width, height, captured_at),
            )

    def list_snapshots(self, session_id: str) -> list[dict]:
        with self._read() as conn:
            rows = conn.execute(
                """SELECT * FROM snapshots
                   WHERE session_id = ?
                   ORDER BY captured_at ASC""",
                (session_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def set_exposed_parameters(
        self,
        session_id: str,
        parameters: list[schemas.ExposedParameter],
        created_at: str,
    ) -> None:
        # Defensive dedupe by name (last wins) before the insert loop —
        # even if upstream merging slips up, this prevents an
        # IntegrityError from killing the whole sync. INSERT OR REPLACE
        # additionally guards against any race where a concurrent
        # writer slipped a row in after our DELETE.
        deduped: dict[str, schemas.ExposedParameter] = {}
        for param in parameters:
            deduped[param.name.strip().lower()] = param
        with self._write() as conn:
            conn.execute(
                "DELETE FROM exposed_parameters WHERE session_id = ?",
                (session_id,),
            )
            for param in deduped.values():
                conn.execute(
                    """INSERT OR REPLACE INTO exposed_parameters
                       (session_id, name, spec_json, created_at)
                       VALUES (?, ?, ?, ?)""",
                    (
                        session_id,
                        param.name,
                        param.model_dump_json(),
                        created_at,
                    ),
                )

    def get_exposed_parameters(
        self, session_id: str
    ) -> list[schemas.ExposedParameter]:
        with self._read() as conn:
            rows = conn.execute(
                """SELECT spec_json FROM exposed_parameters
                   WHERE session_id = ?
                   ORDER BY created_at ASC""",
                (session_id,),
            ).fetchall()
        return [
            schemas.ExposedParameter.model_validate_json(row["spec_json"])
            for row in rows
        ]

    def get_exposed_parameter(
        self, session_id: str, name: str
    ) -> Optional[schemas.ExposedParameter]:
        with self._read() as conn:
            row = conn.execute(
                """SELECT spec_json, name FROM exposed_parameters
                   WHERE session_id = ? AND name = ?""",
                (session_id, name),
            ).fetchone()
            if not row:
                row = conn.execute(
                    """SELECT spec_json, name FROM exposed_parameters
                       WHERE session_id = ? AND lower(name) = lower(?)
                       LIMIT 1""",
                    (session_id, name),
                ).fetchone()
        if not row:
            return None
        return schemas.ExposedParameter.model_validate_json(row["spec_json"])

    def update_exposed_parameter_current(
        self, session_id: str, name: str, new_current: float
    ) -> Optional[schemas.ExposedParameter]:
        # Read + modify + write in ONE write transaction (the write lock
        # serialises all writers) so a concurrent set_exposed_parameters
        # (DELETE-all + re-INSERT from possibly-stale values) cannot interleave
        # between the read and the update and clobber the new current.
        with self._write() as conn:
            row = conn.execute(
                """SELECT spec_json, name FROM exposed_parameters
                   WHERE session_id = ? AND name = ?""",
                (session_id, name),
            ).fetchone()
            if not row:
                row = conn.execute(
                    """SELECT spec_json, name FROM exposed_parameters
                       WHERE session_id = ? AND lower(name) = lower(?)
                       LIMIT 1""",
                    (session_id, name),
                ).fetchone()
            if not row:
                return None
            existing = schemas.ExposedParameter.model_validate_json(row["spec_json"])
            existing.current = new_current
            conn.execute(
                """UPDATE exposed_parameters
                   SET spec_json = ?
                   WHERE session_id = ? AND name = ?""",
                (existing.model_dump_json(), session_id, existing.name),
            )
        return existing

    def clear_exposed_parameters(self, session_id: str) -> None:
        with self._write() as conn:
            conn.execute(
                "DELETE FROM exposed_parameters WHERE session_id = ?",
                (session_id,),
            )

    def set_active_structure_context(
        self,
        context: schemas.EditableStructureContext,
    ) -> None:
        with self._write() as conn:
            conn.execute(
                """INSERT INTO active_structure_contexts
                   (session_id, context_json, updated_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(session_id) DO UPDATE SET
                       context_json = excluded.context_json,
                       updated_at = excluded.updated_at""",
                (
                    context.session_id,
                    context.model_dump_json(),
                    context.updated_at.isoformat(),
                ),
            )

    def get_active_structure_context(
        self, session_id: str
    ) -> Optional[schemas.EditableStructureContext]:
        with self._read() as conn:
            row = conn.execute(
                """SELECT context_json FROM active_structure_contexts
                   WHERE session_id = ?""",
                (session_id,),
            ).fetchone()
        if not row:
            return None
        return schemas.EditableStructureContext.model_validate_json(
            row["context_json"]
        )

    def clear_active_structure_context(self, session_id: str) -> None:
        with self._write() as conn:
            conn.execute(
                "DELETE FROM active_structure_contexts WHERE session_id = ?",
                (session_id,),
            )

    # -- variants ---------------------------------------------------------

    def add_variant(self, variant: schemas.Variant) -> schemas.Variant:
        with self._write() as conn:
            conn.execute(
                """INSERT INTO variants
                   (session_id, variant_id, name, description, layer_name,
                    thumbnail_json, is_active, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    variant.session_id,
                    variant.id,
                    variant.name,
                    variant.description,
                    variant.layer_name,
                    json.dumps(variant.thumbnail.model_dump(mode="json"))
                    if variant.thumbnail
                    else None,
                    1 if variant.is_active else 0,
                    variant.created_at.isoformat(),
                ),
            )
        return variant

    def list_variants(self, session_id: str) -> list[schemas.Variant]:
        with self._read() as conn:
            rows = conn.execute(
                """SELECT * FROM variants
                   WHERE session_id = ?
                   ORDER BY created_at ASC""",
                (session_id,),
            ).fetchall()
        return [_row_to_variant(r) for r in rows]

    def get_variant(self, session_id: str, variant_id: str) -> Optional[schemas.Variant]:
        with self._read() as conn:
            row = conn.execute(
                """SELECT * FROM variants
                   WHERE session_id = ? AND variant_id = ?""",
                (session_id, variant_id),
            ).fetchone()
        return _row_to_variant(row) if row else None

    def get_variant_by_name(self, session_id: str, name: str) -> Optional[schemas.Variant]:
        with self._read() as conn:
            row = conn.execute(
                """SELECT * FROM variants
                   WHERE session_id = ? AND name = ?""",
                (session_id, name),
            ).fetchone()
        return _row_to_variant(row) if row else None

    def get_active_variant(self, session_id: str) -> Optional[schemas.Variant]:
        with self._read() as conn:
            row = conn.execute(
                """SELECT * FROM variants
                   WHERE session_id = ? AND is_active = 1
                   LIMIT 1""",
                (session_id,),
            ).fetchone()
        return _row_to_variant(row) if row else None

    def set_active_variant(self, session_id: str, variant_id: str) -> None:
        with self._write() as conn:
            conn.execute(
                "UPDATE variants SET is_active = 0 WHERE session_id = ?",
                (session_id,),
            )
            conn.execute(
                """UPDATE variants SET is_active = 1
                   WHERE session_id = ? AND variant_id = ?""",
                (session_id, variant_id),
            )

    def update_variant_thumbnail(
        self,
        session_id: str,
        variant_id: str,
        thumbnail: schemas.ImageSource,
    ) -> None:
        with self._write() as conn:
            conn.execute(
                """UPDATE variants SET thumbnail_json = ?
                   WHERE session_id = ? AND variant_id = ?""",
                (
                    json.dumps(thumbnail.model_dump(mode="json")),
                    session_id,
                    variant_id,
                ),
            )

    def remove_variant(self, session_id: str, variant_id: str) -> None:
        with self._write() as conn:
            conn.execute(
                """DELETE FROM variants
                   WHERE session_id = ? AND variant_id = ?""",
                (session_id, variant_id),
            )

    def clear_variants(self, session_id: str) -> None:
        with self._write() as conn:
            conn.execute(
                "DELETE FROM variants WHERE session_id = ?",
                (session_id,),
            )

    # -- study sessions / consent / events (§2.4) --------------------------

    def count_study_sessions_for_participant(self, participant_code: str) -> int:
        """How many study_sessions already exist for this participant.

        Used to derive ``order_index`` for the next study session — the
        first run lands at 1, the second at 2 (§2.1).
        """
        with self._read() as conn:
            row = conn.execute(
                """SELECT COUNT(*) AS n FROM study_sessions
                   WHERE participant_code = ?
                     AND status != 'aborted'
                     AND is_pilot = 0""",
                (participant_code,),
            ).fetchone()
        return int(row["n"] or 0)

    def list_study_sessions_for_participant(
        self, participant_code: str
    ) -> list[schemas.StudySession]:
        """All study runs for one code, newest last.

        The caller decides whether pilot / aborted runs should count for a
        specific rule. Keeping the raw list available makes the frontend's
        pre-session status view and the backend's duplicate guard share the
        same facts.
        """
        with self._read() as conn:
            rows = conn.execute(
                """SELECT * FROM study_sessions
                   WHERE participant_code = ?
                   ORDER BY created_at ASC""",
                (participant_code,),
            ).fetchall()
        return [_row_to_study_session(r) for r in rows]

    def create_study_session(
        self, study_session: schemas.StudySession
    ) -> schemas.StudySession:
        with self._write() as conn:
            conn.execute(
                """INSERT INTO study_sessions
                   (id, session_id, participant_code, condition, task_variant,
                    setting, is_pilot, order_index, status, created_at, ended_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    study_session.id,
                    study_session.session_id,
                    study_session.participant_code,
                    study_session.condition,
                    study_session.task_variant,
                    study_session.setting,
                    1 if study_session.is_pilot else 0,
                    study_session.order_index,
                    study_session.status,
                    study_session.created_at.isoformat(),
                    study_session.ended_at.isoformat()
                    if study_session.ended_at is not None
                    else None,
                ),
            )
        return study_session

    def get_study_session(self, study_session_id: str) -> Optional[schemas.StudySession]:
        with self._read() as conn:
            row = conn.execute(
                "SELECT * FROM study_sessions WHERE id = ?",
                (study_session_id,),
            ).fetchone()
        return _row_to_study_session(row) if row else None

    def set_study_session_status(
        self, study_session_id: str, status: str
    ) -> Optional[schemas.StudySession]:
        if status not in {"active", "completed", "aborted"}:
            raise ValueError(f"Unsupported study session status: {status}")
        ended_at = (
            datetime.now(timezone.utc).isoformat()
            if status in {"completed", "aborted"}
            else None
        )
        with self._write() as conn:
            conn.execute(
                """UPDATE study_sessions
                   SET status = ?, ended_at = ?
                   WHERE id = ?""",
                (status, ended_at, study_session_id),
            )
        return self.get_study_session(study_session_id)

    def latest_study_session(self) -> Optional[schemas.StudySession]:
        """The most recently created study_session row, or None if empty.

        Used as a "currently active" approximation when an event
        (e.g. ``condition_toggle_blocked``) needs a study_session FK but
        the trigger doesn't carry one explicitly.
        """
        with self._read() as conn:
            row = conn.execute(
                "SELECT * FROM study_sessions ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        return _row_to_study_session(row) if row else None

    def latest_active_study_session(self) -> Optional[schemas.StudySession]:
        """Most recently created non-ended study run, used for study locks."""
        with self._read() as conn:
            row = conn.execute(
                """SELECT * FROM study_sessions
                   WHERE status = 'active'
                   ORDER BY created_at DESC
                   LIMIT 1"""
            ).fetchone()
        return _row_to_study_session(row) if row else None

    def get_study_session_for_session(
        self, session_id: str
    ) -> Optional[schemas.StudySession]:
        """Reverse lookup from a plugin session to its study-session row.

        Returns ``None`` when the session isn't part of a study run, which
        is the normal case in dev mode.
        """
        with self._read() as conn:
            row = conn.execute(
                "SELECT * FROM study_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return _row_to_study_session(row) if row else None

    def record_consent(self, consent: schemas.Consent) -> schemas.Consent:
        with self._write() as conn:
            conn.execute(
                """INSERT INTO consent
                   (id, study_session_id, text_hash, confirmed_at, checkboxes_json)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    consent.id,
                    consent.study_session_id,
                    consent.text_hash,
                    consent.confirmed_at.isoformat(),
                    json.dumps(consent.checkboxes, ensure_ascii=False),
                ),
            )
        return consent

    def latest_consent_for_study_session(
        self, study_session_id: str
    ) -> Optional[schemas.Consent]:
        """The newest consent row for a study session, or ``None`` if absent.

        Newest wins so that re-shown dialogs (e.g. after text change) can
        store a fresh confirmation without losing audit history.
        """
        with self._read() as conn:
            row = conn.execute(
                """SELECT * FROM consent
                   WHERE study_session_id = ?
                   ORDER BY confirmed_at DESC
                   LIMIT 1""",
                (study_session_id,),
            ).fetchone()
        return _row_to_consent(row) if row else None

    def latest_reusable_consent_for_participant(
        self,
        participant_code: str,
        is_pilot: bool,
        text_hash: str,
    ) -> Optional[schemas.Consent]:
        """Latest current-text consent for the same participant lane.

        Consent is still stored per study_session so each export bundle stays
        self-contained. This helper finds a prior confirmation that can be
        copied to a new run. Pilot confirmations are deliberately separate
        from real study confirmations.
        """
        with self._read() as conn:
            row = conn.execute(
                """SELECT c.*
                   FROM consent c
                   JOIN study_sessions s ON s.id = c.study_session_id
                   WHERE s.participant_code = ?
                     AND s.is_pilot = ?
                     AND s.status != 'aborted'
                     AND c.text_hash = ?
                   ORDER BY c.confirmed_at DESC
                   LIMIT 1""",
                (participant_code, 1 if is_pilot else 0, text_hash),
            ).fetchone()
        return _row_to_consent(row) if row else None

    def log_study_context_event(
        self, event: schemas.StudyContextEvent
    ) -> schemas.StudyContextEvent:
        with self._write() as conn:
            conn.execute(
                """INSERT INTO events
                   (id, study_session_id, event_type, payload_json, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    event.id,
                    event.study_session_id,
                    event.event_type,
                    json.dumps(event.payload, ensure_ascii=False),
                    event.created_at.isoformat(),
                ),
            )
        return event

    def list_study_context_events(
        self, study_session_id: str
    ) -> list[schemas.StudyContextEvent]:
        with self._read() as conn:
            rows = conn.execute(
                """SELECT * FROM events
                   WHERE study_session_id = ?
                   ORDER BY created_at ASC""",
                (study_session_id,),
            ).fetchall()
        return [_row_to_study_context_event(r) for r in rows]

    def record_agency_survey(
        self, record: schemas.AgencySurveyRecord
    ) -> schemas.AgencySurveyRecord:
        """Persist den Per-Bedingungs-Block — genau EINE Zeile pro Lauf.

        Ein erneuter Submit (Netz-Retry, Korrektur nach Teilfehler im
        end/export-Pfad) ERSETZT die bestehende Zeile statt eine
        Duplikat-Zeile anzuhängen — sonst landen Doppelantworten im
        Auswertungs-Bundle. Die Submission-Events in ``events`` bleiben
        als Audit-Spur jeder einzelnen Abgabe erhalten.
        """

        def b(v: Optional[bool]) -> Optional[int]:
            return None if v is None else int(v)

        with self._write() as conn:
            conn.execute(
                "DELETE FROM agency_survey WHERE study_session_id = ?",
                (record.study_session_id,),
            )
            conn.execute(
                """INSERT INTO agency_survey
                   (id, study_session_id, condition, fragebogen_version_hash,
                    self_efficacy, control, autonomy, ownership,
                    csi_exploration, csi_expressiveness, csi_immersion,
                    csi_enjoyment, csi_results_worth_effort, csi_collaboration,
                    tool_text, tool_text_unused,
                    tool_image_ref, tool_image_ref_unused,
                    tool_viewport_feedback, tool_viewport_feedback_unused,
                    tool_selection_badge, tool_selection_badge_unused,
                    tool_pick, tool_pick_unused,
                    tool_slider, tool_slider_unused,
                    tool_call_cards, tool_call_cards_unused,
                    tool_lock, tool_lock_unused,
                    tool_variants, tool_variants_unused,
                    tool_sketch, tool_sketch_unused,
                    tool_dialog_offers, tool_dialog_offers_unused,
                    collab_editor_trap, collab_expression_limit,
                    collab_system_competence,
                    tools_unused_json, tools_unused_freetext,
                    reflection_freetext, notes, submitted_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                           ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                           ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record.id,
                    record.study_session_id,
                    record.condition,
                    record.fragebogen_version_hash,
                    record.self_efficacy,
                    record.control,
                    record.autonomy,
                    record.ownership,
                    record.csi_exploration,
                    record.csi_expressiveness,
                    record.csi_immersion,
                    record.csi_enjoyment,
                    record.csi_results_worth_effort,
                    record.csi_collaboration,
                    record.tool_text,
                    b(record.tool_text_unused),
                    record.tool_image_ref,
                    b(record.tool_image_ref_unused),
                    record.tool_viewport_feedback,
                    b(record.tool_viewport_feedback_unused),
                    record.tool_selection_badge,
                    b(record.tool_selection_badge_unused),
                    record.tool_pick,
                    b(record.tool_pick_unused),
                    record.tool_slider,
                    b(record.tool_slider_unused),
                    record.tool_call_cards,
                    b(record.tool_call_cards_unused),
                    record.tool_lock,
                    b(record.tool_lock_unused),
                    record.tool_variants,
                    b(record.tool_variants_unused),
                    record.tool_sketch,
                    b(record.tool_sketch_unused),
                    record.tool_dialog_offers,
                    b(record.tool_dialog_offers_unused),
                    record.collab_editor_trap,
                    record.collab_expression_limit,
                    record.collab_system_competence,
                    json.dumps(record.tools_unused_selection, ensure_ascii=False),
                    record.tools_unused_freetext,
                    record.reflection_freetext,
                    record.notes,
                    record.submitted_at.isoformat(),
                ),
            )
        return record

    def latest_agency_survey(
        self, study_session_id: str
    ) -> Optional[schemas.AgencySurveyRecord]:
        with self._read() as conn:
            row = conn.execute(
                """SELECT * FROM agency_survey
                   WHERE study_session_id = ?
                   ORDER BY submitted_at DESC
                   LIMIT 1""",
                (study_session_id,),
            ).fetchone()
        return _row_to_agency_survey(row) if row else None

    def record_final_survey(
        self, record: schemas.FinalSurveyRecord
    ) -> schemas.FinalSurveyRecord:
        """Persist den Vergleichsblock — genau EINE Zeile pro Termin.

        Gleiches Ersetzen-Verhalten wie ``record_agency_survey`` (kein
        Duplikat bei Retry nach Teilfehler).
        """
        with self._write() as conn:
            conn.execute(
                "DELETE FROM final_survey WHERE study_session_id = ?",
                (record.study_session_id,),
            )
            conn.execute(
                """INSERT INTO final_survey
                   (id, study_session_id, fragebogen_version_hash,
                    preference_choice, preference_freetext,
                    v2_control, v2_expressiveness, v2_exploration,
                    v2_speed, v2_trust, v2_ownership,
                    tool_importance_rank_1, tool_importance_rank_2,
                    tool_importance_rank_3, hybrid_mode_freetext,
                    surprise_freetext, missing_freetext, wish_freetext,
                    submitted_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                           ?, ?)""",
                (
                    record.id,
                    record.study_session_id,
                    record.fragebogen_version_hash,
                    record.preference_choice,
                    record.preference_freetext,
                    record.v2_control,
                    record.v2_expressiveness,
                    record.v2_exploration,
                    record.v2_speed,
                    record.v2_trust,
                    record.v2_ownership,
                    record.tool_importance_rank_1,
                    record.tool_importance_rank_2,
                    record.tool_importance_rank_3,
                    record.hybrid_mode_freetext,
                    record.surprise_freetext,
                    record.missing_freetext,
                    record.wish_freetext,
                    record.submitted_at.isoformat(),
                ),
            )
        return record

    def latest_final_survey(
        self, study_session_id: str
    ) -> Optional[schemas.FinalSurveyRecord]:
        with self._read() as conn:
            row = conn.execute(
                """SELECT * FROM final_survey
                   WHERE study_session_id = ?
                   ORDER BY submitted_at DESC
                   LIMIT 1""",
                (study_session_id,),
            ).fetchone()
        return _row_to_final_survey(row) if row else None

    def record_demographics(
        self, record: schemas.DemographicsRecord
    ) -> schemas.DemographicsRecord:
        with self._write() as conn:
            conn.execute(
                """INSERT INTO participant_demographics
                   (id, study_session_id, participant_code, is_pilot,
                    fragebogen_version_hash, age_range, gender, field,
                    design_experience_years, cad_experience_years,
                    rhino_self_assessment, grasshopper_self_assessment,
                    other_cad_tools_json, genai_usage_frequency,
                    genai_design_tools_json, furniture_design_experience,
                    submitted_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record.id,
                    record.study_session_id,
                    record.participant_code,
                    int(record.is_pilot),
                    record.fragebogen_version_hash,
                    record.age_range,
                    record.gender,
                    record.field,
                    record.design_experience_years,
                    record.cad_experience_years,
                    record.rhino_self_assessment,
                    record.grasshopper_self_assessment,
                    json.dumps(record.other_cad_tools, ensure_ascii=False),
                    record.genai_usage_frequency,
                    json.dumps(record.genai_design_tools, ensure_ascii=False),
                    record.furniture_design_experience,
                    record.submitted_at.isoformat(),
                ),
            )
        return record

    def demographics_for_participant(
        self, participant_code: str, is_pilot: bool
    ) -> Optional[schemas.DemographicsRecord]:
        """Demografie-Datensatz einer Teilnehmenden-Lane (code + pilot-Flag).

        Pilot-Läufe haben ihre eigene Lane, damit Probedurchläufe den
        echten Teilnehmenden-Datensatz nicht blockieren (analog zur
        Consent-Reuse-Logik).
        """
        with self._read() as conn:
            row = conn.execute(
                """SELECT * FROM participant_demographics
                   WHERE participant_code = ? AND is_pilot = ?
                   ORDER BY submitted_at DESC
                   LIMIT 1""",
                (participant_code, int(is_pilot)),
            ).fetchone()
        return _row_to_demographics(row) if row else None

    # -- Gating-Abfragen für den Per-Bedingungs-Survey (Spec §4.3) ---------

    def session_has_image_upload(self, session_id: str) -> bool:
        """W2-Gate: wurde in dieser Session mindestens ein Bild angehängt?

        ``messages.modality`` ist eine JSON-Liste von Tags (z.B.
        ``["text", "image"]``), daher reicht ein LIKE auf das quotierte
        Tag.
        """
        with self._read() as conn:
            row = conn.execute(
                """SELECT 1 FROM messages
                   WHERE session_id = ? AND modality LIKE '%"image"%'
                   LIMIT 1""",
                (session_id,),
            ).fetchone()
        return row is not None

    def session_user_slider_count(self, session_id: str) -> int:
        """W6-Gate: Anzahl der Slider-Bewegungen durch die Person selbst."""
        with self._read() as conn:
            row = conn.execute(
                """SELECT COUNT(*) AS n FROM parameter_changes
                   WHERE session_id = ? AND source = 'user'""",
                (session_id,),
            ).fetchone()
        return int(row["n"]) if row else 0

    # -- auto-logged study writers (§2.4) ---------------------------------

    def log_tool_call(self, record: schemas.ToolCallRecord) -> schemas.ToolCallRecord:
        # args_json ist ein freies JSON-Textfeld (kein typisiertes Spaltenset),
        # daher reichern wir den vorhandenen Blob additiv um die HITL-Loop-
        # Metadaten an, statt neue DB-Spalten einzufuehren (migrationsfrei, P3).
        # Echte Tool-Argumente bleiben auf Top-Level; Metadaten liegen unter
        # reservierten '_'-Keys, die mit keinem bestehenden Tool-Argument
        # kollidieren.
        if isinstance(record.args, dict):
            payload: dict = dict(record.args)
        else:
            payload = {"_raw": record.args}
        if record.triggered_by:
            payload["_triggered_by"] = record.triggered_by
        if record.is_repair:
            payload["_is_repair"] = True
        if getattr(record, "is_gate_skip", False):
            payload["_is_gate_skip"] = True
        if getattr(record, "is_gate_autoresolved", False):
            payload["_is_gate_autoresolved"] = True
        with self._write() as conn:
            conn.execute(
                """INSERT INTO tool_calls
                   (id, session_id, message_id, tool_name, args_json,
                    result_json, started_at, duration_ms)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record.id,
                    record.session_id,
                    record.message_id,
                    record.tool_name,
                    json.dumps(payload, ensure_ascii=False, default=str),
                    record.result,
                    record.started_at.isoformat(),
                    record.duration_ms,
                ),
            )
        return record

    def log_parameter_change(
        self, record: schemas.ParameterChangeRecord
    ) -> schemas.ParameterChangeRecord:
        with self._write() as conn:
            conn.execute(
                """INSERT INTO parameter_changes
                   (id, session_id, parameter_name, old_value, new_value,
                    source, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    record.id,
                    record.session_id,
                    record.parameter_name,
                    record.old_value,
                    record.new_value,
                    record.source,
                    record.created_at.isoformat(),
                ),
            )
        return record

    def log_model_state(
        self, record: schemas.ModelStateRecord
    ) -> schemas.ModelStateRecord:
        with self._write() as conn:
            conn.execute(
                """INSERT INTO model_states
                   (id, session_id, label, trigger, triggering_tool_call_id,
                    rhino_objects_json, viewport_path, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record.id,
                    record.session_id,
                    record.label,
                    record.trigger,
                    record.triggering_tool_call_id,
                    json.dumps(record.rhino_objects, ensure_ascii=False, default=str),
                    record.viewport_path,
                    record.created_at.isoformat(),
                ),
            )
        return record

    # -- locked objects (§1.1, P5) -----------------------------------------

    def lock_object(
        self, locked: schemas.LockedObject
    ) -> schemas.LockedObject:
        """Insert or refresh a lock entry. Idempotent on
        (session_id, object_id) — re-locking the same object just
        updates name/note/created_at."""
        with self._write() as conn:
            conn.execute(
                """INSERT INTO locked_objects
                   (session_id, object_id, object_name, note, created_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(session_id, object_id) DO UPDATE SET
                     object_name = excluded.object_name,
                     note = excluded.note,
                     created_at = excluded.created_at""",
                (
                    locked.session_id,
                    locked.object_id,
                    locked.object_name,
                    locked.note,
                    locked.created_at.isoformat(),
                ),
            )
        return locked

    def unlock_object(self, session_id: str, object_id: str) -> bool:
        with self._write() as conn:
            cur = conn.execute(
                """DELETE FROM locked_objects
                   WHERE session_id = ? AND object_id = ?""",
                (session_id, object_id),
            )
        return cur.rowcount > 0

    def list_locked_objects(self, session_id: str) -> list[schemas.LockedObject]:
        with self._read() as conn:
            rows = conn.execute(
                """SELECT * FROM locked_objects
                   WHERE session_id = ?
                   ORDER BY created_at ASC""",
                (session_id,),
            ).fetchall()
        return [_row_to_locked_object(r) for r in rows]

    def clear_locked_objects(self, session_id: str) -> int:
        with self._write() as conn:
            cur = conn.execute(
                "DELETE FROM locked_objects WHERE session_id = ?",
                (session_id,),
            )
        return cur.rowcount

    # -- generic read for export (§2.6) ------------------------------------

    def rows_for_export(
        self, table: str, key_column: str, key_value: str
    ) -> list[dict]:
        """Return all rows of ``table`` filtered by ``key_column = key_value``.

        Read-only helper for the export pipeline. Table/column names cannot be
        SQLite-parameterised, so ``table`` is validated against the database's
        actual table set (``sqlite_master``) before it is interpolated.
        Validating against the live schema instead of a hand-maintained
        allow-list means the guard can never drift from the tables that
        actually exist: a newly created study table becomes exportable without
        touching a second list. ``export._EXPORT_TABLES`` therefore stays the
        single source of truth for *which* tables are exported.
        """
        with self._read() as conn:
            valid_tables = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            if table not in valid_tables:
                raise ValueError(
                    f"refusing to export from unknown table: {table}"
                )
            # PK columns differ across tables; the caller picks the right one.
            rows = conn.execute(
                f"SELECT * FROM {table} WHERE {key_column} = ?",
                (key_value,),
            ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Row-to-model helpers
# ---------------------------------------------------------------------------


def _row_to_session(row: sqlite3.Row) -> schemas.Session:
    # condition column was added later; older rows read via SELECT * still
    # surface it through the migration default, but a fresh row from a
    # not-yet-migrated DB or a test stub may lack the key. Fail SAFE to
    # "basis" here (Pilot 01.07.2026): a missing/empty condition must never
    # resolve to the full interaction surface.
    raw_condition = row["condition"] if "condition" in row.keys() else "basis"
    return schemas.Session(
        id=row["id"],
        title=row["title"],
        created_at=row["created_at"],
        use_mode=row["use_mode"],
        participant_id=row["participant_id"],
        rhino_doc_path=row["rhino_doc_path"],
        condition=raw_condition or "basis",
    )


def _row_to_message(row: sqlite3.Row) -> schemas.Message:
    content = json.loads(row["content_json"])
    raw_modality = row["modality"] if "modality" in row.keys() else None
    modality = json.loads(raw_modality) if raw_modality else None
    return schemas.Message(
        id=row["id"],
        session_id=row["session_id"],
        role=row["role"],
        content=content,
        created_at=row["created_at"],
        model=row["model"],
        stop_reason=row["stop_reason"],
        modality=modality,
    )


def _row_to_variant(row: sqlite3.Row) -> schemas.Variant:
    thumb = None
    raw = row["thumbnail_json"] if "thumbnail_json" in row.keys() else None
    if raw:
        try:
            thumb = schemas.ImageSource.model_validate_json(raw)
        except Exception:
            thumb = None
    return schemas.Variant(
        id=row["variant_id"],
        session_id=row["session_id"],
        name=row["name"],
        description=row["description"] or "",
        layer_name=row["layer_name"],
        thumbnail=thumb,
        is_active=bool(row["is_active"]),
        created_at=row["created_at"],
    )


def _derive_modality(content: list) -> list[str]:
    block_to_tag = {
        "text": "text",
        "image": "image",
        "sketch": "sketch",
        "selection": "selection",
        "point_pick": "point_pick",
        "component_pick": "component_pick",
    }
    tags: list[str] = []
    seen: set[str] = set()
    for block in content:
        block_type = (
            block.type if hasattr(block, "type") else (block or {}).get("type")
        )
        tag = block_to_tag.get(block_type)
        # Viewport-Schnappschüsse (Kamera-Knopf) sind KEINE Referenz-
        # bilder: eigenes Tag, damit das W2-Survey-Gate
        # (session_has_image_upload, fragebogen-spec §5.2.4) nur bei
        # echten Uploads anschlägt.
        if tag == "image":
            origin = (
                getattr(block, "origin", None)
                if hasattr(block, "type")
                else (block or {}).get("origin")
            )
            if origin == "viewport":
                tag = "viewport"
        if tag and tag not in seen:
            seen.add(tag)
            tags.append(tag)
    return tags


def _row_to_event(row: sqlite3.Row) -> schemas.StudyEvent:
    return schemas.StudyEvent(
        id=row["id"],
        session_id=row["session_id"],
        participant_id=row["participant_id"],
        event_type=row["event_type"],
        payload=json.loads(row["payload_json"]),
        timestamp=row["timestamp"],
    )


def _row_to_study_session(row: sqlite3.Row) -> schemas.StudySession:
    return schemas.StudySession(
        id=row["id"],
        session_id=row["session_id"],
        participant_code=row["participant_code"],
        condition=row["condition"],
        task_variant=row["task_variant"],
        setting=row["setting"],
        is_pilot=bool(row["is_pilot"]),
        order_index=int(row["order_index"]),
        status=row["status"] if "status" in row.keys() else "active",
        created_at=row["created_at"],
        ended_at=row["ended_at"] if "ended_at" in row.keys() else None,
    )


def _row_to_consent(row: sqlite3.Row) -> schemas.Consent:
    raw_checkboxes = row["checkboxes_json"] or "{}"
    return schemas.Consent(
        id=row["id"],
        study_session_id=row["study_session_id"],
        text_hash=row["text_hash"],
        confirmed_at=row["confirmed_at"],
        checkboxes=json.loads(raw_checkboxes),
    )


def _row_to_study_context_event(row: sqlite3.Row) -> schemas.StudyContextEvent:
    return schemas.StudyContextEvent(
        id=row["id"],
        study_session_id=row["study_session_id"],
        event_type=row["event_type"],
        payload=json.loads(row["payload_json"]),
        created_at=row["created_at"],
    )


def _row_to_locked_object(row: sqlite3.Row) -> schemas.LockedObject:
    return schemas.LockedObject(
        session_id=row["session_id"],
        object_id=row["object_id"],
        object_name=row["object_name"] or "",
        note=row["note"] or "",
        created_at=row["created_at"],
    )


def _row_to_agency_survey(row: sqlite3.Row) -> schemas.AgencySurveyRecord:
    # Defensive Spaltenzugriffe: Subset-Exports oder Bestands-DBs vor der
    # Lean-Migration kennen die neuen Spalten ggf. nicht.
    keys = row.keys()

    def g(name: str):
        return row[name] if name in keys else None

    def gb(name: str) -> Optional[bool]:
        v = g(name)
        return None if v is None else bool(v)

    try:
        unused_selection = json.loads(g("tools_unused_json") or "[]")
    except json.JSONDecodeError:
        unused_selection = []
    return schemas.AgencySurveyRecord(
        id=row["id"],
        study_session_id=row["study_session_id"],
        condition=row["condition"],
        fragebogen_version_hash=g("fragebogen_version_hash"),
        self_efficacy=g("self_efficacy"),
        control=g("control"),
        autonomy=g("autonomy"),
        ownership=g("ownership"),
        csi_exploration=g("csi_exploration"),
        csi_expressiveness=g("csi_expressiveness"),
        csi_immersion=g("csi_immersion"),
        csi_enjoyment=g("csi_enjoyment"),
        csi_results_worth_effort=g("csi_results_worth_effort"),
        csi_collaboration=g("csi_collaboration"),
        tool_text=g("tool_text"),
        tool_text_unused=gb("tool_text_unused"),
        tool_image_ref=g("tool_image_ref"),
        tool_image_ref_unused=gb("tool_image_ref_unused"),
        tool_viewport_feedback=g("tool_viewport_feedback"),
        tool_viewport_feedback_unused=gb("tool_viewport_feedback_unused"),
        tool_selection_badge=g("tool_selection_badge"),
        tool_selection_badge_unused=gb("tool_selection_badge_unused"),
        tool_pick=g("tool_pick"),
        tool_pick_unused=gb("tool_pick_unused"),
        tool_slider=g("tool_slider"),
        tool_slider_unused=gb("tool_slider_unused"),
        tool_call_cards=g("tool_call_cards"),
        tool_call_cards_unused=gb("tool_call_cards_unused"),
        tool_lock=g("tool_lock"),
        tool_lock_unused=gb("tool_lock_unused"),
        tool_variants=g("tool_variants"),
        tool_variants_unused=gb("tool_variants_unused"),
        tool_sketch=g("tool_sketch"),
        tool_sketch_unused=gb("tool_sketch_unused"),
        tool_dialog_offers=g("tool_dialog_offers"),
        tool_dialog_offers_unused=gb("tool_dialog_offers_unused"),
        collab_editor_trap=g("collab_editor_trap"),
        collab_expression_limit=g("collab_expression_limit"),
        collab_system_competence=g("collab_system_competence"),
        tools_unused_selection=unused_selection,
        tools_unused_freetext=g("tools_unused_freetext") or "",
        reflection_freetext=g("reflection_freetext") or "",
        notes=g("notes") or "",
        submitted_at=row["submitted_at"],
    )


def _row_to_final_survey(row: sqlite3.Row) -> schemas.FinalSurveyRecord:
    return schemas.FinalSurveyRecord(
        id=row["id"],
        study_session_id=row["study_session_id"],
        fragebogen_version_hash=row["fragebogen_version_hash"],
        preference_choice=row["preference_choice"],
        preference_freetext=row["preference_freetext"] or "",
        v2_control=row["v2_control"],
        v2_expressiveness=row["v2_expressiveness"],
        v2_exploration=row["v2_exploration"],
        v2_speed=row["v2_speed"],
        v2_trust=row["v2_trust"],
        v2_ownership=row["v2_ownership"],
        tool_importance_rank_1=row["tool_importance_rank_1"],
        tool_importance_rank_2=row["tool_importance_rank_2"],
        tool_importance_rank_3=row["tool_importance_rank_3"],
        hybrid_mode_freetext=row["hybrid_mode_freetext"] or "",
        surprise_freetext=row["surprise_freetext"] or "",
        missing_freetext=row["missing_freetext"] or "",
        wish_freetext=row["wish_freetext"] or "",
        submitted_at=row["submitted_at"],
    )


def _row_to_demographics(row: sqlite3.Row) -> schemas.DemographicsRecord:
    def _json_list(raw: Optional[str]) -> list[str]:
        try:
            value = json.loads(raw or "[]")
        except json.JSONDecodeError:
            return []
        return value if isinstance(value, list) else []

    return schemas.DemographicsRecord(
        id=row["id"],
        study_session_id=row["study_session_id"],
        participant_code=row["participant_code"],
        is_pilot=bool(row["is_pilot"]),
        fragebogen_version_hash=row["fragebogen_version_hash"],
        age_range=row["age_range"],
        gender=row["gender"],
        field=row["field"],
        design_experience_years=row["design_experience_years"],
        cad_experience_years=row["cad_experience_years"],
        rhino_self_assessment=row["rhino_self_assessment"],
        grasshopper_self_assessment=row["grasshopper_self_assessment"],
        other_cad_tools=_json_list(row["other_cad_tools_json"]),
        genai_usage_frequency=row["genai_usage_frequency"],
        genai_design_tools=_json_list(row["genai_design_tools_json"]),
        furniture_design_experience=row["furniture_design_experience"],
        submitted_at=row["submitted_at"],
    )


# Singleton (lazy)
_store: Optional[SessionStore] = None


def get_store() -> SessionStore:
    global _store
    if _store is None:
        _store = SessionStore()
    return _store
