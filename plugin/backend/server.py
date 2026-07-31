"""FastAPI backend — WebSocket + REST endpoints for the AI Furniture plugin.

Exposes:
    - ``GET  /health``             — liveness probe
    - ``GET  /api/settings``       — current config
    - ``PATCH /api/settings``      — update config (merges, persists to config.json)
    - ``GET  /api/sessions``       — list sessions
    - ``POST /api/sessions``       — create session
    - ``GET  /api/sessions/{id}``  — session details
    - ``GET  /api/sessions/{id}/messages`` — message history
    - ``POST /api/sessions/{id}/messages`` — append a message (used by MCP bridge)
    - ``POST /api/study/events``   — log a study event (no-op in normal mode)
    - ``POST /api/upload/image``   — multipart image upload → base64 data URL
    - ``WS   /ws``                 — bidirectional event bus
    - ``GET  /app/{path}``         — serves built frontend (plugin/web/dist/)

Abschnitte (in Datei-Reihenfolge — Inhaltsverzeichnis fuer dieses grosse File;
jeder Abschnitt ist mit einem ``# ----``-Trenner markiert):
    Lifespan · Health · Settings (+ study-locked-settings) · Sessions (+ Debug-
    Dump-Formatter, Parameter, Strukturkontext, Varianten, Locks, Inspect) ·
    Study events · Study-mode shell (Consent, Agency, Study-Sessions, Export) ·
    Uploads · Direct plugin actions (undo/redo/clear-viewport) · Grasshopper
    (status/connect/bake) · WebSocket (/ws + _handle_*-Dispatch + Slider-
    Coalescing) · Static frontend.

Diese Datei ist bewusst EIN File (kein APIRouter-Split) — Code-Landkarte und
Begruendung in ``rhaino/plugin/ARCHITECTURE.md``.

Run via ``uvicorn backend.server:app --host 127.0.0.1 --port 8765``
(the plugin orchestrator does this in a background thread).
"""

from __future__ import annotations

import asyncio
import ast
import base64
import json
import logging
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import schemas
from .agency import load_agency_items
from .consent import CONSENT_CHECKBOX_LABELS, load_consent_text
from .dedicated_tools import dispatch_dedicated_tool
from .config import config
from .export import export_session_bundle
from .fragebogen import fragebogen_version_hash, load_fragebogen
from .tool_registry import build_tool_list
from .inspection_service import inspect_request as build_inspection_result
from .session_store import get_store
from .structure_context_service import (
    clear_session_parameters,
    sync_structure_context_after_object_change,
    sync_structure_context_from_component_pick,
    sync_structure_context_from_selection,
)
from .websocket_manager import manager

logger = logging.getLogger("FurniturePlugin.Server")

# Path to built frontend (populated after ``pnpm build`` inside plugin/web/)
_PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_WEB_DIST = os.path.join(_PLUGIN_DIR, "web", "dist")

# Running agent tasks per session, so ``chat.cancel`` can abort the in-flight
# run (and the selective preview gate can drain). Populated when ``chat.send``
# spawns ``run_agent`` (today fire-and-forget) and cleared in the done-callback.
# Keyed by session_id; a new run for the same session replaces the entry.
_AGENT_TASKS: dict[str, asyncio.Task] = {}

# Grace period after the LAST WS client disconnects before in-flight agent runs
# are cancelled. Covers the frontend's 1.5s auto-reconnect and brief network
# blips so a transient drop never kills a live run; a real panel close (no
# client back within the window) tears the ghost run down so it stops mutating
# geometry / holding gates with no UI to resolve them.
_DISCONNECT_GRACE_S = 12.0
_disconnect_cleanup_task: Optional[asyncio.Task] = None


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


def _capture_backend_version():
    """Kurzer Git-SHA + Startzeit, EINMAL beim Import erfasst -> spiegelt den
    Code-Stand, den DIESER laufende Backend-Prozess geladen hat (nicht den
    Live-Checkout). Damit ist ablesbar, ob ein gepushter/gemergter Stand schon
    laeuft oder ob ein Neustart fehlt. Faellt defensiv auf "unknown" zurueck."""
    import subprocess
    from datetime import datetime, timezone

    sha = "unknown"
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=_PLUGIN_DIR,
            capture_output=True,
            text=True,
            timeout=3,
        )
        if out.returncode == 0:
            sha = (out.stdout or "").strip() or "unknown"
    except Exception:
        sha = "unknown"
    return sha, datetime.now(timezone.utc).isoformat()


_BACKEND_SHA, _BACKEND_STARTED = _capture_backend_version()

# Gesicherter Ausgangszustand der ChooseOneObjectSettings (Rhino-Auswahlmenue),
# um ihn beim Shutdown wiederherzustellen — siehe lifespan unten.
_CHOOSE_ONE_STATE: dict = {"saved": None}


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Plugin backend starting — db at %s", get_store()._db_path)
    logger.info("Backend-Version: %s (gestartet %s)", _BACKEND_SHA, _BACKEND_STARTED)
    logger.info("Plugin root: %s", _PLUGIN_DIR)
    logger.info("Frontend dist: %s", _WEB_DIST)
    manager.bind_loop(asyncio.get_running_loop())
    # Live Rhino-selection badge: subscribe once to the doc selection events
    # and broadcast viewport.selection_changed. Idempotent (unsubscribe-before-
    # subscribe) so a backend reload can't double-subscribe. Lazy import to
    # match the rest of the viewport_bridge usage (Rhino-only module).
    try:
        from .viewport_bridge import selection_watcher

        selection_watcher.start()
    except Exception as e:  # pragma: no cover — defensive, never block startup
        logger.warning("selection watcher start failed: %s", e)
    # Undo/redo watcher: lets a Rhino Ctrl+Z reset per-object parameter-panel
    # dismissals (see undo_watcher + the frontend dismissedStructureIds). Same
    # idempotent, defensive lifecycle as the selection watcher.
    try:
        from .viewport_bridge import undo_watcher

        undo_watcher.start()
    except Exception as e:  # pragma: no cover — defensive, never block startup
        logger.warning("undo watcher start failed: %s", e)
    # K/F/O-Auswahlmenue aufhuebschen: Kandidaten-Highlight in Brand-Blau +
    # dynamisches Hover-Highlight + lesbarere Eintraege. GLOBALE Rhino-Settings;
    # Ausgangszustand wird gesichert (Restore beim Shutdown). Voll defensiv pro
    # Property — darf den Start nie blockieren und kann das Picken nicht brechen.
    try:
        import Rhino
        from System.Drawing import Color

        cos = Rhino.ApplicationSettings.ChooseOneObjectSettings
        try:
            _CHOOSE_ONE_STATE["saved"] = cos.GetCurrentState()
        except Exception:
            _CHOOSE_ONE_STATE["saved"] = None
        try:
            cos.DynamicHighlight = True
        except Exception as _e:
            logger.debug("ChooseOne DynamicHighlight: %s", _e)
        try:
            cos.UseCustomColor = True
            cos.HighlightColor = Color.FromArgb(40, 102, 246)  # Brand-Primary-Blau
        except Exception as _e:
            logger.debug("ChooseOne HighlightColor: %s", _e)
        try:
            cos.ShowObjectTypeDetails = False
        except Exception as _e:
            logger.debug("ChooseOne ShowObjectTypeDetails: %s", _e)
        logger.info("Auswahlmenue getrimmt (Brand-Blau + clean)")
    except Exception as e:  # pragma: no cover — never block startup
        logger.debug("ChooseOneObjectSettings tuning skipped: %s", e)
    yield
    try:
        from .viewport_bridge import selection_watcher

        selection_watcher.stop()
    except Exception as e:  # pragma: no cover — defensive
        logger.warning("selection watcher stop failed: %s", e)
    try:
        from .viewport_bridge import undo_watcher

        undo_watcher.stop()
    except Exception as e:  # pragma: no cover — defensive
        logger.warning("undo watcher stop failed: %s", e)
    # Auswahlmenue-Settings auf den gesicherten Ausgangszustand zuruecksetzen,
    # damit das Rhino des Nutzers nach dem Plugin-Ende unveraendert bleibt.
    try:
        saved = _CHOOSE_ONE_STATE.get("saved")
        if saved is not None:
            import Rhino

            Rhino.ApplicationSettings.ChooseOneObjectSettings.UpdateFromState(saved)
    except Exception as e:  # pragma: no cover — defensive
        logger.debug("ChooseOneObjectSettings restore failed: %s", e)
    # Cancel in-flight agent runs + background tasks so they don't keep mutating
    # the document or holding gates after the backend stops (e.g. a uvicorn
    # restart inside the same Rhino process).
    try:
        for _t in list(_AGENT_TASKS.values()):
            _t.cancel()
        _AGENT_TASKS.clear()
        for _t in list(_BG_TASKS):
            _t.cancel()
        _BG_TASKS.clear()
    except Exception as e:  # pragma: no cover — defensive
        logger.debug("task cancellation on shutdown failed: %s", e)
    # Disable viewport overlay conduits so stale highlights / persistent refs /
    # point markers don't bleed into the next session opened in this Rhino run.
    try:
        from .viewport_bridge import highlight as _highlight_mod

        _highlight_mod.teardown_overlays()
    except Exception as e:  # pragma: no cover — defensive
        logger.warning("overlay teardown on shutdown failed: %s", e)
    logger.info("Plugin backend shutting down")


app = FastAPI(
    title="AI Furniture Plugin",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS — WebView2 requests come in with an odd origin; allow localhost freely
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "version": "0.1.0",
        "backend_sha": _BACKEND_SHA,
        "backend_started": _BACKEND_STARTED,
        "use_mode": config.use_mode,
        "backend_mode": config.backend_mode,
        "clients": manager.client_count,
        "plugin_root": _PLUGIN_DIR,
        "frontend_dist": _WEB_DIST,
    }


@app.get("/api/version")
def api_version() -> dict:
    """Code-Stand DIESES laufenden Backend-Prozesses (Git-SHA bei Prozess-Start
    erfasst -> spiegelt, was geladen ist, nicht den Live-Checkout). Frontend
    vergleicht das mit seinem Build-SHA, um den Deploy-Gap sichtbar zu machen."""
    return {
        "backend_sha": _BACKEND_SHA,
        "backend_started": _BACKEND_STARTED,
        "backend_mode": config.backend_mode,
        "use_mode": config.use_mode,
    }


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


@app.get("/api/settings", response_model=schemas.Settings)
def get_settings() -> schemas.Settings:
    return schemas.Settings(**config.as_dict())


# Fields the developer is forbidden from changing once study mode is on
# (Studienartefakt-Spec §1.2 + §3.4 + §3.5). Attempts are logged and
# rejected with 409 so a misconfigured admin tool can't silently drift
# the study halfway through. `backend_mode` is in the set so a user
# can't downgrade an active study from API to MCP — the MCP path
# wouldn't satisfy the §3.5 manifest guarantees.
_STUDY_LOCKED_SETTINGS: frozenset[str] = frozenset(
    {"default_condition", "model", "backend_mode"}
)


def _detect_locked_changes(
    patch: dict, allowed: set[str]
) -> list[tuple[str, object]]:
    """Return the locked-key/value pairs in ``patch`` that would change
    the current config under study-mode. Empty when study mode is off
    or the patch only touches unlocked fields / re-asserts current
    values."""
    if not config.is_study_mode:
        return []
    out: list[tuple[str, object]] = []
    for key, value in patch.items():
        if key not in allowed or key not in _STUDY_LOCKED_SETTINGS:
            continue
        if getattr(config, key, None) != value:
            out.append((key, value))
    return out


async def _log_locked_change_attempts(
    rejected: list[tuple[str, object]]
) -> None:
    """Persist + broadcast condition_toggle_blocked / model_change_blocked
    events for each rejected key. Anchors to the latest study_session
    when one exists; otherwise only broadcasts."""
    if not rejected:
        return
    store = get_store()
    anchor = store.latest_active_study_session() or store.latest_study_session()
    for key, attempted in rejected:
        event_type = (
            "condition_toggle_blocked"
            if key == "default_condition"
            else "model_change_blocked"
        )
        event_payload = {"key": key, "attempted_value": attempted}
        if anchor is not None:
            store.log_study_context_event(
                schemas.StudyContextEvent(
                    study_session_id=anchor.id,
                    event_type=event_type,
                    payload=event_payload,
                )
            )
        await manager.broadcast(
            schemas.WsEvent(
                type="study.event_logged",
                payload={
                    "event_type": event_type,
                    "study_session_id": anchor.id if anchor else None,
                    **event_payload,
                },
            )
        )


@app.patch("/api/settings", response_model=schemas.Settings)
async def update_settings(patch: dict) -> schemas.Settings:
    allowed = set(schemas.Settings.model_fields.keys())
    rejected = _detect_locked_changes(patch, allowed)
    if rejected:
        # Reject the whole patch atomically so a half-applied save can't
        # silently drift other fields. Event log + 409 with the offending
        # keys in the detail body.
        await _log_locked_change_attempts(rejected)
        keys = ", ".join(k for k, _ in rejected)
        raise HTTPException(
            status_code=409,
            detail=(
                f"Studienmodus ist aktiv — die Felder {keys} sind gesperrt. "
                "Bitte den Studienmodus deaktivieren, bevor diese Einstellungen "
                "geändert werden."
            ),
        )
    for key, value in patch.items():
        if key in allowed:
            setattr(config, key, value)
    config.save()
    merged = schemas.Settings(**config.as_dict())
    await manager.broadcast(
        schemas.WsEvent(type="settings.updated", payload=merged.model_dump(mode="json"))
    )
    return merged


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


@app.get("/api/sessions", response_model=list[schemas.Session])
def list_sessions() -> list[schemas.Session]:
    return get_store().list_sessions()


@app.post("/api/sessions", response_model=schemas.Session)
async def create_session(session: schemas.Session) -> schemas.Session:
    if session.use_mode == "study" and not session.participant_id:
        session.participant_id = config.participant_id or None
    # Seed the study condition from the developer-facing Settings default
    # (Studienartefakt-Spec §1.2). In study mode (Schritt 2) the
    # pre-session dialog will pass an explicit condition that must win
    # over this default — left as a TODO until the dialog exists.
    session.condition = config.default_condition
    get_store().create_session(session)
    await manager.broadcast(
        schemas.WsEvent(type="session.created", payload=session.model_dump(mode="json"))
    )
    return session


@app.get("/api/sessions/{session_id}", response_model=schemas.Session)
def get_session(session_id: str) -> schemas.Session:
    session = get_store().get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    return session


@app.get(
    "/api/sessions/{session_id}/messages", response_model=list[schemas.Message]
)
def list_messages(session_id: str) -> list[schemas.Message]:
    return get_store().list_messages(session_id)


_DEBUG_SESSION_TABLES: tuple[tuple[str, str], ...] = (
    ("sessions", "id"),
    ("messages", "session_id"),
    ("tool_calls", "session_id"),
    ("parameter_changes", "session_id"),
    ("model_states", "session_id"),
    ("active_structure_contexts", "session_id"),
    ("exposed_parameters", "session_id"),
    ("variants", "session_id"),
    ("snapshots", "session_id"),
    ("locked_objects", "session_id"),
    ("study_events", "session_id"),
    ("study_sessions", "session_id"),
)

_DEBUG_STUDY_TABLES: tuple[tuple[str, str], ...] = (
    ("consent", "study_session_id"),
    ("events", "study_session_id"),
    ("agency_survey", "study_session_id"),
)


@app.get("/api/sessions/{session_id}/debug-dump")
def get_session_debug_dump(session_id: str) -> dict[str, str]:
    store = get_store()
    session = store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    generated_at = datetime.now(timezone.utc).isoformat()
    return {
        "session_id": session_id,
        "generated_at": generated_at,
        "text": _build_session_debug_dump(session_id, generated_at),
    }


def _build_session_debug_dump(session_id: str, generated_at: str) -> str:
    store = get_store()
    session = store.get_session(session_id)
    if session is None:
        return f"Session {session_id} not found."

    study_session = store.get_study_session_for_session(session_id)
    messages = store.list_messages(session_id)
    lines: list[str] = [
        "# AI Furniture Session Debug Dump",
        f"Generated at: {generated_at}",
        f"Plugin-Version: {_BACKEND_SHA} (Backend gestartet {_BACKEND_STARTED})",
        "",
        "## Session",
        _pretty_debug_json(_compact_debug_value(session.model_dump(mode="json"))),
        "",
    ]

    if study_session is not None:
        lines.extend(
            [
                "## Study Session",
                _pretty_debug_json(
                    _compact_debug_value(study_session.model_dump(mode="json"))
                ),
                "",
            ]
        )

    lines.extend(
        [
            "## Rhino Terminal / Command History",
            _rhino_command_history_excerpt(),
            "",
            "## Chat Transcript",
        ]
    )
    if messages:
        for message in messages:
            lines.extend(_format_message_for_debug(message))
            lines.append("")
    else:
        lines.append("(no messages)")
        lines.append("")

    parameter_rows = _debug_rows("parameter_changes", "session_id", session_id)
    if parameter_rows:
        lines.extend(_format_parameter_change_summary(parameter_rows))

    lines.append("## Raw Persisted Rows")
    for table, key_column in _DEBUG_SESSION_TABLES:
        rows = _debug_rows(table, key_column, session_id)
        lines.extend(_format_table_for_debug(table, rows))

    if study_session is not None:
        for table, key_column in _DEBUG_STUDY_TABLES:
            rows = _debug_rows(table, key_column, study_session.id)
            lines.extend(_format_table_for_debug(table, rows))

    return "\n".join(lines).rstrip() + "\n"


def _debug_rows(table: str, key_column: str, key_value: str) -> list[dict[str, Any]]:
    try:
        rows = get_store().rows_for_export(table, key_column, key_value)
    except Exception as e:
        logger.warning("debug dump row read failed for %s: %s", table, e)
        return [{"_error": str(e)}]
    return [_decode_debug_row(row) for row in rows]


