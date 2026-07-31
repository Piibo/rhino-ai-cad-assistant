"""Pure Formatierungs-/Parse-Helfer fuer die Gate-Vorschaukarte (gate.describe()).

Aus gate.py ausgelagert, damit sie isoliert testbar sind: gate.py zieht
Rhino-only transitive Importe (``..dedicated_tools._shared``), diese drei Helfer
brauchen aber nur die stdlib. Der Test (tests/test_describe_format.py) laedt
dieses Modul daher direkt per importlib — ohne Rhino, ohne neue Dependency.
"""
from __future__ import annotations

import math
from typing import Optional


def _fmt_num(value) -> Optional[str]:
    """Kompakte Zahl ohne ueberfluessige Nullen: 12.0 -> '12', 5.50 -> '5.5'.

    None bei Nicht-Zahl / NaN / +-inf, damit der Aufrufer das Feld einfach
    weglaesst (so steht nie 'None mm' oder 'inf mm' in der Karte).
    """
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):  # NaN oder +/-inf
        return None
    s = "{0:.2f}".format(f).rstrip("0").rstrip(".")
    return s or "0"


def _count(n: int, singular: str, plural: str) -> str:
    """'1 Kante' / '3 Kanten' — korrekte Ein-/Mehrzahl."""
    return "{0} {1}".format(n, singular if n == 1 else plural)


def _int_list(value) -> list[int]:
    """Robuste int-Liste aus einem Index-Feld (Einzelwert ODER Liste)."""
    out: list[int] = []
    if isinstance(value, (list, tuple)):
        for v in value:
            if isinstance(v, bool):
                continue
            if isinstance(v, (int, float)):
                out.append(int(v))
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        out.append(int(value))
    return out
