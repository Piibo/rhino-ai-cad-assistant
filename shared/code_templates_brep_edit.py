"""Code templates: brep_edit operations. See code_templates.py."""
from __future__ import annotations

from typing import List

from ._codegen_base import inject_params

__all__ = [
    "resize_box_face_code",
    "resize_cylinder_face_code",
    "resize_extrusion_face_code",
    "fillet_brep_edge_code",
    "round_edges_by_rule_code",
    "chamfer_brep_edge_code",
    "move_brep_face_along_normal_code",
    "move_brep_face_in_direction_code",
]


def resize_box_face_code(
    object_id: str,
    face_index: int,
    distance: float = 1.0,
) -> str:
    """Resize one axis-aligned face of a primitive box in place.

    Position and size are taken from the box's CURRENT bounding box, not
    from the recipe stored at creation time, so a box that was moved or
    scaled afterwards still resizes where it stands instead of jumping
    back to its origin. Rotated boxes are refused, because the rebuild is
    axis-aligned and would silently discard the rotation.
    """
    return inject_params(oid=object_id, fidx=face_index, dist=distance) + """
def _bbox_tuple(_bbox):
    return (
        round(_bbox.Min.X, 9), round(_bbox.Min.Y, 9), round(_bbox.Min.Z, 9),
        round(_bbox.Max.X, 9), round(_bbox.Max.Y, 9), round(_bbox.Max.Z, 9),
    )

guid = System.Guid(_oid)
obj = sc.doc.Objects.Find(guid)
if obj is None or obj.Geometry is None:
    result = "Error: object not found"
elif (rs.GetUserText(guid, "object_class") or "") != "primitive_box":
    result = "Error: object is not an editable primitive_box"
else:
    geom = obj.Geometry
    if isinstance(geom, rg.Brep):
        brep = geom.DuplicateBrep()
    else:
        try:
            brep = rg.Brep.TryConvertBrep(geom)
        except Exception:
            brep = None

    if brep is None:
        result = "Error: object is not a Brep"
    elif _fidx < 0 or _fidx >= brep.Faces.Count:
        result = "Error: face index {0} out of range".format(_fidx)
    else:
        face = brep.Faces[_fidx]
        if not face.IsPlanar(sc.doc.ModelAbsoluteTolerance):
            result = "Error: face {0} is not planar".format(_fidx)
        else:
            du = face.Domain(0)
            dv = face.Domain(1)
            u = 0.5 * (du.T0 + du.T1)
            v = 0.5 * (dv.T0 + dv.T1)
            normal = face.NormalAt(u, v)
            if face.OrientationIsReversed:
                normal.Reverse()
            if not normal.Unitize():
                result = "Error: face {0} has no valid normal".format(_fidx)
            else:
                ax = abs(normal.X)
                ay = abs(normal.Y)
                az = abs(normal.Z)
                if max(ax, ay, az) < 0.9999:
                    # Face normal is not aligned to a world axis -> the box
                    # has been rotated. The rebuild below is axis-aligned
                    # and would silently drop the rotation, so refuse and
                    # point at the general-purpose face tool instead.
                    result = (
                        "Error: face {0} is not axis-aligned; "
                        "resize_box_face only supports axis-aligned boxes. "
                        "Use move_brep_face_along_normal for rotated "
                        "geometry.".format(_fidx)
                    )
                else:
                    # Authoritative size/position from the LIVE geometry,
                    # so a box moved after creation resizes in place.
                    cur = geom.GetBoundingBox(True)
                    x = float(cur.Min.X)
                    y = float(cur.Min.Y)
                    z = float(cur.Min.Z)
                    w = float(cur.Max.X - cur.Min.X)
                    d = float(cur.Max.Y - cur.Min.Y)
                    h = float(cur.Max.Z - cur.Min.Z)
                    if ax >= ay and ax >= az:
                        side = "x_max" if normal.X >= 0 else "x_min"
                    elif ay >= ax and ay >= az:
                        side = "y_max" if normal.Y >= 0 else "y_min"
                    else:
                        side = "z_max" if normal.Z >= 0 else "z_min"

                    tol = max(sc.doc.ModelAbsoluteTolerance, 1e-6)
                    new_x, new_y, new_z = x, y, z
                    new_w, new_d, new_h = w, d, h

                    if side == "x_max":
                        new_w = w + _dist
                    elif side == "x_min":
                        new_x = x - _dist
                        new_w = w + _dist
                    elif side == "y_max":
                        new_d = d + _dist
                    elif side == "y_min":
                        new_y = y - _dist
                        new_d = d + _dist
                    elif side == "z_max":
                        new_h = h + _dist
                    else:
                        new_z = z - _dist
                        new_h = h + _dist

                    if new_w <= tol or new_d <= tol or new_h <= tol:
                        result = "Error: resize would collapse the box"
                    else:
                        new_bbox = rg.BoundingBox(
                            rg.Point3d(new_x, new_y, new_z),
                            rg.Point3d(new_x + new_w, new_y + new_d, new_z + new_h)
                        )
                        new_brep = rg.Brep.CreateFromBox(new_bbox)
                        if new_brep is None or not new_brep.IsValid:
                            result = "Error: could not rebuild primitive_box"
                        else:
                            archive_object(guid)
                            replaced = sc.doc.Objects.Replace(guid, new_brep)
                            if not replaced:
                                result = "Error: could not replace primitive_box"
                            else:
                                bbox_data = [
                                    [new_x, new_y, new_z],
                                    [new_x + new_w, new_y, new_z],
                                    [new_x + new_w, new_y + new_d, new_z],
                                    [new_x, new_y + new_d, new_z],
                                    [new_x, new_y, new_z + new_h],
                                    [new_x + new_w, new_y, new_z + new_h],
                                    [new_x + new_w, new_y + new_d, new_z + new_h],
                                    [new_x, new_y + new_d, new_z + new_h],
                                ]
                                rs.SetUserText(guid, "object_class", "primitive_box")
                                rs.SetUserText(guid, "editable_recipe_type", "box")
                                rs.SetUserText(guid, "editable_operations", "resize_box_face,fillet_brep_edge,chamfer_brep_edge")
                                rs.SetUserText(
                                    guid,
                                    "editable_recipe",
                                    json.dumps(
                                        {
                                            "x": float(new_x),
                                            "y": float(new_y),
                                            "z": float(new_z),
                                            "width": float(new_w),
                                            "depth": float(new_d),
                                            "height": float(new_h),
                                        }
                                    ),
                                )
                                rs.SetUserText(guid, "bbox", json.dumps(bbox_data))
                                rs.SetUserText(guid, "description", "Box {0}x{1}x{2}".format(new_w, new_d, new_h))
                                rs.Redraw()
                                obj_after = sc.doc.Objects.Find(guid)
                                after_geom = obj_after.Geometry if obj_after and obj_after.Geometry else None
                                after_bbox = after_geom.GetBoundingBox(True) if after_geom else None
                                if after_bbox is None:
                                    result = "Error: resized box could not be read back"
                                elif _bbox_tuple(cur) == _bbox_tuple(after_bbox):
                                    result = "Error: resize_box_face replaced the box, but its bounding box did not change"
                                else:
                                    result = "Resized box face {0} by {1} on {2}".format(_fidx, _dist, side)
"""