def _decode_debug_row(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in row.items():
        if key.endswith("_json") and isinstance(value, str) and value:
            try:
                out[key] = _normalize_debug_value(json.loads(value))
                continue
            except json.JSONDecodeError:
                pass
        out[key] = _normalize_debug_value(value)
    return out


def _normalize_debug_value(value: Any, depth: int = 0) -> Any:
    if isinstance(value, dict):
        if value.get("type") == "base64" and isinstance(value.get("data"), str):
            return _compact_debug_value(value)
        return {k: _normalize_debug_value(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize_debug_value(v, depth + 1) for v in value]
    if isinstance(value, str):
        return _normalize_debug_string(value, depth)
    return _compact_debug_value(value)


def _normalize_debug_string(value: str, depth: int) -> Any:
    value = _compact_debug_value(value)
    if not isinstance(value, str) or depth > 8:
        return value

    stripped = value.strip()
    if not stripped:
        return value

    if stripped[0] in "{[\"":
        try:
            return _normalize_debug_value(json.loads(stripped), depth + 1)
        except (TypeError, json.JSONDecodeError):
            pass

    if stripped[0] in "'\"" and stripped[-1:] == stripped[0]:
        try:
            literal = ast.literal_eval(stripped)
        except (SyntaxError, ValueError):
            return value
        if literal != value:
            return _normalize_debug_value(literal, depth + 1)

    return value


def _compact_debug_value(value: Any) -> Any:
    if isinstance(value, dict):
        if value.get("type") == "base64" and isinstance(value.get("data"), str):
            data = value["data"]
            return {
                **value,
                "data": f"<base64 omitted; {len(data)} chars>",
            }
        return {k: _compact_debug_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_compact_debug_value(v) for v in value]
    if isinstance(value, str) and len(value) > 120_000:
        return value[:120_000] + f"\n<text truncated; total {len(value)} chars>"
    return value


def _pretty_debug_json(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, default=str)


def _format_message_for_debug(message: schemas.Message) -> list[str]:
    lines = [
        (
            f"### {message.created_at.isoformat()} | {message.role} | "
            f"id={message.id}"
        )
    ]
    if message.model:
        lines.append(f"model: {message.model}")
    if message.stop_reason:
        lines.append(f"stop_reason: {message.stop_reason}")
    if message.modality:
        lines.append(f"modality: {', '.join(message.modality)}")

    for index, block in enumerate(message.content, start=1):
        data = _normalize_debug_value(block.model_dump(mode="json"))
        block_type = data.get("type") if isinstance(data, dict) else "unknown"
        if block_type == "text" and isinstance(data, dict):
            lines.extend([f"[{index}] text", str(data.get("text", ""))])
        elif block_type == "tool_use" and isinstance(data, dict):
            lines.append(
                f"[{index}] tool_use {data.get('name')} id={data.get('id')}"
            )
            lines.append(_pretty_debug_json(data.get("input", {})))
        elif block_type == "tool_result" and isinstance(data, dict):
            lines.append(
                f"[{index}] tool_result id={data.get('tool_use_id')} "
                f"error={bool(data.get('is_error'))}"
            )
            lines.append(_format_debug_payload(data.get("content")))
        else:
            lines.append(f"[{index}] {block_type}")
            lines.append(_format_debug_payload(data))
    return lines


def _format_debug_payload(value: Any) -> str:
    return value if isinstance(value, str) else _pretty_debug_json(value)


def _format_parameter_change_summary(rows: list[dict[str, Any]]) -> list[str]:
    summary: dict[str, dict[str, Any]] = {}
    for row in rows:
        name = str(row.get("parameter_name") or "(unknown)")
        item = summary.setdefault(
            name,
            {
                "parameter_name": name,
                "changes": 0,
                "first_old": row.get("old_value"),
                "last_new": row.get("new_value"),
                "min_new": row.get("new_value"),
                "max_new": row.get("new_value"),
                "sources": set(),
                "first_at": row.get("created_at"),
                "last_at": row.get("created_at"),
            },
        )
        item["changes"] += 1
        item["last_new"] = row.get("new_value")
        item["last_at"] = row.get("created_at")
        source = row.get("source")
        if source:
            item["sources"].add(str(source))
        for key in ("min_new", "max_new"):
            try:
                current = float(item[key])
                candidate = float(row.get("new_value"))
            except (TypeError, ValueError):
                continue
            item[key] = (
                min(current, candidate)
                if key == "min_new"
                else max(current, candidate)
            )

    normalized = []
    for item in summary.values():
        normalized.append(
            {
                **item,
                "sources": sorted(item["sources"]),
            }
        )
    normalized.sort(key=lambda item: item["parameter_name"])
    return [
        "## Parameter Change Summary",
        _pretty_debug_json(normalized),
        "",
    ]


def _format_table_for_debug(table: str, rows: list[dict[str, Any]]) -> list[str]:
    return [
        "",
        f"### {table} ({len(rows)} row{'s' if len(rows) != 1 else ''})",
        _pretty_debug_json(rows),
    ]


def _rhino_command_history_excerpt() -> str:
    try:
        from . import rhino_exec

        source = """
try:
    history = getattr(Rhino.RhinoApp, "CommandHistoryWindowText", None)
    if callable(history):
        history = history()
    history = str(history or "")
    result = history[-20000:] if history else "Rhino command history not available from RhinoCommon."
except Exception as e:
    result = "Rhino command history unavailable: {0}".format(e)
"""
        text = rhino_exec.run_code(source, timeout=3.0)
    except Exception as e:
        text = f"Rhino command history unavailable: {e}"
    text = _normalize_debug_value(text)
    return text if isinstance(text, str) else _pretty_debug_json(text)


@app.get(
    "/api/sessions/{session_id}/parameters",
    response_model=list[schemas.ExposedParameter],
)
def list_exposed_parameters(session_id: str) -> list[schemas.ExposedParameter]:
    return get_store().get_exposed_parameters(session_id)


@app.get(
    "/api/sessions/{session_id}/structure-context",
    response_model=Optional[schemas.EditableStructureContext],
)
async def get_structure_context(
    session_id: str,
) -> Optional[schemas.EditableStructureContext]:
    active_context = get_store().get_active_structure_context(session_id)
    if active_context is None or active_context.structure_type == "grasshopper":
        return active_context

    refreshed = await sync_structure_context_after_object_change(
        session_id,
        source=active_context.source,
        clear_if_missing=True,
        refresh_active=True,
    )
    return (
        refreshed
        if isinstance(refreshed, schemas.EditableStructureContext)
        else get_store().get_active_structure_context(session_id)
    )


@app.get(
    "/api/sessions/{session_id}/variants",
    response_model=list[schemas.Variant],
)
def list_variants(session_id: str) -> list[schemas.Variant]:
    return get_store().list_variants(session_id)


@app.post("/api/sessions/{session_id}/variants/sync")
async def sync_variants(session_id: str) -> dict:
    """Restore the session's active variant into Rhino's live layer state."""
    from . import dedicated_tools

    variants = await dedicated_tools.sync_variant_state(session_id)
    active = next((v for v in variants if v.is_active), None)
    return {
        "status": "ok",
        "variants": [v.model_dump(mode="json") for v in variants],
        "active_variant_id": active.id if active else None,
    }


@app.get(
    "/api/sessions/{session_id}/locks",
    response_model=list[schemas.LockedObject],
)
def list_locks(session_id: str) -> list[schemas.LockedObject]:
    return get_store().list_locked_objects(session_id)


@app.post(
    "/api/sessions/{session_id}/locks",
    response_model=list[schemas.LockedObject],
)
async def add_locks(
    session_id: str, payload: schemas.LockObjectRequest
) -> list[schemas.LockedObject]:
    """Lock one or more Rhino objects for this session (P5 Lock-Panel).

    Idempotent on (session_id, object_id) — re-locking the same GUID
    refreshes the note/name/timestamp. Object names are optional and
    align positionally with object_ids when present.
    """
    store = get_store()
    names = payload.object_names or []
    for i, obj_id in enumerate(payload.object_ids):
        obj_id = obj_id.strip()
        if not obj_id:
            continue
        name = names[i] if i < len(names) else ""
        store.lock_object(
            schemas.LockedObject(
                session_id=session_id,
                object_id=obj_id,
                object_name=name or "",
                note=payload.note,
            )
        )
    fresh = store.list_locked_objects(session_id)
    await manager.broadcast(
        schemas.WsEvent(
            type="settings.updated",
            payload={"locks_changed": True, "session_id": session_id},
        )
    )
    return fresh


@app.delete("/api/sessions/{session_id}/locks/{object_id}")
async def remove_lock(session_id: str, object_id: str) -> dict:
    removed = get_store().unlock_object(session_id, object_id)
    await manager.broadcast(
        schemas.WsEvent(
            type="settings.updated",
            payload={"locks_changed": True, "session_id": session_id},
        )
    )
    return {"removed": removed}


@app.delete("/api/sessions/{session_id}/locks")
async def clear_locks(session_id: str) -> dict:
    n = get_store().clear_locked_objects(session_id)
    await manager.broadcast(
        schemas.WsEvent(
            type="settings.updated",
            payload={"locks_changed": True, "session_id": session_id},
        )
    )
    return {"cleared": n}


@app.post(
    "/api/sessions/{session_id}/locks/from-rhino-selection",
    response_model=list[schemas.LockedObject],
)
async def lock_from_rhino_selection(
    session_id: str,
) -> list[schemas.LockedObject]:
    """Lock whatever the user has currently selected in Rhino.

    Convenience for the Lock-Panel "Aktuelle Selektion sperren" button:
    avoids a separate pick round-trip by leaning on Rhino's existing
    selection state. Falls back gracefully when Rhino isn't reachable
    (returns the existing lock list unchanged with a 200) and when
    the selection is empty (also 200, unchanged).
    """
    from . import dedicated_tools
    from .rhino_exec import decode_run_code_result
    import re as _re

    store = get_store()
    try:
        raw = await dedicated_tools.dispatch_dedicated_tool("get_selected", {})
    except Exception as e:
        logger.warning("get_selected via lock-from-rhino failed: %s", e)
        return store.list_locked_objects(session_id)

    # get_selected returns run_code's repr-gewrappte JSON-Ausgabe (result =
    # json.dumps({"count": ..., "objects": [...]})). Ein direktes json.loads
    # scheiterte an der repr-Huelle (Single-Quotes); decode_run_code_result
    # zieht sie erst ab. Auf Unparsbares faellt payload auf {"_raw": ...}
    # zurueck, damit _walk unten leer laeuft statt zu werfen.
    payload: Any = raw
    if isinstance(raw, str):
        decoded = decode_run_code_result(raw)
        payload = decoded if isinstance(decoded, (dict, list)) else {"_raw": raw}

    # Defensive: schema isn't perfectly stable across versions.
    # Look for any "object_id" / "id" key in the structure and
    # collect GUID-shaped strings.
    guid_re = _re.compile(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
        _re.IGNORECASE,
    )
    found_pairs: list[tuple[str, str]] = []  # (object_id, name)

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            obj_id = (
                node.get("object_id")
                or node.get("id")
                or node.get("guid")
                or ""
            )
            if isinstance(obj_id, str) and guid_re.match(obj_id):
                name = str(node.get("name") or node.get("object_name") or "")
                found_pairs.append((obj_id, name))
            for v in node.values():
                _walk(v)
        elif isinstance(node, list):
            for it in node:
                _walk(it)

    _walk(payload)

    # Dedupe while preserving order
    seen: set[str] = set()
    unique: list[tuple[str, str]] = []
    for pair in found_pairs:
        if pair[0] in seen:
            continue
        seen.add(pair[0])
        unique.append(pair)

    for obj_id, name in unique:
        store.lock_object(
            schemas.LockedObject(
                session_id=session_id,
                object_id=obj_id,
                object_name=name,
                note="",
            )
        )
    if unique:
        await manager.broadcast(
            schemas.WsEvent(
                type="settings.updated",
                payload={"locks_changed": True, "session_id": session_id},
            )
        )
    return store.list_locked_objects(session_id)


@app.post("/api/sessions/{session_id}/messages", response_model=schemas.Message)
async def append_message(
    session_id: str, message: schemas.Message
) -> schemas.Message:
    if message.session_id != session_id:
        message.session_id = session_id
    # Same consent gate as the WS chat.send path — applies to MCP-bridge
    # writes that come in via REST. User-role messages are gated;
    # assistant/tool messages from the bridge are always let through so
    # an in-flight turn doesn't get torn in half by a consent timing race.
    if message.role == "user":
        consent_block = _study_consent_blocks_chat(session_id)
        if consent_block is not None:
            raise HTTPException(status_code=403, detail=consent_block)
    get_store().add_message(message)
    await manager.broadcast(
        schemas.WsEvent(type="message.new", payload=message.model_dump(mode="json"))
    )
    return message


@app.post(
    "/api/sessions/{session_id}/inspect",
    response_model=schemas.InspectionResult,
)
def inspect_session(
    session_id: str,
    request: schemas.InspectRequest,
) -> schemas.InspectionResult:
    return build_inspection_result(session_id, request)


# ---------------------------------------------------------------------------
# Study events
# ---------------------------------------------------------------------------


@app.post("/api/study/events", response_model=schemas.StudyEvent)
async def log_study_event(event: schemas.StudyEvent) -> schemas.StudyEvent:
    if not config.is_study_mode:
        return event
    if not event.participant_id:
        event.participant_id = config.participant_id or None
    get_store().log_event(event)
    await manager.broadcast(
        schemas.WsEvent(type="study.event_logged", payload=event.model_dump(mode="json"))
    )
    return event


# ---------------------------------------------------------------------------
# Study-mode shell (Studienartefakt-Spec §2)
# ---------------------------------------------------------------------------


@app.get("/api/study/consent-text", response_model=schemas.ConsentTextResponse)
def get_consent_text() -> schemas.ConsentTextResponse:
    """Serve the consent body, its hash, and the checkbox labels.

    Available regardless of study mode so the frontend can prefetch /
    preview; the gate that *requires* consent only applies to chat sends
    once study mode is on (see ``_require_consent_or_block``).
    """
    bundle = load_consent_text()
    return schemas.ConsentTextResponse(
        text=bundle.text,
        text_hash=bundle.text_hash,
        checkbox_labels=CONSENT_CHECKBOX_LABELS,
    )


_STUDY_CONDITION_ORDER = ("basis", "werkzeug")


def _consent_checkboxes_complete(checkboxes: dict[str, Any]) -> bool:
    expected = [f"cb{i + 1}" for i in range(len(CONSENT_CHECKBOX_LABELS))]
    return bool(expected) and all(checkboxes.get(key) is True for key in expected)


def _participant_status(
    participant_code: str,
    is_pilot: bool = False,
) -> schemas.StudyParticipantStatus:
    """Build the pre-session availability view for one participant code."""
    # Kuerzel case-insensitiv + whitespace-robust normalisieren (kanonisch
    # GROSS). Sonst wuerden 'PILOT1' und 'pilot1' zwei getrennte Lanes bilden:
    # Counterbalancing verknuepft die zwei Laeufe nicht mehr und die Demografie
    # wird erneut abgefragt. Ein Fix an dieser + der Create-Stelle reicht, weil
    # alle Downstream-Lookups (Consent/Demografie/Counterbalancing) den im
    # study_session gespeicherten (normalisierten) Code verwenden.
    code = participant_code.strip().upper()
    store = get_store()
    runs = store.list_study_sessions_for_participant(code) if code else []
    lane_runs = [
        run
        for run in runs
        if run.is_pilot == is_pilot and run.status != "aborted"
    ]
    used_conditions = []
    if not is_pilot:
        for condition in _STUDY_CONDITION_ORDER:
            if any(run.condition == condition for run in lane_runs):
                used_conditions.append(condition)

    available_conditions = (
        list(_STUDY_CONDITION_ORDER)
        if is_pilot
        else [
            condition
            for condition in _STUDY_CONDITION_ORDER
            if condition not in used_conditions
        ]
    )
    # Aufgabenvariante parallel zur Bedingung balancieren (Counterbalancing §2.1):
    # im zweiten Lauf ist die genutzte Aufgabe "used", die andere bleibt "available".
    used_task_variants = []
    if not is_pilot:
        for tv in ("A", "B"):
            if any(run.task_variant == tv for run in lane_runs):
                used_task_variants.append(tv)
    available_task_variants = (
        ["A", "B"]
        if is_pilot
        else [tv for tv in ("A", "B") if tv not in used_task_variants]
    )
    current_consent_hash = load_consent_text().text_hash
    reusable = store.latest_reusable_consent_for_participant(
        code,
        is_pilot,
        current_consent_hash,
    )

    return schemas.StudyParticipantStatus(
        participant_code=code,
        is_pilot=is_pilot,
        used_conditions=used_conditions,
        available_conditions=available_conditions,
        used_task_variants=used_task_variants,
        available_task_variants=available_task_variants,
        next_order_index=1 if is_pilot else min(len(used_conditions) + 1, 2),
        has_reusable_consent=(
            reusable is not None and _consent_checkboxes_complete(reusable.checkboxes)
        ),
        has_demographics=(
            store.demographics_for_participant(code, is_pilot) is not None
            if code
            else False
        ),
        active_run_open=(
            not is_pilot and any(run.status == "active" for run in lane_runs)
        ),
        runs=[
            schemas.StudyParticipantRun(
                study_session_id=run.id,
                session_id=run.session_id,
                condition=run.condition,
                task_variant=run.task_variant,
                is_pilot=run.is_pilot,
                order_index=run.order_index,
                status=run.status,
                created_at=run.created_at,
                ended_at=run.ended_at,
            )
            for run in lane_runs
        ],
    )


@app.get(
    "/api/study/participants/{participant_code}/status",
    response_model=schemas.StudyParticipantStatus,
)
def get_study_participant_status(
    participant_code: str,
    is_pilot: bool = False,
) -> schemas.StudyParticipantStatus:
    """Return which conditions are still available for a participant code."""
    return _participant_status(participant_code, is_pilot=is_pilot)


def _copy_reusable_consent_if_available(
    study_session: schemas.StudySession,
) -> Optional[schemas.Consent]:
    """Copy current-text consent from a prior run of the same participant.

    Consent remains per study_session for export/audit completeness, but the
    participant should not have to confirm the same text twice during the
    within-subjects pair.
    """
    store = get_store()
    current = load_consent_text()
    source = store.latest_reusable_consent_for_participant(
        study_session.participant_code,
        study_session.is_pilot,
        current.text_hash,
    )
    if source is None or not _consent_checkboxes_complete(source.checkboxes):
        return None
    consent = schemas.Consent(
        study_session_id=study_session.id,
        text_hash=source.text_hash,
        checkboxes=dict(source.checkboxes),
    )
    store.record_consent(consent)
    store.log_study_context_event(
        schemas.StudyContextEvent(
            study_session_id=study_session.id,
            event_type="consent_given",
            payload={
                "text_hash": consent.text_hash,
                "checkboxes": consent.checkboxes,
                "reused": True,
                "source_study_session_id": source.study_session_id,
            },
        )
    )
    return consent


@app.post("/api/study/sessions", response_model=schemas.StudySession)
async def create_study_session(
    payload: schemas.StudySessionCreate,
) -> schemas.StudySession:
    """Create one study run (§2.1) plus its backing plugin session.

    Pre-session dialog hands us participant_code + condition + variant +
    setting + is_pilot. The server derives ``order_index`` (1 for the
    first run with that participant_code, 2 for the second, capped to 2
    so a misconfigured form can't snowball), creates the plugin session
    with the locked-in condition, and links the two via the
    ``study_sessions.session_id`` UNIQUE FK.
    """
    if not config.is_study_mode:
        raise HTTPException(
            status_code=409,
            detail="Studienmodus ist nicht aktiv — Pre-Session-Dialog nicht erlaubt.",
        )
    # Spec §3.5: Studienmodus weigert sich, im MCP-Modus zu starten.
    # MCP routet alle Chats über Claude Code; im Studienartefakt soll
    # das Plugin direkt mit der Anthropic-API sprechen, damit Modell-ID,
    # System-Prompt-Hash und Tools-Liste in manifest.json fest verankert
    # sind. Lokales Modell (litellm) gilt für die Studie nicht als API.
    if config.backend_mode != "api":
        raise HTTPException(
            status_code=409,
            detail=(
                "Studienmodus benötigt Backend-Modus 'api' (aktuell: "
                f"'{config.backend_mode}'). Bitte in den Einstellungen "
                "auf 'Direkter API-Aufruf' umstellen, bevor eine "
                "Studien-Session angelegt wird."
            ),
        )
    if config.is_local_model:
        raise HTTPException(
            status_code=409,
            detail=(
                "Studienmodus benötigt ein fixiertes Anthropic-Modell, "
                "nicht 'local'. Bitte in den Einstellungen ein "
                "Anthropic-Modell wählen, bevor eine Studien-Session "
                "angelegt wird."
            ),
        )
    if not (config.api_key or "").strip():
        raise HTTPException(
            status_code=409,
            detail=(
                "Studienmodus benötigt einen hinterlegten Anthropic-API-Key. "
                "Bitte in den Einstellungen setzen, bevor eine "
                "Studien-Session angelegt wird."
            ),
        )

    # Kanonisch normalisieren (siehe _participant_status): so werden
    # 'PILOT1' / 'pilot1' / ' pilot1 ' als DERSELBE Teilnehmer gespeichert.
    participant_code = payload.participant_code.strip().upper()
    if not participant_code:
        raise HTTPException(status_code=400, detail="Teilnehmer-Kuerzel fehlt.")

    store = get_store()
    participant_status = _participant_status(
        participant_code,
        is_pilot=payload.is_pilot,
    )
    if not payload.is_pilot and participant_status.active_run_open:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Fuer Teilnehmer-Kuerzel '{participant_code}' ist noch ein "
                "Studienlauf aktiv. Bitte diesen Lauf erst beenden oder "
                "abbrechen, bevor der zweite Lauf gestartet wird."
            ),
        )
    if (
        not payload.is_pilot
        and payload.condition in participant_status.used_conditions
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                f"Teilnehmer-Kuerzel '{participant_code}' hat die Bedingung "
                f"'{payload.condition}' bereits durchlaufen. Bitte die andere "
                "Bedingung waehlen oder den Testlauf als Pilot markieren."
            ),
        )
    if (
        not payload.is_pilot
        and payload.task_variant in participant_status.used_task_variants
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                f"Teilnehmer-Kuerzel '{participant_code}' hat die Aufgabe "
                f"'{payload.task_variant}' bereits bearbeitet. Bitte die andere "
                "Aufgabe waehlen oder den Testlauf als Pilot markieren."
            ),
        )
    existing_count = 0 if payload.is_pilot else len(participant_status.used_conditions)
    if existing_count >= 2:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Teilnehmer-Kürzel '{payload.participant_code}' hat bereits "
                f"{existing_count} Studien-Sessions. Within-Subjects-Design "
                "erwartet maximal zwei (Bedingung 1 und 2)."
            ),
        )
    if payload.is_pilot:
        # Pilots sind bewusst von der Visit-Zaehlung + den Locks ausgenommen
        # (beliebig wiederholbar), brauchen aber trotzdem eine ECHTE
        # Reihenfolge: der Vergleichsblock (V1/V2) kodiert "erste/zweite
        # Bedingung" relativ zur Bearbeitungsreihenfolge, und die ist nur
        # ueber order_index rekonstruierbar. Pilot-Befund 01.07.2026: alle
        # vier Pilot-Laeufe trugen order_index 1 -> Richtung der V2-Antworten
        # nur noch ueber Timestamps zuzuordnen. Pilots zaehlen deshalb ihre
        # eigene Lane fortlaufend (1..n).
        prior_pilot_runs = [
            r
            for r in store.list_study_sessions_for_participant(participant_code)
            if r.is_pilot and r.status != "aborted"
        ]
        order_index = len(prior_pilot_runs) + 1
    else:
        order_index = existing_count + 1

    # Auto-title falls back to participant + order/pilot + condition so the
    # sidebar surfaces the run identity at a glance.
    if payload.title:
        title = payload.title
    elif payload.is_pilot:
        title = f"{participant_code} - Pilot ({payload.condition})"
    else:
        title = f"{participant_code} - Lauf {order_index} ({payload.condition})"
    plugin_session = schemas.Session(
        title=title,
        use_mode="study",
        participant_id=participant_code,
        condition=payload.condition,
    )
    store.create_session(plugin_session)

    study_session = schemas.StudySession(
        session_id=plugin_session.id,
        participant_code=participant_code,
        condition=payload.condition,
        task_variant=payload.task_variant,
        setting=payload.setting,
        is_pilot=payload.is_pilot,
        order_index=order_index,
    )
    store.create_study_session(study_session)
    _copy_reusable_consent_if_available(study_session)
    store.log_study_context_event(
        schemas.StudyContextEvent(
            study_session_id=study_session.id,
            event_type="study_session_started",
            payload={
                "participant_code": participant_code,
                "condition": payload.condition,
                "task_variant": payload.task_variant,
                "setting": payload.setting,
                "is_pilot": payload.is_pilot,
                "order_index": order_index,
                "session_id": plugin_session.id,
            },
        )
    )
    await manager.broadcast(
        schemas.WsEvent(
            type="session.created",
            payload=plugin_session.model_dump(mode="json"),
        )
    )
    return study_session


