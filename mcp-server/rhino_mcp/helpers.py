"""Shared helpers for generating and executing Python code in Rhino."""

import json
import logging
import os
import sys

logger = logging.getLogger("RhinoMCPHelpers")

# Make the sibling ``shared/`` package importable. The MCP server is
# normally launched from ``rhaino/`` as the working directory, but
# we add the parent of this file's package to sys.path defensively so
# imports work regardless of launch dir.
_RHINO_MCP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _RHINO_MCP_ROOT not in sys.path:
    sys.path.insert(0, _RHINO_MCP_ROOT)

from shared.action_history_preamble import ACTION_HISTORY_PREAMBLE  # noqa: E402

# ---------------------------------------------------------------------------
# Code preamble – prepended to every generated code block sent to Rhino.
# Provides rs, rg, sc, math, and the add_object_metadata() helper.
# Must stay IronPython 2.7 compatible (no f-strings, no walrus, no match).
# ---------------------------------------------------------------------------

CODE_PREAMBLE = r'''
import rhinoscriptsyntax as rs
import scriptcontext as sc
import Rhino
import Rhino.Geometry as rg
import json
import math
import time
import System
from datetime import datetime
''' + ACTION_HISTORY_PREAMBLE + r'''

def archive_object(obj_id):
    """Copy an object to the Archive layer before modifying it.

    ALWAYS call this before using sc.doc.Objects.Replace() or
    directly editing SubD vertices/edges. This is our only safety
    net since script-based Replace does not support Ctrl+Z undo.

    Stores metadata on the backup so it can be restored later:
    - archive_source_id: GUID of the original object
    - archive_timestamp: when the backup was created
    - archive_version: incremented per object

    Returns the archive copy's GUID or None on failure.
    """
    try:
        if isinstance(obj_id, str):
            obj_id = System.Guid(obj_id)
        if not rs.IsLayer("Archive"):
            rs.AddLayer("Archive", [128, 128, 128])
            rs.LayerVisible("Archive", False)

        # Count existing backups for this object to build version number
        version = 1
        source_str = str(obj_id)
        archive_objs = rs.ObjectsByLayer("Archive")
        if archive_objs:
            for oid in archive_objs:
                if rs.GetUserText(oid, "archive_source_id") == source_str:
                    try:
                        v = int(rs.GetUserText(oid, "archive_version") or "0")
                        if v >= version:
                            version = v + 1
                    except Exception:
                        pass

        copy_id = rs.CopyObject(obj_id)
        if copy_id:
            rs.ObjectLayer(copy_id, "Archive")
            orig_name = rs.ObjectName(obj_id) or "Unnamed"
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            rs.ObjectName(copy_id, "{0}_v{1}".format(orig_name, version))

            # Store restore metadata
            rs.SetUserText(copy_id, "archive_source_id", source_str)
            rs.SetUserText(copy_id, "archive_source_name", orig_name)
            rs.SetUserText(copy_id, "archive_timestamp", timestamp)
            rs.SetUserText(copy_id, "archive_version", str(version))
            _record_backup(obj_id, copy_id, orig_name)
            return copy_id
        return None
    except Exception as e:
        return None

def clear_editable_recipe(obj_id):
    """Strip primitive-recipe user-text after a topology change.

    Mirrors plugin/backend/rhino_exec.py CODE_PREAMBLE. Tools like
    create_hole, create_slot and round_edges_by_rule replace a primitive
    Brep with one that no longer matches its original recipe (a box with
    a hole isn't a box anymore); leaving the recipe tags in place would
    let the structure-slider rebuild the object from the OLD recipe,
    silently destroying the new feature.
    """
    try:
        if isinstance(obj_id, str):
            obj_id = System.Guid(obj_id)
        for key in (
            "object_class",
            "editable_recipe",
            "editable_recipe_type",
            "editable_operations",
        ):
            try:
                rs.SetUserText(obj_id, key, None)
            except Exception:
                pass
    except Exception:
        pass

def add_object_metadata(obj_id, name=None, description=None):
    """Add standardised metadata to a Rhino object.

    Automatically archives any existing object with the same name
    to the 'Archive' layer (hidden) and places the new object on
    the 'Active' layer.
    """
    try:
        # Coerce string GUIDs to rs-compatible format
        if isinstance(obj_id, str):
            obj_id = System.Guid(obj_id)

        # Ensure Active/Archive layers exist
        if not rs.IsLayer("Active"):
            rs.AddLayer("Active", [0, 255, 0])
        if not rs.IsLayer("Archive"):
            rs.AddLayer("Archive", [128, 128, 128])
            rs.LayerVisible("Archive", False)

        # Archive existing objects with the same name on Active layer
        if name:
            active_objs = rs.ObjectsByLayer("Active")
            if active_objs:
                for oid in active_objs:
                    if rs.ObjectName(oid) == name:
                        archive_object(oid)
                        rs.DeleteObject(oid)

        short_id = datetime.now().strftime("%d%H%M%S")
        bbox = rs.BoundingBox(obj_id)
        bbox_data = [[p.X, p.Y, p.Z] for p in bbox] if bbox else []
        obj = sc.doc.Objects.Find(obj_id)
        obj_type = obj.Geometry.GetType().Name if obj else "Unknown"

        # Place new object on Active layer
        rs.ObjectLayer(obj_id, "Active")

        metadata = {
            "short_id": short_id,
            "created_at": time.time(),
            "layer": "Active",
            "type": obj_type,
            "bbox": bbox_data,
        }
        if name:
            rs.ObjectName(obj_id, name)
            metadata["name"] = name
        else:
            auto_name = "{0}_{1}".format(obj_type, short_id)
            rs.ObjectName(obj_id, auto_name)
            metadata["name"] = auto_name
        if description:
            metadata["description"] = description
        user_text = metadata.copy()
        user_text["bbox"] = json.dumps(bbox_data)
        for k, v in user_text.items():
            rs.SetUserText(obj_id, k, str(v))
        _record_created_object(obj_id)
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
'''


