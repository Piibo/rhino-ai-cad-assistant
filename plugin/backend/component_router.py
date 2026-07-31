"""Component-pick routing for the agent tool dispatch.

Extracted from agent.py. Given the most recent viewport component picks
(face / edge / point) made by the designer, these helpers (a) route a
generic Brep edit to the object-specific primitive edit path, (b) guard
against clearly-wrong tool/pick combinations, and (c) augment a tool's
input with the picked object/component references. Pure read-of-session-
history logic; the only external dependency is the session store.
"""
from __future__ import annotations

from typing import Any

from .session_store import get_store


_COMPONENT_ROUTER_INSPECTION_TOOLS = {
    "capture_viewport",
    "get_brep_component_info",
    "get_object_info",
    "get_scene_info",
    "get_selected",
}

_COMPONENT_ROUTER_LOCAL_EDIT_TOOLS = {
    "resize_box_face",
    "resize_cylinder_face",
    "resize_extrusion_face",
    "create_hole",
    "create_slot",
    "fillet_brep_edge",
    "round_edges_by_rule",
    "chamfer_brep_edge",
    "move_brep_face_along_normal",
    "move_brep_face_in_direction",
    # SubD component-level tools
    "subd_crease_edges",
    "subd_extrude_faces",
    "subd_offset_faces",
    "subd_set_vertex_position",
    "subd_subdivide",
    # Mesh → SubD conversion
    "quad_remesh_to_subd",
    # execute_rhino_code intentionally excluded: free-code calls must never
    # be blocked by a stale component-pick's allowed_operations list.
    # The catch-all guard (~line 238) would silently reject free scripts
    # for the entire lifespan of that pick. Dedicated tools remain gated.
}


def _route_tool_from_recent_component_picks(
    name: str,
    tool_input: dict[str, Any],
    session_id: str | None,
) -> tuple[str, dict[str, Any]]:
    """Prefer object-specific edit paths over generic Brep fallbacks."""
    if not session_id:
        return name, tool_input

    store = get_store()
    latest_user_blocks = _latest_non_tool_user_blocks(store.list_messages(session_id))
    if not latest_user_blocks:
        return name, tool_input

    # --- SubD component routing (any component_type) ---
    object_id_hint = str(tool_input.get("object_id") or tool_input.get("subd_id") or "").strip() or None
    any_pick = _any_component_pick(latest_user_blocks, object_id=object_id_hint)
    if any_pick:
        geometry_class = str(any_pick.get("geometry_class") or "").strip().lower()
        component_type = str(any_pick.get("component_type") or "").strip().lower()
        picked_object_id = str(any_pick.get("object_id") or "").strip()
        component_index = any_pick.get("component_index")

        if geometry_class == "subd" and picked_object_id:
            rewritten = dict(tool_input)
            rewritten["subd_id"] = picked_object_id
            rewritten.pop("object_id", None)

            if component_type == "edge" and isinstance(component_index, int):
                rewritten["edge_indices"] = [component_index]
                return "subd_crease_edges", rewritten

            if component_type == "face" and isinstance(component_index, int):
                rewritten["face_indices"] = [component_index]
                # Default to extrude; model can override to subd_offset_faces
                return "subd_extrude_faces", rewritten

            if component_type == "vertex" and isinstance(component_index, int):
                rewritten["vertex_index"] = component_index
                return "subd_set_vertex_position", rewritten

            if component_type == "object":
                return "subd_subdivide", rewritten

        if geometry_class == "mesh" and picked_object_id:
            rewritten = dict(tool_input)
            rewritten["object_id"] = picked_object_id
            return "quad_remesh_to_subd", rewritten

    # --- Brep / primitive face routing (existing logic) ---
    face_pick = _single_component_pick(
        latest_user_blocks,
        component_type="face",
        object_id=str(tool_input.get("object_id") or "").strip() or None,
    )
    if not face_pick:
        return name, tool_input

    object_class = str(face_pick.get("object_class") or "").strip()
    if object_class not in {
        "primitive_box",
        "primitive_cylinder",
        "primitive_extrusion",
    }:
        return name, tool_input

    if name == "move_brep_face_along_normal":
        try:
            distance = float(tool_input.get("distance"))
        except Exception:
            return name, tool_input
        rewritten = dict(tool_input)
        rewritten["object_id"] = str(face_pick.get("object_id") or "")
        rewritten["face_index"] = face_pick.get("component_index")
        rewritten["distance"] = distance
        routed_name = (
            "resize_box_face"
            if object_class == "primitive_box"
            else "resize_cylinder_face"
            if object_class == "primitive_cylinder"
            else "resize_extrusion_face"
        )
        return (routed_name, rewritten)

    if name == "move_brep_face_in_direction":
        normal = _vector3((face_pick.get("component_info") or {}).get("normal"))
        vector = _vector3(
            [
                tool_input.get("dx", 0.0),
                tool_input.get("dy", 0.0),
                tool_input.get("dz", 0.0),
            ]
        )
        if not normal or not vector:
            return name, tool_input
        length = _length(vector)
        if length <= 1e-9:
            return name, tool_input
        dot = _dot(normal, vector)
        alignment = abs(dot) / length
        if alignment < 0.985:
            return name, tool_input
        rewritten = dict(tool_input)
        rewritten["object_id"] = str(face_pick.get("object_id") or "")
        rewritten["face_index"] = face_pick.get("component_index")
        rewritten["distance"] = dot
        routed_name = (
            "resize_box_face"
            if object_class == "primitive_box"
            else "resize_cylinder_face"
            if object_class == "primitive_cylinder"
            else "resize_extrusion_face"
        )
        return (routed_name, rewritten)

    return name, tool_input


