"""Viewport snapshot — captures Rhino views as base64 JPEGs.

Supports two modes:

- **Single view** (default, ``capture_snapshot``): captures the current viewport
  with optional ZoomExtents / ZoomSelection fit. Backwards compatible with
  the original signature.
- **Multi view** (``capture_snapshots``): captures a list of named projections
  (current, top, front, right, perspective, ...) in one UI-thread round-trip,
  each labelled, so the model can read orthographic sets without ambiguity.

Internally both go through ``_capture_on_ui``. The designer's camera is
saved and restored via ``Rhino.DocObjects.ViewportInfo`` +
``SetViewProjection`` (see ``_save_camera_state`` / ``_restore_camera_state``)
so the original view is restored even on multi-view captures.
PushViewProjection / PopViewProjection were deliberately dropped because the
push/pop stack did not round-trip reliably (see FEATURES.md §12.5).

Runs from the uvicorn background thread (the plugin backend lives inside
Rhino's CPython). Rhino's view APIs must be touched on the UI thread, so
the actual capture is marshalled via ``Rhino.RhinoApp.InvokeOnUiThread``.
"""

from __future__ import annotations

import base64
import logging
import math
import threading
from typing import Optional, TypedDict

logger = logging.getLogger("FurniturePlugin.Viewport")

try:  # Only available inside Rhino's Python context.
    import Rhino  # type: ignore
    import scriptcontext as sc  # type: ignore
    import rhinoscriptsyntax as rs  # type: ignore
    import System  # type: ignore
    from System.Drawing import Bitmap  # type: ignore
    from System.Drawing.Imaging import ImageFormat  # type: ignore
    from System.IO import MemoryStream  # type: ignore

    RHINO_AVAILABLE = True
except Exception as e:  # pragma: no cover — headless fallback
    logger.info("RhinoCommon not available (%s) — viewport snapshot disabled", e)
    RHINO_AVAILABLE = False


class SnapshotResult(TypedDict):
    media_type: str
    data: str
    width: int
    height: int
    view_label: str


# View identifiers the model is allowed to request. Mapped to Rhino's
# DefinedViewportProjection enum lazily (only inside Rhino's UI thread).
SUPPORTED_VIEWS: tuple[str, ...] = (
    "current",
    "top",
    "bottom",
    "front",
    "back",
    "left",
    "right",
    "perspective",
)

# Human-readable labels prepended as a text block before each image in
# the model-facing tool result. German because the surrounding plugin
# strings are German; axis hints help the model interpret silhouettes
# without guessing which side is which.
_VIEW_LABELS: dict[str, str] = {
    "top": "Top (Draufsicht, Blick aus +Z auf XY-Ebene)",
    "bottom": "Bottom (Untersicht, Blick aus -Z auf XY-Ebene)",
    "front": "Front (Vorderansicht, Blick aus -Y auf XZ-Ebene)",
    "back": "Back (Rueckansicht, Blick aus +Y auf XZ-Ebene)",
    "left": "Left (Seitenansicht links, Blick aus -X auf YZ-Ebene)",
    "right": "Right (Seitenansicht rechts, Blick aus +X auf YZ-Ebene)",
    "perspective": "Perspektive (3D-Uebersicht)",
}

VALID_FIT_MODES: tuple[str, ...] = ("none", "extents", "selection")

# Bilder werden nach Pixel-Dimension berechnet (Anthropic ~ w*h/750), NICHT
# nach Dateigroesse/JPEG-Qualitaet. Hoehere JPEG-Qualitaet kostet also KEINE
# zusaetzlichen Modell-Tokens — nur die Aufloesung tut das. Daher: scharf
# rendern (HighQualityBicubic) + JPEG-Qualitaet 92 statt GDI+-Default 75
# (75 blockt CAD-Linienwerk sichtbar zu). Verbessert sowohl die Overlay-
# Vorschau als auch die Modell-Bilder ohne Token-Mehrkosten.
_JPEG_QUALITY = 92


