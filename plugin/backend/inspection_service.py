"""Participant-facing inspection summaries for selection, picks, and structures."""

from __future__ import annotations

from typing import Any

from . import schemas
from .session_store import get_store
from .structure_registry import (
    fetch_object_info,
    resolve_structure_context_from_component_pick,
    resolve_structure_context_from_selection,
)


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt_xyz(values: list[float] | None) -> str:
    if not values or len(values) != 3:
        return "unbekannt"
    nums = [_safe_float(v) for v in values]
    if any(n is None for n in nums):
        return "unbekannt"
    return "({0:.1f}, {1:.1f}, {2:.1f})".format(nums[0], nums[1], nums[2])


def _bbox_size(info: dict[str, Any]) -> str | None:
    bbox = info.get("bounding_box") or {}
    min_pt = bbox.get("min")
    max_pt = bbox.get("max")
    if not isinstance(min_pt, list) or not isinstance(max_pt, list):
        return None
    if len(min_pt) != 3 or len(max_pt) != 3:
        return None
    spans = [float(max_pt[i]) - float(min_pt[i]) for i in range(3)]
    return "{0:.1f} x {1:.1f} x {2:.1f} mm".format(*spans)


def _brep_status(info: dict[str, Any]) -> tuple[str, str] | None:
    brep = info.get("brep")
    if not isinstance(brep, dict):
        return None
    naked = int(brep.get("naked_edge_count") or 0)
    if brep.get("is_solid"):
        return ("Solid", "success")
    if naked > 0:
        return ("Offen ({0} nackte Kanten)".format(naked), "warning")
    return ("Offen", "warning")


def _structure_fact(
    structure: schemas.EditableStructureContext | None,
) -> schemas.InspectionFact | None:
    if structure is None:
        return None
    label = structure.title
    if structure.object_name:
        label = "{0} | {1}".format(structure.title, structure.object_name)
    return schemas.InspectionFact(label="Struktur", value=label)


def _inspection_result(
    *,
    session_id: str,
    scope: str,
    title: str,
    summary: str,
    facts: list[schemas.InspectionFact],
    related_structure: schemas.EditableStructureContext | None = None,
    payload: dict[str, Any] | None = None,
) -> schemas.InspectionResult:
    return schemas.InspectionResult(
        session_id=session_id,
        scope=scope,  # type: ignore[arg-type]
        title=title,
        summary=summary,
        facts=facts,
        related_structure=related_structure,
        payload=payload or {},
    )


def inspect_selection(
    session_id: str,
    selection: schemas.SelectionBlock,
) -> schemas.InspectionResult:
    infos = fetch_object_info(selection.object_ids)
    structure = resolve_structure_context_from_selection(session_id, selection.object_ids)
    facts: list[schemas.InspectionFact] = [
        schemas.InspectionFact(
            label="Objekte",
            value=str(len(selection.object_ids)),
        )
    ]
    structure_fact = _structure_fact(structure)
    if structure_fact is not None:
        facts.append(structure_fact)

    if len(infos) == 1:
        info = infos[0]
        facts.extend(
            [
                schemas.InspectionFact(
                    label="Name",
                    value=str(info.get("name") or "Unbenannt"),
                ),
                schemas.InspectionFact(
                    label="Typ",
                    value=str(info.get("type") or "Unbekannt"),
                ),
                schemas.InspectionFact(
                    label="Layer",
                    value=str(info.get("layer") or "Unbekannt"),
                ),
            ]
        )
        size = _bbox_size(info)
        if size:
            facts.append(schemas.InspectionFact(label="Groesse", value=size))
        status = _brep_status(info)
        if status:
            facts.append(
                schemas.InspectionFact(
                    label="Status",
                    value=status[0],
                    emphasis=status[1],  # type: ignore[arg-type]
                )
            )
        return _inspection_result(
            session_id=session_id,
            scope="selection",
            title="Auswahl lesen",
            summary="Ein Objekt im Fokus.",
            facts=facts,
            related_structure=structure,
            payload={"objects": infos},
        )

    if infos:
        preview = ", ".join(
            str(info.get("name") or info.get("type") or "Objekt")
            for info in infos[:3]
        )
        if len(infos) > 3:
            preview += ", ..."
        facts.append(schemas.InspectionFact(label="Beispiele", value=preview))

    return _inspection_result(
        session_id=session_id,
        scope="selection",
        title="Auswahl lesen",
        summary=(
            "Mehrere Objekte gewaehlt."
            if selection.object_ids
            else "Keine Objektwahl vorhanden."
        ),
        facts=facts,
        related_structure=structure,
        payload={"objects": infos},
    )


