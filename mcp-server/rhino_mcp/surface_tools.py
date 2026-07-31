"""Surface and solid operation tools – extrude, loft, sweep, revolve.

Original work. Tool design inspired by quocvibui/rhino3d-mcp
(https://github.com/quocvibui/rhino3d-mcp); reimplemented from scratch.
"""

from mcp.server.fastmcp import FastMCP, Context
import logging
from typing import List

from .helpers import inject_params, run_code, format_result

logger = logging.getLogger("SurfaceTools")


class SurfaceTools:
    def __init__(self, app: FastMCP):
        self.app = app
        self._register_tools()

    def _register_tools(self):
        self.app.tool()(self.extrude_curve)
        self.app.tool()(self.loft_curves)
        self.app.tool()(self.sweep1)
        self.app.tool()(self.sweep2)
        self.app.tool()(self.revolve_curve)
        self.app.tool()(self.planar_surface)
        self.app.tool()(self.offset_surface)
        self.app.tool()(self.cap_planar_holes)

    # ── Extrude ───────────────────────────────────────────────────────────

    def extrude_curve(self, ctx: Context,
                      curve_id: str = "",
                      dx: float = 0, dy: float = 0, dz: float = 10,
                      cap: bool = True,
                      name: str = "Extrusion") -> str:
        """Extrude a curve along a direction vector (dx, dy, dz).

        Args:
            curve_id: GUID of the profile curve.
            dx, dy, dz: Extrusion direction and length.
            cap: Cap planar openings.
        """
        code = inject_params(cid=curve_id, dx=dx, dy=dy, dz=dz, cap=cap, name=name) + """
path = rs.AddLine((0,0,0), (_dx,_dy,_dz))
srf_id = rs.ExtrudeCurve(System.Guid(_cid), path)
rs.DeleteObject(path)
if srf_id and _cap:
    rs.CapPlanarHoles(srf_id)
if srf_id:
    add_object_metadata(srf_id, _name, "Extrusion")
rs.Redraw()
result = "Created extrusion: " + str(srf_id)
"""
        return format_result(run_code(code))

    # ── Loft ──────────────────────────────────────────────────────────────

    def loft_curves(self, ctx: Context,
                    curve_ids: List[str],
                    loft_type: int = 0,
                    closed: bool = False,
                    name: str = "Loft") -> str:
        """Create a lofted surface through multiple cross-section curves.

        Args:
            curve_ids: GUIDs of the profile curves (in order).
            loft_type: 0=Normal, 1=Loose, 2=Tight, 3=Straight.
            closed: Close the loft (connect last profile back to first).
        """
        code = inject_params(ids=curve_ids, lt=loft_type, closed=closed, name=name) + """
guids = [System.Guid(i) for i in _ids]
loft = rs.AddLoftSrf(guids, loft_type=_lt, closed=_closed)
if loft:
    for lid in loft:
        add_object_metadata(lid, _name, "Loft ({0} sections)".format(len(_ids)))
rs.Redraw()
result = "Created loft: " + str(loft)
"""
        return format_result(run_code(code))

    # ── Sweep ─────────────────────────────────────────────────────────────

    def sweep1(self, ctx: Context,
               rail_id: str = "",
               cross_section_ids: List[str] = [],
               closed: bool = False,
               name: str = "Sweep1") -> str:
        """Sweep cross-section curves along a single rail curve.

        Args:
            rail_id: GUID of the rail curve.
            cross_section_ids: GUIDs of the cross-section curves.
            closed: Close the sweep.
        """
        code = inject_params(rail=rail_id, sections=cross_section_ids,
                             closed=closed, name=name) + """
rail_guid = System.Guid(_rail)
section_guids = [System.Guid(s) for s in _sections]
sweep = rs.AddSweep1(rail_guid, section_guids, _closed)
if sweep:
    for sid in sweep:
        add_object_metadata(sid, _name, "Sweep1")
rs.Redraw()
result = "Created sweep1: " + str(sweep)
"""
        return format_result(run_code(code))

    def sweep2(self, ctx: Context,
               rail1_id: str = "", rail2_id: str = "",
               cross_section_ids: List[str] = [],
               name: str = "Sweep2") -> str:
        """Sweep cross-section curves along two rail curves.

        Args:
            rail1_id, rail2_id: GUIDs of the two rail curves.
            cross_section_ids: GUIDs of the cross-section curves.
        """
        code = inject_params(r1=rail1_id, r2=rail2_id,
                             sections=cross_section_ids, name=name) + """
r1_guid = System.Guid(_r1)
r2_guid = System.Guid(_r2)
section_guids = [System.Guid(s) for s in _sections]
sweep = rs.AddSweep2(r1_guid, r2_guid, section_guids)
if sweep:
    for sid in sweep:
        add_object_metadata(sid, _name, "Sweep2")
rs.Redraw()
result = "Created sweep2: " + str(sweep)
"""
        return format_result(run_code(code))

    # ── Revolve ───────────────────────────────────────────────────────────

    def revolve_curve(self, ctx: Context,
                      curve_id: str = "",
                      axis_start: List[float] = [0, 0, 0],
                      axis_end: List[float] = [0, 0, 1],
                      start_angle: float = 0,
                      end_angle: float = 360,
                      name: str = "Revolve") -> str:
        """Revolve a profile curve around an axis.

        Args:
            curve_id: GUID of the profile curve.
            axis_start, axis_end: [x,y,z] defining the revolution axis.
            start_angle, end_angle: Angle range in degrees.
        """
        code = inject_params(cid=curve_id, a0=axis_start, a1=axis_end,
                             sa=start_angle, ea=end_angle, name=name) + """
# rs.AddRevSrf erwartet die Winkel in GRAD (Default end_angle=360.0), NICHT in
# Bogenmass — math.radians(360) ergab ~6.28 "Grad" und damit nur einen Splitter.
rev = rs.AddRevSrf(System.Guid(_cid), rs.AddLine(_a0, _a1),
                   _sa, _ea)
if rev:
    add_object_metadata(rev, _name, "Revolve {0}-{1} deg".format(_sa, _ea))
rs.Redraw()
result = "Created revolve: " + str(rev)
"""
        return format_result(run_code(code))

    # ── Planar Surface ────────────────────────────────────────────────────

    def planar_surface(self, ctx: Context,
                       curve_ids: List[str],
                       name: str = "PlanarSrf") -> str:
        """Create a planar surface from one or more closed, planar curves.

        Args:
            curve_ids: GUIDs of the boundary curves.
        """
        code = inject_params(ids=curve_ids, name=name) + """
guids = [System.Guid(i) for i in _ids]
srf = rs.AddPlanarSrf(guids)
if srf:
    for s in srf:
        add_object_metadata(s, _name, "Planar surface")
rs.Redraw()
result = "Created planar surface: " + str(srf)
"""
        return format_result(run_code(code))

    # ── Offset Surface ────────────────────────────────────────────────────

    def offset_surface(self, ctx: Context,
                       surface_id: str = "",
                       distance: float = 1.0,
                       name: str = "OffsetSrf") -> str:
        """Offset a surface by a given distance (creates a thickened shell)."""
        code = inject_params(sid=surface_id, dist=distance, name=name) + """
off = rs.OffsetSurface(System.Guid(_sid), _dist)
if off:
    add_object_metadata(off, _name, "Offset d={0}".format(_dist))
rs.Redraw()
result = "Offset surface: " + str(off)
"""
        return format_result(run_code(code))

    # ── Cap Holes ─────────────────────────────────────────────────────────

    def cap_planar_holes(self, ctx: Context,
                         brep_id: str = "") -> str:
        """Cap all planar holes in a brep/polysurface."""
        code = inject_params(bid=brep_id) + """
capped = rs.CapPlanarHoles(System.Guid(_bid))
rs.Redraw()
result = "Capped: " + str(capped)
"""
        return format_result(run_code(code))