def resize_cylinder_face_code(
    object_id: str,
    face_index: int,
    distance: float = 1.0,
) -> str:
    """Resize one face of a vertical primitive cylinder in place.

    Supported:
    - top cap -> height grows/shrinks upward
    - bottom cap -> base moves and height changes
    - side face -> radius grows/shrinks uniformly

    The tool intentionally only supports the vertical cylinders produced by
    ``create_cylinder``. Rotated or later non-uniformly scaled cylinders are
    refused, because the rebuild path would no longer be deterministic.
    """
    return inject_params(oid=object_id, fidx=face_index, dist=distance) + """
def _bbox_tuple(_bbox):
    return (
        round(_bbox.Min.X, 9), round(_bbox.Min.Y, 9), round(_bbox.Min.Z, 9),
        round(_bbox.Max.X, 9), round(_bbox.Max.Y, 9), round(_bbox.Max.Z, 9),
    )

guid = System.Guid(_oid)
obj = sc.doc.Objects.Find(guid)
if obj is None or obj.Geometry is None:
    result = "Error: object not found"
elif (rs.GetUserText(guid, "object_class") or "") != "primitive_cylinder":
    result = "Error: object is not an editable primitive_cylinder"
else:
    geom = obj.Geometry
    if isinstance(geom, rg.Brep):
        brep = geom.DuplicateBrep()
    else:
        try:
            brep = rg.Brep.TryConvertBrep(geom)
        except Exception:
            brep = None

    if brep is None:
        result = "Error: object is not a Brep"
    elif _fidx < 0 or _fidx >= brep.Faces.Count:
        result = "Error: face index {0} out of range".format(_fidx)
    else:
        recipe_raw = rs.GetUserText(guid, "editable_recipe") or ""
        recipe = {}
        if recipe_raw:
            try:
                recipe = json.loads(recipe_raw)
            except Exception:
                recipe = {}
        cap = bool(recipe.get("cap", True))

        cur = geom.GetBoundingBox(True)
        if not cur.IsValid:
            result = "Error: invalid cylinder bounding box"
        else:
            cx = 0.5 * (cur.Min.X + cur.Max.X)
            cy = 0.5 * (cur.Min.Y + cur.Max.Y)
            bz = float(cur.Min.Z)
            h = float(cur.Max.Z - cur.Min.Z)
            rx = 0.5 * float(cur.Max.X - cur.Min.X)
            ry = 0.5 * float(cur.Max.Y - cur.Min.Y)
            tol = max(sc.doc.ModelAbsoluteTolerance, 1e-6)

            if abs(rx - ry) > tol:
                result = (
                    "Error: primitive_cylinder was non-uniformly scaled; "
                    "resize_cylinder_face only supports circular vertical cylinders"
                )
            elif h <= tol or rx <= tol or ry <= tol:
                result = "Error: cylinder dimensions are too small"
            else:
                r = 0.5 * (rx + ry)
                face = brep.Faces[_fidx]
                side = None
                if face.IsPlanar(sc.doc.ModelAbsoluteTolerance):
                    du = face.Domain(0)
                    dv = face.Domain(1)
                    u = 0.5 * (du.T0 + du.T1)
                    v = 0.5 * (dv.T0 + dv.T1)
                    normal = face.NormalAt(u, v)
                    if face.OrientationIsReversed:
                        normal.Reverse()
                    if not normal.Unitize():
                        result = "Error: face {0} has no valid normal".format(_fidx)
                    else:
                        if abs(normal.Z) < 0.9999:
                            result = (
                                "Error: planar cylinder face is not horizontal; "
                                "resize_cylinder_face only supports vertical primitive cylinders"
                            )
                        else:
                            side = "z_max" if normal.Z >= 0 else "z_min"
                else:
                    side = "radius"

                if side:
                    new_bz = bz
                    new_h = h
                    new_r = r

                    if side == "z_max":
                        new_h = h + _dist
                    elif side == "z_min":
                        new_bz = bz - _dist
                        new_h = h + _dist
                    else:
                        new_r = r + _dist

                    if new_h <= tol:
                        result = "Error: resize would collapse the cylinder height"
                    elif new_r <= tol:
                        result = "Error: resize would collapse the cylinder radius"
                    else:
                        plane = rg.Plane(rg.Point3d(cx, cy, new_bz), rg.Vector3d.ZAxis)
                        circle = rg.Circle(plane, new_r)
                        cyl = rg.Cylinder(circle, new_h)
                        new_brep = cyl.ToBrep(cap, cap)
                        if new_brep is None or not new_brep.IsValid:
                            result = "Error: could not rebuild primitive_cylinder"
                        else:
                            archive_object(guid)
                            replaced = sc.doc.Objects.Replace(guid, new_brep)
                            if not replaced:
                                result = "Error: could not replace primitive_cylinder"
                            else:
                                rs.SetUserText(guid, "object_class", "primitive_cylinder")
                                rs.SetUserText(guid, "editable_recipe_type", "cylinder")
                                rs.SetUserText(guid, "editable_operations", "resize_cylinder_face,fillet_brep_edge,chamfer_brep_edge")
                                rs.SetUserText(
                                    guid,
                                    "editable_recipe",
                                    json.dumps(
                                        {
                                            "base_x": float(cx),
                                            "base_y": float(cy),
                                            "base_z": float(new_bz),
                                            "radius": float(new_r),
                                            "height": float(new_h),
                                            "cap": bool(cap),
                                        }
                                    ),
                                )
                                rs.SetUserText(guid, "description", "Cylinder r={0} h={1}".format(new_r, new_h))
                                rs.Redraw()
                                obj_after = sc.doc.Objects.Find(guid)
                                after_geom = obj_after.Geometry if obj_after and obj_after.Geometry else None
                                after_bbox = after_geom.GetBoundingBox(True) if after_geom else None
                                if after_bbox is None:
                                    result = "Error: resized cylinder could not be read back"
                                elif _bbox_tuple(cur) == _bbox_tuple(after_bbox):
                                    result = "Error: resize_cylinder_face replaced the cylinder, but its bounding box did not change"
                                else:
                                    result = "Resized cylinder face {0} by {1} on {2}".format(_fidx, _dist, side)
"""


