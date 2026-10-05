"""Code templates: geometry operations. See code_templates.py."""
from __future__ import annotations

from typing import List

from ._codegen_base import inject_params

__all__ = [
    "create_box_code",
    "create_point_code",
    "create_line_code",
    "create_polyline_code",
    "create_circle_code",
    "create_rectangle_code",
    "create_sphere_code",
    "create_cylinder_code",
    "create_cone_code",
    "create_pipe_code",
    "create_arc_code",
    "create_torus_code",
]


def create_box_code(
    x: float = 0,
    y: float = 0,
    z: float = 0,
    width: float = 100,
    depth: float = 100,
    height: float = 100,
    name: str = "Box",
) -> str:
    """Axis-aligned box. Origin = bottom-left corner at (x, y, z)."""
    return inject_params(
        x=x, y=y, z=z, w=width, d=depth, h=height, name=name
    ) + """
corners = [
    (_x,_y,_z), (_x+_w,_y,_z), (_x+_w,_y+_d,_z), (_x,_y+_d,_z),
    (_x,_y,_z+_h), (_x+_w,_y,_z+_h), (_x+_w,_y+_d,_z+_h), (_x,_y+_d,_z+_h)
]
box_id = rs.AddBox(corners)
add_object_metadata(box_id, _name, "Box {0}x{1}x{2}".format(_w, _d, _h))
rs.SetUserText(box_id, "object_class", "primitive_box")
rs.SetUserText(box_id, "editable_recipe_type", "box")
rs.SetUserText(box_id, "editable_operations", "resize_box_face,fillet_brep_edge,chamfer_brep_edge")
rs.SetUserText(
    box_id,
    "editable_recipe",
    json.dumps(
        {
            "x": float(_x),
            "y": float(_y),
            "z": float(_z),
            "width": float(_w),
            "depth": float(_d),
            "height": float(_h),
        }
    ),
)
rs.Redraw()
result = "Created box: " + str(box_id)
"""


def create_point_code(
    x: float = 0,
    y: float = 0,
    z: float = 0,
    name: str = "Point",
) -> str:
    """Create a point object at (x, y, z)."""
    return inject_params(x=x, y=y, z=z, name=name) + """
pt_id = rs.AddPoint(_x, _y, _z)
add_object_metadata(pt_id, _name, "Point at ({0},{1},{2})".format(_x, _y, _z))
rs.Redraw()
result = "Created point: " + str(pt_id)
"""


def create_line_code(
    x1: float = 0,
    y1: float = 0,
    z1: float = 0,
    x2: float = 10,
    y2: float = 0,
    z2: float = 0,
    name: str = "Line",
) -> str:
    """Create a line from (x1, y1, z1) to (x2, y2, z2)."""
    return inject_params(
        x1=x1, y1=y1, z1=z1, x2=x2, y2=y2, z2=z2, name=name
    ) + """
line_id = rs.AddLine((_x1,_y1,_z1), (_x2,_y2,_z2))
add_object_metadata(line_id, _name, "Line")
rs.Redraw()
result = "Created line: " + str(line_id)
"""


def create_polyline_code(
    points: List[List[float]],
    closed: bool = False,
    name: str = "Polyline",
) -> str:
    """Create a polyline from a list of [x, y, z] points."""
    return inject_params(pts=points, closed=closed, name=name) + """
if _closed and _pts[0] != _pts[-1]:
    _pts.append(_pts[0])
pl_id = rs.AddPolyline([(_p[0],_p[1],_p[2]) for _p in _pts])
add_object_metadata(pl_id, _name, "Polyline with {0} points".format(len(_pts)))
rs.Redraw()
result = "Created polyline: " + str(pl_id)
"""


def create_circle_code(
    center_x: float = 0,
    center_y: float = 0,
    center_z: float = 0,
    radius: float = 5,
    name: str = "Circle",
) -> str:
    """Create a circle on the XY plane at the given center."""
    return inject_params(
        cx=center_x, cy=center_y, cz=center_z, r=radius, name=name
    ) + """
plane = rs.MovePlane(rs.WorldXYPlane(), (_cx, _cy, _cz))
cir_id = rs.AddCircle(plane, _r)
add_object_metadata(cir_id, _name, "Circle r={0}".format(_r))
rs.Redraw()
result = "Created circle: " + str(cir_id)
"""


def create_rectangle_code(
    x: float = 0,
    y: float = 0,
    z: float = 0,
    width: float = 10,
    height: float = 10,
    name: str = "Rectangle",
) -> str:
    """Create a rectangle on the XY plane at (x, y, z)."""
    return inject_params(x=x, y=y, z=z, w=width, h=height, name=name) + """
plane = rs.MovePlane(rs.WorldXYPlane(), (_x, _y, _z))
rect_id = rs.AddRectangle(plane, _w, _h)
add_object_metadata(rect_id, _name, "Rectangle {0}x{1}".format(_w, _h))
rs.Redraw()
result = "Created rectangle: " + str(rect_id)
"""