def _guard_tool_from_recent_component_picks(
    name: str,
    tool_input: dict[str, Any],
    session_id: str | None,
) -> str | None:
    """Reject clearly wrong local-edit routes for the current component picks."""
    if name in _COMPONENT_ROUTER_INSPECTION_TOOLS:
        return None
    if name not in _COMPONENT_ROUTER_LOCAL_EDIT_TOOLS:
        return None
    if not session_id:
        return None

    store = get_store()
    latest_user_blocks = _latest_non_tool_user_blocks(store.list_messages(session_id))
    if not latest_user_blocks:
        return None

    object_id = str(tool_input.get("object_id") or "").strip() or None
    picks = _component_picks(latest_user_blocks, object_id=object_id)
    if not picks:
        return None

    object_ids = {
        str(p.get("object_id") or "").strip()
        for p in picks
        if str(p.get("object_id") or "").strip()
    }
    object_classes = {
        str(p.get("object_class") or "").strip()
        for p in picks
        if str(p.get("object_class") or "").strip()
    }
    component_types = {
        str(p.get("component_type") or "").strip().lower()
        for p in picks
        if str(p.get("component_type") or "").strip()
    }
    allowed_ops = {
        str(op)
        for p in picks
        for op in (p.get("allowed_operations") or [])
        if str(op).strip()
    }

    # Allow object_class to be missing — clear_editable_recipe strips it
    # from BREPs after a topology change (hole, chamfer, fillet), but the
    # pick's allowed_operations is still authoritative. Only object_id
    # and component_type need to be unambiguous to gate the model.
    if len(object_ids) != 1 or len(component_types) != 1:
        return None

    object_class = next(iter(object_classes)) if object_classes else ""
    component_type = next(iter(component_types))

    # SubD and mesh picks: let _route_ handle all component types — do not block.
    # geometry_class is available on the picks from the new SubD-aware pick.py.
    geometry_classes = {
        str(p.get("geometry_class") or "").strip().lower()
        for p in picks
        if str(p.get("geometry_class") or "").strip()
    }
    if geometry_classes & {"subd", "mesh"}:
        return None

    # Hard guard: if the model calls a face-only tool while the picked
    # component is an edge, refuse early instead of letting the tool
    # interpret the edge-index as a face-index (which silently produces
    # the wrong geometry or a cryptic Rhino error). Same for vertex picks.
    _FACE_ONLY_TOOLS = {
        "resize_box_face",
        "resize_cylinder_face",
        "resize_extrusion_face",
        "move_brep_face_along_normal",
        "move_brep_face_in_direction",
        "create_hole",
        "create_slot",
    }
    if name in _FACE_ONLY_TOOLS and component_type in ("edge", "vertex"):
        return (
            "Router: `{0}` braucht eine Flaechen-Selektion, der Designer "
            "hat aber eine {1} gepickt. Eine Kante laesst sich in einem "
            "Brep-Solid nicht isoliert verschieben — fuer Edge-Move in "
            "Rhino: Sub-Object-Selection mit Strg+Shift+Click auf die "
            "Kante, dann Gumball-Pfeil ziehen. Verzichte auf Brep-Face-, "
            "SubD-Convert- oder Script-Tricks und sag dem Designer das "
            "direkt."
        ).format(name, component_type)

    if object_class == "primitive_box" and component_type == "face":
        if name not in {"resize_box_face", "create_hole", "create_slot"}:
            return (
                "Router: Fuer eine primitive_box-Flaeche ist hier nur "
                "`resize_box_face`, `create_hole` oder `create_slot` als "
                "Edit-Pfad erlaubt. Nutze bei Bedarf vorher "
                "`get_brep_component_info`, aber keine generischen Brep- "
                "oder Script-Edits."
            )

    if object_class == "primitive_cylinder" and component_type == "face":
        if name not in {"resize_cylinder_face", "create_hole", "create_slot"}:
            return (
                "Router: Fuer eine primitive_cylinder-Flaeche ist hier nur "
                "`resize_cylinder_face`, `create_hole` oder `create_slot` "
                "als Edit-Pfad erlaubt. Nutze bei Bedarf vorher "
                "`get_brep_component_info`, aber keine generischen Brep- "
                "oder Script-Edits."
            )

    if object_class == "primitive_extrusion" and component_type == "face":
        if name not in {"resize_extrusion_face", "create_hole", "create_slot"}:
            return (
                "Router: Fuer eine primitive_extrusion-Flaeche ist hier nur "
                "`resize_extrusion_face`, `create_hole` oder `create_slot` "
                "als Edit-Pfad erlaubt. Nutze bei Bedarf vorher "
                "`get_brep_component_info`, aber keine generischen Brep- "
                "oder Script-Edits."
            )

    if object_class == "primitive_box" and component_type == "edge":
        if name not in {"fillet_brep_edge", "chamfer_brep_edge"}:
            return (
                "Router: Fuer eine primitive_box-Kante sind hier nur "
                "`fillet_brep_edge` oder `chamfer_brep_edge` erlaubt."
            )

    if allowed_ops and name not in allowed_ops and not (
        name in {"create_hole", "create_slot"} and component_type == "face"
    ):
        return (
            "Router: Das gewaehlte Tool passt nicht zu den erlaubten "
            f"Operationen dieser Referenz ({', '.join(sorted(allowed_ops))})."
        )
    return None