def resize_extrusion_face_code(
    object_id: str,
    face_index: int,
    distance: float = 1.0,
) -> str:
    """Resize a straight capped primitive extrusion by moving one end cap."""
    return inject_params(oid=object_id, fidx=face_index, dist=distance) + """
def _bbox_tuple(_bbox):
    return (
        round(_bbox.Min.X, 9), round(_bbox.Min.Y, 9), round(_bbox.Min.Z, 9),
        round(_bbox.Max.X, 9), round(_bbox.Max.Y, 9), round(_bbox.Max.Z, 9),
    )

def _dot(_a, _b):
    return (_a.X * _b.X) + (_a.Y * _b.Y) + (_a.Z * _b.Z)

guid = System.Guid(_oid)
obj = sc.doc.Objects.Find(guid)
if obj is None or obj.Geometry is None:
    result = "Error: object not found"
elif (rs.GetUserText(guid, "object_class") or "") != "primitive_extrusion":
    result = "Error: object is not an editable primitive_extrusion"
else:
    geom = obj.Geometry
    if isinstance(geom, rg.Brep):
        brep = geom.DuplicateBrep()
    else:
        try:
            brep = rg.Brep.TryConvertBrep(geom)
        except Exception:
            brep = None

    recipe_raw = rs.GetUserText(guid, "editable_recipe") or ""
    recipe = {}
    if recipe_raw:
        try:
            recipe = json.loads(recipe_raw)
        except Exception:
            recipe = {}
    axis = rg.Vector3d(
        float(recipe.get("dx", 0.0)),
        float(recipe.get("dy", 0.0)),
        float(recipe.get("dz", 0.0)),
    )
    cap = bool(recipe.get("cap", True))

    if brep is None:
        result = "Error: object is not a Brep"
    elif _fidx < 0 or _fidx >= brep.Faces.Count:
        result = "Error: face index {0} out of range".format(_fidx)
    elif not axis.Unitize():
        result = "Error: primitive_extrusion has no valid stored axis"
    else:
        tol = max(sc.doc.ModelAbsoluteTolerance, 1e-6)
        candidates = []
        for idx, face in enumerate(brep.Faces):
            if not face.IsPlanar(sc.doc.ModelAbsoluteTolerance):
                continue
            du = face.Domain(0)
            dv = face.Domain(1)
            u = 0.5 * (du.T0 + du.T1)
            v = 0.5 * (dv.T0 + dv.T1)
            normal = face.NormalAt(u, v)
            if face.OrientationIsReversed:
                normal.Reverse()
            if not normal.Unitize():
                continue
            align = _dot(normal, axis)
            if abs(abs(align) - 1.0) > 0.001:
                continue
            pt = face.PointAt(u, v)
            candidates.append(
                {
                    "index": idx,
                    "point": pt,
                    "normal": normal,
                    "t": _dot(rg.Vector3d(pt.X, pt.Y, pt.Z), axis),
                }
            )

        if len(candidates) < 2:
            result = (
                "Error: primitive_extrusion braucht zwei planare Endkappen; "
                "keine gueltige Cap-Geometrie gefunden"
            )
        else:
            top = max(candidates, key=lambda c: c["t"])
            bottom = min(candidates, key=lambda c: c["t"])
            current_length = top["t"] - bottom["t"]
            if current_length <= tol:
                result = "Error: primitive_extrusion has invalid current length"
            elif _fidx not in {int(top["index"]), int(bottom["index"])}:
                result = (
                    "Error: resize_extrusion_face unterstuetzt nur die beiden "
                    "Endkappen, nicht die seitlichen Flaechen"
                )
            else:
                selected_top = int(_fidx) == int(top["index"])
                new_length = current_length + _dist
                if new_length <= tol:
                    result = "Error: resize would collapse the extrusion length"
                else:
                    anchor_idx = int(bottom["index"] if selected_top else top["index"])
                    anchor_face = brep.Faces[anchor_idx]
                    anchor_dup = anchor_face.DuplicateFace(False)
                    edge_curves = anchor_dup.DuplicateEdgeCurves(True) if anchor_dup else None
                    joined = rg.Curve.JoinCurves(edge_curves, sc.doc.ModelAbsoluteTolerance) if edge_curves else None
                    profile = joined[0] if joined and len(joined) >= 1 else None
                    if profile is None or not profile.IsClosed:
                        result = "Error: could not reconstruct extrusion profile from end cap"
                    else:
                        vec = (
                            rg.Vector3d(axis.X * new_length, axis.Y * new_length, axis.Z * new_length)
                            if selected_top
                            else rg.Vector3d(-axis.X * new_length, -axis.Y * new_length, -axis.Z * new_length)
                        )
                        srf = rg.Surface.CreateExtrusion(profile, vec)
                        new_brep = srf.ToBrep() if srf else None
                        if new_brep and cap:
                            capped = new_brep.CapPlanarHoles(sc.doc.ModelAbsoluteTolerance)
                            if capped is not None:
                                new_brep = capped
                        # Geschlossene Knick-Profile ergeben sonst eine Seitenwand
                        # mit Naht statt echter Eckkanten; ohne SplitKinkyFaces
                        # regressiert ein Resize das Objekt zurueck auf die
                        # Naht-Topologie und macht die Ecken wieder unfilletbar.
                        if new_brep is not None:
                            try:
                                split_brep = new_brep.DuplicateBrep()
                                before_face_count = split_brep.Faces.Count
                                split_brep.Faces.SplitKinkyFaces()
                                if split_brep.IsValid and split_brep.Faces.Count > before_face_count:
                                    new_brep = split_brep
                            except Exception:
                                pass
                        if new_brep is None or not new_brep.IsValid:
                            result = "Error: could not rebuild primitive_extrusion"
                        else:
                            before_bbox = geom.GetBoundingBox(True)
                            archive_object(guid)
                            replaced = sc.doc.Objects.Replace(guid, new_brep)
                            if not replaced:
                                result = "Error: could not replace primitive_extrusion"
                            else:
                                rs.SetUserText(guid, "object_class", "primitive_extrusion")
                                rs.SetUserText(guid, "editable_recipe_type", "axis_extrusion")
                                rs.SetUserText(guid, "editable_operations", "resize_extrusion_face,fillet_brep_edge,chamfer_brep_edge")
                                rs.SetUserText(
                                    guid,
                                    "editable_recipe",
                                    json.dumps(
                                        {
                                            "dx": float(vec.X if selected_top else -vec.X),
                                            "dy": float(vec.Y if selected_top else -vec.Y),
                                            "dz": float(vec.Z if selected_top else -vec.Z),
                                            "cap": bool(cap),
                                        }
                                    ),
                                )
                                rs.Redraw()
                                obj_after = sc.doc.Objects.Find(guid)
                                after_geom = obj_after.Geometry if obj_after and obj_after.Geometry else None
                                after_bbox = after_geom.GetBoundingBox(True) if after_geom else None
                                if after_bbox is None:
                                    result = "Error: resized extrusion could not be read back"
                                elif _bbox_tuple(before_bbox) == _bbox_tuple(after_bbox):
                                    result = "Error: resize_extrusion_face replaced the extrusion, but its bounding box did not change"
                                else:
                                    side = "cap_max" if selected_top else "cap_min"
                                    result = "Resized extrusion face {0} by {1} on {2}".format(_fidx, _dist, side)
"""


