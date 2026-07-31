"""dedicated_tools.core - Orchestrator dispatch_dedicated_tool."""
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
from .dispatch_tables import _DISPATCH, _GRASSHOPPER_DISPATCH, _SESSION_AWARE_TOOLS
from ._shared import (
    logger,
    _NO_ACTION_LOG_TOOLS,
    _wrap_with_action_log,
    _maybe_sync_structure_after_tool,
)
from .parameters import _handle_expose_parameters, _handle_clear_parameters
from .variants import (
    _handle_create_variant,
    _handle_select_variant,
    _handle_finish_variants,
    _handle_delete_variant,
    _handle_clear_variants,
)

async def dispatch_dedicated_tool(
    name: str,
    tool_input: dict[str, Any],
    session_id: str | None = None,
    correlation_id: str | None = None,
) -> str:
    """Run a dedicated tool by name and return the string result."""
    if name in _SESSION_AWARE_TOOLS:
        if session_id is None:
            return (
                f"Tool '{name}' braucht einen Session-Kontext, hat aber "
                "keinen bekommen."
            )
        if name == "expose_parameters":
            return await _handle_expose_parameters(
                session_id, tool_input, correlation_id
            )
        if name == "clear_parameters":
            return await _handle_clear_parameters(session_id, correlation_id)
        if name == "create_variant":
            return await _handle_create_variant(
                session_id, tool_input, correlation_id
            )
        if name == "select_variant":
            return await _handle_select_variant(
                session_id, tool_input, correlation_id
            )
        if name == "finish_variants":
            return await _handle_finish_variants(session_id, correlation_id)
        if name == "delete_variant":
            return await _handle_delete_variant(
                session_id, tool_input, correlation_id
            )
        if name == "clear_variants":
            return await _handle_clear_variants(session_id, correlation_id)

    if name in _GRASSHOPPER_DISPATCH:
        param_names = _GRASSHOPPER_DISPATCH[name]
        kwargs = {k: tool_input[k] for k in param_names if k in tool_input}
        logger.info("dedicated tool %s - forwarding to grasshopper bridge", name)
        try:
            return await asyncio.to_thread(
                grasshopper_bridge.dispatch_grasshopper_tool, name, kwargs
            )
        except Exception as e:
            logger.exception("dedicated grasshopper tool %s failed: %s", name, e)
            return f"Ausfuehrungsfehler: {e}"

    if name not in _DISPATCH:
        return f"Unbekanntes dediziertes Tool: {name}"

    template_fn, param_names = _DISPATCH[name]
    kwargs = {k: tool_input[k] for k in param_names if k in tool_input}
    try:
        code = template_fn(**kwargs)
    except TypeError as e:
        logger.exception("dedicated tool %s template build failed: %s", name, e)
        return f"Tool-Argumente ungueltig: {e}"

    if name not in _NO_ACTION_LOG_TOOLS:
        code = _wrap_with_action_log(name, code)

    logger.info("dedicated tool %s - running code (%d chars)", name, len(code))
    native_undo_description = None if name in _NO_ACTION_LOG_TOOLS else name
    try:
        result = await asyncio.to_thread(
            rhino_exec.run_code,
            code,
            native_undo_description=native_undo_description,
        )
        if session_id is not None:
            await _maybe_sync_structure_after_tool(
                session_id=session_id,
                tool_name=name,
                tool_input=tool_input,
                tool_result=result,
                correlation_id=correlation_id,
            )
        return result
    except Exception as e:
        logger.exception("dedicated tool %s exec failed: %s", name, e)
        return f"Ausfuehrungsfehler: {e}"
