"""Resolve existing Rhino/GH data into a shared editable-structure model."""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from shared import code_templates

from . import schemas
from .rhino_exec import RHINO_AVAILABLE, decode_run_code_result, run_code

logger = logging.getLogger("FurniturePlugin.StructureRegistry")

_STRUCTURE_LABELS = {
    "primitive_box": "Box",
    "primitive_cylinder": "Zylinder",
    "primitive_extrusion": "Extrusion",
    "gh_slider_set": "Grasshopper-Struktur",
}


def _split_csv(raw: Any) -> list[str]:
    if not raw:
        return []
    return [part.strip() for part in str(raw).split(",") if part.strip()]


def _safe_json(raw: Any) -> dict[str, Any]:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(str(raw))
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _component_label(
    component_type: str | None,
    component_index: int | None,
) -> Optional[str]:
    if component_type == "face" and component_index is not None:
        return f"Flaeche {component_index}"
    if component_type == "edge" and component_index is not None:
        return f"Kante {component_index}"
    if component_type == "object":
        return "Objekt"
    return None


def fetch_object_info(object_ids: list[str]) -> list[dict[str, Any]]:
    """Fetch object info via the shared Rhino code template."""
    if not RHINO_AVAILABLE or not object_ids:
        return []
    try:
        raw = run_code(
            code_templates.get_object_info_code(
                object_ids=object_ids,
                include_user_text=True,
            )
        )
    except Exception as exc:
        logger.warning("fetch_object_info failed for %s: %s", object_ids, exc)
        return []

    payload = decode_run_code_result(raw)
    if isinstance(payload, dict):
        return [payload]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def _build_structure_context(
    session_id: str,
    info: dict[str, Any],
    source: str,
    component_type: str | None = None,
    component_index: int | None = None,
    component_info: Optional[dict[str, Any]] = None,
) -> Optional[schemas.EditableStructureContext]:
    user_text = info.get("user_text") or {}
    object_class = str(user_text.get("object_class") or "").strip()
    if not object_class:
        return None

    editable_recipe = _safe_json(user_text.get("editable_recipe"))
    editable_recipe_type = str(user_text.get("editable_recipe_type") or "").strip() or None
    editable_operations = _split_csv(user_text.get("editable_operations"))
    object_id = str(info.get("id") or "").strip() or None
    object_name = str(info.get("name") or "").strip() or None
    structure_label = _STRUCTURE_LABELS.get(object_class, object_class)

    anchors = [
        schemas.StructureAnchor(
            kind="object",
            label=object_name or structure_label,
            object_id=object_id,
            object_name=object_name,
        )
    ]
    component_label = _component_label(component_type, component_index)
    if component_label:
        anchors.append(
            schemas.StructureAnchor(
                kind="component",
                label=component_label,
                object_id=object_id,
                object_name=object_name,
                component_type=component_type,  # type: ignore[arg-type]
                component_index=component_index,
            )
        )

    summary_bits = []
    if editable_recipe_type:
        summary_bits.append(editable_recipe_type.replace("_", " "))
    if editable_operations:
        summary_bits.append(
            "{0} direkte Eingriffe".format(len(editable_operations))
        )

    return schemas.EditableStructureContext(
        id="structure:{0}:{1}".format(session_id, object_id or object_class),
        session_id=session_id,
        structure_type="primitive",
        structure_key=object_class,
        title=structure_label,
        summary=" | ".join(summary_bits),
        source=source,  # type: ignore[arg-type]
        object_id=object_id,
        object_name=object_name,
        object_class=object_class,
        editable_recipe_type=editable_recipe_type,
        editable_operations=editable_operations,
        anchors=anchors,
        metadata={
            "editable_recipe": editable_recipe,
            "bounding_box": info.get("bounding_box") or {},
            "layer": info.get("layer"),
            "rhino_type": info.get("type"),
            "component_info": component_info or {},
        },
    )


def resolve_structure_context_from_object(
    session_id: str,
    object_id: str,
    *,
    source: str,
    component_type: str | None = None,
    component_index: int | None = None,
    component_info: Optional[dict[str, Any]] = None,
) -> Optional[schemas.EditableStructureContext]:
    infos = fetch_object_info([object_id])
    if not infos:
        return None
    return _build_structure_context(
        session_id=session_id,
        info=infos[0],
        source=source,
        component_type=component_type,
        component_index=component_index,
        component_info=component_info,
    )


def resolve_structure_context_from_object_candidates(
    session_id: str,
    object_ids: list[str],
    *,
    source: str,
) -> Optional[schemas.EditableStructureContext]:
    seen: set[str] = set()
    for raw_object_id in object_ids:
        object_id = str(raw_object_id or "").strip()
        if not object_id or object_id in seen:
            continue
        seen.add(object_id)
        context = resolve_structure_context_from_object(
            session_id,
            object_id,
            source=source,
        )
        if context is not None:
            return context
    return None


def resolve_structure_context_from_selection(
    session_id: str,
    object_ids: list[str],
) -> Optional[schemas.EditableStructureContext]:
    """Offer structure only for a clearly singular editable object."""
    if len(object_ids) != 1:
        return None
    return resolve_structure_context_from_object(
        session_id,
        object_ids[0],
        source="selection",
    )


def resolve_structure_context_from_component_pick(
    session_id: str,
    pick: schemas.ComponentPickBlock,
) -> Optional[schemas.EditableStructureContext]:
    if not pick.object_id:
        return None
    return resolve_structure_context_from_object(
        session_id,
        pick.object_id,
        source="component_pick",
        component_type=pick.component_type,
        component_index=pick.component_index,
        component_info=pick.component_info,
    )


def resolve_structure_context_from_parameters(
    session_id: str,
    parameters: list[schemas.ExposedParameter],
) -> Optional[schemas.EditableStructureContext]:
    gh_parameters = []
    for param in parameters:
        actions = list(param.actions or ([] if param.action is None else [param.action]))
        if any(action.type == "gh_slider" for action in actions):
            gh_parameters.append(param)

    if not gh_parameters:
        return None

    anchors: list[schemas.StructureAnchor] = []
    slider_guids: list[str] = []
    for param in gh_parameters:
        actions = list(param.actions or ([] if param.action is None else [param.action]))
        for action in actions:
            if action.type != "gh_slider" or not action.instance_guid:
                continue
            slider_guids.append(action.instance_guid)
            anchors.append(
                schemas.StructureAnchor(
                    kind="gh_slider",
                    label=param.name,
                    instance_guid=action.instance_guid,
                )
            )

    return schemas.EditableStructureContext(
        id="structure:{0}:gh".format(session_id),
        session_id=session_id,
        structure_type="grasshopper",
        structure_key="gh_slider_set",
        title=_STRUCTURE_LABELS["gh_slider_set"],
        summary="{0} gespiegelte Slider".format(len(gh_parameters)),
        source="parameters",
        editable_operations=["gh_slider"],
        anchors=anchors,
        parameter_names=[param.name for param in gh_parameters],
        metadata={
            "slider_count": len(gh_parameters),
            "slider_guids": slider_guids,
        },
    )
