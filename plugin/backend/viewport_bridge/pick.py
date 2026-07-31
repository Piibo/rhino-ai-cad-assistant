"""Object picking — lets the user pick objects in Rhino's viewport.

Dispatched via ``Rhino.RhinoApp.InvokeOnUiThread``. The background worker
waits on a ``threading.Event`` while ``rs.GetObject(s)`` runs modally on
the UI thread; once the user confirms (Enter) or cancels (Esc), the event
fires and the worker unblocks with the selected GUIDs.

Note: while a pick is active, Rhino's UI thread is busy inside
``GetObject(s)``. Further ``InvokeOnUiThread`` calls (e.g. other agent
tools) queue up and run after the pick returns. That's intentional —
picking is modal UX anyway.
"""

from __future__ import annotations

import base64
import logging
import math
import threading
from typing import Any, Literal, Optional, TypedDict

logger = logging.getLogger("FurniturePlugin.Pick")

# Live-Modifier-Push ans Panel: broadcast_threadsafe marshalt vom Rhino-UI-Thread
# in die FastAPI-Loop (eigener Thread). Bewaehrtes Muster aus selection_watcher/
# undo_watcher, die ebenfalls waehrend eines modalen Picks broadcasten.
from ..websocket_manager import manager
from .. import schemas

try:  # Only available inside Rhino's Python context.
    import Rhino  # type: ignore
    import scriptcontext as sc  # type: ignore
    import rhinoscriptsyntax as rs  # type: ignore
    import System  # type: ignore
    try:
        # Fuer System.Windows.Forms.Control.ModifierKeys (Modifier beim Klick
        # frisch lesen). In Rhino meist ohnehin geladen; AddReference sichert es.
        import clr  # type: ignore
        clr.AddReference("System.Windows.Forms")
    except Exception:
        pass
    from System.Drawing import Bitmap  # type: ignore
    from System.Drawing.Imaging import ImageFormat  # type: ignore
    from System.IO import MemoryStream  # type: ignore

    RHINO_AVAILABLE = True
except Exception as e:  # pragma: no cover — headless fallback
    logger.info("RhinoCommon not available (%s) — pick disabled", e)
    RHINO_AVAILABLE = False


# Hover-Picker (GetPoint-basiert): hebt die Komponente unter dem Cursor LIVE blau
# hervor, Klick bestaetigt -> kein Auswahlmenue. Der bewaehrte GetObject-Pfad
# bleibt als FALLBACK (siehe _pick_component_on_ui: jeder Fehler faellt zurueck).
#
# Aufbau: _pick_under_cursor pickt entlang des Cursor-Strahls (GetPickTransform ->
# PickContext -> PickObjects). PickObjects liefert nie einen Sub-Komponenten-Index,
# darum wird Kante/Flaeche PUNKTBASIERT aus dem Trefferpunkt (SelectionPoint)
# bestimmt (_nearest_component_on_brep: naechste Kante, sonst getroffene Flaeche;
# Flaeche robust via Brep.ClosestPoint). Der Pick respektiert bewusst den Display-
# Modus: Wireframe = see-through (Kanten + geschlossene Solids, Durchgriff zur
# Rueckseite), Shaded/Rendered = Flaechen auch OFFENER Objekte. Der erzwungene
# Shaded-Pick (_force_shaded_pickmode) ist dormant — Nutzerentscheidung 25.06.2026
# zugunsten von see-through. Iterations-Verlauf: siehe log.md (25.06.2026).
#
# USE_HOVER_PICKER = False -> sofort zurueck zum bewaehrten Menue-Pick.
# HOVER_PICKER_DEBUG = True -> pro Hover 'hover candidate: ...' ins Log (Diagnose;
# in Studiensessions aus).
USE_HOVER_PICKER = True
HOVER_PICKER_DEBUG = False

# Modifier-Erweiterungen des Hover-Pickers (25.06.2026) — einzeln abschaltbar,
# falls doch nicht gewollt einfach auf False setzen (rein additiv, kein Umbau).
# Tastenbelegung (Nutzerwahl 25.06.2026 — "Shift = mehr, Strg = groesser"):
#   SHIFT halten -> Mehrfachauswahl (Shift = "zur Auswahl hinzufuegen", wie ueberall):
#     der Pick bleibt nach dem Klick offen und sammelt weiter, bis ein Klick OHNE
#     Shift oder Enter folgt. Kombinierbar mit Strg (mehrere ganze Objekte).
#   STRG halten  -> GANZES Objekt statt Kante/Flaeche (Hover hebt das ganze Objekt
#     hervor, Klick referenziert es).
# Flaechen OFFENER Objekte (Loft/Sweep): nicht mehr per Taste, sondern indem man
# Rhino kurz auf einen Shaded-Anzeigemodus stellt (Rhino-nativ). Der dormante
# _force_shaded_pickmode bleibt als Reaktivierungs-Haken erhalten.
ENABLE_MULTI_PICK = True       # Shift = mehrere sammeln
ENABLE_OBJECT_MODIFIER = True  # Strg  = ganzes Objekt statt Komponente


class PickResult(TypedDict):
    object_ids: list[str]
    names: list[str]
    types: list[str]
    snapshot: Optional[dict]  # ImageSource-shaped dict or None


def _build_pick_result(ids, snapshot_max_size=600):
    # type: (list, int) -> PickResult
    """Build a PickResult from a list of Rhino object GUIDs.

    Called from the Rhino UI thread — uses rs directly.
    Compatible with IronPython 2.7 (no f-strings).
    """
    names = []
    types = []
    for oid in ids:
        names.append(rs.ObjectName(oid) or "")
        types.append(_type_name(rs.ObjectType(oid)))
    snapshot = _capture_preview(int(snapshot_max_size))
    return PickResult(
        object_ids=[str(i) for i in ids],
        names=names,
        types=types,
        snapshot=snapshot,
    )


# rhinoscriptsyntax filter bit-flags (ORable). See
# https://developer.rhino3d.com/api/RhinoScriptSyntax — "Get" family.
_FILTER_MAP = {
    "any": 0,
    "point": 1,
    "curve": 4,
    "surface": 8,
    "polysurface": 16,
    "mesh": 32,
    "subd": 262144,
    # Common composite: anything "solid" in the casual sense
    "solid": 8 | 16 | 262144,
}


def pick_objects(
    prompt: str = "Objekte wählen (Enter = Fertig, Esc = Abbrechen)",
    mode: str = "multi",
    object_filter: str = "any",
    snapshot_max_size: int = 600,
    timeout: Optional[float] = None,
) -> PickResult:
    """Let the user pick one or more objects in the active Rhino viewport.

    Args:
        prompt: Command-line prompt shown in Rhino.
        mode: ``"multi"`` (rs.GetObjects) or ``"single"`` (rs.GetObject).
        object_filter: Key from ``_FILTER_MAP``; falls back to ``"any"``.
        snapshot_max_size: Largest dimension of the confirmation preview JPEG.
        timeout: Seconds to wait for the UI action. ``None`` = wait forever
            (user must press Esc in Rhino to cancel).

    Returns:
        PickResult with GUIDs, names, rough type labels, and an optional
        preview snapshot. An empty ``object_ids`` means the user cancelled.

    Raises:
        RuntimeError: Rhino not available, timeout tripped, or UI error.
    """
    if not RHINO_AVAILABLE:
        raise RuntimeError(
            "Rhino nicht verfügbar — plugin.backend läuft außerhalb von Rhino"
        )

    holder: dict = {}
    done = threading.Event()

    def _do() -> None:
        try:
            ids = _pick_on_ui(prompt, mode, object_filter)
            if not ids:
                holder["ok"] = PickResult(
                    object_ids=[], names=[], types=[], snapshot=None
                )
                return
            holder["ok"] = _build_pick_result(ids, snapshot_max_size)
        except Exception as exc:  # pragma: no cover — Rhino-side
            logger.exception("pick _do failed: %s", exc)
            holder["err"] = str(exc)
        finally:
            done.set()

    Rhino.RhinoApp.InvokeOnUiThread(System.Action(_do))

    if not done.wait(timeout=timeout):
        raise RuntimeError("pick timed out")

    if "err" in holder:
        raise RuntimeError(holder["err"])

    return holder["ok"]


def _pick_on_ui(prompt: str, mode: str, object_filter: str):
    """Must run on Rhino's UI thread (invoked from ``_do``)."""
    flt = _FILTER_MAP.get(object_filter.lower(), 0)
    if mode == "single":
        oid = rs.GetObject(prompt, flt, preselect=True, select=False)
        ids = [oid] if oid else None
    else:  # multi
        ids = rs.GetObjects(
            prompt,
            flt,
            group=False,
            preselect=True,
            select=False,
        )
    # KEINE gelbe Rhino-Selektion hinterlassen: die Markierung des gepickten
    # Objekts laeuft ueber das blaue DisplayConduit-Overlay (wie bei K/F/O), NICHT
    # ueber rs.Select. Eine evtl. vorbestehende Vorauswahl wird mit aufgehoben,
    # damit nichts gelb stehen bleibt.
    try:
        rs.UnselectAllObjects()
        rs.Redraw()
    except Exception:
        pass
    return ids


def _type_name(otype: int) -> str:
    """Coarse textual label for a Rhino object-type bitflag."""
    lookup = {
        1: "point",
        2: "pointcloud",
        4: "curve",
        8: "surface",
        16: "polysurface",
        32: "mesh",
        256: "light",
        512: "annotation",
        4096: "block",
        8192: "text_dot",
        65536: "hatch",
        262144: "subd",
        1073741824: "extrusion",
    }
    return lookup.get(otype, f"type#{otype}")


def _capture_preview(max_size: int) -> Optional[dict]:
    """Grab a small JPEG of the active view — useful so the model can see
    what the user just selected. Returns an ImageSource-shaped dict.
    """
    try:
        view = sc.doc.Views.ActiveView
        if view is None:
            return None
        bmp: Optional[Bitmap] = None
        resized: Optional[Bitmap] = None
        ms: Optional[MemoryStream] = None
        try:
            bmp = view.CaptureToBitmap()
            w, h = bmp.Width, bmp.Height
            if w >= h:
                nw = max_size
                nh = max(1, int(h * max_size / w))
            else:
                nh = max_size
                nw = max(1, int(w * max_size / h))
            resized = Bitmap(bmp, nw, nh)
            ms = MemoryStream()
            resized.Save(ms, ImageFormat.Jpeg)
            img_bytes = bytes(bytearray(ms.ToArray()))
        finally:
            if ms is not None:
                ms.Dispose()
            if resized is not None:
                resized.Dispose()
            if bmp is not None:
                bmp.Dispose()
        return {
            "type": "base64",
            "media_type": "image/jpeg",
            "data": base64.b64encode(img_bytes).decode("ascii"),
        }
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("pick preview capture failed: %s", exc)
        return None


# Markenfarben fuer die Punkt-Markierung (vgl. FEATURES.md §11, Plugin-Tokens).
_MARKER_RGB = (40, 101, 246)  # Plugin --primary (#2865F6), Marker-Blau
_LABEL_BG_RGB = (255, 255, 255)  # weisse Card-Pille
_LABEL_BORDER_RGB = (216, 220, 228)  # Plugin gray-blue --border (#D8DCE4)
_LABEL_TEXT_RGB = (26, 28, 36)  # Plugin --foreground (#1A1C24), near-black


def _project_world_to_bitmap(world_point, vp, bmp):
    """World-Point3d -> (x, y) Pixel auf dem erfassten Bitmap, oder None.

    Nutzt den World->Screen-Transform des Viewports
    (``vp.GetTransform(CoordinateSystem.World, CoordinateSystem.Screen)``).
    Dieser liefert Pixel relativ zu ``vp.Size``; da ``CaptureToBitmap()`` in
    einer anderen Aufloesung erfassen kann, werden die Koordinaten mit
    ``bmp.Width/vp.Size.Width`` bzw. ``bmp.Height/vp.Size.Height`` skaliert.
    Rein lesend; gibt bei jedem Fehler None zurueck (Fallback auf schlichten
    Capture). Marker-Alignment ist erst im laufenden Rhino exakt verifizierbar.
    """
    try:
        cs = Rhino.DocObjects.CoordinateSystem
        xform = vp.GetTransform(cs.World, cs.Screen)
        pt = Rhino.Geometry.Point3d(
            float(world_point.X), float(world_point.Y), float(world_point.Z)
        )
        pt.Transform(xform)
        sx, sy = float(pt.X), float(pt.Y)
        size = vp.Size
        vw = float(size.Width) if size.Width else float(bmp.Width)
        vh = float(size.Height) if size.Height else float(bmp.Height)
        scale_x = float(bmp.Width) / vw if vw else 1.0
        scale_y = float(bmp.Height) / vh if vh else 1.0
        return (sx * scale_x, sy * scale_y)
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("point projection failed: %s", exc)
        return None


