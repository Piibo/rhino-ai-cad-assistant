"""dedicated_tools.variants - Varianten-Lebenszyklus."""
from __future__ import annotations

import asyncio
import logging
import re
import textwrap
import time
from typing import Any

from shared import code_templates
from .. import grasshopper_bridge, rhino_exec, schemas
from ..capability_router import guard_expose_parameters
from ..structure_context_service import (
    apply_parameter_set,
    clear_session_parameters,
    clear_structure_context,
    refresh_structure_context_in_store,
    sync_structure_context_after_object_change,
)
from ..session_store import get_store
from ..websocket_manager import manager
from ._shared import logger, _tool_result_failed

# ---------------------------------------------------------------------------
# Variant handlers (FF1 co-creator pattern, FF3 ownership)
# ---------------------------------------------------------------------------


_VARIANT_LAYER_PREFIX = "Variant_"


def _variant_session_tag(session_id: str) -> str:
    return "".join(ch for ch in session_id if ch.isalnum())[:8] or "session"


def _variant_layer(session_id: str, name: str) -> str:
    return f"{_VARIANT_LAYER_PREFIX}{_variant_session_tag(session_id)}__{name}"


def _variant_visibility_code(active_layer: str | None) -> str:
    if active_layer:
        return (
            "active_layer = {layer!r}\n"
            "if rs.IsLayer(active_layer):\n"
            "    rs.LayerVisible(active_layer, True)\n"
            "    rs.CurrentLayer(active_layer)\n"
            "all_layers = rs.LayerNames() or []\n"
            "for ln in all_layers:\n"
            "    if ln.startswith('Variant_') and ln != active_layer:\n"
            "        rs.LayerVisible(ln, False)\n"
            "if rs.IsLayer('Active'):\n"
            "    rs.LayerVisible('Active', False)\n"
            "sc.sticky['__active_variant_layer__'] = active_layer\n"
            "rs.Redraw()\n"
            "result = 'variant active: ' + active_layer\n"
        ).format(layer=active_layer)
    return (
        "if rs.IsLayer('Active'):\n"
        "    rs.LayerVisible('Active', True)\n"
        "    rs.CurrentLayer('Active')\n"
        "all_layers = rs.LayerNames() or []\n"
        "for ln in all_layers:\n"
        "    if ln.startswith('Variant_'):\n"
        "        rs.LayerVisible(ln, False)\n"
        "sc.sticky['__active_variant_layer__'] = None\n"
        "rs.Redraw()\n"
        "result = 'variant state cleared'\n"
    )


async def sync_variant_state(
    session_id: str,
    correlation_id: str | None = None,
) -> list[schemas.Variant]:
    """Re-apply the session's active variant to Rhino after reload/reconnect.

    Variant persistence already lives in SQLite, but Rhino's live state
    (visible layer + ``sc.sticky['__active_variant_layer__']``) resets when
    the backend or Rhino restarts. This helper makes session switching and
    reconnects deterministic again by restoring the active layer from the
    stored variants list.
    """
    store = get_store()
    variants = store.list_variants(session_id)
    active = next((v for v in variants if v.is_active), None)
    # active=None is a legitimate state (original shown, no variant selected).
    # Do NOT promote variants[0] here — that would overwrite an intentional
    # show_original on reconnect/session-switch.
    target_layer = active.layer_name if active else None
    try:
        await asyncio.to_thread(rhino_exec.run_code, _variant_visibility_code(target_layer))
    except Exception as e:
        logger.warning("sync_variant_state failed for session %s: %s", session_id, e)
    if correlation_id and active is not None:
        await manager.broadcast(
            schemas.WsEvent(
                type="variant.selected",
                payload={
                    "session_id": session_id,
                    "variant_id": active.id,
                    "name": active.name,
                },
                correlation_id=correlation_id,
            )
        )
    return variants