def fillet_brep_edge_code(
    object_id: str,
    edge_index: int | None = None,
    edge_indices: List[int] | None = None,
    radius: float = 1.0,
    rail_type: str = "rolling_ball",
) -> str:
    """Fillet one or more Brep edges by index."""
    return inject_params(
        oid=object_id, eidx=edge_index, eidxs=edge_indices or [], radius=radius, rail=rail_type
    ) + """
def _normalise_edge_indices(_single, _many):
    vals = []
    if isinstance(_many, (list, tuple)):
        vals.extend(_many)
    elif _many not in (None, ""):
        vals.append(_many)
    if _single not in (None, ""):
        vals.append(_single)
    out = []
    seen = set()
    for raw in vals:
        try:
            idx = int(raw)
        except Exception:
            continue
        if idx not in seen:
            seen.add(idx)
            out.append(idx)
    return out

guid = System.Guid(_oid)
obj = sc.doc.Objects.Find(guid)
if obj is None or obj.Geometry is None:
    result = "Error: object not found"
else:
    geom = obj.Geometry
    original_name = rs.ObjectName(guid) or "FilletedBrep"
    if isinstance(geom, rg.Brep):
        brep = geom.DuplicateBrep()
    else:
        try:
            brep = rg.Brep.TryConvertBrep(geom)
        except Exception:
            brep = None

    edge_ids = _normalise_edge_indices(_eidx, _eidxs)

    if brep is None:
        result = "Error: object is not a Brep"
    elif not edge_ids:
        result = "Error: no edge indices supplied"
    else:
        invalid = [idx for idx in edge_ids if idx < 0 or idx >= brep.Edges.Count]
        if invalid:
            result = "Error: edge indices out of range: {0}; Brep hat {1} Kanten (gueltig 0-{2})".format(
                invalid, brep.Edges.Count, brep.Edges.Count - 1
            )
        else:
            tol = sc.doc.ModelAbsoluteTolerance
            angle_tol = sc.doc.ModelAngleToleranceRadians
            rail_lookup = {
                "distance_from_edge": rg.RailType.DistanceFromEdge,
                "rolling_ball": rg.RailType.RollingBall,
                "distance_between_rails": rg.RailType.DistanceBetweenRails,
            }
            rail = rail_lookup.get(str(_rail).lower(), rg.RailType.RollingBall)
            radii0 = [_radius for _ in edge_ids]
            radii1 = [_radius for _ in edge_ids]
            try:
                out_breps = rg.Brep.CreateFilletEdges(
                    brep,
                    edge_ids,
                    radii0,
                    radii1,
                    rg.BlendType.Fillet,
                    rail,
                    False,
                    tol,
                    angle_tol
                )
            except Exception as e:
                out_breps = None
                err = str(e)
            if not out_breps:
                result = "Error: fillet failed fuer Kante(n) {0} mit Radius {1} (Brep hat {2} Kanten){3}. Kante grenzt evtl. an geknickte Flaeche (interior kink) -- Geometrie pruefen mit get_brep_component_info".format(
                    edge_ids, _radius, brep.Edges.Count, ": " + err if 'err' in locals() else ""
                )
            else:
                valid = [b for b in out_breps if b and b.IsValid]
                if not valid:
                    result = "Error: fillet produced no valid Breps"
                elif len(valid) == 1:
                    archive_object(guid)
                    sc.doc.Objects.Replace(guid, valid[0])
                    clear_editable_recipe(guid)
                    rs.Redraw()
                    result = "Filleted edge(s) {0} with radius {1}".format(edge_ids, _radius)
                else:
                    archive_object(guid)
                    rs.DeleteObject(guid)
                    created = []
                    for item in valid:
                        new_id = sc.doc.Objects.AddBrep(item)
                        if new_id and new_id != System.Guid.Empty:
                            add_object_metadata(new_id, original_name, "Fillet edges {0}".format(edge_ids))
                            created.append(str(new_id))
                    rs.Redraw()
                    result = "Fillet created {0} Breps: {1}".format(len(created), created)
"""