def _annotate_point(bmp, px, py, label):
    """Zeichnet offenen Ring + Mittelpunkt + Aussen-Ticks (+ optionales Label).

    System.Drawing in-place, Plugin-Optik: EIN offener blauer Ring (fill none),
    ein kleiner gefuellter Mittelpunkt und vier kurze Crosshair-Ticks NUR
    ausserhalb des Rings (nicht durch die Mitte — der Nutzer will durch die Mitte
    auf den Punkt sehen). KEIN Halo, KEIN weisser Ring. Linienstaerke und Radius
    skalieren mit der Bildgroesse. Alle GDI-Ressourcen werden disposed.
    """
    from System.Drawing import (  # lazy: nur im Annotations-Pfad noetig
        Color,
        Pen,
        SolidBrush,
        RectangleF,
        Font,
        FontStyle,
        StringFormat,
    )
    from System.Drawing.Drawing2D import SmoothingMode, LineCap

    g = None
    try:
        g = System.Drawing.Graphics.FromImage(bmp)
        g.SmoothingMode = SmoothingMode.AntiAlias

        dim = min(bmp.Width, bmp.Height)
        radius = max(7.0, dim * 0.022)
        core_w = max(2.0, dim * 0.0035)
        tick_in = radius * 1.25  # Ticks beginnen ausserhalb des Rings
        tick_out = radius * 1.95  # und reichen ein Stueck weiter nach aussen
        dot_r = radius * 0.28  # kleiner Mittelpunkt, verdeckt kaum etwas

        accent = Color.FromArgb(255, *_MARKER_RGB)

        # Offener Ring (fill none) + Aussen-Ticks in einem Pen-Pass.
        ring_pen = None
        try:
            ring_pen = Pen(accent, float(core_w))
            try:
                ring_pen.StartCap = LineCap.Round
                ring_pen.EndCap = LineCap.Round
            except Exception:
                pass
            g.DrawEllipse(
                ring_pen,
                float(px - radius),
                float(py - radius),
                float(radius * 2.0),
                float(radius * 2.0),
            )
            # 4 kurze Segmente N/E/S/W, nur ausserhalb des Rings.
            g.DrawLine(
                ring_pen, float(px - tick_out), float(py),
                float(px - tick_in), float(py),
            )
            g.DrawLine(
                ring_pen, float(px + tick_in), float(py),
                float(px + tick_out), float(py),
            )
            g.DrawLine(
                ring_pen, float(px), float(py - tick_out),
                float(px), float(py - tick_in),
            )
            g.DrawLine(
                ring_pen, float(px), float(py + tick_in),
                float(px), float(py + tick_out),
            )
        finally:
            if ring_pen is not None:
                ring_pen.Dispose()

        # Gefuellter Mittelpunkt — markiert den Punkt praezise.
        dot_brush = None
        try:
            dot_brush = SolidBrush(accent)
            g.FillEllipse(
                dot_brush,
                float(px - dot_r),
                float(py - dot_r),
                float(dot_r * 2.0),
                float(dot_r * 2.0),
            )
        finally:
            if dot_brush is not None:
                dot_brush.Dispose()

        if label:
            _draw_label(
                g, px, py, radius, label, dim,
                Color, Pen, SolidBrush, RectangleF, Font, FontStyle, StringFormat,
            )
    finally:
        if g is not None:
            g.Dispose()


def _draw_label(
    g, px, py, radius, label, dim,
    Color, Pen, SolidBrush, RectangleF, Font, FontStyle, StringFormat,
):
    """Weisse Card-Pille (gray-blue Rand) mit blauem Akzent-Punkt + Leitlinie.

    Plugin-Optik: weisser, abgerundeter Hintergrund via GraphicsPath, near-black
    Text, kleiner blauer Akzent-Punkt links vor dem Text, duenne blaue Leitlinie
    vom Ringrand zur Card. Edge-Flip (Label klappt nach links am Bildrand)
    beibehalten. Faellt bei GraphicsPath-Fehler defensiv auf FillRectangle/
    DrawRectangle zurueck — der Pick darf nie brechen.
    """
    from System.Drawing.Drawing2D import GraphicsPath  # lazy
    from System.Drawing import PointF

    font = None
    fmt = None
    bg_brush = None
    text_brush = None
    border_pen = None
    accent_brush = None
    leader_pen = None
    path = None
    try:
        font_size = max(9.0, dim * 0.026)
        font = Font("Segoe UI", float(font_size), FontStyle.Bold)
        fmt = StringFormat()
        text = label if len(label) <= 40 else (label[:39] + "…")
        size = g.MeasureString(text, font)
        pad = max(4.0, font_size * 0.4)
        dot_r = font_size * 0.22  # blauer Akzent-Punkt vor dem Text
        gap = dot_r * 2.0 + pad * 0.6  # Texteinrueckung hinter dem Akzent
        box_w = float(size.Width) + 2.0 * pad + gap
        box_h = float(size.Height) + 1.6 * pad
        # rechts neben dem Marker; bei Bildrand nach links klappen
        flipped = False
        bx = float(px) + radius * 1.4
        by = float(py) - box_h / 2.0
        if bx + box_w > bmp_safe_width(g):
            bx = float(px) - radius * 1.4 - box_w
            flipped = True
        if bx < 0.0:
            bx = max(0.0, float(px) - box_w / 2.0)
            flipped = False
        if by < 0.0:
            by = 0.0

        hairline = max(1.0, dim * 0.0016)
        accent = Color.FromArgb(255, *_MARKER_RGB)

        # Leitlinie: vom Ringrand (Richtung Label) zur nahen Card-Kante.
        try:
            leader_pen = Pen(accent, float(hairline))
            if flipped:
                # Card links: vom linken Ringrand zur rechten Card-Kante.
                sx = float(px) - radius
                ex = bx + box_w
            else:
                # Card rechts: vom rechten Ringrand zur linken Card-Kante.
                sx = float(px) + radius
                ex = bx
            ey = by + box_h / 2.0
            g.DrawLine(leader_pen, float(sx), float(py), float(ex), float(ey))
        except Exception:
            pass

        # Card-Hintergrund: weisse abgerundete Pille via GraphicsPath; bei
        # Fehler defensiver Fallback auf Rechteck.
        bg_brush = SolidBrush(Color.FromArgb(255, *_LABEL_BG_RGB))
        border_pen = Pen(Color.FromArgb(255, *_LABEL_BORDER_RGB), float(hairline))
        corner = min(box_h * 0.45, 12.0)
        used_path = False
        try:
            d = float(corner) * 2.0
            path = GraphicsPath()
            path.AddArc(float(bx), float(by), d, d, 180.0, 90.0)
            path.AddArc(float(bx + box_w - d), float(by), d, d, 270.0, 90.0)
            path.AddArc(
                float(bx + box_w - d), float(by + box_h - d), d, d, 0.0, 90.0
            )
            path.AddArc(float(bx), float(by + box_h - d), d, d, 90.0, 90.0)
            path.CloseFigure()
            g.FillPath(bg_brush, path)
            g.DrawPath(border_pen, path)
            used_path = True
        except Exception:
            used_path = False
        if not used_path:
            g.FillRectangle(bg_brush, RectangleF(bx, by, box_w, box_h))
            g.DrawRectangle(border_pen, bx, by, box_w, box_h)

        # Blauer Akzent-Punkt links vor dem Text.
        accent_brush = SolidBrush(accent)
        adx = bx + pad
        ady = by + box_h / 2.0 - dot_r
        g.FillEllipse(
            accent_brush,
            float(adx), float(ady), float(dot_r * 2.0), float(dot_r * 2.0),
        )

        # Text in near-black, hinter dem Akzent-Punkt eingerueckt.
        text_brush = SolidBrush(Color.FromArgb(255, *_LABEL_TEXT_RGB))
        g.DrawString(text, font, text_brush, bx + pad + gap, by + 0.8 * pad, fmt)
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("point label draw failed: %s", exc)
    finally:
        for res in (
            border_pen, text_brush, bg_brush, accent_brush, leader_pen,
            path, fmt, font,
        ):
            if res is not None:
                try:
                    res.Dispose()
                except Exception:
                    pass


def bmp_safe_width(g):
    """Clip-Breite der Graphics-Flaeche (Bitmap-Breite)."""
    try:
        return float(g.VisibleClipBounds.Width)
    except Exception:
        return 1.0e9


def _capture_point_preview(world_point, label, max_size):
    # type: (Any, str, int) -> Optional[dict]
    """Wie ``_capture_preview``, aber markiert ``world_point`` im Bitmap.

    Zeichnet eine kontrastreiche Ring+Fadenkreuz-Markierung an der projizierten
    Pixel-Position des gepickten Punkts (plus optionales ``label``), resized auf
    ``max_size`` und liefert ein ImageSource-dict. Bricht NIE: schlaegt
    Projektion oder Annotation fehl, faellt die Funktion auf den schlichten
    ``_capture_preview`` zurueck.
    """
    try:
        view = sc.doc.Views.ActiveView
        if view is None:
            return None
        vp = view.ActiveViewport
        bmp = None
        resized = None
        ms = None
        try:
            bmp = view.CaptureToBitmap()
            if bmp is None:
                return _capture_preview(max_size)

            projected = None
            if world_point is not None:
                projected = _project_world_to_bitmap(world_point, vp, bmp)
            if projected is not None:
                try:
                    _annotate_point(bmp, projected[0], projected[1], label)
                except Exception as ann_exc:  # pragma: no cover — defensive
                    logger.warning("point annotation failed: %s", ann_exc)

            w, h = bmp.Width, bmp.Height
            if w >= h:
                nw = max_size
                nh = max(1, int(h * max_size / w))
            else:
                nh = max_size
                nw = max(1, int(w * max_size / h))
            resized = Bitmap(bmp, nw, nh)
            ms = MemoryStream()
            resized.Save(ms, ImageFormat.Jpeg)
            img_bytes = bytes(bytearray(ms.ToArray()))
        finally:
            if ms is not None:
                ms.Dispose()
            if resized is not None:
                resized.Dispose()
            if bmp is not None:
                bmp.Dispose()
        return {
            "type": "base64",
            "media_type": "image/jpeg",
            "data": base64.b64encode(img_bytes).decode("ascii"),
        }
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("point preview capture failed (%s) — plain fallback", exc)
        try:
            return _capture_preview(max_size)
        except Exception:
            return None


class PointResult(TypedDict):
    point: Optional[list[float]]  # [x, y, z] in model units, None if cancelled
    snapshot: Optional[dict]  # ImageSource-shaped dict or None
    object_id: Optional[str]
    object_name: Optional[str]
    object_type: Optional[str]
    snap_type: Optional[str]


class ComponentPickResult(TypedDict):
    point: Optional[list[float]]
    pick_point: Optional[list[float]]
    component_type: Optional[str]
    component_index: Optional[int]
    component_info: Optional[dict[str, Any]]
    snapshot: Optional[dict]
    object_id: Optional[str]
    object_name: Optional[str]
    object_type_name: Optional[str]
    object_class: Optional[str]
    # Resolved geometry class of the picked host object:
    # "brep" | "extrusion" | "subd" | "mesh" | None. Lets the router /
    # flattener dispatch SubD/Mesh component edits without relying on the
    # (optional, marker-based) ``object_class`` user-text. None when the
    # class could not be determined or on cancel.
    geometry_class: Optional[str]
    allowed_operations: list[str]


# Englische Osnap-Enum-Namen -> deutsche Kurzbezeichnung fuers Label.
_SNAP_LABELS_DE = {
    "end": "Endpunkt",
    "near": "Nahe",
    "point": "Punkt",
    "midpoint": "Mittelpunkt",
    "center": "Zentrum",
    "intersection": "Schnittpunkt",
    "perpendicular": "Senkrecht",
    "tangent": "Tangente",
    "quadrant": "Quadrant",
    "knot": "Knoten",
    "vertex": "Vertex",
    "focus": "Brennpunkt",
}


def _snap_label_de(snap_type: Optional[str]) -> str:
    """Deutsche Kurzbezeichnung fuer einen (englischen) Osnap-Enum-Namen."""
    if not snap_type:
        return ""
    key = str(snap_type).lower()
    for token, label in _SNAP_LABELS_DE.items():
        if token in key:
            return label
    return str(snap_type)


# Aufgeloester Komponententyp -> deutsche Kurzbezeichnung fuers Marker-Label.
_COMPONENT_LABELS_DE = {
    "edge": "Kante",
    "face": "Flaeche",
    "vertex": "Vertex",
    "object": "Objekt",
}

# Geometrieklasse -> kurzes Praefix fuers Marker-Label (z.B. "SubD-Kante").
_GEOMETRY_CLASS_PREFIX_DE = {
    "subd": "SubD",
    "mesh": "Mesh",
    "brep": "Brep",
    "extrusion": "Extrusion",
}


def _component_marker_label(
    resolved_type: Optional[str],
    resolved_index: Optional[int],
    geometry_class: Optional[str],
    object_name: Optional[str],
) -> str:
    """Marker-Label fuer den Komponenten-Pick, analog zum Punkt-Pick.

    Format: ``<Klasse>-<Komponente> #<Index> · <Host-Name>``, z.B.
    "SubD-Kante #8 · Stuhlbein" oder "Flaeche · Box". Klassen-Praefix und
    Index sind optional (entfallen, wenn unbekannt); fehlt alles, bleibt das
    Label leer und es wird nur der Marker ohne Textfeld gezeichnet.
    """
    comp_de = _COMPONENT_LABELS_DE.get(str(resolved_type or "").lower(), "")
    if not comp_de:
        head = ""
    else:
        prefix = _GEOMETRY_CLASS_PREFIX_DE.get(str(geometry_class or "").lower(), "")
        # Beim ganzen Objekt ist der Klassen-Praefix als eigenes Wort klarer
        # ("Brep · Objekt" statt "Brep-Objekt"); bei Sub-Komponenten gebunden.
        if prefix and str(resolved_type or "").lower() != "object":
            head = "%s-%s" % (prefix, comp_de)
        elif prefix:
            head = "%s · %s" % (prefix, comp_de)
        else:
            head = comp_de
        if resolved_index is not None:
            try:
                head = "%s #%d" % (head, int(resolved_index))
            except Exception:
                pass
    parts = []
    if head:
        parts.append(head)
    if object_name:
        parts.append(str(object_name))
    return " · ".join(parts)


