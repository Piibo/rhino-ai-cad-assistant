"""Hover-highlight overlay — non-mutating transient highlight of a reference.

The frontend renders inline reference tokens (component picks, point picks,
selections) inside a chat message. When the user hovers over such a token,
the frontend sends ``viewport.highlight_reference`` with the original
reference block; on hover-out it sends ``viewport.highlight_clear``. This
module turns that into a transient overlay in the Rhino viewport so the
designer can see *which* edge/face/vertex/object the token refers to.

Why a DisplayConduit and not selection
--------------------------------------
The hard constraint is **non-mutating**: we must NOT call
``rs.SelectObject`` / change the document selection. The live
``selection_watcher`` is subscribed to Rhino's selection events and would
broadcast a ``viewport.selection_changed`` badge update — and in study mode
that selection feeds the structure-context / logging path. A hover preview
must leave all of that untouched. A ``Rhino.Display.DisplayConduit`` draws
on top of the viewport every frame without touching the document, the
selection, or undo state, so it is the right tool here.

Resolution model
----------------
Unlike ``pick.py`` (which resolves sub-components from a live ``ObjRef`` and
its ``GeometryComponentIndex``), the highlight block arrives from the
frontend carrying ``object_id`` + ``component_type`` + ``component_index`` +
``geometry_class``. We therefore resolve geometry directly from the document
object by GUID (``doc.Objects.FindId``) and index into the Brep/SubD/Mesh.
The index→geometry accessors mirror ``pick._indexed_component_position`` and
the Brep edge/face handling in ``pick._component_info``.

Defensive by design: any failure to resolve a sub-component falls back to
highlighting the whole object (or a marker at ``point``); any failure at all
is logged and swallowed so it can never raise into the WS dispatch loop. Runs
on Rhino's UI thread (the caller marshals via ``InvokeOnUiThread``).

CPython 3 inside Rhino (RhinoCommon available); f-strings are fine here, but
no document mutation happens in this module at all.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Optional

logger = logging.getLogger("FurniturePlugin.Highlight")

try:  # Only available inside Rhino's Python context.
    import Rhino  # type: ignore
    import scriptcontext as sc  # type: ignore
    import System  # type: ignore
    from System.Drawing import Color  # type: ignore

    RHINO_AVAILABLE = True
except Exception as e:  # pragma: no cover — headless fallback
    Rhino = None  # type: ignore
    sc = None  # type: ignore
    System = None  # type: ignore
    Color = None  # type: ignore
    logger.info("RhinoCommon not available (%s) — highlight disabled", e)
    RHINO_AVAILABLE = False


# Highlight-Farbe = Brand-BLAU, durchgaengig (Nutzerwahl 25.06.2026): Hover-
# Highlight (Referenz-Tokens / Choice-Optionen / Gate-Ziele), persistente
# K/F/O-Markierung UND die Punkt-Marker — alles konsistent blau wie der
# K/F/O-Hover-Picker. (Orange wirkte als Hover-/Marker-Farbe zu negativ.)
_HV_R, _HV_G, _HV_B = 40, 102, 246  # brand-primary blue (hsl(222 92% 56%))
_HL_R, _HL_G, _HL_B = _HV_R, _HV_G, _HV_B  # Legacy-Alias -> jetzt ebenfalls blau
_EDGE_THICKNESS = 5  # px, screen-space — chunky enough to read as a highlight (Hover)
_PERSIST_EDGE_THICKNESS = 2  # px — duenne, persistente K/F/O-Markierung (kein Fill)
_POINT_RADIUS = 9  # px, screen-space — radius of the hollow ring around a point
_POINT_RING_THICKNESS = 2  # px, screen-space — ring outline thickness
_MARKER_RADIUS = 4  # px, screen-space — small filled dot at a picked point
_FACE_FILL_ALPHA = 70  # 0..255 — translucent so the model stays visible

# Brand-Orange (hsl(16 86% 53%) = --brand-orange) fuer die LOESCH-Vorschau: das
# Gate-Highlight einer Loeschung leuchtet orange (Warnung "wird entfernt") statt
# neutral blau. Nur delete-Ops (Nutzerwunsch 01.07.2026) — alle uebrigen Gate-
# und Hover-Highlights bleiben blau.
_DEL_R, _DEL_G, _DEL_B = 238, 87, 32  # brand-orange


# ---------------------------------------------------------------------------
# DisplayConduit — draws the staged highlight geometry every frame
# ---------------------------------------------------------------------------


def _make_conduit_class():
    """Build the conduit class lazily so the module imports cleanly outside
    Rhino (``Rhino.Display.DisplayConduit`` only exists inside Rhino)."""

    class _HighlightConduit(Rhino.Display.DisplayConduit):  # type: ignore
        """Transient overlay: curves (edges/silhouettes), points (vertices/
        markers), and shaded mesh faces. Holds only geometry copies — never
        document references — so it is safe across redraws and doc edits."""

        def __init__(self):
            super(_HighlightConduit, self).__init__()
            self.curves = []  # list[Rhino.Geometry.Curve]
            self.lines = []   # list[Rhino.Geometry.Line]
            self.points = []  # list[Rhino.Geometry.Point3d]
            self.face_meshes = []  # list[Rhino.Geometry.Mesh] (shaded fill)
            self._bbox = Rhino.Geometry.BoundingBox.Empty
            self._color = Color.FromArgb(_HV_R, _HV_G, _HV_B)
            self._fill = Color.FromArgb(
                _FACE_FILL_ALPHA, _HV_R, _HV_G, _HV_B
            )
            # Pro Instanz einstellbar: Hover-Conduit = dick + Flaechen-Fill;
            # persistenter Conduit = duenn + ohne Fill (nur Umriss).
            self._thickness = _EDGE_THICKNESS
            self._draw_fills = True

        # -- geometry staging -------------------------------------------------

        def clear_geometry(self):
            self.curves = []
            self.lines = []
            self.points = []
            self.face_meshes = []
            self._bbox = Rhino.Geometry.BoundingBox.Empty

        def _grow_bbox(self, bbox):
            try:
                if bbox is not None and bbox.IsValid:
                    self._bbox.Union(bbox)
            except Exception:
                pass

        def add_curve(self, curve):
            if curve is None:
                return
            try:
                self.curves.append(curve)
                self._grow_bbox(curve.GetBoundingBox(False))
            except Exception:
                pass

        def add_line(self, line):
            if line is None:
                return
            try:
                self.lines.append(line)
                bbox = Rhino.Geometry.BoundingBox([line.From, line.To])
                self._grow_bbox(bbox)
            except Exception:
                pass

        def add_point(self, point):
            if point is None:
                return
            try:
                self.points.append(point)
                self._grow_bbox(Rhino.Geometry.BoundingBox(point, point))
            except Exception:
                pass

        def add_face_mesh(self, mesh):
            if mesh is None:
                return
            try:
                self.face_meshes.append(mesh)
                self._grow_bbox(mesh.GetBoundingBox(False))
            except Exception:
                pass

        @property
        def has_geometry(self):
            return bool(
                self.curves or self.lines or self.points or self.face_meshes
            )

        # -- DisplayConduit overrides ----------------------------------------

        def CalculateBoundingBox(self, e):  # noqa: N802 — Rhino override
            try:
                if self._bbox.IsValid:
                    e.IncludeBoundingBox(self._bbox)
            except Exception:
                pass

        def DrawForeground(self, e):  # noqa: N802 — Rhino override
            # DrawForeground keeps the highlight on top of shaded geometry so
            # an edge tucked behind a face still reads. Wrapped defensively:
            # a draw exception must never bubble into Rhino's render loop.
            try:
                dp = e.Display
                # Shaded translucent faces first (drawn beneath the crisp
                # edge/point overlay). Nur wenn der Conduit Fills zeichnet
                # (Hover ja, persistente Markierung nein).
                if self._draw_fills:
                    for mesh in self.face_meshes:
                        try:
                            dp.DrawMeshShaded(mesh, _highlight_material())
                        except Exception:
                            pass
                for curve in self.curves:
                    try:
                        dp.DrawCurve(curve, self._color, self._thickness)
                    except Exception:
                        pass
                for line in self.lines:
                    try:
                        dp.DrawLine(line, self._color, self._thickness)
                    except Exception:
                        pass
                for point in self.points:
                    try:
                        # Hohler Ring UM den Punkt (kein gefuellter Marker):
                        # DrawPoint rendert seine PointStyles gefuellt, daher
                        # einen echten Kreis-UMRISS zeichnen. Bildschirm-
                        # konstanter Radius (px -> Weltmass am Punkt) und eine
                        # screen-parallele Ebene (CameraX/Y) -> immer ein Kreis,
                        # nie eine Ellipse, unabhaengig vom Zoom.
                        vp = dp.Viewport
                        ok, ppu = vp.GetWorldToScreenScale(point)
                        if ok and ppu > 0:
                            plane = Rhino.Geometry.Plane(
                                point, vp.CameraX, vp.CameraY
                            )
                            circle = Rhino.Geometry.Circle(
                                plane, _POINT_RADIUS / ppu
                            )
                            dp.DrawCircle(
                                circle, self._color, _POINT_RING_THICKNESS
                            )
                        else:
                            # Skala nicht ermittelbar -> wenigstens ein
                            # sichtbarer Marker.
                            dp.DrawPoint(
                                point,
                                Rhino.Display.PointStyle.RoundControlPoint,
                                _POINT_RADIUS,
                                self._color,
                            )
                    except Exception:
                        pass
            except Exception as exc:  # pragma: no cover — Rhino render loop
                logger.debug("highlight draw failed: %s", exc)

    return _HighlightConduit


_CONDUIT_CLASS = None
_conduit = None  # module-level singleton — exactly one active conduit (Hover)
_persist_conduit = None  # persistente, duenne K/F/O-Markierung (eigene Instanz)
_gate_conduit = None  # selektives Vorschau-Gate — eigene Instanz (Hover wischt es nicht weg)
_lock = threading.Lock()
_material = None  # cached DisplayMaterial for shaded faces


def _highlight_material():
    """Lazily build (and cache) the translucent highlight material."""
    global _material
    if _material is not None:
        return _material
    mat = Rhino.Display.DisplayMaterial(Color.FromArgb(_HV_R, _HV_G, _HV_B))
    try:
        mat.Transparency = 0.55
    except Exception:
        pass
    _material = mat
    return mat


def _ensure_conduit():
    """Return the singleton conduit, constructing it on first use."""
    global _CONDUIT_CLASS, _conduit
    if _conduit is not None:
        return _conduit
    if _CONDUIT_CLASS is None:
        _CONDUIT_CLASS = _make_conduit_class()
    _conduit = _CONDUIT_CLASS()
    return _conduit


def _active_doc():
    try:
        return Rhino.RhinoDoc.ActiveDoc
    except Exception:
        return None


def _redraw(doc) -> None:
    try:
        if doc is not None:
            doc.Views.Redraw()
        else:
            Rhino.RhinoApp.Wait()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Geometry resolution (document object by GUID → sub-component geometry)
# ---------------------------------------------------------------------------


def _find_object(doc, object_id: Optional[str]):
    """Resolve a document object by GUID string. None on any failure."""
    if not object_id or doc is None:
        return None
    try:
        guid = System.Guid(str(object_id))
    except Exception:
        return None
    try:
        return doc.Objects.FindId(guid)
    except Exception:
        return None


def _object_geometry(rh_obj):
    try:
        return rh_obj.Geometry if rh_obj is not None else None
    except Exception:
        return None


def _as_brep(geom):
    """Return a Brep view of geom (Brep direct or Extrusion→Brep).

    Extrusion: ``Brep.TryConvertBrep`` (NICHT ``ToBrep(False)``), damit die
    Kanten-/Flaechen-Indizes EXAKT mit dem Pick (pick._extrusion_component_
    fallback) und den Edit-Operationen (fillet/chamfer/round nutzen ebenfalls
    TryConvertBrep) uebereinstimmen — sonst wuerde das Highlight eine andere
    Kante zeigen als gepickt/bearbeitet wird. Fuer Ganz-Objekt-Wires ist die
    Reihenfolge egal; fuer den indizierten Komponenten-Highlight ist sie
    entscheidend."""
    try:
        if isinstance(geom, Rhino.Geometry.Brep):
            return geom
        if isinstance(geom, Rhino.Geometry.Extrusion):
            return Rhino.Geometry.Brep.TryConvertBrep(geom)
    except Exception:
        return None
    return None


def _geometry_class_of(geom) -> Optional[str]:
    try:
        if isinstance(geom, Rhino.Geometry.SubD):
            return "subd"
        if isinstance(geom, Rhino.Geometry.Mesh):
            return "mesh"
        if isinstance(geom, Rhino.Geometry.Extrusion):
            return "extrusion"
        if isinstance(geom, Rhino.Geometry.Brep):
            return "brep"
    except Exception:
        return None
    return None


def _stage_object_wires(conduit, geom) -> bool:
    """Stage the whole object's wires/edges as a highlight. Returns True on
    success (something was staged)."""
    if geom is None:
        return False
    # Curves and points have no geometry-CLASS in _geometry_class_of, but they
    # are perfectly highlightable. This matters for the Vorschau-Gate: a
    # rebuild ("Dicke aendern" -> Profilkurve loeschen, neu extrudieren, Kurve
    # loeschen) often gates a delete_object on a CONSTRUCTION CURVE. Without
    # this branch the highlight stayed empty while the gate card still claimed
    # "Geometrie hervorgehoben" -> der Designer suchte eine Markierung, die es
    # nicht gab.
    try:
        if isinstance(geom, Rhino.Geometry.Curve):
            crv = geom.DuplicateCurve()
            if crv is not None:
                conduit.add_curve(crv)
                return True
        if isinstance(geom, Rhino.Geometry.Point):
            conduit.add_point(geom.Location)
            return True
    except Exception as exc:
        logger.debug("stage curve/point wires failed: %s", exc)
    gclass = _geometry_class_of(geom)
    staged = False
    try:
        if gclass in ("brep", "extrusion"):
            brep = _as_brep(geom)
            if brep is not None and brep.Edges is not None:
                for edge in brep.Edges:
                    try:
                        crv = edge.DuplicateCurve()
                        if crv is not None:
                            conduit.add_curve(crv)
                            staged = True
                    except Exception:
                        pass
        elif gclass == "subd":
            # SubD edges aren't uniformly index-addressable by position, so
            # iterate the edge collection directly and approximate each as a
            # straight line. Falls back to a render-mesh wireframe if that
            # iteration isn't available on this RhinoCommon build.
            try:
                for edge in geom.Edges:
                    line = _subd_edge_line(edge)
                    if line is not None:
                        conduit.add_line(line)
                        staged = True
            except Exception:
                pass
            if not staged:
                staged = _stage_subd_via_mesh(conduit, geom)
        elif gclass == "mesh":
            staged = _stage_mesh_wires(conduit, geom)
    except Exception as exc:
        logger.debug("stage object wires failed: %s", exc)
    if not staged:
        # Universal fallback: ANY other deletable object type (annotation,
        # text, hatch, dimension, leader, light, point cloud — or a brep/mesh
        # whose edge extraction failed) still gets a visible marker via its
        # bounding box. This keeps the Vorschau-Gate card honest: it claims a
        # highlight whenever there are target GUIDs, and an EXISTING target
        # (the normal case at gate time, before deletion) is now ALWAYS
        # visibly highlighted regardless of geometry type — no silent empty.
        staged = _stage_bbox(conduit, geom)
    return staged


def _stage_bbox(conduit, geom) -> bool:
    """Last-resort marker: stage the object's bounding-box wireframe (or its
    centre point for a degenerate/zero-size box). Returns True on success."""
    try:
        bbox = geom.GetBoundingBox(False)
    except Exception:
        return False
    try:
        if bbox is None or not bbox.IsValid:
            return False
        corners = bbox.GetCorners()
        if not corners or len(corners) < 8:
            return False
        # Degenerate (point-like) box -> a single marker reads better than 12
        # zero-length lines.
        if corners[0].DistanceTo(corners[6]) < 1e-6:
            conduit.add_point(bbox.Center)
            return True
        edges = (
            (0, 1), (1, 2), (2, 3), (3, 0),  # bottom face
            (4, 5), (5, 6), (6, 7), (7, 4),  # top face
            (0, 4), (1, 5), (2, 6), (3, 7),  # verticals
        )
        staged = False
        for a, b in edges:
            try:
                conduit.add_line(Rhino.Geometry.Line(corners[a], corners[b]))
                staged = True
            except Exception:
                pass
        return staged
    except Exception as exc:
        logger.debug("stage bbox fallback failed: %s", exc)
        return False


def _subd_edge_line(edge):
    """Best-effort straight line for a SubD edge (control-net endpoints).

    ``SubDEdge`` hat KEIN ``EvaluatePoint`` (verifiziert gegen RhinoCommon.xml,
    0 Treffer); die korrekte API ist das ``ControlNetLine``-Property (Line der
    Kontrollnetz-Endpunkte). Vorher fiel jeder SubD-Edge-Stage still in den
    Render-Mesh-Fallback (_stage_subd_via_mesh) statt die echte Kantenlinie zu
    zeichnen — kein Crash (try/except), aber nie die korrekte Linie."""
    try:
        return edge.ControlNetLine
    except Exception:
        pass
    return None


def _stage_subd_via_mesh(conduit, subd) -> bool:
    """Fallback: stage a SubD's wireframe via its render mesh edges."""
    try:
        mesh = Rhino.Geometry.Mesh.CreateFromSubD(subd, 1)
        if mesh is None:
            return False
        return _stage_mesh_wires(conduit, mesh)
    except Exception:
        return False