def _augment_tool_input_from_recent_component_picks(
    name: str,
    tool_input: dict[str, Any],
    session_id: str | None,
) -> dict[str, Any]:
    """Fill missing object/component refs and preserve exact picked targets."""
    if name not in {
        "create_hole",
        "create_slot",
        "fillet_brep_edge",
        "chamfer_brep_edge",
        "resize_box_face",
        "resize_cylinder_face",
        "resize_extrusion_face",
        "move_brep_face_along_normal",
        "move_brep_face_in_direction",
        # SubD component-level tools
        "subd_crease_edges",
        "subd_extrude_faces",
        "subd_offset_faces",
        "subd_set_vertex_position",
        "subd_subdivide",
        # Mesh conversion
        "quad_remesh_to_subd",
    }:
        return tool_input
    if not session_id:
        return tool_input

    store = get_store()
    latest_user_blocks = _latest_non_tool_user_blocks(store.list_messages(session_id))
    if not latest_user_blocks:
        return tool_input

    next_input = dict(tool_input)

    # --- SubD / mesh augmentation ---
    if name in {
        "subd_crease_edges",
        "subd_extrude_faces",
        "subd_offset_faces",
        "subd_set_vertex_position",
        "subd_subdivide",
        "quad_remesh_to_subd",
    }:
        object_id_hint = (
            str(next_input.get("subd_id") or next_input.get("object_id") or "").strip() or None
        )
        any_pick = _any_component_pick(latest_user_blocks, object_id=object_id_hint)
        if any_pick:
            picked_object_id = str(any_pick.get("object_id") or "").strip()
            geometry_class = str(any_pick.get("geometry_class") or "").strip().lower()
            component_index = any_pick.get("component_index")

            if geometry_class == "subd" and picked_object_id:
                if not next_input.get("subd_id"):
                    next_input["subd_id"] = picked_object_id
                next_input.pop("object_id", None)

                if name == "subd_crease_edges":
                    if not next_input.get("edge_indices") and isinstance(component_index, int):
                        existing = _selected_edge_indices_for_object(latest_user_blocks, picked_object_id)
                        next_input["edge_indices"] = existing if existing else [component_index]

                elif name in {"subd_extrude_faces", "subd_offset_faces"}:
                    if not next_input.get("face_indices") and isinstance(component_index, int):
                        next_input["face_indices"] = [component_index]

                elif name == "subd_set_vertex_position":
                    if next_input.get("vertex_index") is None and isinstance(component_index, int):
                        next_input["vertex_index"] = component_index

                # subd_subdivide: subd_id already set above, no component ref needed

            elif geometry_class == "mesh" and picked_object_id:
                if not next_input.get("object_id"):
                    next_input["object_id"] = picked_object_id

        return next_input

    if name in {"create_hole", "create_slot"}:
        face_pick = _single_component_pick(
            latest_user_blocks,
            component_type="face",
            object_id=str(next_input.get("object_id") or "").strip() or None,
        )
        if face_pick and not str(next_input.get("object_id") or "").strip():
            next_input["object_id"] = str(face_pick.get("object_id") or "")
        if not next_input.get("center"):
            point = _single_point_pick(latest_user_blocks)
            if not point and face_pick:
                point = face_pick.get("pick_point") or face_pick.get("point")
            if isinstance(point, list) and len(point) == 3:
                next_input["center"] = point
        if face_pick and not next_input.get("direction"):
            normal = _vector3((face_pick.get("component_info") or {}).get("normal"))
            if normal:
                next_input["direction"] = [-normal[0], -normal[1], -normal[2]]
        return next_input

    if name in {"fillet_brep_edge", "chamfer_brep_edge"}:
        object_id = str(tool_input.get("object_id") or "").strip()
        if not object_id:
            object_id = _single_object_id_from_component_picks(
                _component_picks(latest_user_blocks, component_type="edge")
            ) or ""
            if object_id:
                next_input["object_id"] = object_id

        edge_indices = _selected_edge_indices_for_object(
            latest_user_blocks, object_id
        )
        if len(edge_indices) >= 2:
            next_input["edge_indices"] = edge_indices
        elif len(edge_indices) == 1 and "edge_index" not in next_input:
            next_input["edge_index"] = edge_indices[0]

        try:
            single = int(next_input.get("edge_index"))
        except Exception:
            single = None
        if (
            "edge_indices" in next_input
            and single is not None
            and single not in next_input["edge_indices"]
        ):
            next_input.pop("edge_index", None)
        return next_input

    face_pick = _single_component_pick(
        latest_user_blocks,
        component_type="face",
        object_id=str(next_input.get("object_id") or "").strip() or None,
    )
    if not face_pick:
        return next_input

    if not str(next_input.get("object_id") or "").strip():
        next_input["object_id"] = str(face_pick.get("object_id") or "")
    if next_input.get("face_index") is None:
        next_input["face_index"] = face_pick.get("component_index")
    return next_input