def _live_point_marker(sender, e):
    """DynamicDraw-Handler: markiert ``e.CurrentPoint`` live waehrend des Picks.

    Bildschirm-fester, KLEINER Marker (Nutzerwunsch 25.06.2026 — der fruehere
    grosse Ring+Crosshair wirkte uebertrieben): offener blauer Ring (~28
    2D-Segmente, Radius R=6.5 px) + winziger gefuellter Mittelpunkt (RoundSimple),
    alles in ``_MARKER_RGB``. Keine Aussen-Ticks mehr (die Snapshot-Annotation
    ``_annotate_point`` bleibt davon unberuehrt). Der aktuelle Weltpunkt wird via
    ``RhinoViewport.WorldToClient`` auf Client-Pixel projiziert und ueber die
    DisplayPipeline in 2D (``Draw2dLine`` mit PointF) gezeichnet — so bleibt der
    Marker konstant gross statt mit dem Zoom zu skalieren. Bricht NIE: schlaegt
    Projektion oder eine 2D-Zeichen-API fehl, faellt der Pfad auf einen
    schlichten gefuellten Punkt (RoundSimple) zurueck. Host-Highlight live
    deferred (Raycast pro Frame waere zu teuer/fragil). Marker-Alignment/
    Aussehen ist erst im laufenden Rhino exakt verifizierbar.
    """
    try:
        disp = e.Display
        pt = e.CurrentPoint
        color = System.Drawing.Color.FromArgb(*_MARKER_RGB)

        drew_2d = False
        try:
            from System.Drawing import PointF  # lazy: nur im Draw-Pfad noetig

            vp = None
            try:
                vp = e.Viewport
            except Exception:
                vp = None
            if vp is None:
                try:
                    vp = disp.Viewport
                except Exception:
                    vp = None

            cpt = vp.WorldToClient(pt) if vp is not None else None
            if cpt is not None:
                cx, cy = float(cpt.X), float(cpt.Y)
                R = 6.5           # Ringradius in Pixeln (bildschirm-fest) — klein
                thick = 1.5
                segs = 28

                # Offener Ring als Polylinie aus 2D-Segmenten.
                two_pi = 2.0 * math.pi
                prev = None
                for i in range(segs + 1):
                    a = two_pi * i / segs
                    x = cx + R * math.cos(a)
                    y = cy + R * math.sin(a)
                    if prev is not None:
                        disp.Draw2dLine(
                            PointF(float(prev[0]), float(prev[1])),
                            PointF(float(x), float(y)),
                            color,
                            float(thick),
                        )
                    prev = (x, y)

                # Kein Aussen-Crosshair mehr — nur der kleine Ring + ein winziger
                # gefuellter Mittelpunkt (klar + unaufdringlich).
                disp.DrawPoint(
                    pt,
                    Rhino.Display.PointStyle.RoundSimple,
                    3,
                    color,
                )
                drew_2d = True
        except Exception:
            drew_2d = False

        if not drew_2d:
            # Fallback: schlichter, sauberer gefuellter Punkt (NICHT der
            # schwere RoundActivePoint-Glyph). Der Pick darf nie stocken.
            try:
                disp.DrawPoint(
                    pt,
                    Rhino.Display.PointStyle.RoundSimple,
                    6,
                    color,
                )
            except Exception:
                pass
    except Exception:
        # Draw-Callback NIE den Pick stoeren lassen.
        pass


def pick_point(
    prompt: str = "Punkt waehlen (Snap aktiv, Esc = Abbrechen)",
    snapshot_max_size: int = 600,
    timeout: Optional[float] = None,
) -> PointResult:
    """Let the user pick a single point in the active viewport.

    Uses ``rs.GetPoint`` so all standard snap modes (endpoint, midpoint,
    center, intersection, ...) work — the user can click anywhere or snap to
    existing geometry. Returns immediately on the click; no Enter required
    (unlike object picking).

    Returns:
        ``PointResult`` with ``point=[x, y, z]`` or ``point=None`` on cancel.

    Note: prompt is kept ASCII-only and the post-pick snapshot is wrapped
    defensively. The previous version used an em-dash in the prompt and a
    raw ``_capture_preview`` call — both became suspects after the picker
    silently no-op'd. Keeping this minimal to stay close to the known-good
    reference call shape.
    """
    if not RHINO_AVAILABLE:
        raise RuntimeError(
            "Rhino nicht verfuegbar — plugin.backend laeuft ausserhalb von Rhino"
        )

    holder: dict = {}
    done = threading.Event()

    def _do() -> None:
        try:
            logger.info("pick_point: Rhino.Input.Custom.GetPoint starting")
            gp = Rhino.Input.Custom.GetPoint()
            gp.SetCommandPrompt(prompt)
            # Live-Markierung des aktuellen Punkts (GetPoint.DynamicDraw ist
            # auf diesen Pick gescoped — kein eigener DisplayConduit, kein
            # Lifecycle-Risiko). Defensiv angehaengt: schlaegt das Verdrahten
            # fehl, laeuft der Pick ohne Live-Marker weiter.
            try:
                gp.DynamicDraw += _live_point_marker
            except Exception as draw_exc:
                logger.warning(
                    "pick_point: DynamicDraw attach failed: %s", draw_exc
                )
            rc = gp.Get()
            logger.info("pick_point: GetPoint returned %r", rc)
            if rc != Rhino.Input.GetResult.Point:
                holder["ok"] = PointResult(
                    point=None,
                    snapshot=None,
                    object_id=None,
                    object_name=None,
                    object_type=None,
                    snap_type=None,
                )
                return
            pt = gp.Point()
            coords = [float(pt.X), float(pt.Y), float(pt.Z)]

            object_id: Optional[str] = None
            object_name: Optional[str] = None
            object_type: Optional[str] = None
            snap_type: Optional[str] = None

            try:
                obj_ref = gp.PointOnObject()
            except Exception:
                obj_ref = None

            if obj_ref is not None:
                try:
                    oid = obj_ref.ObjectId
                    if oid and oid != System.Guid.Empty:
                        object_id = str(oid)
                        object_name = rs.ObjectName(oid) or ""
                        object_type = _type_name(rs.ObjectType(oid))
                except Exception as obj_exc:
                    logger.warning(
                        "pick_point: failed to inspect host object: %s", obj_exc
                    )

            try:
                osnap_event = gp.OsnapEventType
                if osnap_event is not None:
                    osnap_text = (
                        osnap_event.ToString()
                        if hasattr(osnap_event, "ToString")
                        else str(osnap_event)
                    )
                    if osnap_text and osnap_text.lower() not in {"none", "0"}:
                        snap_type = osnap_text
            except Exception as snap_exc:
                logger.warning(
                    "pick_point: failed to inspect snap type: %s", snap_exc
                )

            # Label fuer die Snapshot-Markierung: Snap-Typ (deutsch) und ggf.
            # Host-Objektname, z.B. "Endpunkt · Stuhlbein". Leer, wenn weder
            # Snap noch Name vorliegt — dann nur der Marker ohne Textfeld.
            label_parts = []
            snap_de = _snap_label_de(snap_type)
            if snap_de:
                label_parts.append(snap_de)
            if object_name:
                label_parts.append(object_name)
            marker_label = " · ".join(label_parts)

            # Snapshot is nice-to-have; if capture fails for any reason
            # the pick should still succeed. Markiert den gepickten Punkt.
            try:
                snapshot = _capture_point_preview(
                    pt, marker_label, int(snapshot_max_size)
                )
            except Exception as cap_exc:
                logger.warning("pick_point: capture_preview failed: %s", cap_exc)
                snapshot = None
            holder["ok"] = PointResult(
                point=coords,
                snapshot=snapshot,
                object_id=object_id,
                object_name=object_name,
                object_type=object_type,
                snap_type=snap_type,
            )
        except Exception as exc:
            logger.exception("pick_point _do failed: %s", exc)
            holder["err"] = str(exc)
        finally:
            done.set()

    Rhino.RhinoApp.InvokeOnUiThread(System.Action(_do))

    if not done.wait(timeout=timeout):
        raise RuntimeError("pick_point timed out")

    if "err" in holder:
        raise RuntimeError(holder["err"])

    return holder["ok"]


def pick_component(
    component_type: Literal["edge", "face", "vertex", "auto"] = "auto",
    snapshot_max_size: int = 600,
    timeout: Optional[float] = None,
    on_component=None,
    force_object: bool = False,
) -> ComponentPickResult:
    """Pick a single edge, face or vertex as a true sub-object reference.

    Uses ``Rhino.Input.Custom.GetObject`` with sub-object selection enabled.
    The returned payload carries the parent object GUID plus the picked
    component index, so the chat can refer to "this edge" / "this face" /
    "this vertex" explicitly instead of guessing from a point on the surface.

    Dispatches by geometry class of the picked host object: Brep/Extrusion
    use the typed ``ObjRef.Edge()/.Face()`` accessors; SubD and Mesh are
    resolved from the ``GeometryComponentIndex`` (those accessors are
    Brep-only and would yield ``None`` for SubD/Mesh).
    """
    if not RHINO_AVAILABLE:
        raise RuntimeError(
            "Rhino nicht verfuegbar - plugin.backend laeuft ausserhalb von Rhino"
        )

    raw_kind = str(component_type).lower()
    kind: Literal["edge", "face", "vertex", "auto"] = (
        raw_kind if raw_kind in {"edge", "face", "vertex", "auto"} else "auto"
    )
    holder: dict = {}
    done = threading.Event()

    def _do() -> None:
        try:
            holder["ok"] = _pick_component_on_ui(
                kind, int(snapshot_max_size), on_component, force_object
            )
        except Exception as exc:
            logger.exception("pick_component _do failed: %s", exc)
            holder["err"] = str(exc)
        finally:
            done.set()

    Rhino.RhinoApp.InvokeOnUiThread(System.Action(_do))

    if not done.wait(timeout=timeout):
        raise RuntimeError("pick_component timed out")

    if "err" in holder:
        raise RuntimeError(holder["err"])

    return holder["ok"]


def _pick_component_on_ui(
    component_type: Literal["edge", "face", "vertex", "auto"],
    snapshot_max_size: int,
    on_component=None,
    force_object: bool = False,
) -> ComponentPickResult:
    # Hover-Picker zuerst versuchen (falls aktiviert); bei JEDEM unerwarteten
    # Fehler defensiv in den bewaehrten GetObject-Pfad unten zurueckfallen.
    # Bei USE_HOVER_PICKER=False = Null-Impact (Default).
    if USE_HOVER_PICKER:
        try:
            return _pick_component_on_ui_hover(
                component_type, snapshot_max_size, on_component, force_object
            )
        except Exception as exc:
            logger.warning(
                "hover picker failed, falling back to GetObject: %s", exc
            )
    # --- pre-selection shortcut ---
    # If the user already has objects selected in the viewport, skip the
    # interactive GetObject entirely and return those as a plain selection
    # (pick_result), exactly like the multi-object pick does.  A single
    # pre-selected object is also returned as a list (no special-casing).
    pre = rs.SelectedObjects()
    if pre:
        pick_result = _build_pick_result(pre, snapshot_max_size)
        # Signal to the caller that this is a selection, not a component pick.
        return dict(kind="selection", **pick_result)

    if component_type == "edge":
        prompt = "Kante waehlen (Enter/Esc = Abbrechen)"
    elif component_type == "face":
        prompt = "Flaeche waehlen (Enter/Esc = Abbrechen)"
    elif component_type == "vertex":
        prompt = "Vertex waehlen (Enter/Esc = Abbrechen)"
    else:
        prompt = (
            "Komponente waehlen "
            "(Kante/Flaeche; Strg = Objekt; Enter/Esc = Abbrechen)"
        )
    go = Rhino.Input.Custom.GetObject()
    go.SetCommandPrompt(prompt)
    go.EnableClearObjectsOnEntry(False)
    go.EnableUnselectObjectsOnExit(False)
    go.EnablePreSelect(True, True)
    go.EnablePostSelect(True)
    go.EnableHighlight(True)
    go.SubObjectSelect = True
    go.BottomObjectPreference = False  # False = prefer top/front object (Rhino default; more intuitive for novices)
    go.ChooseOneQuestion = True
    go.AcceptNothing(True)
    # Vertices of SubD/Mesh are not part of the default sub-object filter.
    # Widen the geometry-attribute filter so edge/face/vertex of every
    # geometry class stay selectable without losing whole-object picks.
    # Done defensively: if the enum/property is unavailable in this Rhino
    # build, fall back silently to the SubObjectSelect default (which still
    # covers edges/faces for Brep + SubD + Mesh).
    try:
        gaf = Rhino.DocObjects.GeometryAttributeFilter
        go.GeometryAttributeFilter = (
            gaf.SubSurface
            | gaf.EdgeCurve
            | gaf.MeshVertex
            | gaf.MeshEdge
            | gaf.MeshFace
            | gaf.SubDVertex
            | gaf.SubDEdge
            | gaf.SubDFace
        )
    except Exception as exc:
        logger.warning(
            "GeometryAttributeFilter unavailable — SubD/Mesh vertex picks may fail: %s",
            exc,
        )

    while True:
        rc = go.Get()
        if rc != Rhino.Input.GetResult.Object:
            return ComponentPickResult(
                point=None,
                pick_point=None,
                component_type=None,
                component_index=None,
                component_info=None,
                snapshot=None,
                object_id=None,
                object_name=None,
                object_type_name=None,
                object_class=None,
                geometry_class=None,
                allowed_operations=[],
            )

        obj_ref = go.Object(0)
        if obj_ref is None:
            Rhino.RhinoApp.WriteLine(
                "Kein gueltiges Element erkannt. Bitte erneut waehlen."
            )
            continue

        resolved = _resolve_component_pick(obj_ref, component_type)
        if resolved is None:
            Rhino.RhinoApp.WriteLine(
                "Bitte direkt eine %s anklicken." % _component_label(component_type)
            )
            go.EnablePreSelect(False, True)
            continue

        resolved_type, resolved_index, geometry_class = resolved

        return _finalize_component_pick(
            obj_ref,
            resolved_type,
            resolved_index,
            geometry_class,
            snapshot_max_size,
        )


