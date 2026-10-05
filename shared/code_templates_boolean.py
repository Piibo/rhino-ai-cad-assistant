"""Code templates: boolean operations. See code_templates.py."""
from __future__ import annotations

from typing import List

from ._codegen_base import inject_params

__all__ = [
    "boolean_union_code",
    "boolean_difference_code",
    "boolean_intersection_code",
    "boolean_split_code",
    "create_hole_code",
    "create_slot_code",
]


def boolean_union_code(
    object_ids: List[str],
    delete_input: bool = True,
    name: str = "BoolUnion",
) -> str:
    """Union two or more closed solids into one. Needs at least 2 ids."""
    return inject_params(
        ids=object_ids, delete=delete_input, name=name
    ) + """
guids = [System.Guid(i) for i in _ids]
# Union without auto-delete so a failed op leaves the inputs untouched; archive
# + delete the inputs explicitly only AFTER we know it produced a result
# (mirrors boolean_difference — no phantom archive/undo entry on failure).
union = rs.BooleanUnion(guids, False)
if union:
    if _delete:
        for g in guids:
            archive_object(g)
        rs.DeleteObjects(guids)
    if len(union) == 1:
        add_object_metadata(union[0], _name, "Boolean union of {0} objects".format(len(_ids)))
    else:
        # Multiple result Breps must get UNIQUE names. add_object_metadata
        # archives+deletes any existing same-named object on the layer, so
        # reusing _name for every part would make each later part wipe out
        # the previous one (only the last would survive).
        for i, u in enumerate(union):
            add_object_metadata(u, "{0}_{1}".format(_name, i + 1), "Boolean union part {0} of {1}".format(i + 1, len(_ids)))
    rs.Redraw()
    union_str = [str(u) for u in union]
    if len(union_str) == 1:
        result = "Boolean union merged into 1 object: " + str(union_str)
    else:
        result = "Boolean union returned {0} objects (not fully merged): {1}".format(len(union_str), union_str)
else:
    # No result: inputs untouched. "Fehler:" prefix makes _result_signals_error
    # (prompts.py) flag this so the model retries instead of building on phantom
    # merged geometry.
    result = "Fehler: Boolean Union lieferte kein Ergebnis (Solids ueberlappen nicht, sind offen oder nicht-manifold)."
"""