def _stage_mesh_wires(conduit, mesh) -> bool:
    staged = False
    try:
        topo = mesh.TopologyEdges
        for i in range(topo.Count):
            try:
                line = topo.EdgeLine(i)
                conduit.add_line(line)
                staged = True
            except Exception:
                pass
    except Exception:
        pass
    return staged


def _stage_brep_edge(conduit, geom, idx: int) -> bool:
    brep = _as_brep(geom)
    if brep is None or brep.Edges is None:
        return False
    try:
        if idx < 0 or idx >= brep.Edges.Count:
            return False
        crv = brep.Edges[idx].DuplicateCurve()
        if crv is not None:
            conduit.add_curve(crv)
            return True
    except Exception:
        pass
    return False


def _stage_brep_face(conduit, geom, idx: int) -> bool:
    brep = _as_brep(geom)
    if brep is None or brep.Faces is None:
        return False
    try:
        if idx < 0 or idx >= brep.Faces.Count:
            return False
        face = brep.Faces[idx]
        # Outline: duplicate the face's boundary edges as crisp curves.
        outlined = False
        try:
            for crv in face.DuplicateFace(False).Edges:
                dup = crv.DuplicateCurve()
                if dup is not None:
                    conduit.add_curve(dup)
                    outlined = True
        except Exception:
            outlined = False
        # Translucent shaded fill via the face's render mesh.
        try:
            fmesh = face.GetMesh(Rhino.Geometry.MeshType.Render)
            if fmesh is None:
                fmesh = Rhino.Geometry.Mesh.CreateFromBrep(
                    face.DuplicateFace(False),
                    Rhino.Geometry.MeshingParameters.Default,
                )
                if fmesh is not None and len(fmesh) > 0:
                    fmesh = fmesh[0]
                else:
                    fmesh = None
            if fmesh is not None:
                conduit.add_face_mesh(fmesh)
                outlined = True
        except Exception:
            pass
        return outlined
    except Exception:
        return False