@app.get(
    "/api/study/sessions/by-session/{session_id}",
    response_model=Optional[schemas.StudySession],
)
def get_study_session_by_session(
    session_id: str,
) -> Optional[schemas.StudySession]:
    """Reverse lookup: from plugin session_id → study_session row.

    Returns ``None`` for dev sessions that have no study row, so the
    frontend's consent gate can distinguish "no study run" from "study
    run, no consent yet". 200 + null body, not 404.
    """
    return get_store().get_study_session_for_session(session_id)


@app.post(
    "/api/study/sessions/{study_session_id}/consent",
    response_model=schemas.Consent,
)
async def submit_consent(
    study_session_id: str, payload: schemas.ConsentSubmission
) -> schemas.Consent:
    """Persist participant's consent confirmation (§2.2)."""
    if payload.study_session_id != study_session_id:
        raise HTTPException(
            status_code=400,
            detail="study_session_id im Pfad und Body müssen übereinstimmen.",
        )
    store = get_store()
    if store.get_study_session(study_session_id) is None:
        raise HTTPException(status_code=404, detail="Studien-Session nicht gefunden.")

    current = load_consent_text()
    if payload.text_hash != current.text_hash:
        # Wording drifted between rendering and submission — refuse so
        # the participant sees the current text and confirms it explicitly.
        raise HTTPException(
            status_code=409,
            detail=(
                "Einwilligungstext wurde zwischenzeitlich geändert. "
                "Bitte Dialog erneut öffnen."
            ),
        )

    if not _consent_checkboxes_complete(payload.checkboxes):
        raise HTTPException(
            status_code=400,
            detail="Bitte alle Einwilligungs-Checkboxen bestaetigen.",
        )

    consent = schemas.Consent(
        study_session_id=study_session_id,
        text_hash=payload.text_hash,
        checkboxes=payload.checkboxes,
    )
    store.record_consent(consent)
    store.log_study_context_event(
        schemas.StudyContextEvent(
            study_session_id=study_session_id,
            event_type="consent_given",
            payload={
                "text_hash": payload.text_hash,
                "checkboxes": payload.checkboxes,
            },
        )
    )
    await manager.broadcast(
        schemas.WsEvent(
            type="study.event_logged",
            payload={
                "event_type": "consent_given",
                "study_session_id": study_session_id,
            },
        )
    )
    return consent


@app.get(
    "/api/study/sessions/{study_session_id}/consent",
    response_model=Optional[schemas.Consent],
)
def get_latest_consent(study_session_id: str) -> Optional[schemas.Consent]:
    return get_store().latest_consent_for_study_session(study_session_id)


@app.get("/api/study/agency-items", response_model=schemas.AgencyItemsResponse)
def get_agency_items() -> schemas.AgencyItemsResponse:
    bundle = load_agency_items()
    return schemas.AgencyItemsResponse(
        scale=schemas.AgencyScaleModel(
            min=bundle.scale.min,
            max=bundle.scale.max,
            anchor_min=bundle.scale.anchor_min,
            anchor_max=bundle.scale.anchor_max,
        ),
        items=[
            schemas.AgencyItemModel(id=i.id, dimension=i.dimension, label=i.label)
            for i in bundle.items
        ],
        note=bundle.note,
    )


# ----- Lean-Fragebogen (fragebogen-spec v1.2.7) ---------------------------


def _werkzeug_feature_gates(bundle: dict) -> dict[str, bool]:
    """Feature-Gates für optionale Werkzeug-Items (Spec §4.3).

    Varianten werden gegen die tatsächliche Tool-Surface geprüft;
    Sketch ist eine UI-Affordanz ohne LLM-Tool und hängt am Flag in
    ``fragebogen_items.json``. (Das Lock-Gate wurde am 11.06.2026
    zusammen mit dem Lock-Feature entfernt. Das Measure-Gate entfiel
    am 15.06.2026, als Measure aus dem Studienumfang entfernt wurde.)
    """
    try:
        names = {t.get("name") for t in build_tool_list("werkzeug")}
    except Exception:  # pragma: no cover — Tool-Registry darf Survey nie killen
        logger.exception("build_tool_list für Survey-Gating fehlgeschlagen")
        names = set()
    flags = bundle.get("per_condition", {}).get("feature_flags", {})
    return {
        "feature_variants": "create_variant" in names,
        "feature_sketch": bool(flags.get("feature_sketch", True)),
    }


def _visible_tool_items(
    bundle: dict,
    condition: str,
    *,
    image_uploaded: bool,
    slider_used: bool,
) -> list[schemas.SurveyToolItem]:
    """Werkzeug-Items, die in diesem Lauf sichtbar sind (Spec §4.3).

    Nicht verfügbare Items werden ausgeblendet, nicht ausgegraut —
    konsistent mit Spec §1.3 (Tools in basis komplett unsichtbar).
    """
    gates = _werkzeug_feature_gates(bundle)
    visible: list[schemas.SurveyToolItem] = []
    for raw in bundle.get("per_condition", {}).get("tool_items", []):
        if condition not in raw.get("conditions", []):
            continue
        gate = raw.get("gate")
        if gate == "image_uploaded" and not image_uploaded:
            continue
        if gate == "slider_used" and not slider_used:
            continue
        if gate and gate.startswith("feature_") and not gates.get(gate, False):
            continue
        visible.append(
            schemas.SurveyToolItem(
                id=str(raw["id"]),
                dimension=str(raw.get("dimension", "")),
                label=str(raw.get("label", "")),
            )
        )
    return visible


def _is_final_lane_run(
    study: schemas.StudySession,
) -> bool:
    """True, wenn dieser Lauf mindestens der zweite nicht-abgebrochene
    Lauf seiner Teilnehmenden-Lane (participant_code + is_pilot) ist.

    Bewusst über die Lane-Zählung statt über ``order_index``: historisch
    bekamen Pilot-Läufe bei der Anlage immer order_index 1 (seit 02.07.2026
    zählen sie ihre Lane fortlaufend), und die Lane-Zählung bleibt auch
    gegenüber abgebrochenen/wiederholten Läufen robust — der Vergleichsblock
    muss im Pilot testbar sein (fragebogen-spec §1.2/§7.1).
    """
    store = get_store()
    runs = store.list_study_sessions_for_participant(study.participant_code)
    others = [
        r
        for r in runs
        if r.is_pilot == study.is_pilot
        and r.status != "aborted"
        and r.id != study.id
    ]
    return len(others) >= 1


@app.get(
    "/api/study/sessions/{study_session_id}/survey-config",
    response_model=schemas.SurveyConfigResponse,
)
def get_survey_config(study_session_id: str) -> schemas.SurveyConfigResponse:
    """Per-Bedingungs-Survey, fertig gegated für genau diesen Lauf."""
    store = get_store()
    study = store.get_study_session(study_session_id)
    if study is None:
        raise HTTPException(status_code=404, detail="Studien-Session nicht gefunden.")
    bundle = load_fragebogen()
    per = bundle.get("per_condition", {})
    scale_raw = bundle.get("scale_likert7", {})
    agency = load_agency_items()
    image_uploaded = store.session_has_image_upload(study.session_id)
    slider_used = store.session_user_slider_count(study.session_id) > 0
    tool_items = _visible_tool_items(
        bundle,
        study.condition,
        image_uploaded=image_uploaded,
        slider_used=slider_used,
    )
    # W-OPEN1 fragt nach kaum/nicht genutzten Werkzeugen — die Optionen
    # dürfen daher NUR bedingungs-/feature-gegated sein, nicht
    # nutzungs-gegated (Spec §5.2.4: "dynamisch aus den in der Bedingung
    # aktiven Werkzeugen"). Sonst fehlen Slider/Referenzbild genau dann,
    # wenn sie nicht genutzt wurden.
    unused_pool = _visible_tool_items(
        bundle,
        study.condition,
        image_uploaded=True,
        slider_used=True,
    )
    unused_q = per.get("unused_question", {})
    is_werkzeug = study.condition == "werkzeug"
    reflection = per.get("reflection", {})
    return schemas.SurveyConfigResponse(
        study_session_id=study.id,
        condition=study.condition,
        fragebogen_version_hash=fragebogen_version_hash(),
        intro=str(per.get("intro", "")),
        scale=schemas.AgencyScaleModel(
            min=int(scale_raw.get("min", 1)),
            max=int(scale_raw.get("max", 7)),
            anchor_min=str(scale_raw.get("anchor_min", "")),
            anchor_max=str(scale_raw.get("anchor_max", "")),
        ),
        csi_items=[
            schemas.SurveyLikertItem(
                id=str(i["id"]),
                dimension=str(i.get("dimension", "")),
                label=str(i.get("label", "")),
            )
            for i in per.get("csi_items", [])
        ],
        agency_items=[
            schemas.SurveyLikertItem(id=i.id, dimension=i.dimension, label=i.label)
            for i in agency.items
        ],
        collaboration_items=[
            schemas.SurveyLikertItem(
                id=str(i["id"]),
                dimension=str(i.get("dimension", "")),
                label=str(i.get("label", "")),
            )
            for i in per.get("collaboration_items", [])
        ],
        competence_items=[
            schemas.SurveyLikertItem(
                id=str(i["id"]),
                dimension=str(i.get("dimension", "")),
                label=str(i.get("label", "")),
            )
            for i in per.get("competence_items", [])
        ],
        tool_items=tool_items,
        tool_not_used_label=str(
            per.get("tool_not_used_label", "habe ich nicht genutzt")
        ),
        unused_question_label=str(
            unused_q.get("werkzeug_label" if is_werkzeug else "basis_label", "")
        ),
        unused_options=unused_pool if is_werkzeug else [],
        reflection_label=str(reflection.get("label", "")),
        reflection_max_chars=int(reflection.get("max_chars", 500)),
        is_final_run=_is_final_lane_run(study),
        note=str(bundle.get("_note", "")),
    )


@app.post(
    "/api/study/sessions/{study_session_id}/survey",
    response_model=schemas.AgencySurveyRecord,
)
async def submit_agency_survey(
    study_session_id: str, payload: schemas.AgencySurveyResponse
) -> schemas.AgencySurveyRecord:
    """Persist the per-condition survey block for one run (§2.5).

    Trägt seit dem Lean-Fragebogen den gesamten Per-Bedingungs-Block
    (Mini-CSI, Agency, Werkzeug-Items, W-OPEN1, R1) — Tabelle heißt aus
    historischen Gründen weiterhin ``agency_survey``.
    """
    store = get_store()
    study = store.get_study_session(study_session_id)
    if study is None:
        raise HTTPException(status_code=404, detail="Studien-Session nicht gefunden.")
    record = schemas.AgencySurveyRecord(
        study_session_id=study_session_id,
        condition=study.condition,
        fragebogen_version_hash=(
            payload.fragebogen_version_hash or fragebogen_version_hash()
        ),
        **payload.model_dump(exclude={"fragebogen_version_hash"}),
    )
    store.record_agency_survey(record)
    # Survey-Marker gemäß fragebogen-spec §2.4 (payload = Block-Identifier,
    # nicht die Antworten selbst — die liegen in agency_survey).
    event = schemas.StudyContextEvent(
        study_session_id=study_session_id,
        event_type="survey_submitted",
        payload={
            "block": "per_condition",
            "condition": study.condition,
            "fragebogen_version_hash": record.fragebogen_version_hash,
            "has_reflection": bool(record.reflection_freetext.strip()),
        },
    )
    store.log_study_context_event(event)
    await manager.broadcast(
        schemas.WsEvent(
            type="study.event_logged",
            payload={
                "event_type": "survey_submitted",
                "study_session_id": study_session_id,
            },
        )
    )
    return record


@app.get(
    "/api/study/sessions/{study_session_id}/survey",
    response_model=Optional[schemas.AgencySurveyRecord],
)
def get_latest_agency_survey(
    study_session_id: str,
) -> Optional[schemas.AgencySurveyRecord]:
    """Letzte Per-Bedingungs-Antwort dieses Laufs, ``null`` falls offen.

    Re-Entry-Guard fürs Frontend: wurde der Block bereits beantwortet
    (z.B. Vergleichsblock versehentlich geschlossen, Stop erneut
    gedrückt), springt der Wizard direkt zum Vergleichsblock statt den
    Per-Bedingungs-Block doppelt zu stellen.
    """
    store = get_store()
    if store.get_study_session(study_session_id) is None:
        raise HTTPException(status_code=404, detail="Studien-Session nicht gefunden.")
    return store.latest_agency_survey(study_session_id)


@app.get(
    "/api/study/demographics-config",
    response_model=schemas.DemographicsConfigResponse,
)
def get_demographics_config() -> schemas.DemographicsConfigResponse:
    """Felddefinitionen des Demografie-Blocks D1–D11 (Spec §5.1)."""
    bundle = load_fragebogen()
    demo = bundle.get("demographics", {})
    fields: list[schemas.DemographicsField] = []
    for raw in demo.get("fields", []):
        fields.append(
            schemas.DemographicsField(
                id=str(raw["id"]),
                label=str(raw.get("label", "")),
                kind=str(raw.get("kind", "text")),
                options=[str(o) for o in raw.get("options", [])],
                allow_other=bool(raw.get("allow_other", False)),
                placeholder=str(raw.get("placeholder", "")),
                scale_min=raw.get("scale_min"),
                scale_max=raw.get("scale_max"),
                anchor_min=str(raw.get("anchor_min", "")),
                anchor_max=str(raw.get("anchor_max", "")),
            )
        )
    return schemas.DemographicsConfigResponse(
        fragebogen_version_hash=fragebogen_version_hash(),
        intro=str(demo.get("intro", "")),
        fields=fields,
    )


@app.get(
    "/api/study/participants/{participant_code}/demographics",
    response_model=Optional[schemas.DemographicsRecord],
)
def get_participant_demographics(
    participant_code: str, is_pilot: bool = False
) -> Optional[schemas.DemographicsRecord]:
    """Demografie-Datensatz einer Teilnehmenden-Lane, ``null`` falls offen."""
    return get_store().demographics_for_participant(participant_code, is_pilot)


@app.post(
    "/api/study/sessions/{study_session_id}/demographics",
    response_model=schemas.DemographicsRecord,
)
async def submit_demographics(
    study_session_id: str, payload: schemas.DemographicsSubmission
) -> schemas.DemographicsRecord:
    """Persist den Demografie-Block (einmal pro Teilnehmenden-Lane)."""
    store = get_store()
    study = store.get_study_session(study_session_id)
    if study is None:
        raise HTTPException(status_code=404, detail="Studien-Session nicht gefunden.")
    existing = store.demographics_for_participant(
        study.participant_code, study.is_pilot
    )
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail="Demografie wurde für diesen Teilnehmenden-Code bereits erfasst.",
        )
    record = schemas.DemographicsRecord(
        study_session_id=study_session_id,
        participant_code=study.participant_code,
        is_pilot=study.is_pilot,
        **{
            **payload.model_dump(),
            "fragebogen_version_hash": (
                payload.fragebogen_version_hash or fragebogen_version_hash()
            ),
        },
    )
    store.record_demographics(record)
    event = schemas.StudyContextEvent(
        study_session_id=study_session_id,
        event_type="demographics_submitted",
        payload={"block": "demographics"},
    )
    store.log_study_context_event(event)
    await manager.broadcast(
        schemas.WsEvent(
            type="study.event_logged",
            payload={
                "event_type": "demographics_submitted",
                "study_session_id": study_session_id,
            },
        )
    )
    return record