def _finalize_component_pick(
    obj_ref, resolved_type, resolved_index, geometry_class, snapshot_max_size,
    capture_snapshot=True,
):
    """Gemeinsamer Schwanz von altem GetObject-Pfad und neuem Hover-Pfad: aus
    einem aufgeloesten ObjRef das identische ComponentPickResult bauen
    (pick_point, stabiler Referenzpunkt, Host-Metadaten, component_info,
    allowed_operations, markierter Snapshot). Reine Extraktion des bisherigen
    Inline-Blocks -> beide Pfade liefern byte-gleichen Payload."""
    pick_point = None
    try:
        sel_pt = obj_ref.SelectionPoint()
        if not getattr(sel_pt, "IsValid", False):
            raise ValueError("selection point unset")
        pick_point = [float(sel_pt.X), float(sel_pt.Y), float(sel_pt.Z)]
    except Exception:
        pick_point = None

    # Stabiler geometrischer Referenzpunkt (Edge-Midpoint / Face-Center /
    # Vertex-Position / Objekt-bbox-Center) bevorzugt vor der rohen Klickstelle.
    point = _component_midpoint(
        obj_ref, resolved_type, geometry_class, resolved_index
    )
    if point is None:
        point = pick_point

    oid = obj_ref.ObjectId
    object_id = (
        str(oid)
        if oid is not None and oid != System.Guid.Empty
        else None
    )
    object_name = rs.ObjectName(oid) or "" if object_id else None
    object_type_name = _type_name(rs.ObjectType(oid)) if object_id else None
    object_class = (
        rs.GetUserText(oid, "object_class") or None
        if object_id
        else None
    )
    component_info = _component_info(
        obj_ref, resolved_type, geometry_class, resolved_index
    )
    allowed_operations = _allowed_operations(
        object_class, resolved_type, object_type_name, geometry_class
    )

    # Markiere die gepickte Komponente auf dem Ergebnis-Snapshot — analog zum
    # Punkt-Pick. Defensiv: faellt auf den schlichten _capture_preview zurueck;
    # der Pick bricht NIE.
    if not capture_snapshot:
        # Mehrfachauswahl (Shift): KEIN Snapshot je Pick. Das Render-to-Bitmap
        # blockiert kurz den UI-Thread und liess die Live-Hover-Zeichnung
        # aussetzen; ausserdem waeren die Bilder nahezu identisch. Das Token
        # traegt die Komponente exakt ueber object_id/Index/Punkt -> ohne Bild
        # voll nutzbar.
        snapshot = None
    else:
        try:
            repr_pt = None
            if point is not None:
                try:
                    repr_pt = Rhino.Geometry.Point3d(
                        float(point[0]), float(point[1]), float(point[2])
                    )
                except Exception:
                    repr_pt = None
            if repr_pt is not None:
                marker_label = _component_marker_label(
                    resolved_type, resolved_index, geometry_class, object_name
                )
                snapshot = _capture_point_preview(
                    repr_pt, marker_label, int(snapshot_max_size)
                )
            else:
                snapshot = _capture_preview(snapshot_max_size)
        except Exception as cap_exc:
            logger.warning("pick_component: capture_preview failed: %s", cap_exc)
            try:
                snapshot = _capture_preview(snapshot_max_size)
            except Exception:
                snapshot = None

    return ComponentPickResult(
        point=point,
        pick_point=pick_point,
        component_type=resolved_type,
        component_index=(
            int(resolved_index) if resolved_index is not None else None
        ),
        component_info=component_info,
        snapshot=snapshot,
        object_id=object_id,
        object_name=object_name,
        object_type_name=object_type_name,
        object_class=object_class,
        geometry_class=geometry_class,
        allowed_operations=allowed_operations,
    )


class _DrawSink:
    """Adapter mit der add_*-Schnittstelle, die highlight._stage_* erwartet;
    schreibt die gestagete Geometrie spaet-gebunden in die Draw-Listen des
    Pickers (ueber dessen aktuelle Attribute), damit nach _clear_draw immer in
    die frischen Listen geschrieben wird. So werden die erprobten Staging-
    Funktionen ohne DisplayConduit wiederverwendet."""

    def __init__(self, picker, acc=False):
        self._p = picker
        self._acc = acc  # True -> Accumulator-Listen (duenn, kein Flaechen-Fill)

    def add_curve(self, curve):
        if curve is not None:
            target = self._p._acc_curves if self._acc else self._p._draw_curves
            target.append(curve)

    def add_line(self, line):
        if line is not None:
            target = self._p._acc_lines if self._acc else self._p._draw_lines
            target.append(line)

    def add_face_mesh(self, mesh):
        if self._acc:
            return  # Accumulator zeichnet keinen Flaechen-Fill — nur duenne Umrisse
        if mesh is not None:
            self._p._draw_meshes.append(mesh)

    def add_point(self, point):
        if point is not None:
            target = self._p._acc_points if self._acc else self._p._draw_points
            target.append(point)


def _objref_has_subcomponent(obj_ref):
    """True, wenn der ObjRef einen ECHTEN Sub-Komponenten-Index traegt (Kante/
    Flaeche/Vertex) statt des ganzen Koerpers. Whole-Object-Refs haben
    ComponentIndexType.InvalidType bzw. NoType (Index -1). Voll defensiv — im
    Zweifel False (dann faellt _pick_under_cursor auf refs[0] zurueck)."""
    try:
        ci = obj_ref.GeometryComponentIndex
    except Exception:
        return False
    if ci is None:
        return False
    try:
        t = ci.ComponentIndexType
    except Exception:
        return False
    try:
        if t == Rhino.Geometry.ComponentIndexType.InvalidType:
            return False
        if t == Rhino.Geometry.ComponentIndexType.NoType:
            return False
    except Exception:
        return False
    return True


_PICKMODE_VALUE = None       # gecachter "shaded"-Enum-Wert (oder None)
_PICKMODE_RESOLVED = False   # Enum erst einmal aufloesen + loggen


def _force_shaded_pickmode(pc):
    """Setzt PickContext.PickMode auf den 'shaded'-Wert, damit auch im
    Wireframe-Display die OBERFLAECHE getroffen wird statt nur die Drahtkante.

    Hintergrund (Live-Test 25.06.2026): bei einem offenen Sweep-/Extrusion-Objekt
    traf der Pick im Wireframe nur die Drahtkante -> SelectionPoint lag IMMER auf
    einer Kante (edge_d=0) -> Flaechen waren nicht waehlbar; in Rendered ging es.
    Der Enum-Wert wird per Namensmatch ('shad') gewaehlt (robust gegen den exakten
    Namen) und einmalig mit allen verfuegbaren Werten geloggt."""
    global _PICKMODE_VALUE, _PICKMODE_RESOLVED
    try:
        if not _PICKMODE_RESOLVED:
            _PICKMODE_RESOLVED = True
            try:
                pm_type = Rhino.Input.Custom.PickMode
                names = list(System.Enum.GetNames(pm_type))
                chosen = None
                for nm in names:
                    if "shad" in nm.lower():
                        chosen = System.Enum.Parse(pm_type, nm)
                        break
                _PICKMODE_VALUE = chosen
                logger.info("PickMode-Werte=%s -> shaded=%s", names, chosen)
            except Exception as ex:
                logger.info("PickMode nicht aufloesbar: %s", ex)
                _PICKMODE_VALUE = None
        if _PICKMODE_VALUE is not None:
            pc.PickMode = _PICKMODE_VALUE
    except Exception:
        pass


def _pick_under_cursor(viewport, window_point, shaded=False):
    """ObjRef der nahelegendsten Komponente unter dem Cursor via PickContext +
    doc.Objects.PickObjects mit SubObjectSelectionEnabled — liefert einen ObjRef
    MIT Sub-Komponenten-Index (Brep-Kante/Flaeche, SubD, Mesh) oder None.

    Das ist der ZUVERLAESSIGE Hit-Test: PointOnObject() lag bei PermitObjectSnap-
    aus auf der Konstruktionsebene (z=0) statt auf der Flaeche unter dem Cursor
    und war daher flaky. PickObjects pickt direkt entlang des Cursor-Strahls
    (GetPickTransform vom Fensterpunkt). Voll defensiv — gibt im Zweifel None."""
    try:
        if viewport is None or window_point is None:
            return None
        view = None
        try:
            view = viewport.ParentView
        except Exception:
            view = None
        if view is None:
            try:
                view = sc.doc.Views.ActiveView
            except Exception:
                view = None
        if view is None:
            return None
        pc = Rhino.Input.Custom.PickContext()
        pc.View = view
        try:
            pc.PickStyle = Rhino.Input.Custom.PickStyle.PointPick
        except Exception:
            pass
        try:
            pc.SubObjectSelectionEnabled = True
        except Exception:
            pass
        # Shaded-Pick standardmaessig AUS (see-through): verdeckte/hintere Flaechen
        # bleiben durchs Objekt waehlbar. shaded=True ist DORMANT (OnMouseMove setzt
        # es aktuell nicht mehr — Strg ist jetzt der Objekt-Modifier) und erzwingt
        # bei Bedarf den Shaded-Pick. Reaktivierungs-Haken, falls Flaechen offener
        # Objekte doch per Taste statt per Display-Modus gewaehlt werden sollen.
        if shaded:
            _force_shaded_pickmode(pc)
        pc.SetPickTransform(viewport.GetPickTransform(window_point))
        try:
            pc.UpdateClippingPlanes()
        except Exception:
            pass
        refs = sc.doc.Objects.PickObjects(pc)
        if refs is None:
            return None
        try:
            if len(refs) == 0:
                return None
        except Exception:
            return None
        # Sub-Komponenten-ObjRef BEVORZUGEN: PickObjects liefert pro Objekt unter
        # dem Cursor oft mehrere ObjRefs gemischt (ganzes Objekt + Kante/Flaeche),
        # und die Reihenfolge ist geometrie-typabhaengig. Bei Brep rettet der
        # Edge()/Face()-Pfad in _resolve_component_pick einen Whole-Object-refs[0]
        # noch; bei SubD/Mesh gibt es diesen Rettungspfad NICHT — dort MUSS der
        # ObjRef mit gesetztem Sub-Index durchgereicht werden, sonst bleibt es
        # ewig 'object'. Also explizit den ersten Treffer mit echtem Sub-Index.
        for r in refs:
            if _objref_has_subcomponent(r):
                return r
        return refs[0]
    except Exception:
        return None


_HOVER_PICKER_CLASS = None