def _stage_subd_component(
    conduit, subd, component_type: str, idx: int
) -> bool:
    try:
        if component_type == "edge":
            edge = subd.Edges.Find(idx)
            if edge is not None:
                line = _subd_edge_line(edge)
                if line is not None:
                    conduit.add_line(line)
                    return True
        elif component_type == "face":
            face = subd.Faces.Find(idx)
            if face is not None:
                pt = face.ControlNetCenterPoint
                conduit.add_point(pt)
                return True
        elif component_type == "vertex":
            vert = subd.Vertices.Find(idx)
            if vert is not None:
                conduit.add_point(vert.ControlNetPoint)
                return True
    except Exception:
        pass
    return False


def _stage_mesh_component(
    conduit, mesh, component_type: str, idx: int
) -> bool:
    try:
        if component_type == "edge":
            line = mesh.TopologyEdges.EdgeLine(idx)
            conduit.add_line(line)
            return True
        elif component_type == "face":
            # Build a single-face mesh for a translucent shaded highlight,
            # plus a centre marker as a guaranteed fallback.
            fmesh = _one_face_mesh(mesh, idx)
            if fmesh is not None:
                conduit.add_face_mesh(fmesh)
                return True
            pt = mesh.Faces.GetFaceCenter(idx)
            conduit.add_point(pt)
            return True
        elif component_type == "vertex":
            pt = mesh.TopologyVertices[idx]
            conduit.add_point(Rhino.Geometry.Point3d(pt))
            return True
    except Exception:
        pass
    return False