async def show_original(
    session_id: str,
    correlation_id: str | None = None,
) -> None:
    """Show the Active/Original layer and hide all Variant_* layers.

    Exposed for WS dispatch only — intentionally NOT wired into
    dispatch_dedicated_tool so no Tool-Schema/Hash is touched.
    """
    store = get_store()
    try:
        await asyncio.to_thread(rhino_exec.run_code, _variant_visibility_code(None))
    except Exception as e:
        logger.warning("show_original rhino exec failed for session %s: %s", session_id, e)

    store.set_active_variant(session_id, None)
    await clear_session_parameters(
        session_id,
        correlation_id,
        preserve_router_priority=False,
    )

    await manager.broadcast(
        schemas.WsEvent(
            type="variant.selected",
            payload={
                "session_id": session_id,
                "variant_id": None,
                "name": None,
            },
            correlation_id=correlation_id,
        )
    )
    logger.info("show_original: session=%s", session_id)


async def _handle_create_variant(
    session_id: str, tool_input: dict[str, Any], correlation_id: str | None
) -> str:
    name = (tool_input.get("name") or "").strip()
    description = (tool_input.get("description") or "").strip()
    copy_active = bool(tool_input.get("copy_active", True))
    if not name:
        return "create_variant braucht einen 'name'."

    store = get_store()
    if store.get_variant_by_name(session_id, name) is not None:
        return f"Variante '{name}' existiert bereits."

    layer_name = _variant_layer(session_id, name)
    code = (
        "layer_name = {layer!r}\n"
        "copy_active = {copy!r}\n"
        "if not rs.IsLayer(layer_name):\n"
        "    rs.AddLayer(layer_name, [200, 200, 50])\n"
        "all_layers = rs.LayerNames() or []\n"
        "for ln in all_layers:\n"
        "    if ln.startswith('Variant_') and ln != layer_name:\n"
        "        rs.LayerVisible(ln, False)\n"
        "rs.LayerVisible(layer_name, True)\n"
        "rs.CurrentLayer(layer_name)\n"
        "copied_count = 0\n"
        "if copy_active and rs.IsLayer('Active'):\n"
        "    active_objs = rs.ObjectsByLayer('Active') or []\n"
        "    for src_id in active_objs:\n"
        "        c = rs.CopyObject(src_id)\n"
        "        if c:\n"
        "            rs.ObjectLayer(c, layer_name)\n"
        "            src_name = rs.ObjectName(src_id) or ''\n"
        "            if src_name:\n"
        "                rs.ObjectName(c, src_name)\n"
        "            copied_count += 1\n"
        "if rs.IsLayer('Active'):\n"
        "    rs.LayerVisible('Active', False)\n"
        "sc.sticky['__active_variant_layer__'] = layer_name\n"
        "rs.Redraw()\n"
        "result = 'Variant created: ' + layer_name + ' (copied ' + str(copied_count) + ')'\n"
    ).format(layer=layer_name, copy=copy_active)

    try:
        run_result = await asyncio.to_thread(rhino_exec.run_code, code)
    except Exception as e:
        logger.exception("create_variant failed")
        return f"Variant-Erzeugung fehlgeschlagen: {e}"
    if _tool_result_failed(run_result):
        return run_result

    variant = schemas.Variant(
        session_id=session_id,
        name=name,
        description=description,
        layer_name=layer_name,
        is_active=True,
    )
    store.add_variant(variant)
    store.set_active_variant(session_id, variant.id)
    await clear_session_parameters(
        session_id,
        correlation_id,
        preserve_router_priority=False,
    )

    await manager.broadcast(
        schemas.WsEvent(
            type="variant.added",
            payload={
                "session_id": session_id,
                "variant": variant.model_dump(mode="json"),
            },
            correlation_id=correlation_id,
        )
    )
    logger.info(
        "create_variant: session=%s name=%s layer=%s copy=%s",
        session_id,
        name,
        layer_name,
        copy_active,
    )
    return (
        f"Variante '{name}' erstellt (Layer {layer_name}). "
        "Folgende Geometrie-Tool-Calls landen jetzt automatisch dort."
    )


