"""Transformation tools – move, rotate, scale, mirror, copy, array.

Original work. Tool design inspired by quocvibui/rhino3d-mcp
(https://github.com/quocvibui/rhino3d-mcp); reimplemented from scratch.
"""

from mcp.server.fastmcp import FastMCP, Context
import logging
from typing import List

from .helpers import inject_params, run_code, format_result

logger = logging.getLogger("TransformationTools")


class TransformationTools:
    def __init__(self, app: FastMCP):
        self.app = app
        self._register_tools()

    def _register_tools(self):
        self.app.tool()(self.move_objects)
        self.app.tool()(self.copy_objects)
        self.app.tool()(self.rotate_objects)
        self.app.tool()(self.scale_objects)
        self.app.tool()(self.mirror_objects)
        self.app.tool()(self.array_linear)
        self.app.tool()(self.array_polar)
        self.app.tool()(self.delete_objects)

    # ── Move ──────────────────────────────────────────────────────────────

    def move_objects(self, ctx: Context,
                     object_ids: List[str],
                     dx: float = 0, dy: float = 0, dz: float = 0) -> str:
        """Move one or more objects by a translation vector (dx, dy, dz)."""
        code = inject_params(ids=object_ids, dx=dx, dy=dy, dz=dz) + """
guids = [System.Guid(i) for i in _ids]
for g in guids:
    rs.MoveObject(g, (_dx, _dy, _dz))
rs.Redraw()
result = "Moved {0} object(s) by ({1},{2},{3})".format(len(guids), _dx, _dy, _dz)
"""
        return format_result(run_code(code))

    # ── Copy ──────────────────────────────────────────────────────────────

    def copy_objects(self, ctx: Context,
                     object_ids: List[str],
                     dx: float = 0, dy: float = 0, dz: float = 0) -> str:
        """Copy one or more objects, offset by (dx, dy, dz).

        Returns the GUIDs of the new copies.
        """
        code = inject_params(ids=object_ids, dx=dx, dy=dy, dz=dz) + """
guids = [System.Guid(i) for i in _ids]
copies = []
for g in guids:
    c = rs.CopyObject(g, (_dx, _dy, _dz))
    if c:
        copies.append(str(c))
rs.Redraw()
result = "Copied {0} object(s): {1}".format(len(copies), copies)
"""
        return format_result(run_code(code))

    # ── Rotate ────────────────────────────────────────────────────────────

    def rotate_objects(self, ctx: Context,
                       object_ids: List[str],
                       angle_degrees: float = 90,
                       center: List[float] = [0, 0, 0],
                       axis: List[float] = [0, 0, 1]) -> str:
        """Rotate objects around a point and axis.

        Args:
            object_ids: GUIDs to rotate.
            angle_degrees: Rotation angle in degrees.
            center: [x,y,z] center of rotation.
            axis: [x,y,z] rotation axis direction.
        """
        code = inject_params(ids=object_ids, angle=angle_degrees,
                             center=center, axis=axis) + """
guids = [System.Guid(i) for i in _ids]
for g in guids:
    rs.RotateObject(g, _center, _angle, _axis)
rs.Redraw()
result = "Rotated {0} object(s) by {1} degrees".format(len(guids), _angle)
"""
        return format_result(run_code(code))

    # ── Scale ─────────────────────────────────────────────────────────────

    def scale_objects(self, ctx: Context,
                      object_ids: List[str],
                      scale_factor: float = 2.0,
                      origin: List[float] = [0, 0, 0]) -> str:
        """Uniformly scale objects from an origin point.

        Args:
            scale_factor: Scale multiplier (>1 = bigger, <1 = smaller).
            origin: [x,y,z] scale origin.
        """
        code = inject_params(ids=object_ids, sf=scale_factor, org=origin) + """
guids = [System.Guid(i) for i in _ids]
for g in guids:
    rs.ScaleObject(g, _org, (_sf, _sf, _sf))
rs.Redraw()
result = "Scaled {0} object(s) by factor {1}".format(len(guids), _sf)
"""
        return format_result(run_code(code))

    # ── Mirror ────────────────────────────────────────────────────────────

    def mirror_objects(self, ctx: Context,
                       object_ids: List[str],
                       mirror_plane_origin: List[float] = [0, 0, 0],
                       mirror_plane_normal: List[float] = [1, 0, 0],
                       copy: bool = True) -> str:
        """Mirror objects across a plane.

        Args:
            mirror_plane_origin: [x,y,z] point on the mirror plane.
            mirror_plane_normal: [x,y,z] normal of the mirror plane.
            copy: Keep the original objects.
        """
        code = inject_params(ids=object_ids, org=mirror_plane_origin,
                             normal=mirror_plane_normal, copy=copy) + """
guids = [System.Guid(i) for i in _ids]
# Compute a second point on the mirror plane for rs.MirrorObject
end = [_org[0]+_normal[0]*100, _org[1]+_normal[1]*100, _org[2]+_normal[2]*100]
mirrored = []
for g in guids:
    m = rs.MirrorObject(g, _org, end, _copy)
    if m:
        mirrored.append(str(m))
rs.Redraw()
result = "Mirrored {0} object(s): {1}".format(len(mirrored), mirrored)
"""
        return format_result(run_code(code))

    # ── Linear Array ──────────────────────────────────────────────────────

    def array_linear(self, ctx: Context,
                     object_id: str = "",
                     count: int = 5,
                     dx: float = 10, dy: float = 0, dz: float = 0) -> str:
        """Create a linear array of copies of an object.

        Args:
            object_id: GUID of the source object.
            count: Total number of copies (excluding original).
            dx, dy, dz: Spacing between each copy.
        """
        code = inject_params(oid=object_id, n=count, dx=dx, dy=dy, dz=dz) + """
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
        return format_result(run_code(code))

    # ── Polar Array ───────────────────────────────────────────────────────

    def array_polar(self, ctx: Context,
                    object_id: str = "",
                    count: int = 6,
                    center: List[float] = [0, 0, 0],
                    axis: List[float] = [0, 0, 1],
                    total_angle: float = 360) -> str:
        """Create a polar (circular) array of copies around a center.

        Args:
            object_id: GUID of the source object.
            count: Number of copies (excluding original).
            center: [x,y,z] center of rotation.
            axis: [x,y,z] rotation axis.
            total_angle: Total angle to fill (degrees).
        """
        code = inject_params(oid=object_id, n=count, center=center,
                             axis=axis, angle=total_angle) + """
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
        return format_result(run_code(code))

    # ── Delete ────────────────────────────────────────────────────────────

    def delete_objects(self, ctx: Context,
                       object_ids: List[str]) -> str:
        """Delete one or more objects by their GUIDs."""
        code = inject_params(ids=object_ids) + """
guids = [System.Guid(i) for i in _ids]
deleted = rs.DeleteObjects(guids)
rs.Redraw()
result = "Deleted {0} object(s)".format(deleted if deleted else 0)
"""
        return format_result(run_code(code))
