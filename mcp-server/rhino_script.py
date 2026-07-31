# -*- coding: utf-8 -*-
"""
RhinoMCP - Rhino-side socket server.
Run inside Rhino 8 via:  _-RunPythonScript "path/to/rhino_script.py"

Handles commands from the MCP server over TCP on localhost:9876.
Adapted from SerjoschDuering/rhino-mcp for Rhino 8 (CPython 3 + IronPython 2.7).
"""

import socket
import threading
import json
import time
import sys
import os
import platform
import traceback
import base64

import System
from System import Guid
import Rhino
import Rhino.Geometry as rg
import scriptcontext as sc
import rhinoscriptsyntax as rs

from System.Drawing import Bitmap
from System.Drawing.Imaging import ImageFormat
from System.IO import MemoryStream
from datetime import datetime

# ── Configuration ─────────────────────────────────────────────────────────

HOST = "localhost"
PORT = 9876
ANNOTATION_LAYER = "MCP_Annotations"

VALID_METADATA_FIELDS = {
    "required": ["id", "name", "type", "layer"],
    "optional": ["short_id", "created_at", "bbox", "description", "user_text"],
}

# ── Logging ───────────────────────────────────────────────────────────────

def _log_dir():
    home = os.path.expanduser("~")
    if platform.system() == "Windows":
        return os.path.join(home, "AppData", "Local", "RhinoMCP", "logs")
    elif platform.system() == "Darwin":
        return os.path.join(home, "Library", "Application Support", "RhinoMCP", "logs")
    return os.path.join(home, ".rhino_mcp", "logs")


def log(message):
    Rhino.RhinoApp.WriteLine(str(message))
    try:
        d = _log_dir()
        if not os.path.exists(d):
            os.makedirs(d)
        path = os.path.join(d, "rhino_mcp.log")
        if not os.path.exists(path):
            with open(path, "w") as f:
                f.write("=== RhinoMCP Log ===\n")
                f.write("Platform: {0}\n".format(platform.system()))
                f.write("Python: {0}\n".format(sys.version))
                f.write("Rhino: {0}\n".format(Rhino.RhinoApp.Version))
                f.write("====================\n\n")
        with open(path, "a") as f:
            f.write("[{0}] {1}\n".format(time.strftime("%Y-%m-%d %H:%M:%S"), message))
    except Exception:
        pass

# ── Server ────────────────────────────────────────────────────────────────