@app.get(
    "/api/study/sessions/{study_session_id}/final-survey-config",
    response_model=schemas.FinalSurveyConfigResponse,
)
def get_final_survey_config(
    study_session_id: str,
) -> schemas.FinalSurveyConfigResponse:
    """Vergleichsblock V1–V4 + Schlussfragen S1–S3 (Spec §5.3/§5.4)."""
    store = get_store()
    study = store.get_study_session(study_session_id)
    if study is None:
        raise HTTPException(status_code=404, detail="Studien-Session nicht gefunden.")
    bundle = load_fragebogen()
    final = bundle.get("final", {})
    # Ranking-Optionen: nur Werkzeuge, die im werkzeug-Lauf dieser
    # Teilnehmenden tatsächlich sichtbar waren (gleiche Gating-Logik wie
    # der Per-Bedingungs-Block, Spec §5.3.3).
    runs = store.list_study_sessions_for_participant(study.participant_code)
    werkzeug_run = next(
        (
            r
            for r in runs
            if r.is_pilot == study.is_pilot
            and r.condition == "werkzeug"
            and r.status != "aborted"
        ),
        None,
    )
    if werkzeug_run is not None:
        ranking_options = _visible_tool_items(
            bundle,
            "werkzeug",
            image_uploaded=store.session_has_image_upload(werkzeug_run.session_id),
            slider_used=store.session_user_slider_count(werkzeug_run.session_id) > 0,
        )
    else:
        ranking_options = []
    preference = final.get("preference", {})
    v2 = final.get("v2", {})
    v2_scale = v2.get("scale", {})
    ranking = final.get("ranking", {})
    hybrid = final.get("hybrid", {})
    return schemas.FinalSurveyConfigResponse(
        study_session_id=study.id,
        fragebogen_version_hash=fragebogen_version_hash(),
        intro=str(final.get("intro", "")),
        preference_label=str(preference.get("label", "")),
        preference_options=[
            schemas.DemographicsFieldOption(
                id=str(o.get("id", "")), label=str(o.get("label", ""))
            )
            for o in preference.get("options", [])
        ],
        preference_why_label=str(preference.get("why_label", "")),
        preference_why_max_chars=int(preference.get("why_max_chars", 300)),
        v2_label=str(v2.get("label", "")),
        v2_scale=schemas.FinalSurveyV2Scale(
            min=int(v2_scale.get("min", -2)),
            max=int(v2_scale.get("max", 2)),
            anchor_min=str(v2_scale.get("anchor_min", "")),
            anchor_mid=str(v2_scale.get("anchor_mid", "")),
            anchor_max=str(v2_scale.get("anchor_max", "")),
        ),
        v2_dimensions=[
            schemas.DemographicsFieldOption(
                id=str(d.get("id", "")), label=str(d.get("label", ""))
            )
            for d in v2.get("dimensions", [])
        ],
        ranking_label=str(ranking.get("label", "")),
        ranking_max_rank=int(ranking.get("max_rank", 3)),
        ranking_options=ranking_options,
        hybrid_label=str(hybrid.get("label", "")),
        hybrid_max_chars=int(hybrid.get("max_chars", 500)),
        open_questions=[
            schemas.FinalSurveyOpenQuestion(
                id=str(q.get("id", "")),
                label=str(q.get("label", "")),
                max_chars=int(q.get("max_chars", 500)),
            )
            for q in final.get("open_questions", [])
        ],
    )


@app.get(
    "/api/study/sessions/{study_session_id}/final-survey",
    response_model=Optional[schemas.FinalSurveyRecord],
)
def get_latest_final_survey(
    study_session_id: str,
) -> Optional[schemas.FinalSurveyRecord]:
    """Letzter Vergleichsblock dieses Laufs, ``null`` falls offen.

    Re-Entry-Guard fürs Frontend (analog zum Per-Bedingungs-GET): war
    der Block schon abgeschickt und nur Ende/Export sind gescheitert,
    holt der Dialog die Restschritte nach statt erneut zu fragen.
    """
    store = get_store()
    if store.get_study_session(study_session_id) is None:
        raise HTTPException(status_code=404, detail="Studien-Session nicht gefunden.")
    return store.latest_final_survey(study_session_id)


@app.post(
    "/api/study/sessions/{study_session_id}/final-survey",
    response_model=schemas.FinalSurveyRecord,
)
async def submit_final_survey(
    study_session_id: str, payload: schemas.FinalSurveySubmission
) -> schemas.FinalSurveyRecord:
    """Persist den Vergleichsblock (einmal pro Termin, am letzten Lauf)."""
    store = get_store()
    study = store.get_study_session(study_session_id)
    if study is None:
        raise HTTPException(status_code=404, detail="Studien-Session nicht gefunden.")
    record = schemas.FinalSurveyRecord(
        study_session_id=study_session_id,
        **{
            **payload.model_dump(),
            "fragebogen_version_hash": (
                payload.fragebogen_version_hash or fragebogen_version_hash()
            ),
        },
    )
    store.record_final_survey(record)
    event = schemas.StudyContextEvent(
        study_session_id=study_session_id,
        event_type="final_survey_submitted",
        payload={
            "block": "final",
            "fragebogen_version_hash": record.fragebogen_version_hash,
        },
    )
    store.log_study_context_event(event)
    await manager.broadcast(
        schemas.WsEvent(
            type="study.event_logged",
            payload={
                "event_type": "final_survey_submitted",
                "study_session_id": study_session_id,
            },
        )
    )
    return record


@app.post(
    "/api/study/sessions/{study_session_id}/end",
    response_model=schemas.StudyContextEvent,
)
async def end_study_session(study_session_id: str) -> schemas.StudyContextEvent:
    """Mark a study run as finished (§2.7).

    Doesn't tear down the plugin session — keeps the data around so the
    researcher can still export it. Just logs the marker so downstream
    analysis can bracket the run.
    """
    store = get_store()
    study = store.get_study_session(study_session_id)
    if study is None:
        raise HTTPException(status_code=404, detail="Studien-Session nicht gefunden.")
    if study.status == "aborted":
        raise HTTPException(
            status_code=409,
            detail="Studien-Session wurde bereits abgebrochen.",
        )
    if study.status == "active":
        store.set_study_session_status(study_session_id, "completed")
    event = schemas.StudyContextEvent(
        study_session_id=study_session_id,
        event_type="study_session_ended",
        payload={"status": "completed"},
    )
    store.log_study_context_event(event)
    await manager.broadcast(
        schemas.WsEvent(
            type="study.event_logged",
            payload=event.model_dump(mode="json"),
        )
    )
    return event


@app.post(
    "/api/study/sessions/{study_session_id}/abort",
    response_model=schemas.StudyContextEvent,
)
async def abort_study_session(
    study_session_id: str, payload: Optional[dict] = None
) -> schemas.StudyContextEvent:
    """Abort a study run without survey/export.

    Used for researcher-controlled cancellation before consent, technical
    failures, participant withdrawal, or task aborts. The data stays in the
    DB/exportable for audit, but the run is no longer treated as active after
    restart.
    """
    store = get_store()
    study = store.get_study_session(study_session_id)
    if study is None:
        raise HTTPException(status_code=404, detail="Studien-Session nicht gefunden.")
    if study.status == "completed":
        raise HTTPException(
            status_code=409,
            detail="Studien-Session wurde bereits regulär beendet.",
        )
    if study.status == "active":
        store.set_study_session_status(study_session_id, "aborted")

    event_payload = dict(payload or {})
    event_payload["status"] = "aborted"
    event = schemas.StudyContextEvent(
        study_session_id=study_session_id,
        event_type="study_session_aborted",
        payload=event_payload,
    )
    store.log_study_context_event(event)
    await manager.broadcast(
        schemas.WsEvent(
            type="study.event_logged",
            payload=event.model_dump(mode="json"),
        )
    )
    return event


_MODEL_3DM_CODE = '''
import Rhino, System, os
result = ""
try:
    _path = __PATH_LITERAL__
    # Isoliert: NUR die aktuell sichtbare Geometrie des laufenden Laufs. Direkt
    # ueber sc.doc.Objects (RhinoCommon) iterieren statt ueber rs.AllObjects() —
    # letzteres lieferte in diesem UI-Thread-Kontext leer, obwohl sichtbare
    # Geometrie da war (06.07.2026, "Active"-Layer, Box). Verstecktes Objekt
    # ODER versteckter Layer (Archive-Backups, "Sitzung ..."-Layer voriger
    # Laeufe) wird uebersprungen -> die .3dm ist das FINALE Modell dieses Laufs.
    _f3 = Rhino.FileIO.File3dm()
    try:
        _f3.Settings.ModelUnitSystem = sc.doc.ModelUnitSystem
    except Exception:
        pass
    _lmap = {}
    _added = 0
    _seen = 0
    _skips = {}
    for _ro in sc.doc.Objects:
        try:
            if _ro is None or _ro.IsDeleted:
                continue
            _seen += 1
            _att = _ro.Attributes
            _li = _att.LayerIndex
            _lay = None
            try:
                if 0 <= _li < sc.doc.Layers.Count:
                    _lay = sc.doc.Layers[_li]
            except Exception:
                _lay = None
            if _lay is not None and not _lay.IsVisible:
                _skips["hidden_layer"] = _skips.get("hidden_layer", 0) + 1
                continue
            if _att.Visible is False:
                _skips["hidden_obj"] = _skips.get("hidden_obj", 0) + 1
                continue
            _geo = _ro.Geometry
            if _geo is None:
                _skips["no_geo"] = _skips.get("no_geo", 0) + 1
                continue
            if _li not in _lmap:
                if _lay is not None:
                    _lmap[_li] = _f3.AllLayers.AddLayer(_lay.Name, _lay.Color)
                else:
                    _lmap[_li] = _f3.AllLayers.AddLayer(
                        "Default", System.Drawing.Color.Black
                    )
            _dup = _att.Duplicate()
            _dup.LayerIndex = _lmap[_li]
            _f3.Objects.Add(_geo.Duplicate(), _dup)
            _added += 1
        except Exception as _e:
            _k = "err:" + type(_e).__name__
            _skips[_k] = _skips.get(_k, 0) + 1
    if _added == 0:
        result = json.dumps(
            {"exported": False, "count": 0, "seen": _seen, "skips": _skips, "reason": "empty"}
        )
    else:
        _ok = False
        try:
            _ok = _f3.Write(_path, 0)
        except Exception:
            _ok = False
        result = json.dumps(
            {"exported": bool(_ok) and os.path.exists(_path), "count": _added, "seen": _seen}
        )
except Exception as _exc:
    import traceback
    result = json.dumps({"error": str(_exc), "trace": traceback.format_exc()[:400]})
'''


async def _save_run_model_3dm() -> "str | None":
    """Best-effort: write the run's CURRENTLY VISIBLE geometry to a temp ``.3dm``.

    Isolated variant (Nutzerwahl 01.07.2026): only visible objects — anything
    hidden or on a hidden layer (prior runs' ``Sitzung …`` archive layers, the
    ``Archive`` version backups) is skipped — so the file is this run's final
    model, not the whole document. Dialog-free via ``File3dm`` so it can never
    block the UI thread. Returns the temp path on success, else ``None``; never
    raises into the export path.
    """
    try:
        from .rhino_exec import RHINO_AVAILABLE, run_code
    except Exception:
        return None
    if not RHINO_AVAILABLE:
        return None
    import tempfile as _tempfile

    fd, path = _tempfile.mkstemp(suffix=".3dm", prefix="rhaino-model-")
    os.close(fd)
    try:
        os.remove(path)  # let File3dm.Write create a clean file
    except OSError:
        pass
    code = _MODEL_3DM_CODE.replace("__PATH_LITERAL__", json.dumps(path))
    try:
        resp = await asyncio.to_thread(run_code, code, timeout=30.0)
    except Exception as e:  # pragma: no cover — defensive
        logger.warning("run model .3dm export failed: %s", e)
        return None
    # run_code liefert repr(result); result war json.dumps(...), der Rueckgabe-
    # String ist also repr-gewrappt ('{"exported": ...}'). Erst die repr-Huelle
    # mit literal_eval abziehen, dann das JSON parsen — sonst scheitert json.loads
    # an den Single-Quotes und die Funktion meldet faelschlich "leere Szene"
    # (der Grund, warum model.3dm bis 06.07.2026 NIE im Bundle landete).
    parsed: dict = {}
    if resp:
        _s = resp.strip()
        try:
            import ast as _ast

            _unwrapped = _ast.literal_eval(_s)
            if isinstance(_unwrapped, str):
                _s = _unwrapped
        except Exception:
            pass
        try:
            parsed = json.loads(_s)
        except Exception:
            parsed = {}
    if parsed.get("exported") and os.path.isfile(path):
        logger.info("run model .3dm saved (%s Objekte)", parsed.get("count"))
        return path
    logger.info("run model .3dm not saved: %s", str(parsed)[:200])
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass
    return None


def _run_model_store_dir() -> str:
    """Verzeichnis fuer die beim Lauf-Ende vorab gesicherten .3dm-Modelle
    (neben plugin.db). Ueberlebt einen Backend-Neustart, sodass ein spaeterer
    Export die Lauf-Geometrie Reset-unabhaengig findet."""
    from .config import _CONFIG_DIR

    d = os.path.join(_CONFIG_DIR, "run_models")
    os.makedirs(d, exist_ok=True)
    return d


def _saved_run_model_path(study_session_id: str) -> str:
    return os.path.join(_run_model_store_dir(), f"{study_session_id}.3dm")


@app.post("/api/study/sessions/{study_session_id}/save-run-model")
async def save_run_model(study_session_id: str) -> dict:
    """Sichert die AKTUELL sichtbare Lauf-Geometrie als ``.3dm`` fuer diese
    Studien-Session. Wird beim Lauf-Ende (Stop-Button) aufgerufen — VOR dem
    Fragebogen, solange die Geometrie garantiert in der Szene liegt. Der
    spaetere Export bevorzugt diese Datei (Reset-unabhaengig); nichts wird
    ueberschrieben. Best-effort: leere Szene / kein Rhino -> ``saved: False``,
    nie ein Fehler (der Lauf-Abschluss darf nie blockieren)."""
    import shutil as _shutil

    store = get_store()
    if store.get_study_session(study_session_id) is None:
        raise HTTPException(status_code=404, detail="Studien-Session nicht gefunden.")
    tmp = await _save_run_model_3dm()
    if not tmp:
        return {"saved": False}
    try:
        dest = _saved_run_model_path(study_session_id)
        _shutil.copyfile(tmp, dest)
        logger.info("run model vorab gesichert fuer %s", study_session_id)
        return {"saved": True}
    except Exception as e:  # pragma: no cover — defensive
        logger.warning("run model vorab-Sicherung fehlgeschlagen: %s", e)
        return {"saved": False}
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


@app.post(
    "/api/study/sessions/{study_session_id}/export",
    response_model=schemas.ExportResponse,
)
async def export_study_session(study_session_id: str) -> schemas.ExportResponse:
    """Produce the per-session ZIP bundle (§2.6).

    Bewusst statusunabhaengig: ``active``/``completed``/``aborted`` sind alle
    exportierbar (Forscher:in darf Teil-/Abbruchdaten jederzeit sichern). Den
    Lauf-Status haelt das Bundle selbst fest — ``manifest.json`` schreibt
    ``study_session.status`` + ``ended_at`` (siehe export._write_manifest),
    sodass die Auswertung jederzeit erkennt, ob ein Lauf vollstaendig war.
    """
    store = get_store()
    if store.get_study_session(study_session_id) is None:
        raise HTTPException(status_code=404, detail="Studien-Session nicht gefunden.")
    # Reset-sicher: bevorzugt die beim Lauf-Ende (Stop-Button) vorab gesicherte
    # .3dm (save_run_model) — sie liegt unabhaengig davon vor, ob der Viewport
    # inzwischen geleert / neu geladen wurde. Nur wenn keine existiert, wird die
    # AKTUELL sichtbare Szene gezogen (Fallback, z.B. Alt-Sessions ohne Vorab-
    # Sicherung). Die Vorab-Datei wird NICHT geloescht (mehrere Exporte moeglich,
    # sie gehoert der Session); die Fallback-Temp-Datei schon.
    prefetched = _saved_run_model_path(study_session_id)
    if os.path.isfile(prefetched):
        model_3dm_path = prefetched
        model_is_temp = False
    else:
        model_3dm_path = await _save_run_model_3dm()
        model_is_temp = True
    try:
        # IO-heavy (SQLite subset, PNG copies, ZIP). Off-load so the export
        # of a large session doesn't freeze the FastAPI event loop / WS traffic.
        result = await asyncio.to_thread(
            export_session_bundle, study_session_id, model_3dm_path
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        logger.exception("Export failed for %s", study_session_id)
        raise HTTPException(
            status_code=500, detail=f"Export fehlgeschlagen: {e}"
        ) from e
    finally:
        if model_3dm_path and model_is_temp:
            try:
                os.remove(model_3dm_path)
            except OSError:
                pass
    return schemas.ExportResponse(
        bundle_path=result.bundle_path,
        bytes=result.bytes,
        files_included=result.files_included,
    )


@app.post(
    "/api/study/sessions/{study_session_id}/events",
    response_model=schemas.StudyContextEvent,
)
async def log_study_context_event(
    study_session_id: str, payload: schemas.StudyContextEventCreate
) -> schemas.StudyContextEvent:
    """Log a study-scoped event (pause marker, blocked toggle, etc.)."""
    if payload.study_session_id != study_session_id:
        raise HTTPException(
            status_code=400,
            detail="study_session_id im Pfad und Body müssen übereinstimmen.",
        )
    store = get_store()
    if store.get_study_session(study_session_id) is None:
        raise HTTPException(status_code=404, detail="Studien-Session nicht gefunden.")
    event = schemas.StudyContextEvent(
        study_session_id=study_session_id,
        event_type=payload.event_type,
        payload=payload.payload,
    )
    store.log_study_context_event(event)
    await manager.broadcast(
        schemas.WsEvent(
            type="study.event_logged",
            payload=event.model_dump(mode="json"),
        )
    )
    return event


def _study_consent_blocks_chat(session_id: str) -> Optional[str]:
    """If study mode is active and this session is study-linked but lacks
    consent, return a German error message — otherwise ``None`` (proceed).

    Lives here, not in chat.send, because both the WS handler and the
    REST append_message route the bridge uses need the same gate.
    """
    if not config.is_study_mode:
        return None
    store = get_store()
    study = store.get_study_session_for_session(session_id)
    if study is None:
        # Dev session running parallel to study mode — allowed. The
        # study gate only applies to runs created via the pre-session
        # dialog.
        return None
    if study.status == "completed":
        return (
            "Diese Studien-Session ist bereits beendet. Bitte eine neue "
            "Studien-Session starten, wenn weitergearbeitet werden soll."
        )
    if study.status == "aborted":
        return (
            "Diese Studien-Session wurde abgebrochen. Bitte eine neue "
            "Studien-Session starten, wenn weitergearbeitet werden soll."
        )
    consent = store.latest_consent_for_study_session(study.id)
    if consent is None:
        return (
            "Einwilligung ausstehend — bitte den Einwilligungsdialog "
            "ausfüllen, bevor die erste Nachricht gesendet wird."
        )
    return None


# ---------------------------------------------------------------------------
# Uploads
# ---------------------------------------------------------------------------


@app.post("/api/upload/image")
async def upload_image(file: UploadFile = File(...)) -> JSONResponse:
    mime = file.content_type or "image/png"
    if mime not in ("image/png", "image/jpeg", "image/webp", "image/gif"):
        raise HTTPException(status_code=415, detail=f"unsupported media type: {mime}")
    # Cap the read so a huge upload can't balloon memory in the Rhino process
    # (the backend lives inside Rhino). ~20 MB matches Anthropic's image limit.
    max_bytes = 20 * 1024 * 1024
    raw = await file.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"Bild zu gross (max. {max_bytes // (1024 * 1024)} MB).",
        )
    data = base64.b64encode(raw).decode("ascii")
    return JSONResponse({"media_type": mime, "data": data, "size": len(raw)})