def _one_face_mesh(mesh, idx: int):
    """Build a tiny mesh containing only face ``idx`` of ``mesh``, or None."""
    try:
        if idx < 0 or idx >= mesh.Faces.Count:
            return None
        face = mesh.Faces[idx]
        out = Rhino.Geometry.Mesh()
        out.Vertices.Add(mesh.Vertices[face.A])
        out.Vertices.Add(mesh.Vertices[face.B])
        out.Vertices.Add(mesh.Vertices[face.C])
        if face.IsQuad:
            out.Vertices.Add(mesh.Vertices[face.D])
            out.Faces.AddFace(0, 1, 2, 3)
        else:
            out.Faces.AddFace(0, 1, 2)
        out.Normals.ComputeNormals()
        return out
    except Exception:
        return None


def _stage_point(conduit, point: Any) -> bool:
    """Stage a Point3d marker from a [x,y,z] list."""
    try:
        if (
            isinstance(point, (list, tuple))
            and len(point) >= 3
        ):
            pt = Rhino.Geometry.Point3d(
                float(point[0]), float(point[1]), float(point[2])
            )
            conduit.add_point(pt)
            return True
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------------
# Block dispatch — stage highlight geometry from a reference block
# ---------------------------------------------------------------------------


def _stage_component_pick(conduit, block: dict) -> bool:
    doc = _active_doc()
    object_id = block.get("object_id")
    component_type = str(block.get("component_type") or "object").lower()
    component_index = block.get("component_index")
    geometry_class = block.get("geometry_class")

    rh_obj = _find_object(doc, object_id)
    geom = _object_geometry(rh_obj)

    # Whole-object highlight when no specific component is addressable.
    if (
        component_type == "object"
        or component_index is None
        or geom is None
    ):
        if _stage_object_wires(conduit, geom):
            return True
        # Last resort: marker at the reference point.
        return _stage_point(conduit, block.get("point"))

    try:
        idx = int(component_index)
    except (TypeError, ValueError):
        idx = None

    gclass = (geometry_class or _geometry_class_of(geom) or "").lower()
    staged = False
    if idx is not None:
        if gclass in ("brep", "extrusion"):
            if component_type == "edge":
                staged = _stage_brep_edge(conduit, geom, idx)
            elif component_type == "face":
                staged = _stage_brep_face(conduit, geom, idx)
            elif component_type == "vertex":
                # Breps don't expose a stable vertex index for our picks;
                # fall back to the reference point marker below.
                staged = False
        elif gclass == "subd":
            staged = _stage_subd_component(conduit, geom, component_type, idx)
        elif gclass == "mesh":
            staged = _stage_mesh_component(conduit, geom, component_type, idx)

    if staged:
        return True
    # Sub-component unresolved → fall back to whole object, then to a marker.
    if _stage_object_wires(conduit, geom):
        return True
    return _stage_point(conduit, block.get("point"))


def _stage_point_pick(conduit, block: dict) -> bool:
    # NUR den Punkt markieren — kein Ganz-Objekt-Highlight. Frueher wurde
    # zusaetzlich das Host-Objekt hervorgehoben ("damit der Punkt im Kontext
    # liegt"); beim Hover ueber ein Punkt-Token leuchtete dadurch aber die GANZE
    # Box blau auf. Gewollt ist nur der Punkt (Nutzerwunsch 25.06.2026).
    return _stage_point(conduit, block.get("point"))


def _stage_selection(conduit, block: dict) -> bool:
    doc = _active_doc()
    object_ids = block.get("object_ids") or []
    staged = False
    for object_id in object_ids:
        geom = _object_geometry(_find_object(doc, object_id))
        if _stage_object_wires(conduit, geom):
            staged = True
    return staged


# ---------------------------------------------------------------------------
# Regel-basierte Kantenauswahl — Gate-Highlight fuer round_edges_by_rule
# ---------------------------------------------------------------------------
# Die drei Helfer unten sind eine WORTGLEICHE Spiegelung der Auswahl-Logik in
# ``shared/code_templates_brep_edit.py`` -> ``round_edges_by_rule_code``
# (_norm_rule / _edge_row / _matches). Sie MUESSEN synchron bleiben: weicht die
# Klassifikation ab, hebt das Gate andere Kanten hervor als die Operation
# tatsaechlich rundet (schlimmer als das ganze Objekt). Quelle der Wahrheit ist
# das Code-Template; hier nur read-only nachgebildet, um EXAKT dieselben
# edge_ids zu bestimmen (gleicher bbox/tol/Klassifikations-Pfad).


