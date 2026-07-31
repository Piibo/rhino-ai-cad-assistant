"""Persist + broadcast active editable-structure state."""

from __future__ import annotations

from . import schemas
from .parameter_derivation import annotate_structure_context, sync_session_parameters
from .rhino_exec import RHINO_AVAILABLE
from .session_store import get_store
from .structure_registry import (
    resolve_structure_context_from_component_pick,
    resolve_structure_context_from_object,
    resolve_structure_context_from_object_candidates,
    resolve_structure_context_from_parameters,
    resolve_structure_context_from_selection,
)
from .websocket_manager import manager


async def _broadcast_parameters(
    session_id: str,
    parameters: list[schemas.ExposedParameter],
    correlation_id: str | None,
) -> None:
    event_type = "parameter.exposed" if parameters else "parameter.cleared"
    payload: dict[str, object] = {"session_id": session_id}
    if parameters:
        payload["parameters"] = [
            parameter.model_dump(mode="json") for parameter in parameters
        ]
    await manager.broadcast(
        schemas.WsEvent(
            type=event_type,  # type: ignore[arg-type]
            payload=payload,
            correlation_id=correlation_id,
        )
    )


async def _broadcast_structure_context(
    session_id: str,
    context: schemas.EditableStructureContext | None,
    correlation_id: str | None,
) -> None:
    if context is None:
        await manager.broadcast(
            schemas.WsEvent(
                type="structure_context.cleared",
                payload={"session_id": session_id},
                correlation_id=correlation_id,
            )
        )
        return

    await manager.broadcast(
        schemas.WsEvent(
            type="structure_context.updated",
            payload={
                "session_id": session_id,
                "context": context.model_dump(mode="json"),
            },
            correlation_id=correlation_id,
        )
    )


def _clean_object_ids(object_ids: list[str] | None) -> list[str]:
    if not object_ids:
        return []
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw_object_id in object_ids:
        object_id = str(raw_object_id or "").strip()
        if not object_id or object_id in seen:
            continue
        seen.add(object_id)
        cleaned.append(object_id)
    return cleaned


async def apply_structure_context(
    session_id: str,
    context: schemas.EditableStructureContext,
    correlation_id: str | None = None,
) -> tuple[schemas.EditableStructureContext, list[schemas.ExposedParameter]]:
    store = get_store()
    enriched_context, _ = annotate_structure_context(context)
    store.set_active_structure_context(enriched_context)
    parameters = sync_session_parameters(
        session_id,
        structure_context=enriched_context,
    )
    await _broadcast_structure_context(session_id, enriched_context, correlation_id)
    await _broadcast_parameters(session_id, parameters, correlation_id)
    return enriched_context, parameters


async def clear_structure_context(
    session_id: str,
    correlation_id: str | None = None,
) -> list[schemas.ExposedParameter]:
    store = get_store()
    store.clear_active_structure_context(session_id)
    parameters = sync_session_parameters(session_id, structure_context=None)
    await _broadcast_structure_context(session_id, None, correlation_id)
    await _broadcast_parameters(session_id, parameters, correlation_id)
    return parameters


async def apply_parameter_set(
    session_id: str,
    parameters: list[schemas.ExposedParameter],
    correlation_id: str | None = None,
) -> tuple[schemas.EditableStructureContext | None, list[schemas.ExposedParameter]]:
    store = get_store()
    gh_context = resolve_structure_context_from_parameters(session_id, parameters)
    active_context = store.get_active_structure_context(session_id)

    has_gh_parameter = any(parameter.source == "gh_slider" for parameter in parameters)
    if gh_context is not None:
        active_context = gh_context
        store.set_active_structure_context(active_context)
        await _broadcast_structure_context(session_id, active_context, correlation_id)
    elif active_context is not None and active_context.structure_type == "grasshopper" and not has_gh_parameter:
        active_context = None
        store.clear_active_structure_context(session_id)
        await _broadcast_structure_context(session_id, None, correlation_id)

    parameters_out = sync_session_parameters(
        session_id,
        structure_context=active_context,
        incoming_parameters=parameters,
    )

    if active_context is not None and active_context.structure_type == "grasshopper":
        refreshed = active_context.model_copy(deep=True)
        refreshed.parameter_names = [
            parameter.name
            for parameter in parameters_out
            if parameter.source == "gh_slider"
        ]
        store.set_active_structure_context(refreshed)
        # Re-broadcast: the context broadcast above used the pre-merge
        # parameter_names. After sync_session_parameters dedupes/merges the GH
        # sliders, the stored copy changed — push it so the frontend's
        # activeStructureContext matches the persisted/REST copy (otherwise the
        # ParameterPanel anchor list shows stale names until a reload).
        if refreshed.parameter_names != active_context.parameter_names:
            await _broadcast_structure_context(session_id, refreshed, correlation_id)
        active_context = refreshed

    await _broadcast_parameters(session_id, parameters_out, correlation_id)
    return active_context, parameters_out


