"""Undo/redo watcher — emits ``viewport.undo_redo`` so panels can reset
per-object UI state the user dismissed.

Subscribes once to Rhino's ``Commands.Command.UndoRedo`` event and broadcasts a single
``viewport.undo_redo`` to every connected panel whenever the user performs an
undo or a redo. The frontend uses this to clear the set of structure ids whose
parameter panel the user dismissed (× ) — so a Rhino undo brings a dismissed
slider panel back, matching the designer's mental model ("I closed it for this
object; an undo should undo that too").

Why filter on the *begin* of an actual undo/redo
------------------------------------------------
``Commands.Command.UndoRedo`` fires at several phases. The recording phases
(``IsBeginRecording`` / ``IsEndRecording``) fire on ordinary undoable actions
— i.e. on almost every edit. Treating those as "an undo happened" would clear
the dismiss set constantly and defeat the per-object hide (the *primary*
feature). We therefore react ONLY to the begin of an actual undo/redo execution
(``IsBeginUndo`` / ``IsBeginRedo``), which fires once per Ctrl+Z / Ctrl+Y. All
attribute reads are defensive (``getattr``): if the args lack these flags we do
nothing — degrading to "undo doesn't reset" (hide still works) rather than
over-firing (which would break the hide).

Idempotent lifecycle mirrors ``selection_watcher``: ``start()`` unsubscribes
any previous handler first so a backend reload (Rhino's CPython process
persists across script runs) can't double-subscribe; ``stop()`` is safe when
nothing is attached.

This is CPython 3 inside Rhino (RhinoCommon available); f-strings are fine.
"""

from __future__ import annotations

import logging

from ..schemas import WsEvent
from ..websocket_manager import manager

logger = logging.getLogger("FurniturePlugin.UndoWatcher")

try:  # Only available inside Rhino's Python context.
    import Rhino  # type: ignore

    RHINO_AVAILABLE = True
except Exception as e:  # pragma: no cover — headless fallback
    logger.info("RhinoCommon not available (%s) — undo watcher disabled", e)
    Rhino = None  # type: ignore
    RHINO_AVAILABLE = False


_active = False


def _broadcast_undo() -> None:
    """Marshal a ``viewport.undo_redo`` broadcast onto the asyncio loop."""
    try:
        manager.broadcast_threadsafe(
            WsEvent(type="viewport.undo_redo", payload={})
        )
    except Exception as exc:  # pragma: no cover — defensive
        logger.debug("undo broadcast failed: %s", exc)


def _on_undo_redo(sender, e) -> None:  # noqa: ANN001 — Rhino delegate
    # Fires on Rhino's UI thread. React ONLY to the begin of an actual undo or
    # redo — never the recording phases (IsBeginRecording/IsEndRecording) that
    # fire on ordinary edits, which would clear the dismiss set constantly.
    try:
        is_begin_undo = bool(getattr(e, "IsBeginUndo", False))
        is_begin_redo = bool(getattr(e, "IsBeginRedo", False))
    except Exception:  # pragma: no cover — defensive
        return
    if is_begin_undo or is_begin_redo:
        _broadcast_undo()


def _unsubscribe() -> None:
    """Detach the handler. Safe to call when nothing is attached."""
    if not RHINO_AVAILABLE:
        return
    try:
        Rhino.Commands.Command.UndoRedo -= _on_undo_redo
    except Exception:
        pass


def start() -> None:
    """Subscribe to the undo/redo event. Idempotent; safe outside Rhino."""
    global _active
    if not RHINO_AVAILABLE:
        logger.debug("undo watcher start skipped — Rhino unavailable")
        return
    # Unsubscribe-before-subscribe guarantees exactly one handler even if a
    # previous module instance left one attached (CPython persists in Rhino).
    _unsubscribe()
    try:
        Rhino.Commands.Command.UndoRedo += _on_undo_redo
    except Exception as exc:  # pragma: no cover — Rhino-side
        logger.warning("undo watcher subscribe failed: %s", exc)
        return
    _active = True
    logger.info("undo watcher subscribed")


def stop() -> None:
    """Unsubscribe cleanly. Idempotent; safe outside Rhino."""
    global _active
    _unsubscribe()
    _active = False
    logger.info("undo watcher unsubscribed")


__all__ = ["RHINO_AVAILABLE", "start", "stop"]
