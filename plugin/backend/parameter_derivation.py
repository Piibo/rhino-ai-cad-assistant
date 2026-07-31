"""Automatic parameter derivation for editable structures."""

from __future__ import annotations

import math
from datetime import datetime, timezone

from . import schemas
from .session_store import get_store


def _clone(param: schemas.ExposedParameter) -> schemas.ExposedParameter:
    return param.model_copy(deep=True)


def _pos_float(value) -> float | None:
    """Coerce to a strictly-positive float, else None (skip that slider).

    Recipe dims are normally float()-written by the geometry templates, but the
    editable_recipe UserText can be hand-edited in Rhino to a non-numeric or
    zero/negative value. Without this guard float() would raise (aborting the
    whole structure-context sync -> no parametric chip + a generic error) or a
    0/negative current would fall outside its own derived [min,max].
    """
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def _range_for_value(value: float) -> tuple[float, float, float]:
    """Pick slider min/max/step for an initial value.

    Critical: ``minimum`` must be a multiple of ``step``, otherwise an
    HTML range slider with ``step=1`` and a fractional min (e.g. 29.75)
    only ever lands on 29.75, 30.75, 31.75, ... — so the displayed
    "120" actually scales the box to 120.75 (or 119.75 after rebuild),
    and the user sees a slider value that doesn't match the geometry.
    Snapping min and max to step boundaries keeps slider positions
    aligned to round mm values.
    """
    safe = max(float(value or 0.0), 1.0)
    step = 1.0 if safe >= 10 else 0.5
    raw_min = max(step, safe * 0.25)
    raw_max = max(safe * 2.5, safe + 50.0)
    # Floor min, ceil max to step boundaries so every slider tick is
    # exactly representable in mm.
    minimum = math.floor(raw_min / step) * step
    maximum = math.ceil(raw_max / step) * step
    if minimum < step:
        minimum = step
    return float(minimum), float(maximum), float(step)


def nice_step(span: float) -> float:
    """Runder, menschenfreundlicher Slider-Step fuer eine Wertspanne (~100 Ticks).

    Wird genutzt, um ExposedParameter.step nachzufuellen, wenn ein eingehender
    gh_slider-/agent_exposed-Parameter ihn weglaesst: statt des krummen
    ``(max-min)/100``-Frontend-Fallbacks rastet der Slider dann auf runde
    1/2/5x10^k-Schritte — dieselbe Granularitaets-Logik wie ``_range_for_value``
    fuer die editable-Struktur-Params. min/max bleiben unberuehrt.
    """
    if span <= 0:
        return 1.0
    raw = span / 100.0
    magnitude = 10.0 ** math.floor(math.log10(raw))
    norm = raw / magnitude
    if norm <= 1.0:
        nice = 1.0
    elif norm <= 2.0:
        nice = 2.0
    elif norm <= 5.0:
        nice = 5.0
    else:
        nice = 10.0
    return float(nice * magnitude)


def _editable_action(
    object_id: str,
    parameter_key: str,
) -> schemas.ParameterAction:
    return schemas.ParameterAction(
        type="editable_recipe_value",
        target_object_ids=[object_id],
        parameter_key=parameter_key,
    )


def _structure_param(
    *,
    context: schemas.EditableStructureContext,
    name: str,
    parameter_key: str,
    current: float,
) -> schemas.ExposedParameter:
    minimum, maximum, step = _range_for_value(current)
    return schemas.ExposedParameter(
        name=name,
        current=float(current),
        min=minimum,
        max=maximum,
        step=step,
        display_unit="mm",
        source="editable_structure",
        structure_id=context.id,
        structure_key=context.structure_key,
        actions=[_editable_action(context.object_id or "", parameter_key)],
    )


