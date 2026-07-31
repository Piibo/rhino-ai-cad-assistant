"""dedicated_tools.parameters - Parameter-/Slider-Handler."""
from __future__ import annotations

import asyncio
import logging
import re
import textwrap
import time
from typing import Any

from shared import code_templates
from .. import grasshopper_bridge, rhino_exec, schemas
from ..capability_router import guard_expose_parameters
from ..parameter_derivation import nice_step
from ..structure_context_service import (
    apply_parameter_set,
    clear_session_parameters,
    clear_structure_context,
    refresh_structure_context_in_store,
    sync_structure_context_after_object_change,
)
from ..session_store import get_store
from ..websocket_manager import manager
from ._shared import logger, _build_action_code, _parameter_actions

# ---------------------------------------------------------------------------
# Parameter slider handlers (FF2 — direct manipulation)
#
# Two paths share this module:
#   - ``_handle_*`` runs via the regular tool dispatch when the LLM calls
#     ``expose_parameters`` / ``clear_parameters``.
#   - ``apply_parameter_change`` is called directly from the WebSocket
#     handler in server.py when the user drags a slider; no LLM round-trip.
# ---------------------------------------------------------------------------


async def _handle_expose_parameters(
    session_id: str, tool_input: dict[str, Any], correlation_id: str | None
) -> str:
    """Persist the slider set and broadcast a ``parameter.exposed`` event.

    Treats the LLM's call as the manual slider proposal and merges it
    behind higher-priority structure/GH controls. The model is expected
    to call this only *after* the designer accepted in chat, except for
    GH slider mirroring.
    """
    raw_params = tool_input.get("parameters") or []
    try:
        params = [schemas.ExposedParameter.model_validate(p) for p in raw_params]
    except Exception as e:
        logger.exception("expose_parameters: invalid input: %s", e)
        return f"Slider-Parameter ungültig: {e}"

    if not params:
        return (
            "expose_parameters wurde mit leerer Liste aufgerufen — "
            "nutze 'clear_parameters' wenn du Slider entfernen willst."
        )

    # Step backfillen: fehlt einem Param der step, einen RUNDEN aus der Spanne
    # ableiten (statt des krummen (max-min)/100-Frontend-Fallbacks) -> gleiche
    # Slider-Granularitaet wie bei den editable-Struktur-Params, ueber alle Quellen
    # konsistent. min/max/current bleiben unberuehrt.
    params = [
        p
        if p.step is not None
        else p.model_copy(update={"step": nice_step(p.max - p.min)})
        for p in params
    ]

    router_error = guard_expose_parameters(session_id, params)
    if router_error:
        logger.info(
            "expose_parameters routed away: session=%s reason=%s",
            session_id,
            router_error,
        )
        return router_error

    _, merged = await apply_parameter_set(
        session_id,
        params,
        correlation_id,
    )
    names = ", ".join(p.name for p in merged)
    logger.info(
        "expose_parameters: session=%s exposed %d slider(s): %s",
        session_id,
        len(merged),
        names,
    )
    return (
        f"{len(merged)} Slider eingeblendet: {names}. "
        "Der Designer kann sie jetzt direkt ziehen; Aenderungen werden "
        "automatisch in der Geometrie angewandt."
    )


async def _handle_clear_parameters(
    session_id: str, correlation_id: str | None
) -> str:
    """Clear free sliders while preserving prioritized structure/GH control."""
    _, remaining = await clear_session_parameters(session_id, correlation_id)
    logger.info("clear_parameters: session=%s", session_id)
    if remaining:
        return (
            "Freie Slider entfernt; der priorisierte Struktur-/GH-Pfad "
            "bleibt aktiv."
        )
    return "Slider-Panel geschlossen."


async def _apply_single_parameter_action(
    action: schemas.ParameterAction,
    delta: float,
    new_current: float,
) -> tuple[bool, str]:
    """Apply one action of a slider step. Returns ``(ok, message)``.

    Two paths:
      - ``gh_slider`` → talk to the Grasshopper HTTP bridge directly so
        the Number Slider component in GH gets the new value and the
        document recomputes.
      - everything else → build Rhino-Python via ``_build_action_code``,
        wrap in begin_action/finish_action, run on the UI thread.
    """
    if action.type == "gh_slider":
        if not action.instance_guid:
            return False, "gh_slider action ohne instance_guid."
        result = await asyncio.to_thread(
            grasshopper_bridge.set_slider_value,
            action.instance_guid,
            new_current,
        )
        if result.startswith("Error:"):
            return False, result
        return True, result

    try:
        code = _build_action_code(action, delta, new_current)
    except Exception as e:
        return False, f"Slider-Aktion konnte nicht aufgebaut werden: {e}"

    wrapped = (
        "_action_id = begin_action({!r})\n"
        "try:\n"
        "{}\n"
        "finally:\n"
        "    finish_action(_action_id)\n"
    ).format(
        "slider action -> {0}".format(new_current),
        textwrap.indent(code, "    "),
    )
    try:
        run_result = await asyncio.to_thread(
            rhino_exec.run_code,
            wrapped,
            native_undo_description="slider action -> {0}".format(new_current),
        )
    except Exception as e:
        return False, f"Ausführungsfehler: {e}"
    # The editable-recipe rebuild path (_editable_recipe_action_code) signals
    # failures with an English "Error:" prefix, not "Fehler:". Checking only
    # "Fehler:" here would treat a failed rebuild as success, persist the new
    # slider value, and desync the slider DB from the unchanged geometry.
    if run_result.startswith(
        ("Fehler:", "Error:", "Ausführungsfehler:", "Ausfuehrungsfehler:")
    ):
        return False, run_result
    return True, run_result


