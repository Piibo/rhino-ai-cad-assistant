"""dedicated_tools - Paket-Split der frueheren dedicated_tools.py.

Duenne Fassade: re-exportiert exakt die oeffentliche Oberflaeche, damit
Aufrufer weiter backend.dedicated_tools.<name> bzw.
'from .dedicated_tools import <name>' nutzen koennen (keine Aenderung an
agent.py / server.py / tool_registry.py). Schichtung (nur abwaerts):
dispatch_tables, _shared -> parameters, variants -> core -> __init__.
Siehe ARCHITECTURE.md Abschnitt 8.
"""
from ..tool_schemas import TOOL_SCHEMAS
from .dispatch_tables import is_dedicated_tool
from .core import dispatch_dedicated_tool
from .parameters import apply_parameter_change
from .variants import sync_variant_state, show_original, commit_active_variant

__all__ = [
    "TOOL_SCHEMAS",
    "is_dedicated_tool",
    "dispatch_dedicated_tool",
    "apply_parameter_change",
    "sync_variant_state",
    "show_original",
    "commit_active_variant",
]
