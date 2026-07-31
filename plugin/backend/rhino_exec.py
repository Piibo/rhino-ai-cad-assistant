"""Execute arbitrary Python code inside Rhino's UI thread.

Used by ``agent.execute_rhino_code`` as the tool executor. Wraps user code
in a preamble that exposes ``rs``/``rg``/``sc``/``math`` and an
``archive_object(id)`` helper (matches the rhaino server's ``CODE_PREAMBLE``).

The code is dispatched via ``Rhino.RhinoApp.InvokeOnUiThread`` because all
document mutations must run on the UI thread. A ``threading.Event`` syncs
the result back to the caller thread.

``run_code(source)`` returns a string with either the serialised ``result``
variable, captured stdout, or the exception traceback. The agent then
wraps it into a ``tool_result`` block.
"""

from __future__ import annotations

import ast
import io
import json
import logging
import threading
import textwrap
import traceback
from contextlib import redirect_stdout
from typing import Any

try:  # Rhino-only imports
    import Rhino  # type: ignore
    import System  # type: ignore

    RHINO_AVAILABLE = True
except Exception:  # pragma: no cover — only hits when running outside Rhino
    Rhino = None  # type: ignore
    System = None  # type: ignore
    RHINO_AVAILABLE = False

# Shared undo/action-history source — concatenated into CODE_PREAMBLE
# below so Plugin and MCP-server PREAMBLEs stay in lockstep. The plugin
# entry script (``start_plugin.py``) adds rhaino/ to sys.path so this
# import resolves; outside Rhino's CPython this module is never reached.
from shared.action_history_preamble import ACTION_HISTORY_PREAMBLE

logger = logging.getLogger("FurniturePlugin.RhinoExec")