def round_edges_by_rule_code(
    object_id: str,
    rule: str = "top",
    radius: float = 1.0,
    rail_type: str = "rolling_ball",
) -> str:
    """Fillet multiple Brep edges selected by a semantic rule."""
    return inject_params(
        oid=object_id, rule=rule, radius=radius, rail=rail_type
    ) + """
def _norm_rule(raw):
    text = str(raw or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "alle": "all",
        "all_edges": "all",
        "alle_kanten": "all",
        "alles": "all",
        "alle_ausser_unten": "all_except_bottom",
        "all_except_lower": "all_except_bottom",
        "outer_except_bottom": "all_except_bottom",
        "aussen_ausser_unten": "all_except_bottom",
        "oben": "top",
        "obere": "top",
        "obere_kanten": "top",
        "unten": "bottom",
        "untere": "bottom",
        "untere_kanten": "bottom",
        "vertikal": "vertical",
        "vertikale": "vertical",
        "vertikale_kanten": "vertical",
        "horizontal": "horizontal",
        "horizontale": "horizontal",
        "horizontale_kanten": "horizontal",
        "x": "x_edges",
        "x_kanten": "x_edges",
        "y": "y_edges",
        "y_kanten": "y_edges",
        "z": "z_edges",
        "z_kanten": "z_edges",
        "front": "front",
        "vorne": "front",
        "vordere": "front",
        "back": "back",
        "hinten": "back",
        "hintere": "back",
        "left": "left",
        "links": "left",
        "linke": "left",
        "right": "right",
        "rechts": "right",
        "rechte": "right",
        "oben_vorne": "top_front",
        "top_front_edges": "top_front",
        "oben_hinten": "top_back",
        "top_back_edges": "top_back",
        "oben_links": "top_left",
        "top_left_edges": "top_left",
        "oben_rechts": "top_right",
        "top_right_edges": "top_right",
    }
    return aliases.get(text, text)


def _near(a, b, tol):
    return abs(float(a) - float(b)) <= tol


def _edge_row(edge, idx, bbox, tol):
    start = edge.PointAtStart
    end = edge.PointAtEnd
    center = rg.Point3d(
        0.5 * (start.X + end.X),
        0.5 * (start.Y + end.Y),
        0.5 * (start.Z + end.Z),
    )
    direction = end - start
    if not direction.Unitize():
        direction = rg.Vector3d(0, 0, 0)
    return {
        "index": idx,
        "center": center,
        "direction": direction,
        "top": _near(center.Z, bbox.Max.Z, tol),
        "bottom": _near(center.Z, bbox.Min.Z, tol),
        "front": _near(center.Y, bbox.Min.Y, tol),
        "back": _near(center.Y, bbox.Max.Y, tol),
        "left": _near(center.X, bbox.Min.X, tol),
        "right": _near(center.X, bbox.Max.X, tol),
        "x_edges": abs(direction.X) >= 0.85,
        "y_edges": abs(direction.Y) >= 0.85,
        "z_edges": abs(direction.Z) >= 0.85,
        "vertical": abs(direction.Z) >= 0.85,
        "horizontal": abs(direction.Z) <= 0.15,
    }


def _matches(row, rule_name):
    if rule_name == "all":
        return True
    if rule_name == "all_except_bottom":
        return not row["bottom"]
    if rule_name in row:
        return bool(row[rule_name])
    if rule_name == "top_front":
        return row["top"] and row["front"]
    if rule_name == "top_back":
        return row["top"] and row["back"]
    if rule_name == "top_left":
        return row["top"] and row["left"]
    if rule_name == "top_right":
        return row["top"] and row["right"]
    return False


guid = System.Guid(_oid)
obj = sc.doc.Objects.Find(guid)
if obj is None or obj.Geometry is None:
    result = "Error: object not found"
else:
    geom = obj.Geometry
    original_name = rs.ObjectName(guid) or "RoundedBrep"
    if isinstance(geom, rg.Brep):
        brep = geom.DuplicateBrep()
    else:
        try:
            brep = rg.Brep.TryConvertBrep(geom)
        except Exception:
            brep = None

    rule_name = _norm_rule(_rule)
    if brep is None:
        result = "Error: object is not a Brep"
    elif _radius <= 0:
        result = "Error: radius must be greater than zero"
    elif brep.Edges.Count == 0:
        result = "Error: object has no Brep edges"
    else:
        bbox = brep.GetBoundingBox(True)
        diag = bbox.Diagonal.Length
        tol = max(sc.doc.ModelAbsoluteTolerance * 10.0, diag * 1e-6, 1e-5)
        rows = [_edge_row(brep.Edges[i], i, bbox, tol) for i in range(brep.Edges.Count)]
        edge_ids = [row["index"] for row in rows if _matches(row, rule_name)]
        if not edge_ids:
            result = "Error: no edges matched rule '{0}'".format(_rule)
        else:
            tol_doc = sc.doc.ModelAbsoluteTolerance
            angle_tol = sc.doc.ModelAngleToleranceRadians
            rail_lookup = {
                "distance_from_edge": rg.RailType.DistanceFromEdge,
                "rolling_ball": rg.RailType.RollingBall,
                "distance_between_rails": rg.RailType.DistanceBetweenRails,
            }
            rail = rail_lookup.get(str(_rail).lower(), rg.RailType.RollingBall)
            radii0 = [_radius for _ in edge_ids]
            radii1 = [_radius for _ in edge_ids]
            try:
                out_breps = rg.Brep.CreateFilletEdges(
                    brep,
                    edge_ids,
                    radii0,
                    radii1,
                    rg.BlendType.Fillet,
                    rail,
                    False,
                    tol_doc,
                    angle_tol
                )
            except Exception as e:
                out_breps = None
                err = str(e)
            if not out_breps:
                result = "Error: rule fillet failed for edges {0}{1}".format(
                    edge_ids,
                    ": " + err if 'err' in locals() else ""
                )
            else:
                valid = [b for b in out_breps if b and b.IsValid]
                if not valid:
                    result = "Error: rule fillet produced no valid Breps"
                elif len(valid) == 1:
                    archive_object(guid)
                    sc.doc.Objects.Replace(guid, valid[0])
                    # Topology changed — strip primitive-recipe tags so the
                    # structure-slider can't rebuild the object as a fresh
                    # primitive and silently undo these fillets.
                    clear_editable_recipe(guid)
                    rs.Redraw()
                    result = "Rounded {0} edge(s) by rule '{1}' with radius {2}: {3}".format(
                        len(edge_ids), rule_name, _radius, edge_ids
                    )
                else:
                    archive_object(guid)
                    rs.DeleteObject(guid)
                    created = []
                    for item in valid:
                        new_id = sc.doc.Objects.AddBrep(item)
                        if new_id and new_id != System.Guid.Empty:
                            add_object_metadata(new_id, original_name, "Round edges by rule {0}".format(rule_name))
                            created.append(str(new_id))
                    rs.Redraw()
                    result = "Rule fillet created {0} Breps from edges {1}: {2}".format(
                        len(created), edge_ids, created
                    )
"""


