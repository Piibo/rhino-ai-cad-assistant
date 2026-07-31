"""Live selection watcher — emits ``viewport.selection_changed`` to the panels.

Subscribes once to Rhino's document selection events (``SelectObjects`` /
``DeselectObjects`` / ``DeselectAllObjects``) plus the active-document change,
and broadcasts the current selection *count* to every connected panel so the
UI can show a live "N markierte Objekte" badge before the user even clicks the
K/F/O picker.

Design notes
------------
* **Lightweight.** Each event only triggers a re-read of ``rs.SelectedObjects``
  (a GUID list) — no geometry is gathered. The payload is exactly
  ``{"count": <int>}`` (the shape ``useWebSocket.ts`` reads via ``p.count ?? 0``).
* **Coalescing.** A window-select fires many ``SelectObjects`` events in a
  burst. Rather than broadcast per event, the events only set a "dirty" flag;
  the actual count is read and broadcast once on the next ``RhinoApp.Idle``.
  That collapses a burst into a single emit without a custom timer.
* **UI-thread → loop marshaling.** Selection events fire on Rhino's UI thread;
  the broadcast is handed to the asyncio loop via
  ``manager.broadcast_threadsafe`` — the same helper the pick flow uses. No new
  marshaling mechanism is invented here.
* **Idempotent lifecycle.** ``start()`` unsubscribes any previous handlers
  first, so a backend force-reload (Rhino's CPython process persists between
  script runs) or a uvicorn restart can never double-subscribe. ``stop()`` is
  safe to call when nothing is subscribed.

This is CPython 3 inside Rhino (RhinoCommon available); f-strings are fine.
"""

from __future__ import annotations

import logging
from typing import Optional

from ..schemas import WsEvent
from ..websocket_manager import manager

logger = logging.getLogger("FurniturePlugin.SelectionWatcher")

try:  # Only available inside Rhino's Python context.
    import Rhino  # type: ignore
    import rhinoscriptsyntax as rs  # type: ignore
    import System  # type: ignore

    RHINO_AVAILABLE = True
except Exception as e:  # pragma: no cover — headless fallback
    logger.info(
        "RhinoCommon not available (%s) — selection watcher disabled", e
    )
    RHINO_AVAILABLE = False


# Module-level singleton state. Survives a force-reload only if this module
# itself is re-imported fresh (which clears it), so the guard relies on
# explicit unsubscribe-before-subscribe in ``start()`` rather than on this
# flag surviving. ``_active`` short-circuits a redundant start within one
# module lifetime.
_active = False
_dirty = False


def _selected_count() -> int:
    """Current number of selected objects (cheap GUID-list length read)."""
    try:
        sel = rs.SelectedObjects()
        return len(sel) if sel else 0
    except Exception as exc:  # pragma: no cover — defensive
        logger.debug("selection count read failed: %s", exc)
        return 0


def _broadcast_count(count: int) -> None:
    """Marshal a ``viewport.selection_changed`` broadcast onto the asyncio loop."""
    try:
        manager.broadcast_threadsafe(
            WsEvent(
                type="viewport.selection_changed",
                payload={"count": int(count)},
            )
        )
    except Exception as exc:  # pragma: no cover — defensive
        logger.debug("selection broadcast failed: %s", exc)


# --- Rhino event handlers (fire on the UI thread) --------------------------
#
# The selection events arrive in bursts during a window-select, so they only
# mark the state dirty. The single read + broadcast happens on the next Idle.


def _on_selection_event(sender, e) -> None:  # noqa: ANN001 — Rhino delegate
    global _dirty
    _dirty = True


def _on_idle(sender, e) -> None:  # noqa: ANN001 — Rhino delegate
    global _dirty
    if not _dirty:
        return
    _dirty = False
    _broadcast_count(_selected_count())


def _on_active_doc_changed(sender, e) -> None:  # noqa: ANN001 — Rhino delegate
    # A document switch implies a fresh (usually empty) selection state.
    _broadcast_count(_selected_count())


def _unsubscribe() -> None:
    """Detach all handlers. Safe to call when nothing is attached."""
    if not RHINO_AVAILABLE:
        return
    try:
        Rhino.RhinoDoc.SelectObjects -= _on_selection_event
    except Exception:
        pass
    try:
        Rhino.RhinoDoc.DeselectObjects -= _on_selection_event
    except Exception:
        pass
    try:
        Rhino.RhinoDoc.DeselectAllObjects -= _on_selection_event
    except Exception:
        pass
    try:
        Rhino.RhinoDoc.ActiveDocumentChanged -= _on_active_doc_changed
    except Exception:
        pass
    try:
        Rhino.RhinoApp.Idle -= _on_idle
    except Exception:
        pass


def start() -> None:
    """Subscribe to selection events and emit the current count once.

    Idempotent: detaches any prior handlers first so a backend reload or a
    uvicorn restart can never leave a double subscription. Safe no-op outside
    Rhino.
    """
    global _active, _dirty
    if not RHINO_AVAILABLE:
        logger.debug("selection watcher start skipped — Rhino unavailable")
        return
    # Unsubscribe-before-subscribe guarantees exactly one handler set even if
    # a previous module instance left handlers attached (CPython persists in
    # Rhino across script runs).
    _unsubscribe()
    try:
        Rhino.RhinoDoc.SelectObjects += _on_selection_event
        Rhino.RhinoDoc.DeselectObjects += _on_selection_event
        Rhino.RhinoDoc.DeselectAllObjects += _on_selection_event
        Rhino.RhinoDoc.ActiveDocumentChanged += _on_active_doc_changed
        Rhino.RhinoApp.Idle += _on_idle
    except Exception as exc:  # pragma: no cover — Rhino-side
        logger.warning("selection watcher subscribe failed: %s", exc)
        return
    _active = True
    _dirty = False
    logger.info("selection watcher subscribed")
    # Push the initial count immediately so a panel that connected before the
    # first selection change still shows the correct badge.
    emit_current()


def stop() -> None:
    """Unsubscribe cleanly. Idempotent; safe outside Rhino."""
    global _active, _dirty
    _unsubscribe()
    _active = False
    _dirty = False
    logger.info("selection watcher unsubscribed")


def emit_current() -> None:
    """Broadcast the current selection count once (initial-state push).

    Called on watcher start (already on the UI thread, from the Idle/event
    context or startup). Broadcasts to every panel; the state is idempotent.
    """
    if not RHINO_AVAILABLE:
        return
    _broadcast_count(_selected_count())


def emit_current_threadsafe() -> None:
    """Initial-state push when called from a non-UI thread (e.g. WS connect).

    ``rs.SelectedObjects`` reads the active document, so the count read is
    marshalled onto Rhino's UI thread via ``InvokeOnUiThread`` (the same
    mechanism the pick flow uses) before broadcasting. Fire-and-forget: the
    actual broadcast still goes through ``broadcast_threadsafe`` to all panels,
    so a panel that connected before the first selection change gets the
    correct badge without waiting for the next change.
    """
    if not RHINO_AVAILABLE:
        return

    def _do() -> None:
        try:
            _broadcast_count(_selected_count())
        except Exception as exc:  # pragma: no cover — Rhino-side
            logger.debug("emit_current_threadsafe read failed: %s", exc)

    try:
        Rhino.RhinoApp.InvokeOnUiThread(System.Action(_do))
    except Exception as exc:  # pragma: no cover — defensive
        logger.debug("emit_current_threadsafe invoke failed: %s", exc)


__all__ = [
    "RHINO_AVAILABLE",
    "start",
    "stop",
    "emit_current",
    "emit_current_threadsafe",
]