async def _handle_select_variant(
    session_id: str, tool_input: dict[str, Any], correlation_id: str | None
) -> str:
    name = (tool_input.get("name") or "").strip()
    if not name:
        return "select_variant braucht einen 'name'."

    store = get_store()
    variant = store.get_variant_by_name(session_id, name)
    if variant is None:
        return f"Variante '{name}' nicht gefunden."

    code = _variant_visibility_code(variant.layer_name)

    try:
        run_result = await asyncio.to_thread(rhino_exec.run_code, code)
    except Exception as e:
        return f"Variant-Wechsel fehlgeschlagen: {e}"
    if _tool_result_failed(run_result):
        return run_result

    store.set_active_variant(session_id, variant.id)
    await clear_session_parameters(
        session_id,
        correlation_id,
        preserve_router_priority=False,
    )

    await manager.broadcast(
        schemas.WsEvent(
            type="variant.selected",
            payload={
                "session_id": session_id,
                "variant_id": variant.id,
                "name": variant.name,
            },
            correlation_id=correlation_id,
        )
    )
    logger.info("select_variant: session=%s name=%s", session_id, name)
    return f"Variante '{name}' ist jetzt aktiv."


async def _handle_finish_variants(
    session_id: str, correlation_id: str | None
) -> str:
    store = get_store()
    variants = store.list_variants(session_id)
    if not variants:
        return "Keine Varianten zum Abschluss."

    # If a variant is active, restore it at the end; otherwise (e.g. after
    # show_original cleared all is_active flags) restore the ORIGINAL via
    # _variant_visibility_code(None), not variants[0] — silently promoting the
    # first variant would desync the viewport from the store's "no active
    # variant" state and tell the designer nothing changed.
    active_layer = next(
        (v.layer_name for v in variants if v.is_active),
        None,
    )

    from ..viewport_bridge import RHINO_AVAILABLE, capture_snapshot

    if not RHINO_AVAILABLE:
        return "Rhino nicht verfuegbar fuer Thumbnail-Capture."

    captured = 0
    for variant in variants:
        toggle = _variant_visibility_code(variant.layer_name)
        await asyncio.to_thread(rhino_exec.run_code, toggle)
        try:
            snap = await asyncio.to_thread(
                capture_snapshot, max_size=300, fit="extents"
            )
        except Exception as e:
            logger.warning("variant %s thumbnail capture failed: %s", variant.name, e)
            continue

        thumb = schemas.ImageSource(
            type="base64",
            media_type=snap.get("media_type", "image/jpeg"),
            data=snap.get("data", ""),
        )
        store.update_variant_thumbnail(session_id, variant.id, thumb)
        await manager.broadcast(
            schemas.WsEvent(
                type="variant.thumbnail_ready",
                payload={
                    "session_id": session_id,
                    "variant_id": variant.id,
                    "name": variant.name,
                    "thumbnail": thumb.model_dump(mode="json"),
                },
                correlation_id=correlation_id,
            )
        )
        captured += 1

    restore = _variant_visibility_code(active_layer)
    await asyncio.to_thread(rhino_exec.run_code, restore)

    return (
        f"Thumbnails fuer {captured} Variante(n) erstellt. "
        "Designer kann jetzt in der Gallery auswaehlen."
    )