def _make_hover_picker_class():
    """Lazy-Factory fuer die GetPoint-Subklasse (Rhino.Input.Custom.GetPoint
    existiert nur im Rhino-Kontext; gleiches Muster wie
    highlight._make_conduit_class)."""

    class _HoverComponentPicker(Rhino.Input.Custom.GetPoint):  # type: ignore
        def __init__(self, requested_type, force_object=False):
            super(_HoverComponentPicker, self).__init__()
            self._requested = requested_type     # "edge"|"face"|"vertex"|"auto"
            self._force_object = bool(force_object)  # immer ganzes Objekt (Parametrisieren)
            self.cand_objref = None               # Rhino.DocObjects.ObjRef|None
            self.cand_type = None                 # "edge"|"face"|"vertex"|"object"|None
            self.cand_index = None                # Optional[int]
            self.cand_gclass = None               # "brep"|"extrusion"|"subd"|"mesh"|None
            self._draw_curves = []                # list[Curve]   (Kanten / Face-Outline)
            self._draw_lines = []                 # list[Line]    (SubD/Mesh-Kanten)
            self._draw_meshes = []                # list[Mesh]    (transluzente Face-Fills)
            self._draw_points = []                # list[Point3d] (Vertices)
            # Accumulator: schon GEWAEHLTE Komponenten (Mehrfachauswahl). Werden
            # DUENN mitgezeichnet (OnDynamicDraw), sofort sichtbar sobald gewaehlt
            # — der Backend-Conduit kann waehrend des modalen Picks nicht zeichnen.
            # NICHT pro Hover geleert.
            self._acc_curves = []
            self._acc_lines = []
            self._acc_points = []
            self._blue = System.Drawing.Color.FromArgb(40, 102, 246)  # --primary
            self._material = None                 # cached DisplayMaterial
            self._last_key = None                 # Drossel: (oid, type, index)
            self.multi_down = False               # Shift gehalten? (Mehrfachauswahl)
            self.object_mode = bool(force_object)  # Strg gehalten? ODER force_object
            self._multi_session = False           # schon 1x mit Shift gepickt?
            self._prev_shift = False              # Shift-Zustand des letzten Frames
            # Zuletzt ans Panel GEPUSHTER Modifier-Status (Aenderungserkennung,
            # damit nicht jeder Frame ein WS-Event ausloest). object startet auf
            # force_object (im Objekt-Pick konstant -> kein Spurious-Event).
            self._pushed_multi = False
            self._pushed_object = bool(force_object)
            self._last_vp = None                  # letzter Viewport (Off-Move-Recompute)
            self._last_wpt = None                 # letzte Cursor-Screen-Pos

        def _candidate_key(self, obj_ref, rtype, ridx):
            try:
                return (str(obj_ref.ObjectId), rtype, ridx)
            except Exception:
                return None

        def _has_draw(self):
            return bool(
                self._draw_curves or self._draw_lines
                or self._draw_meshes or self._draw_points
            )

        def _clear_draw(self):
            self._draw_curves = []
            self._draw_lines = []
            self._draw_meshes = []
            self._draw_points = []

        def _material_blue(self):
            if self._material is not None:
                return self._material
            mat = Rhino.Display.DisplayMaterial(self._blue)
            try:
                mat.Transparency = 0.6
            except Exception:
                pass
            self._material = mat
            return mat

        def _rebuild_draw(self, obj_ref, rtype, ridx, gclass):
            """Zeichen-Geometrie fuer den Kandidaten bauen — delegiert an die
            erprobten highlight._stage_*-Funktionen via _DrawSink-Adapter."""
            self._clear_draw()
            try:
                rh_obj = obj_ref.Object()
                geom = rh_obj.Geometry if rh_obj is not None else None
            except Exception:
                geom = None
            if geom is None:
                return
            try:
                from . import highlight as _hl
            except Exception:
                return
            sink = _DrawSink(self)
            try:
                if rtype == "object" or ridx is None:
                    _hl._stage_object_wires(sink, geom)
                elif gclass in ("brep", "extrusion"):
                    if rtype == "edge":
                        _hl._stage_brep_edge(sink, geom, int(ridx))
                    elif rtype == "face":
                        _hl._stage_brep_face(sink, geom, int(ridx))
                    else:
                        _hl._stage_object_wires(sink, geom)
                elif gclass == "subd":
                    _hl._stage_subd_component(sink, geom, rtype, int(ridx))
                elif gclass == "mesh":
                    _hl._stage_mesh_component(sink, geom, rtype, int(ridx))
                else:
                    _hl._stage_object_wires(sink, geom)
            except Exception:
                self._clear_draw()

        def _maybe_push_modifiers(self, multi, obj):
            # Bei einer Modifier-AENDERUNG genau EIN schlankes WS-Event ans Panel
            # pushen ({shift, object}); _pushed_* verhindert Pro-Frame-Flut. Geteilt
            # von OnMouseMove (mit Mausbewegung) und dem RhinoApp.Idle-Handler (OHNE,
            # fuer Strg). broadcast_threadsafe marshalt vom UI-Thread in die Loop;
            # darf den Pick nie stoeren.
            try:
                if multi != self._pushed_multi or obj != self._pushed_object:
                    self._pushed_multi = multi
                    self._pushed_object = obj
                    manager.broadcast_threadsafe(
                        schemas.WsEvent(
                            type="viewport.pick_modifiers",
                            payload={"shift": multi, "object": obj},
                        )
                    )
                    return True
            except Exception:
                pass
            return False

        def OnMouseMove(self, e):  # noqa: N802 — Rhino override
            # Pick unter Cursor + Kandidat cachen. Darf den Pick NIE crashen.
            try:
                # SHIFT -> Mehrfachauswahl merken (vom Klick-Loop ausgewertet);
                # STRG -> ganzes Objekt statt Kante/Flaeche.
                try:
                    self.multi_down = ENABLE_MULTI_PICK and bool(e.ShiftKeyDown)
                    self.object_mode = self._force_object or (
                        ENABLE_OBJECT_MODIFIER and bool(e.ControlKeyDown)
                    )
                except Exception:
                    pass
                # Shift losgelassen, nachdem mind. 1x mit Shift gepickt wurde ->
                # den modalen Pick sauber beenden: PostCustomMessage weckt Get(),
                # der Klick-Loop bricht mit GetResult.CustomMessage ab und gibt das
                # Gesammelte zurueck. Greift beim naechsten Mausmove nach dem
                # Loslassen (es gibt keinen Key-Up-Hook); Enter bleibt der sofortige
                # Weg. NIE crashen.
                try:
                    if (
                        self._multi_session
                        and self._prev_shift
                        and not self.multi_down
                    ):
                        self.PostCustomMessage("multi_done")
                    self._prev_shift = bool(self.multi_down)
                except Exception:
                    pass
                # Banner-Push bei Modifier-Aenderung (geteilt mit dem Idle-Handler,
                # der dasselbe OHNE Mausbewegung tut -> Strg-Lag-Fix; Shift triggert
                # via Ortho intern ein Mausevent, Strg nicht).
                self._maybe_push_modifiers(
                    bool(self.multi_down), bool(self.object_mode)
                )
                # Cursor-Position merken (fuer den Off-Move-Recompute aus dem Timer)
                # + Kandidat/Highlight an dieser Position berechnen.
                self._last_vp = e.Viewport
                self._last_wpt = e.WindowPoint
                self._recompute_candidate(e.Viewport, e.WindowPoint)
            except Exception:
                pass

        def _recompute_candidate(self, viewport, wpt):
            # Kandidat unter (viewport, wpt) bestimmen + Highlight stagen, abhaengig
            # vom AKTUELLEN self.object_mode. Aufgerufen aus OnMouseMove (mit dem
            # Event) UND aus dem Timer bei Modifier-Wechsel OHNE Mausbewegung (mit der
            # zuletzt gemerkten Position) -> das Highlight schaltet sofort um (z.B.
            # Strg: Kante -> ganzes Objekt). Darf den Pick nie crashen.
            try:
                obj_ref = _pick_under_cursor(viewport, wpt)
                if obj_ref is None:
                    if self.cand_objref is not None or self._has_draw():
                        self.cand_objref = None
                        self.cand_type = None
                        self.cand_index = None
                        self.cand_gclass = None
                        self._clear_draw()
                        self._last_key = None
                    return
                if self.object_mode:
                    # Strg -> ganzes Objekt: nicht in Kante/Flaeche aufloesen.
                    resolved = ("object", None, _geometry_class(obj_ref) or "brep")
                else:
                    resolved = _resolve_component_pick(obj_ref, self._requested)
                if resolved is None:
                    self.cand_objref = None
                    self.cand_type = None
                    self.cand_index = None
                    self.cand_gclass = None
                    self._clear_draw()
                    self._last_key = None
                    return
                rtype, ridx, gclass = resolved
                key = self._candidate_key(obj_ref, rtype, ridx)
                # Drossel: gleiche Komponente + schon gezeichnet -> nichts neu bauen
                # (_has_draw-Gate gegen "eingefrorenes leeres Highlight").
                if key is not None and key == self._last_key and self._has_draw():
                    self.cand_objref = obj_ref
                    return
                self.cand_objref = obj_ref
                self.cand_type = rtype
                self.cand_index = ridx
                self.cand_gclass = gclass
                self._last_key = key
                self._rebuild_draw(obj_ref, rtype, ridx, gclass)
            except Exception:
                pass

        def _recompute_offmove(self):
            # Vom Timer bei Modifier-Wechsel OHNE Mausbewegung: Highlight an der
            # zuletzt gemerkten Cursor-Position neu berechnen + Viewport neu zeichnen,
            # damit z.B. Strg sofort von Kante auf ganzes Objekt umschaltet (statt
            # erst beim naechsten Move). Darf den Pick nie crashen.
            try:
                if self._last_vp is None or self._last_wpt is None:
                    return
                self._recompute_candidate(self._last_vp, self._last_wpt)
                try:
                    view = sc.doc.Views.ActiveView
                    if view is not None:
                        view.Redraw()
                except Exception:
                    pass
            except Exception:
                pass

        def _stage_accumulated(self, obj_ref, rtype, ridx, gclass):
            """Eine schon GEWAEHLTE Komponente in die Accumulator-Listen stagen ->
            OnDynamicDraw zeichnet sie duenn mit, sofort sobald gewaehlt (waehrend
            des modalen Picks; der Backend-Conduit kann das erst danach). Nutzt die
            erprobten highlight._stage_*-Funktionen via _DrawSink(acc=True). NICHT
            clearen. Darf nie crashen."""
            try:
                rh_obj = obj_ref.Object()
                geom = rh_obj.Geometry if rh_obj is not None else None
            except Exception:
                geom = None
            if geom is None:
                return
            try:
                from . import highlight as _hl
            except Exception:
                return
            sink = _DrawSink(self, acc=True)
            try:
                if rtype == "object" or ridx is None:
                    _hl._stage_object_wires(sink, geom)
                elif gclass in ("brep", "extrusion"):
                    if rtype == "edge":
                        _hl._stage_brep_edge(sink, geom, int(ridx))
                    elif rtype == "face":
                        _hl._stage_brep_face(sink, geom, int(ridx))
                    else:
                        _hl._stage_object_wires(sink, geom)
                elif gclass == "subd":
                    _hl._stage_subd_component(sink, geom, rtype, int(ridx))
                elif gclass == "mesh":
                    _hl._stage_mesh_component(sink, geom, rtype, int(ridx))
                else:
                    _hl._stage_object_wires(sink, geom)
            except Exception:
                pass

        def OnDynamicDraw(self, e):  # noqa: N802 — Rhino override
            # Kandidat blau zeichnen. Jeder Draw-Call einzeln gekapselt — ein
            # Fehler darf den Render-Loop des Picks NIE stoeren.
            try:
                dp = e.Display
                # Schon GEWAEHLTE Komponenten (Accumulator) DUENN (2px) zeichnen —
                # sofort sichtbar; vor dem dickeren aktuellen Hover.
                for c in self._acc_curves:
                    try:
                        dp.DrawCurve(c, self._blue, 2)
                    except Exception:
                        pass
                for ln in self._acc_lines:
                    try:
                        dp.DrawLine(ln, self._blue, 2)
                    except Exception:
                        pass
                for p in self._acc_points:
                    try:
                        vp = dp.Viewport
                        ok, ppu = vp.GetWorldToScreenScale(p)
                        if ok and ppu > 0:
                            plane = Rhino.Geometry.Plane(p, vp.CameraX, vp.CameraY)
                            circle = Rhino.Geometry.Circle(plane, 5.0 / ppu)
                            dp.DrawCircle(circle, self._blue, 1)
                    except Exception:
                        pass
                for m in self._draw_meshes:
                    try:
                        dp.DrawMeshShaded(m, self._material_blue())
                    except Exception:
                        pass
                for c in self._draw_curves:
                    try:
                        dp.DrawCurve(c, self._blue, 4)
                    except Exception:
                        pass
                for ln in self._draw_lines:
                    try:
                        dp.DrawLine(ln, self._blue, 4)
                    except Exception:
                        pass
                for p in self._draw_points:
                    try:
                        vp = dp.Viewport
                        ok, ppu = vp.GetWorldToScreenScale(p)
                        if ok and ppu > 0:
                            plane = Rhino.Geometry.Plane(
                                p, vp.CameraX, vp.CameraY
                            )
                            circle = Rhino.Geometry.Circle(plane, 7.0 / ppu)
                            dp.DrawCircle(circle, self._blue, 2)
                        else:
                            dp.DrawPoint(
                                p,
                                Rhino.Display.PointStyle.RoundControlPoint,
                                7,
                                self._blue,
                            )
                    except Exception:
                        pass
                # Mehrfachauswahl-Cue: kleines "+" oben rechts am Cursor, solange
                # Shift gehalten wird ("dieser Klick fuegt hinzu") — Konvention wie
                # Rhinos Add-Cursor, minimaler Hinweis (die Erklaerung steht im Panel).
                try:
                    wp = self._last_wpt
                    if self.multi_down and wp is not None:
                        pt = Rhino.Geometry.Point2d(wp.X + 16, wp.Y - 16)
                        dp.Draw2dText("+", self._blue, pt, True, 22)
                except Exception:
                    pass
            except Exception:
                pass

    return _HoverComponentPicker


def _empty_component_result() -> "ComponentPickResult":
    """Leeres ComponentPickResult — identisch zum Abbruch-/Miss-Payload des
    alten GetObject-Pfads."""
    return ComponentPickResult(
        point=None,
        pick_point=None,
        component_type=None,
        component_index=None,
        component_info=None,
        snapshot=None,
        object_id=None,
        object_name=None,
        object_type_name=None,
        object_class=None,
        geometry_class=None,
        allowed_operations=[],
    )