def _latest_non_tool_user_blocks(messages: list[Any]) -> list[dict[str, Any]]:
    for msg in reversed(messages):
        if getattr(msg, "role", None) != "user":
            continue
        raw_blocks = getattr(msg, "content", None) or []
        blocks: list[dict[str, Any]] = []
        has_non_tool = False
        for block in raw_blocks:
            if isinstance(block, dict):
                b = block
            elif hasattr(block, "model_dump"):
                b = block.model_dump(mode="json")
            else:
                continue
            blocks.append(b)
            if b.get("type") != "tool_result":
                has_non_tool = True
        if has_non_tool:
            return blocks
    return []


def _selected_edge_indices_for_object(
    blocks: list[dict[str, Any]], object_id: str
) -> list[int]:
    seen: set[int] = set()
    out: list[int] = []
    for block in blocks:
        if block.get("type") != "component_pick":
            continue
        if str(block.get("component_type") or "").strip().lower() != "edge":
            continue
        if str(block.get("object_id") or "").strip() != object_id:
            continue
        idx = block.get("component_index")
        if not isinstance(idx, int):
            continue
        if idx in seen:
            continue
        seen.add(idx)
        out.append(idx)
    return out


def _any_component_pick(
    blocks: list[dict[str, Any]],
    object_id: str | None = None,
) -> dict[str, Any] | None:
    """Return the most recent component_pick regardless of component_type.

    Used by SubD/mesh routing where edge, face, vertex, and object picks all
    carry meaningful geometry_class information that the Brep path doesn't need.
    Returns None when picks come from multiple distinct objects (ambiguous).
    """
    picks = _component_picks(blocks, object_id=object_id)
    if not picks:
        return None
    # If no object_id hint, require all picks to agree on a single object
    if object_id is None:
        inferred = _single_object_id_from_component_picks(picks)
        if inferred:
            picks = _component_picks(blocks, object_id=inferred)
        else:
            return None
    return picks[-1] if picks else None


def _single_point_pick(blocks: list[dict[str, Any]]) -> list[float] | None:
    points = [
        block.get("point")
        for block in blocks
        if block.get("type") == "point_pick"
        and isinstance(block.get("point"), list)
        and len(block.get("point")) == 3
    ]
    if len(points) == 1:
        return points[0]
    return None