def inject_params(**params):
    """Build a parameter-injection preamble for generated code.

    Returns lines like ``_name = value`` that can be prepended to the
    template code so that the template can reference ``_name`` freely.
    """
    lines = []
    for key, value in params.items():
        if isinstance(value, str):
            # Escape backslashes, single quotes, and newlines/CR (newlines
            # after the backslash escape so the inserted backslash isn't
            # doubled) so a multi-line string value can't break out of the
            # single-quoted literal and crash the whole generated block.
            # Byte-identical to before for newline-free values.
            escaped = (
                value.replace("\\", "\\\\")
                .replace("'", "\\'")
                .replace("\n", "\\n")
                .replace("\r", "\\r")
            )
            lines.append("_{} = '{}'".format(key, escaped))
        else:
            lines.append("_{} = {}".format(key, repr(value)))
    return "\n".join(lines) + "\n"


def run_code(code: str) -> dict:
    """Send *code* to Rhino for execution via the socket connection.

    The code is automatically prepended with :data:`CODE_PREAMBLE` so that
    ``rs``, ``rg``, ``sc`` and ``add_object_metadata`` are available.
    The executed code should set ``result = <value>`` to return data.
    """
    from .rhino_tools import get_rhino_connection

    full_code = CODE_PREAMBLE + "\n" + code

    try:
        connection = get_rhino_connection()
        response = connection.send_command("execute_code", {"code": full_code})
        return response
    except Exception as e:
        logger.error("Error executing Rhino code: %s", e)
        return {"status": "error", "message": str(e)}


def format_result(response: dict) -> str:
    """Turn a ``run_code`` response into a user-friendly string."""
    if response.get("status") == "error":
        return "Error: {}".format(response.get("message", "Unknown error"))
    return str(response.get("result", "Code executed successfully"))