def boolean_difference_code(
    keep_id: str,
    remove_ids: List[str],
    delete_input: bool = False,
    name: str = "BoolDiff",
) -> str:
    """Subtract ``remove_ids`` from ``keep_id``.

    ``delete_input`` controls ONLY the cutters (``remove_ids``). The base
    (``keep_id``) is always replaced by the result. Default ``False`` keeps
    the cutters, because in furniture modelling they are usually real parts
    (e.g. subtracting the legs from the seat must NOT consume the legs). Pass
    ``True`` only when the cutter is a throwaway tool the user wants gone.
    """
    return inject_params(
        keep=keep_id, removes=remove_ids, delete=delete_input, name=name
    ) + """
def _brep_from_guid(g):
    obj = sc.doc.Objects.Find(g)
    if obj is None or obj.Geometry is None:
        return None
    geom = obj.Geometry
    if isinstance(geom, rg.Brep):
        return geom.DuplicateBrep()
    try:
        return rg.Brep.TryConvertBrep(geom)
    except Exception:
        return None


def _doc_boolean_difference(keep_g, cutter_g, tol):
    # RhinoCommon fallback: rs.BooleanDifference can return None when solids
    # only touch (coincident faces). CreateBooleanDifference is more tolerant,
    # and a loosened tolerance gives sich-beruehrenden Solids a second chance.
    keep_brep = _brep_from_guid(keep_g)
    cutter_breps = []
    for cg in cutter_g:
        cb = _brep_from_guid(cg)
        if cb is not None:
            cutter_breps.append(cb)
    if keep_brep is None or not cutter_breps:
        return None
    for cur_tol in (tol, tol * 10.0):
        try:
            res = rg.Brep.CreateBooleanDifference([keep_brep], cutter_breps, cur_tol)
        except Exception:
            res = None
        valid = [b for b in (res or []) if b and b.IsValid]
        if valid:
            return valid
    return None


keep_guid = System.Guid(_keep)
remove_guids = [System.Guid(r) for r in _removes]
tol = max(sc.doc.ModelAbsoluteTolerance, 1e-6)

# Try rs.BooleanDifference first (delete_input=False so deletion is explicit:
# the base is replaced by the result below, but the subtracted objects survive
# unless _delete is True — stops the "subtract the legs -> legs silently
# consumed" data loss seen in the live test). Nothing is archived/deleted until
# we KNOW the operation produced a result, so a failure leaves keep+cutters
# untouched.
diff = rs.BooleanDifference(keep_guid, remove_guids, False)
diff_breps = None
if not diff:
    # Fallback for sich-beruehrende Solids (coincident faces): RhinoCommon
    # boolean, retried with a loosened tolerance.
    diff_breps = _doc_boolean_difference(keep_guid, remove_guids, tol)

if diff:
    archive_object(keep_guid)
    if _delete:
        for g in remove_guids:
            archive_object(g)
    rs.DeleteObject(keep_guid)
    if _delete:
        rs.DeleteObjects(remove_guids)
    if len(diff) == 1:
        add_object_metadata(diff[0], _name, "Boolean difference")
    else:
        # Multiple result Breps must get UNIQUE names. add_object_metadata
        # archives+deletes any existing same-named object on the layer, so
        # reusing _name for every part would make each later part wipe out
        # the previous one (only the last would survive) — exactly the data
        # loss this fix targets.
        for i, d in enumerate(diff):
            add_object_metadata(d, "{0}_{1}".format(_name, i + 1), "Boolean difference part {0}".format(i + 1))
    rs.Redraw()
    diff_str = [str(d) for d in diff]
    result = "Boolean difference result: " + str(diff_str)
elif diff_breps:
    archive_object(keep_guid)
    if _delete:
        for g in remove_guids:
            archive_object(g)
    rs.DeleteObject(keep_guid)
    if _delete:
        rs.DeleteObjects(remove_guids)
    new_ids = []
    multi = len(diff_breps) > 1
    for i, b in enumerate(diff_breps):
        new_id = sc.doc.Objects.AddBrep(b)
        if new_id and new_id != System.Guid.Empty:
            if multi:
                add_object_metadata(new_id, "{0}_{1}".format(_name, i + 1), "Boolean difference part {0}".format(i + 1))
            else:
                add_object_metadata(new_id, _name, "Boolean difference")
            new_ids.append(str(new_id))
    rs.Redraw()
    result = "Boolean difference result (fallback): " + str(new_ids)
else:
    # No result from either path: leave keep + cutters untouched. The
    # "Fehler:" prefix makes _result_signals_error (prompts.py) flag this.
    result = "Fehler: Boolean Difference lieferte kein Ergebnis (Flaechen beruehren sich evtl. nur — Cutter minimal ueberlappen lassen)."
"""


def boolean_intersection_code(
    object_ids: List[str],
    delete_input: bool = True,
    name: str = "BoolIntersect",
) -> str:
    """Keep only the overlapping volume of two or more objects."""
    return inject_params(ids=object_ids, delete=delete_input, name=name) + """
if len(_ids) != 2:
    # Only guids[0] and guids[1] are intersected; 3rd+ inputs were silently
    # ignored (and previously archived) while success was reported. Be honest
    # about the actual capability instead of returning a wrong result.
    result = "Fehler: Boolean Intersection braucht genau 2 Objekte (erhalten: {0}).".format(len(_ids))
else:
    guids = [System.Guid(i) for i in _ids]
    # Intersect without auto-delete; archive + delete the two consumed inputs
    # only on success (no phantom archive/undo on a non-overlapping pair, and
    # never archive inputs beyond the two actually intersected).
    inter = rs.BooleanIntersection(guids[0], guids[1], False)
    if inter:
        if _delete:
            archive_object(guids[0])
            archive_object(guids[1])
            rs.DeleteObjects([guids[0], guids[1]])
        for it in inter:
            add_object_metadata(it, _name, "Boolean intersection")
        rs.Redraw()
        inter_str = [str(it) for it in inter]
        result = "Boolean intersection result: " + str(inter_str)
    else:
        result = "Fehler: Boolean Intersection lieferte kein Ergebnis (Solids ueberlappen nicht)."
"""