def _save_jpeg(bitmap, stream, quality: int = _JPEG_QUALITY) -> None:
    """JPEG mit EXPLIZITER Qualitaet speichern (GDI+-Default ist nur 75).
    Faellt defensiv auf den Default-Save zurueck, wenn der Encoder-Pfad auf
    diesem .NET nicht verfuegbar ist."""
    try:
        from System.Drawing.Imaging import (  # type: ignore
            Encoder,
            EncoderParameter,
            EncoderParameters,
            ImageCodecInfo,
        )

        enc = None
        for candidate in ImageCodecInfo.GetImageEncoders():
            if candidate.MimeType == "image/jpeg":
                enc = candidate
                break
        if enc is not None:
            params = EncoderParameters(1)
            params.Param[0] = EncoderParameter(Encoder.Quality, int(quality))
            bitmap.Save(stream, enc, params)
            return
    except Exception as exc:  # pragma: no cover — defensiv
        logger.debug("explicit-quality JPEG save failed (%s) — default", exc)
    bitmap.Save(stream, ImageFormat.Jpeg)


def _resize_high_quality(src, nw: int, nh: int):
    """Auf (nw, nh) skalieren mit HighQualityBicubic. Der frueher genutzte
    ``Bitmap(img, w, h)``-Konstruktor skaliert schnell aber unscharf
    (annaehernd Nearest-Neighbour) — sichtbar bei CAD-Kanten/Text. Defensiv:
    schlaegt der HighQuality-Pfad fehl, faellt es auf den Schnell-Konstruktor
    zurueck (der Aufrufer hat kein except, nur finally)."""
    try:
        from System.Drawing import Graphics  # type: ignore
        from System.Drawing.Drawing2D import (  # type: ignore
            CompositingQuality,
            InterpolationMode,
            PixelOffsetMode,
            SmoothingMode,
        )

        dst = Bitmap(int(nw), int(nh))
        g = None
        try:
            g = Graphics.FromImage(dst)
            g.InterpolationMode = InterpolationMode.HighQualityBicubic
            g.SmoothingMode = SmoothingMode.HighQuality
            g.PixelOffsetMode = PixelOffsetMode.HighQuality
            g.CompositingQuality = CompositingQuality.HighQuality
            g.DrawImage(src, 0, 0, int(nw), int(nh))
        finally:
            if g is not None:
                g.Dispose()
        return dst
    except Exception as exc:  # pragma: no cover — defensiv, Snapshot darf nie brechen
        logger.debug("high-quality resize failed (%s) — fast fallback", exc)
        return Bitmap(src, int(nw), int(nh))


def capture_snapshot(
    max_size: int = 800,
    show_annotations: bool = False,
    fit: str = "none",
    timeout: float = 5.0,
) -> SnapshotResult:
    """Capture the active viewport synchronously (single-view convenience).

    Backward-compatible wrapper around ``capture_snapshots`` for callers
    that only need one image. Returns the same TypedDict as before, now
    with an extra ``view_label`` field that existing callers can ignore.
    """
    results = capture_snapshots(
        views=["current"],
        max_size=max_size,
        show_annotations=show_annotations,
        fit=fit,
        timeout=timeout,
    )
    return results[0]


