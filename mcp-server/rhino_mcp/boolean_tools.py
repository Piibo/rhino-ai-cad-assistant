"""Boolean operation tools – union, difference, intersection.

Original work. Tool design inspired by quocvibui/rhino3d-mcp
(https://github.com/quocvibui/rhino3d-mcp); reimplemented from scratch.
"""

from mcp.server.fastmcp import FastMCP, Context
import logging
from typing import List

from .helpers import inject_params, run_code, format_result

logger = logging.getLogger("BooleanTools")


class BooleanTools:
    def __init__(self, app: FastMCP):
        self.app = app
        self._register_tools()

    def _register_tools(self):
        self.app.tool()(self.boolean_union)
        self.app.tool()(self.boolean_difference)
        self.app.tool()(self.boolean_intersection)
        self.app.tool()(self.boolean_split)

    def boolean_union(self, ctx: Context,
                      object_ids: List[str],
                      delete_input: bool = True,
                      name: str = "BoolUnion") -> str:
        """Boolean union of two or more closed solids/polysurfaces.

        Args:
            object_ids: GUIDs of the objects to union (at least 2).
            delete_input: Delete the input objects after union.
        """
        code = inject_params(ids=object_ids, delete=delete_input, name=name) + """
guids = [System.Guid(i) for i in _ids]
union = rs.BooleanUnion(guids, _delete)
if union:
    for u in union:
        add_object_metadata(u, _name, "Boolean union of {0} objects".format(len(_ids)))
rs.Redraw()
result = "Boolean union result: " + str(union)
"""
        return format_result(run_code(code))

    def boolean_difference(self, ctx: Context,
                           keep_id: str = "",
                           remove_ids: List[str] = [],
                           delete_input: bool = True,
                           name: str = "BoolDiff") -> str:
        """Boolean difference: subtract remove_ids from keep_id.

        Args:
            keep_id: GUID of the object to keep.
            remove_ids: GUIDs of the objects to subtract.
            delete_input: Delete input objects after operation.
        """
        code = inject_params(keep=keep_id, removes=remove_ids,
                             delete=delete_input, name=name) + """
keep_guid = System.Guid(_keep)
remove_guids = [System.Guid(r) for r in _removes]
diff = rs.BooleanDifference(keep_guid, remove_guids, _delete)
if diff:
    for d in diff:
        add_object_metadata(d, _name, "Boolean difference")
rs.Redraw()
result = "Boolean difference result: " + str(diff)
"""
        return format_result(run_code(code))

    def boolean_intersection(self, ctx: Context,
                             object_ids: List[str],
                             delete_input: bool = True,
                             name: str = "BoolIntersect") -> str:
        """Boolean intersection: keep only the overlapping volume.

        Args:
            object_ids: GUIDs of the objects to intersect (exactly 2).
            delete_input: Delete input objects after operation.
        """
        code = inject_params(ids=object_ids, delete=delete_input, name=name) + """
if len(_ids) < 2:
    result = "Error: need at least 2 objects for intersection"
else:
    guids = [System.Guid(i) for i in _ids]
    inter = rs.BooleanIntersection(guids[0], guids[1], _delete)
    if inter:
        for it in inter:
            add_object_metadata(it, _name, "Boolean intersection")
    rs.Redraw()
    result = "Boolean intersection result: " + str(inter)
"""
        return format_result(run_code(code))

    def boolean_split(self, ctx: Context,
                      object_id: str = "",
                      cutter_id: str = "",
                      delete_input: bool = False,
                      name: str = "BoolSplit") -> str:
        """Split a brep with another brep (non-destructive by default).

        Args:
            object_id: GUID of the object to split.
            cutter_id: GUID of the cutting object.
            delete_input: Delete input objects after split.
        """
        code = inject_params(oid=object_id, cid=cutter_id,
                             delete=delete_input, name=name) + """
splits = rs.SplitBrep(System.Guid(_oid), System.Guid(_cid), _delete)
if splits:
    for i, s in enumerate(splits):
        add_object_metadata(s, "{0}_{1}".format(_name, i), "Boolean split fragment")
rs.Redraw()
result = "Split into {0} parts: {1}".format(len(splits) if splits else 0, str(splits))
"""
        return format_result(run_code(code))
