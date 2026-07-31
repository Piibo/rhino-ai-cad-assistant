"""Launch Grasshopper and load grasshopper_mcp_client.gh on Rhino's UI thread.

Extracted from ``server.grasshopper_connect`` so the same launch path can be
reused by two callers without a user round-trip:

* the ``POST /api/grasshopper/connect`` endpoint (the header "GH dot" button),
* the bridge auto-connect path (``grasshopper_bridge``), which fires this
  itself when a GH tool call hits a dead :9998 server, then polls for the HTTP
  server to come up before retrying the original call.

This module imports ONLY ``rhino_exec`` (never ``server`` or
``grasshopper_bridge``) so it can be imported from either side without a
circular import. The generated Rhino-Python string is IronPython-2.7 safe
(no f-strings/walrus; ``.format()`` only) and defensive throughout — a hard
failure here must not take Rhino down.
"""

from __future__ import annotations

import os

# Mirror server._GH_CLIENT_PATH resolution: this module sits at
# rhaino/plugin/backend/, the GH client lives at rhaino/grasshopper/.
_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
_PLUGIN_DIR = os.path.dirname(_BACKEND_DIR)
_RHINO_MCP_ROOT = os.path.dirname(_PLUGIN_DIR)
GH_CLIENT_PATH = os.path.join(
    _RHINO_MCP_ROOT, "grasshopper", "grasshopper_mcp_client.gh"
)


