"""Code templates: curve operations. See code_templates.py."""
from __future__ import annotations

from typing import List

from ._codegen_base import inject_params

__all__ = [
    "offset_curve_code",
    "join_curves_code",
    "curve_boolean_union_code",
    "fillet_curves_code",
    "divide_curve_code",
    "create_interpolated_curve_code",
    "create_control_point_curve_code",
    "explode_curves_code",
    "extend_curve_code",
    "rebuild_curve_code",
    "project_curve_to_surface_code",
]


def offset_curve_code(
    curve_id: str,
    distance: float = 1.0,
    direction_point: List[float] | None = None,
    name: str = "OffsetCurve",
) -> str:
    """Offset a curve by a distance."""
    dp = direction_point or [0, 0, 0]
    return inject_params(cid=curve_id, dist=distance, dp=dp, name=name) + """
mid = rs.CurveMidPoint(System.Guid(_cid))
if _dp == [0,0,0]:
    normal = rs.CurveNormal(System.Guid(_cid))
    if normal:
        _dp = [mid[0]+normal[0], mid[1]+normal[1], mid[2]+normal[2]]
    else:
        _dp = [mid[0]+1, mid[1], mid[2]]
off_ids = rs.OffsetCurve(System.Guid(_cid), _dp, _dist)
if off_ids:
    for oid in off_ids:
        add_object_metadata(oid, _name, "Offset d={0}".format(_dist))
rs.Redraw()
off_ids_str = [str(oid) for oid in off_ids] if off_ids else []
result = "Offset curve(s): " + str(off_ids_str)
"""


def join_curves_code(
    curve_ids: List[str],
    name: str = "JoinedCurve",
) -> str:
    """Join multiple curves into one or more polycurves."""
    return inject_params(ids=curve_ids, name=name) + """
guids = [System.Guid(i) for i in _ids]
joined = rs.JoinCurves(guids, delete_input=False)
if joined:
    for j in joined:
        add_object_metadata(j, _name, "Joined curve")
rs.Redraw()
joined_str = [str(j) for j in joined] if joined else []
result = "Joined into {0} curve(s): {1}".format(len(joined_str), joined_str)
"""


def curve_boolean_union_code(
    curve_ids: List[str],
    delete_input: bool = True,
    name: str = "UnionKontur",
) -> str:
    """2D boolean union of closed, coplanar, overlapping planar curves.

    Dedicated replacement for the raw ``rs.CurveBooleanUnion`` path the model
    previously had to improvise via ``execute_rhino_code`` (Pilot 01.07.2026:
    hand-computed tangent arcs self-intersected over many rebuild cycles).
    Validates the inputs with actionable error messages, archives them for
    undo, and only deletes them after a successful union.
    """
    return inject_params(ids=curve_ids, delete_input=delete_input, name=name) + """
guids = [System.Guid(i) for i in _ids]
problems = []
for g in guids:
    if not rs.IsCurve(g):
        problems.append("{0} ist keine Kurve".format(g))
    elif not rs.IsCurveClosed(g):
        problems.append("{0} ist nicht geschlossen".format(g))
    elif not rs.IsCurvePlanar(g):
        problems.append("{0} ist nicht planar".format(g))
if problems:
    result = ("Fehler: curve_boolean_union braucht geschlossene, planare "
              "Kurven — " + "; ".join(problems))
else:
    for g in guids:
        archive_object(g)
    union_ids = rs.CurveBooleanUnion(guids)
    if not union_ids:
        result = ("Fehler: Kurven-Boolean lieferte kein Ergebnis. Typische "
                  "Ursachen: die Kurven liegen nicht in derselben Ebene "
                  "(koplanar noetig) oder ueberlappen sich nicht.")
    else:
        if _delete_input:
            for g in guids:
                try:
                    rs.DeleteObject(g)
                except Exception:
                    pass
        for u in union_ids:
            add_object_metadata(u, _name, "Kurven-Boolean-Union aus {0} Kurven".format(len(guids)))
        rs.Redraw()
        union_str = [str(u) for u in union_ids]
        result = "Union-Kontur erstellt ({0} Kurve(n)): {1}".format(len(union_str), union_str)
"""