def _pick_component_on_ui_hover(component_type, snapshot_max_size, on_component=None, force_object=False):
    """UI-Thread-Kern des Hover-Pickers: Pre-Select-Abkuerzung (geteilt),
    GetPoint-Subklasse, modaler Get(), Resolve bei Klick, identisches
    ComponentPickResult via _finalize_component_pick. Laeuft im selben
    InvokeOnUiThread/holder-Rahmen wie der alte Pfad (Schalter sitzt in
    _pick_component_on_ui)."""
    global _HOVER_PICKER_CLASS

    # Pre-Select-Abkuerzung: identisch zum alten Pfad.
    pre = rs.SelectedObjects()
    if pre:
        pick_result = _build_pick_result(pre, snapshot_max_size)
        return dict(kind="selection", **pick_result)

    if component_type == "edge":
        prompt = "Kante anvisieren und klicken (Esc = Abbrechen)"
    elif component_type == "face":
        prompt = "Flaeche anvisieren und klicken (Esc = Abbrechen)"
    elif component_type == "vertex":
        prompt = "Vertex anvisieren und klicken (Esc = Abbrechen)"
    else:
        prompt = "Komponente anvisieren und klicken (Esc = Abbrechen)"

    # Modifier-Hinweise an den Prompt haengen (nur die aktiven Flags).
    _hints = []
    if ENABLE_MULTI_PICK:
        _hints.append("Shift=Mehrfach")
    if ENABLE_OBJECT_MODIFIER:
        _hints.append("Strg=Objekt")
    if _hints:
        prompt = prompt + " [" + ", ".join(_hints) + "]"

    if _HOVER_PICKER_CLASS is None:
        _HOVER_PICKER_CLASS = _make_hover_picker_class()
    gp = _HOVER_PICKER_CLASS(component_type, force_object)
    gp.SetCommandPrompt(prompt)
    # Osnap aus: der Hit-Test laeuft jetzt ueber _pick_under_cursor (PickContext),
    # NICHT mehr ueber den GetPoint-Punkt. Osnap-Marker waeren hier nur visuelles
    # Rauschen — aus.
    try:
        gp.PermitObjectSnap(False)
    except Exception:
        pass
    try:
        # Strg-Elevator-Modus AUS: sonst frisst Rhino den ersten Strg+Klick (er
        # setzt den Elevator-Basispunkt, der zweite Klick die Hoehe) -> man braucht
        # zwei Klicks. Strg ist bei uns der Objekt-Modifier -> ein Klick muss
        # genuegen. 0 = kein Elevator-Modus.
        gp.PermitElevatorMode(0)
    except Exception:
        pass
    try:
        gp.AcceptNothing(True)  # Enter = Abbruch (wie der GetObject-Pfad)
    except Exception:
        pass
    try:
        # Erlaubt, den modalen Pick aus OnMouseMove heraus zu beenden (Shift
        # losgelassen -> PostCustomMessage -> Get() liefert CustomMessage).
        gp.AcceptCustomMessage(True)
    except Exception:
        pass
    if HOVER_PICKER_DEBUG:
        logger.info("hover picker aktiv (component_type=%s)", component_type)

    # Klick-Loop: normalerweise EIN Klick -> ein Ergebnis (unveraendertes
    # Verhalten). Mit gehaltener SHIFT-Taste (self.multi_down, in OnMouseMove
    # gesetzt) bleibt der Pick offen und sammelt weitere Komponenten, bis ein
    # Klick OHNE Shift oder Enter folgt. Esc verwirft komplett. Der gecachte
    # Kandidat ist frisch (OnMouseMove feuert direkt vor dem Klick).
    # Live-Modifier-Hinweis auch OHNE Mausbewegung: Rhino feuert KEIN Event, wenn man
    # im Pick nur eine Taste HÄLT (OnMouseMove braucht eine Bewegung; RhinoApp.Idle
    # feuert im ruhenden modalen GetPoint nicht zuverlaessig). Loesung: aktiv pollen
    # per WinForms-Timer auf dem UI-Thread — dessen WM_TIMER wird von der modalen
    # Get-Schleife gepumpt (wie die Mausevents), und Control.ModifierKeys ist auf dem
    # UI-Thread zuverlaessig. Liest die Modifier zyklisch + pusht bei Aenderung
    # (Strg UND Shift, sofort, ohne Move). Wird nach dem Pick gestoppt + disposed.
    _mod_timer = None

    def _poll_modifiers(sender, e):
        try:
            _m = System.Windows.Forms.Control.ModifierKeys
            _k = System.Windows.Forms.Keys
            cur_multi = bool(ENABLE_MULTI_PICK and (_m & _k.Shift) == _k.Shift)
            if force_object:
                cur_object = True
            else:
                cur_object = bool(
                    ENABLE_OBJECT_MODIFIER and (_m & _k.Control) == _k.Control
                )
            if gp._maybe_push_modifiers(cur_multi, cur_object):
                # Modifier hat sich OHNE Mausbewegung geaendert -> self-State
                # nachziehen + Highlight an der letzten Cursor-Position sofort
                # umschalten (z.B. Strg: Kante -> ganzes Objekt).
                gp.multi_down = cur_multi
                gp.object_mode = cur_object
                gp._recompute_offmove()
        except Exception:
            pass

    def _stop_idle():
        try:
            if _mod_timer is not None:
                _mod_timer.Stop()
                _mod_timer.Dispose()
        except Exception:
            pass

    try:
        _mod_timer = System.Windows.Forms.Timer()
        _mod_timer.Interval = 60  # ~16x/s; reicht fuer eine fluessige Modus-Anzeige
        _mod_timer.Tick += _poll_modifiers
        _mod_timer.Start()
    except Exception:
        _mod_timer = None

    results = []
    seen = set()
    while True:
        rc = gp.Get()
        if rc == Rhino.Input.GetResult.Cancel:
            # Esc -> komplett abbrechen (auch bereits Gesammeltes verwerfen).
            _stop_idle()
            return _empty_component_result()
        if rc != Rhino.Input.GetResult.Point:
            break  # Enter / Nothing / Miss -> mit dem Gesammelten abschliessen
        obj_ref = gp.cand_objref
        multi = bool(getattr(gp, "multi_down", False))
        object_mode = bool(getattr(gp, "object_mode", False))
        # Modifier beim Klick FRISCH lesen: object_mode/multi_down werden nur in
        # OnMouseMove gesetzt (braucht Mausbewegung). Wer Strg/Shift drueckt OHNE
        # die Maus zu bewegen und dann klickt, haette sonst den veralteten Wert
        # -> erst der zweite Klick (nach Mausbewegung) griffe. Daher hier die
        # aktuellen Modifier direkt abfragen (WinForms; defensiv, kein Crash).
        try:
            _mods = System.Windows.Forms.Control.ModifierKeys
            _keys = System.Windows.Forms.Keys
            if force_object:
                object_mode = True
            elif ENABLE_OBJECT_MODIFIER:
                object_mode = (_mods & _keys.Control) == _keys.Control
            if ENABLE_MULTI_PICK:
                multi = (_mods & _keys.Shift) == _keys.Shift
        except Exception:
            pass
        if obj_ref is not None:
            if object_mode:
                resolved = ("object", None, _geometry_class(obj_ref) or "brep")
            else:
                resolved = _resolve_component_pick(obj_ref, component_type)
            if resolved is not None:
                res = _finalize_component_pick(
                    obj_ref,
                    resolved[0],
                    resolved[1],
                    resolved[2],
                    snapshot_max_size,
                    # Mehrfachauswahl (Shift gehalten): kein Snapshot je Pick ->
                    # Hover bleibt fluessig (kein UI-Thread-blockierendes Rendern
                    # zwischen den Picks). Einzel-Pick behaelt den Snapshot.
                    capture_snapshot=(not multi),
                )
                key = (
                    res.get("object_id"),
                    res.get("component_type"),
                    res.get("component_index"),
                )
                if key not in seen:
                    seen.add(key)
                    results.append(res)
                    # Mehrfachauswahl: die gerade gewaehlte Komponente live (duenn)
                    # im Picker mitzeichnen -> sofort blau, sobald gewaehlt (der
                    # Backend-Conduit kann waehrend des modalen Picks nicht zeichnen).
                    if multi:
                        try:
                            gp._stage_accumulated(
                                obj_ref, resolved[0], resolved[1], resolved[2]
                            )
                        except Exception:
                            pass
                    # LIVE rausschicken: jede gewaehlte Komponente sofort an den
                    # Event-Loop melden (statt am Ende gebuendelt) -> erscheint
                    # direkt als Token im Chat. Darf den Pick NIE crashen.
                    if on_component is not None:
                        try:
                            on_component(res)
                        except Exception:
                            pass
        if not multi:
            break  # normaler Einzelklick (oder Multi-Modus aus) -> fertig
        # Shift gehalten -> Pick bleibt offen, naechste Komponente sammeln.
        # Ab jetzt beendet Shift-Loslassen den Pick (siehe OnMouseMove).
        gp._multi_session = True

    _stop_idle()  # Idle-Handler abmelden (deckt alle break-Ausgaenge + Post-Loop-Returns)
    if not results:
        return _empty_component_result()
    if on_component is not None:
        # Schon live (inkrementell) gestreamt -> der Aufrufer broadcastet nicht
        # nochmal (sonst Doppel-Tokens). Sentinel statt der Komponentenliste.
        return {"kind": "streamed"}
    if len(results) == 1:
        return results[0]
    return {"kind": "components", "items": results}


def _component_label(
    component_type: Literal["edge", "face", "vertex", "auto"],
) -> str:
    if component_type == "edge":
        return "Kante"
    if component_type == "face":
        return "Flaeche"
    if component_type == "vertex":
        return "Vertex"
    return "Komponente"


def _component_type_from_index(component_index) -> Optional[str]:
    try:
        enum_name = str(component_index.ComponentIndexType.ToString())
    except Exception:
        enum_name = str(component_index.ComponentIndexType)
    lower = enum_name.lower()
    if "edge" in lower:
        return "edge"
    if "face" in lower or "surface" in lower:
        return "face"
    if "vertex" in lower:
        return "vertex"
    return None


def _geometry_class(obj_ref) -> Optional[str]:
    """Coarse geometry class of the picked host object.

    Returns one of ``"brep"`` / ``"extrusion"`` / ``"subd"`` / ``"mesh"`` or
    ``None`` when the object/geometry cannot be resolved. This is the dispatch
    key: Brep/Extrusion use the typed ``ObjRef.Edge()/.Face()`` accessors,
    while SubD and Mesh must be resolved from the ``GeometryComponentIndex``
    (those accessors are Brep-only and return ``None`` otherwise).
    """
    try:
        obj = obj_ref.Object()
        if obj is None:
            return None
        geom = obj.Geometry
    except Exception:
        return None
    if geom is None:
        return None
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


def _nearest_face_index(brep, pt) -> Optional[int]:
    """Index der Brep-Flaeche unter/naechst zu ``pt``.

    Primaer ueber ``Brep.ClosestPoint`` mit ComponentIndex — laut Doku
    "searches all Brep faces looking for the one closest to testPoint" und liefert
    eine BrepFace- bzw. BrepEdge-ComponentIndex. Das ist robust auch fuer
    konvertierte Extrusions (die Pro-Face-Iteration unten lieferte dort ``None``
    -> der Pick fiel auf die Kante zurueck, Flaechen waren nicht waehlbar).
    Faellt auf die Pro-Face-Iteration zurueck, falls die ci-Variante nichts gibt.
    """
    # Brep.ClosestPoint(testPoint, out cp, out ci, out s, out t, maxDist, out n)
    # -> pythonnet: Rueckgabe (bool, cp, ci, s, t, n); IN-Param maxDist=0 (=kein
    # Limit). Liegt der Treffer auf einer Flaeche, ist ci.Index der Face-Index.
    try:
        res = brep.ClosestPoint(pt, 0.0)
        if res and res[0]:
            ci = res[2]
            if (
                ci is not None
                and ci.ComponentIndexType
                == Rhino.Geometry.ComponentIndexType.BrepFace
            ):
                fi = ci.Index
                if fi is not None and int(fi) >= 0:
                    return int(fi)
    except Exception:
        pass

    # Fallback: Pro-Face-Iteration (naechster Punkt je Flaeche).
    best_i: Optional[int] = None
    best_d: Optional[float] = None
    try:
        for i in range(brep.Faces.Count):
            face = brep.Faces[i]
            rc = face.ClosestPoint(pt)
            # ClosestPoint gibt (bool, u, v) zurueck.
            if not rc or not rc[0]:
                continue
            d = face.PointAt(rc[1], rc[2]).DistanceTo(pt)
            if best_d is None or d < best_d:
                best_d, best_i = d, i
    except Exception:
        return None
    return best_i


def _extrusion_component_fallback(
    obj_ref,
    requested_type: Literal["edge", "face", "vertex", "auto"],
) -> Optional[tuple[Literal["edge", "face"], int]]:
    """Loest eine Kante/Flaeche an einer EXTRUSION ueber ihre Brep-Form auf.

    Hintergrund: Die typisierten ``ObjRef.Edge()/.Face()``-Zugriffe greifen bei
    Extrusions oft nicht (sie liefern nichts -> der Pick fiel aufs ganze Objekt
    zurueck). Diese Aufloesung nutzt ``Brep.TryConvertBrep`` — exakt die
    Konvertierung, die fillet/chamfer/round_edges intern verwenden -> der
    zurueckgegebene Index passt zu dem, worauf die Operation wirkt, und sie
    waehlt die Brep-Komponente, die dem Klickpunkt
    (``SelectionPoint``) am naechsten liegt. NICHT-MUTIEREND: die Brep ist eine
    Wegwerf-Kopie, das Dokument-Objekt bleibt eine Extrusion.
    """
    try:
        obj = obj_ref.Object()
        geom = obj.Geometry if obj is not None else None
        if not isinstance(geom, Rhino.Geometry.Extrusion):
            return None
        sel = obj_ref.SelectionPoint()
        if sel is None or not sel.IsValid:
            return None
        brep = Rhino.Geometry.Brep.TryConvertBrep(geom)
        return _nearest_component_on_brep(brep, sel, requested_type)
    except Exception:
        return None


def _nearest_component_on_brep(
    brep,
    sel,
    requested_type: Literal["edge", "face", "vertex", "auto"],
) -> Optional[tuple[Literal["edge", "face"], int]]:
    """Punktbasierte Aufloesung: bestimmt die Brep-Kante/Flaeche, die dem
    Trefferpunkt ``sel`` am naechsten liegt. Geteilter Kern fuer Extrusion (ueber
    TryConvertBrep) UND echte Breps.

    Notwendig, weil PickObjects (PickContext/PointPick) NIE einen Sub-Komponenten-
    Index liefert (Live-Log: durchgaengig 'pick refs (1): InvalidType#-1') — die
    Komponente wird daher geometrisch aus dem Klickpunkt bestimmt, exakt im Sinne
    des Markierens ("zeig auf die Stelle")."""
    try:
        if (
            brep is None
            or sel is None
            or brep.Edges is None
            or brep.Edges.Count == 0
        ):
            return None

        best_edge_i = -1
        best_edge_d: Optional[float] = None
        for i in range(brep.Edges.Count):
            ec = brep.Edges[i]
            rc = ec.ClosestPoint(sel)
            # Curve.ClosestPoint gibt (bool, t) zurueck.
            if not rc or not rc[0]:
                continue
            d = ec.PointAt(rc[1]).DistanceTo(sel)
            if best_edge_d is None or d < best_edge_d:
                best_edge_d, best_edge_i = d, i

        if requested_type == "edge":
            return ("edge", best_edge_i) if best_edge_i >= 0 else None
        if requested_type == "face":
            fi = _nearest_face_index(brep, sel)
            return ("face", fi) if fi is not None else None

        # auto / vertex: Kante, wenn der Klick praktisch AUF einer Kante liegt,
        # sonst die getroffene Flaeche (der Klick liegt ohnehin auf einer Flaeche).
        bbox = brep.GetBoundingBox(True)
        diag = bbox.Diagonal.Length if bbox.IsValid else 0.0
        edge_thresh = diag * 0.015 if diag > 0 else 0.0
        near_edge = (
            best_edge_i >= 0
            and best_edge_d is not None
            and (edge_thresh <= 0.0 or best_edge_d <= edge_thresh)
        )
        if near_edge:
            return ("edge", best_edge_i)
        fi = _nearest_face_index(brep, sel)
        if fi is not None:
            return ("face", fi)
        if best_edge_i >= 0:
            return ("edge", best_edge_i)
        return None
    except Exception:
        return None