def chamfer_brep_edge_code(
    object_id: str,
    edge_index: int | None = None,
    edge_indices: List[int] | None = None,
    distance: float = 1.0,
    rail_type: str = "distance_from_edge",
) -> str:
    """Chamfer one or more Brep edges by index."""
    return inject_params(
        oid=object_id, eidx=edge_index, eidxs=edge_indices or [], dist=distance, rail=rail_type
    ) + """
def _normalise_edge_indices(_single, _many):
    vals = []
    if isinstance(_many, (list, tuple)):
        vals.extend(_many)
    elif _many not in (None, ""):
        vals.append(_many)
    if _single not in (None, ""):
        vals.append(_single)
    out = []
    seen = set()
    for raw in vals:
        try:
            idx = int(raw)
        except Exception:
            continue
        if idx not in seen:
            seen.add(idx)
            out.append(idx)
    return out

guid = System.Guid(_oid)
obj = sc.doc.Objects.Find(guid)
if obj is None or obj.Geometry is None:
    result = "Error: object not found"
else:
    geom = obj.Geometry
    original_name = rs.ObjectName(guid) or "ChamferedBrep"
    if isinstance(geom, rg.Brep):
        brep = geom.DuplicateBrep()
    else:
        try:
            brep = rg.Brep.TryConvertBrep(geom)
        except Exception:
            brep = None

    edge_ids = _normalise_edge_indices(_eidx, _eidxs)

    if brep is None:
        result = "Error: object is not a Brep"
    elif not edge_ids:
        result = "Error: no edge indices supplied"
    else:
        invalid = [idx for idx in edge_ids if idx < 0 or idx >= brep.Edges.Count]
        if invalid:
            result = "Error: edge indices out of range: {0}; Brep hat {1} Kanten (gueltig 0-{2})".format(
                invalid, brep.Edges.Count, brep.Edges.Count - 1
            )
        else:
            tol = sc.doc.ModelAbsoluteTolerance
            angle_tol = sc.doc.ModelAngleToleranceRadians
            rail_lookup = {
                "distance_from_edge": rg.RailType.DistanceFromEdge,
                "rolling_ball": rg.RailType.RollingBall,
                "distance_between_rails": rg.RailType.DistanceBetweenRails,
            }
            rail = rail_lookup.get(str(_rail).lower(), rg.RailType.DistanceFromEdge)
            dists0 = [_dist for _ in edge_ids]
            dists1 = [_dist for _ in edge_ids]
            try:
                out_breps = rg.Brep.CreateFilletEdges(
                    brep,
                    edge_ids,
                    dists0,
                    dists1,
                    rg.BlendType.Chamfer,
                    rail,
                    False,
                    tol,
                    angle_tol
                )
            except Exception as e:
                out_breps = None
                err = str(e)
            if not out_breps:
                result = "Error: chamfer failed fuer Kante(n) {0} mit Distanz {1} (Brep hat {2} Kanten){3}. Kante grenzt evtl. an geknickte Flaeche (interior kink) -- Geometrie pruefen mit get_brep_component_info".format(
                    edge_ids, _dist, brep.Edges.Count, ": " + err if 'err' in locals() else ""
                )
            else:
                valid = [b for b in out_breps if b and b.IsValid]
                if not valid:
                    result = "Error: chamfer produced no valid Breps"
                elif len(valid) == 1:
                    archive_object(guid)
                    sc.doc.Objects.Replace(guid, valid[0])
                    clear_editable_recipe(guid)
                    rs.Redraw()
                    result = "Chamfered edge(s) {0} by {1}".format(edge_ids, _dist)
                else:
                    archive_object(guid)
                    rs.DeleteObject(guid)
                    created = []
                    for item in valid:
                        new_id = sc.doc.Objects.AddBrep(item)
                        if new_id and new_id != System.Guid.Empty:
                            add_object_metadata(new_id, original_name, "Chamfer edges {0}".format(edge_ids))
                            created.append(str(new_id))
                    rs.Redraw()
                    result = "Chamfer created {0} Breps: {1}".format(len(created), created)
"""