def boolean_split_code(
    object_id: str,
    cutter_id: str,
    delete_input: bool = False,
    name: str = "BoolSplit",
) -> str:
    """Split a brep with another brep."""
    return inject_params(
        oid=object_id, cid=cutter_id, delete=delete_input, name=name
    ) + """
_oid_g = System.Guid(_oid)
# Split without auto-delete; archive + delete the consumed object only on
# success. SplitBrep deletes only the object being split (not the cutter), so a
# failed split leaves both inputs untouched and unarchived.
splits = rs.SplitBrep(_oid_g, System.Guid(_cid), False)
if splits:
    if _delete:
        archive_object(_oid_g)
        rs.DeleteObject(_oid_g)
    for i, s in enumerate(splits):
        add_object_metadata(s, "{0}_{1}".format(_name, i), "Boolean split fragment")
    rs.Redraw()
    splits_str = [str(s) for s in splits]
    result = "Split into {0} parts: {1}".format(len(splits_str), splits_str)
else:
    result = "Fehler: Boolean Split ergab keine Teile (Cutter schneidet das Objekt nicht)."
"""


def create_hole_code(
    object_id: str,
    center: List[float],
    diameter: float,
    direction: List[float] = [0, 0, -1],
    depth: float = 0.0,
    through: bool = True,
) -> str:
    """Cut a cylindrical round hole into a Brep."""
    return inject_params(
        oid=object_id,
        center=center,
        diameter=diameter,
        direction=direction,
        depth=depth,
        through=through,
    ) + """
def _point3(values, fallback=None):
    try:
        if len(values) >= 3:
            return rg.Point3d(float(values[0]), float(values[1]), float(values[2]))
    except Exception:
        pass
    return fallback


def _vector3(values, fallback=None):
    try:
        if len(values) >= 3:
            return rg.Vector3d(float(values[0]), float(values[1]), float(values[2]))
    except Exception:
        pass
    return fallback


def _doc_boolean_difference_fallback(target_guid, cutter_breps):
    cutter_ids = []
    try:
        for cutter_brep in cutter_breps:
            cid = sc.doc.Objects.AddBrep(cutter_brep)
            if cid and cid != System.Guid.Empty:
                cutter_ids.append(cid)
        if not cutter_ids:
            return []
        result_ids = rs.BooleanDifference(target_guid, cutter_ids, False)
        return list(result_ids or [])
    finally:
        for cid in cutter_ids:
            if sc.doc.Objects.Find(cid) is not None:
                rs.DeleteObject(cid)


guid = System.Guid(_oid)
obj = sc.doc.Objects.Find(guid)
if obj is None or obj.Geometry is None:
    result = "Error: object not found"
else:
    geom = obj.Geometry
    original_name = rs.ObjectName(guid) or "HoleBrep"
    if isinstance(geom, rg.Brep):
        brep = geom.DuplicateBrep()
    else:
        try:
            brep = rg.Brep.TryConvertBrep(geom)
        except Exception:
            brep = None

    center_pt = _point3(_center)
    direction_vec = _vector3(_direction, rg.Vector3d(0, 0, -1))
    tol = max(sc.doc.ModelAbsoluteTolerance, 1e-6)
    if brep is None:
        result = "Error: object is not a Brep"
    elif center_pt is None:
        result = "Error: center must be [x, y, z]"
    elif _diameter <= tol:
        result = "Error: diameter must be greater than zero"
    elif direction_vec is None or not direction_vec.Unitize():
        result = "Error: direction must be a non-zero [x, y, z] vector"
    elif (not _through) and _depth <= tol:
        result = "Error: depth must be greater than zero for blind holes"
    else:
        bbox = brep.GetBoundingBox(True)
        diag = max(float(bbox.Diagonal.Length), float(_diameter) * 4.0, 1.0)
        pad = max(tol * 20.0, diag * 0.01)
        if _through:
            length = diag * 3.0
            start = center_pt - direction_vec * (length * 0.5)
        else:
            length = float(_depth) + pad
            start = center_pt - direction_vec * pad

        radius = float(_diameter) * 0.5
        plane = rg.Plane(start, direction_vec)
        # rg.Cylinder has NO (Plane, radius, length) overload — go through
        # Circle (Plane + radius), then Cylinder(Circle, height).
        cylinder = rg.Cylinder(rg.Circle(plane, radius), length)
        cutter = cylinder.ToBrep(True, True)
        if cutter is None or not cutter.IsValid:
            result = "Error: could not build cylindrical hole cutter"
        else:
            try:
                diff = rg.Brep.CreateBooleanDifference([brep], [cutter], tol)
            except Exception as e:
                diff = None
                err = str(e)
            valid = [item for item in (diff or []) if item and item.IsValid]
            if valid:
                if len(valid) == 1:
                    archive_object(guid)
                    sc.doc.Objects.Replace(guid, valid[0])
                    # Topology changed — strip primitive-recipe tags so the
                    # structure-slider can't rebuild the object as a fresh
                    # primitive and silently wipe out this hole.
                    clear_editable_recipe(guid)
                    rs.Redraw()
                    mode = "through" if _through else "blind"
                    result = "Created {0} round hole diameter {1} mm in {2}".format(mode, _diameter, str(guid))
                else:
                    archive_object(guid)
                    rs.DeleteObject(guid)
                    created = []
                    for idx, item in enumerate(valid):
                        new_id = sc.doc.Objects.AddBrep(item)
                        if new_id and new_id != System.Guid.Empty:
                            add_object_metadata(new_id, original_name, "Hole result {0}".format(idx + 1))
                            created.append(str(new_id))
                    rs.Redraw()
                    result = "Hole operation produced {0} Breps: {1}".format(len(created), created)
            else:
                fallback_ids = _doc_boolean_difference_fallback(guid, [cutter])
                if fallback_ids:
                    archive_object(guid)
                    rs.DeleteObject(guid)
                    for fid in fallback_ids:
                        clear_editable_recipe(fid)
                        add_object_metadata(fid, original_name, "Hole boolean fallback")
                    rs.Redraw()
                    mode = "through" if _through else "blind"
                    result = "Created {0} round hole diameter {1} mm via fallback: {2}".format(
                        mode, _diameter, [str(fid) for fid in fallback_ids]
                    )
                elif not diff:
                    result = "Error: hole boolean failed{0}".format(": " + err if 'err' in locals() else "")
                else:
                    result = "Error: hole boolean produced no valid Breps"
"""


