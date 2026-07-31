"""Panel tools – read/write the AI-Furniture plugin chat from the MCP side.

Bridges the stdio-based MCP server (used by Claude Code / Desktop) to the
plugin's FastAPI backend (running inside Rhino). Talks over plain HTTP to
``http://127.0.0.1:{port}/api/...``. The port is discovered from
``rhaino/config.json`` (written by the plugin orchestrator at startup).

These tools are the "MCP-mode" path of the HITL loop: the user types into
the panel → plugin stores the message → Claude (via MCP) reads it with
``panel_read_chat`` → Claude narrates progress via ``panel_log_tool_call``
and ``panel_capture_to_chat``, and finally writes the conclusion with
``panel_write_message`` — all broadcast over WebSocket so the UI updates.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any, List, Optional, Union

from mcp.server.fastmcp import Context, FastMCP

logger = logging.getLogger("PanelTools")

_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config.json",
)
_DEFAULT_PORT = 8765


def _plugin_base_url() -> str:
    port = _DEFAULT_PORT
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        port = int(data.get("port", _DEFAULT_PORT))
    except Exception:
        pass
    return f"http://127.0.0.1:{port}"


def _request(
    method: str, path: str, body: Optional[dict] = None, timeout: float = 5.0
) -> Any:
    url = _plugin_base_url() + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    if not raw:
        return None
    return json.loads(raw.decode("utf-8"))


def _first_session_id() -> Optional[str]:
    try:
        sessions = _request("GET", "/api/sessions") or []
        if sessions:
            return sessions[0]["id"]
    except Exception as e:
        logger.warning("_first_session_id: %s", e)
    return None


def _ensure_session_id(session_id: Optional[str]) -> Optional[str]:
    if session_id:
        return session_id
    existing = _first_session_id()
    if existing:
        return existing
    try:
        created = _request("POST", "/api/sessions", {"title": "MCP-Sitzung"})
        return created["id"] if created else None
    except Exception as e:
        logger.warning("_ensure_session_id: %s", e)
        return None


class PanelTools:
    """MCP tools for reading/writing the plugin's chat panel."""

    def __init__(self, app: FastMCP):
        self.app = app
        self._register_tools()

    def _register_tools(self) -> None:
        self.app.tool()(self.panel_get_status)
        self.app.tool()(self.panel_list_sessions)
        self.app.tool()(self.panel_read_chat)
        self.app.tool()(self.panel_write_message)
        self.app.tool()(self.panel_log_tool_call)
        self.app.tool()(self.panel_capture_to_chat)

    def panel_get_status(self, ctx: Context) -> str:
        """Report whether the AI-Furniture plugin backend is reachable.

        Returns a summary with ``status``, ``use_mode``, ``backend_mode``,
        active session id, and connected-client count.
        """
        try:
            health = _request("GET", "/health")
            session_id = _first_session_id()
            return json.dumps(
                {
                    "status": "ok",
                    "health": health,
                    "active_session_id": session_id,
                },
                ensure_ascii=False,
            )
        except urllib.error.URLError as e:
            return f"Plugin-Backend nicht erreichbar ({_plugin_base_url()}): {e}"
        except Exception as e:
            return f"Error: {e}"

    def panel_list_sessions(self, ctx: Context) -> str:
        """List all sessions in the plugin's session store.

        Returns JSON with id, title, created_at, use_mode, participant_id.
        """
        try:
            sessions = _request("GET", "/api/sessions") or []
            return json.dumps(sessions, ensure_ascii=False)
        except Exception as e:
            return f"Error: {e}"

    def panel_read_chat(
        self,
        ctx: Context,
        session_id: Optional[str] = None,
        limit: int = 20,
    ) -> str:
        """Read the latest messages from the plugin's chat panel.

        Args:
            session_id: Explicit session id; defaults to the most recent session.
            limit: Maximum number of trailing messages to return (default 20).

        Returns the messages as JSON, newest last.
        """
        try:
            sid = _ensure_session_id(session_id)
            if sid is None:
                return "Error: no session available"
            messages = _request("GET", f"/api/sessions/{sid}/messages") or []
            if limit and len(messages) > limit:
                messages = messages[-limit:]
            return json.dumps({"session_id": sid, "messages": messages}, ensure_ascii=False)
        except Exception as e:
            return f"Error: {e}"

    def panel_write_message(
        self,
        ctx: Context,
        text: Optional[str] = None,
        content_blocks_json: Optional[str] = None,
        session_id: Optional[str] = None,
        role: str = "assistant",
    ) -> str:
        """Post a message into the plugin's chat panel.

        Used in MCP mode: Claude writes the assistant reply here so the
        panel UI renders it via its WebSocket broadcast.

        Pass either ``text`` (convenience for a single text block) or
        ``content_blocks_json`` (a JSON array of Anthropic-format content
        blocks — text/image/tool_use/tool_result or plugin-native sketch/
        selection/measurement blocks).

        Args:
            text: Plain text content. Rendered as markdown in the panel.
            content_blocks_json: JSON array of content blocks. Overrides text
                if both are given. Example:
                ``'[{"type":"text","text":"Fertig."},{"type":"image","source":{"type":"base64","media_type":"image/png","data":"..."}}]'``
            session_id: Target session. Defaults to the most recent session.
            role: "assistant" (default), "user", or "system".
        """
        if role not in ("assistant", "user", "system"):
            return f"Error: invalid role '{role}'"
        content = _build_content(text, content_blocks_json)
        if isinstance(content, str):
            return content  # error message
        if not content:
            return "Error: provide text or content_blocks_json"
        try:
            sid = _ensure_session_id(session_id)
            if sid is None:
                return "Error: no session available"
            message = {
                "session_id": sid,
                "role": role,
                "content": content,
                "created_at": _iso_now(),
            }
            posted = _request("POST", f"/api/sessions/{sid}/messages", message)
            return json.dumps(
                {"status": "ok", "message_id": posted.get("id") if posted else None},
                ensure_ascii=False,
            )
        except Exception as e:
            return f"Error: {e}"

    def panel_log_tool_call(
        self,
        ctx: Context,
        tool_name: str,
        tool_input_json: str = "{}",
        result_text: str = "",
        is_error: bool = False,
        preamble: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> str:
        """Append an assistant message showing a tool-use and its result.

        Makes Claude's tool usage visible to the designer in the panel UI.
        Call this immediately after you ran an MCP tool (e.g. a geometry
        creation, a SubD modification, a viewport capture) so the chat
        shows what was built and with what parameters.

        Args:
            tool_name: The name of the MCP tool that was invoked.
            tool_input_json: JSON string of the tool input parameters.
            result_text: Short summary of the tool's return value (will be
                truncated if longer than 2000 chars).
            is_error: Set True if the tool failed. Renders in red in the UI.
            preamble: Optional one-line note appearing above the tool card
                (e.g. "Erstelle Sitzfläche…").
            session_id: Target session. Defaults to the most recent.
        """
        try:
            tool_input = json.loads(tool_input_json) if tool_input_json else {}
        except json.JSONDecodeError as e:
            return f"Error: tool_input_json is not valid JSON: {e}"
        if not isinstance(tool_input, dict):
            return "Error: tool_input_json must decode to an object"

        call_id = f"mcp_{_short_id()}"
        blocks: List[dict] = []
        if preamble:
            blocks.append({"type": "text", "text": preamble})
        blocks.append(
            {
                "type": "tool_use",
                "id": call_id,
                "name": tool_name,
                "input": tool_input,
            }
        )
        blocks.append(
            {
                "type": "tool_result",
                "tool_use_id": call_id,
                "content": (result_text or "")[:2000],
                "is_error": bool(is_error),
            }
        )
        try:
            sid = _ensure_session_id(session_id)
            if sid is None:
                return "Error: no session available"
            message = {
                "session_id": sid,
                "role": "assistant",
                "content": blocks,
                "created_at": _iso_now(),
            }
            posted = _request("POST", f"/api/sessions/{sid}/messages", message)
            return json.dumps(
                {
                    "status": "ok",
                    "message_id": posted.get("id") if posted else None,
                    "tool_use_id": call_id,
                },
                ensure_ascii=False,
            )
        except Exception as e:
            return f"Error: {e}"

    def panel_capture_to_chat(
        self,
        ctx: Context,
        caption: Optional[str] = None,
        max_size: int = 800,
        session_id: Optional[str] = None,
    ) -> str:
        """Capture the current Rhino viewport and append it to the chat.

        Useful for showing the designer what the scene looks like after a
        tool call. Uses the existing TCP capture_viewport command on the
        Rhino side, so this only works when the Rhino listener is running
        (``rhino_script.py``).

        Args:
            caption: Optional short text rendered above the image.
            max_size: Largest dimension of the captured PNG in pixels.
            session_id: Target session. Defaults to the most recent.
        """
        try:
            from .rhino_tools import get_rhino_connection
        except Exception as e:
            return f"Error: cannot reach Rhino connection module: {e}"
        try:
            conn = get_rhino_connection()
            result = conn.send_command(
                "capture_viewport",
                {"layer": None, "show_annotations": True, "max_size": int(max_size)},
            )
        except Exception as e:
            return f"Error: Rhino capture failed: {e}"
        if not isinstance(result, dict) or result.get("type") != "image":
            return f"Error: capture returned no image ({result})"
        source = result.get("source") or {}
        data = source.get("data")
        media_type = source.get("media_type", "image/png")
        if not data:
            return "Error: capture returned empty data"

        blocks: List[dict] = []
        if caption:
            blocks.append({"type": "text", "text": caption})
        blocks.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": data,
                },
            }
        )
        try:
            sid = _ensure_session_id(session_id)
            if sid is None:
                return "Error: no session available"
            message = {
                "session_id": sid,
                "role": "assistant",
                "content": blocks,
                "created_at": _iso_now(),
            }
            posted = _request("POST", f"/api/sessions/{sid}/messages", message)
            return json.dumps(
                {
                    "status": "ok",
                    "message_id": posted.get("id") if posted else None,
                    "bytes": len(base64.b64decode(data)) if data else 0,
                },
                ensure_ascii=False,
            )
        except Exception as e:
            return f"Error: {e}"


def _iso_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _short_id() -> str:
    import secrets

    return secrets.token_hex(6)


def _build_content(
    text: Optional[str], content_blocks_json: Optional[str]
) -> Union[List[dict], str]:
    """Resolve the ``text`` / ``content_blocks_json`` pair into a block list.

    Returns a list on success; a string (error message) on failure.
    """
    if content_blocks_json:
        try:
            blocks = json.loads(content_blocks_json)
        except json.JSONDecodeError as e:
            return f"Error: content_blocks_json invalid JSON: {e}"
        if not isinstance(blocks, list):
            return "Error: content_blocks_json must decode to a JSON array"
        for b in blocks:
            if not isinstance(b, dict) or "type" not in b:
                return "Error: each content block needs a 'type' field"
        return blocks
    if text is not None:
        return [{"type": "text", "text": text}]
    return []


__all__ = ["PanelTools"]