def move_brep_face_along_normal_code(
    object_id: str,
    face_index: int,
    distance: float = 1.0,
) -> str:
    """Move a planar Brep face along its normal while recomputing adjacent planar faces."""
    return inject_params(oid=object_id, fidx=face_index, dist=distance) + """
def _bbox_tuple(_bbox):
    return (
        round(_bbox.Min.X, 9), round(_bbox.Min.Y, 9), round(_bbox.Min.Z, 9),
        round(_bbox.Max.X, 9), round(_bbox.Max.Y, 9), round(_bbox.Max.Z, 9),
    )

def _try_pushpull_replace(_guid, _geom, _face_index, _xform, _before_bbox):
    if isinstance(_geom, rg.Brep):
        _brep = _geom.DuplicateBrep()
    else:
        try:
            _brep = rg.Brep.TryConvertBrep(_geom)
        except Exception:
            _brep = None
    if _brep is None:
        return False, "fallback could not convert object to Brep"
    try:
        _ok = _brep.PushPullExtend(_face_index, _xform, sc.doc.ModelAbsoluteTolerance)
    except Exception as _e:
        return False, "fallback PushPullExtend failed: {0}".format(_e)
    if not _ok:
        return False, "fallback PushPullExtend returned false"
    _replace_ok = sc.doc.Objects.Replace(_guid, _brep)
    if not _replace_ok:
        return False, "fallback could not replace original object"
    sc.doc.Views.Redraw()
    _obj_after = sc.doc.Objects.Find(_guid)
    _after_geom = _obj_after.Geometry if _obj_after and _obj_after.Geometry else None
    _after_bbox = _after_geom.GetBoundingBox(True) if _after_geom else None
    if _after_bbox is None:
        return False, "fallback updated object could not be read back"
    if _bbox_tuple(_before_bbox) == _bbox_tuple(_after_bbox):
        return False, "fallback bounding box did not change"
    return True, None

guid = System.Guid(_oid)
obj = sc.doc.Objects.Find(guid)
if obj is None or obj.Geometry is None:
    result = "Error: object not found"
else:
    geom = obj.Geometry
    if isinstance(geom, rg.Brep):
        brep = geom.DuplicateBrep()
    else:
        try:
            brep = rg.Brep.TryConvertBrep(geom)
        except Exception:
            brep = None

    if brep is None:
        result = "Error: object is not a Brep"
    elif _fidx < 0 or _fidx >= brep.Faces.Count:
        result = "Error: face index {0} out of range".format(_fidx)
    else:
        face = brep.Faces[_fidx]
        if not face.IsPlanar(sc.doc.ModelAbsoluteTolerance):
            result = "Error: face {0} is not planar".format(_fidx)
        else:
            du = face.Domain(0)
            dv = face.Domain(1)
            u = 0.5 * (du.T0 + du.T1)
            v = 0.5 * (dv.T0 + dv.T1)
            from_pt = face.PointAt(u, v)
            normal = face.NormalAt(u, v)
            if face.OrientationIsReversed:
                normal.Reverse()
            if not normal.Unitize():
                result = "Error: face {0} has no valid normal".format(_fidx)
            else:
                vec = rg.Vector3d(normal.X * _dist, normal.Y * _dist, normal.Z * _dist)
                to_pt = rg.Point3d(
                    from_pt.X + vec.X,
                    from_pt.Y + vec.Y,
                    from_pt.Z + vec.Z
                )
                xform = rg.Transform.Translation(vec)
                before_bbox = obj.Geometry.GetBoundingBox(True)
                ci = rg.ComponentIndex(rg.ComponentIndexType.BrepFace, int(_fidx))
                objref = Rhino.DocObjects.ObjRef(sc.doc, guid, ci)
                rs.UnselectAllObjects()
                try:
                    selected = sc.doc.Objects.Select(objref, True, True, True, True, True, True)
                except TypeError:
                    selected = sc.doc.Objects.Select(objref, True)
                if not selected:
                    result = "Error: could not select face {0}".format(_fidx)
                else:
                    archive_object(guid)
                    cmd = "_-MoveFace _DirectionConstraint=_Normal {0},{1},{2} {3},{4},{5} _Enter".format(
                        from_pt.X, from_pt.Y, from_pt.Z,
                        to_pt.X, to_pt.Y, to_pt.Z
                    )
                    ok = False
                    try:
                        ok = rs.Command(cmd, False)
                    except Exception as e:
                        err = str(e)
                    finally:
                        rs.UnselectAllObjects()
                    if not ok:
                        fallback_ok, fallback_err = _try_pushpull_replace(guid, geom, _fidx, xform, before_bbox)
                        if fallback_ok:
                            result = "Moved face {0} along normal by {1}".format(_fidx, _dist)
                        else:
                            msg = "MoveFace command failed{0}".format(": " + err if 'err' in locals() else "")
                            result = "Error: {0}; {1}".format(msg, fallback_err)
                    else:
                        sc.doc.Views.Redraw()
                        obj_after = sc.doc.Objects.Find(guid)
                        after_geom = obj_after.Geometry if obj_after and obj_after.Geometry else None
                        after_bbox = after_geom.GetBoundingBox(True) if after_geom else None
                        if after_bbox is None:
                            fallback_ok, fallback_err = _try_pushpull_replace(guid, geom, _fidx, xform, before_bbox)
                            if fallback_ok:
                                result = "Moved face {0} along normal by {1}".format(_fidx, _dist)
                            else:
                                result = "Error: face move completed, but updated object could not be read back; {0}".format(fallback_err)
                        elif _bbox_tuple(before_bbox) == _bbox_tuple(after_bbox):
                            fallback_ok, fallback_err = _try_pushpull_replace(guid, geom, _fidx, xform, before_bbox)
                            if fallback_ok:
                                result = "Moved face {0} along normal by {1}".format(_fidx, _dist)
                            else:
                                result = "Error: MoveFace reported success, but the object bounding box did not change; {0}".format(fallback_err)
                        else:
                            result = "Moved face {0} along normal by {1}".format(_fidx, _dist)
"""


