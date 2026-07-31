"""Backend package for the AI Furniture plugin.

Runs inside Rhino 8's CPython environment as a background thread
alongside the Eto.Forms/WebView2 panel. Hosts the FastAPI server that
the web UI and the MCP bridge both talk to.
"""

__version__ = "0.1.0"