# ---------------------------------------------------------------------------
# Direct plugin actions (no LLM round-trip)
# ---------------------------------------------------------------------------


def _log_button_repair(
    *,
    tool_name: str,
    session_id: Optional[str],
    result: Any,
    started_at: datetime,
    duration_ms: int,
) -> None:
    """Persist a ``tool_calls`` row for a participant-initiated undo/redo.

    The direct undo/redo buttons (``/api/rhino/undo``, ``/api/rhino/redo``)
    bypass the agent loop, so the model path's repair logging in
    ``dispatch._persist_tool_call_and_maybe_snapshot`` never fires for them.
    This writes the same ``is_repair=True`` row through the identical
    persistence layer so the HITL-Auswertung (Pick -> Aktion -> korrekt? ->
    Reparatur) sees button- and model-initiated repairs uniformly.

    ``args`` carries a ``{"source": "button"}`` marker so button-initiated
    repairs stay distinguishable from model-initiated ones; ``triggered_by``
    stays None (manual repair, not pick-triggered).

    Best-effort + defensive: logging must never derail the endpoint. Without
    a session_id there is no row to attach the repair to, so we skip cleanly.
    """
    if not session_id:
        return
    try:
        from .agent.dispatch import _tool_result_text

        record = schemas.ToolCallRecord(
            session_id=session_id,
            message_id=None,
            tool_name=tool_name,
            args={"source": "button"},
            result=_tool_result_text(result),
            started_at=started_at,
            duration_ms=duration_ms,
            triggered_by=None,
            is_repair=True,
        )
        get_store().log_tool_call(record)
    except Exception as e:  # never derail the endpoint on a logging failure
        logger.warning("button-repair log failed for %s: %s", tool_name, e)


@app.post("/api/rhino/undo")
async def rhino_undo(payload: Optional[dict] = None) -> dict:
    session_id = str((payload or {}).get("session_id") or "").strip() or None
    started_at = datetime.now(timezone.utc)
    try:
        result = await dispatch_dedicated_tool(
            "undo_last_action",
            {},
            session_id=session_id,
        )
    except Exception as exc:
        # Log the failed repair attempt too, then re-raise unchanged.
        _log_button_repair(
            tool_name="undo_last_action",
            session_id=session_id,
            result=f"ERROR: {exc}",
            started_at=started_at,
            duration_ms=int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000),
        )
        raise
    _log_button_repair(
        tool_name="undo_last_action",
        session_id=session_id,
        result=result,
        started_at=started_at,
        duration_ms=int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000),
    )
    return {"status": "ok", "result": result}


@app.post("/api/rhino/redo")
async def rhino_redo(payload: Optional[dict] = None) -> dict:
    session_id = str((payload or {}).get("session_id") or "").strip() or None
    started_at = datetime.now(timezone.utc)
    try:
        result = await dispatch_dedicated_tool(
            "redo_last_action",
            {},
            session_id=session_id,
        )
    except Exception as exc:
        _log_button_repair(
            tool_name="redo_last_action",
            session_id=session_id,
            result=f"ERROR: {exc}",
            started_at=started_at,
            duration_ms=int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000),
        )
        raise
    _log_button_repair(
        tool_name="redo_last_action",
        session_id=session_id,
        result=result,
        started_at=started_at,
        duration_ms=int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000),
    )
    return {"status": "ok", "result": result}


@app.post("/api/rhino/clear-viewport")
async def rhino_clear_viewport() -> dict:
    """Hide — not delete — all currently-visible geometry for a fresh start.

    Triggered from the "Neue Sitzung"-Bestaetigungsdialog when the user
    opts to clear the viewport. Non-destructive by design, in line with the
    project's Active/Archive versioning ethos: every visible object is moved
    onto a fresh, hidden, grey ``Sitzung <timestamp>`` layer. Un-hiding that
    layer in Rhino's Layer-Panel brings the whole previous session straight
    back, and the move sits inside a native undo record so Ctrl+Z works too.

    Objects that are already hidden or live on a hidden layer (e.g. the
    ``Archive`` version backups, or other sessions' hidden variant layers)
    are left untouched. Afterwards the ``Active`` layer is left empty +
    current — and any stale active-variant pointer is dropped — so the new
    session's first geometry lands cleanly on ``Active``.
    """
    from .rhino_exec import RHINO_AVAILABLE, decode_run_code_result, run_code

    if not RHINO_AVAILABLE:
        raise HTTPException(status_code=503, detail="Rhino nicht verfuegbar")

    code = '''
moved = 0
layer_name = ""
try:
    # Move every currently-visible object onto a hidden session layer.
    # Skip objects already hidden or on a hidden layer so the Archive
    # backups (and other sessions' hidden layers) stay exactly as they are.
    # The session layer is created lazily on the first qualifying object so
    # clearing an already-empty viewport leaves no stray "Sitzung …" layers.
    for _oid in (rs.AllObjects() or []):
        try:
            if rs.IsObjectHidden(_oid):
                continue
            _cur = rs.ObjectLayer(_oid)
            if _cur and not rs.LayerVisible(_cur):
                continue
            if not layer_name:
                _base = "Sitzung " + datetime.now().strftime("%Y-%m-%d %H-%M")
                layer_name = _base
                _n = 2
                while rs.IsLayer(layer_name):
                    layer_name = _base + " (" + str(_n) + ")"
                    _n += 1
                rs.AddLayer(layer_name, color=(150, 150, 150), visible=False)
            rs.ObjectLayer(_oid, layer_name)
            moved += 1
        except Exception:
            pass

    # Drop any active-variant pointer so fresh geometry lands on Active.
    try:
        if "__active_variant_layer__" in sc.sticky:
            del sc.sticky["__active_variant_layer__"]
    except Exception:
        pass

    if not rs.IsLayer("Active"):
        rs.AddLayer("Active", (0, 0, 0))
    else:
        rs.LayerColor("Active", (0, 0, 0))
    rs.LayerVisible("Active", True)
    rs.CurrentLayer("Active")
    rs.Redraw()

    # Seal the previous session's undo state so a single Undo in the new
    # session can never roll back THIS clear-viewport layer-move (which
    # would resurrect the previous session's geometry). Rhino's native undo
    # stack AND the plugin action-history in sc.sticky are process-global
    # and outlive the chat/session switch (Pilot 01.07.2026, Bug A). The
    # hidden "Sitzung ..." layer stays as the explicit, visible recovery
    # path, so nothing is lost — only the cross-session undo is cut.
    try:
        sc.sticky["__furniture_action_history__"] = []
        sc.sticky["__furniture_redo_history__"] = []
        sc.sticky["__furniture_current_action__"] = None
        sc.sticky["__furniture_native_redo_available__"] = False
        sc.sticky["__furniture_native_redo_action__"] = None
    except Exception:
        pass
    try:
        # RhinoDoc.ClearUndoRecords(bool purgeDeletedObjects) — clears the
        # doc-global undo/redo buffer only; objects, layers and UserText
        # (archive backups, model_states) are untouched.
        sc.doc.ClearUndoRecords(True)
    except Exception:
        pass

    result = json.dumps({"moved": moved, "layer": layer_name})
except Exception as _exc:
    import traceback
    result = json.dumps({"error": str(_exc), "trace": traceback.format_exc()[:400]})
'''
    # Off-load the (UI-thread, up to 30 s) run so the FastAPI event loop
    # keeps serving WS heartbeats / status polls during the clear.
    response = await asyncio.to_thread(
        run_code,
        code,
        timeout=30.0,
        native_undo_description="clear_viewport",
    )
    logger.info("clear_viewport → %s", response[:300])

    # The Rhino code returns a JSON string ({"moved", "layer"} on success,
    # {"error", "trace"} on failure). run_code transports it repr-gewrappt
    # (result = json.dumps(...)); decode_run_code_result zieht die repr-Huelle
    # ab, bevor JSON geparst wird — sonst blieben moved/layer immer bei 0/"".
    # Auf alles Unparsbare fallen moved/layer auf 0 zurueck und die Rohantwort
    # wird zum Debuggen mitgegeben.
    moved = 0
    layer_name = ""
    parsed = decode_run_code_result(response)
    if isinstance(parsed, dict) and "moved" in parsed:
        moved = int(parsed.get("moved") or 0)
        layer_name = parsed.get("layer") or ""
    return {
        "status": "ok",
        "moved": moved,
        "layer": layer_name,
        "rhino_response": response,
    }


# ---------------------------------------------------------------------------
# Grasshopper bootstrap — "Click-to-Connect" header button
# ---------------------------------------------------------------------------

# Path to grasshopper_mcp_client.gh. Plugin sits at rhaino/plugin/,
# the GH client lives in rhaino/grasshopper/.
_RHINO_MCP_ROOT = os.path.dirname(_PLUGIN_DIR)
_GH_CLIENT_PATH = os.path.join(_RHINO_MCP_ROOT, "grasshopper", "grasshopper_mcp_client.gh")


@app.get("/api/grasshopper/status")
def grasshopper_status() -> dict:
    """Is the GH HTTP server (inside grasshopper_mcp_client.gh) reachable?

    Frontend polls this every few seconds to drive the GH-dot color.
    Cheap: tiny POST to localhost:9998; returns within ~30ms when GH is up,
    ConnectionRefused when it's not.
    """
    from .grasshopper_bridge import dispatch_grasshopper_tool

    result = dispatch_grasshopper_tool("is_server_available", {})
    return {"connected": result == "True"}


@app.post("/api/grasshopper/connect")
def grasshopper_connect() -> dict:
    """Open Grasshopper and load grasshopper_mcp_client.gh on Rhino's UI thread.

    The function blocks until the Rhino command + GH document open returns,
    typically 1-5 seconds depending on whether GH had to be initialised
    for the first time. After this call returns, the GH HTTP server may
    still need another moment to come up — the frontend keeps polling
    /status to detect actual readiness.
    """
    from .grasshopper_launch import GH_CLIENT_PATH, launch_grasshopper
    from .rhino_exec import RHINO_AVAILABLE

    if not RHINO_AVAILABLE:
        raise HTTPException(status_code=503, detail="Rhino nicht verfuegbar")
    if not os.path.isfile(GH_CLIENT_PATH):
        raise HTTPException(
            status_code=500,
            detail=f"GH-Datei nicht gefunden: {GH_CLIENT_PATH}",
        )

    # The launch code (open GH window, load grasshopper_mcp_client.gh, flip
    # the server toggle on, minimize the window, enable global preview) lives
    # in grasshopper_launch.py so the bridge auto-connect path can reuse the
    # exact same sequence without a user round-trip. See that module for the
    # GH 2.x API rationale (canvas.Document, cluster toggle recursion, …).
    response = launch_grasshopper(timeout=30.0)
    logger.info("grasshopper_connect → %s", response[:800])
    return {"status": "triggered", "rhino_response": response}


@app.post("/api/grasshopper/bake")
async def grasshopper_bake(payload: dict) -> dict:
    """Bake the live GH geometry into real Rhino objects — the HITL commit.

    User-driven (the "Backen" button), never agent-driven: the designer
    explores parametrically via sliders, then commits the state they
    like. We bake the currently visible AI-created GH geometry onto the
    currently active design layer (variant layer when active, otherwise
    ``Active``), then clear the AI-built GH chain entirely
    (clear_gh_session). The baked Rhino objects — plus the Archive-layer
    versioning convention — ARE the snapshots, so the parametric
    scaffolding has served its purpose and would only clutter the
    canvas; re-parametrising is a fresh prompt. The GH chain and the
    session's exposed sliders are cleared only after a successful bake;
    on failure everything stays alive so the designer can retry or
    adjust first.
    """
    from .rhino_exec import RHINO_AVAILABLE, run_code

    session_id = payload.get("session_id")
    if not RHINO_AVAILABLE:
        raise HTTPException(status_code=503, detail="Rhino nicht verfuegbar")

    # Runs on Rhino's UI thread. Defensive throughout (per-component
    # try/except + a steps log) — GH's bake API has version quirks and a
    # hard failure here must not take Rhino down.
    code = '''
import Rhino
import System

steps = []
baked = []
try:
    import clr
    clr.AddReference("Grasshopper")
    import Grasshopper as GH

    canvas = GH.Instances.ActiveCanvas
    doc = canvas.Document if canvas is not None else None
    if doc is None:
        result = "no_gh_document"
    else:
        rhino_doc = Rhino.RhinoDoc.ActiveDoc

        # Ensure the "Active" layer (green) exists — project versioning
        # convention: current work on Active, old versions on Archive.
        target_layer_name = sc.sticky.get("__active_variant_layer__") or "Active"
        active_idx = rhino_doc.Layers.FindByFullPath(target_layer_name, -1)
        if active_idx < 0:
            layer = Rhino.DocObjects.Layer()
            layer.Name = target_layer_name
            layer.Color = System.Drawing.Color.FromArgb(0, 200, 0)
            active_idx = rhino_doc.Layers.Add(layer)
        attr = Rhino.DocObjects.ObjectAttributes()
        attr.LayerIndex = active_idx
        try:
            rs.LayerVisible(target_layer_name, True)
            if target_layer_name != "Active" and rs.IsLayer("Active"):
                rs.LayerVisible("Active", False)
        except Exception:
            pass

        bake_iface = GH.Kernel.IGH_BakeAwareObject
        preview_iface = GH.Kernel.IGH_PreviewObject
        tracked = set(sc.sticky.get("ai_created_guids", []) or [])

        for obj in list(doc.Objects):
            guid_str = str(getattr(obj, "InstanceGuid", ""))
            if tracked and guid_str not in tracked:
                continue
            if not isinstance(obj, bake_iface):
                continue
            if isinstance(obj, preview_iface):
                try:
                    if preview_iface(obj).Hidden:
                        continue
                except Exception:
                    pass
            baker = bake_iface(obj)
            try:
                if not baker.IsBakeCapable:
                    continue
            except Exception:
                pass
            ids = System.Collections.Generic.List[System.Guid]()
            try:
                baker.BakeGeometry(rhino_doc, attr, ids)
            except Exception as bake_exc:
                steps.append("bake-fail %s: %s" % (
                    getattr(obj, "NickName", "?"), bake_exc))
                continue
            if ids.Count == 0:
                continue
            for g in ids:
                g_str = str(g)
                baked.append(g_str)
                # Defense-in-depth for the multi-object undo/redo path: GH
                # BakeGeometry leaves the baked objects nameless (or shares
                # the component nickname across all of them). Give each baked
                # object a GUID-unique name so any name-based bookkeeping in
                # the undo/redo snapshot/restore path can't collapse several
                # legs of ONE bake onto a single name. The GUID-based root fix
                # in action_history_preamble.py already handles this, but a
                # unique name keeps Archive snapshots and rs.ObjectsByName
                # unambiguous as a second line of defense. Only the first 8
                # hex chars of the GUID: short enough for pick chips/chat,
                # collision risk is negligible per document.
                try:
                    if not rs.ObjectName(g):
                        rs.ObjectName(g, "GH_Bake_%s" % g_str[:8])
                except Exception:
                    pass
            # Hide this component's GH preview so the freshly baked Rhino
            # object isn't shadowed by an overlapping live preview.
            if isinstance(obj, preview_iface):
                try:
                    preview_iface(obj).Hidden = True
                except Exception:
                    pass

        # Hang the baked GUIDs onto the plugin action-history so the "Backen"
        # commit becomes undoable: undo_last_action deletes these created
        # objects (and snapshots them to Archive for redo). native_undo is a
        # documented no-op here, so without an explicit action there is
        # nothing for undo_last_action to find. Undo of the bake removes the
        # baked Rhino objects but does NOT restore the cleared GH definition
        # — accepted semantics (re-parametrising is a fresh prompt).
        if baked:
            try:
                _aid = begin_action("grasshopper_bake")
                for g in baked:
                    _record_created_object(g)
                finish_action(_aid)
                steps.append("action_recorded %d" % len(baked))
            except Exception as action_exc:
                steps.append("action-record-fail: %s" % action_exc)

        try:
            rhino_doc.Views.Redraw()
        except Exception:
            pass
        result = "baked %d object(s) to %s; steps=%s" % (
            len(baked), target_layer_name, ", ".join(steps))
except Exception as exc:
    import traceback
    result = "exception: " + str(exc) + " | " + traceback.format_exc()[:400]
'''
    # run_code blocks up to 30 s on the Rhino UI thread. Off-load it so a
    # bake doesn't freeze the whole FastAPI event loop (WS heartbeats,
    # slider ticks, status polls) during the HITL commit moment.
    response = await asyncio.to_thread(
        run_code,
        code,
        timeout=30.0,
        native_undo_description="grasshopper_bake",
    )
    logger.info("grasshopper_bake → %s", response[:500])

    # Parse the "baked N object(s)" count out of the Rhino response so the
    # frontend can show how many objects landed without re-querying Rhino.
    baked_count = 0
    _count_match = re.search(r"baked\s+(\d+)\s+object", response)
    if _count_match:
        baked_count = int(_count_match.group(1))

    baked_ok = "baked" in response and not response.startswith("baked 0")
    if baked_ok:
        # After a successful bake the parametric GH chain has done its
        # job — delete it so the canvas stays clean (the baked Rhino
        # objects are the snapshots, not dormant GH chains). The bake
        # code already hid the components as a fallback; clear_gh_session
        # removes them outright. Then clear the session sliders, which
        # would otherwise drive a now-deleted chain. On a failed bake
        # everything is left intact for a retry.
        from .grasshopper_bridge import dispatch_grasshopper_tool

        # Blocking urllib call (up to 30 s) — off-load like run_code above.
        clear_result = await asyncio.to_thread(
            dispatch_grasshopper_tool, "clear_gh_session", {}
        )
        logger.info(
            "grasshopper_bake → clear_gh_session: %s", str(clear_result)[:200]
        )
        if session_id:
            await clear_session_parameters(
                session_id,
                clear_grasshopper_context=True,
            )

    return {
        "status": "ok" if baked_ok else "nothing_baked",
        "rhino_response": response,
        "baked_count": baked_count,
    }


