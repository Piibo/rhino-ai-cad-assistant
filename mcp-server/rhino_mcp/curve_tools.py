"""Curve operation tools – create, modify, analyse curves.

Original work. Tool design inspired by quocvibui/rhino3d-mcp
(https://github.com/quocvibui/rhino3d-mcp); reimplemented from scratch.
"""

from mcp.server.fastmcp import FastMCP, Context
import logging
from typing import List, Optional

from .helpers import inject_params, run_code, format_result

logger = logging.getLogger("CurveTools")


class CurveTools:
    def __init__(self, app: FastMCP):
        self.app = app
        self._register_tools()

    def _register_tools(self):
        self.app.tool()(self.create_interpolated_curve)
        self.app.tool()(self.create_control_point_curve)
        self.app.tool()(self.offset_curve)
        self.app.tool()(self.join_curves)
        self.app.tool()(self.explode_curves)
        self.app.tool()(self.fillet_curves)
        self.app.tool()(self.extend_curve)
        self.app.tool()(self.rebuild_curve)
        self.app.tool()(self.divide_curve)
        self.app.tool()(self.project_curve_to_surface)

    # ── Creation ──────────────────────────────────────────────────────────

    def create_interpolated_curve(self, ctx: Context,
                                  points: List[List[float]],
                                  degree: int = 3,
                                  name: str = "InterpCurve") -> str:
        """Create a curve that passes through (interpolates) the given points.

        Args:
            points: [[x,y,z], …] – at least 2 points.
            degree: Curve degree (1=linear, 3=cubic).
        """
        code = inject_params(pts=points, deg=degree, name=name) + """
crv_id = rs.AddInterpCurve([(_p[0],_p[1],_p[2]) for _p in _pts], _deg)
add_object_metadata(crv_id, _name, "Interpolated curve, {0} pts, deg {1}".format(len(_pts), _deg))
rs.Redraw()
result = "Created interpolated curve: " + str(crv_id)
"""
        return format_result(run_code(code))

    def create_control_point_curve(self, ctx: Context,
                                   points: List[List[float]],
                                   degree: int = 3,
                                   name: str = "CPCurve") -> str:
        """Create a NURBS curve defined by control points (curve does NOT pass through them).

        Args:
            points: [[x,y,z], …] control points.
            degree: Curve degree.
        """
        code = inject_params(pts=points, deg=degree, name=name) + """
crv_id = rs.AddCurve([(_p[0],_p[1],_p[2]) for _p in _pts], _deg)
add_object_metadata(crv_id, _name, "CP curve, {0} pts, deg {1}".format(len(_pts), _deg))
rs.Redraw()
result = "Created control-point curve: " + str(crv_id)
"""
        return format_result(run_code(code))

    # ── Modification ──────────────────────────────────────────────────────

    def offset_curve(self, ctx: Context,
                     curve_id: str = "",
                     distance: float = 1.0,
                     direction_point: Optional[List[float]] = None,
                     name: str = "OffsetCurve") -> str:
        """Offset a curve by a distance.

        Args:
            curve_id: GUID of the source curve.
            distance: Offset distance.
            direction_point: [x,y,z] point indicating offset side.  If omitted, offsets outward.
        """
        dp = direction_point or [0, 0, 0]
        code = inject_params(cid=curve_id, dist=distance, dp=dp, name=name) + """
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
result = "Offset curve(s): " + str(off_ids)
"""
        return format_result(run_code(code))

    def join_curves(self, ctx: Context,
                    curve_ids: List[str],
                    name: str = "JoinedCurve") -> str:
        """Join multiple curves into one or more polycurves.

        Args:
            curve_ids: List of curve GUIDs to join.
        """
        code = inject_params(ids=curve_ids, name=name) + """
guids = [System.Guid(i) for i in _ids]
joined = rs.JoinCurves(guids, delete_input=False)
if joined:
    for j in joined:
        add_object_metadata(j, _name, "Joined curve")
rs.Redraw()
result = "Joined into {0} curve(s): {1}".format(len(joined) if joined else 0, str(joined))
"""
        return format_result(run_code(code))

    def explode_curves(self, ctx: Context,
                       curve_id: str = "") -> str:
        """Explode a polycurve into its individual segments."""
        code = inject_params(cid=curve_id) + """
segments = rs.ExplodeCurves(System.Guid(_cid), delete_input=False)
if segments:
    for i, seg in enumerate(segments):
        add_object_metadata(seg, "Segment_{0}".format(i), "Exploded segment")
rs.Redraw()
result = "Exploded into {0} segment(s): {1}".format(len(segments) if segments else 0, str(segments))
"""
        return format_result(run_code(code))

    def fillet_curves(self, ctx: Context,
                      curve_id_1: str = "", curve_id_2: str = "",
                      radius: float = 1.0,
                      name: str = "Fillet") -> str:
        """Create a fillet arc between two curves.

        Args:
            curve_id_1, curve_id_2: GUIDs of the two curves.
            radius: Fillet radius.
        """
        code = inject_params(c1=curve_id_1, c2=curve_id_2, r=radius, name=name) + """
fillet_id = rs.AddFilletCurve(System.Guid(_c1), System.Guid(_c2), _r)
if fillet_id:
    add_object_metadata(fillet_id, _name, "Fillet r={0}".format(_r))
rs.Redraw()
result = "Created fillet: " + str(fillet_id)
"""
        return format_result(run_code(code))

    def extend_curve(self, ctx: Context,
                     curve_id: str = "",
                     length: float = 5.0,
                     side: int = 2,
                     extension_type: int = 0) -> str:
        """Extend a curve by a given length.

        Args:
            curve_id: GUID of the curve.
            length: Extension length.
            side: 0=start, 1=end, 2=both.
            extension_type: 0=line, 1=arc, 2=smooth.
        """
        code = inject_params(cid=curve_id, l=length, side=side, ext=extension_type) + """
guid = System.Guid(_cid)
extended = rs.ExtendCurveLength(guid, _ext, _side, _l)
rs.Redraw()
result = "Extended curve: " + str(extended)
"""
        return format_result(run_code(code))

    def rebuild_curve(self, ctx: Context,
                      curve_id: str = "",
                      point_count: int = 10,
                      degree: int = 3) -> str:
        """Rebuild a curve with a new point count and degree.

        Useful for simplifying or smoothing curves.
        """
        code = inject_params(cid=curve_id, pc=point_count, deg=degree) + """
rebuilt = rs.RebuildCurve(System.Guid(_cid), _deg, _pc)
rs.Redraw()
result = "Rebuilt curve: " + str(rebuilt)
"""
        return format_result(run_code(code))

    def divide_curve(self, ctx: Context,
                     curve_id: str = "",
                     segment_count: int = 10) -> str:
        """Divide a curve into equal segments and return the division points.

        Args:
            curve_id: GUID of the curve.
            segment_count: Number of equal segments.
        """
        code = inject_params(cid=curve_id, n=segment_count) + """
pts = rs.DivideCurve(System.Guid(_cid), _n)
pts_list = [[p[0], p[1], p[2]] for p in pts] if pts else []
result = json.dumps({"count": len(pts_list), "points": pts_list})
"""
        return format_result(run_code(code))

    def project_curve_to_surface(self, ctx: Context,
                                 curve_id: str = "",
                                 surface_id: str = "",
                                 direction: List[float] = None,
                                 name: str = "ProjectedCurve") -> str:
        """Project a curve onto a surface along a direction vector.

        Args:
            direction: [x,y,z] projection direction.  Defaults to [0,0,-1] (down).
        """
        d = direction or [0, 0, -1]
        code = inject_params(cid=curve_id, sid=surface_id, dir=d, name=name) + """
proj = rs.ProjectCurveToSurface(System.Guid(_cid), System.Guid(_sid), tuple(_dir))
if proj:
    for p in proj:
        add_object_metadata(p, _name, "Projected curve")
rs.Redraw()
result = "Projected curve(s): " + str(proj)
"""
        return format_result(run_code(code))
