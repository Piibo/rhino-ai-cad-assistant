"""dedicated_tools._shared - geteilte Leaf-Helfer (Klassifikations-Sets,
Struktur-Sync, Action-Log-Wrapper, Slider-Action-Codegen). Nur abwaerts
gerichtete Importe; wird von core/parameters/variants importiert,
importiert selbst KEIN Paket-Submodul (zyklenfrei)."""
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
from ..structure_context_service import (
    apply_parameter_set,
    clear_session_parameters,
    clear_structure_context,
    refresh_structure_context_in_store,
    sync_structure_context_after_object_change,
)
from ..session_store import get_store
from ..websocket_manager import manager

logger = logging.getLogger("FurniturePlugin.DedicatedTools")

_GUID_RE = re.compile(
    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
)

_STRUCTURE_RESULT_TOOLS = {
    "restore_object",
    "undo_last_action",
    "redo_last_action",
    "curve_boolean_union",
    "join_surfaces",
    "boolean_union",
    "boolean_difference",
    "boolean_intersection",
    "boolean_split",
    "thicken_surface_to_solid",
}

_STRUCTURE_REPLACED_OBJECT_FIELDS = {
    "set_bbox_dimension": "object_id",
    "resize_box_face": "object_id",
    "resize_cylinder_face": "object_id",
    "resize_extrusion_face": "object_id",
}

# Editierbare Primitive, deren Groessen-Slider direkt nach dem Erstellen
# auftauchen sollen — ABER nur, wenn der Designer keine Masse genannt hat
# (Designentscheidung 16.06.2026). Das Modell signalisiert "keine Masse",
# indem es jedes Groessen-Argument WEGLAESST (Schema: dims optional). Nennt
# der Designer ein Mass, ist mindestens ein Groessen-Feld im tool_input und
# es erscheinen keine Auto-Slider. Wert = die Groessen-Felder (Position/cap
# zaehlen bewusst NICHT als Mass). Die Slider rendern nur in der werkzeug-
# Bedingung (App.tsx gated die ParameterPanel) — basis-neutral.
_STRUCTURE_CREATED_OBJECT_TOOLS = {
    "create_box": ("width", "depth", "height"),
    "create_cylinder": ("radius", "height"),
}

_STRUCTURE_BREAKING_TOOLS = {
    "create_hole",
    "create_slot",
    "fillet_brep_edge",
    "round_edges_by_rule",
    "chamfer_brep_edge",
}


_NO_ACTION_LOG_TOOLS = {
    "get_scene_info",
    "get_layer_info",
    "get_object_info",
    "get_brep_component_info",
    "resolve_reference",
    "get_layers",
    "get_scene_objects_with_metadata",
    "get_selected",
    "select_objects",
    "list_backups",
    "undo_last_action",
    "redo_last_action",
}


def _wrap_with_action_log(tool_name: str, code: str) -> str:
    return (
        "_action_id = begin_action({!r})\n"
        "try:\n"
        "{}\n"
        "finally:\n"
        "    finish_action(_action_id)\n"
    ).format(tool_name, textwrap.indent(code, "    "))