def move_brep_face_in_direction_code(
    object_id: str,
    face_index: int,
    dx: float = 0.0,
    dy: float = 0.0,
    dz: float = 0.0,
) -> str:
    """Move a planar Brep face by a translation vector while recomputing adjacent planar faces."""
    return inject_params(oid=object_id, fidx=face_index, dx=dx, dy=dy, dz=dz) + """
def _bbox_tuple(_bbox):
    return (
        round(_bbox.Min.X, 9), round(_bbox.Min.Y, 9), round(_bbox.Min.Z, 9),
        round(_bbox.Max.X, 9), round(_bbox.Max.Y, 9), round(_bbox.Max.Z, 9),
    )

def _try_pushpull_replace(_guid, _geom, _face_index, _xform, _before_bbox):
    if isinstance(_geom, rg.Brep):
        _brep = _geom.DuplicateBrep()
    else:
        try:
            _brep = rg.Brep.TryConvertBrep(_geom)
        except Exception:
            _brep = None
    if _brep is None:
        return False, "fallback could not convert object to Brep"
    try:
        _ok = _brep.PushPullExtend(_face_index, _xform, sc.doc.ModelAbsoluteTolerance)
    except Exception as _e:
        return False, "fallback PushPullExtend failed: {0}".format(_e)
    if not _ok:
        return False, "fallback PushPullExtend returned false"
    _replace_ok = sc.doc.Objects.Replace(_guid, _brep)
    if not _replace_ok:
        return False, "fallback could not replace original object"
    sc.doc.Views.Redraw()
    _obj_after = sc.doc.Objects.Find(_guid)
    _after_geom = _obj_after.Geometry if _obj_after and _obj_after.Geometry else None
    _after_bbox = _after_geom.GetBoundingBox(True) if _after_geom else None
    if _after_bbox is None:
        return False, "fallback updated object could not be read back"
    if _bbox_tuple(_before_bbox) == _bbox_tuple(_after_bbox):
        return False, "fallback bounding box did not change"
    return True, None

guid = System.Guid(_oid)
obj = sc.doc.Objects.Find(guid)
if obj is None or obj.Geometry is None:
    result = "Error: object not found"
else:
    geom = obj.Geometry
    if isinstance(geom, rg.Brep):
        brep = geom.DuplicateBrep()
    else:
        try:
            brep = rg.Brep.TryConvertBrep(geom)
        except Exception:
            brep = None

    if brep is None:
        result = "Error: object is not a Brep"
    elif _fidx < 0 or _fidx >= brep.Faces.Count:
        result = "Error: face index {0} out of range".format(_fidx)
    else:
        face = brep.Faces[_fidx]
        if not face.IsPlanar(sc.doc.ModelAbsoluteTolerance):
            result = "Error: face {0} is not planar".format(_fidx)
        else:
            du = face.Domain(0)
            dv = face.Domain(1)
            u = 0.5 * (du.T0 + du.T1)
            v = 0.5 * (dv.T0 + dv.T1)
            from_pt = face.PointAt(u, v)
            xform = rg.Transform.Translation(rg.Vector3d(_dx, _dy, _dz))
            to_pt = rg.Point3d(from_pt.X + _dx, from_pt.Y + _dy, from_pt.Z + _dz)
            before_bbox = obj.Geometry.GetBoundingBox(True)
            ci = rg.ComponentIndex(rg.ComponentIndexType.BrepFace, int(_fidx))
            objref = Rhino.DocObjects.ObjRef(sc.doc, guid, ci)
            rs.UnselectAllObjects()
            try:
                selected = sc.doc.Objects.Select(objref, True, True, True, True, True, True)
            except TypeError:
                selected = sc.doc.Objects.Select(objref, True)
            if not selected:
                result = "Error: could not select face {0}".format(_fidx)
            else:
                archive_object(guid)
                cmd = "_-MoveFace _DirectionConstraint=_None {0},{1},{2} {3},{4},{5} _Enter".format(
                    from_pt.X, from_pt.Y, from_pt.Z,
                    to_pt.X, to_pt.Y, to_pt.Z
                )
                ok = False
                try:
                    ok = rs.Command(cmd, False)
                except Exception as e:
                    err = str(e)
                finally:
                    rs.UnselectAllObjects()
                if not ok:
                    fallback_ok, fallback_err = _try_pushpull_replace(guid, geom, _fidx, xform, before_bbox)
                    if fallback_ok:
                        result = "Moved face {0} by vector ({1}, {2}, {3})".format(_fidx, _dx, _dy, _dz)
                    else:
                        msg = "MoveFace command failed{0}".format(": " + err if 'err' in locals() else "")
                        result = "Error: {0}; {1}".format(msg, fallback_err)
                else:
                    sc.doc.Views.Redraw()
                    obj_after = sc.doc.Objects.Find(guid)
                    after_geom = obj_after.Geometry if obj_after and obj_after.Geometry else None
                    after_bbox = after_geom.GetBoundingBox(True) if after_geom else None
                    if after_bbox is None:
                        fallback_ok, fallback_err = _try_pushpull_replace(guid, geom, _fidx, xform, before_bbox)
                        if fallback_ok:
                            result = "Moved face {0} by vector ({1}, {2}, {3})".format(_fidx, _dx, _dy, _dz)
                        else:
                            result = "Error: face move completed, but updated object could not be read back; {0}".format(fallback_err)
                    elif _bbox_tuple(before_bbox) == _bbox_tuple(after_bbox):
                        fallback_ok, fallback_err = _try_pushpull_replace(guid, geom, _fidx, xform, before_bbox)
                        if fallback_ok:
                            result = "Moved face {0} by vector ({1}, {2}, {3})".format(_fidx, _dx, _dy, _dz)
                        else:
                            result = "Error: MoveFace reported success, but the object bounding box did not change; {0}".format(fallback_err)
                    else:
                        result = "Moved face {0} by vector ({1}, {2}, {3})".format(_fidx, _dx, _dy, _dz)
"""