def _component_picks(
    blocks: list[dict[str, Any]],
    component_type: str | None = None,
    object_id: str | None = None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    wanted_type = (
        str(component_type).strip().lower() if component_type is not None else None
    )
    wanted_object = str(object_id).strip() if object_id is not None else None
    for block in blocks:
        if block.get("type") != "component_pick":
            continue
        block_type = str(block.get("component_type") or "").strip().lower()
        block_object = str(block.get("object_id") or "").strip()
        if wanted_type is not None and block_type != wanted_type:
            continue
        if wanted_object is not None and block_object != wanted_object:
            continue
        out.append(block)
    return out


def _single_object_id_from_component_picks(
    picks: list[dict[str, Any]],
) -> str | None:
    object_ids = {
        str(p.get("object_id") or "").strip()
        for p in picks
        if str(p.get("object_id") or "").strip()
    }
    if len(object_ids) != 1:
        return None
    return next(iter(object_ids))


def _single_component_pick(
    blocks: list[dict[str, Any]],
    component_type: str,
    object_id: str | None = None,
) -> dict[str, Any] | None:
    picks = _component_picks(blocks, component_type=component_type, object_id=object_id)
    if not picks and object_id is None:
        inferred_object = _single_object_id_from_component_picks(
            _component_picks(blocks, component_type=component_type)
        )
        if inferred_object:
            picks = _component_picks(
                blocks,
                component_type=component_type,
                object_id=inferred_object,
            )
    indices = {
        int(p.get("component_index"))
        for p in picks
        if isinstance(p.get("component_index"), int)
    }
    if len(indices) != 1:
        return None
    target_idx = next(iter(indices))
    for pick in picks:
        if pick.get("component_index") == target_idx:
            return pick
    return None


def _trigger_refs_for_tool(
    name: str,
    tool_input: dict[str, Any],
    session_id: str | None,
) -> list[dict[str, Any]] | None:
    """Synthesise references to the pick/selection blocks that triggered a tool.

    Read-only: inspects the most recent non-tool user message (the same
    blocks ``_route_``/``_augment_``/``_guard_`` already consult, so the
    chaining is causally aligned with what the tool routing actually saw)
    and returns a compact list of the ``component_pick`` / ``point_pick`` /
    ``selection`` blocks present there.

    The blocks carry no own id (schemas: ComponentPickBlock/PointPickBlock/
    SelectionBlock have no ``id`` field), so each ref gets a deterministic,
    *synthetic* ``ref`` string (``kind:object_id:component_index``). That is
    rekonstruierbar but not globally unique — the result is a CANDIDATE SET
    of possible triggers, not a 1:1 edge. Returns ``None`` when there are no
    such blocks. Never mutates ``tool_input``.
    """
    if not session_id:
        return None

    store = get_store()
    blocks = _latest_non_tool_user_blocks(store.list_messages(session_id))
    if not blocks:
        return None

    object_id_hint = (
        str(
            tool_input.get("object_id")
            or tool_input.get("subd_id")
            or ""
        ).strip()
        or None
    )

    refs: list[dict[str, Any]] = []

    for pick in _component_picks(blocks, object_id=object_id_hint):
        object_id = str(pick.get("object_id") or "").strip()
        component_type = (
            str(pick.get("component_type") or "").strip().lower() or None
        )
        component_index = pick.get("component_index")
        refs.append(
            {
                "kind": "component_pick",
                "object_id": object_id or None,
                "component_type": component_type,
                "component_index": component_index,
                "ref": "component_pick:{0}:{1}".format(
                    object_id, component_index
                ),
            }
        )

    for block in blocks:
        block_type = block.get("type")
        if block_type == "point_pick":
            object_id = str(block.get("object_id") or "").strip()
            point = block.get("point")
            refs.append(
                {
                    "kind": "point_pick",
                    "object_id": object_id or None,
                    "component_type": None,
                    "component_index": None,
                    "point": point if isinstance(point, list) else None,
                    "ref": "point_pick:{0}".format(object_id or "free"),
                }
            )
        elif block_type == "selection":
            object_ids = block.get("object_ids") or []
            if not isinstance(object_ids, list):
                object_ids = []
            refs.append(
                {
                    "kind": "selection",
                    "object_id": None,
                    "object_ids": [str(o) for o in object_ids],
                    "component_type": None,
                    "component_index": None,
                    "ref": "selection:{0}".format(
                        ",".join(str(o) for o in object_ids)
                    ),
                }
            )

    return refs or None


def _vector3(value: Any) -> tuple[float, float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    try:
        return (float(value[0]), float(value[1]), float(value[2]))
    except Exception:
        return None


def _dot(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _length(a: tuple[float, float, float]) -> float:
    return (a[0] ** 2 + a[1] ** 2 + a[2] ** 2) ** 0.5