def _abort_restore_code(source_ids: list[str]) -> str:
    """Rhino-Python: macht das/die Original-Objekt(e) nach einem Parametrisier-
    Abbruch wieder sichtbar. Deterministisch ueber die exakten object_ids, die
    der Nutzer beim Parametrisieren gepickt hat (frontend-getrackt):

      - Objekt noch im Dokument (GH war eine Vorschau-Ueberlagerung, das Original
        blieb auf Active): einblenden + ggf. vom versteckten Archive-Layer zurueck
        auf Active holen.
      - Objekt weg (der Agent hat es ins Archive verschoben/ersetzt): den Archive-
        Backup mit passender ``archive_source_id`` (hoechste ``archive_version``)
        als saubere Kopie auf Active zurueckholen und den Originalnamen setzen.
    """
    return (
        "ids = {ids!r}\n"
        "if rs.IsLayer('Active'):\n"
        "    rs.LayerVisible('Active', True)\n"
        "shown = []\n"
        "restored = []\n"
        "for sid in ids:\n"
        "    if rs.IsObject(sid):\n"
        "        try:\n"
        "            if rs.ObjectLayer(sid) == 'Archive':\n"
        "                rs.ObjectLayer(sid, 'Active')\n"
        "            if rs.IsObjectHidden(sid):\n"
        "                rs.ShowObject(sid)\n"
        "            shown.append(str(sid))\n"
        "        except Exception:\n"
        "            pass\n"
        "        continue\n"
        "    arch = rs.ObjectsByLayer('Archive') or []\n"
        "    best = None\n"
        "    best_ver = -1\n"
        "    for o in arch:\n"
        "        try:\n"
        "            if rs.GetUserText(o, 'archive_source_id') == sid:\n"
        "                v = rs.GetUserText(o, 'archive_version')\n"
        "                vi = int(v) if v else 0\n"
        "                if vi > best_ver:\n"
        "                    best_ver = vi\n"
        "                    best = o\n"
        "        except Exception:\n"
        "            pass\n"
        "    if best:\n"
        "        copy = rs.CopyObject(best)\n"
        "        if copy:\n"
        "            rs.ObjectLayer(copy, 'Active')\n"
        "            rs.ShowObject(copy)\n"
        "            nm = rs.GetUserText(best, 'archive_source_name')\n"
        "            if nm:\n"
        "                rs.ObjectName(copy, nm)\n"
        "            for k in ('archive_source_id', 'archive_source_name', 'archive_timestamp', 'archive_version'):\n"
        "                try:\n"
        "                    rs.SetUserText(copy, k, '')\n"
        "                except Exception:\n"
        "                    pass\n"
        "            restored.append(str(copy))\n"
        "rs.Redraw()\n"
        "result = 'shown=%d restored=%d' % (len(shown), len(restored))\n"
    ).format(ids=list(source_ids))


@app.post("/api/grasshopper/abort")
async def grasshopper_abort(payload: dict) -> dict:
    """Eine aktive GH-Parametrisierung verwerfen + das alte Original zurueckholen.

    Die HITL-Notbremse — der "Verwerfen"-Button, spiegelbildlich zum Bake nur
    OHNE das Backen: (1) die KI-gebaute GH-Chain abbauen (clear_gh_session — der
    gleiche erprobte Teardown, den der Bake nach Erfolg faehrt; die Live-Vorschau
    verschwindet), (2) die Original-Objekt(e), auf denen die Parametrisierung
    basierte, deterministisch wiederherstellen (einblenden bzw. ueber die
    archive_source_id aus dem Archive zurueckholen — keine Heuristik, die exakten
    gepickten object_ids kommen vom Frontend), (3) Slider + GH-Strukturkontext
    clearen, sodass sich das Panel selbst schliesst (parameter.cleared).
    Nutzergesteuert, nie agentengesteuert; beruehrt kein Tool/keinen Prompt ->
    Golden-Hash bleibt gruen.
    """
    from .grasshopper_bridge import dispatch_grasshopper_tool
    from .rhino_exec import RHINO_AVAILABLE, run_code

    session_id = payload.get("session_id")
    raw_ids = payload.get("source_object_ids") or []
    source_ids = [str(s) for s in raw_ids if s]
    if not RHINO_AVAILABLE:
        raise HTTPException(status_code=503, detail="Rhino nicht verfuegbar")

    # 1. KI-GH-Chain abbauen (Slider + Komponenten + Live-Vorschau). Off-load,
    #    da der HTTP-Call zum GH-Server bis 30 s blockieren kann.
    clear_result = await asyncio.to_thread(
        dispatch_grasshopper_tool, "clear_gh_session", {}
    )
    logger.info("grasshopper_abort → clear_gh_session: %s", str(clear_result)[:200])

    # 2. Original(e) wiederherstellen (nur wenn das Frontend Ziele mitgeschickt
    #    hat; sonst entfaellt der Schritt sauber — kein Raten).
    restored_info = ""
    if source_ids:
        try:
            restored_info = await asyncio.to_thread(
                run_code,
                _abort_restore_code(source_ids),
                timeout=15.0,
                native_undo_description="grasshopper_abort_restore",
            )
        except Exception as e:  # noqa: BLE001 — Restore darf den Abort nie kippen
            logger.warning("grasshopper_abort restore failed: %s", e)
        logger.info("grasshopper_abort → restore: %s", str(restored_info)[:200])

    # 3. Slider + GH-Kontext clearen -> Panel unmountet via parameter.cleared
    #    (gleicher Pfad wie nach dem Bake / beim ×).
    if session_id:
        await clear_session_parameters(
            session_id,
            clear_grasshopper_context=True,
        )

    return {"status": "ok", "restored": restored_info}


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------


def _cancel_session_run(session_id: str) -> None:
    """Cancel a session's in-flight run + drain its open gates (chat.cancel core)."""
    task = _AGENT_TASKS.get(session_id)
    if task is not None and not task.done():
        task.cancel()
    try:
        from .agent.gate import gate_registry

        gate_registry.cancel_session(session_id)
    except Exception as e:  # pragma: no cover — defensive
        logger.warning("gate drain failed for %s: %s", session_id, e)


async def _cleanup_orphaned_runs_after_grace() -> None:
    """Cancel every in-flight run + background task once the LAST client is gone.

    Waits out the grace window first; a reconnect within it (blip / the
    frontend's 1.5s auto-reconnect) aborts the teardown via the post-sleep
    client_count re-check, so a transient drop never kills a live run. Only a
    real close (no client back) tears the ghost run down.
    """
    try:
        await asyncio.sleep(_DISCONNECT_GRACE_S)
    except asyncio.CancelledError:  # pragma: no cover — shutdown
        return
    if manager.client_count > 0:
        return
    for session_id in list(_AGENT_TASKS.keys()):
        _cancel_session_run(session_id)
    for t in list(_BG_TASKS):
        t.cancel()
    _BG_TASKS.clear()
    logger.info(
        "Orphaned runs cancelled — no WS client after %.0fs grace",
        _DISCONNECT_GRACE_S,
    )


async def _on_client_disconnect(ws: WebSocket) -> None:
    """Drop the client; if it was the last one, schedule a graced run teardown."""
    global _disconnect_cleanup_task
    await manager.disconnect(ws)
    if manager.client_count == 0 and (
        _disconnect_cleanup_task is None or _disconnect_cleanup_task.done()
    ):
        _disconnect_cleanup_task = asyncio.create_task(
            _cleanup_orphaned_runs_after_grace()
        )


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    global _disconnect_cleanup_task
    await manager.connect(ws)
    # A client is back — abort any pending orphan-run teardown scheduled by a
    # prior last-disconnect, so a reconnect (network blip / panel reload) keeps
    # the in-flight run alive.
    if _disconnect_cleanup_task is not None and not _disconnect_cleanup_task.done():
        _disconnect_cleanup_task.cancel()
        _disconnect_cleanup_task = None
    # Initial-state push: the frontend resets rhinoSelectionCount to 0 on
    # (re)connect, so send the current Rhino selection count right away —
    # otherwise the badge would only appear after the next selection change.
    try:
        from .viewport_bridge import selection_watcher

        selection_watcher.emit_current_threadsafe()
    except Exception as e:  # pragma: no cover — defensive
        logger.debug("initial selection emit failed: %s", e)
    try:
        while True:
            raw = await ws.receive_text()
            try:
                cmd = schemas.WsCommand.model_validate_json(raw)
            except Exception as e:
                await manager.send_to(
                    ws,
                    schemas.WsEvent(
                        type="error", payload={"message": f"bad command: {e}"}
                    ),
                )
                continue
            # Isolate a single bad command: log + report it to this client,
            # then keep the connection. Only a real disconnect/transport error
            # (outer handler) tears the socket down.
            try:
                await _handle_command(ws, cmd)
            except WebSocketDisconnect:
                raise
            except Exception as e:
                logger.exception(
                    "WS command %s failed: %s", getattr(cmd, "type", "?"), e
                )
                await manager.send_to(
                    ws,
                    schemas.WsEvent(
                        type="error",
                        payload={"message": f"Befehl fehlgeschlagen: {e}"},
                        correlation_id=getattr(cmd, "correlation_id", None),
                    ),
                )
    except WebSocketDisconnect:
        await _on_client_disconnect(ws)
    except Exception as e:
        logger.exception("WS handler error: %s", e)
        await _on_client_disconnect(ws)


# Viewport / parameter / variant WS commands that drive werkzeug-only
# affordances. They bypass the model tool surface, so build_tool_list (which
# strips interaction TOOLS from basis) does not gate them — this set does.
_WERKZEUG_ONLY_COMMANDS = frozenset(
    {
        "viewport.request_snapshot",
        "viewport.request_pick",
        "viewport.request_point",
        "viewport.request_component",
        "viewport.request_sketch",
        "viewport.sketch_window",
        "viewport.highlight_reference",
        "viewport.highlight_clear",
        "viewport.point_markers",
        "viewport.persistent_refs",
        "parameter.changed",
        "parameter.clear",
        "variant.select",
        "variant.delete",
        "variant.show_original",
        "variants.clear",
        "variant.commit",
    }
)


def _session_condition_is_basis(session_id: str | None) -> bool:
    """True only when the session provably exists and is the basis condition.

    Fail-open for unknown/missing ids: we BLOCK only when we can prove the
    session is basis, so an overlay-only command without a session_id is never
    broken. The study-critical commands (picks, parameter, variant) all carry
    session_id.
    """
    if not session_id:
        return False
    try:
        row = get_store().get_session(session_id)
    except Exception:
        return False
    return row is not None and row.condition == "basis"


def _log_affordance_event(
    session_id: str, event_type: str, payload: dict
) -> None:
    """Best-effort: persist a designer-affordance interaction as a study event.

    FF1/FF2 need affordance-usage counts that aren't reconstructable from the
    chat transcript alone — a quick-reply click looks like a typed message, a
    variant pick leaves no ``tool_calls`` row. Resolves the active study
    session for this plugin session and writes one ``events`` row. No-op
    outside an active study session (the dev/normal case). Never raises —
    logging is observability, not business logic.
    """
    try:
        store = get_store()
        study = store.get_study_session_for_session(session_id)
        if study is None or getattr(study, "status", None) != "active":
            return
        store.log_study_context_event(
            schemas.StudyContextEvent(
                study_session_id=study.id,
                event_type=event_type,
                payload=payload,
            )
        )
    except Exception as e:  # pragma: no cover — defensive
        logger.warning("affordance event log failed (%s): %s", event_type, e)


async def _handle_command(ws: WebSocket, cmd: schemas.WsCommand) -> None:
    """Dispatch an inbound WS command.

    Phase-1 implementation: chat.send persists the user message and emits
    ``message.new``. Actual AI response generation is wired up in phase 2
    (MCP bridge) and phase 6 (API client).
    """
    payload = cmd.payload
    corr = cmd.correlation_id

    if cmd.type == "chat.send":
        session_id = payload.get("session_id")
        content = payload.get("content", [])
        if not session_id or not content:
            await manager.send_to(
                ws,
                schemas.WsEvent(
                    type="error",
                    payload={"message": "chat.send requires session_id + content"},
                    correlation_id=corr,
                ),
            )
            return
        # Study-mode consent gate (Studienartefakt-Spec §2.7) — a
        # study-linked session without recorded consent rejects sends so
        # the participant can't accidentally start before agreeing.
        consent_block = _study_consent_blocks_chat(session_id)
        if consent_block is not None:
            await manager.send_to(
                ws,
                schemas.WsEvent(
                    type="message.error",
                    payload={
                        "session_id": session_id,
                        "message": consent_block,
                    },
                    correlation_id=corr,
                ),
            )
            return
        # Serialize sends per session: if a run is already in flight, reject the
        # duplicate instead of starting a second agent loop. Two concurrent runs
        # interleave assistant/tool blocks into one history (breaking
        # tool_use/tool_result pairing -> next-turn API 400) and corrupt the
        # study transcript. The frontend disables the send button via
        # pendingSend, but a second client/panel or a replayed frame bypasses it,
        # so the backend must be authoritative.
        if config.is_api_mode:
            existing_run = _AGENT_TASKS.get(session_id)
            if existing_run is not None and not existing_run.done():
                await manager.send_to(
                    ws,
                    schemas.WsEvent(
                        type="message.error",
                        payload={
                            "session_id": session_id,
                            "message": (
                                "Es läuft bereits eine Antwort für diese "
                                "Sitzung. Bitte warten, bis sie abgeschlossen "
                                "ist."
                            ),
                        },
                        correlation_id=corr,
                    ),
                )
                return
        user_msg = schemas.Message(
            session_id=session_id,
            role="user",
            content=content,
        )
        get_store().add_message(user_msg)
        await manager.broadcast(
            schemas.WsEvent(
                type="message.new",
                payload=user_msg.model_dump(mode="json"),
                correlation_id=corr,
            )
        )
        # FF1/FF2-Affordanz-Spur (best-effort, nur im Studienmodus): ein
        # Quick-Reply-Klick (sonst im messages-Log wie getippter Text) und
        # mitgesendete Pick-/Sketch-Bloecke als typisierte Events festhalten,
        # damit die Auswertung sie ohne Transkript-Parsing abfragen kann.
        if payload.get("via") == "quick_reply":
            _log_affordance_event(
                session_id, "quick_reply_used", {"message_id": user_msg.id}
            )
        _block_types = {b.get("type") for b in content if isinstance(b, dict)}
        _pick_kinds = sorted(
            _block_types & {"point_pick", "component_pick", "selection"}
        )
        if _pick_kinds:
            _log_affordance_event(
                session_id,
                "pick_used",
                {"message_id": user_msg.id, "kinds": _pick_kinds},
            )
        if "sketch" in _block_types:
            _log_affordance_event(
                session_id, "sketch_submitted", {"message_id": user_msg.id}
            )
        # Kick off the agent in api mode. In mcp mode the reply is driven
        # externally by Claude Code / Desktop writing to /api/sessions/{id}/messages.
        if config.is_api_mode:
            from .agent import run_agent

            task = asyncio.create_task(run_agent(session_id, correlation_id=corr))
            # Track the run so chat.cancel can abort it (and drain its gates).
            _AGENT_TASKS[session_id] = task

            def _log_task_result(t: asyncio.Task, _sid: str = session_id) -> None:
                # Clear the registry entry only if it still points at this
                # task — a newer run for the same session must not be evicted.
                if _AGENT_TASKS.get(_sid) is t:
                    _AGENT_TASKS.pop(_sid, None)
                if t.cancelled():
                    logger.info("agent task cancelled")
                    return
                exc = t.exception()
                if exc is not None:
                    logger.error("agent task died uncaught: %r", exc)

            task.add_done_callback(_log_task_result)
        else:
            # MCP bridge not yet wired (Phase 2). Without this explicit error
            # the UI would spin forever on pendingSend because no reply comes back.
            logger.info(
                "chat.send in %s mode — no bridge, surfacing hint to UI",
                config.backend_mode,
            )
            await manager.broadcast(
                schemas.WsEvent(
                    type="message.error",
                    payload={
                        "session_id": session_id,
                        "message": (
                            f"Backend-Modus ist '{config.backend_mode}'. Für direkte "
                            "Antworten bitte Einstellungen öffnen und 'Direkter "
                            "API-Aufruf' wählen (API-Key setzen). Der MCP-Bridge-Modus "
                            "ist noch nicht verdrahtet."
                        ),
                    },
                    correlation_id=corr,
                )
            )
        return

    if cmd.type == "gate.resolve":
        await _handle_gate_resolve(ws, payload, corr)
        return

    if cmd.type == "chat.cancel":
        await _handle_chat_cancel(ws, payload, corr)
        return

    if cmd.type == "session.create":
        session = schemas.Session(**payload)
        # Mirror the REST handler — condition lives in Settings during
        # development mode and is fixed at session creation.
        session.condition = config.default_condition
        get_store().create_session(session)
        await manager.broadcast(
            schemas.WsEvent(
                type="session.created",
                payload=session.model_dump(mode="json"),
                correlation_id=corr,
            )
        )
        return

    if cmd.type == "settings.update":
        allowed = set(schemas.Settings.model_fields.keys())
        rejected = _detect_locked_changes(payload, allowed)
        if rejected:
            await _log_locked_change_attempts(rejected)
            keys = ", ".join(k for k, _ in rejected)
            await manager.send_to(
                ws,
                schemas.WsEvent(
                    type="error",
                    payload={
                        "message": (
                            f"Studienmodus ist aktiv — die Felder {keys} "
                            "sind gesperrt."
                        ),
                        "rejected_keys": [k for k, _ in rejected],
                    },
                    correlation_id=corr,
                ),
            )
            return
        for k, v in payload.items():
            if k in allowed:
                setattr(config, k, v)
        config.save()
        merged = schemas.Settings(**config.as_dict())
        await manager.broadcast(
            schemas.WsEvent(
                type="settings.updated",
                payload=merged.model_dump(mode="json"),
                correlation_id=corr,
            )
        )
        return

    # Defense-in-depth condition gate (Studienartefakt-Spec §1.2): the
    # werkzeug-only viewport/parameter/variant commands below must never act in
    # a basis session. The frontend already hides the buttons; this is the
    # authoritative backstop against a forged/replayed frame or a frontend
    # regression. Pure runtime gating — does not touch build_tool_list output,
    # so golden hashes are unaffected.
    if cmd.type in _WERKZEUG_ONLY_COMMANDS and _session_condition_is_basis(
        payload.get("session_id") if isinstance(payload, dict) else None
    ):
        logger.info(
            "Blocked werkzeug-only command %s in a basis session", cmd.type
        )
        await manager.send_to(
            ws,
            schemas.WsEvent(
                type="error",
                payload={
                    "message": "Interaktionswerkzeug in der Basis-Bedingung nicht verfuegbar.",
                },
                correlation_id=corr,
            ),
        )
        return

    if cmd.type == "viewport.request_snapshot":
        # DORMANT seit 16.06.2026: der manuelle Kamera-Button wurde aus der
        # InputBar entfernt und in das Sketch-Werkzeug eingeschmolzen (Sketch
        # ohne Striche = reiner Multi-View-Screenshot). Kein Frontend-Caller
        # mehr; Handler bleibt fuer ein evtl. Reaktivieren erhalten. Die
        # autonome Modell-Perzeption laeuft ueber capture_viewport (eigener
        # Pfad in agent/dispatch.py), nicht hierueber.
        await _handle_snapshot_request(ws, payload, corr)
        return

    if cmd.type == "viewport.request_pick":
        await _handle_pick_request(ws, payload, corr)
        return

    if cmd.type == "viewport.request_point":
        await _handle_point_request(ws, payload, corr)
        return

    if cmd.type == "viewport.request_component":
        await _handle_component_request(ws, payload, corr)
        return

    if cmd.type == "viewport.request_sketch":
        await _handle_sketch_request(ws, payload, corr)
        return

    if cmd.type == "viewport.sketch_window":
        await _handle_sketch_window(ws, payload, corr)
        return

    if cmd.type == "viewport.highlight_reference":
        await _handle_highlight_reference(ws, payload, corr)
        return

    if cmd.type == "viewport.highlight_clear":
        await _handle_highlight_clear(ws, payload, corr)
        return

    if cmd.type == "viewport.point_markers":
        await _handle_point_markers(ws, payload, corr)
        return

    if cmd.type == "viewport.persistent_refs":
        await _handle_persistent_refs(ws, payload, corr)
        return

    if cmd.type == "parameter.changed":
        await _handle_parameter_changed(ws, payload, corr)
        return

    if cmd.type == "parameter.clear":
        await _handle_parameter_clear(ws, payload, corr)
        return

    if cmd.type == "variant.select":
        await _handle_variant_select_ws(ws, payload, corr)
        return

    if cmd.type == "variant.delete":
        await _handle_variant_delete_ws(ws, payload, corr)
        return

    if cmd.type == "variant.show_original":
        await _handle_variant_show_original_ws(ws, payload, corr)
        return

    if cmd.type == "variants.clear":
        await _handle_variants_clear_ws(ws, payload, corr)
        return

    if cmd.type == "variant.commit":
        await _handle_variant_commit_ws(ws, payload, corr)
        return

    await manager.send_to(
        ws,
        schemas.WsEvent(
            type="ack",
            payload={"command": cmd.type, "status": "not_implemented"},
            correlation_id=corr,
        ),
    )