CODE_PREAMBLE = """
import math
import json
import time
import rhinoscriptsyntax as rs
import Rhino
import Rhino.Geometry as rg
import scriptcontext as sc
import System
from datetime import datetime

def get_obj(id_str):
    \"\"\"Helps the AI retrieve objects by string ID securely by converting to Guid first.\"\"\"
    return sc.doc.Objects.FindId(System.Guid(str(id_str)))

def begin_native_undo(description="tool"):
    # NOTE: doc.UndoActive / doc.RedoActive are PROPERTIES, not methods, so
    # calling them raises TypeError and this function returns None (no-op) in
    # practice. That is intentional and must stay that way: execute_rhino_code
    # is undone via the plugin-backup path (begin/finish_scripted_action).
    # Enabling a real native undo record here would double-undo with that path.
    try:
        doc = sc.doc
        if doc is None:
            return None
        if doc.UndoActive() or doc.RedoActive():
            return None
        try:
            return Rhino.RhinoDocUndoRecord(doc, description)
        except Exception:
            pass
        try:
            serial = doc.BeginUndoRecord(description)
            if serial:
                return ("serial", serial)
        except Exception:
            pass
    except Exception:
        pass
    return None

def finish_native_undo(handle):
    if handle is None:
        return
    try:
        if hasattr(handle, "Dispose"):
            handle.Dispose()
            return
    except Exception:
        pass
    try:
        if isinstance(handle, tuple) and len(handle) == 2 and handle[0] == "serial":
            serial = handle[1]
            if serial:
                sc.doc.EndUndoRecord(serial)
    except Exception:
        pass

def _ensure_layer(name, color_rgb=None, visible=True):
    idx = sc.doc.Layers.FindByFullPath(name, -1)
    if idx < 0:
        parent = rs.AddLayer(name, color=color_rgb, visible=visible)
        idx = sc.doc.Layers.FindByFullPath(parent, -1)
    return idx
""" + ACTION_HISTORY_PREAMBLE + """
def archive_object(obj_id):
    \"\"\"Backup obj to hidden Archive layer before destructive edits.\"\"\"
    try:
        if isinstance(obj_id, str):
            obj_id = System.Guid(obj_id)
        _ensure_layer("Archive", (120, 120, 120), visible=False)
        version = 1
        source_str = str(obj_id)
        archive_objs = rs.ObjectsByLayer("Archive")
        if archive_objs:
            for oid in archive_objs:
                if rs.GetUserText(oid, "archive_source_id") == source_str:
                    try:
                        existing = int(rs.GetUserText(oid, "archive_version") or "0")
                        if existing >= version:
                            version = existing + 1
                    except Exception:
                        pass
        copy_id = rs.CopyObject(obj_id)
        if copy_id:
            rs.ObjectLayer(copy_id, "Archive")
            name = rs.ObjectName(obj_id) or "Unnamed"
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            rs.ObjectName(copy_id, "{0}_v{1}".format(name, version))
            rs.SetUserText(copy_id, "archive_source_id", source_str)
            rs.SetUserText(copy_id, "archive_source_name", name)
            rs.SetUserText(copy_id, "archive_timestamp", timestamp)
            rs.SetUserText(copy_id, "archive_version", str(version))
            _record_backup(obj_id, copy_id, name)
        return copy_id
    except Exception as _arch_err:
        # Backup fehlgeschlagen (z.B. CopyObject scheitert bei Speicherdruck):
        # NICHT lautlos verschlucken. In die aktuelle Action vermerken, damit
        # undo + Auswertung ein unvollstaendiges Backup erkennen, plus eine
        # Warnung in die (von run_code erfasste) Ausgabe schreiben.
        try:
            _failed = _get_action_by_id(sc.sticky.get(_ACTION_CURRENT_KEY))
            if _failed is not None:
                _failed.setdefault("backup_failures", []).append(str(obj_id))
        except Exception:
            pass
        print("[archive] WARN: Backup fehlgeschlagen fuer {0}: {1}".format(obj_id, _arch_err))
        return None

def clear_editable_recipe(obj_id):
    \"\"\"Strip primitive-recipe user-text from an object after topology change.

    Tools like ``create_hole``, ``create_slot`` and ``round_edges_by_rule``
    replace a primitive Brep with one that no longer matches its original
    recipe (a box with a hole isn't a box anymore). Leaving the
    ``editable_recipe`` / ``object_class`` user-text in place would let the
    structure-slider rebuild the object from the OLD recipe, silently
    destroying the new feature. Clearing the recipe tags makes the slider
    inert until the object is re-classified.
    \"\"\"
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

def update_box_recipe_from_live(obj_id):
    # Re-sync a primitive_box recipe (width/depth/height/origin) from the live
    # geometry. No-op for non-box objects. Keeps slider scaling (scale_axis)
    # from drifting away from the original create dimensions, so a later
    # rebuild or radius change uses the CURRENT size, not the stale one.
    try:
        if isinstance(obj_id, str):
            obj_id = System.Guid(obj_id)
        if (rs.GetUserText(obj_id, "object_class") or "") != "primitive_box":
            return
        obj = sc.doc.Objects.Find(obj_id)
        geom = obj.Geometry if obj else None
        if geom is None:
            return
        bbox = geom.GetBoundingBox(True)
        width = float(bbox.Max.X - bbox.Min.X)
        depth = float(bbox.Max.Y - bbox.Min.Y)
        height = float(bbox.Max.Z - bbox.Min.Z)
        rs.SetUserText(obj_id, "editable_recipe", json.dumps({
            "x": float(bbox.Min.X),
            "y": float(bbox.Min.Y),
            "z": float(bbox.Min.Z),
            "width": width,
            "depth": depth,
            "height": height,
        }))
        rs.SetUserText(
            obj_id,
            "description",
            "Box {0}x{1}x{2}".format(width, depth, height),
        )
    except Exception:
        pass

def add_object_metadata(obj_id, name=None, description=None):
    \"\"\"Tag a freshly-created object with name + bbox + target layer.

    Mirrors rhino_mcp/helpers.py CODE_PREAMBLE so the shared code
    templates in shared/code_templates.py work identically when called
    from the plugin (here) or from the MCP server.

    If ``sc.sticky['__active_variant_layer__']`` is set, new geometry lands
    on that variant layer instead of ``Active`` so parallel alternatives stay
    isolated from each other.
    \"\"\"
    try:
        if isinstance(obj_id, str):
            obj_id = System.Guid(obj_id)
        target_layer = sc.sticky.get("__active_variant_layer__") or "Active"
        if not rs.IsLayer(target_layer):
            rs.AddLayer(target_layer, [0, 0, 0])
        elif target_layer == "Active":
            rs.LayerColor("Active", [0, 0, 0])
        if not rs.IsLayer("Archive"):
            rs.AddLayer("Archive", [128, 128, 128])
            rs.LayerVisible("Archive", False)
        # Archive any existing Active-layer object that has the same name
        # — keeps the Active layer at one "version" per logical thing.
        if name:
            layer_objs = rs.ObjectsByLayer(target_layer)
            if layer_objs:
                for oid in layer_objs:
                    if rs.ObjectName(oid) == name:
                        archive_object(oid)
                        rs.DeleteObject(oid)
        short_id = datetime.now().strftime("%d%H%M%S")
        bbox = rs.BoundingBox(obj_id)
        bbox_data = [[p.X, p.Y, p.Z] for p in bbox] if bbox else []
        obj = sc.doc.Objects.Find(obj_id)
        obj_type = obj.Geometry.GetType().Name if obj else "Unknown"
        rs.ObjectLayer(obj_id, target_layer)
        metadata = {
            "short_id": short_id,
            "created_at": time.time(),
            "layer": target_layer,
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
"""