def derive_parameters_from_structure(
    context: schemas.EditableStructureContext | None,
) -> list[schemas.ExposedParameter]:
    if context is None or context.structure_type != "primitive" or not context.object_id:
        return []

    recipe = context.metadata.get("editable_recipe") or {}
    if not isinstance(recipe, dict):
        return []

    if context.structure_key == "primitive_box":
        width = _pos_float(recipe.get("width"))
        depth = _pos_float(recipe.get("depth"))
        height = _pos_float(recipe.get("height"))
        if width is None or depth is None or height is None:
            return []
        return [
            _structure_param(
                context=context,
                name="Breite",
                parameter_key="width",
                current=width,
            ),
            _structure_param(
                context=context,
                name="Tiefe",
                parameter_key="depth",
                current=depth,
            ),
            _structure_param(
                context=context,
                name="Hoehe",
                parameter_key="height",
                current=height,
            ),
        ]

    if context.structure_key == "primitive_cylinder":
        radius = _pos_float(recipe.get("radius"))
        height = _pos_float(recipe.get("height"))
        if radius is None or height is None:
            return []
        return [
            _structure_param(
                context=context,
                name="Radius",
                parameter_key="radius",
                current=radius,
            ),
            _structure_param(
                context=context,
                name="Hoehe",
                parameter_key="height",
                current=height,
            ),
        ]

    if context.structure_key == "primitive_extrusion":
        dx = float(recipe.get("dx", 0.0))
        dy = float(recipe.get("dy", 0.0))
        dz = float(recipe.get("dz", 0.0))
        length = math.sqrt((dx * dx) + (dy * dy) + (dz * dz))
        if length <= 1e-6:
            return []
        return [
            _structure_param(
                context=context,
                name="Laenge",
                parameter_key="length",
                current=length,
            )
        ]

    return []


def annotate_structure_context(
    context: schemas.EditableStructureContext,
) -> tuple[schemas.EditableStructureContext, list[schemas.ExposedParameter]]:
    derived = derive_parameters_from_structure(context)
    enriched = context.model_copy(deep=True)
    enriched.parameter_names = [param.name for param in derived]
    return enriched, derived


def _parameter_matches_context(
    param: schemas.ExposedParameter,
    context: schemas.EditableStructureContext,
) -> bool:
    actions = list(param.actions or ([] if param.action is None else [param.action]))
    if context.structure_type == "grasshopper":
        return any(action.type == "gh_slider" for action in actions)
    if context.object_id:
        return any(context.object_id in (action.target_object_ids or []) for action in actions)
    return True


def _manual_parameters_for_context(
    context: schemas.EditableStructureContext | None,
    existing: list[schemas.ExposedParameter],
) -> list[schemas.ExposedParameter]:
    manual = [param for param in existing if param.source != "editable_structure"]
    if context is None:
        return [_clone(param) for param in manual]
    return [
        _clone(param)
        for param in manual
        if _parameter_matches_context(param, context)
    ]


def _parameter_scope_key(param: schemas.ExposedParameter) -> str:
    if param.structure_id:
        return param.structure_id
    actions = list(param.actions or ([] if param.action is None else [param.action]))
    for action in actions:
        if action.instance_guid:
            return "gh:{0}".format(action.instance_guid)
        if action.target_object_ids:
            return "obj:{0}".format(action.target_object_ids[0])
    return param.source


def merge_parameter_sets(
    auto_parameters: list[schemas.ExposedParameter],
    manual_parameters: list[schemas.ExposedParameter],
) -> list[schemas.ExposedParameter]:
    # Key by name only — the DB UNIQUE constraint on
    # ``(session_id, name)`` doesn't allow two params with the same
    # display name regardless of scope. Two same-named params from
    # different scopes would survive a scope-aware merge and then crash
    # ``set_exposed_parameters`` with an IntegrityError.
    merged: dict[str, schemas.ExposedParameter] = {}
    for param in [*_clone_all(auto_parameters), *_clone_all(manual_parameters)]:
        key = param.name.strip().lower()
        existing = merged.get(key)
        if existing is None or param.source_priority < existing.source_priority:
            merged[key] = param
    return sorted(
        merged.values(),
        key=lambda param: (param.source_priority, param.name.lower()),
    )


def _clone_all(
    parameters: list[schemas.ExposedParameter],
) -> list[schemas.ExposedParameter]:
    return [_clone(param) for param in parameters]


def sync_session_parameters(
    session_id: str,
    *,
    structure_context: schemas.EditableStructureContext | None = None,
    incoming_parameters: list[schemas.ExposedParameter] | None = None,
) -> list[schemas.ExposedParameter]:
    store = get_store()
    auto_parameters = derive_parameters_from_structure(structure_context)

    if incoming_parameters is not None:
        manual_parameters = _clone_all(incoming_parameters)
    else:
        manual_parameters = _manual_parameters_for_context(
            structure_context,
            store.get_exposed_parameters(session_id),
        )

    merged = merge_parameter_sets(auto_parameters, manual_parameters)
    timestamp = datetime.now(timezone.utc).isoformat()
    if merged:
        store.set_exposed_parameters(session_id, merged, timestamp)
    else:
        store.clear_exposed_parameters(session_id)
    return merged