async def apply_parameter_change(
    session_id: str,
    name: str,
    new_value: float,
    correlation_id: str | None,
    *,
    defer_refresh: bool = False,
) -> dict[str, Any]:
    """Execute one slider move. Called from the ``parameter.changed`` WS handler.

    Steps:
      1. Look up the stored parameter (with its action list).
      2. Snap ``new_value`` to the parameter's step grid (defensive — the
         frontend should already step-align via the HTML range element,
         but any caller-supplied value gets rounded the same way so the
         geometry exactly matches what the slider's UI shows).
      3. Compute delta = new_value − current.
      4. Run each action via ``_apply_single_parameter_action``; abort on
         the first failure (subsequent actions don't run, so the geometry
         doesn't end up in a half-transformed state).
      5. On success: persist new_current and log a user-role Message with
         ``modality=["parameter"]`` so the next agent turn sees the change.
    """
    store = get_store()
    param = store.get_exposed_parameter(session_id, name)
    if param is None:
        return {
            "ok": False,
            "message": f"Parameter '{name}' nicht bekannt für diese Sitzung.",
        }

    # Snap to the parameter's step grid so the resulting geometry exactly
    # matches the value the slider shows. Without this, a slider whose
    # min isn't step-aligned could send fractional values that propagate
    # into the recipe and drift over many slider moves.
    step = float(getattr(param, "step", 0) or 0)
    if step > 0:
        minimum = float(getattr(param, "min", 0) or 0)
        snapped = round((new_value - minimum) / step) * step + minimum
        # Clamp to declared bounds before applying.
        maximum = float(getattr(param, "max", snapped) or snapped)
        snapped = max(minimum, min(maximum, snapped))
        new_value = snapped

    delta = new_value - param.current
    if delta == 0:
        return {
            "ok": True,
            "message": "Kein Wechsel.",
            "no_op": True,
            "current_value": param.current,
        }

    logger.info(
        "parameter.changed: session=%s name=%s %s -> %s (delta=%s)",
        session_id,
        name,
        param.current,
        new_value,
        delta,
    )
    action_results: list[str] = []
    try:
        actions = _parameter_actions(param)
    except Exception as e:
        logger.exception("parameter.changed: invalid action spec for %s", name)
        return {
            "ok": False,
            "message": f"Slider-Aktion ungueltig: {e}",
            "current_value": param.current,
        }
    recipe_targets = [
        action.target_object_ids[0]
        for action in actions
        if action.type == "editable_recipe_value" and action.target_object_ids
    ]

    _apply_t0 = time.perf_counter()
    applied: list[schemas.ParameterAction] = []
    for action in actions:
        ok, action_result = await _apply_single_parameter_action(
            action, delta, new_value
        )
        action_results.append(action_result)
        if not ok:
            logger.warning(
                "parameter.changed: action failed for %s: %s",
                name,
                action_result,
            )
            # Partial failure with multiple actions: revert the ones that
            # already applied so the geometry returns to its pre-drag state,
            # consistent with the unchanged param.current. Otherwise it stays
            # half-applied AND the next drag recomputes delta from a stale base,
            # double-applying the succeeded actions. Best-effort — a revert that
            # itself fails is logged, not fatal. Reverting to param.current (the
            # old value) with -delta inverts both delta- and value-based actions.
            for done_action in reversed(applied):
                try:
                    ok_rev, msg_rev = await _apply_single_parameter_action(
                        done_action, -delta, param.current
                    )
                    # _apply_single_parameter_action wirft im Normalfall NICHT,
                    # sondern signalisiert Fehlschlag ueber ok=False (z.B. ein
                    # gh_slider-/Recipe-Rebuild, der 'Error:' liefert). Diesen
                    # haeufigeren Soft-Failure ebenfalls loggen, sonst bleibt die
                    # Geometrie halb-revertet ohne jede Spur.
                    if not ok_rev:
                        logger.warning(
                            "parameter.changed: revert action returned failure "
                            "for %s: %s",
                            name,
                            msg_rev,
                        )
                except Exception as e:  # pragma: no cover — defensive
                    logger.warning(
                        "parameter.changed: revert raised for %s: %s", name, e
                    )
            if recipe_targets:
                await sync_structure_context_after_object_change(
                    session_id,
                    candidate_object_ids=recipe_targets,
                    invalidated_object_ids=recipe_targets,
                    source="tool_result",
                    correlation_id=correlation_id,
                    clear_if_missing=True,
                    refresh_active=True,
                )
            return {
                "ok": False,
                "message": action_result,
                "current_value": param.current,
            }
        applied.append(action)

    logger.info(
        "parameter.changed applied: name=%s -> %s in %.0f ms (Rhino rebuild)",
        name,
        new_value,
        (time.perf_counter() - _apply_t0) * 1000.0,
    )
    store.update_exposed_parameter_current(session_id, name, new_value)
    # Skip the structure-context refresh on mid-drag ticks: the refresh
    # round-trips back to Rhino to re-read user-text, doubling the per-
    # tick latency. The WS handler does one refresh after the last
    # coalesced value lands, which is the only one users see anyway.
    if recipe_targets and not defer_refresh:
        refresh_structure_context_in_store(
            session_id,
            object_ids=recipe_targets,
            source="tool_result",
        )

    display_value = new_value * param.display_factor
    # Note: we deliberately do NOT create a chat message for every slider
    # tick. Live dragging would flood the transcript with one entry per
    # frame (~8/s); the slider label itself already shows the value, and
    # the next LLM turn can read the current state from the exposed
    # parameters DB. If we ever want per-drag analytics for the study,
    # those should go to a separate study-events log, not the chat.
    return {
        "ok": True,
        "message": " | ".join(action_results) if action_results else "OK",
        "name": name,
        "new_value": new_value,
        "display_value": display_value,
        "current_value": new_value,
    }
