"""Anthropic agent loop for the Rhino plugin.

On each ``chat.send`` the backend schedules ``run_agent(session_id)`` as an
asyncio task. The agent:

1. Loads the persisted session history.
2. Flattens plugin-specific blocks (selection, point-picks, sketches)
   into Anthropic-native text/image blocks.
3. Calls the Anthropic Messages API in streaming mode with the built-in
   viewport/fallback tools plus the dedicated Rhino tool registry.
4. Persists and broadcasts the assistant response.
5. If ``stop_reason == "tool_use"``, executes the requested tools, persists
   the resulting ``tool_result`` message, and loops.
6. Otherwise emits ``message.complete`` and exits.

Built-in tools:
    - ``capture_viewport``
    - ``execute_rhino_code``

The tool list is assembled per session by ``tool_registry.build_tool_list``
(study condition gate, Studienartefakt-Spec §1.1); ``dedicated_tools``
contributes the dedicated Rhino/Grasshopper schemas.
"""

from .loop import run_agent
from .prompts import SYSTEM_PROMPT, SNAPSHOT_SCENE_PREFIX

__all__ = [
    "run_agent",
    "SYSTEM_PROMPT",
    "SNAPSHOT_SCENE_PREFIX",
]