async def _handle_delete_variant(
    session_id: str, tool_input: dict[str, Any], correlation_id: str | None
) -> str:
    name = (tool_input.get("name") or "").strip()
    if not name:
        return "delete_variant braucht einen 'name'."

    store = get_store()
    variant = store.get_variant_by_name(session_id, name)
    if variant is None:
        return f"Variante '{name}' nicht gefunden."

    code = (
        "layer_name = {layer!r}\n"
        "if rs.IsLayer('Active'):\n"
        "    rs.LayerVisible('Active', True)\n"
        "    rs.CurrentLayer('Active')\n"
        "objs = rs.ObjectsByLayer(layer_name) or []\n"
        "for oid in objs:\n"
        "    try:\n"
        "        archive_object(oid)\n"
        "    except Exception:\n"
        "        pass\n"
        "    rs.DeleteObject(oid)\n"
        "if rs.IsLayer(layer_name):\n"
        "    try:\n"
        "        rs.PurgeLayer(layer_name)\n"
        "    except Exception:\n"
        "        pass\n"
        "rs.Redraw()\n"
        "result = 'deleted variant layer: ' + layer_name\n"
    ).format(layer=variant.layer_name)

    try:
        await asyncio.to_thread(rhino_exec.run_code, code)
    except Exception as e:
        return f"Variant-Loeschen fehlgeschlagen: {e}"

    was_active = variant.is_active
    store.remove_variant(session_id, variant.id)
    remaining = store.list_variants(session_id)
    if was_active:
        await clear_session_parameters(
            session_id,
            correlation_id,
            preserve_router_priority=False,
        )

    if was_active and remaining:
        new_active = next((v for v in remaining if v.is_active), remaining[0])
        store.set_active_variant(session_id, new_active.id)
        select_code = _variant_visibility_code(new_active.layer_name)
        await asyncio.to_thread(rhino_exec.run_code, select_code)
    elif not remaining:
        reset_code = _variant_visibility_code(None)
        await asyncio.to_thread(rhino_exec.run_code, reset_code)

    await manager.broadcast(
        schemas.WsEvent(
            type="variant.removed",
            payload={
                "session_id": session_id,
                "variant_id": variant.id,
                "name": variant.name,
            },
            correlation_id=correlation_id,
        )
    )
    if was_active and remaining:
        # new_active (oben aus is_active bzw. remaining[0] bestimmt) ist die
        # Variante, die set_active_variant + Layer-Switch tatsaechlich aktiviert
        # haben. Den Broadcast daran binden, NICHT an remaining[0] — sonst
        # koennten Viewport (new_active) und UI-Highlight (remaining[0])
        # auseinanderlaufen, sobald die is_active-Invariante sich aendert.
        await manager.broadcast(
            schemas.WsEvent(
                type="variant.selected",
                payload={
                    "session_id": session_id,
                    "variant_id": new_active.id,
                    "name": new_active.name,
                },
                correlation_id=correlation_id,
            )
        )
    if was_active:
        await sync_structure_context_after_object_change(
            session_id,
            source="tool_result",
            correlation_id=correlation_id,
            clear_if_missing=True,
            refresh_active=True,
        )
    return f"Variante '{name}' geloescht."


async def _handle_clear_variants(
    session_id: str, correlation_id: str | None
) -> str:
    store = get_store()
    variants = store.list_variants(session_id)
    if not variants:
        return "Keine Varianten vorhanden."

    layer_names = [v.layer_name for v in variants]
    code = (
        "target_layers = {layers!r}\n"
        "if rs.IsLayer('Active'):\n"
        "    rs.LayerVisible('Active', True)\n"
        "    rs.CurrentLayer('Active')\n"
        "removed = 0\n"
        "for ln in target_layers:\n"
        "    if not rs.IsLayer(ln):\n"
        "        continue\n"
        "    objs = rs.ObjectsByLayer(ln) or []\n"
        "    for oid in objs:\n"
        "        try:\n"
        "            archive_object(oid)\n"
        "        except Exception:\n"
        "            pass\n"
        "        rs.DeleteObject(oid)\n"
        "    try:\n"
        "        rs.PurgeLayer(ln)\n"
        "        removed += 1\n"
        "    except Exception:\n"
        "        pass\n"
        "all_layers = rs.LayerNames() or []\n"
        "for ln in all_layers:\n"
        "    if ln.startswith('Variant_'):\n"
        "        rs.LayerVisible(ln, False)\n"
        "sc.sticky['__active_variant_layer__'] = None\n"
        "if rs.IsLayer('Active'):\n"
        "    rs.LayerVisible('Active', True)\n"
        "rs.Redraw()\n"
        "result = 'Cleared ' + str(removed) + ' variant layer(s)'\n"
    ).format(layers=layer_names)
    try:
        await asyncio.to_thread(rhino_exec.run_code, code)
    except Exception as e:
        return f"Variant-Clear fehlgeschlagen: {e}"

    store.clear_variants(session_id)
    await clear_session_parameters(
        session_id,
        correlation_id,
        preserve_router_priority=False,
    )
    await manager.broadcast(
        schemas.WsEvent(
            type="variant.cleared",
            payload={"session_id": session_id},
            correlation_id=correlation_id,
        )
    )
    await sync_structure_context_after_object_change(
        session_id,
        source="tool_result",
        correlation_id=correlation_id,
        clear_if_missing=True,
        refresh_active=True,
    )
    return "Alle Varianten geloescht. Active-Layer ist wieder aktiv."


