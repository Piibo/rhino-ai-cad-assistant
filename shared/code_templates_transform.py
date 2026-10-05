"""Code templates: transform operations. See code_templates.py."""
from __future__ import annotations

from typing import List

from ._codegen_base import inject_params

__all__ = [
    "move_objects_code",
    "copy_objects_code",
    "rotate_objects_code",
    "scale_objects_code",
    "set_bbox_dimension_code",
    "mirror_objects_code",
    "array_linear_code",
    "array_polar_code",
    "delete_objects_code",
    "set_layer_code",
    "rename_object_code",
]


def move_objects_code(
    object_ids: List[str],
    dx: float = 0,
    dy: float = 0,
    dz: float = 0,
) -> str:
    """Translate one or more objects by (dx, dy, dz)."""
    return inject_params(ids=object_ids, dx=dx, dy=dy, dz=dz) + """
guids = [System.Guid(i) for i in _ids]
for g in guids:
    archive_object(g)
    rs.MoveObject(g, (_dx, _dy, _dz))
rs.Redraw()
result = "Moved {0} object(s) by ({1},{2},{3})".format(len(guids), _dx, _dy, _dz)
"""


def copy_objects_code(
    object_ids: List[str],
    dx: float = 0,
    dy: float = 0,
    dz: float = 0,
) -> str:
    """Copy one or more objects, offset by (dx, dy, dz)."""
    # Defensively coerce dx/dy/dz: the model may pass a string or None even
    # though the schema default is 0.0, because inject_params injects 1:1.
    try:
        dx = float(dx)
    except Exception:
        dx = 0.0
    try:
        dy = float(dy)
    except Exception:
        dy = 0.0
    try:
        dz = float(dz)
    except Exception:
        dz = 0.0
    return inject_params(ids=object_ids, dx=dx, dy=dy, dz=dz) + """
copies = []
invalid = []
missing = []

for raw_id in (_ids or []):
	try:
		g = System.Guid(str(raw_id))
	except Exception:
		invalid.append(str(raw_id))
		continue
	if sc.doc.Objects.Find(g) is None:
		missing.append(str(raw_id))
		continue
	c = rs.CopyObject(g, (_dx, _dy, _dz))
	if c:
		_record_created_object(c)
		copies.append(str(c))

rs.Redraw()
if not copies:
	result = "Fehler: Keine Kopie erstellt. Ungueltige IDs: {0}. Nicht gefunden: {1}.".format(invalid, missing)
elif invalid or missing:
	result = "Copied {0} object(s): {1}. Uebersprungen — ungueltig: {2}, nicht gefunden: {3}.".format(
		len(copies), copies, invalid, missing)
else:
	result = "Copied {0} object(s): {1}".format(len(copies), copies)
"""


def rotate_objects_code(
    object_ids: List[str],
    angle_degrees: float = 90,
    center: List[float] = [0, 0, 0],
    axis: List[float] = [0, 0, 1],
) -> str:
    """Rotate objects around a point and axis."""
    return inject_params(
        ids=object_ids, angle=angle_degrees, center=center, axis=axis
    ) + """
guids = [System.Guid(i) for i in _ids]
for g in guids:
    archive_object(g)
    rs.RotateObject(g, _center, _angle, _axis)
rs.Redraw()
result = "Rotated {0} object(s) by {1} degrees".format(len(guids), _angle)
"""


def scale_objects_code(
    object_ids: List[str],
    scale_factor: float = 2.0,
    origin_x: float = 0,
    origin_y: float = 0,
    origin_z: float = 0,
) -> str:
    """Uniform scale around a given origin point."""
    return inject_params(
        ids=object_ids, sf=scale_factor, ox=origin_x, oy=origin_y, oz=origin_z
    ) + """
guids = [System.Guid(i) for i in _ids]
origin = (_ox, _oy, _oz)
for g in guids:
    archive_object(g)
    rs.ScaleObject(g, origin, (_sf, _sf, _sf))
rs.Redraw()
result = "Scaled {0} object(s) by factor {1}".format(len(guids), _sf)
"""