def _rule_norm(raw) -> str:
    text = str(raw or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "alle": "all", "all_edges": "all", "alle_kanten": "all", "alles": "all",
        "alle_ausser_unten": "all_except_bottom",
        "all_except_lower": "all_except_bottom",
        "outer_except_bottom": "all_except_bottom",
        "aussen_ausser_unten": "all_except_bottom",
        "oben": "top", "obere": "top", "obere_kanten": "top",
        "unten": "bottom", "untere": "bottom", "untere_kanten": "bottom",
        "vertikal": "vertical", "vertikale": "vertical", "vertikale_kanten": "vertical",
        "horizontal": "horizontal", "horizontale": "horizontal",
        "horizontale_kanten": "horizontal",
        "x": "x_edges", "x_kanten": "x_edges",
        "y": "y_edges", "y_kanten": "y_edges",
        "z": "z_edges", "z_kanten": "z_edges",
        "front": "front", "vorne": "front", "vordere": "front",
        "back": "back", "hinten": "back", "hintere": "back",
        "left": "left", "links": "left", "linke": "left",
        "right": "right", "rechts": "right", "rechte": "right",
        "oben_vorne": "top_front", "top_front_edges": "top_front",
        "oben_hinten": "top_back", "top_back_edges": "top_back",
        "oben_links": "top_left", "top_left_edges": "top_left",
        "oben_rechts": "top_right", "top_right_edges": "top_right",
    }
    return aliases.get(text, text)


def _rule_near(a, b, tol) -> bool:
    return abs(float(a) - float(b)) <= tol


def _rule_edge_row(edge, idx, bbox, tol) -> dict:
    start = edge.PointAtStart
    end = edge.PointAtEnd
    center = Rhino.Geometry.Point3d(
        0.5 * (start.X + end.X),
        0.5 * (start.Y + end.Y),
        0.5 * (start.Z + end.Z),
    )
    direction = end - start
    if not direction.Unitize():
        direction = Rhino.Geometry.Vector3d(0, 0, 0)
    return {
        "index": idx,
        "top": _rule_near(center.Z, bbox.Max.Z, tol),
        "bottom": _rule_near(center.Z, bbox.Min.Z, tol),
        "front": _rule_near(center.Y, bbox.Min.Y, tol),
        "back": _rule_near(center.Y, bbox.Max.Y, tol),
        "left": _rule_near(center.X, bbox.Min.X, tol),
        "right": _rule_near(center.X, bbox.Max.X, tol),
        "x_edges": abs(direction.X) >= 0.85,
        "y_edges": abs(direction.Y) >= 0.85,
        "z_edges": abs(direction.Z) >= 0.85,
        "vertical": abs(direction.Z) >= 0.85,
        "horizontal": abs(direction.Z) <= 0.15,
    }


def _rule_matches(row, rule_name) -> bool:
    if rule_name == "all":
        return True
    if rule_name == "all_except_bottom":
        return not row["bottom"]
    if rule_name in row:
        return bool(row[rule_name])
    if rule_name == "top_front":
        return row["top"] and row["front"]
    if rule_name == "top_back":
        return row["top"] and row["back"]
    if rule_name == "top_left":
        return row["top"] and row["left"]
    if rule_name == "top_right":
        return row["top"] and row["right"]
    return False


def _stage_edge_rule(conduit, block: dict) -> bool:
    """Stage ONLY the edges a ``round_edges_by_rule`` op would round, resolved
    by the same rule logic (so the gate highlight matches the operation, not
    the whole object). Falls back to whole-object wires if nothing matches or
    resolution fails, so the highlight is never silently empty."""
    doc = _active_doc()
    geom = _object_geometry(_find_object(doc, block.get("object_id")))
    if geom is None:
        return False
    # Brep genau wie round_edges_by_rule_code aufloesen -> identische Indizes.
    brep = None
    try:
        if isinstance(geom, Rhino.Geometry.Brep):
            brep = geom
        else:
            brep = Rhino.Geometry.Brep.TryConvertBrep(geom)
    except Exception:
        brep = None
    if brep is None or brep.Edges is None or brep.Edges.Count == 0:
        return _stage_object_wires(conduit, geom)
    try:
        rule_name = _rule_norm(block.get("rule"))
        bbox = brep.GetBoundingBox(True)
        diag = bbox.Diagonal.Length
        try:
            base_tol = sc.doc.ModelAbsoluteTolerance
        except Exception:
            base_tol = 1e-3
        tol = max(base_tol * 10.0, diag * 1e-6, 1e-5)
        staged = False
        for i in range(brep.Edges.Count):
            row = _rule_edge_row(brep.Edges[i], i, bbox, tol)
            if _rule_matches(row, rule_name):
                crv = brep.Edges[i].DuplicateCurve()
                if crv is not None:
                    conduit.add_curve(crv)
                    staged = True
        if staged:
            return True
    except Exception as exc:
        logger.debug("edge_rule staging failed: %s", exc)
    # Keine Kante gematcht / Fehler -> ganzes Objekt, damit nie leer.
    return _stage_object_wires(conduit, geom)


def _stage_block(conduit, block: dict) -> bool:
    """Route a reference block to the matching staging routine.

    The block ``type`` is the discriminator; we also accept a bare block
    without ``type`` and infer from its shape (object_ids → selection,
    component_type → component_pick, point → point_pick)."""
    block_type = str(block.get("type") or "").lower()
    if not block_type:
        if block.get("object_ids"):
            block_type = "selection"
        elif block.get("component_type") or block.get("component_index") is not None:
            block_type = "component_pick"
        elif block.get("point") is not None:
            block_type = "point_pick"

    if block_type == "component_pick":
        return _stage_component_pick(conduit, block)
    if block_type == "point_pick":
        return _stage_point_pick(conduit, block)
    if block_type == "selection":
        return _stage_selection(conduit, block)
    if block_type == "edge_rule":
        return _stage_edge_rule(conduit, block)
    # Unknown block — best effort marker at point if present.
    return _stage_point(conduit, block.get("point"))


# ---------------------------------------------------------------------------
# Public API (called on the UI thread by server.py via InvokeOnUiThread)
# ---------------------------------------------------------------------------


def highlight_reference(block: Optional[dict]) -> None:
    """Stage and show a transient highlight for one reference block.

    Non-mutating: builds a DisplayConduit overlay only — no selection, no
    document edit, no undo record. Replaces any currently active highlight
    (exactly one is shown at a time). Best-effort: every failure is logged
    and swallowed so this never raises into the WS dispatch loop.
    """
    if not RHINO_AVAILABLE:
        logger.debug("highlight_reference skipped — Rhino unavailable")
        return
    if not isinstance(block, dict):
        logger.debug("highlight_reference: block is not a dict — clearing")
        clear_highlight()
        return
    try:
        with _lock:
            conduit = _ensure_conduit()
            # Re-stage from scratch each hover so a previous highlight can't
            # bleed into the new one.
            conduit.Enabled = False
            conduit.clear_geometry()
            try:
                _stage_block(conduit, block)
            except Exception as exc:  # defensive — staging must never raise
                logger.debug("highlight staging failed: %s", exc)
            if conduit.has_geometry:
                conduit.Enabled = True
            else:
                conduit.Enabled = False
            _redraw(_active_doc())
    except Exception as exc:  # pragma: no cover — never raise into WS loop
        logger.warning("highlight_reference failed: %s", exc)