def inspect_component_pick(
    session_id: str,
    pick: schemas.ComponentPickBlock,
) -> schemas.InspectionResult:
    structure = resolve_structure_context_from_component_pick(session_id, pick)
    info = fetch_object_info([pick.object_id]) if pick.object_id else []
    obj = info[0] if info else {}
    facts: list[schemas.InspectionFact] = [
        schemas.InspectionFact(
            label="Komponente",
            value=(
                "{0} {1}".format(pick.component_type, pick.component_index)
                if pick.component_index is not None
                else pick.component_type
            ),
        )
    ]
    structure_fact = _structure_fact(structure)
    if structure_fact is not None:
        facts.append(structure_fact)
    if pick.object_name:
        facts.append(schemas.InspectionFact(label="Objekt", value=pick.object_name))
    elif obj.get("name"):
        facts.append(
            schemas.InspectionFact(label="Objekt", value=str(obj.get("name")))
        )
    if obj.get("layer"):
        facts.append(
            schemas.InspectionFact(label="Layer", value=str(obj.get("layer")))
        )
    size = _bbox_size(obj)
    if size:
        facts.append(schemas.InspectionFact(label="Groesse", value=size))
    if pick.allowed_operations:
        facts.append(
            schemas.InspectionFact(
                label="Direkt moeglich",
                value=", ".join(pick.allowed_operations),
            )
        )

    component_info = pick.component_info or {}
    # component_info values are client-supplied (dict[str, Any], unvalidated), so
    # coerce defensively: a non-numeric length/area must skip the fact, not 500
    # the /inspect endpoint (which has no try/except around this).
    _length = _safe_float(component_info.get("length")) if "length" in component_info else None
    if _length is not None:
        facts.append(
            schemas.InspectionFact(
                label="Laenge",
                value="{0:.1f} mm".format(_length),
            )
        )
    _area = _safe_float(component_info.get("area")) if "area" in component_info else None
    if _area is not None:
        facts.append(
            schemas.InspectionFact(
                label="Flaeche",
                value="{0:.1f} mm^2".format(_area),
            )
        )
    if "midpoint" in component_info:
        facts.append(
            schemas.InspectionFact(
                label="Mittelpunkt",
                value=_fmt_xyz(component_info.get("midpoint")),
            )
        )
    if "normal" in component_info:
        facts.append(
            schemas.InspectionFact(
                label="Normale",
                value=_fmt_xyz(component_info.get("normal")),
            )
        )
    if "is_planar" in component_info:
        facts.append(
            schemas.InspectionFact(
                label="Planar",
                value="ja" if component_info.get("is_planar") else "nein",
            )
        )

    return _inspection_result(
        session_id=session_id,
        scope="component_pick",
        title="Komponente lesen",
        summary="Gezielte Teilgeometrie mit Kontext.",
        facts=facts,
        related_structure=structure,
        payload={
            "object": obj,
            "component_info": component_info,
            "allowed_operations": pick.allowed_operations,
        },
    )


def inspect_active_structure(session_id: str) -> schemas.InspectionResult:
    context = get_store().get_active_structure_context(session_id)
    if context is None:
        return _inspection_result(
            session_id=session_id,
            scope="structure_context",
            title="Aktive Struktur lesen",
            summary="Kein aktiver Strukturkontext.",
            facts=[],
        )

    facts = [
        schemas.InspectionFact(label="Typ", value=context.title),
    ]
    if context.object_name:
        facts.append(
            schemas.InspectionFact(label="Objekt", value=context.object_name)
        )
    if context.parameter_names:
        facts.append(
            schemas.InspectionFact(
                label="Parameter",
                value=", ".join(context.parameter_names),
            )
        )
    if context.editable_operations:
        facts.append(
            schemas.InspectionFact(
                label="Direkt moeglich",
                value=", ".join(context.editable_operations),
            )
        )

    infos = fetch_object_info([context.object_id]) if context.object_id else []
    if infos:
        obj = infos[0]
        if obj.get("layer"):
            facts.append(
                schemas.InspectionFact(label="Layer", value=str(obj.get("layer")))
            )
        size = _bbox_size(obj)
        if size:
            facts.append(schemas.InspectionFact(label="Groesse", value=size))
        status = _brep_status(obj)
        if status:
            facts.append(
                schemas.InspectionFact(
                    label="Status",
                    value=status[0],
                    emphasis=status[1],  # type: ignore[arg-type]
                )
            )

    return _inspection_result(
        session_id=session_id,
        scope="structure_context",
        title="Aktive Struktur lesen",
        summary=context.summary or "Explizit bearbeitbarer Modellkontext.",
        facts=facts,
        related_structure=context,
        payload={"context": context.model_dump(mode="json")},
    )


def inspect_request(
    session_id: str,
    request: schemas.InspectRequest,
) -> schemas.InspectionResult:
    if request.target == "selection":
        if request.selection is None:
            return _inspection_result(
                session_id=session_id,
                scope="selection",
                title="Auswahl lesen",
                summary="Keine Auswahl uebergeben.",
                facts=[],
            )
        return inspect_selection(session_id, request.selection)

    if request.target == "component_pick":
        if request.component_pick is None:
            return _inspection_result(
                session_id=session_id,
                scope="component_pick",
                title="Komponente lesen",
                summary="Kein Komponenten-Pick uebergeben.",
                facts=[],
            )
        return inspect_component_pick(session_id, request.component_pick)

    return inspect_active_structure(session_id)
