"""Regressionsnetz: Aufgabenvariante-Counterbalancing (Pre-Mortem 30.06.2026).

Parallel zum bestehenden Bedingungs-Lock: ein zweiter NICHT-Pilot-Lauf desselben
Kuerzels darf nicht dieselbe Aufgabe (task_variant) wie der erste verwenden, sonst
ist der Within-Subjects-Kontrast durch Carryover verzerrt. Geprueft wird:
  - der Teilnehmer-Status meldet used_task_variants / available_task_variants
  - der Create-Endpoint lehnt eine bereits genutzte Aufgabe mit 409 ab
  - die komplementaere Aufgabe wird akzeptiert
Pilotlaeufe sind (wie beim Bedingungs-Lock) ausgenommen.

Lauf (aus rhaino/plugin):
    python -m unittest backend.tests.test_task_variant_guard
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_PLUGIN = os.path.dirname(os.path.dirname(_TESTS_DIR))
_RHAINO = os.path.dirname(_PLUGIN)
for _p in (_PLUGIN, _RHAINO):
    if _p not in sys.path:
        sys.path.insert(0, _p)


class TaskVariantGuardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from backend import session_store
        from backend.config import config

        # ignore_cleanup_errors: die SQLite-Datei (WAL) wird auf Windows beim
        # Teardown evtl. noch vom Prozess gehalten -> rmtree wuerfe PermissionError.
        # Reines Test-Aufraeumen, kein Produktbelang (Backend laeuft langlebig).
        cls._tmp = tempfile.TemporaryDirectory(
            prefix="rhaino-taskguard-test-", ignore_cleanup_errors=True
        )
        session_store._store = session_store.SessionStore(
            os.path.join(cls._tmp.name, "test.db")
        )
        cls._cfg = config
        cls._orig = {"use_mode": config.use_mode, "backend_mode": config.backend_mode,
                     "api_key": config.api_key}
        config.use_mode = "study"
        config.backend_mode = "api"
        config.api_key = "sk-ant-test-dummy"

        from fastapi.testclient import TestClient
        from backend import server

        cls.client = TestClient(server.app)

    @classmethod
    def tearDownClass(cls) -> None:
        from backend import session_store

        for k, v in cls._orig.items():
            setattr(cls._cfg, k, v)
        session_store._store = None
        cls._tmp.cleanup()

    def _create(self, cond, task, pilot=False):
        return self.client.post("/api/study/sessions", json={
            "participant_code": "GUARD", "condition": cond,
            "task_variant": task, "is_pilot": pilot})

    def test_task_variant_lock_and_status(self) -> None:
        c = self.client
        # Lauf 1: basis + A (echter Lauf)
        r1 = self._create("basis", "A")
        self.assertEqual(r1.status_code, 200, r1.text)
        ss1 = r1.json()
        self.assertEqual(ss1["order_index"], 1)
        # Lauf 1 beenden, sonst blockt der Active-Run-Guard den zweiten Lauf
        re = c.post("/api/study/sessions/%s/end" % ss1["id"])
        self.assertEqual(re.status_code, 200, re.text)

        # Status: Aufgabe A genutzt, B frei (parallel zu Bedingung)
        st = c.get("/api/study/participants/GUARD/status",
                   params={"is_pilot": False}).json()
        self.assertEqual(st["used_task_variants"], ["A"])
        self.assertEqual(st["available_task_variants"], ["B"])
        self.assertEqual(st["used_conditions"], ["basis"])

        # Lauf 2 mit DERSELBEN Aufgabe A -> 409 (neuer Guard)
        bad = self._create("werkzeug", "A")
        self.assertEqual(bad.status_code, 409, "gleiche Aufgabe zweimal muss 409 sein: %s" % bad.text)

        # Lauf 2 mit der ANDEREN Aufgabe B -> ok
        ok = self._create("werkzeug", "B")
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertEqual(ok.json()["order_index"], 2)

    def test_pilot_is_exempt(self) -> None:
        # Pilotlaeufe duerfen dieselbe Aufgabe wiederholen (kein Lock)
        a = self.client.post("/api/study/sessions", json={
            "participant_code": "PILOTGUARD", "condition": "basis",
            "task_variant": "A", "is_pilot": True})
        b = self.client.post("/api/study/sessions", json={
            "participant_code": "PILOTGUARD", "condition": "basis",
            "task_variant": "A", "is_pilot": True})
        self.assertEqual(a.status_code, 200, a.text)
        self.assertEqual(b.status_code, 200, b.text)


if __name__ == "__main__":
    unittest.main()
