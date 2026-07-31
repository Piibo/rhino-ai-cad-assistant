"""Core Rhino tools – scene info, viewport capture, code execution.

Adapted from SerjoschDuering/rhino-mcp with Rhino 8 updates.
"""

from mcp.server.fastmcp import FastMCP, Context, Image
import logging
from typing import Dict, Any, List, Optional
import json
import socket
import time
import base64
import io
from PIL import Image as PILImage

logger = logging.getLogger("RhinoTools")


class RhinoConnection:
    """Persistent TCP connection to the Rhino-side socket server."""

    def __init__(self, host: str = "localhost", port: int = 9876):
        self.host = host
        self.port = port
        self.socket = None
        self.timeout = 30.0
        self.buffer_size = 14_485_760  # ~14 MB

    def connect(self):
        if self.socket is None:
            try:
                self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.socket.settimeout(self.timeout)
                self.socket.connect((self.host, self.port))
                logger.info("Connected to Rhino on %s:%s", self.host, self.port)
            except Exception as e:
                logger.error("Failed to connect to Rhino: %s", e)
                self.disconnect()
                raise

    def disconnect(self):
        if self.socket:
            try:
                self.socket.close()
            except Exception:
                pass
            self.socket = None

    def send_command(self, command_type: str, params: Dict[str, Any] | None = None) -> Dict[str, Any]:
        """Send a JSON command and wait for the complete JSON response."""
        if self.socket is None:
            self.connect()

        try:
            command = {"type": command_type, "params": params or {}}
            self.socket.sendall(json.dumps(command).encode("utf-8"))

            buffer = b""
            start = time.time()

            while True:
                if time.time() - start > self.timeout:
                    raise TimeoutError("Response timeout after {} s".format(self.timeout))
                try:
                    data = self.socket.recv(self.buffer_size)
                except socket.timeout:
                    raise TimeoutError("Socket timeout while receiving response")
                if not data:
                    raise ConnectionError("Connection closed by Rhino")

                buffer += data
                try:
                    response = json.loads(buffer.decode("utf-8"))
                    if response.get("status") == "error":
                        raise RuntimeError(response.get("message", "Unknown error"))
                    return response
                except json.JSONDecodeError:
                    continue  # incomplete – keep reading

        except Exception as e:
            logger.error("Communication error: %s", e)
            self.disconnect()
            raise


# ── Singleton ─────────────────────────────────────────────────────────────

_rhino_connection: RhinoConnection | None = None


def get_rhino_connection() -> RhinoConnection:
    global _rhino_connection
    if _rhino_connection is None:
        _rhino_connection = RhinoConnection()
    return _rhino_connection


# ── MCP Tools ─────────────────────────────────────────────────────────────