def _normalise_object_ids(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        object_ids: list[str] = []
        for entry in value:
            if isinstance(entry, str) and entry.strip():
                object_ids.append(entry.strip())
        return object_ids
    return []


def _result_object_ids(tool_result: str) -> list[str]:
    if tool_result.startswith("Error:") or tool_result.startswith("Fehler:"):
        return []
    seen: set[str] = set()
    object_ids: list[str] = []
    for object_id in _GUID_RE.findall(tool_result or ""):
        if object_id in seen:
            continue
        seen.add(object_id)
        object_ids.append(object_id)
    return object_ids


def _structure_candidate_object_ids(
    tool_name: str,
    tool_input: dict[str, Any],
    tool_result: str,
) -> list[str]:
    if tool_name in _STRUCTURE_RESULT_TOOLS:
        return _result_object_ids(tool_result)

    field = _STRUCTURE_REPLACED_OBJECT_FIELDS.get(tool_name)
    if field:
        return _normalise_object_ids(tool_input.get(field))

    dim_fields = _STRUCTURE_CREATED_OBJECT_TOOLS.get(tool_name)
    if dim_fields is not None:
        # Auto-Expose nur bei masslosem Erstellen: lieferte das Modell ein
        # Groessen-Argument mit, hat der Designer die Groesse selbst gewaehlt
        # -> keine Slider. Fehlgeschlagenes Tool -> kein Kandidat (die GUID-
        # Extraktion liefert ohnehin nichts, der Guard ist explizit).
        if any(dim_field in tool_input for dim_field in dim_fields):
            return []
        if _tool_result_failed(tool_result):
            return []
        return _result_object_ids(tool_result)
    return []


def _structure_invalidated_object_ids(
    tool_name: str,
    tool_input: dict[str, Any],
) -> list[str]:
    if tool_name == "delete_object":
        return _normalise_object_ids(tool_input.get("object_ids"))

    if tool_name in {"join_surfaces", "boolean_union", "boolean_intersection"}:
        delete_input = bool(tool_input.get("delete_input", True))
        if delete_input:
            return _normalise_object_ids(tool_input.get("object_ids"))
        return []

    if tool_name == "boolean_difference":
        # boolean_difference_code ALWAYS replaces keep_id with the result, so
        # keep_id is invalidated regardless of delete_input — otherwise its
        # structure-context slider lingers and resizes a phantom box (the
        # consumed seat) instead of the cut result. The cutters (remove_ids)
        # are only consumed when delete_input is True.
        invalidated = [tool_input.get("keep_id")]
        if bool(tool_input.get("delete_input", False)):
            invalidated.extend(tool_input.get("remove_ids") or [])
        return _normalise_object_ids(invalidated)

    if tool_name == "boolean_split" and bool(tool_input.get("delete_input", False)):
        return _normalise_object_ids(
            [tool_input.get("object_id"), tool_input.get("cutter_id")]
        )

    if tool_name == "thicken_surface_to_solid" and bool(tool_input.get("delete_input", False)):
        return _normalise_object_ids([tool_input.get("surface_id")])

    return []


def _tool_result_failed(tool_result: str) -> bool:
    return (tool_result or "").startswith(
        ("Error:", "Fehler:", "Ausfuehrungsfehler:", "Ausführungsfehler:")
    )


async def _clear_structure_after_recipe_breaking_tool(
    *,
    session_id: str,
    tool_name: str,
    tool_input: dict[str, Any],
    tool_result: str,
    correlation_id: str | None,
) -> bool:
    if tool_name not in _STRUCTURE_BREAKING_TOOLS or _tool_result_failed(tool_result):
        return False

    target_ids = {
        object_id.lower()
        for object_id in _normalise_object_ids(tool_input.get("object_id"))
    }
    active_context = get_store().get_active_structure_context(session_id)
    active_object_id = str(getattr(active_context, "object_id", "") or "").strip()
    if active_object_id and active_object_id.lower() in target_ids:
        await clear_structure_context(session_id, correlation_id)
        logger.info(
            "structure context cleared after %s changed topology of %s",
            tool_name,
            active_object_id,
        )
        return True
    return False


async def _maybe_sync_structure_after_tool(
    *,
    session_id: str,
    tool_name: str,
    tool_input: dict[str, Any],
    tool_result: str,
    correlation_id: str | None,
) -> None:
    if await _clear_structure_after_recipe_breaking_tool(
        session_id=session_id,
        tool_name=tool_name,
        tool_input=tool_input,
        tool_result=tool_result,
        correlation_id=correlation_id,
    ):
        return

    candidate_ids = _structure_candidate_object_ids(
        tool_name,
        tool_input,
        tool_result,
    )
    invalidated_ids = _structure_invalidated_object_ids(tool_name, tool_input)
    if not candidate_ids and not invalidated_ids and tool_name not in {
        "undo_last_action",
        "redo_last_action",
    }:
        return

    refresh_active = bool(
        invalidated_ids
        or tool_name in _STRUCTURE_REPLACED_OBJECT_FIELDS
        or tool_name in {"undo_last_action", "redo_last_action", "restore_object"}
    )
    await sync_structure_context_after_object_change(
        session_id,
        candidate_object_ids=candidate_ids,
        invalidated_object_ids=invalidated_ids,
        source="tool_result",
        correlation_id=correlation_id,
        clear_if_missing=refresh_active,
        refresh_active=refresh_active,
    )


def _editable_recipe_action_code(
    action: schemas.ParameterAction,
    new_current: float,
) -> str:
    object_id = (action.target_object_ids or [""])[0]
    parameter_key = action.parameter_key or ""
    return code_templates.inject_params(
        oid=object_id,
        key=parameter_key,
        value=new_current,
    ) + """
guid = System.Guid(_oid)
obj = sc.doc.Objects.Find(guid)
if obj is None:
    result = "Error: object not found"
else:
    object_class = rs.GetUserText(guid, "object_class") or ""
    recipe_raw = rs.GetUserText(guid, "editable_recipe") or ""
    recipe_type = rs.GetUserText(guid, "editable_recipe_type") or ""
    editable_ops = rs.GetUserText(guid, "editable_operations") or ""
    if not recipe_raw:
        result = "Error: object has no editable_recipe"
    else:
        try:
            recipe = json.loads(recipe_raw)
        except Exception as e:
            result = "Error: invalid editable_recipe: {0}".format(e)
        else:
            tol = max(sc.doc.ModelAbsoluteTolerance, 1e-6)
            new_value = float(_value)
            if new_value <= tol:
                result = "Error: parameter must stay > 0"
            else:
                rebuilt = None
                next_recipe = dict(recipe)
                try:
                    if object_class == "primitive_box":
                        x = float(recipe.get("x", 0.0))
                        y = float(recipe.get("y", 0.0))
                        z = float(recipe.get("z", 0.0))
                        # Refresh the origin from live geometry so a prior move
                        # isn't undone: recipe x/y/z are only re-synced on scale,
                        # so rebuilding from the stored origin would teleport a
                        # moved box back. The box is axis-aligned, so its bbox
                        # min IS the origin corner.
                        try:
                            _bb = rs.BoundingBox(guid)
                            if _bb:
                                x = float(_bb[0].X)
                                y = float(_bb[0].Y)
                                z = float(_bb[0].Z)
                        except Exception:
                            pass
                        width = float(recipe.get("width", 0.0))
                        depth = float(recipe.get("depth", 0.0))
                        height = float(recipe.get("height", 0.0))
                        if _key == "width":
                            width = new_value
                        elif _key == "depth":
                            depth = new_value
                        elif _key == "height":
                            height = new_value
                        else:
                            raise ValueError("unsupported box parameter: " + str(_key))
                        corners = [
                            rg.Point3d(x, y, z),
                            rg.Point3d(x + width, y, z),
                            rg.Point3d(x + width, y + depth, z),
                            rg.Point3d(x, y + depth, z),
                            rg.Point3d(x, y, z + height),
                            rg.Point3d(x + width, y, z + height),
                            rg.Point3d(x + width, y + depth, z + height),
                            rg.Point3d(x, y + depth, z + height),
                        ]
                        rebuilt = rg.Brep.CreateFromBox(corners)
                        next_recipe.update(
                            {
                                "x": float(x),
                                "y": float(y),
                                "z": float(z),
                                "width": float(width),
                                "depth": float(depth),
                                "height": float(height),
                            }
                        )
                    elif object_class == "primitive_cylinder":
                        base_x = float(recipe.get("base_x", 0.0))
                        base_y = float(recipe.get("base_y", 0.0))
                        base_z = float(recipe.get("base_z", 0.0))
                        radius = float(recipe.get("radius", 0.0))
                        height = float(recipe.get("height", 0.0))
                        cap = bool(recipe.get("cap", True))
                        # Refresh base from live geometry before applying the new
                        # value, so a prior move isn't undone. Z-axis cylinder:
                        # bbox min = (cx - r, cy - r, base_z) with the CURRENT
                        # (recipe) radius.
                        try:
                            _bb = rs.BoundingBox(guid)
                            if _bb and radius > 0:
                                base_x = float(_bb[0].X) + radius
                                base_y = float(_bb[0].Y) + radius
                                base_z = float(_bb[0].Z)
                        except Exception:
                            pass
                        if _key == "radius":
                            radius = new_value
                        elif _key == "height":
                            height = new_value
                        else:
                            raise ValueError(
                                "unsupported cylinder parameter: " + str(_key)
                            )
                        circle = rg.Circle(
                            rg.Plane(rg.Point3d(base_x, base_y, base_z), rg.Vector3d.ZAxis),
                            radius,
                        )
                        cylinder = rg.Cylinder(circle, height)
                        rebuilt = cylinder.ToBrep(cap, cap)
                        next_recipe.update(
                            {
                                "base_x": float(base_x),
                                "base_y": float(base_y),
                                "base_z": float(base_z),
                                "radius": float(radius),
                                "height": float(height),
                            }
                        )
                    elif object_class == "primitive_extrusion":
                        axis = rg.Vector3d(
                            float(recipe.get("dx", 0.0)),
                            float(recipe.get("dy", 0.0)),
                            float(recipe.get("dz", 0.0)),
                        )
                        if not axis.Unitize():
                            raise ValueError("primitive_extrusion axis invalid")
                        if _key != "length":
                            raise ValueError(
                                "unsupported extrusion parameter: " + str(_key)
                            )
                        brep = rs.coercebrep(guid)
                        if brep is None:
                            raise ValueError("current extrusion could not be read")
                        caps = []
                        for face in brep.Faces:
                            if not face.IsPlanar(tol):
                                continue
                            amp = Rhino.Geometry.AreaMassProperties.Compute(
                                face.DuplicateFace(False)
                            )
                            if amp is None:
                                continue
                            du = face.Domain(0)
                            dv = face.Domain(1)
                            u = 0.5 * (du.T0 + du.T1)
                            v = 0.5 * (dv.T0 + dv.T1)
                            normal = face.NormalAt(u, v)
                            if face.OrientationIsReversed:
                                normal.Reverse()
                            if abs(normal * axis) < 0.999:
                                continue
                            point = amp.Centroid
                            caps.append({"face": face, "t": point.X * axis.X + point.Y * axis.Y + point.Z * axis.Z})
                        if len(caps) < 2:
                            raise ValueError("extrusion needs two planar end caps")
                        bottom = min(caps, key=lambda item: item["t"])
                        base_face = bottom["face"]
                        dup_face = base_face.DuplicateFace(False)
                        curves = list(dup_face.DuplicateNakedEdgeCurves(True, False) or [])
                        if not curves:
                            curves = list(dup_face.DuplicateEdgeCurves() or [])
                        if not curves:
                            raise ValueError("could not extract extrusion profile")
                        joined = list(rg.Curve.JoinCurves(curves) or [])
                        profile = joined[0] if joined else curves[0]
                        surface = rg.Surface.CreateExtrusion(profile, axis * new_value)
                        if surface is None:
                            raise ValueError("could not rebuild extrusion surface")
                        rebuilt = surface.ToBrep()
                        if rebuilt is not None and bool(recipe.get("cap", True)) and profile.IsClosed:
                            capped = rebuilt.CapPlanarHoles(tol)
                            if capped is not None:
                                rebuilt = capped
                        next_recipe.update(
                            {
                                "dx": float(axis.X * new_value),
                                "dy": float(axis.Y * new_value),
                                "dz": float(axis.Z * new_value),
                            }
                        )
                    else:
                        raise ValueError("unsupported editable structure: " + str(object_class))
                except Exception as e:
                    result = "Error: editable parameter update failed: {0}".format(e)
                else:
                    # Backup VOR dem Replace -> der Struktur-Slider-Drag (Box-/
                    # Zylinder-/Extrusionsmasse) wird undobar. Ohne das bliebe die
                    # begin_action/finish_action-Action leer (kein backup, kein
                    # created_id) und finish_action verwirft sie -> kein Undo-Eintrag,
                    # nativer Ctrl+Z greift hier ebenfalls nicht. archive_object ist
                    # ueber die Preamble verfuegbar und haengt das Backup an die offene
                    # Action (analog zu den move/scale/rotate-Templates).
                    archive_object(guid)
                    if rebuilt is None or not rebuilt.IsValid:
                        result = "Error: could not rebuild editable object"
                    elif not sc.doc.Objects.Replace(guid, rebuilt):
                        result = "Error: could not replace editable object"
                    else:
                        rs.SetUserText(guid, "object_class", object_class)
                        if recipe_type:
                            rs.SetUserText(guid, "editable_recipe_type", recipe_type)
                        if editable_ops:
                            rs.SetUserText(guid, "editable_operations", editable_ops)
                        rs.SetUserText(guid, "editable_recipe", json.dumps(next_recipe))
                        obj_after = sc.doc.Objects.Find(guid)
                        if obj_after is not None and obj_after.Geometry is not None:
                            rs.SetUserText(
                                guid,
                                "type",
                                obj_after.Geometry.GetType().Name,
                            )
                        bbox = rs.BoundingBox(guid)
                        if bbox:
                            rs.SetUserText(
                                guid,
                                "bbox",
                                json.dumps([[pt.X, pt.Y, pt.Z] for pt in bbox]),
                            )
                        rs.Redraw()
                        result = "Set editable parameter {0} -> {1}".format(_key, new_value)
"""


def _build_action_code(
    action: schemas.ParameterAction, delta: float, new_current: float
) -> str:
    """Build the Rhino-Python source for one slider step.

    ``delta`` = new_current − old_current, in model units.
    ``new_current`` is passed for log/error messages only. Returns Python
    source to be wrapped in begin_action/finish_action by the caller so
    undo_last_action reverts the slider step.

    Only handles transformation actions (move/scale/rotate). ``gh_slider``
    bypasses this entirely — it goes through grasshopper_bridge instead.
    """
    axis = action.axis
    targets = action.target_object_ids
    origin = action.origin or [0.0, 0.0, 0.0]

    if action.type == "move_axis":
        dx, dy, dz = (
            (delta, 0.0, 0.0)
            if axis == "x"
            else (0.0, delta, 0.0)
            if axis == "y"
            else (0.0, 0.0, delta)
        )
        return code_templates.move_objects_code(
            object_ids=targets, dx=dx, dy=dy, dz=dz
        )

    if action.type == "scale_uniform":
        if new_current == 0 or new_current - delta == 0:
            raise ValueError("scale_uniform with zero current - undefined")
        factor = new_current / (new_current - delta)
        return code_templates.scale_objects_code(
            object_ids=targets,
            scale_factor=factor,
            origin_x=origin[0],
            origin_y=origin[1],
            origin_z=origin[2],
        )

    if action.type == "scale_axis":
        if new_current == 0 or new_current - delta == 0:
            raise ValueError("scale_axis with zero current - undefined")
        factor = new_current / (new_current - delta)
        sx, sy, sz = (
            (factor, 1.0, 1.0)
            if axis == "x"
            else (1.0, factor, 1.0)
            if axis == "y"
            else (1.0, 1.0, factor)
        )
        return code_templates.inject_params(
            ids=targets,
            sx=sx,
            sy=sy,
            sz=sz,
            ox=origin[0],
            oy=origin[1],
            oz=origin[2],
        ) + """
guids = [System.Guid(i) for i in _ids]
origin = (_ox, _oy, _oz)
for g in guids:
    archive_object(g)  # Backup vor Scale -> scale_axis-Slider undobar (wie scale_uniform)
    rs.ScaleObject(g, origin, (_sx, _sy, _sz))
    update_box_recipe_from_live(g)
rs.Redraw()
result = "Scaled {0} object(s) by (sx={1}, sy={2}, sz={3})".format(
    len(guids), _sx, _sy, _sz
)
"""

    if action.type == "rotate_axis":
        axis_vec = (
            [1, 0, 0] if axis == "x" else [0, 1, 0] if axis == "y" else [0, 0, 1]
        )
        return code_templates.rotate_objects_code(
            object_ids=targets,
            angle_degrees=delta,
            center=origin,
            axis=axis_vec,
        )

    if action.type == "editable_recipe_value":
        return _editable_recipe_action_code(action, new_current)

    raise ValueError(f"Unknown ParameterAction type: {action.type}")


def _parameter_actions(param: schemas.ExposedParameter) -> list[schemas.ParameterAction]:
    """Normalise ``action`` (single) vs. ``actions`` (list) to a list.

    The Pydantic ``model_validator`` already ensures one of them is set, but
    older DB rows might still hold the single-action shape — defensive
    fallback returns either source as a list.
    """
    if getattr(param, "actions", None):
        return list(param.actions)
    if getattr(param, "action", None) is not None:
        return [param.action]
    raise ValueError(
        "ExposedParameter braucht mindestens eine action bzw. actions-Liste."
    )
