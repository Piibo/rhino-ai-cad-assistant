"""agent.history - persistierte Historie -> Anthropic-API-Form."""
from __future__ import annotations

import logging
from typing import Any

from .. import schemas
from .prompts import (
    SNAPSHOT_SCENE_PREFIX,
    _USER_TYPES,
    _ASSISTANT_TYPES,
    _IMAGE_HISTORY_KEEP,
    _CAPPED_IMAGE_PLACEHOLDER,
    _API_BLOCK_FIELDS,
)
from .message_flattener import _flatten_plugin_blocks

logger = logging.getLogger("FurniturePlugin.Agent")

def _filter_for_role(role: str, blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    allowed = _ASSISTANT_TYPES if role == "assistant" else _USER_TYPES
    return [b for b in blocks if b.get("type") in allowed]


def _is_snapshot_scene_block(block: dict[str, Any]) -> bool:
    if not isinstance(block, dict):
        return False
    if block.get("type") != "text":
        return False
    text = block.get("text")
    return isinstance(text, str) and text.startswith(SNAPSHOT_SCENE_PREFIX)


def _strip_stale_snapshot_scene_text(
    api_messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Drop snapshot-scene text blocks from every user message except the
    latest one that contains any.

    Images stay — visual history is still useful, and the model can
    interpret an older snapshot in the context of later tool calls. The
    textual scene dump on the other hand is a frozen ``get_scene_info``
    output that goes stale as soon as the AI modifies anything; keeping
    every older copy just bloats the prompt and risks the model citing
    object names/IDs that don't exist anymore.

    No-op when there are zero or one snapshots in the history.
    """
    latest_idx = -1
    for i in range(len(api_messages) - 1, -1, -1):
        msg = api_messages[i]
        if msg.get("role") != "user":
            continue
        if any(_is_snapshot_scene_block(b) for b in msg.get("content", []) or []):
            latest_idx = i
            break
    if latest_idx <= 0:
        return api_messages

    for i, msg in enumerate(api_messages):
        if i >= latest_idx or msg.get("role") != "user":
            continue
        content = msg.get("content") or []
        pruned = [b for b in content if not _is_snapshot_scene_block(b)]
        if len(pruned) != len(content):
            msg["content"] = pruned
    return api_messages


def _cap_history_images(
    api_messages: list[dict[str, Any]], keep_last: int = _IMAGE_HISTORY_KEEP
) -> list[dict[str, Any]]:
    """Replace all but the last ``keep_last`` images in the history with a
    short text placeholder.

    Viewport snapshots are the single largest variable cost in a long session:
    each image is ~1-1.5k tokens, the conversation history is re-sent every
    turn (it carries no cache breakpoint), and the model captures the viewport
    after almost every operation. Older screenshots are stale after edits
    anyway (same rationale as ``_strip_stale_snapshot_scene_text``), so keeping
    only the most recent few caps the cost without losing current visual
    context.

    Images appear both as top-level ``image`` blocks (uploads, designer
    snapshots) and nested inside ``tool_result`` content lists
    (``capture_viewport`` results) — handle both. Walk newest->oldest so the
    images that survive are the most recent ones.
    """
    seen = 0

    for msg in reversed(api_messages):
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for i in range(len(content) - 1, -1, -1):
            block = content[i]
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "image":
                seen += 1
                if seen > keep_last:
                    content[i] = {"type": "text", "text": _CAPPED_IMAGE_PLACEHOLDER}
            elif btype == "tool_result":
                inner = block.get("content")
                if not isinstance(inner, list):
                    continue
                for j in range(len(inner) - 1, -1, -1):
                    sub = inner[j]
                    if isinstance(sub, dict) and sub.get("type") == "image":
                        seen += 1
                        if seen > keep_last:
                            inner[j] = {
                                "type": "text",
                                "text": _CAPPED_IMAGE_PLACEHOLDER,
                            }
    return api_messages


def _history_to_api(messages: list[schemas.Message]) -> list[dict[str, Any]]:
    """Convert persisted messages to the Anthropic API shape.

    Pipeline:
      1. serialise each block to dict + scrub SDK-only fields (_block_dict)
      2. flatten plugin-specific blocks (selection/point_pick/component_pick/sketch) into
         native text + image blocks (_flatten_plugin_blocks)
      3. role-filter (_filter_for_role)
      4. repair orphan tool_use pairs (_repair_tool_use_pairs)
      5. strip stale snapshot-scene text blocks (_strip_stale_snapshot_scene_text)
      6. cap old viewport images to the last few (_cap_history_images) — biggest
         single token/cost lever in long, screenshot-heavy sessions
    """
    api_messages: list[dict[str, Any]] = []
    for msg in messages:
        if msg.role not in ("user", "assistant"):
            continue
        raw = [_block_dict(b) for b in msg.content]
        raw = _flatten_plugin_blocks(raw)
        blocks = _filter_for_role(msg.role, raw)
        if not blocks:
            # Nicht mehr stumm ueberspringen: sonst verschwindet Modell-Kontext
            # spurlos aus der Historie (z. B. eine leer gespeicherte Nachricht
            # oder ein kuenftiger unbekannter Blocktyp). Detektierbar machen.
            logger.warning(
                "history: Nachricht %s (role=%s) uebersprungen — nach Rollen-"
                "Filter keine Bloecke uebrig (leere/gefilterte Nachricht?)",
                getattr(msg, "id", "?"),
                msg.role,
            )
            continue
        api_messages.append({"role": msg.role, "content": blocks})
    return _cap_history_images(
        _strip_stale_snapshot_scene_text(_repair_tool_use_pairs(api_messages))
    )


def _repair_tool_use_pairs(
    api_messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Insert synthetic tool_result blocks for orphan tool_use blocks.

    Orphans arise when an earlier agent run persisted the assistant tool_use
    message but crashed (or was interrupted) before persisting the matching
    tool_result user message. The Anthropic API rejects such histories with
    400 'tool_use ids were found without tool_result blocks'. Instead of
    failing every subsequent chat.send on this session, we fill the gaps
    with an is_error tool_result noting the cancellation — the model then
    treats the previous attempt as a failed step and can continue.
    """
    i = 0
    synth_count = 0
    while i < len(api_messages):
        msg = api_messages[i]
        if msg["role"] == "assistant":
            tool_uses = [
                b for b in msg.get("content", []) if b.get("type") == "tool_use"
            ]
            if tool_uses:
                next_msg = (
                    api_messages[i + 1] if i + 1 < len(api_messages) else None
                )
                covered: set[str] = set()
                if next_msg is not None and next_msg.get("role") == "user":
                    for b in next_msg.get("content", []):
                        if b.get("type") == "tool_result":
                            covered.add(b.get("tool_use_id", ""))
                orphans = [tu for tu in tool_uses if tu["id"] not in covered]
                if orphans:
                    synthetic = [
                        {
                            "type": "tool_result",
                            "tool_use_id": tu["id"],
                            "content": (
                                "Vorheriger Lauf abgebrochen — "
                                "Tool-Ergebnis nicht verfügbar."
                            ),
                            "is_error": True,
                        }
                        for tu in orphans
                    ]
                    synth_count += len(synthetic)
                    if next_msg is not None and next_msg.get("role") == "user":
                        next_msg["content"] = synthetic + list(
                            next_msg.get("content", [])
                        )
                    else:
                        api_messages.insert(
                            i + 1, {"role": "user", "content": synthetic}
                        )
        i += 1
    if synth_count:
        logger.info(
            "history repair: injected %d synthetic tool_result block(s) "
            "for orphan tool_use(s)",
            synth_count,
        )
    return api_messages


def _block_dict(block: Any) -> dict[str, Any]:
    """Normalise a block (Pydantic model, Anthropic SDK model, or dict) to dict.

    Also strips SDK-internal fields that the API rejects as input on the
    next turn (e.g. ``parsed_output`` on text blocks). See ``_clean_block``.
    """
    if isinstance(block, dict):
        d = block
    elif hasattr(block, "model_dump"):
        d = block.model_dump(mode="json")
    elif hasattr(block, "to_dict"):
        d = block.to_dict()
    else:
        # Anthropic SDK uses pydantic v2 models too; fall back to __dict__
        d = dict(getattr(block, "__dict__", {}))
    return _clean_block(d)


def _clean_block(block: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``block`` containing only API-accepted fields."""
    t = block.get("type")
    allowed = _API_BLOCK_FIELDS.get(t)
    if allowed is None:
        return block  # Unknown block type — leave as-is; server will reject loudly
    cleaned: dict[str, Any] = {}
    for k, v in block.items():
        if k not in allowed or v is None:
            continue
        cleaned[k] = v
    # tool_result's ``content`` is itself a list of blocks — recurse so nested
    # text/image blocks also get scrubbed.
    if t == "tool_result":
        inner = cleaned.get("content")
        if isinstance(inner, list):
            cleaned["content"] = [
                _clean_block(x) if isinstance(x, dict) else x for x in inner
            ]
    return cleaned


def _summarise(msg: dict[str, Any]) -> str:
    """Short textual summary of an API message for debug logs (no base64 bloat)."""
    role = msg.get("role", "?")
    blocks = msg.get("content", []) or []
    parts: list[str] = []
    for b in blocks:
        t = b.get("type", "?")
        if t == "text":
            txt = b.get("text", "")
            parts.append(f"text({len(txt)}ch)")
        elif t == "image":
            parts.append("image")
        elif t == "tool_use":
            parts.append(f"tool_use:{b.get('name')}")
        elif t == "tool_result":
            inner = b.get("content", [])
            if isinstance(inner, list):
                inner_types = ",".join(
                    x.get("type", "?") for x in inner if isinstance(x, dict)
                )
                parts.append(f"tool_result[{inner_types}]")
            else:
                parts.append("tool_result(str)")
        else:
            parts.append(t)
    return f"{role}:[{', '.join(parts)}]"