def highlight_references(blocks: Optional[list]) -> None:
    """Stage and show a transient highlight for MULTIPLE reference blocks at
    once (e.g. several filleted edges of one Brep, or several SubD faces).

    Same non-mutating DisplayConduit as ``highlight_reference`` — builds an
    overlay only, no selection / document edit / undo record. Replaces any
    currently active highlight (exactly one overlay is shown at a time).
    Every block is staged into the SAME conduit before it is enabled, so all
    targeted components light up together. Best-effort: per-block and overall
    failures are logged and swallowed so this never raises into the caller.
    """
    if not RHINO_AVAILABLE:
        return
    if not blocks:
        clear_highlight()
        return
    try:
        with _lock:
            conduit = _ensure_conduit()
            conduit.Enabled = False
            conduit.clear_geometry()
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                try:
                    _stage_block(conduit, block)
                except Exception as exc:  # defensive — one bad block must not abort
                    logger.debug("highlight staging (multi) failed: %s", exc)
            conduit.Enabled = bool(conduit.has_geometry)
            _redraw(_active_doc())
    except Exception as exc:  # pragma: no cover — never raise into WS loop
        logger.warning("highlight_references failed: %s", exc)


def clear_highlight() -> None:
    """Disable the active highlight overlay and redraw.

    Idempotent and best-effort: safe to call when nothing is highlighted or
    when Rhino is unavailable.
    """
    if not RHINO_AVAILABLE:
        return
    try:
        with _lock:
            global _conduit
            if _conduit is not None:
                _conduit.Enabled = False
                _conduit.clear_geometry()
            _redraw(_active_doc())
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("clear_highlight failed: %s", exc)


# ---------------------------------------------------------------------------
# Selective-preview GATE highlight — OWN conduit instance
# ---------------------------------------------------------------------------
# The gate overlay (targets of a pending destructive op) must stay up for the
# whole decision window. It used to reuse the hover ``_conduit``, so a chat
# reference-token hover (re-stages _conduit) and its hover-out (clear_highlight
# disables _conduit) wiped the pending gate overlay. A dedicated conduit keeps
# the two independent (same pattern as _persist_conduit / _marker_conduit).


def _ensure_gate_conduit():
    """Return the gate-preview conduit (own instance)."""
    global _CONDUIT_CLASS, _gate_conduit
    if _gate_conduit is not None:
        return _gate_conduit
    if _CONDUIT_CLASS is None:
        _CONDUIT_CLASS = _make_conduit_class()
    _gate_conduit = _CONDUIT_CLASS()
    return _gate_conduit


def highlight_gate_targets(blocks: Optional[list], orange: bool = False) -> None:
    """Stage + show the selective-preview gate overlay on its OWN conduit.

    Same non-mutating overlay as ``highlight_references`` but on a dedicated
    conduit, so a transient hover-highlight (and its clear on hover-out) cannot
    wipe the gate's target highlight while the preview card is pending.

    ``orange=True`` faerbt das Overlay in Brand-Orange (Loesch-Vorschau: die zu
    entfernende Geometrie leuchtet als Warnung orange statt neutral blau). Der
    Gate-Conduit ist ein Singleton -> die Farbe wird bei JEDEM Aufruf explizit
    gesetzt, sonst bliebe der Ton des vorigen Gates haengen.
    """
    if not RHINO_AVAILABLE:
        return
    if not blocks:
        clear_gate_highlight()
        return
    try:
        with _lock:
            conduit = _ensure_gate_conduit()
            conduit.Enabled = False
            conduit.clear_geometry()
            if orange:
                conduit._color = Color.FromArgb(_DEL_R, _DEL_G, _DEL_B)
                conduit._fill = Color.FromArgb(
                    _FACE_FILL_ALPHA, _DEL_R, _DEL_G, _DEL_B
                )
            else:
                conduit._color = Color.FromArgb(_HV_R, _HV_G, _HV_B)
                conduit._fill = Color.FromArgb(
                    _FACE_FILL_ALPHA, _HV_R, _HV_G, _HV_B
                )
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                try:
                    _stage_block(conduit, block)
                except Exception as exc:  # defensive — one bad block must not abort
                    logger.debug("gate highlight staging failed: %s", exc)
            conduit.Enabled = bool(conduit.has_geometry)
            _redraw(_active_doc())
    except Exception as exc:  # pragma: no cover — never raise into caller
        logger.warning("highlight_gate_targets failed: %s", exc)


def clear_gate_highlight() -> None:
    """Disable the gate-preview overlay. Idempotent, best-effort."""
    if not RHINO_AVAILABLE:
        return
    try:
        with _lock:
            global _gate_conduit
            if _gate_conduit is not None:
                _gate_conduit.Enabled = False
                _gate_conduit.clear_geometry()
            _redraw(_active_doc())
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("clear_gate_highlight failed: %s", exc)


# ---------------------------------------------------------------------------
# Persistent component-reference highlight (thin blue outline of staged K/F/O picks)
# ---------------------------------------------------------------------------
# Eigene, PERSISTENTE Overlay-Schicht (getrennt vom transienten Hover-Highlight):
# markiert die gestagten K/F/O-Komponenten (Kante/Flaeche/Objekt) DUENN blau und
# bleibt sichtbar, solange die Tokens im Composer stehen. Eigene Conduit-Instanz
# (duenne Linie, kein Flaechen-Fill) -> das Hover-Highlight (clear-t beim Hover-Out)
# wischt sie nicht weg. Non-mutating; das Frontend setzt sie via
# ``viewport.persistent_refs`` (ersetzt die Menge) und leert sie nach dem Senden.


def _ensure_persist_conduit():
    """Singleton der persistenten, duennen Komponenten-Markierung."""
    global _CONDUIT_CLASS, _persist_conduit
    if _persist_conduit is not None:
        return _persist_conduit
    if _CONDUIT_CLASS is None:
        _CONDUIT_CLASS = _make_conduit_class()
    c = _CONDUIT_CLASS()
    try:
        c._thickness = _PERSIST_EDGE_THICKNESS
        c._draw_fills = False  # nur duenner Umriss, kein dicker Flaechen-Fill
    except Exception:
        pass
    _persist_conduit = c
    return _persist_conduit