def capture_snapshots(
    views: Optional[list[str]] = None,
    max_size: int = 800,
    show_annotations: bool = False,
    fit: str = "none",
    timeout: Optional[float] = None,
) -> list[SnapshotResult]:
    """Capture one or more named viewport projections in one UI round-trip.

    Args:
        views: Ordered list of view identifiers. Each entry must be in
            ``SUPPORTED_VIEWS``. Defaults to ``["current"]``.
        max_size: Largest dimension of each resulting image in pixels.
            When ``views`` has more than one entry, the caller may want
            to drop this to ~500 to keep total vision-token cost in
            check; this function does not auto-throttle.
        show_annotations: If True, decorate objects with name + short-id
            text dots before capture (matches the legacy behaviour). The
            dots are added once and visible in every captured view.
        fit: One of ``"none"`` / ``"extents"`` / ``"selection"``. For
            non-current views, ``"none"`` falls back to extents (a fresh
            standard projection without zoom would otherwise show empty
            space). ``"selection"`` falls back to extents when nothing
            is selected.
        timeout: UI-thread wait in seconds. Auto-scales with ``len(views)``
            when not given (5s base + 3s per view).

    Raises:
        RuntimeError: When Rhino isn't available, the UI call timed out,
            an unknown view name was requested, or the capture failed.
    """
    if views is None or len(views) == 0:
        views = ["current"]
    unknown = [v for v in views if v not in SUPPORTED_VIEWS]
    if unknown:
        raise RuntimeError(
            "Unbekannte Ansicht(en): {0}. Erlaubt: {1}".format(
                ", ".join(unknown), ", ".join(SUPPORTED_VIEWS)
            )
        )
    if fit not in VALID_FIT_MODES:
        raise RuntimeError(
            "Unbekannter fit-Modus: {0}. Erlaubt: {1}".format(
                fit, ", ".join(VALID_FIT_MODES)
            )
        )
    if not RHINO_AVAILABLE:
        raise RuntimeError(
            "Rhino nicht verfuegbar — plugin.backend laeuft ausserhalb von Rhino"
        )

    effective_timeout = (
        timeout if timeout is not None else 5.0 + 3.0 * len(views)
    )

    holder: dict = {}
    done = threading.Event()

    def _do() -> None:
        try:
            holder["ok"] = _capture_on_ui(
                list(views), int(max_size), bool(show_annotations), fit
            )
        except Exception as exc:  # pragma: no cover — Rhino-side
            holder["err"] = str(exc)
        finally:
            done.set()

    Rhino.RhinoApp.InvokeOnUiThread(System.Action(_do))

    if not done.wait(timeout=effective_timeout):
        raise RuntimeError("viewport snapshot timed out")

    if "err" in holder:
        raise RuntimeError(holder["err"])

    return holder["ok"]