def _brep_component_fallback(
    obj_ref,
    requested_type: Literal["edge", "face", "vertex", "auto"],
) -> Optional[tuple[Literal["edge", "face"], int]]:
    """Punktbasierte Kante/Flaeche fuer ECHTE Breps — das Pendant zum
    Extrusion-Fallback. Noetig, weil PickObjects fuer Breps nie einen
    Sub-Komponenten-Index liefert (Live-Log: 'pick refs (1): InvalidType#-1'),
    der typisierte ObjRef.Edge()/.Face()-Pfad also leer ausgeht und der Pick auf
    'object' fiel. Wir nehmen den Trefferpunkt (SelectionPoint) und bestimmen die
    naechste Kante/Flaeche direkt auf der Brep-Geometrie. Nicht-mutierend."""
    try:
        obj = obj_ref.Object()
        geom = obj.Geometry if obj is not None else None
        if not isinstance(geom, Rhino.Geometry.Brep):
            return None
        sel = obj_ref.SelectionPoint()
        if sel is None or not sel.IsValid:
            return None
        return _nearest_component_on_brep(geom, sel, requested_type)
    except Exception:
        return None


def _resolve_component_pick(
    obj_ref,
    requested_type: Literal["edge", "face", "vertex", "auto"],
) -> Optional[
    tuple[Literal["edge", "face", "vertex", "object"], Optional[int], Optional[str]]
]:
    """Resolve the picked subobject and classify the host geometry.

    Returns ``(component_type, component_index, geometry_class)`` or ``None``
    when nothing usable was picked. The geometry class is the dispatch key:

    * ``brep`` / ``extrusion`` — Brep selection menu often offers
      ``boundary``/``edge``/``surface``/``object`` at the same click. The raw
      ``GeometryComponentIndex`` can be too strict (a ``boundary``/trim is
      rejected although it resolves to a real edge), so the typed
      ``ObjRef.Edge()/.Face()`` accessors are preferred (unchanged behaviour).
    * ``subd`` / ``mesh`` — ``ObjRef.Edge()/.Face()`` are Brep-only and would
      return ``None``, so the component is read straight from
      ``GeometryComponentIndex`` (SubdEdge/SubdFace/SubdVertex resp.
      MeshTopologyEdge/MeshFace/MeshTopologyVertex).

    ``object`` is the fallback for whole-object picks / unknown classes.
    """
    gclass = _geometry_class(obj_ref)

    # --- SubD / Mesh: resolve from the component index directly ---
    if gclass in ("subd", "mesh"):
        return _resolve_indexed_component_pick(obj_ref, requested_type, gclass)

    # --- Extrusion: resolve via the Brep form up front ---
    # Die typisierten ObjRef.Edge()/.Face()-Zugriffe greifen bei Extrusions
    # unzuverlaessig (-> Pick fiel auf "object" zurueck, einzelne Kanten waren
    # nicht waehlbar). Die Brep-Form (Brep.TryConvertBrep, index-gleich zu
    # fillet/chamfer/round) + naechste Komponente zum Klickpunkt loest das
    # zuverlaessig + nicht-mutierend. Schlaegt sie fehl, faellt es auf die
    # bestehende typisierte Logik zurueck (keine Regression).
    if gclass == "extrusion":
        fb = _extrusion_component_fallback(obj_ref, requested_type)
        if fb is not None:
            return (fb[0], fb[1], gclass)

    # --- Brep: same point-based resolution as Extrusion ---
    # PickObjects liefert fuer Breps nie einen Sub-Index (Live-Log durchgaengig
    # 'pick refs (1): InvalidType#-1'), daher geht der typisierte Edge()/Face()-
    # Pfad unten leer aus und der Pick fiel auf 'object'. Wir loesen die
    # Komponente punktbasiert aus dem Trefferpunkt auf (wie bei der Extrusion).
    # Schlaegt es fehl, faellt es auf den typisierten Pfad zurueck (keine
    # Regression).
    if gclass == "brep":
        fb = _brep_component_fallback(obj_ref, requested_type)
        if fb is not None:
            return (fb[0], fb[1], gclass)

    # --- Brep / Extrusion (and unknown): typed accessor path (unchanged) ---
    comp_index = getattr(obj_ref, "GeometryComponentIndex", None)

    if requested_type in ("auto", "vertex"):
        # Brep vertices are not an edit target here; treat a vertex request on
        # a Brep as "auto" so the user still gets a usable edge/face/object.
        raw_type = (
            _component_type_from_index(comp_index)
            if comp_index is not None
            else None
        )
        if raw_type == "edge":
            requested_type = "edge"
        elif raw_type == "face":
            requested_type = "face"
        else:
            requested_type = "auto"

    try:
        if requested_type == "edge":
            edge = obj_ref.Edge()
            if edge is not None:
                edge_index = getattr(edge, "EdgeIndex", None)
                if edge_index is not None and edge_index >= 0:
                    return ("edge", int(edge_index), gclass)
            trim = obj_ref.Trim()
            if trim is not None:
                trim_edge = getattr(trim, "Edge", None)
                if trim_edge is not None:
                    edge_index = getattr(trim_edge, "EdgeIndex", None)
                    if edge_index is not None and edge_index >= 0:
                        return ("edge", int(edge_index), gclass)
        elif requested_type == "face":
            face = obj_ref.Face()
            if face is not None:
                face_index = getattr(face, "FaceIndex", None)
                if face_index is not None and face_index >= 0:
                    return ("face", int(face_index), gclass)
    except Exception:
        return None

    if requested_type == "auto":
        try:
            edge = obj_ref.Edge()
            if edge is not None:
                edge_index = getattr(edge, "EdgeIndex", None)
                if edge_index is not None and edge_index >= 0:
                    return ("edge", int(edge_index), gclass)
            trim = obj_ref.Trim()
            if trim is not None:
                trim_edge = getattr(trim, "Edge", None)
                if trim_edge is not None:
                    edge_index = getattr(trim_edge, "EdgeIndex", None)
                    if edge_index is not None and edge_index >= 0:
                        return ("edge", int(edge_index), gclass)
            face = obj_ref.Face()
            if face is not None:
                face_index = getattr(face, "FaceIndex", None)
                if face_index is not None and face_index >= 0:
                    return ("face", int(face_index), gclass)
        except Exception:
            return None
        try:
            oid = obj_ref.ObjectId
            if oid and oid != System.Guid.Empty:
                return ("object", None, gclass)
        except Exception:
            return None
        return None

    if comp_index is None:
        return None
    raw_type = _component_type_from_index(comp_index)
    if raw_type != requested_type:
        return None
    try:
        raw_index = int(comp_index.Index)
    except Exception:
        return None
    return (requested_type, raw_index, gclass)


def _resolve_indexed_component_pick(
    obj_ref,
    requested_type: Literal["edge", "face", "vertex", "auto"],
    gclass: str,
) -> Optional[
    tuple[Literal["edge", "face", "vertex", "object"], Optional[int], Optional[str]]
]:
    """Resolve a SubD/Mesh sub-component straight from the component index.

    SubD: SubdEdge/SubdFace/SubdVertex. Mesh: MeshTopologyEdge/MeshFace/
    MeshTopologyVertex. Falls back to a whole-object pick when no usable
    sub-component index is present (e.g. the user picked the body, not a
    face/edge/vertex). ``requested_type`` filters: if the user asked for a
    specific kind and the pick resolved to something else, fall through to
    object rather than silently returning the wrong kind.
    """
    comp_index = getattr(obj_ref, "GeometryComponentIndex", None)
    raw_type = (
        _component_type_from_index(comp_index) if comp_index is not None else None
    )

    if raw_type in ("edge", "face", "vertex"):
        # Vertex ist KEIN K/F/O-Ziel mehr: unter "auto" (= der K/F/O-Picker) nur
        # Kante/Flaeche zulassen. Vertices laufen ueber den Punkt-Picker
        # (Crosshair via Osnap, Snap "Endpunkt"/"Vertex"). Ein expliziter
        # "vertex"-Request (dormanter Modus) bleibt erlaubt; ein Vertex-Klick
        # unter "auto" faellt unten auf die Whole-Object-Referenz durch.
        type_ok = requested_type == raw_type or (
            requested_type == "auto" and raw_type in ("edge", "face")
        )
        if type_ok:
            try:
                raw_index = int(comp_index.Index)
            except Exception:
                raw_index = None
            if raw_index is not None and raw_index >= 0:
                return (raw_type, raw_index, gclass)

    # No usable sub-component (or kind mismatch) -> whole-object reference.
    try:
        oid = obj_ref.ObjectId
        if oid and oid != System.Guid.Empty:
            return ("object", None, gclass)
    except Exception:
        return None
    return None


def _component_midpoint(
    obj_ref,
    component_type: Literal["edge", "face", "vertex", "object"],
    geometry_class: Optional[str] = None,
    index: Optional[int] = None,
) -> Optional[list[float]]:
    try:
        if geometry_class in ("subd", "mesh"):
            idx = _picked_index(obj_ref)
            pos = _indexed_component_position(
                obj_ref, component_type, geometry_class, idx
            )
            if pos is not None:
                return pos
        elif geometry_class == "extrusion" and index is not None:
            # Extrusion: ueber dieselbe TryConvertBrep-Form + den aufgeloesten
            # Index wie der Pick (nicht ueber die fuer Extrusions unzuverlaessigen
            # obj_ref.Edge()/.Face()), damit der Referenzpunkt zur gepickten
            # Kante/Flaeche passt. Faellt sonst defensiv auf bbox-Center.
            pos = _brep_indexed_component_position(
                _extrusion_brep(obj_ref), component_type, index
            )
            if pos is not None:
                return pos
        else:
            if component_type == "edge":
                edge = obj_ref.Edge()
                if edge is not None:
                    dom = edge.Domain
                    t = 0.5 * (dom.T0 + dom.T1)
                    pt = edge.PointAt(t)
                    return [float(pt.X), float(pt.Y), float(pt.Z)]
            if component_type == "face":
                face = obj_ref.Face()
                if face is not None:
                    du = face.Domain(0)
                    dv = face.Domain(1)
                    pt = face.PointAt(0.5 * (du.T0 + du.T1), 0.5 * (dv.T0 + dv.T1))
                    return [float(pt.X), float(pt.Y), float(pt.Z)]
        obj = obj_ref.Object()
        if obj is not None and obj.Geometry is not None:
            bbox = obj.Geometry.GetBoundingBox(True)
            if bbox.IsValid:
                pt = bbox.Center
                return [float(pt.X), float(pt.Y), float(pt.Z)]
    except Exception:
        return None
    return None


def _picked_index(obj_ref) -> Optional[int]:
    """Index of the picked sub-component (``GeometryComponentIndex.Index``)."""
    comp_index = getattr(obj_ref, "GeometryComponentIndex", None)
    if comp_index is None:
        return None
    try:
        idx = int(comp_index.Index)
    except Exception:
        return None
    return idx if idx >= 0 else None


def _subd_geometry(obj_ref):
    try:
        geom = obj_ref.Object().Geometry
    except Exception:
        return None
    return geom if isinstance(geom, Rhino.Geometry.SubD) else None


def _mesh_geometry(obj_ref):
    try:
        geom = obj_ref.Object().Geometry
    except Exception:
        return None
    return geom if isinstance(geom, Rhino.Geometry.Mesh) else None


def _extrusion_brep(obj_ref):
    """``Brep.TryConvertBrep`` der gepickten Extrusion — dieselbe Konvertierung
    wie ``_extrusion_component_fallback``, die Edit-Operationen (fillet/chamfer/
    round) und das Highlight (``_as_brep``). So beziehen sich component_index,
    component_info, midpoint und Highlight auf DIESELBE Brep -> derselbe Index =
    dieselbe Kante/Flaeche. None, wenn das Objekt keine Extrusion ist."""
    try:
        geom = obj_ref.Object().Geometry
    except Exception:
        return None
    if not isinstance(geom, Rhino.Geometry.Extrusion):
        return None
    try:
        return Rhino.Geometry.Brep.TryConvertBrep(geom)
    except Exception:
        return None


def _brep_indexed_component_position(brep, component_type, idx):
    """Mittelpunkt von ``brep.Edges[idx]`` / ``brep.Faces[idx]`` (Extrusion-
    Picks, aufgeloest ueber dieselbe TryConvertBrep-Form wie der Index)."""
    if brep is None or idx is None or idx < 0:
        return None
    try:
        if component_type == "edge":
            if idx >= brep.Edges.Count:
                return None
            edge = brep.Edges[idx]
            dom = edge.Domain
            pt = edge.PointAt(0.5 * (dom.T0 + dom.T1))
            return [float(pt.X), float(pt.Y), float(pt.Z)]
        if component_type == "face":
            if idx >= brep.Faces.Count:
                return None
            face = brep.Faces[idx]
            du = face.Domain(0)
            dv = face.Domain(1)
            pt = face.PointAt(0.5 * (du.T0 + du.T1), 0.5 * (dv.T0 + dv.T1))
            return [float(pt.X), float(pt.Y), float(pt.Z)]
    except Exception:
        return None
    return None


