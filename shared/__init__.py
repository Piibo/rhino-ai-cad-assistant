"""Shared code shared between the MCP server (``rhino_mcp``) and the
standalone plugin (``plugin/``).

Why this package exists:
    Both the MCP server and the plugin's API-mode agent want to execute
    the same Rhino operations (create box, move object, boolean union,
    …). The actual *execution path* differs — MCP sends a Python source
    string over a TCP socket to Rhino's internal ``rhino_script.py``,
    while the plugin runs source strings on Rhino's UI thread directly
    via ``Rhino.RhinoApp.InvokeOnUiThread``. What's identical is the
    *Python source* itself. This package collects those source templates
    in one place so both consumers stay in lockstep.

Module layout:
    ``code_templates``  pure functions that take parameters and return a
                        Python source string ready to be exec'd inside
                        Rhino's CPython 3 (or RhinoScript-compatible
                        IronPython if we ever go back). No imports of
                        rhinoscriptsyntax here — templates are *strings*.
"""