def create_slot_code(
    object_id: str,
    center: List[float],
    length: float,
    width: float,
    slot_axis: List[float] = [1, 0, 0],
    direction: List[float] = [0, 0, -1],
    depth: float = 0.0,
    through: bool = True,
    end_shape: str = "square",
) -> str:
    """Cut a slot/groove into a Brep.

    end_shape:
      - "square" (default): rectangular cutter, flat ends. Matches the
        German "Nut" (groove) semantics — e.g. a slot that runs across
        the full face of a board.
      - "rounded": obround cutter with semicircular end caps. Matches
        the classic engineering "slot" (e.g. fastener slot).
    """
    return inject_params(
        oid=object_id,
        center=center,
        length=length,
        width=width,
        slot_axis=slot_axis,
        direction=direction,
        depth=depth,
        through=through,
        end_shape=end_shape,
    ) + """
def _point3(values, fallback=None):
    try:
        if len(values) >= 3:
            return rg.Point3d(float(values[0]), float(values[1]), float(values[2]))
    except Exception:
        pass
    return fallback


def _vector3(values, fallback=None):
    try:
        if len(values) >= 3:
            return rg.Vector3d(float(values[0]), float(values[1]), float(values[2]))
    except Exception:
        pass
    return fallback


def _dot(a, b):
    return float(a.X * b.X + a.Y * b.Y + a.Z * b.Z)


def _perpendicular_to(vec):
    if abs(vec.Z) < 0.9:
        candidate = rg.Vector3d.CrossProduct(rg.Vector3d.ZAxis, vec)
    else:
        candidate = rg.Vector3d.CrossProduct(rg.Vector3d.XAxis, vec)
    if not candidate.Unitize():
        candidate = rg.Vector3d(1, 0, 0)
    return candidate


def _doc_boolean_difference_fallback(target_guid, cutter_breps):
    cutter_ids = []
    try:
        for cutter_brep in cutter_breps:
            cid = sc.doc.Objects.AddBrep(cutter_brep)
            if cid and cid != System.Guid.Empty:
                cutter_ids.append(cid)
        if not cutter_ids:
            return []
        result_ids = rs.BooleanDifference(target_guid, cutter_ids, False)
        return list(result_ids or [])
    finally:
        for cid in cutter_ids:
            if sc.doc.Objects.Find(cid) is not None:
                rs.DeleteObject(cid)


guid = System.Guid(_oid)
obj = sc.doc.Objects.Find(guid)
if obj is None or obj.Geometry is None:
    result = "Error: object not found"
else:
    geom = obj.Geometry
    original_name = rs.ObjectName(guid) or "SlotBrep"
    if isinstance(geom, rg.Brep):
        brep = geom.DuplicateBrep()
    else:
        try:
            brep = rg.Brep.TryConvertBrep(geom)
        except Exception:
            brep = None

    center_pt = _point3(_center)
    direction_vec = _vector3(_direction, rg.Vector3d(0, 0, -1))
    axis_vec = _vector3(_slot_axis, rg.Vector3d(1, 0, 0))
    tol = max(sc.doc.ModelAbsoluteTolerance, 1e-6)
    if brep is None:
        result = "Error: object is not a Brep"
    elif center_pt is None:
        result = "Error: center must be [x, y, z]"
    elif _width <= tol:
        result = "Error: width must be greater than zero"
    elif _length <= tol:
        result = "Error: length must be greater than zero"
    elif _length < _width:
        result = "Error: slot length must be greater than or equal to width"
    elif direction_vec is None or not direction_vec.Unitize():
        result = "Error: direction must be a non-zero [x, y, z] vector"
    elif axis_vec is None or not axis_vec.Unitize():
        result = "Error: slot_axis must be a non-zero [x, y, z] vector"
    elif (not _through) and _depth <= tol:
        result = "Error: depth must be greater than zero for blind slots"
    else:
        # Project the slot axis onto the surface plane perpendicular to the cut direction.
        axis_vec = axis_vec - direction_vec * _dot(axis_vec, direction_vec)
        if not axis_vec.Unitize():
            axis_vec = _perpendicular_to(direction_vec)
        side_vec = rg.Vector3d.CrossProduct(direction_vec, axis_vec)
        if not side_vec.Unitize():
            axis_vec = _perpendicular_to(direction_vec)
            side_vec = rg.Vector3d.CrossProduct(direction_vec, axis_vec)
            side_vec.Unitize()

        bbox = brep.GetBoundingBox(True)
        diag = max(float(bbox.Diagonal.Length), float(_length) * 2.0, 1.0)
        pad = max(tol * 20.0, diag * 0.01)
        if _through:
            cutter_len = diag * 3.0
            start_center = center_pt - direction_vec * (cutter_len * 0.5)
        else:
            cutter_len = float(_depth) + pad
            start_center = center_pt - direction_vec * pad

        cutters = []
        radius = float(_width) * 0.5
        is_square = str(_end_shape or "square").lower() == "square"
        # Square ends: rectangular cutter spans the full length so the
        # end faces are flat (matches "Nut"/groove semantics — a slot
        # that runs across the whole face stays open at both ends).
        # Rounded ends: rect spans (length - width) so the two cylinder
        # caps complete the obround shape.
        if is_square:
            rect_len = float(_length)
            cap_offsets = []
        else:
            rect_len = float(_length) - float(_width)
            cap_offsets = (
                [0.0]
                if rect_len <= tol
                else [-rect_len * 0.5, rect_len * 0.5]
            )
        if rect_len > tol:
            plane = rg.Plane(start_center, axis_vec, side_vec)
            box = rg.Box(
                plane,
                rg.Interval(-rect_len * 0.5, rect_len * 0.5),
                rg.Interval(-float(_width) * 0.5, float(_width) * 0.5),
                rg.Interval(0.0, cutter_len),
            )
            box_brep = box.ToBrep()
            if box_brep and box_brep.IsValid:
                cutters.append(box_brep)

        for offset in cap_offsets:
            cap_start = start_center + axis_vec * offset
            cap_plane = rg.Plane(cap_start, direction_vec)
            # rg.Cylinder has NO (Plane, radius, length) overload — go
            # through Circle (Plane + radius), then Cylinder(Circle, height).
            cylinder = rg.Cylinder(rg.Circle(cap_plane, radius), cutter_len)
            cap_brep = cylinder.ToBrep(True, True)
            if cap_brep and cap_brep.IsValid:
                cutters.append(cap_brep)

        if not cutters:
            result = "Error: could not build slot cutter"
        else:
            try:
                diff = rg.Brep.CreateBooleanDifference([brep], cutters, tol)
            except Exception as e:
                diff = None
                err = str(e)
            valid = [item for item in (diff or []) if item and item.IsValid]
            if valid:
                if len(valid) == 1:
                    archive_object(guid)
                    sc.doc.Objects.Replace(guid, valid[0])
                    # Topology changed — strip primitive-recipe tags so the
                    # structure-slider can't rebuild the object as a fresh
                    # primitive and silently wipe out this slot.
                    clear_editable_recipe(guid)
                    rs.Redraw()
                    mode = "through" if _through else "blind"
                    result = "Created {0} slot {1}x{2} mm in {3}".format(mode, _length, _width, str(guid))
                else:
                    archive_object(guid)
                    rs.DeleteObject(guid)
                    created = []
                    for idx, item in enumerate(valid):
                        new_id = sc.doc.Objects.AddBrep(item)
                        if new_id and new_id != System.Guid.Empty:
                            add_object_metadata(new_id, original_name, "Slot result {0}".format(idx + 1))
                            created.append(str(new_id))
                    rs.Redraw()
                    result = "Slot operation produced {0} Breps: {1}".format(len(created), created)
            else:
                fallback_ids = _doc_boolean_difference_fallback(guid, cutters)
                if fallback_ids:
                    archive_object(guid)
                    rs.DeleteObject(guid)
                    for fid in fallback_ids:
                        clear_editable_recipe(fid)
                        add_object_metadata(fid, original_name, "Slot boolean fallback")
                    rs.Redraw()
                    mode = "through" if _through else "blind"
                    result = "Created {0} slot {1}x{2} mm via fallback: {3}".format(
                        mode, _length, _width, [str(fid) for fid in fallback_ids]
                    )
                elif not diff:
                    result = "Error: slot boolean failed{0}".format(": " + err if 'err' in locals() else "")
                else:
                    result = "Error: slot boolean produced no valid Breps"
"""
