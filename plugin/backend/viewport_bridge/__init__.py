"""Viewport-Bridge — non-blocking bridge between web UI and Rhino viewport.

Phase breakdown:
    - Phase 3a: snapshot (``snapshot.capture_snapshot``) — done.
    - Phase 3b: picking (``pick.pick_objects``) — done.
    - Phase 3c: sketch-overlay (DisplayConduit + 2D stroke capture as SVG).

All handlers dispatch via ``Rhino.RhinoApp.InvokeOnUiThread`` and push
results back to the frontend via the WebSocket manager (see
``backend/websocket_manager.py``). ``GetPoint`` and ``GetObjects`` are
modal on the UI thread during a pick; further ``InvokeOnUiThread`` calls
queue and run after the pick returns.
"""

from .snapshot import (
    RHINO_AVAILABLE,
    SUPPORTED_VIEWS,
    VALID_FIT_MODES,
    SnapshotResult,
    capture_composite,
    capture_snapshot,
    capture_snapshots,
    compose_views,
    fit_ortho_viewports_if_changed,
)
from .pick import (
    ComponentPickResult,
    PickResult,
    PointResult,
    pick_component,
    pick_objects,
    pick_point,
)
from .highlight import (
    clear_highlight,
    clear_point_markers,
    flash_run_created,
    highlight_reference,
    set_point_markers,
)

__all__ = [
    "RHINO_AVAILABLE",
    "SUPPORTED_VIEWS",
    "VALID_FIT_MODES",
    "SnapshotResult",
    "capture_composite",
    "capture_snapshot",
    "capture_snapshots",
    "compose_views",
    "fit_ortho_viewports_if_changed",
    "PickResult",
    "PointResult",
    "ComponentPickResult",
    "pick_objects",
    "pick_point",
    "pick_component",
    "highlight_reference",
    "clear_highlight",
    "set_point_markers",
    "clear_point_markers",
    "flash_run_created",
]
