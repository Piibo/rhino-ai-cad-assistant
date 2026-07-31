"""RhinoMCP – FastMCP server with Rhino, Grasshopper, and dedicated modelling tools."""

from mcp.server.fastmcp import FastMCP
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator, Dict, Any

from .rhino_tools import RhinoTools, get_rhino_connection
from .grasshopper_tools import GrasshopperTools, get_grasshopper_connection
from .geometry_tools import GeometryTools
from .curve_tools import CurveTools
from .surface_tools import SurfaceTools
from .transformation_tools import TransformationTools
from .boolean_tools import BooleanTools
from .subd_tools import SubDTools
from .panel_tools import PanelTools

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("RhinoMCPServer")


@asynccontextmanager
async def server_lifespan(server: FastMCP) -> AsyncIterator[Dict[str, Any]]:
    """Manage server startup and shutdown lifecycle."""
    rhino_conn = None
    gh_conn = None

    try:
        logger.info("RhinoMCP server starting up")

        try:
            rhino_conn = get_rhino_connection()
            rhino_conn.connect()
            logger.info("Connected to Rhino (port %s)", rhino_conn.port)
        except Exception as e:
            logger.warning("Could not connect to Rhino: %s", e)

        try:
            gh_conn = get_grasshopper_connection()
            if gh_conn.check_server_available():
                logger.info("Grasshopper server available (port %s)", gh_conn.port)
            else:
                logger.warning(
                    "Grasshopper server not available – start the GHPython component first."
                )
        except Exception as e:
            logger.warning("Error checking Grasshopper: %s", e)

        yield {}
    finally:
        logger.info("RhinoMCP server shutting down")
        if rhino_conn:
            try:
                rhino_conn.disconnect()
            except Exception:
                pass
        if gh_conn:
            try:
                gh_conn.disconnect()
            except Exception:
                pass


app = FastMCP(
    "RhinoMCP",
    lifespan=server_lifespan,
)

# ── Register tool collections ────────────────────────────────────────────
rhino_tools = RhinoTools(app)
grasshopper_tools = GrasshopperTools(app)
geometry_tools = GeometryTools(app)
curve_tools = CurveTools(app)
surface_tools = SurfaceTools(app)
transformation_tools = TransformationTools(app)
boolean_tools = BooleanTools(app)
subd_tools = SubDTools(app)
panel_tools = PanelTools(app)


# ── Prompts ──────────────────────────────────────────────────────────────

@app.prompt()
def rhino_creation_strategy() -> str:
    """Preferred strategy for creating and managing objects in Rhino."""
    return """When working with Rhino through MCP, follow these guidelines:

    1. Scene Context:
       - Start with get_scene_info() for an overview.
       - Use capture_viewport() to visually verify results.
       - Use get_scene_objects_with_metadata() for detailed filtering.

    2. Object Creation:
       - Prefer the dedicated geometry/curve/surface tools over raw code execution.
       - When using execute_rhino_code(), always call add_object_metadata() after
         creating objects so they can be identified later.
       - Organise objects with layers and meaningful names.

    3. SubD Workflow (unique to this server):
       - Use SubD tools for organic/freeform shapes.
       - Convert existing meshes to SubD with quad_remesh_to_subd().
       - Convert SubD to NURBS for downstream operations with subd_to_nurbs().
       - SubD creasing gives sharp edges while keeping smooth surfaces elsewhere.

    4. Code Execution:
       - This targets Rhino 8 (CPython 3).  Avoid IronPython-only APIs.
       - rhinoscriptsyntax (rs) and RhinoCommon (Rhino.Geometry) are both available.
    """


@app.prompt()
def panel_hitl_workflow() -> str:
    """Preferred workflow when the AI-Furniture panel is active (MCP mode)."""
    return """When the user is running the AI-Furniture plugin (they will usually
    say something like "im Plugin" or "im Panel"), the panel is a persistent
    chat UI inside Rhino. The user types there; your job is to read, act, and
    write back so the result is visible in the panel.

    Standard roundtrip:

    1. Start with ``panel_get_status`` to confirm the backend is reachable and
       pick up the active session id.
    2. ``panel_read_chat`` to see the last ~20 messages — not just the latest
       one, because the user may refer to earlier turns.
    3. Work in Rhino using the normal MCP tools (geometry/curve/surface/subd
       /execute_rhino_code, etc.).
    4. After each meaningful tool call, call ``panel_log_tool_call`` with
       ``tool_name``, ``tool_input_json``, and a short ``result_text``. Use
       ``preamble`` for a natural-language "Ich erstelle jetzt …"
       introduction. This is what makes your work visible in the panel UI.
    5. When the scene has changed, call ``panel_capture_to_chat`` with a
       short caption — the user sees a fresh viewport snapshot inline.
    6. Finish with ``panel_write_message`` containing a plain-text summary
       of what you did and any follow-up questions. Use ``text`` for short
       answers, or ``content_blocks_json`` if you want to mix markdown with
       another image block.

    Style notes:

    - Reply in German (the user's default) unless they switched language.
    - Keep assistant messages short. The designer is in a CAD session, not
      reading an essay.
    - Never write to the panel if you only did a read-only inspection
      (``get_scene_info``, ``get_selected``) — it would clutter the chat.
    - If a tool fails, still log it with ``is_error=True`` so the user sees
      what went wrong.
    - Use ``preamble`` to sequence actions: each ``panel_log_tool_call`` is
      a chat card and appears in the order you call them.
    """


@app.prompt()
def grasshopper_usage_strategy() -> str:
    """Preferred strategy for working with Grasshopper."""
    return """When working with Grasshopper through MCP:

    1. Use get_gh_context(simplified=True) for an overview of the canvas.
    2. Use get_gh_selected() to inspect selected GH components.
    3. Use update_script() to modify GHPython script components.
       - Code must be valid IronPython 2.7 (GHPython still uses IronPython).
       - NO f-strings.  Use .format() or % formatting.
       - Use 'result = value' instead of 'return value'.
    4. Use update_script_with_code_reference() to link components to external .py files.
    5. Work in small steps.  Expire and re-check after changes.
    """


def main():
    """Run the MCP server."""
    app.run(transport="stdio")


if __name__ == "__main__":
    main()