def fillet_curves_code(
    curve_id_1: str,
    curve_id_2: str,
    radius: float = 1.0,
    name: str = "Fillet",
) -> str:
    """Create a fillet arc between two curves."""
    return inject_params(c1=curve_id_1, c2=curve_id_2, r=radius, name=name) + """
fillet_id = rs.AddFilletCurve(System.Guid(_c1), System.Guid(_c2), _r)
if fillet_id:
    add_object_metadata(fillet_id, _name, "Fillet r={0}".format(_r))
rs.Redraw()
result = "Created fillet: " + str(fillet_id)
"""


def divide_curve_code(
    curve_id: str,
    segment_count: int = 10,
) -> str:
    """Divide a curve into equal segments and return the division points."""
    return inject_params(cid=curve_id, n=segment_count) + """
pts = rs.DivideCurve(System.Guid(_cid), _n)
pts_list = [[p[0], p[1], p[2]] for p in pts] if pts else []
result = json.dumps({"count": len(pts_list), "points": pts_list})
"""


def create_interpolated_curve_code(
    points: List[List[float]],
    degree: int = 3,
    name: str = "InterpCurve",
) -> str:
    """Create a curve that passes through the given points."""
    return inject_params(pts=points, deg=degree, name=name) + """
crv_id = rs.AddInterpCurve([(_p[0],_p[1],_p[2]) for _p in _pts], _deg)
add_object_metadata(crv_id, _name, "Interpolated curve, {0} pts, deg {1}".format(len(_pts), _deg))
rs.Redraw()
result = "Created interpolated curve: " + str(crv_id)
"""


def create_control_point_curve_code(
    points: List[List[float]],
    degree: int = 3,
    name: str = "CPCurve",
) -> str:
    """Create a NURBS curve defined by control points."""
    return inject_params(pts=points, deg=degree, name=name) + """
crv_id = rs.AddCurve([(_p[0],_p[1],_p[2]) for _p in _pts], _deg)
add_object_metadata(crv_id, _name, "CP curve, {0} pts, deg {1}".format(len(_pts), _deg))
rs.Redraw()
result = "Created control-point curve: " + str(crv_id)
"""


def explode_curves_code(curve_id: str) -> str:
    """Explode a polycurve into individual segments."""
    return inject_params(cid=curve_id) + """
segments = rs.ExplodeCurves(System.Guid(_cid), delete_input=False)
if segments:
    for i, seg in enumerate(segments):
        add_object_metadata(seg, "Segment_{0}".format(i), "Exploded segment")
rs.Redraw()
segments_str = [str(seg) for seg in segments] if segments else []
result = "Exploded into {0} segment(s): {1}".format(len(segments_str), segments_str)
"""


def extend_curve_code(
    curve_id: str,
    length: float = 5.0,
    side: int = 2,
    extension_type: int = 0,
) -> str:
    """Extend a curve by a given length."""
    return inject_params(cid=curve_id, l=length, side=side, ext=extension_type) + """
guid = System.Guid(_cid)
archive_object(guid)
extended = rs.ExtendCurveLength(guid, _ext, _side, _l)
rs.Redraw()
result = "Extended curve: " + str(extended)
"""


def rebuild_curve_code(
    curve_id: str,
    point_count: int = 10,
    degree: int = 3,
) -> str:
    """Rebuild a curve with a new point count and degree."""
    return inject_params(cid=curve_id, pc=point_count, deg=degree) + """
archive_object(System.Guid(_cid))
rebuilt = rs.RebuildCurve(System.Guid(_cid), _deg, _pc)
rs.Redraw()
if rebuilt:
    result = "Rebuilt curve in place: " + str(_cid)
else:
    result = "Rebuild curve failed"
"""


def project_curve_to_surface_code(
    curve_id: str,
    surface_id: str,
    direction: List[float] | None = None,
    name: str = "ProjectedCurve",
) -> str:
    """Project a curve onto a surface along a direction vector."""
    return inject_params(cid=curve_id, sid=surface_id, dir=direction or [0, 0, -1], name=name) + """
proj = rs.ProjectCurveToSurface(System.Guid(_cid), System.Guid(_sid), tuple(_dir))
if proj:
    for p in proj:
        add_object_metadata(p, _name, "Projected curve")
rs.Redraw()
proj_str = [str(p) for p in proj] if proj else []
result = "Projected curve(s): " + str(proj_str)
"""
