"""HTTP bridge for Grasshopper tools in API mode.

The Grasshopper MCP tools do not execute Rhino Python in-process like the
other dedicated tools. They talk to the GHPython HTTP server exposed by
the Grasshopper side. This module keeps that transport separate from the
Rhino UI-thread executor.
"""

from __future__ import annotations

import json
import logging
import socket
import time
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger("FurniturePlugin.GrasshopperBridge")

_BASE_URL = "http://127.0.0.1:9998"
_TIMEOUT = 30.0
# Short timeout for the liveness probe so a hung GH can't stall the 3 s
# status poll for the full 30 s. Real tool calls keep _TIMEOUT.
_HEALTH_TIMEOUT = 2.0

# Auto-connect tuning. When a GH tool call hits a dead :9998 server we try to
# launch GH ourselves and poll until the HTTP server answers, then retry the
# original call exactly once. The first launch waits up to _LAUNCH_DEADLINE s
# (cold GH start + assembly load + server bootstrap). A second, near-in-time
# call doesn't re-launch — GH is already starting — it just polls briefly
# (_RELAUNCH_POLL s) in case the server came up in the meantime.
_LAUNCH_DEADLINE = 25.0
_RELAUNCH_POLL = 5.0
_POLL_INTERVAL = 1.0
# Cooldown after a launch attempt: within this window we don't fire another
# launch (GH is mid-startup), we only poll. Module-global so concurrent /
# back-to-back tool calls coordinate.
_RELAUNCH_COOLDOWN = 30.0
_last_launch_attempt = 0.0

# Returned verbatim by dispatch_grasshopper_tool when the GH HTTP server
# is unreachable. agent.py imports this exact string and, when it shows
# up in a tool result, short-circuits the turn with a fixed chat message
# — no LLM round-trip for a deterministic "Grasshopper is down" case.
GH_NOT_CONNECTED_MESSAGE = (
    "Grasshopper ist nicht verbunden — der GH-Server auf Port 9998 "
    "antwortet nicht."
)


def _post(
    command_type: str,
    payload: dict[str, Any] | None = None,
    timeout: float = _TIMEOUT,
) -> Any:
    body = {"type": command_type}
    if payload:
        body.update({k: v for k, v in payload.items() if v is not None})

    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        _BASE_URL,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
    return json.loads(raw)


def _format_response(response: Any) -> str:
    # GH usually returns a JSON object, but a list/scalar/null is possible;
    # guard so the response.get(...) calls below can't throw a cryptic
    # AttributeError on a non-dict payload.
    if not isinstance(response, dict):
        if isinstance(response, list):
            return json.dumps(response, indent=2)
        return str(response)
    if response.get("status") == "error":
        return "Error: {0}".format(
            response.get("result") or response.get("message") or "Unknown error"
        )

    payload = response.get("result", response)
    if isinstance(payload, (dict, list)):
        return json.dumps(payload, indent=2)
    return str(payload)


def _is_server_available() -> bool:
    """Cheap liveness probe against the GH HTTP server on :9998."""
    try:
        _post("test_command", timeout=_HEALTH_TIMEOUT)
        return True
    except Exception:
        return False


def _try_autoconnect() -> bool:
    """Launch GH (if not already mid-startup) and poll until :9998 answers.

    Returns True if the GH HTTP server became reachable within the deadline,
    False otherwise. Runs blocking ``time.sleep`` — fine because the whole
    bridge call already executes inside ``asyncio.to_thread`` (see
    dedicated_tools/core.py), so it never blocks the FastAPI event loop.

    A module-global timestamp throttles launches: the first failing call
    triggers ``launch_grasshopper`` and polls up to _LAUNCH_DEADLINE s; a
    second call arriving while GH is still booting skips the relaunch and
    only polls briefly (_RELAUNCH_POLL s).
    """
    global _last_launch_attempt

    now = time.time()
    within_cooldown = (now - _last_launch_attempt) < _RELAUNCH_COOLDOWN
    if within_cooldown:
        # GH was launched very recently — don't fire a second launch, just
        # give the in-flight startup a short additional window to come up.
        deadline = time.time() + _RELAUNCH_POLL
        logger.info("GH auto-connect: relaunch suppressed (cooldown), polling briefly")
    else:
        _last_launch_attempt = now
        try:
            from .grasshopper_launch import launch_grasshopper

            response = launch_grasshopper(timeout=_LAUNCH_DEADLINE)
            logger.info("GH auto-connect: launch_grasshopper -> %s", str(response)[:200])
        except Exception as e:
            logger.exception("GH auto-connect: launch_grasshopper failed: %s", e)
        deadline = time.time() + _LAUNCH_DEADLINE

    while time.time() < deadline:
        if _is_server_available():
            logger.info("GH auto-connect: :9998 is up")
            return True
        time.sleep(_POLL_INTERVAL)
    logger.info("GH auto-connect: :9998 still unreachable after deadline")
    return False