def set_bbox_dimension_code(
    object_id: str,
    axis: str,
    target_size: float,
    anchor: str = "center",
) -> str:
    """Set a single world-bounding-box dimension by non-uniform scaling."""
    return inject_params(
        oid=object_id, axis=axis, target=target_size, anchor=anchor
    ) + """
def _axis_key(raw):
    text = str(raw or "").strip().lower()
    aliases = {
        "x": "x",
        "width": "x",
        "breite": "x",
        "w": "x",
        "y": "y",
        "depth": "y",
        "tiefe": "y",
        "d": "y",
        "z": "z",
        "height": "z",
        "hoehe": "z",
        "hohe": "z",
        "h": "z",
    }
    return aliases.get(text)


def _axis_label(axis_key):
    return {"x": "Breite", "y": "Tiefe", "z": "Hoehe"}.get(axis_key, axis_key)


def _bbox_payload(bbox):
    return [
        [float(bbox.Min.X), float(bbox.Min.Y), float(bbox.Min.Z)],
        [float(bbox.Max.X), float(bbox.Min.Y), float(bbox.Min.Z)],
        [float(bbox.Max.X), float(bbox.Max.Y), float(bbox.Min.Z)],
        [float(bbox.Min.X), float(bbox.Max.Y), float(bbox.Min.Z)],
        [float(bbox.Min.X), float(bbox.Min.Y), float(bbox.Max.Z)],
        [float(bbox.Max.X), float(bbox.Min.Y), float(bbox.Max.Z)],
        [float(bbox.Max.X), float(bbox.Max.Y), float(bbox.Max.Z)],
        [float(bbox.Min.X), float(bbox.Max.Y), float(bbox.Max.Z)],
    ]


def _update_box_recipe(guid, bbox):
    width = float(bbox.Max.X - bbox.Min.X)
    depth = float(bbox.Max.Y - bbox.Min.Y)
    height = float(bbox.Max.Z - bbox.Min.Z)
    rs.SetUserText(guid, "object_class", "primitive_box")
    rs.SetUserText(guid, "editable_recipe_type", "box")
    rs.SetUserText(guid, "editable_operations", "resize_box_face,fillet_brep_edge,chamfer_brep_edge")
    rs.SetUserText(
        guid,
        "editable_recipe",
        json.dumps(
            {
                "x": float(bbox.Min.X),
                "y": float(bbox.Min.Y),
                "z": float(bbox.Min.Z),
                "width": width,
                "depth": depth,
                "height": height,
            }
        ),
    )
    rs.SetUserText(guid, "bbox", json.dumps(_bbox_payload(bbox)))
    rs.SetUserText(guid, "description", "Box {0}x{1}x{2}".format(width, depth, height))


axis_key = _axis_key(_axis)
anchor_key = str(_anchor or "center").strip().lower()
if anchor_key not in ("center", "min", "max"):
    anchor_key = "center"

try:
    target = float(_target)
except Exception:
    target = 0.0

try:
    guid = System.Guid(str(_oid))
except Exception:
    guid = None

tol = max(sc.doc.ModelAbsoluteTolerance, 1e-6)
if guid is None:
    result = "Error: invalid object_id"
elif axis_key is None:
    result = "Error: axis must be x/width, y/depth or z/height"
elif target <= tol:
    result = "Error: target_size must be greater than zero"
else:
    obj = sc.doc.Objects.Find(guid)
    if obj is None or obj.Geometry is None:
        result = "Error: object not found"
    else:
        geom = obj.Geometry
        bbox = geom.GetBoundingBox(True)
        current = {
            "x": float(bbox.Max.X - bbox.Min.X),
            "y": float(bbox.Max.Y - bbox.Min.Y),
            "z": float(bbox.Max.Z - bbox.Min.Z),
        }[axis_key]
        if current <= tol:
            result = "Error: current {0} is too small to scale".format(_axis_label(axis_key))
        else:
            factor = target / current
            sx = factor if axis_key == "x" else 1.0
            sy = factor if axis_key == "y" else 1.0
            sz = factor if axis_key == "z" else 1.0
            center = bbox.Center
            origin_x = float(center.X)
            origin_y = float(center.Y)
            origin_z = float(center.Z)
            if axis_key == "x":
                origin_x = float(bbox.Min.X if anchor_key == "min" else bbox.Max.X if anchor_key == "max" else center.X)
            elif axis_key == "y":
                origin_y = float(bbox.Min.Y if anchor_key == "min" else bbox.Max.Y if anchor_key == "max" else center.Y)
            else:
                origin_z = float(bbox.Min.Z if anchor_key == "min" else bbox.Max.Z if anchor_key == "max" else center.Z)

            archive_object(guid)
            scaled_id = rs.ScaleObject(guid, (origin_x, origin_y, origin_z), (sx, sy, sz), False)
            if not scaled_id:
                result = "Error: could not scale object"
            else:
                obj_after = sc.doc.Objects.Find(guid)
                after_geom = obj_after.Geometry if obj_after and obj_after.Geometry else None
                after_bbox = after_geom.GetBoundingBox(True) if after_geom else None
                if after_bbox is None:
                    result = "Error: object could not be read after scaling"
                else:
                    object_class = rs.GetUserText(guid, "object_class") or ""
                    if object_class == "primitive_box":
                        _update_box_recipe(guid, after_bbox)
                    rs.Redraw()
                    new_size = {
                        "x": float(after_bbox.Max.X - after_bbox.Min.X),
                        "y": float(after_bbox.Max.Y - after_bbox.Min.Y),
                        "z": float(after_bbox.Max.Z - after_bbox.Min.Z),
                    }[axis_key]
                    result = (
                        "Set {0} of {1} to {2} mm (was {3} mm, anchor={4})"
                    ).format(_axis_label(axis_key), str(guid), round(new_size, 6), round(current, 6), anchor_key)
"""