async def commit_active_variant(
    session_id: str,
    correlation_id: str | None = None,
) -> None:
    """Promote the active variant to the main 'Active' geometry (WS dispatch only).

    The designer picked a variant and wants to keep working with it as THE
    model. We archive the stale pre-variant original still sitting on 'Active',
    MOVE the selected variant's objects onto 'Active', clean up every remaining
    Variant_* layer, and drop back to a no-variant state
    (``__active_variant_layer__ = None``). After that the assistant reads the
    promoted geometry from 'Active' on its next turn via
    ``sync_structure_context_after_object_change`` — no prompt change needed.

    Exposed for WS dispatch only — intentionally NOT wired into
    dispatch_dedicated_tool so no Tool-Schema/Hash is touched (same rationale as
    show_original). This is the "close the picker but keep my choice" path,
    distinct from the model-visible ``clear_variants`` tool (which discards all).
    With no active variant it falls back to the discard-all path.
    """
    store = get_store()
    variants = store.list_variants(session_id)
    active = next((v for v in variants if v.is_active), None)
    if active is None:
        # Nothing selected to promote — behave like the discard-all path.
        await _handle_clear_variants(session_id, correlation_id)
        return

    other_layers = [v.layer_name for v in variants if v.id != active.id]
    code = (
        "target = 'Active'\n"
        "if not rs.IsLayer(target):\n"
        "    rs.AddLayer(target, [0, 200, 0])\n"
        "rs.LayerVisible(target, True)\n"
        "rs.CurrentLayer(target)\n"
        # 1) Archive the stale pre-variant original still on 'Active'.
        "for oid in (rs.ObjectsByLayer(target) or []):\n"
        "    try:\n"
        "        archive_object(oid)\n"
        "    except Exception:\n"
        "        pass\n"
        "    rs.DeleteObject(oid)\n"
        # 2) Move the selected variant's objects onto 'Active' (keep names).
        "active_layer = {active_layer!r}\n"
        "moved = 0\n"
        "if rs.IsLayer(active_layer):\n"
        "    for oid in (rs.ObjectsByLayer(active_layer) or []):\n"
        "        rs.ObjectLayer(oid, target)\n"
        "        moved += 1\n"
        # 3) Archive+delete the OTHER variants' objects.
        "other_layers = {other_layers!r}\n"
        "for ln in other_layers:\n"
        "    if not rs.IsLayer(ln):\n"
        "        continue\n"
        "    for oid in (rs.ObjectsByLayer(ln) or []):\n"
        "        try:\n"
        "            archive_object(oid)\n"
        "        except Exception:\n"
        "            pass\n"
        "        rs.DeleteObject(oid)\n"
        # 4) Purge every now-empty Variant_* layer (current layer is 'Active').
        "for ln in (rs.LayerNames() or []):\n"
        "    if ln.startswith('Variant_'):\n"
        "        try:\n"
        "            rs.LayerVisible(ln, False)\n"
        "            rs.PurgeLayer(ln)\n"
        "        except Exception:\n"
        "            pass\n"
        "sc.sticky['__active_variant_layer__'] = None\n"
        "rs.Redraw()\n"
        "result = 'variant promoted to Active (moved ' + str(moved) + ')'\n"
    ).format(active_layer=active.layer_name, other_layers=other_layers)

    try:
        await asyncio.to_thread(rhino_exec.run_code, code)
    except Exception as e:
        logger.warning(
            "commit_active_variant rhino exec failed for session %s: %s",
            session_id,
            e,
        )

    store.clear_variants(session_id)
    await clear_session_parameters(
        session_id,
        correlation_id,
        preserve_router_priority=False,
    )
    await manager.broadcast(
        schemas.WsEvent(
            type="variant.cleared",
            payload={"session_id": session_id},
            correlation_id=correlation_id,
        )
    )
    await sync_structure_context_after_object_change(
        session_id,
        source="tool_result",
        correlation_id=correlation_id,
        clear_if_missing=True,
        refresh_active=True,
    )
    logger.info(
        "commit_active_variant: session=%s name=%s -> Active",
        session_id,
        active.name,
    )