def dispatch_grasshopper_tool(name: str, tool_input: dict[str, Any]) -> str:
    """Run one Grasshopper tool call and return a formatted string result."""
    try:
        if name == "is_server_available":
            return "True" if _is_server_available() else "False"

        command_type = {
            "execute_gh_code": "execute_code",
            "get_gh_context": "get_context",
            "get_objects": "get_objects",
            "get_gh_selected": "get_selected",
            "update_script": "update_script",
            "update_script_with_code_reference": "update_script_with_code_reference",
            "expire_and_get_info": "expire_component",
            "add_component": "add_component",
            "clear_gh_session": "clear_ai_components",
            "arrange_gh_layout": "arrange_layout",
        }.get(name)

        if command_type is None:
            return "Unbekanntes Grasshopper-Tool: {0}".format(name)

        try:
            response = _post(command_type, tool_input)
        except (urllib.error.URLError, TimeoutError, socket.timeout) as conn_exc:
            # :9998 is dead or hung — Grasshopper is not connected. Before
            # falling back to the "GH not connected" message, try to bring GH
            # up ourselves (launch + poll), then retry the original call once.
            # HTTPError is a URLError subclass but means GH *answered* with an
            # error, so it's excluded here and handled below as a real fault.
            if isinstance(conn_exc, urllib.error.HTTPError):
                raise
            logger.info(
                "grasshopper tool %s — GH not reachable, attempting auto-connect: %s",
                name,
                conn_exc,
            )
            if _try_autoconnect():
                response = _post(command_type, tool_input)
            else:
                return GH_NOT_CONNECTED_MESSAGE
        return _format_response(response)
    except urllib.error.HTTPError as e:
        try:
            details = e.read().decode("utf-8")
        except Exception:
            details = str(e)
        logger.exception("grasshopper tool %s http error", name)
        return "Error: HTTP {0} - {1}".format(e.code, details)
    except urllib.error.URLError as e:
        # Connection refused / no route — the GH HTTP server on :9998 is
        # not answering, i.e. Grasshopper is not connected. The agent loop
        # detects GH_NOT_CONNECTED_MESSAGE in the tool result and ends the
        # turn with a fixed chat message — no LLM round-trip needed.
        logger.info("grasshopper tool %s — GH not reachable: %s", name, e)
        return GH_NOT_CONNECTED_MESSAGE
    except (TimeoutError, socket.timeout) as e:
        # GH accepted the connection but the request hung (busy solver /
        # heavy definition). A read-timeout is socket.timeout, NOT a URLError
        # subclass, so it would otherwise fall through to the generic branch.
        # Treat it as "not reachable" so the agent short-circuits instead of
        # paying an LLM round-trip for a generic timeout.
        logger.info("grasshopper tool %s timed out (GH busy): %s", name, e)
        return GH_NOT_CONNECTED_MESSAGE
    except Exception as e:
        logger.exception("grasshopper tool %s failed", name)
        return "Error: {0}".format(e)


def set_slider_value(instance_guid: str, value: float) -> str:
    """Set a Grasshopper Number Slider and trigger recomputation."""
    try:
        response = _post(
            "set_slider_value",
            {"instance_guid": instance_guid, "value": value},
        )
        return _format_response(response)
    except urllib.error.HTTPError as e:
        try:
            details = e.read().decode("utf-8")
        except Exception:
            details = str(e)
        logger.exception("set_slider_value http error")
        return "Error: HTTP {0} - {1}".format(e.code, details)
    except Exception as e:
        logger.exception("set_slider_value failed")
        return "Error: {0}".format(e)


__all__ = ["dispatch_grasshopper_tool", "set_slider_value"]