def create_sphere_code(
    center_x: float = 0,
    center_y: float = 0,
    center_z: float = 0,
    radius: float = 5,
    name: str = "Sphere",
) -> str:
    """Create a sphere at the given center."""
    return inject_params(
        cx=center_x, cy=center_y, cz=center_z, r=radius, name=name
    ) + """
sph_id = rs.AddSphere((_cx, _cy, _cz), _r)
add_object_metadata(sph_id, _name, "Sphere r={0}".format(_r))
rs.Redraw()
result = "Created sphere: " + str(sph_id)
"""


def create_cylinder_code(
    base_x: float = 0,
    base_y: float = 0,
    base_z: float = 0,
    radius: float = 5,
    height: float = 10,
    cap: bool = True,
    name: str = "Cylinder",
) -> str:
    """Create a vertical cylinder with base center at (base_x, base_y, base_z)."""
    return inject_params(
        bx=base_x, by=base_y, bz=base_z, r=radius, h=height, cap=cap, name=name
    ) + """
base = (_bx, _by, _bz)
top = (_bx, _by, _bz + _h)
cyl_id = rs.AddCylinder(base, top, _r, _cap)
add_object_metadata(cyl_id, _name, "Cylinder r={0} h={1}".format(_r, _h))
rs.SetUserText(cyl_id, "object_class", "primitive_cylinder")
rs.SetUserText(cyl_id, "editable_recipe_type", "cylinder")
rs.SetUserText(cyl_id, "editable_operations", "resize_cylinder_face,fillet_brep_edge,chamfer_brep_edge")
rs.SetUserText(
    cyl_id,
    "editable_recipe",
    json.dumps(
        {
            "base_x": float(_bx),
            "base_y": float(_by),
            "base_z": float(_bz),
            "radius": float(_r),
            "height": float(_h),
            "cap": bool(_cap),
        }
    ),
)
rs.Redraw()
result = "Created cylinder: " + str(cyl_id)
"""


def create_cone_code(
    base_x: float = 0,
    base_y: float = 0,
    base_z: float = 0,
    radius: float = 5,
    height: float = 10,
    cap: bool = True,
    name: str = "Cone",
) -> str:
    """Create a vertical cone with base center at (base_x, base_y, base_z)."""
    return inject_params(
        bx=base_x, by=base_y, bz=base_z, r=radius, h=height, cap=cap, name=name
    ) + """
base_plane = rs.MovePlane(rs.WorldXYPlane(), (_bx, _by, _bz))
cone_id = rs.AddCone(base_plane, _h, _r, _cap)
add_object_metadata(cone_id, _name, "Cone r={0} h={1}".format(_r, _h))
rs.Redraw()
result = "Created cone: " + str(cone_id)
"""


def create_pipe_code(
    curve_id: str,
    radius: float = 1.0,
    cap: bool = True,
    name: str = "Pipe",
) -> str:
    """Create a pipe (tube) along an existing curve."""
    return inject_params(cid=curve_id, r=radius, cap=cap, name=name) + """
pipe_ids = rs.AddPipe(System.Guid(_cid), [0, 1], [_r, _r], cap=_cap if _cap else 0)
if pipe_ids:
    for pid in pipe_ids:
        add_object_metadata(pid, _name, "Pipe r={0}".format(_r))
rs.Redraw()
pipe_ids_str = [str(pid) for pid in pipe_ids] if pipe_ids else []
result = "Created pipe(s): " + str(pipe_ids_str)
"""


def create_arc_code(
    center_x: float = 0,
    center_y: float = 0,
    center_z: float = 0,
    radius: float = 5,
    start_angle: float = 0,
    end_angle: float = 90,
    name: str = "Arc",
) -> str:
    """Create an arc on the XY plane. Angles are in degrees."""
    return inject_params(
        cx=center_x, cy=center_y, cz=center_z, r=radius, sa=start_angle, ea=end_angle, name=name
    ) + """
plane = rs.MovePlane(rs.WorldXYPlane(), (_cx, _cy, _cz))
# rs.AddArc erwartet das Bogen-Intervall in GRAD, nicht in Bogenmass — ein
# frueheres math.radians() machte aus z.B. 90 Grad ~1.57 "Grad" (winziger Bogen).
arc_id = rs.AddArc(plane, _r, _ea - _sa)
if _sa != 0:
    rs.RotateObject(arc_id, (_cx, _cy, _cz), _sa)
add_object_metadata(arc_id, _name, "Arc r={0}".format(_r))
rs.Redraw()
result = "Created arc: " + str(arc_id)
"""


def create_torus_code(
    center_x: float = 0,
    center_y: float = 0,
    center_z: float = 0,
    major_radius: float = 10,
    minor_radius: float = 2,
    name: str = "Torus",
) -> str:
    """Create a torus on the XY plane."""
    return inject_params(
        cx=center_x, cy=center_y, cz=center_z, R=major_radius, r=minor_radius, name=name
    ) + """
torus = rg.Torus(rg.Plane(rg.Point3d(_cx, _cy, _cz), rg.Vector3d.ZAxis), _R, _r)
brep = torus.ToRevSurface().ToBrep()
tor_id = sc.doc.Objects.AddBrep(brep)
add_object_metadata(tor_id, _name, "Torus R={0} r={1}".format(_R, _r))
rs.Redraw()
result = "Created torus: " + str(tor_id)
"""