class RhinoMCPServer:
    def __init__(self, host=HOST, port=PORT):
        self.host = host
        self.port = port
        self.running = False
        self.socket = None
        self.server_thread = None

    def start(self):
        if self.running:
            log("Server already running")
            return
        self.running = True
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.socket.bind((self.host, self.port))
            self.socket.listen(1)
            self.server_thread = threading.Thread(target=self._loop)
            self.server_thread.daemon = True
            self.server_thread.start()
            log("RhinoMCP server started on {0}:{1}".format(self.host, self.port))
        except Exception as e:
            log("Failed to start: {0}".format(e))
            self.stop()

    def stop(self):
        self.running = False
        if self.socket:
            try:
                self.socket.close()
            except Exception:
                pass
            self.socket = None
        if self.server_thread and self.server_thread.is_alive():
            try:
                self.server_thread.join(timeout=1.0)
            except Exception:
                pass
            self.server_thread = None
        log("RhinoMCP server stopped")

    def _loop(self):
        while self.running:
            try:
                client, addr = self.socket.accept()
                log("Client connected from {0}:{1}".format(addr[0], addr[1]))
                t = threading.Thread(target=self._handle, args=(client,))
                t.daemon = True
                t.start()
            except Exception as e:
                if self.running:
                    log("Accept error: {0}".format(e))
                    time.sleep(0.5)

    def _handle(self, client):
        BUF = 14485760
        try:
            client.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, BUF)
            client.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, BUF)
            while self.running:
                data = client.recv(BUF)
                if not data:
                    log("Client disconnected")
                    break
                try:
                    command = json.loads(data.decode("utf-8"))
                except (ValueError, UnicodeDecodeError) as e:
                    log("Bad JSON: {0}".format(e))
                    self._send(client, {"status": "error", "message": "Invalid JSON"})
                    continue

                log("Command: {0}".format(command.get("type", "?")))

                # Execute on UI thread for safety
                def _run(cmd=command, cl=client):
                    try:
                        resp = self._dispatch(cmd)
                        self._send(cl, resp)
                    except Exception as e:
                        log("Dispatch error: {0}".format(e))
                        traceback.print_exc()
                        self._send(cl, {"status": "error", "message": str(e)})

                # Rhino 8 CPython: use InvokeOnUiThread if available, else Idle event
                if hasattr(Rhino.RhinoApp, "InvokeOnUiThread"):
                    Rhino.RhinoApp.InvokeOnUiThread(System.Action(_run))
                else:
                    # Fallback for IronPython / older Rhino
                    def _idle(sender, e, fn=_run):
                        fn()
                        Rhino.RhinoApp.Idle -= _idle
                    Rhino.RhinoApp.Idle += _idle

        except Exception as e:
            log("Handle error: {0}".format(e))
            traceback.print_exc()
        finally:
            try:
                client.close()
            except Exception:
                pass

    def _send(self, client, response):
        try:
            data = json.dumps(response).encode("utf-8")
            client.sendall(data)
        except Exception as e:
            log("Send error: {0}".format(e))

    # ── Command dispatch ──────────────────────────────────────────────────

    def _dispatch(self, command):
        cmd_type = command.get("type", "")
        params = command.get("params", {})
        # Ensure we always operate on the Rhino document, not ghdoc
        sc.doc = Rhino.RhinoDoc.ActiveDoc
        try:
            if cmd_type == "get_scene_info":
                return self._get_scene_info(params)
            elif cmd_type == "get_layers":
                return self._get_layers()
            elif cmd_type == "execute_code":
                return self._execute_code(params)
            elif cmd_type == "get_objects_with_metadata":
                return self._get_objects_with_metadata(params)
            elif cmd_type == "capture_viewport":
                return self._capture_viewport(params)
            elif cmd_type == "add_metadata":
                return self._add_metadata(
                    params.get("object_id"),
                    params.get("name"),
                    params.get("description"),
                )
            else:
                return {"status": "error", "message": "Unknown command: {0}".format(cmd_type)}
        except Exception as e:
            log("Command error [{0}]: {1}".format(cmd_type, e))
            traceback.print_exc()
            return {"status": "error", "message": str(e)}

    # ── get_scene_info ────────────────────────────────────────────────────

    def _get_scene_info(self, params=None):
        doc = sc.doc
        if not doc:
            return {"status": "error", "message": "No active document"}
        layers_info = []
        for layer in doc.Layers:
            objs = [o for o in doc.Objects if o.Attributes.LayerIndex == layer.Index]
            samples = []
            for obj in objs[:5]:
                try:
                    user_strings = {}
                    us = obj.Attributes.GetUserStrings()
                    if us:
                        for k in us:
                            user_strings[k] = obj.Attributes.GetUserString(k)
                    samples.append({
                        "id": str(obj.Id),
                        "name": obj.Name or "Unnamed",
                        "type": obj.Geometry.GetType().Name if obj.Geometry else "Unknown",
                        "metadata": user_strings,
                    })
                except Exception:
                    continue
            layers_info.append({
                "full_path": layer.FullPath,
                "object_count": len(objs),
                "is_visible": layer.IsVisible,
                "is_locked": layer.IsLocked,
                "example_objects": samples,
            })
        return {"status": "success", "layers": layers_info}

    # ── get_layers ────────────────────────────────────────────────────────

    def _get_layers(self):
        return {
            "status": "success",
            "layers": [
                {
                    "id": l.Index,
                    "name": l.Name,
                    "object_count": l.ObjectCount,
                    "is_visible": l.IsVisible,
                    "is_locked": l.IsLocked,
                }
                for l in sc.doc.Layers
            ],
        }

    # ── execute_code ──────────────────────────────────────────────────────

    def _execute_code(self, params):
        code = params.get("code", "")
        if not code:
            return {"status": "error", "message": "No code provided"}
        log("Executing code ({0} chars)".format(len(code)))
        exec_globals = dict(globals())
        try:
            exec(code, exec_globals)
            result = exec_globals.get("result", "Code executed successfully")
            return {
                "status": "success",
                "result": str(result),
                "variables": {},
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    # ── add_metadata ──────────────────────────────────────────────────────

    def _add_metadata(self, obj_id, name=None, description=None):
        try:
            short_id = datetime.now().strftime("%d%H%M%S")
            bbox = rs.BoundingBox(obj_id)
            bbox_data = [[p.X, p.Y, p.Z] for p in bbox] if bbox else []
            obj = sc.doc.Objects.Find(obj_id)
            obj_type = obj.Geometry.GetType().Name if obj else "Unknown"
            meta = {
                "short_id": short_id,
                "created_at": time.time(),
                "layer": rs.ObjectLayer(obj_id),
                "type": obj_type,
                "bbox": bbox_data,
            }
            if name:
                rs.ObjectName(obj_id, name)
                meta["name"] = name
            else:
                auto = "{0}_{1}".format(obj_type, short_id)
                rs.ObjectName(obj_id, auto)
                meta["name"] = auto
            if description:
                meta["description"] = description
            store = meta.copy()
            store["bbox"] = json.dumps(bbox_data)
            for k, v in store.items():
                rs.SetUserText(obj_id, k, str(v))
            return {"status": "success"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    # ── get_objects_with_metadata ──────────────────────────────────────────

    def _get_objects_with_metadata(self, params):
        import re as _re

        filters = params.get("filters", {})
        metadata_fields = params.get("metadata_fields")
        layer_f = filters.get("layer")
        name_f = filters.get("name")
        id_f = filters.get("short_id")

        all_fields = VALID_METADATA_FIELDS["required"] + VALID_METADATA_FIELDS["optional"]
        if metadata_fields:
            bad = [f for f in metadata_fields if f not in all_fields]
            if bad:
                return {
                    "status": "error",
                    "message": "Invalid fields: " + ", ".join(bad),
                    "available_fields": all_fields,
                }

        objects = []
        for obj in sc.doc.Objects:
            oid = obj.Id
            if layer_f:
                pat = "^" + layer_f.replace("*", ".*") + "$"
                if not _re.match(pat, rs.ObjectLayer(oid), _re.IGNORECASE):
                    continue
            if name_f:
                pat = "^" + name_f.replace("*", ".*") + "$"
                if not _re.match(pat, obj.Name or "", _re.IGNORECASE):
                    continue
            if id_f:
                if (rs.GetUserText(oid, "short_id") or "") != id_f:
                    continue

            data = {
                "id": str(oid),
                "name": obj.Name or "Unnamed",
                "type": obj.Geometry.GetType().Name,
                "layer": rs.ObjectLayer(oid),
            }
            stored = {}
            for k in (rs.GetUserText(oid) or []):
                v = rs.GetUserText(oid, k)
                if k == "bbox":
                    try:
                        v = json.loads(v)
                    except Exception:
                        v = []
                elif k == "created_at":
                    try:
                        v = float(v)
                    except Exception:
                        v = 0
                stored[k] = v

            if metadata_fields:
                meta = {k: stored[k] for k in metadata_fields if k in stored}
            else:
                meta = {k: v for k, v in stored.items() if k not in VALID_METADATA_FIELDS["required"]}
            if meta:
                data["metadata"] = meta
            objects.append(data)

        return {"status": "success", "count": len(objects), "objects": objects, "available_fields": all_fields}

    # ── capture_viewport ──────────────────────────────────────────────────

    def _capture_viewport(self, params):
        layer_name = params.get("layer")
        show_annot = params.get("show_annotations", True)
        max_size = params.get("max_size", 800)
        orig_layer = rs.CurrentLayer()
        dots = []

        if show_annot:
            if not rs.IsLayer(ANNOTATION_LAYER):
                rs.AddLayer(ANNOTATION_LAYER, color=(255, 0, 0))
            rs.CurrentLayer(ANNOTATION_LAYER)
            for obj in sc.doc.Objects:
                if layer_name and rs.ObjectLayer(obj.Id) != layer_name:
                    continue
                bbox = rs.BoundingBox(obj.Id)
                if bbox:
                    sid = rs.GetUserText(obj.Id, "short_id")
                    if not sid:
                        sid = datetime.now().strftime("%d%H%M%S")
                        rs.SetUserText(obj.Id, "short_id", sid)
                    nm = rs.ObjectName(obj.Id) or "Unnamed"
                    dot = rs.AddTextDot("{0}\n{1}".format(nm, sid), bbox[1])
                    rs.TextDotHeight(dot, 8)
                    dots.append(dot)

        try:
            view = sc.doc.Views.ActiveView
            bmp = view.CaptureToBitmap()
            w, h = bmp.Width, bmp.Height
            if w > h:
                nw, nh = max_size, int(h * max_size / w)
            else:
                nh, nw = max_size, int(w * max_size / h)
            resized = Bitmap(bmp, nw, nh)
            ms = MemoryStream()
            resized.Save(ms, ImageFormat.Jpeg)
            img_data = base64.b64encode(bytes(bytearray(ms.ToArray()))).decode("utf-8")
            bmp.Dispose()
            resized.Dispose()
            ms.Dispose()
        finally:
            if dots:
                rs.DeleteObjects(dots)
            rs.CurrentLayer(orig_layer)

        return {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": img_data},
        }


# ── Auto-start ────────────────────────────────────────────────────────────

server = RhinoMCPServer(HOST, PORT)
server.start()

log("============================================================")
log("RhinoMCP Server (merged) – Rhino 8")
log("Python: {0}".format(sys.version.split()[0]))
log("Port: {0}".format(PORT))
log("============================================================")