def run_code(
    source: str,
    timeout: float = 30.0,
    native_undo_description: str | None = None,
) -> str:
    """Run ``source`` on Rhino's UI thread and return a string summary.

    Returns:
        - ``str(result)`` if the user assigned to ``result``
        - captured stdout otherwise (empty becomes "OK")
        - traceback on exception (prefixed with "Fehler:")
    """
    if not RHINO_AVAILABLE:
        return "Rhino nicht verfügbar."

    done = threading.Event()
    output: dict[str, Any] = {"text": "", "error": None}
    # Set when the caller gives up after the timeout. The UI-thread action
    # checks it before executing: if a modal pick (or any long op) held the UI
    # thread until run_code already returned the timeout error, the queued
    # action must NOT still run the mutation afterwards (which would land a
    # destructive edit the model was told had failed — possibly twice if it
    # retried).
    cancelled = {"v": False}

    wrapped_source = source
    if native_undo_description:
        wrapped_source = (
            "_native_undo = begin_native_undo({!r})\n"
            "try:\n"
            "{}\n"
            "finally:\n"
            "    finish_native_undo(_native_undo)\n"
        ).format(
            native_undo_description,
            textwrap.indent(source, "    "),
        )

    full_code = CODE_PREAMBLE + "\n" + wrapped_source

    def _do() -> None:
        # If the caller already timed out (e.g. this action was queued behind a
        # modal pick that held the UI thread), do not run the mutation now.
        if cancelled["v"]:
            return
        buf = io.StringIO()
        local_ns: dict[str, Any] = {}
        try:
            with redirect_stdout(buf):
                exec(full_code, local_ns, local_ns)
            if "result" in local_ns:
                output["text"] = _stringify(local_ns["result"])
            else:
                printed = buf.getvalue().strip()
                output["text"] = printed if printed else "OK"
        except Exception as e:
            output["error"] = (
                f"Fehler: {e}\n{traceback.format_exc()}"
            )
        finally:
            done.set()

    Rhino.RhinoApp.InvokeOnUiThread(System.Action(_do))

    if not done.wait(timeout=timeout):
        # Tell a not-yet-started queued action to skip its mutation. (An action
        # already mid-exec can't be interrupted safely — that case keeps the
        # documented limitation, but the common "queued behind a modal pick"
        # case no longer double-applies.)
        cancelled["v"] = True
        return f"Fehler: Ausführung nach {timeout}s abgebrochen."

    if output["error"] is not None:
        return str(output["error"])
    return str(output["text"])


def _stringify(value: Any) -> str:
    """Make ``result`` safe for text transport (truncated repr for big data)."""
    try:
        text = repr(value)
    except Exception as e:
        return f"<unrepresentable result: {e}>"
    if len(text) > 4000:
        return text[:4000] + f"… [truncated, total {len(text)} chars]"
    return text


def decode_run_code_result(raw: Any) -> Any:
    """Invert :func:`_stringify` for callers that need the structured result.

    ``run_code`` transports a tool's ``result`` as ``repr(result)`` (see
    :func:`_stringify`). When that ``result`` was itself a ``json.dumps(...)``
    string, a naive ``json.loads`` on the transported text fails on the repr
    hull's single quotes — the recurring bug class behind the model.3dm /
    model_states parse failures (06.07.2026). Strip the repr hull via
    ``ast.literal_eval`` first, then JSON-decode the inner string.

    Returns ``None`` for empty or ``Error:``/``Fehler:``-prefixed payloads,
    the decoded object when the text is repr- or JSON-shaped, and the raw
    text when it is neither. Non-string input is returned unchanged so the
    helper is safe to call on already-decoded values.
    """
    if not isinstance(raw, str):
        return raw
    text = raw.strip()
    if not text or text.startswith("Error:") or text.startswith("Fehler:"):
        return None
    try:
        value = ast.literal_eval(text)
    except Exception:
        value = text
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return value
    return value