def _build_launch_code(gh_client_path: str) -> str:
    """Build the IronPython source that opens GH and loads the client doc.

    GH 2.x (Rhino 8) rewrote ``GH_DocumentEditor``. Methods like
    ``OpenDocument``, ``LoadEditor`` and the ``Document`` property are gone.
    The displayed document now lives on ``Instances.ActiveCanvas`` —
    ``canvas.Document = doc`` is the supported way to switch the view,
    ``AddDocument`` only registers the doc with the server.

    The launch also flips any ``GH_BooleanToggle`` inside the loaded doc back
    to ``True``. Rationale: "Connect" should mean "make GH work" from the
    user's POV. If they previously toggled the server off and closed the GH
    window (which only hides it — the doc stays loaded with its in-memory
    False state), launching again would otherwise just re-show the window
    without bringing the server back. The toggle in
    ``grasshopper_mcp_client.gh`` sits inside a cluster, hence the recursion.
    """
    return '''
import os
import time
import Rhino

target_path = r"{GH_CLIENT_PATH}"
target_norm = os.path.normcase(os.path.abspath(target_path))

steps = []

def _path_match(fp):
    if not fp:
        return False
    try:
        return os.path.normcase(os.path.abspath(fp)) == target_norm
    except Exception:
        return False

def _cluster_subdoc(cluster):
    """Return the document inside a GH_Cluster, trying a few API shapes."""
    for attr in ("Document", "DocumentMain", "OwnerDoc"):
        if not hasattr(cluster, attr):
            continue
        val = getattr(cluster, attr)
        if callable(val):
            for args in ((0,), ()):
                try:
                    sub = val(*args)
                    if sub is not None and hasattr(sub, "Objects"):
                        return sub
                except Exception:
                    continue
        else:
            if val is not None and hasattr(val, "Objects"):
                return val
    return None

def _force_toggles_true(doc, depth=0):
    """Set every GH_BooleanToggle (incl. inside clusters) to True. Returns
    the number flipped. We touch only False->True transitions so toggles
    already at True don't generate redundant ExpireSolution churn."""
    if doc is None or depth > 4:
        return 0
    flipped = 0
    try:
        objs = list(doc.Objects)
    except Exception:
        return 0
    for obj in objs:
        try:
            type_name = obj.GetType().Name
        except Exception:
            continue
        if type_name == "GH_BooleanToggle":
            try:
                if not obj.Value:
                    obj.Value = True
                    obj.ExpireSolution(False)
                    flipped += 1
            except Exception:
                pass
        elif type_name == "GH_Cluster":
            sub = _cluster_subdoc(obj)
            flipped += _force_toggles_true(sub, depth + 1)
    return flipped

# 1. Open the GH window (also loads the plugin assembly if needed).
ok = Rhino.RhinoApp.RunScript("!_-Grasshopper _Window _Show _Enter", False)
steps.append("RunScript=" + str(ok))

try:
    import clr
    clr.AddReference("Grasshopper")
    import Grasshopper as GH

    # 2. Wait for the canvas singleton to come up. Cold-start can take
    #    half a second or so before Instances.ActiveCanvas is non-None.
    for _ in range(30):
        if GH.Instances.ActiveCanvas is not None:
            break
        time.sleep(0.1)

    canvas = GH.Instances.ActiveCanvas
    server = GH.Instances.DocumentServer
    steps.append("canvas=" + ("present" if canvas else "None"))
    steps.append("server.DocumentCount=" + str(server.DocumentCount))

    # 3. Already-loaded check.
    already_doc = None
    for i in range(server.DocumentCount):
        d = server[i]
        if _path_match(getattr(d, "FilePath", None)):
            already_doc = d
            break

    target_doc = None
    if already_doc is not None:
        if canvas is not None:
            shown = getattr(canvas, "Document", None)
            if shown is None or shown.DocumentID != already_doc.DocumentID:
                try:
                    canvas.Document = already_doc
                    steps.append("canvas.Document=set(already)")
                except Exception as e:
                    steps.append("canvas.Document(already)=" + type(e).__name__)
        target_doc = already_doc
        outcome = "already_loaded"
    else:
        # 4. Load the .gh file from disk into a fresh GH_Document.
        io = GH.Kernel.GH_DocumentIO()
        opened = io.Open(target_path)
        steps.append("GH_DocumentIO.Open=" + str(opened))
        if opened:
            added = False
            for variant in ("2-arg", "1-arg"):
                try:
                    if variant == "2-arg":
                        server.AddDocument(io.Document, True)
                    else:
                        server.AddDocument(io.Document)
                    steps.append("AddDocument(" + variant + ")=ok")
                    added = True
                    break
                except Exception as e:
                    steps.append("AddDocument(" + variant + ")=" + type(e).__name__)

            if added and canvas is not None:
                try:
                    canvas.Document = io.Document
                    steps.append("canvas.Document=set")
                except Exception as e:
                    steps.append("canvas.Document=" + type(e).__name__ + ":" + str(e)[:120])

            if added:
                target_doc = io.Document
                outcome = "loaded"
            else:
                outcome = "addDocument_failed"
        else:
            outcome = "open_failed"

    # 4b. Minimize the GH window once it's loaded. The canvas needs to be up
    #     for the layout to settle, so we do this here (after load) rather
    #     than fighting the just-shown window. We MINIMIZE, not hide — hiding
    #     drops the doc's in-memory state from the user's reach; minimizing
    #     keeps GH running with the server alive but out of the way.
    if outcome in ("loaded", "already_loaded"):
        try:
            editor = GH.Instances.DocumentEditor
            if editor is not None:
                import clr as _clr
                _clr.AddReference("System.Windows.Forms")
                from System.Windows.Forms import FormWindowState
                editor.WindowState = FormWindowState.Minimized
                steps.append("editor.Minimized")
                Rhino.RhinoApp.SetFocusToMainWindow()
                steps.append("focus=rhino")
            else:
                steps.append("editor=None")
        except Exception as e:
            steps.append("minimize=" + type(e).__name__)

    # 5. Make sure the server-bootstrap toggle is on, enable global
    #    GH preview (so geometry components show up in the Rhino viewport
    #    without the LLM having to bake), then recompute.
    if target_doc is not None:
        flipped = _force_toggles_true(target_doc)
        steps.append("toggles_flipped=" + str(flipped))
        # Try several preview-enable APIs — exact name varies across GH
        # builds, all are no-ops if the property doesn't exist.
        try:
            if hasattr(target_doc, "PreviewMode"):
                try:
                    pm_enum = GH.Kernel.GH_PreviewMode
                    if hasattr(pm_enum, "Shaded"):
                        target_doc.PreviewMode = pm_enum.Shaded
                    elif hasattr(pm_enum, "Wireframe"):
                        target_doc.PreviewMode = pm_enum.Wireframe
                    steps.append("doc.PreviewMode=set")
                except Exception as e:
                    steps.append("doc.PreviewMode=" + type(e).__name__)
            # Preview filter: disable "Selected Only Preview" so geometry built
            # by the assistant remains visible in the Rhino viewport even when
            # the designer clicks elsewhere on the canvas.
            # GH_PreviewFilter enum has two members: None (0) = all objects
            # drawn, Selected (1) = selected-only. We want None/0.
            # Note: Python's None keyword shadows the enum member, so we use
            # getattr with the string "None" and fall back to int 0.
            try:
                if hasattr(target_doc, "PreviewFilter"):
                    pf_enum = getattr(GH.Kernel, "GH_PreviewFilter", None)
                    set_ok = False
                    if pf_enum is not None:
                        none_val = getattr(pf_enum, "None", None)
                        if none_val is not None:
                            target_doc.PreviewFilter = none_val
                            steps.append("doc.PreviewFilter=GH_PreviewFilter.None")
                            set_ok = True
                        if not set_ok:
                            try:
                                target_doc.PreviewFilter = pf_enum(0)
                                steps.append("doc.PreviewFilter=GH_PreviewFilter(0)")
                                set_ok = True
                            except Exception:
                                pass
                    if not set_ok:
                        target_doc.PreviewFilter = 0
                        steps.append("doc.PreviewFilter=0(int)")
                else:
                    steps.append("doc.PreviewFilter=property_missing")
            except Exception as e:
                steps.append("doc.PreviewFilter=" + type(e).__name__)
            if canvas is not None and hasattr(canvas, "DocumentPreviewMode"):
                try:
                    canvas.DocumentPreviewMode = 2  # 0=Off, 1=Wireframe, 2=Shaded
                    steps.append("canvas.PreviewMode=set")
                except Exception as e:
                    steps.append("canvas.PreviewMode=" + type(e).__name__)
        except Exception as e:
            steps.append("preview-enable=" + type(e).__name__)
        try:
            target_doc.NewSolution(False)
            steps.append("NewSolution=ok")
        except Exception as e:
            steps.append("NewSolution=" + type(e).__name__)

    # 6. Verify the canvas actually points at our file.
    canvas_doc_path = None
    if canvas is not None:
        d_show = getattr(canvas, "Document", None)
        if d_show is not None:
            canvas_doc_path = getattr(d_show, "FilePath", None)
    steps.append("canvas.Document.FilePath=" + repr(canvas_doc_path))

    if target_doc is not None and _path_match(canvas_doc_path):
        result = outcome
    elif target_doc is not None:
        result = outcome + "_but_not_visible; steps=" + ", ".join(steps)
    else:
        result = outcome + "; steps=" + ", ".join(steps)
except Exception as exc:
    import traceback
    result = ("exception: " + str(exc) + " | steps=" + ", ".join(steps)
              + " | tb=" + traceback.format_exc()[:600])
'''.format(GH_CLIENT_PATH=gh_client_path)


def launch_grasshopper(timeout: float = 30.0) -> str:
    """Open GH + load the client doc on Rhino's UI thread; return the raw
    Rhino response string (``loaded`` / ``already_loaded`` / an error or
    ``..._but_not_visible; steps=...`` trace). Caller decides what to do with
    it; this function never raises for the GH-side cases (they come back as
    strings inside the response).
    """
    from .rhino_exec import run_code

    code = _build_launch_code(GH_CLIENT_PATH)
    return run_code(code, timeout=timeout)


__all__ = ["launch_grasshopper", "GH_CLIENT_PATH"]
