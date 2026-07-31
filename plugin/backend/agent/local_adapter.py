"""agent.local_adapter - Anthropic <-> OpenAI/chat-completions Uebersetzung."""
from __future__ import annotations

import base64
import json
import logging
from typing import Any

logger = logging.getLogger("FurniturePlugin.Agent")

# ---------------------------------------------------------------------------
# Anthropic ↔ OpenAI conversion (for the local-LLM path only)
# ---------------------------------------------------------------------------


def _tools_anthropic_to_openai(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate Anthropic tool definitions to OpenAI function-calling shape."""
    out: list[dict[str, Any]] = []
    for t in tools:
        out.append(
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": t.get("input_schema", {"type": "object"}),
                },
            }
        )
    return out


def _img_to_data_url(source: dict[str, Any]) -> str:
    media = source.get("media_type", "image/png")
    data = source.get("data", "")
    return f"data:{media};base64,{data}"


def _messages_anthropic_to_openai(
    anthropic_messages: list[dict[str, Any]], system: str
) -> list[dict[str, Any]]:
    """Translate the Anthropic message history into OpenAI chat-completions.

    The Anthropic format uses content-block lists everywhere; OpenAI uses
    role-specific shapes (``tool_calls`` on assistant, ``role: "tool"`` for
    results, ``image_url`` parts for vision). Conversion is lossy in two
    places worth knowing about:

    - ``tool_result`` content that contains image blocks: OpenAI's ``tool``
      messages only accept string content, so images get re-emitted as a
      follow-up user message with ``image_url`` parts. Non-vision local
      models (e.g. Qwen 2.5 Coder) will silently ignore them — that's fine
      for development, just don't expect them to "see" viewport snapshots.
    - ``cache_control`` markers are stripped (OpenAI has no equivalent).
    """
    out: list[dict[str, Any]] = [{"role": "system", "content": system}]

    for msg in anthropic_messages:
        role = msg.get("role")
        blocks = msg.get("content", []) or []

        if role == "assistant":
            text_parts: list[str] = []
            tool_calls: list[dict[str, Any]] = []
            for b in blocks:
                t = b.get("type")
                if t == "text":
                    text_parts.append(b.get("text", ""))
                elif t == "tool_use":
                    tool_calls.append(
                        {
                            "id": b["id"],
                            "type": "function",
                            "function": {
                                "name": b["name"],
                                "arguments": json.dumps(
                                    b.get("input", {}), ensure_ascii=False
                                ),
                            },
                        }
                    )
            entry: dict[str, Any] = {"role": "assistant"}
            entry["content"] = "\n".join(text_parts) if text_parts else None
            if tool_calls:
                entry["tool_calls"] = tool_calls
            out.append(entry)
            continue

        # user / tool_result side
        tool_results = [b for b in blocks if b.get("type") == "tool_result"]
        other_blocks = [b for b in blocks if b.get("type") != "tool_result"]

        # OpenAI requires every role:"tool" message to directly follow the
        # assistant turn that carried the tool_calls. Emitting a role:"user"
        # image message right after each image-bearing tool_result would insert
        # a user message BETWEEN two tool messages (when the image-bearing one
        # isn't last) -> 400. Collect all image parts and emit ONE user message
        # after the whole tool loop instead.
        pending_image_blocks: list[dict[str, Any]] = []

        for tr in tool_results:
            content = tr.get("content", "")
            image_blocks: list[dict[str, Any]] = []
            if isinstance(content, list):
                text_parts = []
                for inner in content:
                    if not isinstance(inner, dict):
                        continue
                    if inner.get("type") == "text":
                        text_parts.append(inner.get("text", ""))
                    elif inner.get("type") == "image":
                        image_blocks.append(inner)
                summary = "\n".join(text_parts) if text_parts else ""
                if image_blocks:
                    suffix = (
                        f"[{len(image_blocks)} Bild(er) — folgen als "
                        "Nachricht des Nutzers]"
                    )
                    summary = (summary + "\n" + suffix).strip()
                if not summary:
                    summary = "(leeres Tool-Ergebnis)"
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": tr["tool_use_id"],
                        "content": summary,
                    }
                )
            else:
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": tr["tool_use_id"],
                        "content": str(content) if content else "(leer)",
                    }
                )
            if image_blocks:
                pending_image_blocks.extend(image_blocks)

        # One consolidated user image message AFTER all tool messages, so every
        # role:"tool" stays contiguous right after the assistant turn.
        if pending_image_blocks:
            out.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": _img_to_data_url(b.get("source", {}))
                            },
                        }
                        for b in pending_image_blocks
                    ],
                }
            )

        if other_blocks:
            user_content: list[dict[str, Any]] = []
            for b in other_blocks:
                t = b.get("type")
                if t == "text":
                    user_content.append(
                        {"type": "text", "text": b.get("text", "")}
                    )
                elif t == "image":
                    user_content.append(
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": _img_to_data_url(b.get("source", {}))
                            },
                        }
                    )
            if not user_content:
                continue
            if len(user_content) == 1 and user_content[0]["type"] == "text":
                out.append(
                    {"role": "user", "content": user_content[0]["text"]}
                )
            else:
                out.append({"role": "user", "content": user_content})

    return out
