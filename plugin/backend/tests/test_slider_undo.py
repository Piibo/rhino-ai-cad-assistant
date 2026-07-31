"""Regressionsnetz: Struktur-Slider-Drags muessen undobar sein.

Befund (Kohaerenz-Audit 29.06.2026): `_editable_recipe_action_code` (Box-/
Zylinder-/Extrusionsmasse) und der `scale_axis`-Zweig in `_build_action_code`
mutierten die Geometrie (`Objects.Replace` bzw. `rs.ScaleObject`) OHNE vorheriges
`archive_object` -> die umgebende begin_action/finish_action-Action blieb leer
(kein backup, kein created_id) und wurde von finish_action verworfen -> der
Slider-Drag war ueber KEINEN Mechanismus rueckgaengig zu machen (nativer Ctrl+Z
greift dort ebenfalls nicht).

Fix: `archive_object` VOR der Mutation (analog zu move/scale_uniform/rotate). Da
der Effekt erst in Rhino sichtbar wird, naegelt dieser Test die Invariante am
GENERIERTEN Code-String fest: `archive_object` muss VOR dem Replace/ScaleObject
stehen. Beide Funktionen sind reine String-Generatoren -> kein Rhino noetig.

Lauf (aus rhaino/plugin):
    python -m unittest backend.tests.test_slider_undo
"""
from __future__ import annotations

import os
import sys
import unittest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_PLUGIN = os.path.dirname(os.path.dirname(_TESTS_DIR))   # .../rhaino/plugin
_RHAINO = os.path.dirname(_PLUGIN)                        # .../rhaino  -> 'shared'
for _p in (_PLUGIN, _RHAINO):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_GUID = "11111111-1111-1111-1111-111111111111"


class SliderReversibilityTest(unittest.TestCase):
    def test_editable_recipe_archives_before_replace(self) -> None:
        """Box-/Zylinder-/Extrusions-Slider: archive_object vor Objects.Replace."""
        from backend import schemas
        from backend.dedicated_tools import _shared

        action = schemas.ParameterAction(
            type="editable_recipe_value",
            target_object_ids=[_GUID],
            parameter_key="width",
        )
        code = _shared._editable_recipe_action_code(action, 5.0)
        self.assertIn("archive_object", code)
        self.assertIn("Objects.Replace", code)
        self.assertLess(
            code.index("archive_object"),
            code.index("Objects.Replace"),
            "archive_object muss VOR Objects.Replace stehen, sonst nicht undobar",
        )

    def test_scale_axis_archives_before_scaleobject(self) -> None:
        """Inline scale_axis-Slider: archive_object vor rs.ScaleObject."""
        from backend import schemas
        from backend.dedicated_tools import _shared

        action = schemas.ParameterAction(
            type="scale_axis",
            axis="x",
            target_object_ids=[_GUID],
            origin=[0.0, 0.0, 0.0],
        )
        code = _shared._build_action_code(action, delta=1.0, new_current=5.0)
        self.assertIn("archive_object", code)
        self.assertIn("ScaleObject", code)
        self.assertLess(
            code.index("archive_object"),
            code.index("ScaleObject"),
            "archive_object muss VOR rs.ScaleObject stehen, sonst nicht undobar",
        )

    def test_move_and_scale_uniform_still_archive(self) -> None:
        """Regression: die bereits korrekten Transform-Slider archivieren weiter."""
        from backend import schemas
        from backend.dedicated_tools import _shared

        for atype in ("move_axis", "scale_uniform"):
            action = schemas.ParameterAction(
                type=atype,
                axis="x",
                target_object_ids=[_GUID],
                origin=[0.0, 0.0, 0.0],
            )
            code = _shared._build_action_code(action, delta=1.0, new_current=5.0)
            self.assertIn(
                "archive_object", code, "%s sollte vor der Mutation archivieren" % atype
            )


class NiceStepTest(unittest.TestCase):
    """nice_step fuellt fehlende Slider-Steps mit RUNDEN Werten (1/2/5 x 10^k),
    statt des krummen (max-min)/100-Frontend-Fallbacks -> einheitliche Granularitaet
    ueber alle Slider-Quellen (Kohaerenz-Audit #6/low)."""

    def test_returns_round_steps(self) -> None:
        from backend.parameter_derivation import nice_step

        self.assertAlmostEqual(nice_step(100.0), 1.0)   # 1.0   -> 1
        self.assertAlmostEqual(nice_step(1000.0), 10.0)  # 10.0  -> 10
        self.assertAlmostEqual(nice_step(50.0), 0.5)    # 0.5   -> 0.5
        self.assertAlmostEqual(nice_step(73.0), 1.0)    # 0.73  -> 1
        self.assertAlmostEqual(nice_step(300.0), 5.0)   # 3.0   -> 5

    def test_handles_degenerate_span(self) -> None:
        from backend.parameter_derivation import nice_step

        self.assertEqual(nice_step(0.0), 1.0)
        self.assertEqual(nice_step(-5.0), 1.0)


if __name__ == "__main__":
    unittest.main()