def _brep_indexed_component_info(brep, component_type, idx):
    """``component_info`` fuer eine Extrusion-Kante/-Flaeche, aufgeloest ueber
    ``brep.Edges[idx]``/``brep.Faces[idx]`` derselben TryConvertBrep-Form, aus
    der der ``component_index`` stammt. KEIN ``index_mismatch``-Flag: der Index
    ist per Konstruktion mit den Edit-Operationen aligned (alle nutzen
    TryConvertBrep), eine Mismatch-Warnung waere hier irrefuehrend."""
    if brep is None or idx is None or idx < 0:
        return None
    try:
        if component_type == "edge":
            if idx >= brep.Edges.Count:
                return None
            edge = brep.Edges[idx]
            dom = edge.Domain
            t = 0.5 * (dom.T0 + dom.T1)
            mid = edge.PointAt(t)
            tan = edge.TangentAt(t)
            adjacent_faces = []
            try:
                for fi in edge.AdjacentFaces():
                    adjacent_faces.append(int(fi))
            except Exception:
                pass
            return {
                "start_point": _xyz(edge.PointAtStart),
                "end_point": _xyz(edge.PointAtEnd),
                "midpoint": _xyz(mid),
                "length": float(edge.GetLength()),
                "tangent": [float(tan.X), float(tan.Y), float(tan.Z)],
                "adjacent_face_indices": adjacent_faces,
                "total_edges": int(brep.Edges.Count),
            }
        if component_type == "face":
            if idx >= brep.Faces.Count:
                return None
            face = brep.Faces[idx]
            du = face.Domain(0)
            dv = face.Domain(1)
            u = 0.5 * (du.T0 + du.T1)
            v = 0.5 * (dv.T0 + dv.T1)
            pt = face.PointAt(u, v)
            normal = face.NormalAt(u, v)
            if face.OrientationIsReversed:
                normal.Reverse()
            adjacent_edges = []
            try:
                for loop in face.Loops:
                    for trim in loop.Trims:
                        if trim.Edge is not None:
                            ei = int(trim.Edge.EdgeIndex)
                            if ei not in adjacent_edges:
                                adjacent_edges.append(ei)
            except Exception:
                pass
            info = {
                "midpoint": _xyz(pt),
                "normal": [float(normal.X), float(normal.Y), float(normal.Z)],
                "is_planar": bool(face.IsPlanar(sc.doc.ModelAbsoluteTolerance)),
                "adjacent_edge_indices": adjacent_edges,
                "total_faces": int(brep.Faces.Count),
            }
            try:
                amp = Rhino.Geometry.AreaMassProperties.Compute(
                    face.DuplicateFace(False)
                )
                if amp:
                    info["area"] = float(amp.Area)
            except Exception:
                pass
            return info
    except Exception:
        return None
    return None


def _indexed_component_position(
    obj_ref,
    component_type: Literal["edge", "face", "vertex", "object"],
    geometry_class: str,
    idx: Optional[int],
) -> Optional[list[float]]:
    """Reference point of a SubD/Mesh edge/face/vertex by index, or None."""
    if idx is None:
        return None
    try:
        if geometry_class == "subd":
            subd = _subd_geometry(obj_ref)
            if subd is None:
                return None
            if component_type == "edge":
                edge = subd.Edges.Find(idx)
                if edge is not None:
                    pt = edge.EvaluatePoint(0.5)
                    return [float(pt.X), float(pt.Y), float(pt.Z)]
            elif component_type == "face":
                face = subd.Faces.Find(idx)
                if face is not None:
                    pt = face.ControlNetCenterPoint
                    return [float(pt.X), float(pt.Y), float(pt.Z)]
            elif component_type == "vertex":
                vert = subd.Vertices.Find(idx)
                if vert is not None:
                    pt = vert.ControlNetPoint
                    return [float(pt.X), float(pt.Y), float(pt.Z)]
        elif geometry_class == "mesh":
            mesh = _mesh_geometry(obj_ref)
            if mesh is None:
                return None
            if component_type == "edge":
                line = mesh.TopologyEdges.EdgeLine(idx)
                pt = line.PointAt(0.5)
                return [float(pt.X), float(pt.Y), float(pt.Z)]
            elif component_type == "face":
                pt = mesh.Faces.GetFaceCenter(idx)
                return [float(pt.X), float(pt.Y), float(pt.Z)]
            elif component_type == "vertex":
                pt = mesh.TopologyVertices[idx]
                return [float(pt.X), float(pt.Y), float(pt.Z)]
    except Exception:
        return None
    return None


def _xyz(pt) -> list[float]:
    return [float(pt.X), float(pt.Y), float(pt.Z)]


def _doc_brep(obj_ref):
    """Return the Brep of the picked object as it lives in the document.

    Rhino resolves sub-object picks (``obj_ref.Edge()`` / ``.Face()``)
    against an internally kinky-split *copy* of the Brep, so the component
    index can refer to a Brep whose edge/face count differs from the one
    stored in the document. To detect that, we need the doc-side Brep.
    Uses the Brep direct / Extrusion-via-ToBrep geometry handling pattern.
    """
    try:
        obj = obj_ref.Object()
        if obj is None:
            return None
        geom = obj.Geometry
        if isinstance(geom, Rhino.Geometry.Brep):
            return geom
        if isinstance(geom, Rhino.Geometry.Extrusion):
            try:
                return geom.ToBrep(False)
            except Exception:
                return None
    except Exception:
        return None
    return None


def _component_info(
    obj_ref,
    component_type: Literal["edge", "face", "vertex", "object"],
    geometry_class: Optional[str] = None,
    index: Optional[int] = None,
) -> Optional[dict[str, Any]]:
    if geometry_class in ("subd", "mesh"):
        return _indexed_component_info(obj_ref, component_type, geometry_class)
    # Extrusion: component_info aus DERSELBEN TryConvertBrep-Form + Index wie
    # der component_index (nicht aus den fuer Extrusions unzuverlaessigen
    # obj_ref.Edge()/.Face()), sonst koennten midpoint/normal/length eine
    # ANDERE Komponente beschreiben als der Index meint (-> falsche
    # move-Richtung, irrefuehrende Mismatch-Warnung). Kein index_mismatch-Flag.
    if (
        geometry_class == "extrusion"
        and index is not None
        and component_type in ("edge", "face")
    ):
        ext_info = _brep_indexed_component_info(
            _extrusion_brep(obj_ref), component_type, index
        )
        if ext_info is not None:
            return ext_info
        # defensiv: faellt auf den typisierten Pfad unten zurueck
    try:
        if component_type == "edge":
            edge = obj_ref.Edge()
            if edge is None:
                return None
            dom = edge.Domain
            t = 0.5 * (dom.T0 + dom.T1)
            mid = edge.PointAt(t)
            tan = edge.TangentAt(t)
            adjacent_faces: list[int] = []
            try:
                for fi in edge.AdjacentFaces():
                    adjacent_faces.append(int(fi))
            except Exception:
                pass
            edge_info: dict[str, Any] = {
                "start_point": _xyz(edge.PointAtStart),
                "end_point": _xyz(edge.PointAtEnd),
                "midpoint": _xyz(mid),
                "length": float(edge.GetLength()),
                "tangent": [float(tan.X), float(tan.Y), float(tan.Z)],
                "adjacent_face_indices": adjacent_faces,
            }
            # The picked index is relative to ``edge.Brep`` (the internally
            # split copy Rhino resolves the pick against). Compare its edge
            # count to the doc-Brep so the model knows whether the index is
            # usable. We deliberately do NOT remap — the picked edge may not
            # exist in the doc-Brep at all.
            try:
                total_edges = int(edge.Brep.Edges.Count)
                edge_info["total_edges"] = total_edges
                doc_brep = _doc_brep(obj_ref)
                if doc_brep is not None and doc_brep.Edges is not None:
                    doc_edge_count = int(doc_brep.Edges.Count)
                    edge_info["doc_edge_count"] = doc_edge_count
                    edge_info["index_mismatch"] = doc_edge_count != total_edges
            except Exception:
                pass
            return edge_info
        if component_type == "face":
            face = obj_ref.Face()
            if face is None:
                return None
            du = face.Domain(0)
            dv = face.Domain(1)
            u = 0.5 * (du.T0 + du.T1)
            v = 0.5 * (dv.T0 + dv.T1)
            pt = face.PointAt(u, v)
            normal = face.NormalAt(u, v)
            if face.OrientationIsReversed:
                normal.Reverse()
            adjacent_edges: list[int] = []
            try:
                for loop in face.Loops:
                    for trim in loop.Trims:
                        if trim.Edge is not None:
                            idx = int(trim.Edge.EdgeIndex)
                            if idx not in adjacent_edges:
                                adjacent_edges.append(idx)
            except Exception:
                pass
            info: dict[str, Any] = {
                "midpoint": _xyz(pt),
                "normal": [float(normal.X), float(normal.Y), float(normal.Z)],
                "is_planar": bool(face.IsPlanar(sc.doc.ModelAbsoluteTolerance)),
                "adjacent_edge_indices": adjacent_edges,
            }
            try:
                amp = Rhino.Geometry.AreaMassProperties.Compute(face.DuplicateFace(False))
                if amp:
                    info["area"] = float(amp.Area)
            except Exception:
                pass
            # Same split-copy caveat as edges: the face index is relative to
            # ``face.Brep``. Surface the doc-Brep face count + mismatch flag.
            try:
                total_faces = int(face.Brep.Faces.Count)
                info["total_faces"] = total_faces
                doc_brep = _doc_brep(obj_ref)
                if doc_brep is not None and doc_brep.Faces is not None:
                    doc_face_count = int(doc_brep.Faces.Count)
                    info["doc_face_count"] = doc_face_count
                    info["index_mismatch"] = doc_face_count != total_faces
            except Exception:
                pass
            return info
        obj = obj_ref.Object()
        if obj is None or obj.Geometry is None:
            return None
        bbox = obj.Geometry.GetBoundingBox(True)
        if not bbox.IsValid:
            return None
        return {
            "bbox_min": _xyz(bbox.Min),
            "bbox_max": _xyz(bbox.Max),
            "bbox_center": _xyz(bbox.Center),
        }
    except Exception:
        return None


def _indexed_component_info(
    obj_ref,
    component_type: Literal["edge", "face", "vertex", "object"],
    geometry_class: str,
) -> Optional[dict[str, Any]]:
    """component_info for SubD/Mesh sub-components.

    Surfaces the reference point under both ``position`` and ``midpoint`` (so
    the existing face-summary path renders), plus ``bbox_center`` (so the
    object/else summary path renders too), and the relevant total count
    (``total_edges`` / ``total_faces`` / ``total_vertices``) for later
    out-of-range detection. Unlike Breps there is no internally-split copy, so
    no ``doc_*_count`` / ``index_mismatch`` comparison is needed.
    """
    try:
        idx = _picked_index(obj_ref)
        info: dict[str, Any] = {"geometry_class": geometry_class}
        if idx is not None:
            info["component_index"] = idx

        pos = _indexed_component_position(
            obj_ref, component_type, geometry_class, idx
        )
        if pos is not None:
            info["position"] = pos
            info["midpoint"] = pos
            info["bbox_center"] = pos

        if geometry_class == "subd":
            subd = _subd_geometry(obj_ref)
            if subd is not None:
                try:
                    info["total_edges"] = int(subd.Edges.Count)
                    info["total_faces"] = int(subd.Faces.Count)
                    info["total_vertices"] = int(subd.Vertices.Count)
                except Exception:
                    pass
        elif geometry_class == "mesh":
            info["hint"] = (
                "Mesh erst zu SubD konvertieren (quad_remesh_to_subd), "
                "dann erneut picken."
            )
            mesh = _mesh_geometry(obj_ref)
            if mesh is not None:
                try:
                    info["total_edges"] = int(mesh.TopologyEdges.Count)
                    info["total_faces"] = int(mesh.Faces.Count)
                    info["total_vertices"] = int(mesh.TopologyVertices.Count)
                except Exception:
                    pass

        # Fall back to a whole-object bbox center when no per-component point
        # was resolved (e.g. object-level pick), so the summary still renders.
        if "bbox_center" not in info:
            try:
                geom = obj_ref.Object().Geometry
                bbox = geom.GetBoundingBox(True)
                if bbox.IsValid:
                    info["bbox_center"] = _xyz(bbox.Center)
            except Exception:
                pass
        return info or None
    except Exception:
        return None


_GENERIC_BREP_TYPES = {"polysurface", "brep", "extrusion"}


def _allowed_operations(
    object_class: Optional[str],
    component_type: Literal["edge", "face", "vertex", "object"],
    object_type_name: Optional[str] = None,
    geometry_class: Optional[str] = None,
) -> list[str]:
    # SubD/Mesh dispatch first — these rely on geometry_class, not on the
    # (optional, marker-based) object_class, so imported/old SubD/Mesh objects
    # without a user-text marker still get the right edit affordances.
    if geometry_class == "subd":
        if component_type == "edge":
            return ["subd_crease_edges"]
        if component_type == "face":
            return ["subd_extrude_faces", "subd_offset_faces"]
        if component_type == "vertex":
            return ["subd_set_vertex_position"]
        return ["subd_subdivide"]
    if geometry_class == "mesh":
        # A mesh has no direct parametric edit path here — convert to SubD
        # first, then re-pick. Same op for every component kind.
        return ["quad_remesh_to_subd"]

    if component_type == "object":
        if not object_class and object_type_name and object_type_name.lower() in _GENERIC_BREP_TYPES:
            return [
                "move_brep_face_along_normal",
                "move_brep_face_in_direction",
                "fillet_brep_edge",
                "chamfer_brep_edge",
            ]
        return []
    if object_class == "primitive_box":
        if component_type == "face":
            return ["resize_box_face"]
        return ["fillet_brep_edge", "chamfer_brep_edge"]
    if object_class == "primitive_cylinder":
        if component_type == "face":
            return ["resize_cylinder_face"]
        return ["fillet_brep_edge", "chamfer_brep_edge"]
    if object_class == "primitive_extrusion":
        if component_type == "face":
            return ["resize_extrusion_face"]
        return ["fillet_brep_edge", "chamfer_brep_edge"]
    if component_type == "edge":
        return ["fillet_brep_edge", "chamfer_brep_edge"]
    return ["move_brep_face_along_normal", "move_brep_face_in_direction"]


__all__ = [
    "RHINO_AVAILABLE",
    "PickResult",
    "PointResult",
    "ComponentPickResult",
    "pick_objects",
    "pick_point",
    "pick_component",
]
