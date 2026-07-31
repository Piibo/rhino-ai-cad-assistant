"""Regressionsnetz: das × auf dem Parameter-Panel muss GH-Slider WIRKLICH entfernen.

Befund (Live-Test 29.06.2026): Der ×-Knopf konnte GH-gestuetzte Slider nicht
dauerhaft wegklicken. Ursache: der ×-Pfad (``_handle_parameter_clear`` ->
``clear_session_parameters(preserve_router_priority=False)``) loescht zwar die
exponierten Parameter, laesst aber den aktiven GRASSHOPPER-Strukturkontext
stehen. Das Frontend (ParameterPanel) rendert das Panel aber bereits, solange
ENTWEDER Parameter ODER ein Strukturkontext da ist (``showStructureContext``) ->
der GH-Kontext haelt das Panel sichtbar, und der naechste
``sync_session_parameters`` kann die Slider aus diesem Kontext re-derivven.

Fix: der ×-Handler ruft jetzt
``clear_session_parameters(..., clear_grasshopper_context=True)`` -> der
Voll-Clear-Zweig raeumt Kontext UND Parameter ab und broadcastet beide als leer.
Dieser zuvor SCHLAFENDE Code-Pfad (``clear_grasshopper_context=True`` hatte
keinen Aufrufer) wird hier erstmals exerziert + festgenagelt.

Selbstheilend: ein bewusstes Re-Expose der KI (apply_parameter_set) loest den
GH-Kontext spaeter wieder auf. Kein periodischer GH-Poll exposed Slider von
selbst -> der Clear haelt. Kein Rhino noetig.

Lauf (aus rhaino/plugin):
    python -m unittest backend.tests.test_gh_param_clear
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest

# --- 'backend' und 'shared' ausserhalb von Rhino importierbar machen ----------
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_PLUGIN = os.path.dirname(os.path.dirname(_TESTS_DIR))   # .../rhaino/plugin
_RHAINO = os.path.dirname(_PLUGIN)                        # .../rhaino  -> 'shared'
for _p in (_PLUGIN, _RHAINO):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _make_gh_param():
    """Ein gh_slider-Parameter, wie ihn ein Parametrisieren-Call exponiert.

    Der Validator setzt ``source`` automatisch auf ``gh_slider``, weil eine
    gh_slider-action dranhaengt.
    """
    from backend import schemas

    return schemas.ExposedParameter(
        name="Fase Kante #9",
        current=2.0,
        min=0.0,
        max=10.0,
        step=0.1,
        actions=[
            schemas.ParameterAction(
                type="gh_slider", instance_guid="slider-guid-abc"
            )
        ],
    )


def _make_gh_context(session_id: str):
    from backend import schemas

    return schemas.EditableStructureContext(
        session_id=session_id,
        structure_type="grasshopper",
        structure_key="gh",
        title="Grasshopper-Definition",
        source="parameters",
        parameter_names=["Fase Kante #9"],
    )


class GhParameterClearTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from backend import session_store

        # ignore_cleanup_errors: SQLite oeffnet/schliesst pro Operation eine
        # eigene Verbindung; unter Windows kann die Temp-DB beim Cleanup noch
        # kurz gelockt sein -> Cleanup-Fehler ignorieren (Temp-Dir raeumt das OS).
        cls._tmp = tempfile.TemporaryDirectory(
            prefix="rhaino-ghclear-test-", ignore_cleanup_errors=True
        )
        db_path = os.path.join(cls._tmp.name, "test.db")
        session_store._store = session_store.SessionStore(db_path)

    @classmethod
    def tearDownClass(cls) -> None:
        import gc

        from backend import session_store

        session_store._store = None
        gc.collect()  # offene sqlite-Verbindungen freigeben, bevor wir loeschen
        cls._tmp.cleanup()

    def setUp(self) -> None:
        from backend import schemas, session_store

        self.store = session_store.get_store()
        # Eigene Session pro Test (FK fuer active_structure_contexts).
        self.sid = self.store.create_session(schemas.Session()).id

    def _arm_gh_panel(self) -> None:
        """Aktiven GH-Kontext + einen gh_slider-Parameter setzen (Zustand direkt
        nach einem Parametrisieren-Call mit GH-Ergebnis)."""
        self.store.set_active_structure_context(_make_gh_context(self.sid))
        self.store.set_exposed_parameters(
            self.sid, [_make_gh_param()], "2026-06-29T00:00:00+00:00"
        )
        # Vorbedingung: Panel waere sichtbar (Parameter + Kontext da).
        self.assertIsNotNone(self.store.get_active_structure_context(self.sid))
        self.assertEqual(len(self.store.get_exposed_parameters(self.sid)), 1)

    def test_x_full_clear_removes_gh_context_and_params(self) -> None:
        """FIX-Pfad: clear_grasshopper_context=True raeumt Kontext UND Slider ab,
        Return + DB sind leer -> Frontend unmountet das Panel."""
        from backend import structure_context_service as svc

        self._arm_gh_panel()

        ctx_out, params_out = asyncio.run(
            svc.clear_session_parameters(
                self.sid,
                None,
                clear_grasshopper_context=True,
                preserve_router_priority=False,
            )
        )

        self.assertIsNone(ctx_out)
        self.assertEqual(params_out, [])
        # In der DB tatsaechlich weg -> kein Re-Derive-Naehrboden mehr.
        self.assertIsNone(self.store.get_active_structure_context(self.sid))
        self.assertEqual(self.store.get_exposed_parameters(self.sid), [])

    def test_clear_survives_a_resync(self) -> None:
        """Nach dem Voll-Clear darf ein erneuter sync_session_parameters die
        GH-Slider NICHT wiederherstellen (Kontext ist weg)."""
        from backend import structure_context_service as svc
        from backend.parameter_derivation import sync_session_parameters

        self._arm_gh_panel()
        asyncio.run(
            svc.clear_session_parameters(
                self.sid,
                None,
                clear_grasshopper_context=True,
                preserve_router_priority=False,
            )
        )

        ctx = self.store.get_active_structure_context(self.sid)  # None nach Clear
        merged = sync_session_parameters(self.sid, structure_context=ctx)
        self.assertEqual(merged, [])
        self.assertEqual(self.store.get_exposed_parameters(self.sid), [])

    def test_old_x_path_leaves_gh_context_behind(self) -> None:
        """Dokumentiert, WARUM der Flag noetig ist: ohne clear_grasshopper_context
        ueberlebt der GH-Kontext den Clear -> showStructureContext haelt das Panel
        sichtbar (= das Symptom 'GH-Slider lassen sich nicht wegklicken')."""
        from backend import structure_context_service as svc

        self._arm_gh_panel()
        asyncio.run(
            svc.clear_session_parameters(
                self.sid,
                None,
                preserve_router_priority=False,
            )
        )

        # Parameter sind weg ...
        self.assertEqual(self.store.get_exposed_parameters(self.sid), [])
        # ... aber der GH-Kontext steht noch (genau das haelt das Panel offen).
        self.assertIsNotNone(self.store.get_active_structure_context(self.sid))


class AbortRestoreCodeTest(unittest.TestCase):
    """Das Verwerfen schickt generierten Rhino-Python (run_code) zum Original-
    Restore. Der String wird in IronPython AUSGEFUEHRT — ein Syntaxfehler oder
    eine kaputte ID-Einbettung wuerde erst im Live-Test (in Rhino) auffallen.
    Hier wird der generierte Code statisch geprueft: syntaktisch valides Python +
    die gepickten object_ids korrekt eingebettet + die Kern-Restore-Schritte da."""

    def test_generated_code_is_valid_python_and_embeds_ids(self) -> None:
        import ast

        from backend import server

        code = server._abort_restore_code(["guid-aaa", "guid-bbb"])
        ast.parse(code)  # wirft SyntaxError bei kaputtem Code
        self.assertIn("guid-aaa", code)
        self.assertIn("guid-bbb", code)
        # Kern-Schritte: Archive-Match ueber archive_source_id, Restore auf Active.
        self.assertIn("archive_source_id", code)
        self.assertIn("'Active'", code)
        self.assertIn("CopyObject", code)
        self.assertIn("result =", code)

    def test_empty_ids_still_valid(self) -> None:
        import ast

        from backend import server

        code = server._abort_restore_code([])
        ast.parse(code)
        self.assertIn("ids = []", code)

    def test_ids_are_safely_quoted(self) -> None:
        """object_ids werden als Python-Literal (repr) eingebettet, nicht roh
        interpoliert -> ein ' im Namen darf den Code nicht zerbrechen."""
        import ast

        from backend import server

        code = server._abort_restore_code(["a'b", 'c"d'])
        ast.parse(code)


if __name__ == "__main__":
    unittest.main()