class RhinoTools:
    """Scene inspection, viewport capture, and raw code execution."""

    def __init__(self, app: FastMCP):
        self.app = app
        self._register_tools()

    def _register_tools(self):
        self.app.tool()(self.get_scene_info)
        self.app.tool()(self.get_layers)
        self.app.tool()(self.get_scene_objects_with_metadata)
        self.app.tool()(self.capture_viewport)
        self.app.tool()(self.get_selected)
        self.app.tool()(self.list_backups)
        self.app.tool()(self.restore_object)
        self.app.tool()(self.execute_rhino_code)

    # ── get_scene_info ────────────────────────────────────────────────────

    def get_scene_info(self, ctx: Context) -> str:
        """Get a lightweight overview of the current Rhino scene.

        Returns layer list with up to 5 sample objects per layer.
        """
        try:
            conn = get_rhino_connection()
            result = conn.send_command("get_scene_info")
            return json.dumps(result, indent=2)
        except Exception as e:
            return "Error getting scene info: {}".format(e)

    # ── get_layers ────────────────────────────────────────────────────────

    def get_layers(self, ctx: Context) -> str:
        """Get list of all layers in the Rhino document."""
        try:
            conn = get_rhino_connection()
            result = conn.send_command("get_layers")
            return json.dumps(result, indent=2)
        except Exception as e:
            return "Error getting layers: {}".format(e)

    # ── get_scene_objects_with_metadata ────────────────────────────────────

    def get_scene_objects_with_metadata(
        self,
        ctx: Context,
        filters: Optional[Dict[str, Any]] = None,
        metadata_fields: Optional[List[str]] = None,
    ) -> str:
        """Get detailed object info with metadata and optional filtering.

        Filters: layer (wildcard), name (wildcard), short_id (exact).
        metadata_fields: list of fields to return (reduces response size).
        """
        try:
            conn = get_rhino_connection()
            result = conn.send_command(
                "get_objects_with_metadata",
                {"filters": filters or {}, "metadata_fields": metadata_fields},
            )
            return json.dumps(result, indent=2)
        except Exception as e:
            return "Error getting objects: {}".format(e)

    # ── capture_viewport ──────────────────────────────────────────────────

    def capture_viewport(
        self,
        ctx: Context,
        layer: Optional[str] = None,
        show_annotations: bool = True,
        max_size: int = 800,
    ) -> Image:
        """Capture the current Rhino viewport as an image.

        Args:
            layer: Optional layer name to filter annotations.
            show_annotations: Show short_id labels in viewport.
            max_size: Maximum pixel dimension (maintains aspect ratio).
        """
        try:
            conn = get_rhino_connection()
            result = conn.send_command(
                "capture_viewport",
                {"layer": layer, "show_annotations": show_annotations, "max_size": max_size},
            )

            if result.get("type") != "image":
                raise RuntimeError(result.get("text", "Failed to capture viewport"))

            image_bytes = base64.b64decode(result["source"]["data"])
            img = PILImage.open(io.BytesIO(image_bytes))
            png_buf = io.BytesIO()
            img.save(png_buf, format="PNG")
            return Image(data=png_buf.getvalue(), format="png")

        except Exception as e:
            logger.error("Error capturing viewport: %s", e)
            raise

    # ── get_selected_objects ────────────────────────────────────────────────

    def get_selected(self, ctx: Context) -> str:
        """Get details about the currently selected objects in the Rhino viewport.

        Returns object GUIDs, names, types, layers. For SubD objects also
        returns selected sub-object indices (vertices, edges, faces) when
        the user has sub-selected them (e.g. via Ctrl+Shift click).
        """
        from .helpers import run_code

        code = r'''
import Rhino.DocObjects as rd

selected = []
for obj in sc.doc.Objects.GetSelectedObjects(False, False):
    oid = obj.Id
    info = {
        "id": str(oid),
        "name": obj.Name or "Unnamed",
        "type": obj.Geometry.GetType().Name if obj.Geometry else "Unknown",
        "layer": rs.ObjectLayer(oid),
    }

    # Check for sub-object selection (SubD vertices, edges, faces)
    grips = obj.GetGrips()
    sub_vertices = []
    sub_edges = []
    sub_faces = []

    # Method 1: Check via GetSelectedSubObjects (Rhino 8)
    try:
        sub_objs = obj.GetSelectedSubObjects()
        if sub_objs:
            for ci in sub_objs:
                if ci.ComponentIndexType == rg.ComponentIndexType.SubdVertex:
                    sub_vertices.append(ci.Index)
                elif ci.ComponentIndexType == rg.ComponentIndexType.SubdEdge:
                    sub_edges.append(ci.Index)
                elif ci.ComponentIndexType == rg.ComponentIndexType.SubdFace:
                    sub_faces.append(ci.Index)
    except Exception:
        pass

    # Add SubD info if it's a SubD
    if isinstance(obj.Geometry, rg.SubD):
        subd = obj.Geometry
        info["subd_info"] = {
            "vertex_count": subd.Vertices.Count,
            "edge_count": subd.Edges.Count,
            "face_count": subd.Faces.Count,
        }

    if sub_vertices:
        info["selected_vertices"] = sub_vertices
    if sub_edges:
        info["selected_edges"] = sub_edges
    if sub_faces:
        info["selected_faces"] = sub_faces

    selected.append(info)

result = json.dumps({"count": len(selected), "objects": selected}, indent=2)
'''
        try:
            response = run_code(code)
            if response.get("status") == "error":
                return "Error: {}".format(response.get("message", "Unknown"))
            return str(response.get("result", "No selection"))
        except Exception as e:
            return "Error getting selection: {}".format(e)

    # ── list_backups ─────────────────────────────────────────────────────

    def list_backups(self, ctx: Context, object_name: str = "") -> str:
        """List available backups on the Archive layer.

        Shows all archived versions with timestamps, sorted newest first.
        Optionally filter by object name.

        Args:
            object_name: Filter by original object name (empty = show all).
        """
        from .helpers import run_code, inject_params

        code = inject_params(name_filter=object_name) + r'''
backups = []
archive_objs = rs.ObjectsByLayer("Archive") if rs.IsLayer("Archive") else []
if archive_objs:
    for oid in archive_objs:
        source_id = rs.GetUserText(oid, "archive_source_id") or ""
        source_name = rs.GetUserText(oid, "archive_source_name") or rs.ObjectName(oid) or "Unnamed"
        timestamp = rs.GetUserText(oid, "archive_timestamp") or "unknown"
        version = rs.GetUserText(oid, "archive_version") or "?"

        if _name_filter and _name_filter.lower() not in source_name.lower():
            continue

        # Check if source object still exists
        source_exists = False
        if source_id:
            try:
                source_exists = sc.doc.Objects.Find(System.Guid(source_id)) is not None
            except Exception:
                pass

        backups.append({
            "backup_id": str(oid),
            "source_name": source_name,
            "source_id": source_id,
            "source_exists": source_exists,
            "version": version,
            "timestamp": timestamp,
            "display_name": rs.ObjectName(oid) or "Unnamed",
        })

# Sort newest first
backups.sort(key=lambda b: b["timestamp"], reverse=True)
result = json.dumps({"count": len(backups), "backups": backups}, indent=2)
'''
        try:
            response = run_code(code)
            if response.get("status") == "error":
                return "Error: {}".format(response.get("message", "Unknown"))
            return str(response.get("result", "No backups found"))
        except Exception as e:
            return "Error listing backups: {}".format(e)

    # ── restore_object ─────────────────────────────────────────────────────

    def restore_object(self, ctx: Context, backup_id: str = "", object_name: str = "") -> str:
        """Restore an object from the Archive layer.

        Provide either backup_id (exact GUID from list_backups) or
        object_name (restores the latest backup matching that name).

        The current version is archived before restoring, so nothing is lost.
        The restored object is placed back on the Active layer.

        Args:
            backup_id: GUID of the specific backup to restore.
            object_name: Restore latest backup matching this name.
        """
        from .helpers import run_code, inject_params

        code = inject_params(backup_id=backup_id, obj_name=object_name) + r'''
if not rs.IsLayer("Archive"):
    result = json.dumps({"status": "error", "message": "No Archive layer found"})
else:
    archive_objs = rs.ObjectsByLayer("Archive")
    target_backup = None

    if _backup_id:
        # Direct restore by GUID
        try:
            target_backup = System.Guid(_backup_id)
            if not sc.doc.Objects.Find(target_backup):
                target_backup = None
        except Exception:
            target_backup = None
    elif _obj_name:
        # Match against both the original source_name ("Box" -> picks the
        # latest backup version) and the display_name ("Box_v3" -> picks
        # exactly version 3). Without the display_name fallback the model
        # gets "No backup found" when it passes a name straight from
        # list_backups' display_name field.
        target = _obj_name.lower()
        best_ts = ""
        if archive_objs:
            for oid in archive_objs:
                source_name = (rs.GetUserText(oid, "archive_source_name") or "").lower()
                display_name = (rs.ObjectName(oid) or "").lower()
                if source_name != target and display_name != target:
                    continue
                ts = rs.GetUserText(oid, "archive_timestamp") or ""
                if ts > best_ts:
                    best_ts = ts
                    target_backup = oid

    if not target_backup:
        result = json.dumps({"status": "error", "message": "No backup found. Use list_backups to see available versions."})
    else:
        source_id = rs.GetUserText(target_backup, "archive_source_id") or ""
        source_name = rs.GetUserText(target_backup, "archive_source_name") or "Unnamed"
        version = rs.GetUserText(target_backup, "archive_version") or "?"

        # If the source object still exists, archive it first (so we don't lose the current state)
        if source_id:
            try:
                source_guid = System.Guid(source_id)
                source_obj = sc.doc.Objects.Find(source_guid)
                if source_obj:
                    archive_object(source_guid)
                    rs.DeleteObject(source_guid)
            except Exception:
                pass

        # Ensure Active layer exists
        if not rs.IsLayer("Active"):
            rs.AddLayer("Active", [0, 255, 0])

        # Move backup to Active layer and restore its original name
        rs.ObjectLayer(target_backup, "Active")
        rs.ObjectName(target_backup, source_name)

        # Clean up archive metadata from the restored object
        rs.SetUserText(target_backup, "archive_source_id")
        rs.SetUserText(target_backup, "archive_source_name")
        rs.SetUserText(target_backup, "archive_timestamp")
        rs.SetUserText(target_backup, "archive_version")

        rs.Redraw()
        result = json.dumps({
            "status": "success",
            "message": "Restored '{0}' (version {1}) to Active layer".format(source_name, version),
            "restored_id": str(target_backup),
        })
'''
        try:
            response = run_code(code)
            if response.get("status") == "error":
                return "Error: {}".format(response.get("message", "Unknown"))
            return str(response.get("result", "Restore failed"))
        except Exception as e:
            return "Error restoring: {}".format(e)

    # ── execute_rhino_code ────────────────────────────────────────────────

    def execute_rhino_code(self, ctx: Context, code: str) -> str:
        """Execute arbitrary Python code inside Rhino 8.

        The code runs in Rhino's embedded Python with full access to
        rhinoscriptsyntax (rs), RhinoCommon (Rhino.Geometry), and
        scriptcontext (sc).

        IMPORTANT:
        - Always call add_object_metadata(obj_id, name, description)
          after creating objects.
        - Set ``result = <value>`` to return data to the caller.
        - Prefer dedicated tools (create_box, loft_curves, …) over
          raw code execution when possible.
        """
        from .helpers import CODE_PREAMBLE

        try:
            full_code = CODE_PREAMBLE + "\n" + code
            conn = get_rhino_connection()
            response = conn.send_command("execute_code", {"code": full_code})

            if response.get("status") == "error":
                return "Error: {}".format(response.get("message", "Unknown"))
            return str(response.get("result", "Code executed successfully"))
        except Exception as e:
            return "Error executing code: {}".format(e)