def capture_composite(
    views: Optional[list[str]] = None,
    max_size: int = 1024,
    show_annotations: bool = False,
    fit: str = "extents",
    timeout: Optional[float] = None,
) -> SnapshotResult:
    """Capture multiple views and bake them into a single labelled grid image.

    Returns a single SnapshotResult whose pixels contain a roughly square
    grid of the requested views, each cell labelled in its top-left corner
    so the model (and the human seeing the staged thumbnail) can attribute
    the silhouettes without an out-of-band hint.

    Token economics vs. four separate images: one 1024x1024 JPEG costs
    roughly the same as a single 800x600 image, while delivering all four
    orthographic + perspective views at once. That's the whole point of
    making this the default for both the chat camera button and the
    capture_viewport model tool — multi-view becomes free of effort for
    both the designer and the model.

    For ``len(views) == 1`` this passes through to ``capture_snapshot``
    (no composition, no label bake) so the cheap single-view path stays
    cheap.
    """
    if views is None or len(views) == 0:
        views = ["current"]

    if len(views) == 1:
        return capture_snapshot(
            max_size=max_size,
            show_annotations=show_annotations,
            fit=fit,
            timeout=timeout if timeout is not None else 5.0,
        )

    cols, rows = _grid_for(len(views))
    # Per-cell capture size: cap the longest dim so the composite as a
    # whole lands near ``max_size`` on its longest axis. The actual
    # per-cell pixel dimensions are derived from the captured aspect
    # ratio so we never stretch a 16:9 viewport into a square cell.
    cell_max = max(160, max_size // max(cols, rows))

    snapshots = capture_snapshots(
        views=views,
        max_size=cell_max,
        show_annotations=show_annotations,
        fit=fit,
        timeout=timeout,
    )
    return _compose_grid(snapshots, cols, rows)


def compose_views(snapshots: list[SnapshotResult]) -> SnapshotResult:
    """Baut aus bereits erfassten Einzelansichten ein gelabeltes Multi-View-Grid (kein erneuter Capture)."""
    cols, rows = _grid_for(len(snapshots))
    return _compose_grid(snapshots, cols, rows)


def _grid_for(n: int) -> tuple[int, int]:
    """Pick a nearly-square (cols, rows) layout for *n* cells."""
    if n <= 1:
        return (1, 1)
    cols = int(math.ceil(math.sqrt(n)))
    rows = int(math.ceil(n / cols))
    return cols, rows


def _short_label(view_label: str) -> str:
    """Strip the parenthesised hint from a view label for the in-image badge."""
    head = view_label.split("(", 1)[0].strip()
    return head or view_label


def _compose_grid(
    snapshots: list[SnapshotResult],
    cols: int,
    rows: int,
) -> SnapshotResult:
    """Decode the per-view JPEGs and paint them into one labelled grid.

    Cell dimensions are taken from the first captured snapshot so a 16:9
    or 4:3 viewport doesn't get stretched into a square cell — the
    captures all share the same Rhino viewport aspect, so a single probe
    is enough. Empty grid slots are painted white. The label is baked
    with a translucent dark backdrop so it stays legible across light
    and dark scene backgrounds.

    Runs on the calling (background) thread — no Rhino UI calls here, so
    the UI stays responsive while we re-encode.
    """
    from System import Array, Byte  # type: ignore
    from System.Drawing import (  # type: ignore
        Bitmap,
        Brushes,
        Color,
        Font,
        FontFamily,
        FontStyle,
        Graphics,
        SolidBrush,
    )

    first = snapshots[0]
    cell_w = int(first["width"])
    cell_h = int(first["height"])
    total_w = int(cols * cell_w)
    total_h = int(rows * cell_h)
    composite = Bitmap(total_w, total_h)
    g: Optional[Graphics] = None
    try:
        g = Graphics.FromImage(composite)
        g.FillRectangle(Brushes.White, 0, 0, int(total_w), int(total_h))

        # Font scales with the shorter cell dimension so labels stay
        # proportional regardless of viewport aspect.
        font_size = float(max(10.0, min(cell_w, cell_h) / 28.0))
        font = Font(FontFamily.GenericSansSerif, font_size, FontStyle.Bold)
        text_brush = SolidBrush(Color.White)
        bg_brush = SolidBrush(Color.FromArgb(180, 0, 0, 0))
        try:
            for i, snap in enumerate(snapshots):
                col = i % cols
                row = i // cols
                x = col * cell_w
                y = row * cell_h

                data = base64.b64decode(snap["data"])
                # pythonnet does NOT auto-convert ``bytes`` to
                # ``System.Byte[]``, so ``MemoryStream(data)`` would pick
                # the ``MemoryStream(Int32)`` capacity overload and fail
                # with "an integer is required". Wrap explicitly.
                byte_arr = Array[Byte](data)
                ms = MemoryStream(byte_arr)
                try:
                    cell_bmp = Bitmap(ms)
                    try:
                        # Draw at the cell's exact pixel dimensions so the
                        # captured aspect is preserved. snap["width"] /
                        # snap["height"] already match (cell_w, cell_h)
                        # because every snapshot comes from the same
                        # Rhino viewport at the same max_size.
                        g.DrawImage(cell_bmp, int(x), int(y), cell_w, cell_h)
                    finally:
                        cell_bmp.Dispose()
                finally:
                    ms.Dispose()

                label = _short_label(snap["view_label"])
                size = g.MeasureString(label, font)
                pad = 4
                g.FillRectangle(
                    bg_brush,
                    int(x + 6),
                    int(y + 6),
                    int(size.Width) + 2 * pad,
                    int(size.Height) + pad,
                )
                g.DrawString(
                    label,
                    font,
                    text_brush,
                    float(x + 6 + pad),
                    float(y + 6),
                )
        finally:
            text_brush.Dispose()
            bg_brush.Dispose()
            font.Dispose()

        out_ms = MemoryStream()
        try:
            _save_jpeg(composite, out_ms)
            img_bytes = bytes(bytearray(out_ms.ToArray()))
        finally:
            out_ms.Dispose()
    finally:
        if g is not None:
            g.Dispose()
        composite.Dispose()

    summary = "Multi-View ({0}): {1}".format(
        len(snapshots), ", ".join(_short_label(s["view_label"]) for s in snapshots)
    )
    return SnapshotResult(
        media_type="image/jpeg",
        data=base64.b64encode(img_bytes).decode("ascii"),
        width=total_w,
        height=total_h,
        view_label=summary,
    )


def _projection_for(view_name: str):
    """Map a view identifier to Rhino's DefinedViewportProjection.

    Called from inside the UI thread only; lookup is deferred so the
    headless import path doesn't need ``Rhino.Display``.
    """
    table = {
        "top": Rhino.Display.DefinedViewportProjection.Top,
        "bottom": Rhino.Display.DefinedViewportProjection.Bottom,
        "front": Rhino.Display.DefinedViewportProjection.Front,
        "back": Rhino.Display.DefinedViewportProjection.Back,
        "left": Rhino.Display.DefinedViewportProjection.Left,
        "right": Rhino.Display.DefinedViewportProjection.Right,
        "perspective": Rhino.Display.DefinedViewportProjection.Perspective,
    }
    return table[view_name]


def _compute_visible_bbox():
    """Bounding box of every non-hidden, non-locked object in the doc.

    Returned as a ``Rhino.Geometry.BoundingBox`` so we can hand it to
    ``ZoomBoundingBox`` directly. Returns ``None`` when the document has
    no visible geometry — callers can then skip the zoom.
    """
    bbox = None
    for obj in sc.doc.Objects:
        try:
            if obj.IsHidden or obj.IsLocked:
                continue
            geo = obj.Geometry
            if geo is None:
                continue
            gbb = geo.GetBoundingBox(True)
            if not gbb.IsValid:
                continue
        except Exception:
            continue
        if bbox is None:
            bbox = gbb
        else:
            bbox.Union(gbb)
    return bbox


def _zoom_all_visible(viewport) -> None:
    """Frame all visible geometry without relying on ``ZoomExtents``.

    ``RhinoViewport.ZoomExtents()`` is unreliable on non-active viewports
    (and on some Rhino builds it falls back to selected-only semantics),
    which is why earlier multi-view captures kept the designer's wide-zoom
    Top/Front/Right viewports unchanged. Computing the bbox ourselves and
    calling ``ZoomBoundingBox`` works for every viewport regardless of
    active state or selection.
    """
    bbox = _compute_visible_bbox()
    if bbox is None or not bbox.IsValid:
        return
    viewport.ZoomBoundingBox(bbox)


def _zoom_selection_or_extents(viewport) -> None:
    """Frame the selected objects, falling back to all visible geometry."""
    sel = rs.SelectedObjects()
    if not sel:
        _zoom_all_visible(viewport)
        return
    bbox_pts = rs.BoundingBox(sel)
    if not bbox_pts:
        _zoom_all_visible(viewport)
        return
    points = [Rhino.Geometry.Point3d(p[0], p[1], p[2]) for p in bbox_pts]
    bbox = Rhino.Geometry.BoundingBox(points)
    viewport.ZoomBoundingBox(bbox)


# Letzte sichtbare-Geometrie-Extents, auf die die Ortho-Ansichten zuletzt
# eingepasst wurden. Damit feuert der End-of-Turn-Fit NUR bei tatsaechlicher
# Geometrie-Aenderung — ein manueller Ortho-Zoom bleibt bei reinen Chat-/
# Abfrage-Turns erhalten. Doc-global (ein Rhino-Doc pro Prozess).
_last_fitted_extents: Optional[tuple] = None

# Nur diese Ansichten werden automatisch eingepasst. Perspektive bewusst NIE.
_ORTHO_VIEW_NAMES = ("top", "front", "right")


def _bbox_signature(bbox) -> Optional[tuple]:
    """(minX,minY,minZ, maxX,maxY,maxZ) gerundet — oder None bei leerer Bbox.

    Vergleichsschluessel der Aenderungs-Erkennung; gerundet, damit Float-
    Rauschen keinen Pseudo-Change ausloest.
    """
    if bbox is None or not bbox.IsValid:
        return None
    mn, mx = bbox.Min, bbox.Max
    return (
        round(mn.X, 3), round(mn.Y, 3), round(mn.Z, 3),
        round(mx.X, 3), round(mx.Y, 3), round(mx.Z, 3),
    )


def fit_ortho_viewports_if_changed(timeout: float = 4.0) -> bool:
    """Passe Top/Front/Right auf die sichtbare Geometrie ein — NUR wenn sie
    sich seit dem letzten Fit geaendert hat. Perspektive bleibt unberuehrt.

    Gedacht fuer das Ende eines KI-Turns: hat der Lauf Geometrie erzeugt/
    veraendert (Bbox != zuletzt gefittet), werden die drei Ortho-Ansichten neu
    gerahmt; sonst No-Op (so wird ein manueller Ortho-Zoom bei reinen Chat-/
    Abfrage-Turns nicht zurueckgesetzt). Laeuft auf dem Rhino-UI-Thread
    (InvokeOnUiThread + Event, wie ``capture_snapshots``); gefittet wird ueber
    den zuverlaessigen ``ZoomBoundingBox`` (nicht das auf Nicht-Aktiv-Viewports
    unzuverlaessige ZoomExtents).

    Gibt True zurueck, wenn tatsaechlich eingepasst wurde. Voll defensiv: jeder
    Fehler wird geloggt und geschluckt — das Plugin laeuft weiter, nur ohne
    Auto-Fit.
    """
    if not RHINO_AVAILABLE:
        return False

    holder: dict = {}
    done = threading.Event()

    def _do() -> None:
        global _last_fitted_extents
        try:
            bbox = _compute_visible_bbox()
            sig = _bbox_signature(bbox)
            if sig is None or sig == _last_fitted_extents:
                holder["fitted"] = False
                return
            try:
                views = list(sc.doc.Views.GetViewList(True, False))
            except Exception:
                views = []
            fitted_any = False
            for v in views:
                try:
                    vp = v.ActiveViewport
                    name = (vp.Name or "").lower()
                except Exception:
                    continue
                if name not in _ORTHO_VIEW_NAMES:
                    continue  # Perspektive + alles andere unberuehrt
                try:
                    if vp.IsPerspectiveProjection:
                        continue  # doppelte Sicherung: nie eine Perspektiv-Ansicht
                    vp.ZoomBoundingBox(bbox)
                    fitted_any = True
                except Exception as exc:
                    logger.warning("fit ortho %s failed: %s", name, exc)
            if fitted_any:
                _last_fitted_extents = sig
                try:
                    sc.doc.Views.Redraw()
                except Exception:
                    pass
            holder["fitted"] = fitted_any
        except Exception as exc:  # pragma: no cover — Rhino UI thread, defensiv
            logger.warning("fit_ortho_viewports_if_changed failed: %s", exc)
            holder["fitted"] = False
        finally:
            done.set()

    try:
        Rhino.RhinoApp.InvokeOnUiThread(System.Action(_do))
    except Exception as exc:  # pragma: no cover — defensiv
        logger.warning("fit ortho invoke failed: %s", exc)
        return False
    if not done.wait(timeout=timeout):
        logger.warning("fit ortho timed out after %ss", timeout)
        return False
    return bool(holder.get("fitted"))


def _save_camera_state(viewport) -> dict:
    """Snapshot the full viewport state for later restore.

    Wraps ``Rhino.DocObjects.ViewportInfo(viewport)``, which captures
    camera location/target/up, frustum bounds, projection type and lens
    in one struct. That's the only way to restore the *zoom* of a
    parallel viewport (Top/Front/Right): parallel projections encode
    their zoom in the frustum, and ``RhinoViewport`` exposes no public
    ``SetFrustum`` — so manual frustum save/restore can't work. The
    ViewportInfo roundtrip via ``SetViewProjection`` does.

    The viewport's Name is captured separately because SetViewProjection
    doesn't touch it; only the fallback ``SetProjection`` path mutates
    Name, but we always restore both regardless of which path ran.
    """
    state: dict = {}
    try:
        state["viewport_info"] = Rhino.DocObjects.ViewportInfo(viewport)
    except Exception as exc:
        logger.warning("ViewportInfo snapshot failed: %s", exc)
    try:
        state["name"] = viewport.Name
    except Exception:
        pass
    return state


def _restore_camera_state(viewport, state: dict) -> None:
    """Re-apply a saved snapshot via SetViewProjection + Name."""
    info = state.get("viewport_info")
    if info is not None:
        try:
            viewport.SetViewProjection(info, True)
        except Exception as exc:
            logger.warning("SetViewProjection failed: %s", exc)
    try:
        if state.get("name"):
            viewport.Name = state["name"]
    except Exception:
        pass


def _label_for_current(viewport) -> str:
    """Use the actual viewport name so the model knows which view it sees."""
    try:
        name = viewport.Name
    except Exception:
        name = "Aktive Ansicht"
    return "Aktuelle Ansicht ({0})".format(name)


def _resolve_target_view(
    view_name: str,
    active_view,
    by_name: dict,
    all_views: list,
) -> tuple[object, bool]:
    """Pick which RhinoView a requested view name should be captured from.

    Returns ``(view, needs_projection_switch)``. When ``needs_projection_switch``
    is True, the active view's viewport must be mutated to the requested
    projection (and restored later); that's the fallback for the rare case
    that the designer's layout doesn't include the requested standard view.

    For "current" we always use the active view as-is. For named views like
    "top" / "front" / etc. we prefer the designer's existing same-named
    viewport — that keeps the layout pristine: no SetProjection, no Name
    flips, no display-mode mutation. "perspective" additionally matches any
    viewport with a perspective projection if the literal name doesn't hit.
    """
    if view_name == "current":
        return active_view, False
    match = by_name.get(view_name)
    if match is not None:
        return match, False
    if view_name == "perspective":
        try:
            for v in all_views:
                if v.ActiveViewport.IsPerspectiveProjection:
                    return v, False
        except Exception:
            pass
    return active_view, True


def _capture_on_ui(
    views: list[str],
    max_size: int,
    show_annotations: bool,
    fit: str,
) -> list[SnapshotResult]:
    """Real capture logic — MUST be called on Rhino's UI thread.

    Uses the designer's existing named viewports (Top / Front / Right /
    Perspective) when present, so capturing multi-view never re-projects
    the active viewport. The active viewport's name, display mode, and
    sibling viewports stay untouched. Only ZoomExtents / ZoomSelection
    are applied (the camera state of every touched viewport is saved via
    ViewportInfo and restored at the end — see _save/_restore_camera_state).
    """
    # Defense in depth: a 0 / negative max_size (e.g. from a model tool call
    # that bypassed the WS-handler clamp) would make Bitmap(bmp, 0, ...) throw
    # a .NET ArgumentException. Clamp to a safe minimum here too.
    max_size = max(16, int(max_size))
    active_view = sc.doc.Views.ActiveView
    if active_view is None:
        raise RuntimeError("no active viewport")

    # Index the designer's existing viewports by lowercase Name so we can
    # route "top" → their Top viewport without mutating Perspective.
    try:
        all_views = list(sc.doc.Views.GetViewList(True, False))
    except Exception:
        all_views = [active_view]
    by_name: dict = {}
    for v in all_views:
        try:
            n = v.ActiveViewport.Name
            if n:
                by_name[n.lower()] = v
        except Exception:
            continue

    annotation_layer = "AI_Furniture_Annotations"
    orig_layer = rs.CurrentLayer()
    dots: list = []

    if show_annotations:
        if not rs.IsLayer(annotation_layer):
            rs.AddLayer(annotation_layer, color=(255, 0, 0))
        rs.CurrentLayer(annotation_layer)
        for obj in sc.doc.Objects:
            bbox = rs.BoundingBox(obj.Id)
            if not bbox:
                continue
            name = rs.ObjectName(obj.Id) or "Unnamed"
            dot = rs.AddTextDot(name, bbox[1])
            rs.TextDotHeight(dot, 8)
            dots.append(dot)

    # Track viewports we mutated so the final restore block can undo
    # *every* change regardless of which view raised. Snapshot the full
    # camera+frustum+name state once per viewport (the first time we
    # touch it) — that's the state we replay at the end.
    saved_states: dict = {}

    results: list[SnapshotResult] = []
    try:
        for view_name in views:
            target_view, needs_projection_switch = _resolve_target_view(
                view_name, active_view, by_name, all_views
            )
            target_viewport = target_view.ActiveViewport
            vp_key = id(target_viewport)

            need_camera_change = (
                fit != "none" or needs_projection_switch
            )

            if need_camera_change and vp_key not in saved_states:
                try:
                    saved_states[vp_key] = (
                        target_viewport,
                        _save_camera_state(target_viewport),
                    )
                except Exception as exc:
                    logger.warning("camera state snapshot failed: %s", exc)

            if needs_projection_switch:
                # Fallback path: the designer doesn't have a viewport of
                # this projection in their layout, so we re-project the
                # active viewport. SetProjection mutates Name too; the
                # saved state already captured the original Name.
                target_viewport.SetProjection(
                    _projection_for(view_name), view_name.title(), True
                )

            if need_camera_change:
                if fit == "selection":
                    _zoom_selection_or_extents(target_viewport)
                else:
                    # fit == "extents" or fallback after projection switch
                    _zoom_all_visible(target_viewport)

            # Force a redraw so CaptureToBitmap sees the (possibly
            # mutated) projection.
            target_view.Redraw()
            bmp: Optional[Bitmap] = None
            resized: Optional[Bitmap] = None
            ms: Optional[MemoryStream] = None
            try:
                bmp = target_view.CaptureToBitmap()
                w, h = bmp.Width, bmp.Height
                if w >= h:
                    nw = max_size
                    nh = max(1, int(h * max_size / w))
                else:
                    nh = max_size
                    nw = max(1, int(w * max_size / h))
                resized = _resize_high_quality(bmp, nw, nh)
                ms = MemoryStream()
                _save_jpeg(resized, ms)
                img_bytes = bytes(bytearray(ms.ToArray()))
            finally:
                if ms is not None:
                    ms.Dispose()
                if resized is not None:
                    resized.Dispose()
                if bmp is not None:
                    bmp.Dispose()

            label = (
                _label_for_current(target_viewport)
                if view_name == "current"
                else _VIEW_LABELS.get(view_name, view_name.title())
            )
            results.append(
                SnapshotResult(
                    media_type="image/jpeg",
                    data=base64.b64encode(img_bytes).decode("ascii"),
                    width=nw,
                    height=nh,
                    view_label=label,
                )
            )
    finally:
        if dots:
            try:
                rs.DeleteObjects(dots)
            except Exception:
                pass
        try:
            rs.CurrentLayer(orig_layer)
        except Exception:
            pass
        for vp, state in saved_states.values():
            try:
                _restore_camera_state(vp, state)
            except Exception as exc:
                logger.warning("restore camera state failed: %s", exc)
        try:
            sc.doc.Views.Redraw()
        except Exception:
            pass

    return results


__all__ = [
    "RHINO_AVAILABLE",
    "SUPPORTED_VIEWS",
    "VALID_FIT_MODES",
    "SnapshotResult",
    "capture_snapshot",
    "capture_snapshots",
    "capture_composite",
    "compose_views",
    "fit_ortho_viewports_if_changed",
]