def _clamp_max_size(payload: dict, default: int) -> int:
    """Defensive parse of a viewport ``max_size`` hint: a missing or
    non-numeric value falls back to ``default``, then the result is clamped
    to a sane render range so a 0 / negative / absurdly large value can
    neither crash the .NET bitmap path nor balloon memory."""
    try:
        value = int(payload.get("max_size", default))
    except (TypeError, ValueError):
        value = default
    return max(64, min(4096, value))


async def _viewport_unavailable(ws: WebSocket, source: str, corr) -> None:
    """Notify the client that a viewport operation cannot run because the
    backend is running outside Rhino. Every viewport WS handler calls this
    when RHINO_AVAILABLE is False, then returns."""
    await manager.send_to(
        ws,
        schemas.WsEvent(
            type="viewport.error",
            payload={
                "source": source,
                "message": "Rhino nicht verfügbar — Backend läuft außerhalb von Rhino.",
            },
            correlation_id=corr,
        ),
    )


async def _handle_sketch_window(
    ws: WebSocket, payload: dict, corr: Optional[str]
) -> None:
    """Grow/restore the floating plugin window around the active viewport.

    Frontend sends ``{state: "grow"}`` when the sketch overlay opens and
    ``{state: "restore"}`` on every close path. We marshal the matching
    ``panel`` call onto the Rhino UI thread (Eto window geometry must be
    touched there).

    Bulletproof-defensiv: a failure to import ``panel``, reach Rhino, or
    marshal the call is logged and swallowed — the sketch keeps working,
    just without the resize. In docked mode the panel functions are clean
    no-ops, so this branch is harmless there too. Never raises into the WS
    dispatch loop.
    """
    state = str(payload.get("state", "")).strip()
    if state not in ("grow", "restore"):
        return
    try:
        import panel  # plugin/ is on sys.path (see panel.py's backend.config import)
    except Exception as e:  # pragma: no cover — defensive
        logger.warning("sketch_window: panel import failed (%s) — skipping resize", e)
        return
    try:
        import Rhino  # type: ignore
        import System  # type: ignore

        action = (
            panel.grow_to_active_viewport
            if state == "grow"
            else panel.restore_geometry
        )

        def _run() -> None:
            try:
                action()
            except Exception as inner:  # pragma: no cover — defensive
                logger.warning("sketch_window %s failed on UI thread: %s", state, inner)

        Rhino.RhinoApp.InvokeOnUiThread(System.Action(_run))
    except Exception as e:  # pragma: no cover — defensive
        logger.warning("sketch_window %s dispatch failed: %s", state, e)


async def _handle_gate_resolve(
    ws: WebSocket, payload: dict, corr: Optional[str]
) -> None:
    """Resolve one selective-preview gate (Part C): the designer accepted or
    reverted a pending destructive tool call.

    Idempotent on the registry side — a duplicate resolve (or a resolve for an
    unknown / already-decided tu_id) is a harmless no-op. The actual highlight
    clear + ``gate.cleared`` broadcast happen on the awaiting side in
    ``dispatch._await_gate_decision`` once the future fires, so this handler
    only flips the future.
    """
    tu_id = str(payload.get("tu_id") or "").strip()
    decision = str(payload.get("decision") or "").strip()
    if not tu_id:
        return
    try:
        from .agent.gate import gate_registry

        resolved = gate_registry.resolve(tu_id, decision)
        if not resolved:
            logger.debug("gate.resolve: tu_id %s unknown/already decided", tu_id)
    except Exception as e:  # pragma: no cover — never break the WS loop
        logger.warning("gate.resolve failed for %s: %s", tu_id, e)


async def _handle_chat_cancel(
    ws: WebSocket, payload: dict, corr: Optional[str]
) -> None:
    """Cancel the in-flight run for a session and revert all its open gates.

    Aborts the tracked ``run_agent`` task (if any), resolves every pending
    selective-preview gate of the session as "revert" so no tool call hangs,
    and broadcasts ``run.cancelled``. Defensive throughout — a cancel must
    never leave a half-state behind, and a missing session_id is a no-op.
    """
    session_id = str(payload.get("session_id") or "").strip()
    if not session_id:
        return
    # Cancel the run task first: a gate await blocked on ``asyncio.wait_for``
    # then receives CancelledError and runs its Design-A cancel path (clear
    # highlight + gate.cleared + re-raise), unwinding the whole turn instead of
    # continuing to the next tool. The gate-registry sweep afterwards is a
    # belt-and-suspenders cleanup for any gate not currently being awaited
    # (e.g. a cancel that landed between tool calls).
    task = _AGENT_TASKS.get(session_id)
    if task is not None and not task.done():
        task.cancel()
    try:
        from .agent.gate import gate_registry

        gate_registry.cancel_session(session_id)
    except Exception as e:  # pragma: no cover — defensive
        logger.warning("chat.cancel gate drain failed for %s: %s", session_id, e)
    await manager.broadcast(
        schemas.WsEvent(
            type="run.cancelled",
            payload={"session_id": session_id},
            correlation_id=corr,
        )
    )


async def _handle_highlight_reference(
    ws: WebSocket, payload: dict, corr: Optional[str]
) -> None:
    """Show a transient, NON-MUTATING highlight of a referenced geometry.

    Frontend sends this when the user hovers an inline reference token in a
    chat message (component pick, point pick, or selection). The viewport
    overlay is drawn via a DisplayConduit — no selection change, no document
    mutation, so the selection_watcher / study logging stays untouched.

    Marshalled onto Rhino's UI thread (view APIs must run there), matching
    the ``_handle_sketch_window`` pattern. Bulletproof-defensiv: import
    failures, ``RHINO_AVAILABLE=False``, or a marshaling error are logged and
    swallowed — a hover preview must never break the WS dispatch loop and
    never surfaces a viewport.error to the client (it's a passive aid).
    """
    block = payload.get("block")
    if not isinstance(block, dict):
        return
    try:
        from .viewport_bridge import highlight as highlight_mod
    except Exception as e:  # pragma: no cover — defensive
        logger.debug("highlight import failed (%s) — skipping", e)
        return
    if not getattr(highlight_mod, "RHINO_AVAILABLE", False):
        return
    try:
        import Rhino  # type: ignore
        import System  # type: ignore

        def _run() -> None:
            try:
                highlight_mod.highlight_reference(block)
            except Exception as inner:  # pragma: no cover — UI-thread defensive
                logger.debug("highlight_reference UI run failed: %s", inner)

        Rhino.RhinoApp.InvokeOnUiThread(System.Action(_run))
    except Exception as e:  # pragma: no cover — defensive
        logger.debug("highlight_reference dispatch failed: %s", e)


async def _handle_highlight_clear(
    ws: WebSocket, payload: dict, corr: Optional[str]
) -> None:
    """Clear the transient hover highlight (hover-out / token unmount).

    Same defensive UI-thread marshaling as ``_handle_highlight_reference``.
    Idempotent on the bridge side, so a stray clear with nothing highlighted
    is a harmless no-op.
    """
    try:
        from .viewport_bridge import highlight as highlight_mod
    except Exception as e:  # pragma: no cover — defensive
        logger.debug("highlight import failed (%s) — skipping clear", e)
        return
    if not getattr(highlight_mod, "RHINO_AVAILABLE", False):
        return
    try:
        import Rhino  # type: ignore
        import System  # type: ignore

        def _run() -> None:
            try:
                highlight_mod.clear_highlight()
            except Exception as inner:  # pragma: no cover — UI-thread defensive
                logger.debug("clear_highlight UI run failed: %s", inner)

        Rhino.RhinoApp.InvokeOnUiThread(System.Action(_run))
    except Exception as e:  # pragma: no cover — defensive
        logger.debug("highlight_clear dispatch failed: %s", e)


async def _handle_point_markers(
    ws: WebSocket, payload: dict, corr: Optional[str]
) -> None:
    """Set/replace the persistent point-reference markers (small orange dots at
    the points the user has picked), or clear them (empty list).

    Non-mutating DisplayConduit overlay — same defensive UI-thread marshaling
    as the hover highlight, and never surfaces a viewport.error to the client
    (it's a passive reference aid).
    """
    points = payload.get("points")
    if not isinstance(points, list):
        points = []
    # Diagnostic: confirms the command passed WsCommand validation and reached
    # the handler (the marker count tells us the frontend payload arrived).
    logger.info("viewport.point_markers: %d point(s)", len(points))
    try:
        from .viewport_bridge import highlight as highlight_mod
    except Exception as e:  # pragma: no cover — defensive
        logger.debug("highlight import failed (%s) — skipping point markers", e)
        return
    if not getattr(highlight_mod, "RHINO_AVAILABLE", False):
        return
    try:
        import Rhino  # type: ignore
        import System  # type: ignore

        def _run() -> None:
            try:
                highlight_mod.set_point_markers(points)
            except Exception as inner:  # pragma: no cover — UI-thread defensive
                logger.debug("set_point_markers UI run failed: %s", inner)

        Rhino.RhinoApp.InvokeOnUiThread(System.Action(_run))
    except Exception as e:  # pragma: no cover — defensive
        logger.debug("point_markers dispatch failed: %s", e)


async def _handle_persistent_refs(
    ws: WebSocket, payload: dict, corr: Optional[str]
) -> None:
    """Set/replace the persistent THIN component highlight — the staged K/F/O
    picks stay thin-blue while their tokens are in the composer — or clear it
    (empty list). Non-mutating DisplayConduit, UI-thread marshaled, passive
    (never surfaces a viewport.error)."""
    blocks = payload.get("blocks")
    if not isinstance(blocks, list):
        blocks = []
    logger.info("viewport.persistent_refs: %d ref(s)", len(blocks))
    try:
        from .viewport_bridge import highlight as highlight_mod
    except Exception as e:  # pragma: no cover — defensive
        logger.debug("highlight import failed (%s) — skipping persistent refs", e)
        return
    if not getattr(highlight_mod, "RHINO_AVAILABLE", False):
        return
    try:
        import Rhino  # type: ignore
        import System  # type: ignore

        def _run() -> None:
            try:
                highlight_mod.set_persistent_references(blocks)
            except Exception as inner:  # pragma: no cover — UI-thread defensive
                logger.debug("set_persistent_references UI run failed: %s", inner)

        Rhino.RhinoApp.InvokeOnUiThread(System.Action(_run))
    except Exception as e:  # pragma: no cover — defensive
        logger.debug("persistent_refs dispatch failed: %s", e)


async def _handle_snapshot_request(
    ws: WebSocket, payload: dict, corr: Optional[str]
) -> None:
    """Run a viewport snapshot off the event loop and broadcast the result.

    Default behaviour is the labelled 2x2 multi-view composite (perspective
    + top + front + right with ZoomExtents) — the designer's chat camera
    button shouldn't have to know about multi-view, it just gets one
    information-rich attachment per click. Clients can override via
    explicit ``views``/``fit`` payload entries.
    """
    from .viewport_bridge import (
        RHINO_AVAILABLE,
        SUPPORTED_VIEWS,
        VALID_FIT_MODES,
        capture_composite,
    )

    if not RHINO_AVAILABLE:
        await _viewport_unavailable(ws, "snapshot", corr)
        return

    max_size = _clamp_max_size(payload, 1024)
    show_annotations = bool(payload.get("show_annotations", False))
    raw_views = payload.get("views") or ["current", "top", "front", "right"]
    if not isinstance(raw_views, list) or not raw_views:
        raw_views = ["current", "top", "front", "right"]
    views = [str(v) for v in raw_views if isinstance(v, str)]
    bad = [v for v in views if v not in SUPPORTED_VIEWS]
    if bad:
        await manager.send_to(
            ws,
            schemas.WsEvent(
                type="viewport.error",
                payload={
                    "source": "snapshot",
                    "message": "Unbekannte Ansicht(en): {0}".format(
                        ", ".join(bad)
                    ),
                },
                correlation_id=corr,
            ),
        )
        return
    fit = str(payload.get("fit", "extents"))
    if fit not in VALID_FIT_MODES:
        await manager.send_to(
            ws,
            schemas.WsEvent(
                type="viewport.error",
                payload={
                    "source": "snapshot",
                    "message": "Unbekannter fit-Modus: {0}".format(fit),
                },
                correlation_id=corr,
            ),
        )
        return

    try:
        result = await asyncio.to_thread(
            capture_composite,
            views=views,
            max_size=max_size,
            show_annotations=show_annotations,
            fit=fit,
        )
    except Exception as e:
        logger.warning("snapshot failed: %s", e)
        await manager.send_to(
            ws,
            schemas.WsEvent(
                type="viewport.error",
                payload={"source": "snapshot", "message": str(e)},
                correlation_id=corr,
            ),
        )
        return

    # Pair the snapshot with a fresh get_scene_info dump so the model has
    # IDs/layers/names alongside the image without an extra round-trip.
    # Only on the designer-initiated path (this handler) — model-initiated
    # capture_viewport stays image-only because those are usually just
    # visual verifications where the metadata would be wasted tokens.
    scene_text: Optional[str] = None
    try:
        scene_raw = await dispatch_dedicated_tool("get_scene_info", {})
        if isinstance(scene_raw, str) and scene_raw.strip():
            # The prefix MUST stay aligned with agent.SNAPSHOT_SCENE_PREFIX —
            # the agent's history pruner matches on this exact string to
            # strip outdated scene dumps from older turns.
            scene_text = (
                "Szene-Kontext zum angehaengten Snapshot "
                "(get_scene_info, Stand zum Aufnahmezeitpunkt):\n```\n"
                + scene_raw
                + "\n```"
            )
    except Exception as e:
        logger.warning("get_scene_info for snapshot failed: %s", e)

    payload_out: dict = {
        "source": {
            "type": "base64",
            "media_type": result["media_type"],
            "data": result["data"],
        },
        "width": result["width"],
        "height": result["height"],
    }
    if scene_text:
        payload_out["scene_text"] = scene_text

    await manager.broadcast(
        schemas.WsEvent(
            type="viewport.snapshot",
            payload=payload_out,
            correlation_id=corr,
        )
    )


async def _handle_pick_request(
    ws: WebSocket, payload: dict, corr: Optional[str]
) -> None:
    """Run a Rhino object pick modally on the UI thread and broadcast the result.

    rs.GetObjects blocks the UI thread until the user presses Enter or Esc.
    We run it via asyncio.to_thread → InvokeOnUiThread, so the FastAPI event
    loop stays free to service other WebSocket traffic (viewport snapshots
    request during a pick will queue until the pick returns — that's fine,
    pick UX is modal by design).
    """
    from .viewport_bridge import RHINO_AVAILABLE
    from .viewport_bridge.pick import pick_objects

    if not RHINO_AVAILABLE:
        await _viewport_unavailable(ws, "pick", corr)
        return

    mode = str(payload.get("mode", "multi"))
    obj_filter = str(payload.get("filter", "any"))
    session_id = str(payload.get("session_id") or "").strip()

    try:
        result = await asyncio.to_thread(
            pick_objects, mode=mode, object_filter=obj_filter
        )
    except Exception as e:
        logger.warning("pick failed: %s", e)
        await manager.send_to(
            ws,
            schemas.WsEvent(
                type="viewport.error",
                payload={"source": "pick", "message": str(e)},
                correlation_id=corr,
            ),
        )
        return

    # Panel-WebView nach dem modalen Pick wieder fokussieren -> direkt weitertippen.
    _focus_panel_soon()

    await manager.broadcast(
        schemas.WsEvent(
            type="viewport.pick_result",
            payload={
                "object_ids": result["object_ids"],
                "names": result["names"],
                "types": result["types"],
                "snapshot": result["snapshot"],
            },
            correlation_id=corr,
        )
    )
    if session_id and result["object_ids"]:
        await sync_structure_context_from_selection(
            session_id,
            list(result["object_ids"]),
            corr,
        )


async def _handle_point_request(
    ws: WebSocket, payload: dict, corr: Optional[str]
) -> None:
    """Single-point pick on Rhino's UI thread. Unlike object picking this
    returns on the first click (no Enter required) and uses Rhino's snap
    settings, so the user can lock onto endpoints / midpoints / centers of
    existing geometry. Cancelled pick → ``point: None``."""
    from .viewport_bridge import RHINO_AVAILABLE
    from .viewport_bridge.pick import pick_point

    if not RHINO_AVAILABLE:
        await _viewport_unavailable(ws, "point", corr)
        return

    try:
        result = await asyncio.to_thread(pick_point)
    except Exception as e:
        logger.warning("point pick failed: %s", e)
        await manager.send_to(
            ws,
            schemas.WsEvent(
                type="viewport.error",
                payload={"source": "point", "message": str(e)},
                correlation_id=corr,
            ),
        )
        return

    # Panel-WebView nach dem modalen Pick wieder fokussieren -> direkt weitertippen.
    _focus_panel_soon()

    await manager.broadcast(
        schemas.WsEvent(
            type="viewport.point_result",
            payload={
                "point": result["point"],
                "snapshot": result["snapshot"],
                "object_id": result["object_id"],
                "object_name": result["object_name"],
                "object_type": result["object_type"],
                "snap_type": result["snap_type"],
            },
            correlation_id=corr,
        )
    )


async def _broadcast_component_item(
    item: dict,
    session_id: str,
    corr: Optional[str],
    nest_target: Optional[str] = None,
) -> None:
    """Broadcast EIN viewport.component_result (Frontend stagt ein Inline-Token)
    + Strukturkontext-Sync. Geteilt von Einzel-Pick, Batch-Fallback und dem
    Live-Stream der Mehrfachauswahl. ``nest_target="command"`` weist das Frontend
    an, den Pick live in den Befehls-Chip (Parametrisieren) zu nisten statt als
    freistehenden Referenz-Token."""
    await manager.broadcast(
        schemas.WsEvent(
            type="viewport.component_result",
            payload={
                "point": item["point"],
                "pick_point": item["pick_point"],
                "component_type": item["component_type"],
                "component_index": item["component_index"],
                "geometry_class": item.get("geometry_class"),
                "component_info": item["component_info"],
                "snapshot": item["snapshot"],
                "object_id": item["object_id"],
                "object_name": item["object_name"],
                "object_type_name": item["object_type_name"],
                "object_class": item["object_class"],
                "allowed_operations": item["allowed_operations"],
                "nest_target": nest_target,
            },
            correlation_id=corr,
        )
    )
    if session_id and item.get("object_id"):
        try:
            blk = schemas.ComponentPickBlock.model_validate(
                {
                    "type": "component_pick",
                    "component_type": item.get("component_type"),
                    "component_index": item.get("component_index"),
                    "geometry_class": item.get("geometry_class"),
                    "point": item.get("point") or [],
                    "pick_point": item.get("pick_point"),
                    "component_info": item.get("component_info") or {},
                    "snapshot": item.get("snapshot"),
                    "object_id": item.get("object_id"),
                    "object_name": item.get("object_name"),
                    "object_type_name": item.get("object_type_name"),
                    "object_class": item.get("object_class"),
                    "allowed_operations": item.get("allowed_operations") or [],
                }
            )
        except Exception:
            blk = None
        await sync_structure_context_from_component_pick(session_id, blk, corr)