async def clear_session_parameters(
    session_id: str,
    correlation_id: str | None = None,
    *,
    clear_grasshopper_context: bool = False,
    preserve_router_priority: bool = True,
) -> tuple[schemas.EditableStructureContext | None, list[schemas.ExposedParameter]]:
    store = get_store()
    active_context = store.get_active_structure_context(session_id)
    if (
        clear_grasshopper_context
        and active_context is not None
        and active_context.structure_type == "grasshopper"
    ):
        # Clear the GH-backed structure context and ALL slider params
        # (including gh_slider source) in one pass.  clear_structure_context
        # alone calls sync_session_parameters(structure_context=None) without
        # incoming_parameters=[], which causes _manual_parameters_for_context
        # to re-preserve gh_slider params and broadcast parameter.exposed
        # instead of parameter.cleared — the panel would stay visible until a
        # second call.  Passing incoming_parameters=[] forces a full clear.
        store.clear_active_structure_context(session_id)
        sync_session_parameters(session_id, structure_context=None, incoming_parameters=[])
        await _broadcast_structure_context(session_id, None, correlation_id)
        await _broadcast_parameters(session_id, [], correlation_id)
        return None, []

    if preserve_router_priority and active_context is not None:
        preserved_parameters: list[schemas.ExposedParameter] = []
        if active_context.structure_type == "grasshopper":
            preserved_parameters = [
                parameter.model_copy(deep=True)
                for parameter in store.get_exposed_parameters(session_id)
                if parameter.source == "gh_slider"
            ]
        parameters = sync_session_parameters(
            session_id,
            structure_context=active_context,
            incoming_parameters=preserved_parameters,
        )
        await _broadcast_parameters(session_id, parameters, correlation_id)
        return active_context, parameters

    store.clear_exposed_parameters(session_id)
    await _broadcast_parameters(session_id, [], correlation_id)
    return active_context, []


async def sync_structure_context_from_selection(
    session_id: str,
    object_ids: list[str],
    correlation_id: str | None = None,
) -> schemas.EditableStructureContext | None:
    context = resolve_structure_context_from_selection(
        session_id,
        _clean_object_ids(object_ids),
    )
    if context is None:
        await clear_structure_context(session_id, correlation_id)
        return None
    await apply_structure_context(session_id, context, correlation_id)
    return context


async def sync_structure_context_from_component_pick(
    session_id: str,
    pick: schemas.ComponentPickBlock | None,
    correlation_id: str | None = None,
) -> schemas.EditableStructureContext | None:
    context = (
        resolve_structure_context_from_component_pick(session_id, pick)
        if pick is not None
        else None
    )
    if context is None:
        await clear_structure_context(session_id, correlation_id)
        return None
    await apply_structure_context(session_id, context, correlation_id)
    return context


async def sync_structure_context_after_object_change(
    session_id: str,
    *,
    candidate_object_ids: list[str] | None = None,
    invalidated_object_ids: list[str] | None = None,
    source: str = "tool_result",
    correlation_id: str | None = None,
    clear_if_missing: bool = False,
    refresh_active: bool = False,
) -> schemas.EditableStructureContext | None:
    preferred_ids = _clean_object_ids(candidate_object_ids)
    invalidated_ids = set(_clean_object_ids(invalidated_object_ids))
    store = get_store()

    if not RHINO_AVAILABLE and (preferred_ids or invalidated_ids or refresh_active):
        # Outside Rhino (dev server, tests, interrupted host) object lookups
        # cannot distinguish "missing" from "unreachable". Keep persisted
        # context rather than clearing good state on a false negative.
        return store.get_active_structure_context(session_id)

    if preferred_ids:
        preferred_context = resolve_structure_context_from_object_candidates(
            session_id,
            preferred_ids,
            source=source,
        )
        if preferred_context is not None:
            await apply_structure_context(session_id, preferred_context, correlation_id)
            return preferred_context

    active_context = store.get_active_structure_context(session_id)
    if active_context is None:
        if clear_if_missing and (preferred_ids or invalidated_ids):
            await clear_structure_context(session_id, correlation_id)
        return None

    active_object_id = str(active_context.object_id or "").strip()
    active_was_invalidated = bool(
        active_object_id and active_object_id in invalidated_ids
    )

    if active_context.structure_type == "grasshopper":
        if clear_if_missing and (preferred_ids or active_was_invalidated):
            await clear_structure_context(session_id, correlation_id)
            return None
        return active_context

    if active_object_id and (refresh_active or active_was_invalidated):
        refreshed = resolve_structure_context_from_object(
            session_id,
            active_object_id,
            source=source,
        )
        if refreshed is not None:
            await apply_structure_context(session_id, refreshed, correlation_id)
            return refreshed

    if clear_if_missing and (preferred_ids or active_was_invalidated or refresh_active):
        await clear_structure_context(session_id, correlation_id)
        return None

    return active_context


def refresh_structure_context_in_store(
    session_id: str,
    *,
    object_ids: list[str],
    source: str = "tool_result",
) -> schemas.EditableStructureContext | None:
    context = resolve_structure_context_from_object_candidates(
        session_id,
        _clean_object_ids(object_ids),
        source=source,
    )
    if context is None:
        return None

    store = get_store()
    enriched_context, _ = annotate_structure_context(context)
    store.set_active_structure_context(enriched_context)
    return enriched_context
