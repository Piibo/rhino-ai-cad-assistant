"""Regressionsnetz: Bedingungs-Gating der Tool-Surface (Spec 1.1/1.2).

``build_tool_list(condition)`` ist die EINZIGE Stelle, die den Tool-Set nach
Bedingung variiert. Der Golden Hash (validate_hashes.py) friert die exakten
Listen ein; dieser Test prueft zusaetzlich die LOGIK mit klaren Meldungen, damit
eine versehentliche Aenderung an INTERACTION_TOOL_NAMES nicht erst in der
Auswertung auffaellt:
  - basis == werkzeug MINUS INTERACTION_TOOL_NAMES (gleiche Reihenfolge)
  - Interaction-Tools sind in werkzeug, aber NIE in basis
  - Lock-Tools (dormant seit 11.06.2026) landen in KEINER Bedingung
  - konkrete Zahlen 94 / 107 / 13 bewusst gepinnt -> aendert sich eine Zahl, ist
    das eine bewusste Scope-Aenderung (Test + Golden Hash zusammen anpassen)

Lauf (aus rhaino/plugin):
    python -m unittest backend.tests.test_tool_registry
"""
from __future__ import annotations

import os
import sys
import unittest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_PLUGIN = os.path.dirname(os.path.dirname(_TESTS_DIR))   # .../rhaino/plugin
_RHAINO = os.path.dirname(_PLUGIN)                        # .../rhaino
for _p in (_PLUGIN, _RHAINO):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from backend.tool_registry import (  # noqa: E402
    build_tool_list,
    INTERACTION_TOOL_NAMES,
    LOCK_TOOL_NAMES,
)


def _names(tools):
    return [t["name"] for t in tools]


class ConditionGatingTest(unittest.TestCase):
    def test_basis_is_werkzeug_minus_interaction(self) -> None:
        werkzeug = _names(build_tool_list("werkzeug"))
        basis = _names(build_tool_list("basis"))
        self.assertEqual(
            basis,
            [n for n in werkzeug if n not in INTERACTION_TOOL_NAMES],
            "basis muss exakt werkzeug ohne INTERACTION_TOOL_NAMES sein (gleiche Reihenfolge)",
        )

    def test_interaction_tools_only_in_werkzeug(self) -> None:
        werkzeug = set(_names(build_tool_list("werkzeug")))
        basis = set(_names(build_tool_list("basis")))
        for name in INTERACTION_TOOL_NAMES:
            self.assertIn(name, werkzeug, "Interaction-Tool fehlt in werkzeug: " + name)
            self.assertNotIn(name, basis, "Interaction-Tool leakt in basis: " + name)

    def test_lock_tools_never_offered(self) -> None:
        for cond in ("basis", "werkzeug"):
            offered = set(_names(build_tool_list(cond)))
            leaked = offered & set(LOCK_TOOL_NAMES)
            self.assertEqual(
                leaked, set(), "Lock-Tools in '%s' angeboten: %s" % (cond, leaked)
            )

    def test_unknown_condition_fails_closed_to_basis(self) -> None:
        # Fail-CLOSED (Pilot 01.07.2026): only the exact string "werkzeug"
        # unlocks the interaction surface. Every other value — unknown, "",
        # or wrong-case — must resolve to the interaction-free basis stack,
        # so a lost/garbled condition never leaks the full surface into a
        # basis run. (Was previously fail-OPEN to werkzeug; a stale-build
        # deploy gap then handed basis participants the dialog tools.)
        basis = _names(build_tool_list("basis"))
        for bad in ("irgendwas-unbekanntes", "", "Werkzeug", "BASIS", "none"):
            self.assertEqual(
                _names(build_tool_list(bad)),
                basis,
                "unbekannte Bedingung '%s' muss fail-closed auf basis fallen" % bad,
            )

    def test_tool_counts_pinned(self) -> None:
        # 30.06.2026: interaction 15 -> 13 (get_selected/get_gh_selected nach
        # core_cad verschoben), basis 92 -> 94. Golden Hash tools_basis bewusst
        # neu verankert. Aendert sich eine Zahl, ist das eine bewusste
        # Scope-Aenderung (diesen Test + Golden Hash zusammen anpassen).
        # 02.07.2026: curve_boolean_union als neues core_cad-Tool (basis
        # 94 -> 95, werkzeug 107 -> 108; interaction unveraendert 13). Pilot-
        # Befund: 2D-Regionen-Union musste via execute_rhino_code improvisiert
        # werden (Einzelbogen-Marathon). Beide Tools-Hashes bewusst neu
        # verankert.
        self.assertEqual(len(INTERACTION_TOOL_NAMES), 13)
        self.assertEqual(len(build_tool_list("basis")), 95)
        self.assertEqual(len(build_tool_list("werkzeug")), 108)

    def test_selection_inspection_is_core_cad(self) -> None:
        # get_selected/get_gh_selected sind read-only KI-Inspektion ohne
        # Designer-Affordanz -> seit 30.06.2026 core_cad (in BEIDEN Bedingungen),
        # damit die Basis-Bedingung eine in Rhino gesetzte Selektion lesen kann
        # und kein Kompetenz-Delta zur Werkzeug-Bedingung entsteht.
        basis = set(_names(build_tool_list("basis")))
        for name in ("get_selected", "get_gh_selected"):
            self.assertIn(name, basis, name + " muss in basis (core_cad) sein")
            self.assertNotIn(
                name,
                INTERACTION_TOOL_NAMES,
                name + " darf nicht mehr interaction sein",
            )
        # select_objects bleibt die KI-zeigt-Deixis-Affordanz -> interaction.
        self.assertIn("select_objects", INTERACTION_TOOL_NAMES)


if __name__ == "__main__":
    unittest.main()
