"""Regression: jeder im Backend-Quelltext emittierte Study-Event-Typ muss das
``StudyContextEvent``-Schema passieren.

Hintergrund (06.07.2026): ``tool_surface`` und ``max_iterations_reached``
wurden seit dem Pilot-Fix (01.07.) emittiert, fehlten aber im
``StudyContextEventType``-Literal. Die Pydantic-ValidationError wurde vom
defensiven ``except`` in ``loop._log_tool_surface`` still geschluckt — die
Events wurden NIE persistiert, der Condition-Audit-Trail fehlte in allen
Export-Bundles, ohne dass es irgendwo sichtbar war. Dieser Test scannt den
Quelltext nach ``event_type``-Literalen und validiert jeden gegen das
Schema, damit diese Bug-Klasse beim naechsten Mal im Testlauf auffliegt.

06.07.2026: Der Scan deckt jetzt alle realen Emit-Formen ab, nicht nur die
kwarg-Form. Zuvor uebersehen: die vier positionalen 2. Argumente an
``_log_affordance_event`` (quick_reply_used/pick_used/sketch_submitted/
variant_selected) und die beiden Ternaerzweige condition_toggle_blocked/
model_change_blocked, die ueber eine Variable emittiert werden. Alle sechs
sind aktuell im Literal — der erweiterte Scan faengt aber ein kuenftiges
Umbenennen, das den Literal-Eintrag vergisst.
"""

from __future__ import annotations

import os
import re
import unittest

from backend import schemas

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Alle Formen, in denen ein event_type-Literal real ins StudyContextEvent-
# Schema fliesst:
#   1) Direkt als kwarg/Zuweisung: event_type="consent_given",
#      StudyContextEvent(event_type="..."), event_type = "..."
#   2) 2. Positionsargument an _log_affordance_event(session_id, "pick_used", …)
#   3) Ternaer zugewiesenes event_type = "a" if … else "b" (beide Zweige)
_EVENT_TYPE_PATTERNS = (
    re.compile(r"event_type\s*=\s*[\"']([a-z_]+)[\"']"),
    re.compile(r"_log_affordance_event\(\s*[^,]+?,\s*[\"']([a-z_]+)[\"']"),
    re.compile(
        r"event_type\s*=\s*\(?\s*[\"']([a-z_]+)[\"']\s*if\b.+?\belse\s+[\"']([a-z_]+)[\"']",
        re.DOTALL,
    ),
)


class EventTypeCoverageTest(unittest.TestCase):
    def test_emitted_event_types_validate(self) -> None:
        emitted: set[str] = set()
        for root, _dirs, files in os.walk(_BACKEND_DIR):
            # Tests duerfen absichtlich ungueltige Typen konstruieren.
            if os.path.basename(root) == "tests":
                continue
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                path = os.path.join(root, fn)
                with open(path, encoding="utf-8") as f:
                    text = f.read()
                for pattern in _EVENT_TYPE_PATTERNS:
                    for match in pattern.findall(text):
                        # Ternaer-Pattern liefert Tupel (beide Zweige).
                        if isinstance(match, tuple):
                            emitted.update(m for m in match if m)
                        else:
                            emitted.add(match)
        self.assertTrue(
            emitted,
            "keine event_type-Literale gefunden — Scan-Pattern defekt?",
        )
        # Bekannte Emissionen muessen dabei sein (Selbsttest des Scans).
        self.assertIn("tool_surface", emitted)
        self.assertIn("max_iterations_reached", emitted)
        # Selbsttest der 06.07. ergaenzten Emit-Formen: positionale Affordanz-
        # Args + condition/model-blocked-Ternaerzweige muessen jetzt erfasst
        # sein, sonst deckt der Scan diese sechs Typen nicht ab.
        for expected in (
            "quick_reply_used",
            "pick_used",
            "sketch_submitted",
            "variant_selected",
            "condition_toggle_blocked",
            "model_change_blocked",
        ):
            self.assertIn(expected, emitted)
        for event_type in sorted(emitted):
            with self.subTest(event_type=event_type):
                schemas.StudyContextEvent(
                    study_session_id="coverage-test",
                    event_type=event_type,  # type: ignore[arg-type]
                )


if __name__ == "__main__":
    unittest.main()