# Letzte gesetzte persistente Referenzmenge — fuer reapply_persistent_references()
# nach einem MODALEN Pick (siehe dort).
_LAST_PERSIST_BLOCKS: Optional[list] = None


def set_persistent_references(blocks: Optional[list]) -> None:
    """Persistente, DUENNE blaue Markierung der gestagten Referenz-Bloecke
    (K/F/O-Komponenten). Ersetzt die aktuelle Menge; bleibt sichtbar bis zur
    naechsten Setzung oder bis leer (Senden/Entfernen). Non-mutating, eigener
    Conduit, best-effort — raised nie in die WS-Schleife."""
    global _LAST_PERSIST_BLOCKS
    if not RHINO_AVAILABLE:
        return
    if not blocks:
        clear_persistent_references()
        return
    _LAST_PERSIST_BLOCKS = list(blocks)
    try:
        with _lock:
            conduit = _ensure_persist_conduit()
            conduit.Enabled = False
            conduit.clear_geometry()
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                try:
                    _stage_block(conduit, block)
                except Exception as exc:  # eine schlechte Referenz bricht nicht ab
                    logger.debug("persist staging failed: %s", exc)
            conduit.Enabled = bool(conduit.has_geometry)
            _redraw(_active_doc())
    except Exception as exc:  # pragma: no cover — never raise into WS loop
        logger.warning("set_persistent_references failed: %s", exc)


def clear_persistent_references() -> None:
    """Persistente Komponenten-Markierung ausblenden. Idempotent, best-effort."""
    global _LAST_PERSIST_BLOCKS
    _LAST_PERSIST_BLOCKS = None
    if not RHINO_AVAILABLE:
        return
    try:
        with _lock:
            global _persist_conduit
            if _persist_conduit is not None:
                _persist_conduit.Enabled = False
                _persist_conduit.clear_geometry()
            _redraw(_active_doc())
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("clear_persistent_references failed: %s", exc)


def reapply_persistent_references() -> None:
    """Die zuletzt gesetzte persistente Markierung neu anwenden + neu zeichnen.

    Wird nach einem MODALEN Pick aufgerufen: bei der Shift-Mehrfachauswahl
    streamt das Frontend ``persistent_refs`` WAEHREND der modale ``gp.Get()``-
    Loop laeuft. Der zugehoerige UI-Run wird hinter den Pick gequeued und der
    Conduit-Redraw zeichnet nach Modal-Ende nicht zuverlaessig nach, wenn der
    Nutzer danach zum Panel statt in den Viewport geht. Ein erneuter
    set_persistent_references(zuletzt) raeumt das auf. Idempotent, best-effort."""
    if not RHINO_AVAILABLE:
        return
    if _LAST_PERSIST_BLOCKS:
        set_persistent_references(_LAST_PERSIST_BLOCKS)


# ---------------------------------------------------------------------------
# Persistent point-reference markers (small filled dots at picked points)
# ---------------------------------------------------------------------------
# Separate, persistent overlay from the transient hover highlight above: these
# mark the points the user picked (the inline point-pick tokens) and stay
# visible while composing, as a reference view. The frontend sets them via
# ``viewport.point_markers`` (replacing the set) and clears them once the sent
# message's operation has finished. Same non-mutating DisplayConduit approach —
# no selection, no document edit, no undo. Its own conduit instance so the hover
# highlight (which clears on hover-out) never wipes these markers.


def _make_marker_conduit_class():
    class _MarkerConduit(Rhino.Display.DisplayConduit):  # type: ignore
        def __init__(self):
            super(_MarkerConduit, self).__init__()
            self.points = []  # list[Rhino.Geometry.Point3d]
            self._bbox = Rhino.Geometry.BoundingBox.Empty
            self._color = Color.FromArgb(_HV_R, _HV_G, _HV_B)  # Brand-Blau (konsistent)

        def set_points(self, pts):
            self.points = list(pts)
            self._bbox = Rhino.Geometry.BoundingBox.Empty
            for p in self.points:
                try:
                    self._bbox.Union(Rhino.Geometry.BoundingBox(p, p))
                except Exception:
                    pass

        @property
        def has_points(self):
            return bool(self.points)

        def CalculateBoundingBox(self, e):  # noqa: N802 — Rhino override
            try:
                if self._bbox.IsValid:
                    e.IncludeBoundingBox(self._bbox)
            except Exception:
                pass

        def DrawForeground(self, e):  # noqa: N802 — Rhino override
            try:
                dp = e.Display
                for p in self.points:
                    try:
                        dp.DrawPoint(
                            p,
                            Rhino.Display.PointStyle.RoundControlPoint,
                            _MARKER_RADIUS,
                            self._color,
                        )
                    except Exception:
                        pass
            except Exception as exc:  # pragma: no cover — Rhino render loop
                logger.debug("marker draw failed: %s", exc)

    return _MarkerConduit


_MARKER_CONDUIT_CLASS = None
_marker_conduit = None


def _ensure_marker_conduit():
    global _MARKER_CONDUIT_CLASS, _marker_conduit
    if _marker_conduit is not None:
        return _marker_conduit
    if _MARKER_CONDUIT_CLASS is None:
        _MARKER_CONDUIT_CLASS = _make_marker_conduit_class()
    _marker_conduit = _MARKER_CONDUIT_CLASS()
    return _marker_conduit


def _parse_points(points) -> list:
    """Parse a list of [x, y, z] into Point3d; skip anything malformed."""
    out = []
    if not points:
        return out
    try:
        for p in points:
            if isinstance(p, (list, tuple)) and len(p) >= 3:
                try:
                    out.append(
                        Rhino.Geometry.Point3d(
                            float(p[0]), float(p[1]), float(p[2])
                        )
                    )
                except Exception:
                    pass
    except Exception:
        pass
    return out


def set_point_markers(points: Optional[list]) -> None:
    """Show small persistent blue dots at the given ``[x,y,z]`` points,
    replacing any current markers. Empty/None clears them. Non-mutating;
    best-effort (never raises into the WS loop). Caller marshals onto the UI
    thread."""
    if not RHINO_AVAILABLE:
        return
    try:
        with _lock:
            conduit = _ensure_marker_conduit()
            pts = _parse_points(points)
            conduit.set_points(pts)
            conduit.Enabled = bool(pts)
            _redraw(_active_doc())
    except Exception as exc:  # pragma: no cover — never raise into WS loop
        logger.warning("set_point_markers failed: %s", exc)


def clear_point_markers() -> None:
    """Hide all persistent point markers. Idempotent, best-effort."""
    if not RHINO_AVAILABLE:
        return
    try:
        with _lock:
            global _marker_conduit
            if _marker_conduit is not None:
                _marker_conduit.set_points([])
                _marker_conduit.Enabled = False
            _redraw(_active_doc())
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("clear_point_markers failed: %s", exc)