# Fire-and-forget-Tasks (z.B. der Persistent-Reapply nach einem Pick) hier
# halten, damit der Event-Loop sie nicht vorzeitig garbage-collected.
_BG_TASKS: set = set()


async def _reapply_persistent_after_pick() -> None:
    """Nach einem MODALEN Komponenten-Pick die persistente Markierung neu
    anstossen, sobald der UI-Thread frei + die letzte persistent_refs-Nachricht
    verarbeitet ist. Bei der Shift-Mehrfachauswahl streamt das Frontend
    persistent_refs WAEHREND des modalen gp.Get()-Loops; deren UI-Run wird hinter
    den Pick gequeued und zeichnet nach Modal-Ende nicht zuverlaessig nach. Ein
    kurzer Verzug + ein erneutes set_persistent_references(zuletzt) raeumt das
    auf (siehe highlight.reapply_persistent_references)."""
    await asyncio.sleep(0.08)
    try:
        from .viewport_bridge import highlight as highlight_mod
    except Exception:  # pragma: no cover — defensive
        return
    if not getattr(highlight_mod, "RHINO_AVAILABLE", False):
        return
    try:
        import Rhino  # type: ignore
        import System  # type: ignore

        def _run() -> None:
            try:
                highlight_mod.reapply_persistent_references()
            except Exception as inner:  # pragma: no cover — UI-thread defensive
                logger.debug("reapply persistent UI run failed: %s", inner)

        Rhino.RhinoApp.InvokeOnUiThread(System.Action(_run))
    except Exception as e:  # pragma: no cover — defensive
        logger.debug("reapply persistent dispatch failed: %s", e)


def _focus_panel_soon() -> None:
    """Dem Panel-WebView nach einem modalen Pick (GetPoint/GetObject) den Fokus
    zurueckgeben, damit der Nutzer direkt weiter ins Eingabefeld tippen kann,
    ohne erst ins Panel klicken zu muessen. Der modale Pick hat den Fokus an den
    Rhino-Viewport abgegeben. Best-effort, fire-and-forget auf den UI-Thread."""
    try:
        import panel  # plugin/ ist auf sys.path
        import Rhino  # type: ignore
        import System  # type: ignore
    except Exception:
        return
    try:
        Rhino.RhinoApp.InvokeOnUiThread(System.Action(panel.focus_webview))
    except Exception as e:  # pragma: no cover — defensive
        logger.debug("focus panel dispatch failed: %s", e)


async def _handle_component_request(
    ws: WebSocket, payload: dict, corr: Optional[str]
) -> None:
    """Pick a single edge, face, or whole object as a viewport reference."""
    from .viewport_bridge import RHINO_AVAILABLE
    from .viewport_bridge.pick import pick_component

    if not RHINO_AVAILABLE:
        await _viewport_unavailable(ws, "component", corr)
        return

    raw_component_type = str(payload.get("component_type") or "auto").strip().lower()
    session_id = str(payload.get("session_id") or "").strip()
    component_type = (
        raw_component_type
        if raw_component_type in {"edge", "face", "vertex", "auto"}
        else "auto"
    )
    # Parametrisieren-Objekt-Pick: ganzes Objekt erzwingen (force_object) + die
    # Picks live in den Befehls-Chip nisten (nest_target="command") statt als
    # freistehende Referenz-Tokens. Beides nur gesetzt, wenn das Frontend es
    # explizit anfordert; sonst identisch zum K/F/O-Pick.
    force_object = bool(payload.get("force_object"))
    nest_target = payload.get("nest_target")
    if nest_target != "command":
        nest_target = None

    loop = asyncio.get_running_loop()

    def _emit(item: dict) -> None:
        # Vom UI-Thread-Pick-Loop pro gewaehlter Komponente aufgerufen -> sofort
        # (live) broadcasten statt am Ende gebuendelt. run_coroutine_threadsafe
        # marshalt vom UI-Thread auf den Event-Loop (fire-and-forget).
        try:
            asyncio.run_coroutine_threadsafe(
                _broadcast_component_item(item, session_id, corr, nest_target), loop
            )
        except Exception:
            pass

    try:
        result = await asyncio.to_thread(
            pick_component,
            component_type=component_type,
            on_component=_emit,
            force_object=force_object,
        )
    except Exception as e:
        logger.warning("component pick failed: %s", e)
        await manager.send_to(
            ws,
            schemas.WsEvent(
                type="viewport.error",
                payload={"source": "component", "message": str(e)},
                correlation_id=corr,
            ),
        )
        return

    # Panel-WebView nach dem modalen Pick wieder fokussieren -> direkt weitertippen.
    _focus_panel_soon()

    # Nach dem (modalen) Pick die persistente Markierung neu anstossen, wenn die
    # Picks live in den Befehls-Chip genistet wurden (nest_target="command").
    # Hintergrund-Task -> blockiert das Broadcasting der Pick-Ergebnisse unten
    # nicht; der kurze Verzug in _reapply_persistent_after_pick wartet die letzte
    # persistent_refs-Nachricht ab.
    if nest_target == "command":
        _t = asyncio.create_task(_reapply_persistent_after_pick())
        _BG_TASKS.add(_t)
        _t.add_done_callback(_BG_TASKS.discard)

    # Pre-selection shortcut: if the user already had objects selected when the
    # component pick was triggered, pick_component returns a plain selection
    # dict (kind="selection") instead of a ComponentPickResult.  Route it
    # through the same path as a regular multi-object pick so the frontend
    # receives a viewport.pick_result event (which it already handles).
    if result.get("kind") == "selection":
        await manager.broadcast(
            schemas.WsEvent(
                type="viewport.pick_result",
                payload={
                    "object_ids": result["object_ids"],
                    "names": result["names"],
                    "types": result["types"],
                    "snapshot": result["snapshot"],
                },
                correlation_id=corr,
            )
        )
        if session_id and result.get("object_ids"):
            await sync_structure_context_from_selection(
                session_id,
                list(result["object_ids"]),
                corr,
            )
        return

    # Live-Stream (Shift-Mehrfachauswahl): die Komponenten wurden bereits einzeln
    # ueber _emit (run_coroutine_threadsafe) rausgeschickt -> hier nichts mehr.
    if result.get("kind") == "streamed":
        return

    # Batch-Fallback (ohne Live-Stream): {"kind":"components","items":[...]}.
    if result.get("kind") == "components":
        for item in result.get("items", []):
            await _broadcast_component_item(item, session_id, corr, nest_target)
        return

    # Einzel-Pick (leeres Resultat -> point=None, das Frontend ignoriert es).
    await _broadcast_component_item(result, session_id, corr, nest_target)


_SKETCH_VIEW_LABELS = {
    "perspective": "Perspektive",
    "top": "Top",
    "front": "Front",
    "right": "Right",
}


async def _handle_sketch_request(
    ws: WebSocket, payload: dict, corr: Optional[str]
) -> None:
    """Grab viewport snapshots to serve as the sketch overlay's backdrop.

    Unlike pick, sketch is an all-frontend flow: the user draws on
    an HTML canvas, the composite PNG is built client-side via canvas APIs,
    and a SketchBlock is staged locally as an attachment. The backend's only
    job here is handing the frontend a current view of the model to draw on.

    Sketch and snapshot are now one tool: instead of a single view we capture
    the four standard views (Perspektive / Top / Front / Right) in one
    UI-thread round-trip plus a labelled multi-view composite. The frontend's
    view picker chooses which single view the user draws on; the composite
    rides along as spatial context.
    """
    from .viewport_bridge import (
        RHINO_AVAILABLE,
        capture_snapshots,
        compose_views,
    )

    if not RHINO_AVAILABLE:
        await _viewport_unavailable(ws, "sketch", corr)
        return

    # Higher default resolution than a plain snapshot — the user draws on this
    # and the result ends up in front of the model as annotated context, so
    # legibility matters more than bandwidth. Default capped at 1000 (down
    # from 1200) because the composite is stitched from four images and would
    # otherwise grow too large. `show_annotations` stays off: the sketch is
    # the annotation.
    max_size = _clamp_max_size(payload, 1000)
    view_names = ["perspective", "top", "front", "right"]

    try:
        snaps = await asyncio.to_thread(
            capture_snapshots,
            views=view_names,
            max_size=max_size,
            fit="extents",
        )
        composite = await asyncio.to_thread(compose_views, snaps)
    except Exception as e:
        logger.warning("sketch snapshot failed: %s", e)
        await manager.send_to(
            ws,
            schemas.WsEvent(
                type="viewport.error",
                payload={"source": "sketch", "message": str(e)},
                correlation_id=corr,
            ),
        )
        return

    await manager.send_to(
        ws,
        schemas.WsEvent(
            type="viewport.sketch_ready",
            payload={
                "views": [
                    {
                        "name": vname,
                        "label": _SKETCH_VIEW_LABELS[vname],
                        "source": {
                            "type": "base64",
                            "media_type": s["media_type"],
                            "data": s["data"],
                        },
                        "width": s["width"],
                        "height": s["height"],
                    }
                    for vname, s in zip(view_names, snaps)
                ],
                "composite": {
                    "source": {
                        "type": "base64",
                        "media_type": composite["media_type"],
                        "data": composite["data"],
                    },
                    "width": composite["width"],
                    "height": composite["height"],
                },
            },
            correlation_id=corr,
        ),
    )


# Per-(session, parameter) coalescing state. The WS handler returns
# immediately on every drag-tick: the FIRST message in a drag spawns a
# background worker, subsequent messages only update ``_pending_param_value``
# (the latest target). When the worker finishes one apply it picks up the
# newest pending value and runs again, skipping every intermediate. That
# way a 300ms-per-tick Rhino round-trip stops queuing up — the user sees
# the first frame and the final frame, intermediates are dropped without
# ever hitting Rhino. This is intentionally in-process state: the plugin
# backend runs as one local FastAPI/Uvicorn process inside Rhino. If this
# ever becomes a multi-worker deployment, coalescing must move into a
# shared per-session store or be disabled.
_pending_param_value: dict[tuple[str, str], tuple[float, Optional[str]]] = {}
_in_flight_param: set[tuple[str, str]] = set()


async def _drain_parameter_changes(
    session_id: str, name: str, initial_value: float, initial_corr: Optional[str]
) -> None:
    """Background worker that applies the parameter, then loops on pending.

    Mid-drag ticks set ``defer_refresh=True`` so the heavy structure-context
    re-read doesn't run per tick — that round-trip back to Rhino was the
    main per-tick latency. Once the drain loop exits (no more pending
    values), we do a single refresh so the structure context catches the
    final state.
    """
    from . import dedicated_tools
    from .structure_context_service import refresh_structure_context_in_store

    key = (session_id, name)
    value, corr = initial_value, initial_corr
    store = get_store()
    last_recipe_targets: list[str] = []
    try:
        while True:
            existing = store.get_exposed_parameter(session_id, name)
            old_value = existing.current if existing else None
            try:
                result = await dedicated_tools.apply_parameter_change(
                    session_id=session_id,
                    name=name,
                    new_value=value,
                    correlation_id=corr,
                    defer_refresh=True,
                )
            except Exception as e:
                logger.exception("parameter.changed worker failed: %s", e)
                # Pending-Badge zuverlaessig loesen: ohne dieses Broadcast bliebe
                # bei einer unerwarteten Worker-Exception KEIN parameter.updated
                # uebrig -> der Slider-Wert bliebe im Frontend dauerhaft gedimmt
                # (markParameterPending bleibt true) bis zum naechsten Turn-
                # Abschluss/Reconnect. ok=False loest Pending + zeigt den Fehler.
                await manager.broadcast(
                    schemas.WsEvent(
                        type="parameter.updated",
                        payload={
                            "session_id": session_id,
                            "name": name,
                            "new_value": value,
                            "current_value": (
                                old_value if old_value is not None else value
                            ),
                            "ok": False,
                            "message": "Slider-Aktion fehlgeschlagen: {0}".format(e),
                        },
                        correlation_id=corr,
                    )
                )
                break

            if result.get("ok"):
                applied_value = float(result.get("current_value", value))
                try:
                    store.log_parameter_change(
                        schemas.ParameterChangeRecord(
                            session_id=session_id,
                            parameter_name=name,
                            old_value=old_value,
                            new_value=applied_value,
                            source="user",
                        )
                    )
                except Exception as e:
                    logger.warning("log_parameter_change failed: %s", e)

            await manager.broadcast(
                schemas.WsEvent(
                    type="parameter.updated",
                    payload={
                        "session_id": session_id,
                        "name": name,
                        "new_value": value,
                        "current_value": result.get("current_value", value),
                        "ok": bool(result.get("ok")),
                        "message": result.get("message", ""),
                    },
                    correlation_id=corr,
                )
            )

            # Pick up a newer pending value if the user kept dragging
            # while we were busy in Rhino. Otherwise we're done.
            if key in _pending_param_value:
                value, corr = _pending_param_value.pop(key)
                continue
            # Final value processed — do the single deferred refresh
            # now so the active structure context reflects the new
            # geometry. We pull recipe targets from the parameter's
            # actions because apply_parameter_change doesn't return
            # them; this keeps the existing signature unchanged.
            try:
                final_param = store.get_exposed_parameter(session_id, name)
                if final_param is not None:
                    targets: list[str] = []
                    for action in getattr(final_param, "actions", None) or []:
                        if (
                            getattr(action, "type", "") == "editable_recipe_value"
                            and getattr(action, "target_object_ids", None)
                        ):
                            targets.append(action.target_object_ids[0])
                    if targets:
                        refresh_structure_context_in_store(
                            session_id,
                            object_ids=targets,
                            source="tool_result",
                        )
            except Exception as e:
                logger.warning(
                    "deferred structure refresh failed: %s", e
                )
            # Slider-Drag ist geometrieveraendernd (Spec §2.4 "Immer Snapshot:
            # set_parameter"), laeuft aber ueber diesen WS-Worker statt ueber
            # den Tool-Dispatch und hinterlaesst daher sonst KEINEN
            # model_states-Snapshot. Einmal am Drain-Ende fuer den finalen Wert
            # nachholen (NICHT pro Tick — sonst Timeline-Flut + UI-Thread-
            # Blockade), nur im Studienmodus und nur bei echter Aenderung.
            try:
                if result.get("ok") and not result.get("no_op"):
                    study = store.get_study_session_for_session(session_id)
                    if study is not None and getattr(study, "status", None) == "active":
                        from .agent.dispatch import fire_model_state_snapshot

                        fire_model_state_snapshot(session_id, trigger="parameter")
            except Exception as e:
                logger.warning("slider model_state snapshot failed: %s", e)
            return
    finally:
        _in_flight_param.discard(key)
        _pending_param_value.pop(key, None)


async def _handle_parameter_changed(
    ws: WebSocket, payload: dict, corr: Optional[str]
) -> None:
    session_id = payload.get("session_id")
    name = payload.get("name")
    new_value = payload.get("value")
    if not session_id or name is None or new_value is None:
        await manager.send_to(
            ws,
            schemas.WsEvent(
                type="error",
                payload={
                    "message": "parameter.changed braucht session_id, name und value."
                },
                correlation_id=corr,
            ),
        )
        return

    try:
        new_value = float(new_value)
    except (TypeError, ValueError):
        await manager.send_to(
            ws,
            schemas.WsEvent(
                type="error",
                payload={"message": f"value '{new_value}' ist keine Zahl."},
                correlation_id=corr,
            ),
        )
        return

    key = (str(session_id), str(name))
    # Drag in progress: just record the latest target and let the running
    # worker pick it up. Returning immediately keeps the WS event loop
    # responsive for the next slider tick.
    if key in _in_flight_param:
        _pending_param_value[key] = (new_value, corr)
        return

    _in_flight_param.add(key)
    asyncio.create_task(
        _drain_parameter_changes(
            session_id=str(session_id),
            name=str(name),
            initial_value=new_value,
            initial_corr=corr,
        )
    )


async def _handle_parameter_clear(
    ws: WebSocket, payload: dict, corr: Optional[str]
) -> None:
    session_id = payload.get("session_id")
    if not session_id:
        return
    # × auf dem Panel = der Designer will es WEG. Ein nackter Clear loescht zwar
    # die exponierten Parameter, laesst aber bei GH-Panels den aktiven GH-
    # Strukturkontext stehen -> der naechste sync_session_parameters re-derived
    # die gh_slider-Parameter und das Panel ploppt zurueck (Symptom: "GH-Slider
    # lassen sich nicht wegklicken"). clear_grasshopper_context=True schickt GH-
    # Panels durch den Voll-Clear-Zweig (Kontext + Parameter, Broadcast cleared).
    # Selbstheilend: ein bewusstes Re-Expose der KI loest den GH-Kontext via
    # apply_parameter_set wieder auf. Nicht-GH-Panels bleiben unberuehrt (der
    # Zweig greift nur bei structure_type == "grasshopper").
    await clear_session_parameters(
        session_id,
        corr,
        clear_grasshopper_context=True,
        preserve_router_priority=False,
    )


async def _handle_variant_select_ws(
    ws: WebSocket, payload: dict, corr: Optional[str]
) -> None:
    session_id = payload.get("session_id")
    name = payload.get("name")
    if not session_id or not name:
        return

    from . import dedicated_tools

    await dedicated_tools.dispatch_dedicated_tool(
        "select_variant",
        {"name": name},
        session_id=session_id,
        correlation_id=corr,
    )
    # FF1/FF3-Spur: die Galerie-Variantenwahl laeuft ueber diesen WS-Handler
    # (keine tool_calls-Zeile), daher als eigenes Studien-Event festhalten.
    _log_affordance_event(session_id, "variant_selected", {"name": name})


async def _handle_variant_delete_ws(
    ws: WebSocket, payload: dict, corr: Optional[str]
) -> None:
    session_id = payload.get("session_id")
    name = payload.get("name")
    if not session_id or not name:
        return

    from . import dedicated_tools

    await dedicated_tools.dispatch_dedicated_tool(
        "delete_variant",
        {"name": name},
        session_id=session_id,
        correlation_id=corr,
    )


async def _handle_variant_show_original_ws(
    ws: WebSocket, payload: dict, corr: Optional[str]
) -> None:
    session_id = payload.get("session_id")
    if not session_id:
        return

    from . import dedicated_tools

    await dedicated_tools.show_original(session_id, correlation_id=corr)


async def _handle_variants_clear_ws(
    ws: WebSocket, payload: dict, corr: Optional[str]
) -> None:
    session_id = payload.get("session_id")
    if not session_id:
        return

    from . import dedicated_tools

    await dedicated_tools.dispatch_dedicated_tool(
        "clear_variants",
        {},
        session_id=session_id,
        correlation_id=corr,
    )


async def _handle_variant_commit_ws(
    ws: WebSocket, payload: dict, corr: Optional[str]
) -> None:
    """Designer closed the gallery with a variant selected: keep it as the main
    model (promote to 'Active') instead of discarding. WS-only path — see
    dedicated_tools.commit_active_variant (no Tool-Schema/Hash touched)."""
    session_id = payload.get("session_id")
    if not session_id:
        return

    from . import dedicated_tools

    await dedicated_tools.commit_active_variant(session_id, correlation_id=corr)


# ---------------------------------------------------------------------------
# Static frontend
# ---------------------------------------------------------------------------

if os.path.isdir(_WEB_DIST):
    app.mount("/app", StaticFiles(directory=_WEB_DIST, html=True), name="frontend")
else:
    @app.get("/app")
    def frontend_placeholder() -> dict:
        return {
            "status": "frontend not built",
            "hint": "run `npm install && npm run build` inside plugin/web/",
            "expected_dir": _WEB_DIST,
        }
