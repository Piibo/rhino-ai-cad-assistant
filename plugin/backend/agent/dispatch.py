"""agent.dispatch - Tool-Dispatch + Built-in-Tools + Lock/Parameter/Logging."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import traceback
from datetime import datetime, timezone
from typing import Any

from .. import dedicated_tools, schemas
from ..session_store import get_store
from ..websocket_manager import manager
from ..tool_registry import (
    DIALOG_TOOL_NAMES,
    INTERACTION_TOOL_NAMES,
    LOCK_TOOL_NAMES,
    is_modifying_tool,
)
from ..component_router import (
    _augment_tool_input_from_recent_component_picks,
    _guard_tool_from_recent_component_picks,
    _route_tool_from_recent_component_picks,
    _trigger_refs_for_tool,
)
from .prompts import _PARAMETER_INVALIDATING_TOOLS, _ERROR_PREFIXES
from .gate import GATE_TIMEOUT, describe, gate_registry, highlight_targets, should_gate

logger = logging.getLogger("FurniturePlugin.Agent")

# Strong refs to fire-and-forget model_states snapshot tasks. asyncio keeps only
# a weak reference to bare create_task() results, so without this the snapshot
# writer could be garbage-collected before it logs — dropping rows from the
# study's per-tool model-state timeline. Discarded on completion.
_SNAPSHOT_TASKS: set = set()

# Tools whose only purpose is to repair / revert prior edits. Marked on the
# tool_calls row (is_repair) so the HITL-loop can be auswertbar without
# re-deriving intent from tool names downstream (P3, 15.06.2026).
REPAIR_TOOL_NAMES = {"undo_last_action", "redo_last_action"}

# Tool-Result text returned for a gated operation the designer rejected
# (revert / Timeout). The model is told NOT to silently retry — it should ask
# how to proceed instead. Also used as the marker substring to flag the
# tool_calls row as a skip in _persist_tool_call_and_maybe_snapshot.
GATE_SKIP_RESULT = (
    "Uebersprungen: der Designer hat diese Operation verworfen. Diese "
    "Aenderung wurde NICHT ausgefuehrt; nachfolgende Schritte laufen daher auf "
    "der urspruenglichen Geometrie, nicht auf dem Ergebnis dieses Schritts. "
    "Frage nach, wie es weitergehen soll, statt sie erneut zu versuchen."
)

# Tool list is built per session via ``build_tool_list(condition)`` so the
# basis / werkzeug study condition (Studienartefakt-Spec §1.1) decides
# which tools the model sees. The schemas live in ``tool_registry`` and
# ``dedicated_tools`` so this module stays focused on the agent loop.


# ---------------------------------------------------------------------------
# Auto-logging hooks (Studienartefakt-Spec §2.4 writers)
# ---------------------------------------------------------------------------


def _snapshots_dir() -> str:
    """Directory where per-snapshot PNGs land before they ship in the
    export bundle. Lives next to plugin.db so a single OS path covers
    both data sources."""
    from ..config import _CONFIG_DIR

    return os.path.join(_CONFIG_DIR, "snapshots")


def _tool_result_text(result_content: Any) -> str | None:
    """Serialise a tool result for the ``tool_calls.result_json`` column.

    Anthropic's tool_result content is either a string or a list of
    blocks; we flatten to a single string so the column stays
    text-queryable. Base64 image blobs are redacted so the DB doesn't
    bloat with megabytes per row.
    """
    if result_content is None:
        return None
    if isinstance(result_content, str):
        return result_content
    if isinstance(result_content, list):
        cleaned: list[dict[str, Any]] = []
        for block in result_content:
            if not isinstance(block, dict):
                cleaned.append({"_raw": str(block)})
                continue
            if block.get("type") == "image":
                src = block.get("source") or {}
                data = src.get("data", "")
                cleaned.append(
                    {
                        "type": "image",
                        "media_type": src.get("media_type"),
                        "data_len": len(data) if isinstance(data, str) else 0,
                        "_note": "base64 omitted in tool_calls.result_json",
                    }
                )
            else:
                cleaned.append(block)
        return json.dumps(cleaned, ensure_ascii=False, default=str)
    return json.dumps(result_content, ensure_ascii=False, default=str)


async def _capture_snapshot_for_model_state(
    session_id: str, model_state_id: str
) -> tuple[str | None, dict[str, Any]]:
    """Grab a viewport JPEG + scene summary on the Rhino UI thread.

    Returns ``(viewport_path, rhino_objects)``. Falls back gracefully
    when Rhino isn't reachable (running outside Rhino, capture failed):
    we still want the ``model_states`` row so the trigger-spec timing
    is preserved.
    """
    viewport_path: str | None = None
    rhino_objects: dict[str, Any] = {}
    try:
        from ..viewport_bridge import RHINO_AVAILABLE, capture_snapshot

        if RHINO_AVAILABLE:
            # Auto-snapshots run on plugin initiative (not designer-
            # initiated), so frame the model's geometry instead of
            # whatever zoom the designer left the viewport at. The
            # designer's camera is restored after capture.
            result = await asyncio.to_thread(
                capture_snapshot,
                max_size=1024,
                show_annotations=False,
                fit="extents",
            )
            if isinstance(result, dict) and result.get("data"):
                target_dir = os.path.join(_snapshots_dir(), session_id)
                os.makedirs(target_dir, exist_ok=True)
                ext = (result.get("media_type") or "image/jpeg").split("/")[-1]
                target = os.path.join(target_dir, f"{model_state_id}.{ext}")
                import base64

                with open(target, "wb") as f:
                    f.write(base64.b64decode(result["data"]))
                viewport_path = target
    except Exception as e:
        logger.warning("model_state snapshot failed: %s", e)
    try:
        scene = await dedicated_tools.dispatch_dedicated_tool(
            "get_scene_info", {}
        )
        if isinstance(scene, str):
            # run_code liefert repr(result); get_scene_info setzt result=
            # json.dumps(...). Erst die repr-Huelle via ast.literal_eval abziehen,
            # dann JSON parsen — sonst scheitert json.loads an den Single-Quotes
            # und JEDER model_state landet als roher {"_raw": ...}-String statt als
            # strukturierter Szenenzustand (06.07.2026, gleiche Klasse wie der
            # model.3dm-Parsebug). Fallback auf _raw bleibt fuer echte Fehler.
            import ast as _ast

            _txt = scene.strip()
            try:
                _inner = _ast.literal_eval(_txt)
            except Exception:
                _inner = _txt
            if isinstance(_inner, dict):
                rhino_objects = _inner
            elif isinstance(_inner, str):
                try:
                    rhino_objects = json.loads(_inner)
                except Exception:
                    rhino_objects = {"_raw": _txt[:8000]}
            else:
                rhino_objects = {"_raw": _txt[:8000]}
        elif isinstance(scene, dict):
            rhino_objects = scene
    except Exception as e:
        logger.warning("model_state scene summary failed: %s", e)
    return viewport_path, rhino_objects


def fire_model_state_snapshot(
    session_id: str,
    *,
    trigger: str,
    triggering_tool_call_id: str | None = None,
    label: str | None = None,
) -> None:
    """Fire-and-forget a ``model_states`` snapshot (viewport + scene).

    Shared by the tool-dispatch path and the slider WebSocket worker so a
    geometry change reached by *dragging a slider* lands in the same
    ``model_states`` timeline as one reached by a modifying tool call
    (Spec §2.4 lists ``set_parameter`` under *Immer Snapshot*). The slider
    path carries no ``tool_calls`` row, hence ``trigger="parameter"`` with
    no ``triggering_tool_call_id``.

    We don't await the snapshot — ``capture_snapshot`` blocks the Rhino UI
    thread for ~100–300 ms and would otherwise serialise the caller behind
    it. A strong ref is retained so the task can't be GC'd before it writes
    the ``model_states`` row.
    """
    state = schemas.ModelStateRecord(
        session_id=session_id,
        label=label,
        trigger=trigger,
        triggering_tool_call_id=triggering_tool_call_id,
    )

    async def _do_snapshot() -> None:
        viewport_path, rhino_objects = await _capture_snapshot_for_model_state(
            session_id, state.id
        )
        state.viewport_path = viewport_path
        state.rhino_objects = rhino_objects
        try:
            get_store().log_model_state(state)
        except Exception as e:
            logger.warning("log_model_state failed: %s", e)

    _snapshot_task = asyncio.create_task(_do_snapshot())
    _SNAPSHOT_TASKS.add(_snapshot_task)
    _snapshot_task.add_done_callback(_SNAPSHOT_TASKS.discard)


async def _persist_tool_call_and_maybe_snapshot(
    *,
    session_id: str,
    message_id: str,
    tool_name: str,
    tool_input: dict[str, Any],
    result_content: Any,
    started_at: datetime,
    duration_ms: int,
    has_study_session: bool,
    tu_id: str | None = None,
) -> None:
    """Write the ``tool_calls`` row, and when study mode applies and the
    tool is modifying, fire-and-forget a ``model_states`` snapshot.

    Failures here never propagate — auto-logging is observability, not
    business logic, and must not derail the chat turn.
    """
    store = get_store()
    # Additive HITL-loop metadata (P3). Both are best-effort: a lookup failure
    # must never derail the tool_calls write — auto-logging is observability.
    triggered_by = None
    try:
        triggered_by = _trigger_refs_for_tool(tool_name, tool_input, session_id)
    except Exception as e:
        logger.warning("trigger-ref lookup failed for %s: %s", tool_name, e)
    # Gate-Skip-Marker (Part C): ein wegen Gate-Verwerfung NICHT ausgefuehrter
    # destruktiver Tool-Call soll im tool_calls-Log auftauchen (auswertbar fuer
    # FF2/Kontrolle) — analog is_repair, aber OHNE model_states-Snapshot, weil
    # keine Mutation passiert ist. Erkannt am Skip-Result-Marker, der nur vom
    # Gate-Revert-Pfad zurueckgegeben wird.
    # HINWEIS zur Auswertung: Seit "eine Entscheidung pro Operation" trifft der
    # Designer pro Run GENAU EINE Gate-Entscheidung; bei mehreren destruktiven
    # Schritten erbt der Rest des Runs diese Entscheidung. Auf einem revert
    # tragen daher ggf. MEHRERE Zeilen is_gate_skip, obwohl nur die erste eine
    # aktive Verwerfung war. is_gate_skip ist also pro-Run-Entscheidung zu
    # lesen, nicht als Zahl aktiver Klicks (Run-Gruppierung noetig). Eine
    # explizite Provenance (interactive vs. cache-aufgeloest) ist bewusst NICHT
    # geloggt — siehe dev-notes/Part C "offen".
    is_gate_skip = (
        isinstance(result_content, str) and result_content == GATE_SKIP_RESULT
    )
    # Gate-Provenance (F2): war die Gate-Entscheidung DIESES Schritts aus der
    # Run-Entscheidung geerbt (True) statt aktiv getroffen? Macht im Export die
    # EINE echte Entscheidung von den geerbten unterscheidbar (is_gate_skip ist
    # pro-Run zu lesen). None = kein gegatetes Tool. pop-on-read (kein Leak).
    is_gate_autoresolved = bool(
        gate_registry.pop_decision_provenance(session_id, tu_id)
    )
    record = schemas.ToolCallRecord(
        session_id=session_id,
        message_id=message_id,
        tool_name=tool_name,
        args=tool_input if isinstance(tool_input, dict) else {"_raw": str(tool_input)},
        result=_tool_result_text(result_content),
        started_at=started_at,
        duration_ms=duration_ms,
        triggered_by=triggered_by,
        is_repair=tool_name in REPAIR_TOOL_NAMES,
        is_gate_skip=is_gate_skip,
        is_gate_autoresolved=is_gate_autoresolved,
    )
    try:
        store.log_tool_call(record)
    except Exception as e:
        logger.warning("log_tool_call failed for %s: %s", tool_name, e)
        return

    if not has_study_session:
        return
    if not is_modifying_tool(tool_name):
        return
    # Ein verworfener (skip) Tool-Call hat nichts mutiert -> kein Snapshot.
    if is_gate_skip:
        return

    fire_model_state_snapshot(
        session_id,
        trigger="tool_call",
        triggering_tool_call_id=record.id,
    )


# ---------------------------------------------------------------------------
# Selektives Vorschau-Gate (Part C, Design A)
# ---------------------------------------------------------------------------


def _gate_highlight(blocks: list[dict], destructive: bool = False) -> None:
    """Stage a non-mutating highlight of the gated geometry in the viewport.

    ``destructive=True`` (Loesch-Ops) faerbt das Overlay orange (Warnung "wird
    entfernt") statt neutral blau — sonst ist die zu loeschende Geometrie visuell
    nicht von einer normalen Aenderung zu unterscheiden.

    ``blocks`` come from ``gate.highlight_targets`` — component_pick blocks for
    component-targeted ops (so ONLY the affected edge/face/vertex lights up,
    not the whole object), a point marker for hole/slot centres, or a
    selection block for whole-object ops. Reuses the hover-highlight
    DisplayConduit (``viewport_bridge.highlight``) via ``highlight_references``
    — same UI-thread path as ``_handle_highlight_reference`` in server.py.
    Best-effort: any failure (no Rhino, import error, marshalling) is
    swallowed; the gate still works without the highlight.
    """
    if not blocks:
        return
    try:
        from ..viewport_bridge import highlight as highlight_mod
    except Exception as e:  # pragma: no cover — defensive
        logger.debug("gate highlight import failed (%s) — skipping", e)
        return
    if not getattr(highlight_mod, "RHINO_AVAILABLE", False):
        return
    try:
        import Rhino  # type: ignore
        import System  # type: ignore

        def _run() -> None:
            try:
                # Dedicated gate conduit — a hover-highlight (and its clear on
                # hover-out) must not wipe the pending gate overlay.
                highlight_mod.highlight_gate_targets(blocks, orange=destructive)
            except Exception as inner:  # pragma: no cover — UI-thread defensive
                logger.debug("gate highlight UI run failed: %s", inner)

        Rhino.RhinoApp.InvokeOnUiThread(System.Action(_run))
    except Exception as e:  # pragma: no cover — defensive
        logger.debug("gate highlight dispatch failed: %s", e)


def _gate_clear_highlight() -> None:
    """Clear the gate highlight overlay (mirror of _gate_highlight)."""
    try:
        from ..viewport_bridge import highlight as highlight_mod
    except Exception as e:  # pragma: no cover — defensive
        logger.debug("gate highlight clear import failed (%s)", e)
        return
    if not getattr(highlight_mod, "RHINO_AVAILABLE", False):
        return
    try:
        import Rhino  # type: ignore
        import System  # type: ignore

        def _run() -> None:
            try:
                highlight_mod.clear_gate_highlight()
            except Exception as inner:  # pragma: no cover — UI-thread defensive
                logger.debug("gate clear_highlight UI run failed: %s", inner)

        Rhino.RhinoApp.InvokeOnUiThread(System.Action(_run))
    except Exception as e:  # pragma: no cover — defensive
        logger.debug("gate clear_highlight dispatch failed: %s", e)


async def _await_gate_decision(
    name: str, tool_input: dict[str, Any], tu_id: str, session_id: str | None = None
) -> tuple[str, str]:
    """Show a preview card, highlight the targets, block until accept/revert.

    Returns "accept" or "revert". A timeout (no decision within GATE_TIMEOUT)
    resolves as "revert" (auto-skip — never hang forever). A cancel
    (asyncio.CancelledError, e.g. chat.cancel cancelling the run task) unwinds
    via the ``finally`` (highlight + card cleared, future resolved) and the
    CancelledError re-propagates so the loop unwinds.

    ``session_id`` is threaded into the registry so ``cancel_session`` can find
    and revert this gate. EVERYTHING after ``register`` runs inside one
    ``try/finally``: even a cancel during ``_gate_highlight``/``broadcast``
    (i.e. BEFORE ``wait_for``) still resolves the future and clears the UI.
    """
    summary, object_ids = describe(name, tool_input)
    # Highlight-Ziele: bei komponenten-gezielten Ops nur die Kante/Flaeche/
    # Vertex, sonst das ganze Objekt (siehe gate.highlight_targets).
    highlight_blocks = highlight_targets(name, tool_input)
    # Loesch-Ops: Ziel-Highlight orange (Warnung "wird entfernt") statt neutral
    # blau. "delete" im Tool-Namen deckt delete_object / delete_* ab; nicht-
    # geometrische Deletes tragen ohnehin keine Highlight-Ziele.
    is_delete = "delete" in name.lower()
    fut = gate_registry.register(tu_id, session_id=session_id)
    try:
        _gate_highlight(highlight_blocks, destructive=is_delete)
        await manager.broadcast(
            schemas.WsEvent(
                type="gate.preview",
                payload={
                    "session_id": session_id,
                    "tu_id": tu_id,
                    "tool_name": name,
                    "summary": summary,
                    "object_ids": object_ids,
                },
            )
        )
        try:
            decision = await asyncio.wait_for(fut, timeout=GATE_TIMEOUT)
            # ("accept"|"revert", "interactive") — eine echte Designer-Entscheidung.
            return (decision, "interactive")
        except asyncio.TimeoutError:
            logger.info(
                "gate %s timed out after %ss — auto-skip (revert)", tu_id, GATE_TIMEOUT
            )
            # Nicht-Entscheid: skippt NUR diesen Schritt, wird NICHT run-weit gecacht.
            return ("revert", "timeout")
    finally:
        # Garantiert auf JEDEM Ausgang (accept/revert/timeout/cancel, auch wenn
        # der Cancel WAEHREND highlight/broadcast vor dem wait_for kam):
        # verwaisten Warter aufloesen, Highlight + Vorschaukarte entfernen.
        if not fut.done():
            gate_registry.resolve(tu_id, "revert")
        _gate_clear_highlight()
        await _safe_broadcast_gate_cleared(tu_id, session_id)


async def _safe_broadcast_gate_cleared(
    tu_id: str, session_id: str | None = None
) -> None:
    try:
        await manager.broadcast(
            schemas.WsEvent(
                type="gate.cleared",
                payload={"tu_id": tu_id, "session_id": session_id},
            )
        )
    except Exception as e:  # pragma: no cover — defensive
        logger.debug("gate.cleared broadcast failed: %s", e)


# ---------------------------------------------------------------------------
# Tool dispatch
# ---------------------------------------------------------------------------


async def _execute_tool(
    name: str,
    tool_input: dict[str, Any],
    session_id: str | None = None,
    correlation_id: str | None = None,
    tu_id: str | None = None,
    condition: str | None = None,
) -> Any:
    name, tool_input = _route_tool_from_recent_component_picks(
        name, tool_input, session_id
    )
    tool_input = _augment_tool_input_from_recent_component_picks(
        name, tool_input, session_id
    )
    guard_error = _guard_tool_from_recent_component_picks(
        name, tool_input, session_id
    )
    if guard_error:
        return guard_error
    # Selektives Vorschau-Gate (Part C, Design A): in der werkzeug-Bedingung
    # hält ein destruktives Tool hier an und wartet auf die Designer-
    # Entscheidung, BEVOR die Mutation passiert. "revert"/Timeout -> KEIN
    # echter Call, ein normales Skip-Result wird zurückgegeben; "accept" ->
    # weiter wie bisher. Defensiv: jeder Fehler im Gate fällt auf normale
    # Ausführung zurück, nie auf einen kaputten Turn.
    if tu_id is not None and should_gate(name, condition):
        cached = gate_registry.get_run_decision(session_id, correlation_id)
        if cached is not None:
            # In diesem Run schon entschieden — eine User-Instruktion ist EIN
            # Vorgang (intern oft mehrere Loeschungen/Aenderungen). Nicht erneut
            # fragen: accept -> ausfuehren, revert -> ueberspringen. Das ist die
            # frueher aufgeschobene "Auto-Skip Rest-Tools im Turn"-Semantik,
            # plus ihr Accept-Pendant.
            decision = cached
            # Geerbte Entscheidung -> fuer die Auswertung als auto-aufgeloest
            # markieren (nicht aktiv geklickt in diesem Schritt).
            gate_registry.set_decision_provenance(session_id, tu_id, True)
        else:
            try:
                decision, source = await _await_gate_decision(
                    name, tool_input, tu_id, session_id
                )
                # Nur eine ECHTE, interaktive Entscheidung run-weit merken.
                # NICHT bei Timeout (Nicht-Entscheid -> wuerde sonst alle
                # Folgeschritte stumm ueberspringen) und NICHT beim Exception-
                # Fallback unten.
                if source == "interactive":
                    gate_registry.set_run_decision(session_id, correlation_id, decision)
                # Eigene Entscheidung dieses Schritts (interaktiv ODER Timeout)
                # -> nicht geerbt.
                gate_registry.set_decision_provenance(session_id, tu_id, False)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # pragma: no cover — Gate darf Turn nie killen
                logger.warning("gate for %s failed (%s) — executing without gate", name, e)
                decision = "accept"
        if decision != "accept":
            return GATE_SKIP_RESULT
    if name == "capture_viewport":
        return await _tool_capture_viewport(tool_input)
    if name == "execute_rhino_code":
        return await _tool_execute_rhino_code(tool_input)
    # Defense in depth (Pilot 01.07.2026 condition-leak): an interaction
    # tool must never actually run in the basis condition. build_tool_list
    # already withholds these from the model in basis; if one still arrives
    # (e.g. a stale build served the full surface), refuse it here instead
    # of rendering a card the basis UI can't show. The non-awaiting result
    # tells the model to proceed — and loop._contains_dialog_request is
    # condition-gated too, so the loop won't stall on a card nobody sees.
    if condition == "basis" and name in INTERACTION_TOOL_NAMES:
        logger.warning(
            "interaction tool %s called in basis condition — refused (leak guard)",
            name,
        )
        return json.dumps(
            {
                "status": "unavailable",
                "message": (
                    "Interaktionswerkzeuge (Dialogkarten, Slider, Varianten) "
                    "stehen in dieser Sitzung nicht zur Verfügung. Triff eine "
                    "sinnvolle Standardannahme und arbeite ohne Karte weiter."
                ),
                "tool": name,
            },
            ensure_ascii=False,
        )
    if name in DIALOG_TOOL_NAMES:
        return json.dumps(
            {
                "status": "awaiting_user",
                "message": "Dialog quick reply card shown.",
                "tool": name,
            },
            ensure_ascii=False,
        )
    # Lock-Panel tools (Studienartefakt-Spec §1.1, P5) mutate plugin
    # state in the locked_objects table, not Rhino itself; they're
    # routed before the dedicated dispatch so dedicated_tools can stay
    # focused on Rhino operations.
    if name in LOCK_TOOL_NAMES:
        return await _tool_lock_dispatch(name, tool_input, session_id)
    # Dedicated tools (create_box, move_object, …) live in dedicated_tools.
    # Session-aware tools (e.g. expose_parameters) also receive session_id
    # and correlation_id so they can update slider state and broadcast WS
    # events without another agent round-trip.
    if dedicated_tools.is_dedicated_tool(name):
        result = await dedicated_tools.dispatch_dedicated_tool(
            name,
            tool_input,
            session_id=session_id,
            correlation_id=correlation_id,
        )
        # Slider lifecycle: after a destructive tool, the slider targets
        # are likely gone — clear the strip so the designer doesn't drag
        # sliders that pump into the void.
        if session_id and name in _PARAMETER_INVALIDATING_TOOLS:
            try:
                await _clear_parameters_after_destructive(session_id, name)
            except Exception as e:
                logger.warning(
                    "auto-clear-params after %s failed: %s", name, e
                )
        return result
    return f"Unbekanntes Tool: {name}"


async def _clear_parameters_after_destructive(
    session_id: str, tool_name: str
) -> None:
    """Drop every exposed slider for ``session_id`` after a destructive tool.

    Safer than per-slider stale checks: even if a slider's target
    object survives the destructive call, the designer can re-expose
    the parameters with one chat turn. Hanging sliders that pump into
    the void are the worse failure mode.
    """
    store = get_store()
    existing = store.get_exposed_parameters(session_id)
    if not existing:
        return
    store.clear_exposed_parameters(session_id)
    await manager.broadcast(
        schemas.WsEvent(
            type="parameter.cleared",
            payload={
                "session_id": session_id,
                "reason": f"auto-cleared after {tool_name}",
            },
        )
    )
    logger.info(
        "auto-cleared %d slider(s) after destructive tool: %s",
        len(existing),
        tool_name,
    )


async def _tool_lock_dispatch(
    name: str, tool_input: dict[str, Any], session_id: str | None
) -> str:
    """Handle lock_object / unlock_object / list_locked_objects.

    Writes go through SessionStore so the lock state survives reloads
    and shows up in the locked_objects JSONL export.
    """
    if not session_id:
        return f"Tool {name} braucht eine aktive Session."
    store = get_store()
    if name == "lock_object":
        ids = tool_input.get("object_ids") or []
        note = str(tool_input.get("note") or "")
        if not isinstance(ids, list) or not ids:
            return "lock_object: object_ids ist leer."
        locked_now: list[str] = []
        for obj_id in ids:
            store.lock_object(
                schemas.LockedObject(
                    session_id=session_id,
                    object_id=str(obj_id),
                    note=note,
                )
            )
            locked_now.append(str(obj_id))
        await manager.broadcast(
            schemas.WsEvent(
                type="settings.updated",  # piggyback so the UI refetches
                payload={"locks_changed": True, "session_id": session_id},
            )
        )
        return f"Gesperrt: {', '.join(locked_now)}"
    if name == "unlock_object":
        ids = tool_input.get("object_ids") or []
        if not isinstance(ids, list) or not ids:
            return "unlock_object: object_ids ist leer."
        removed: list[str] = []
        for obj_id in ids:
            if store.unlock_object(session_id, str(obj_id)):
                removed.append(str(obj_id))
        await manager.broadcast(
            schemas.WsEvent(
                type="settings.updated",
                payload={"locks_changed": True, "session_id": session_id},
            )
        )
        return f"Entsperrt: {', '.join(removed) if removed else '(keine)'}"
    if name == "list_locked_objects":
        locks = store.list_locked_objects(session_id)
        if not locks:
            return "Keine Objekte gesperrt."
        lines = [
            f"- {lk.object_id}"
            + (f" ({lk.object_name})" if lk.object_name else "")
            + (f" — {lk.note}" if lk.note else "")
            for lk in locks
        ]
        return "Gesperrte Objekte:\n" + "\n".join(lines)
    return f"Unbekanntes Lock-Tool: {name}"


def _result_signals_error(result: Any) -> bool:
    """True when a tool's result content represents a failure.

    Generated Rhino scripts return their failure as a string starting
    with ``"Fehler: ..."`` rather than raising, so the calling site never
    sees an exception. Without flagging the tool_result block as an
    error the model just sees text and may retry the same broken tool
    several times before parsing the message. Mirroring the string-level
    failure into ``is_error=True`` short-circuits that.

    Handles three shapes seen in the wild:
    - ``str`` (most dedicated tools): prefix check
    - ``dict`` (some return structured payloads): ``status == "error"``
    - ``list`` (capture_viewport interleaves text+image blocks): any text
      block whose ``text`` starts with an error prefix
    """
    if result is None:
        return False
    if isinstance(result, str):
        return result.startswith(_ERROR_PREFIXES)
    if isinstance(result, dict):
        status = str(result.get("status", "")).lower()
        if status in {"error", "failed", "failure"}:
            return True
        return False
    if isinstance(result, list):
        for block in result:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                text = str(block.get("text", ""))
                if text.startswith(_ERROR_PREFIXES):
                    return True
        return False
    return False


async def _tool_capture_viewport(tool_input: dict[str, Any]) -> list[dict[str, Any]]:
    from ..viewport_bridge import (
        RHINO_AVAILABLE,
        SUPPORTED_VIEWS,
        VALID_FIT_MODES,
        capture_composite,
    )

    if not RHINO_AVAILABLE:
        return [
            {
                "type": "text",
                "text": "Viewport nicht verfügbar — Backend läuft außerhalb von Rhino.",
            }
        ]
    # Default to a 2x2 composite (perspective + 3 orthos) with extents-fit
    # so the model gets full geometric context on every call without
    # having to know about a separate "multi-view" tool. The model can
    # opt back to single view via views=["current"] for cheap checks.
    raw_views = tool_input.get("views") or ["current", "top", "front", "right"]
    if not isinstance(raw_views, list) or not raw_views:
        raw_views = ["current", "top", "front", "right"]
    views = [str(v) for v in raw_views]
    bad = [v for v in views if v not in SUPPORTED_VIEWS]
    if bad:
        return [
            {
                "type": "text",
                "text": (
                    "Unbekannte Ansicht(en): {0}. Erlaubt: {1}".format(
                        ", ".join(bad), ", ".join(SUPPORTED_VIEWS)
                    )
                ),
            }
        ]
    fit = str(tool_input.get("fit", "extents"))
    if fit not in VALID_FIT_MODES:
        return [
            {
                "type": "text",
                "text": (
                    "Unbekannter fit-Modus: {0}. Erlaubt: {1}".format(
                        fit, ", ".join(VALID_FIT_MODES)
                    )
                ),
            }
        ]
    max_size = int(tool_input.get("max_size", 1024))

    snap = await asyncio.to_thread(
        capture_composite,
        views=views,
        max_size=max_size,
        fit=fit,
    )
    # For composite results the label-text reminds the model that the
    # 2x2 grid is one object from multiple angles, not four objects.
    # Single-view captures use a plain label.
    is_composite = len(views) > 1
    if is_composite:
        prefix = (
            "Viewport-Aufnahme (2x2-Komposit, dieselbe Rhino-Szene aus "
            "vier Ansichten — Labels stehen oben links in jeder Zelle): "
        )
    else:
        prefix = "Viewport-Aufnahme: "
    return [
        {
            "type": "text",
            "text": prefix + snap["view_label"],
        },
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": snap["media_type"],
                "data": snap["data"],
            },
        },
    ]


async def _tool_execute_rhino_code(tool_input: dict[str, Any]) -> str:
    from .. import rhino_exec

    if not rhino_exec.RHINO_AVAILABLE:
        return "Rhino nicht verfügbar — Code kann nicht ausgeführt werden."
    code = tool_input.get("code", "")
    if not code:
        return "Fehler: leerer Code."
    # begin_scripted_action pre-emptively archives the whole live scene so
    # raw RhinoCommon edits (doc.Objects.Delete + AddBrep, Objects.Replace),
    # which bypass the plugin's archive_object/_record_* hooks, still land on
    # the reliable plugin-backup undo path. finish_scripted_action records
    # created objects via a before/after diff and prunes untouched backups.
    wrapped_code = (
        "_action_id = begin_scripted_action('execute_rhino_code')\n"
        "try:\n"
        + "\n".join("    " + line for line in code.splitlines())
        + "\nfinally:\n"
        "    finish_scripted_action(_action_id)\n"
    )
    try:
        out = await asyncio.to_thread(
            rhino_exec.run_code,
            wrapped_code,
            native_undo_description="execute_rhino_code",
        )
    except Exception as e:
        return f"Ausführungsfehler: {e}\n{traceback.format_exc()}"
    return out