def teardown_overlays() -> None:
    """Disable every overlay conduit and release the cached display material.

    Called on backend shutdown. The conduits are process-lifetime singletons
    whose state is otherwise only cleared by frontend WS commands; if the panel
    disconnects with overlays staged (or a uvicorn restart happens inside the
    same Rhino process), stale highlight / persistent-reference / point-marker
    overlays would bleed into the next study session. Marshals onto the Rhino UI
    thread (fire-and-forget) like the WS clear handlers, so this is safe to call
    from the async shutdown path.
    """
    if not RHINO_AVAILABLE:
        return
    try:
        import Rhino  # type: ignore
        import System  # type: ignore

        def _run() -> None:
            global _material
            for fn in (
                clear_highlight,
                clear_gate_highlight,
                clear_persistent_references,
                clear_point_markers,
            ):
                try:
                    fn()
                except Exception:  # pragma: no cover — defensive
                    logger.debug("overlay teardown step failed", exc_info=True)
            try:
                if _material is not None:
                    _material.Dispose()
            except Exception:  # pragma: no cover — defensive
                logger.debug("material dispose failed", exc_info=True)
            _material = None

        Rhino.RhinoApp.InvokeOnUiThread(System.Action(_run))
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("teardown_overlays dispatch failed: %s", exc)


# ---------------------------------------------------------------------------
# Transient "created" flash (blue) — briefly lights up what a run produced
# ---------------------------------------------------------------------------
# After an agent run, the objects CREATED in that run light up blue for a
# moment ("wurde erstellt"-Bestaetigung), then fade. The real objects keep
# their normal (black/by-layer) colour — this is only a non-mutating overlay
# (no selection, no doc edit, no undo). Reuses the highlight conduit CLASS but
# its OWN instance in brand-primary blue, so the hover-highlight and the point
# markers stay completely independent. Timing is a single one-shot
# timer (no per-frame animation loop). Werkzeug-only — gated by the caller.

# Brand-Primary-Blau (= --primary hsl(222 92% 56%)) = die Plugin-Hauptfarbe.
_FLASH_R, _FLASH_G, _FLASH_B = 40, 102, 246
_FLASH_SECONDS = 0.7  # Dauer, die der blaue Flash steht, bevor er ausblendet

_flash_conduit = None
_flash_timer = None
_flash_gen = 0  # Generationszaehler: ein neuer Flash entwertet alte Clear-Timer


def _ensure_flash_conduit():
    global _CONDUIT_CLASS, _flash_conduit
    if _flash_conduit is not None:
        return _flash_conduit
    if _CONDUIT_CLASS is None:
        _CONDUIT_CLASS = _make_conduit_class()
    conduit = _CONDUIT_CLASS()
    # Blau statt Brand-Orange — eigener Ton fuer "neu erstellt".
    conduit._color = Color.FromArgb(_FLASH_R, _FLASH_G, _FLASH_B)
    conduit._fill = Color.FromArgb(
        _FACE_FILL_ALPHA, _FLASH_R, _FLASH_G, _FLASH_B
    )
    _flash_conduit = conduit
    return _flash_conduit


def _collect_run_created_ids(since_ts) -> list:
    """GUID-Strings, die in Aktionen ab ``since_ts`` erzeugt wurden (aus der
    Action-History in ``sc.sticky``). Reihenfolge erhalten, dedupliziert."""
    out = []
    if sc is None:
        return out
    try:
        history = sc.sticky.get("__furniture_action_history__") or []
    except Exception:
        return out
    for action in history:
        try:
            if float(action.get("started_at", 0)) < float(since_ts):
                continue
            for cid in (action.get("created_ids") or []):
                if cid not in out:
                    out.append(cid)
        except Exception:
            pass
    return out


def _do_flash(since_ts) -> None:
    """UI-Thread: erzeugte Objekte blau stagen + One-Shot-Timer zum Ausblenden."""
    global _flash_timer, _flash_gen
    try:
        with _lock:
            ids = _collect_run_created_ids(since_ts)
            conduit = _ensure_flash_conduit()
            conduit.Enabled = False
            conduit.clear_geometry()
            doc = _active_doc()
            for cid in ids:
                try:
                    geom = _object_geometry(_find_object(doc, cid))
                    _stage_object_wires(conduit, geom)
                except Exception:
                    pass
            conduit.Enabled = bool(conduit.has_geometry)
            _redraw(doc)
            if not conduit.Enabled:
                return
            _flash_gen += 1
            gen = _flash_gen
            if _flash_timer is not None:
                try:
                    _flash_timer.cancel()
                except Exception:
                    pass
            _flash_timer = threading.Timer(
                _FLASH_SECONDS, lambda: _on_flash_timeout(gen)
            )
            _flash_timer.daemon = True
            _flash_timer.start()
    except Exception as exc:  # pragma: no cover — never raise into the loop
        logger.warning("created-flash staging failed: %s", exc)


def _on_flash_timeout(gen) -> None:
    """Timer-Thread: das Ausblenden auf den UI-Thread marshallen."""
    try:
        Rhino.RhinoApp.InvokeOnUiThread(
            System.Action(lambda: _clear_flash_ui(gen))
        )
    except Exception:
        pass


def _clear_flash_ui(gen) -> None:
    global _flash_conduit
    try:
        with _lock:
            if gen != _flash_gen:
                return  # von einem neueren Flash abgeloest
            if _flash_conduit is not None:
                _flash_conduit.Enabled = False
                _flash_conduit.clear_geometry()
            _redraw(_active_doc())
    except Exception as exc:  # pragma: no cover — defensive
        logger.debug("created-flash clear failed: %s", exc)


def flash_run_created(since_ts) -> None:
    """Briefly flash (blue) the objects created at/after ``since_ts``.

    Non-mutating overlay only (no selection / doc edit / undo). Marshals onto
    the Rhino UI thread and arms a one-shot timer that fades the flash again.
    Best-effort — never raises into the agent loop. Called after a run."""
    if not RHINO_AVAILABLE:
        return
    try:
        Rhino.RhinoApp.InvokeOnUiThread(
            System.Action(lambda: _do_flash(since_ts))
        )
    except Exception as exc:  # pragma: no cover — never raise into the loop
        logger.debug("flash_run_created marshal failed: %s", exc)


__all__ = [
    "RHINO_AVAILABLE",
    "highlight_reference",
    "highlight_references",
    "clear_highlight",
    "set_point_markers",
    "clear_point_markers",
    "flash_run_created",
]