def mirror_objects_code(
    object_ids: List[str],
    mirror_plane_origin: List[float] = [0, 0, 0],
    mirror_plane_normal: List[float] = [1, 0, 0],
    copy: bool = True,
) -> str:
    """Mirror objects across a plane-like axis definition."""
    return inject_params(
        ids=object_ids,
        org=mirror_plane_origin,
        normal=mirror_plane_normal,
        copy=copy,
    ) + """
guids = [System.Guid(i) for i in _ids]
end = [_org[0]+_normal[0]*100, _org[1]+_normal[1]*100, _org[2]+_normal[2]*100]
mirrored = []
for g in guids:
    if not _copy:
        archive_object(g)
    m = rs.MirrorObject(g, _org, end, _copy)
    if m:
        if _copy:
            _record_created_object(m)
        mirrored.append(str(m))
rs.Redraw()
result = "Mirrored {0} object(s): {1}".format(len(mirrored), mirrored)
"""


def array_linear_code(
    object_id: str,
    count: int = 5,
    dx: float = 10,
    dy: float = 0,
    dz: float = 0,
) -> str:
    """Create a linear array of copies of an object."""
    return inject_params(oid=object_id, n=count, dx=dx, dy=dy, dz=dz) + """
guid = System.Guid(_oid)
copies = []
for i in range(1, _n + 1):
    c = rs.CopyObject(guid, (_dx * i, _dy * i, _dz * i))
    if c:
        add_object_metadata(c, "Array_{0}".format(i), "Linear array copy")
        copies.append(str(c))
rs.Redraw()
result = "Created {0} array copies: {1}".format(len(copies), copies)
"""


def array_polar_code(
    object_id: str,
    count: int = 6,
    center: List[float] = [0, 0, 0],
    axis: List[float] = [0, 0, 1],
    total_angle: float = 360,
) -> str:
    """Create a polar (circular) array of copies around a center."""
    return inject_params(
        oid=object_id, n=count, center=center, axis=axis, angle=total_angle
    ) + """
guid = System.Guid(_oid)
step = _angle / (_n + 1)
copies = []
for i in range(1, _n + 1):
    c = rs.CopyObject(guid)
    if c:
        rs.RotateObject(c, _center, step * i, _axis)
        add_object_metadata(c, "PolarArray_{0}".format(i), "Polar array copy")
        copies.append(str(c))
rs.Redraw()
result = "Created {0} polar array copies: {1}".format(len(copies), copies)
"""


def delete_objects_code(object_ids: List[str], archive_first: bool = True) -> str:
    """Delete one or more objects, optionally archiving first."""
    return inject_params(ids=object_ids, archive=archive_first) + """
guids = [System.Guid(i) for i in _ids]
existing = [g for g in guids if sc.doc.Objects.Find(g) is not None]
if _archive:
    for g in existing:
        try:
            archive_object(g)
        except Exception:
            pass
deleted = rs.DeleteObjects(existing)
rs.Redraw()
if isinstance(deleted, list):
    deleted_count = len(deleted)
elif isinstance(deleted, int):
    deleted_count = deleted
elif deleted:
    deleted_count = len(existing)
else:
    deleted_count = 0
result = "Deleted {0} object(s){1}".format(
    deleted_count,
    " (archived first)" if _archive else "",
)
"""


def set_layer_code(
    object_ids: List[str],
    layer: str = "Active",
    create_if_missing: bool = True,
) -> str:
    """Move objects to a layer. Optionally create the layer first."""
    return inject_params(
        ids=object_ids, layer=layer, create=create_if_missing
    ) + """
if _create and not rs.IsLayer(_layer):
    rs.AddLayer(_layer)
guids = [System.Guid(i) for i in _ids]
moved = 0
for g in guids:
    archive_object(g)
    if rs.ObjectLayer(g, _layer):
        moved += 1
rs.Redraw()
result = "Set layer '{0}' on {1} object(s)".format(_layer, moved)
"""


def rename_object_code(
    object_id: str,
    name: str,
) -> str:
    """Rename a Rhino object."""
    return inject_params(oid=object_id, name=name) + """
guid = System.Guid(_oid)
if sc.doc.Objects.Find(guid) is None:
    result = "Error: object not found"
else:
    archive_object(guid)
    rs.ObjectName(guid, _name)
    try:
        rs.SetUserText(guid, "name", _name)
    except Exception:
        pass
    current_name = rs.ObjectName(guid)
    rs.Redraw()
    if current_name == _name:
        result = "Renamed object to '{0}': {1}".format(_name, _oid)
    else:
        result = "Rename failed"
"""
