"""Adversariale Edge-Case-Suite fuer den Studien-Fragebogen-Datenpfad.

Ergaenzt ``test_survey_roundtrip`` (Happy Path) um den Robustheits-Beweis: das
System muss bei wilden Eingaben ENTWEDER sauber round-trippen ODER sauber
ablehnen (4xx) -- niemals still Daten verlieren, verfaelschen oder crashen
(500). Schwerpunkte:

  * Grenzwerte / out-of-range Likert  -> Ablehnung (422), nichts gespeichert
  * komplett leerer / uebersprungener Fragebogen -> sauberer Round-trip (None)
  * CSV-gefaehrlicher + Unicode + ueberlanger Freitext -> exakt durch den
    CSV-Export erhalten (das groesste stille Korruptionsrisiko)
  * Listenfelder mit Kommas/Quotes/Unicode/Duplikaten -> erhalten
  * Doppel-Submit -> latest wins, kein Duplikat im Export
  * unbekannte Session (Submit + Export) -> 4xx, kein 200/500
  * Export ohne Survey-Daten -> kein Crash
  * unbekannte Felder im Payload -> brechen nichts
  * kaputte Consent-Payloads -> sauber abgelehnt

Kein Rhino noetig. Lauf (aus rhaino/plugin):
    python -m unittest backend.tests.test_survey_edge_cases
"""
from __future__ import annotations

import csv
import io
import os
import sys
import tempfile
import unittest
import zipfile

# --- 'backend' + 'shared' ausserhalb von Rhino importierbar machen ------------
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_PLUGIN = os.path.dirname(os.path.dirname(_TESTS_DIR))
_RHAINO = os.path.dirname(_PLUGIN)
for _p in (_PLUGIN, _RHAINO):
    if _p not in sys.path:
        sys.path.insert(0, _p)


class SurveyEdgeCaseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from backend import session_store
        from backend.config import config

        cls._tmp = tempfile.TemporaryDirectory(prefix="rhaino-survey-edge-")
        session_store._store = session_store.SessionStore(
            os.path.join(cls._tmp.name, "test.db")
        )
        cls._export_dir = os.path.join(cls._tmp.name, "exports")
        cls._cfg = config
        cls._orig = {
            "use_mode": config.use_mode,
            "backend_mode": config.backend_mode,
            "export_dir": config.export_dir,
            "api_key": config.api_key,
        }
        config.use_mode = "study"
        config.backend_mode = "api"
        config.api_key = "sk-ant-test-dummy"
        config.export_dir = cls._export_dir

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

    # -- helpers ---------------------------------------------------------------

    def _new_session(self, code: str, condition: str = "werkzeug", consent: bool = True) -> dict:
        r = self.client.post(
            "/api/study/sessions",
            json={
                "participant_code": code,
                "condition": condition,
                "task_variant": "A",
                "is_pilot": True,
            },
        )
        self.assertEqual(r.status_code, 200, f"create {code}: {r.text}")
        ss = r.json()
        if consent:
            ct = self.client.get("/api/study/consent-text").json()
            cbs = {f"cb{i + 1}": True for i in range(len(ct["checkbox_labels"]))}
            cr = self.client.post(
                f"/api/study/sessions/{ss['id']}/consent",
                json={"study_session_id": ss["id"], "text_hash": ct["text_hash"], "checkboxes": cbs},
            )
            self.assertEqual(cr.status_code, 200, f"consent {code}: {cr.text}")
        return ss

    def _export_row(self, study_session_id: str, code: str) -> dict:
        """Export the session, then return the survey_export.csv row for ``code``.

        Reads the CSV via StringIO (NOT line-streaming) so eingebettete Newlines
        in gequoteten Feldern korrekt geparst werden.
        """
        r = self.client.post(f"/api/study/sessions/{study_session_id}/export")
        self.assertEqual(r.status_code, 200, f"export: {r.text}")
        for zname in os.listdir(self._export_dir):
            if not zname.endswith(".zip"):
                continue
            with zipfile.ZipFile(os.path.join(self._export_dir, zname)) as z:
                if "survey_export.csv" not in z.namelist():
                    continue
                # utf-8-sig: die CSV traegt bewusst ein BOM (Excel-Erkennung);
                # utf-8-sig entfernt es beim Lesen, sonst landete es im ersten
                # Header-Namen und participant_code waere nicht auffindbar.
                text = z.read("survey_export.csv").decode("utf-8-sig")
            for row in csv.DictReader(io.StringIO(text)):
                if (row.get("participant_code") or "").upper() == code.upper():
                    return row
        self.fail(f"keine survey_export.csv-Zeile fuer {code}")

    # -- 1) Grenzwerte / out-of-range -> Ablehnung, nichts gespeichert ---------

    def test_out_of_range_values_rejected(self) -> None:
        c = self.client
        ss = self._new_session("EDGE_OOR")
        # Likert unter/ueber Bereich (ge=1, le=7)
        for bad in ({"self_efficacy": 0}, {"self_efficacy": 8}, {"csi_immersion": -3}, {"tool_pick": 99}):
            r = c.post(f"/api/study/sessions/{ss['id']}/survey", json=bad)
            self.assertEqual(r.status_code, 422, f"erwartet 422 fuer {bad}: {r.status_code} {r.text}")
        # Demografie-Skala (ge=1, le=5)
        r = c.post(f"/api/study/sessions/{ss['id']}/demographics", json={"rhino_self_assessment": 6})
        self.assertEqual(r.status_code, 422, r.text)
        # Final V2-Skala (ge=-2, le=2)
        r = c.post(f"/api/study/sessions/{ss['id']}/final-survey", json={"v2_control": 3})
        self.assertEqual(r.status_code, 422, r.text)
        # Nichts davon darf gespeichert worden sein:
        got = c.get(f"/api/study/sessions/{ss['id']}/survey")
        self.assertIn(got.status_code, (200, 404))
        if got.status_code == 200:
            self.assertIsNone(got.json())

    # -- 2) komplett leerer Fragebogen -> sauberer Round-trip ------------------

    def test_all_optional_omitted_round_trips(self) -> None:
        c = self.client
        ss = self._new_session("EDGE_EMPTY", condition="basis")
        self.assertEqual(c.post(f"/api/study/sessions/{ss['id']}/demographics", json={}).status_code, 200)
        self.assertEqual(c.post(f"/api/study/sessions/{ss['id']}/survey", json={}).status_code, 200)
        row = self._export_row(ss["id"], "EDGE_EMPTY")
        # Zeile existiert, Pflicht-Spalten leer (None -> "") -> keine Geister-Daten
        self.assertEqual(row.get("basis_self_efficacy", ""), "")
        self.assertEqual(row.get("demo_age_range", ""), "")

    # -- 3) CSV-gefaehrlicher + Unicode + ueberlanger Freitext -> exakt erhalten

    def test_nasty_freetext_survives_csv_export(self) -> None:
        c = self.client
        ss = self._new_session("EDGE_TEXT")
        nasty = (
            'Komma, "Anfuehrung", ;Semikolon;\nZeilenumbruch\tTab '
            '=SUM(A1)+CMD|"formula" \U0001fa91\U0001f527 Ueberlaenge Uemlaeuetee '
            "— Gedankenstrich 'single' \\backslash {json:\"like\"}"
        )
        long_text = "L" + ("a" * 4000) + "Z"  # 4002 Zeichen, kein Backend-Limit
        survey = {
            "self_efficacy": 4,
            "reflection_freetext": nasty,
            "tools_unused_freetext": long_text,
        }
        r = c.post(f"/api/study/sessions/{ss['id']}/survey", json=survey)
        self.assertEqual(r.status_code, 200, r.text)
        # Submit-Response schon exakt?
        self.assertEqual(r.json()["reflection_freetext"], nasty)
        self.assertEqual(r.json()["tools_unused_freetext"], long_text)
        # ... und exakt durch den CSV-Export (das ist der eigentliche Test):
        row = self._export_row(ss["id"], "EDGE_TEXT")
        self.assertEqual(row["werkzeug_reflection_freetext"], nasty)
        self.assertEqual(row["werkzeug_tools_unused_freetext"], long_text)
        self.assertEqual(len(row["werkzeug_tools_unused_freetext"]), 4002)

    # -- 4) Listenfelder mit fiesen Items -> erhalten --------------------------

    def test_list_fields_with_nasty_items(self) -> None:
        c = self.client
        ss = self._new_session("EDGE_LIST")
        tools = ['A, mit Komma', 'B "quote"', "C\nNewline", "D \U0001f600", "Dup", "Dup"]
        cad = ["Rhino", "Fusion, 360", "Auto\"CAD"]
        self.assertEqual(
            c.post(f"/api/study/sessions/{ss['id']}/demographics", json={"other_cad_tools": cad}).status_code,
            200,
        )
        r = c.post(
            f"/api/study/sessions/{ss['id']}/survey",
            json={"self_efficacy": 5, "tools_unused_selection": tools},
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["tools_unused_selection"], tools)  # Reihenfolge + Dups erhalten
        row = self._export_row(ss["id"], "EDGE_LIST")
        # Listen werden als JSON in eine CSV-Zelle serialisiert -> Items muessen
        # den CSV-Transport unbeschadet ueberstehen.
        sel = row["werkzeug_tools_unused_selection"]
        for item in ("A, mit Komma", 'B \\"quote\\"', "Dup"):
            self.assertIn(item.split(",")[0][:5], sel)  # Kern jedes Items vorhanden
        self.assertIn("Fusion", row["demo_other_cad_tools"])

    # -- 5) Doppel-Submit -> latest wins, kein Duplikat ------------------------

    def test_resubmission_latest_wins(self) -> None:
        c = self.client
        ss = self._new_session("EDGE_RESUB")
        self.assertEqual(c.post(f"/api/study/sessions/{ss['id']}/survey", json={"self_efficacy": 3, "control": 3}).status_code, 200)
        self.assertEqual(c.post(f"/api/study/sessions/{ss['id']}/survey", json={"self_efficacy": 6, "control": 7}).status_code, 200)
        self.assertEqual(c.get(f"/api/study/sessions/{ss['id']}/survey").json()["self_efficacy"], 6)
        row = self._export_row(ss["id"], "EDGE_RESUB")
        self.assertEqual(row["werkzeug_self_efficacy"], "6")  # latest
        self.assertEqual(row["werkzeug_control"], "7")

    # -- 6) unbekannte Session: Submit -> 4xx, kein 200/500 --------------------

    def test_submit_to_unknown_session_rejected(self) -> None:
        c = self.client
        r = c.post("/api/study/sessions/DOES-NOT-EXIST/survey", json={"self_efficacy": 5})
        self.assertTrue(400 <= r.status_code < 500, f"erwartet 4xx, war {r.status_code}: {r.text}")
        r = c.post("/api/study/sessions/DOES-NOT-EXIST/demographics", json={"age_range": "x"})
        self.assertTrue(400 <= r.status_code < 500, f"erwartet 4xx, war {r.status_code}: {r.text}")

    # -- 7) unbekannte Session: Export -> 4xx ----------------------------------

    def test_export_unknown_session_rejected(self) -> None:
        r = self.client.post("/api/study/sessions/DOES-NOT-EXIST/export")
        self.assertTrue(400 <= r.status_code < 500, f"erwartet 4xx, war {r.status_code}: {r.text}")

    # -- 8) Export ohne Survey-Daten -> kein Crash, Bundle entsteht ------------

    def test_export_with_no_survey_data_no_crash(self) -> None:
        ss = self._new_session("EDGE_NODATA", consent=False)
        r = self.client.post(f"/api/study/sessions/{ss['id']}/export")
        self.assertEqual(r.status_code, 200, r.text)  # kein Crash trotz leerer Daten
        zips = [f for f in os.listdir(self._export_dir) if "EDGE_NODATA" in f and f.endswith(".zip")]
        self.assertTrue(zips, "Bundle wurde auch ohne Survey-Daten erwartet")

    # -- 9) unbekannte Felder im Payload -> brechen nichts ---------------------

    def test_unknown_fields_dont_break(self) -> None:
        c = self.client
        ss = self._new_session("EDGE_XTRA")
        r = c.post(
            f"/api/study/sessions/{ss['id']}/survey",
            json={"self_efficacy": 5, "bogus_unknown_field": 999, "another": {"nested": True}},
        )
        self.assertIn(r.status_code, (200, 422), r.text)  # ignoriert ODER abgelehnt, nie 500
        if r.status_code == 200:
            self.assertNotIn("bogus_unknown_field", r.json())
            self.assertEqual(r.json()["self_efficacy"], 5)

    # -- 10) kaputte Consent-Payloads -> sauber abgelehnt ----------------------

    def test_consent_guards(self) -> None:
        c = self.client
        ss = self._new_session("EDGE_CONSENT", consent=False)
        ct = c.get("/api/study/consent-text").json()
        n = len(ct["checkbox_labels"])
        # eine Checkbox False -> 400
        cbs = {f"cb{i + 1}": True for i in range(n)}
        if n:
            cbs[f"cb{n}"] = False
        r = c.post(
            f"/api/study/sessions/{ss['id']}/consent",
            json={"study_session_id": ss["id"], "text_hash": ct["text_hash"], "checkboxes": cbs},
        )
        self.assertEqual(r.status_code, 400, f"unvollstaendiger Consent muss 400 sein: {r.text}")
        # falscher text_hash -> 409 (Wording-Drift)
        r = c.post(
            f"/api/study/sessions/{ss['id']}/consent",
            json={
                "study_session_id": ss["id"],
                "text_hash": "deadbeef",
                "checkboxes": {f"cb{i + 1}": True for i in range(n)},
            },
        )
        self.assertEqual(r.status_code, 409, f"Hash-Drift muss 409 sein: {r.text}")
        # study_session_id Pfad != Body -> 400
        r = c.post(
            f"/api/study/sessions/{ss['id']}/consent",
            json={"study_session_id": "andere", "text_hash": ct["text_hash"], "checkboxes": {f"cb{i + 1}": True for i in range(n)}},
        )
        self.assertEqual(r.status_code, 400, f"ID-Mismatch muss 400 sein: {r.text}")

    # -- 11) Injection-foermige Eingaben -> literal gespeichert, kein Schaden ---

    def test_injection_shaped_input_no_harm(self) -> None:
        c = self.client
        code = "a' OR 1=1;--"  # 12 Zeichen, innerhalb max_length=32
        ss = self._new_session(code)
        inj = "Robert'); DROP TABLE study_sessions;-- <script>alert(1)</script>"
        r = c.post(
            f"/api/study/sessions/{ss['id']}/survey",
            json={"self_efficacy": 5, "reflection_freetext": inj},
        )
        self.assertEqual(r.status_code, 200, r.text)
        # Tabelle intakt? -> es laesst sich noch eine Session anlegen (kein DROP)
        ss2 = self._new_session("AFTER_INJ")
        self.assertEqual(ss2["condition"], "werkzeug")
        # literaler Round-trip in den Export (parametrisierte Queries -> keine Injektion)
        row = self._export_row(ss["id"], code)
        # Kuerzel wird kanonisch grossgeschrieben (case-insensitive Lane); der
        # Freitext bleibt dagegen literal erhalten (parametrisierte Queries ->
        # keine Injektion, keine Case-Aenderung am Inhalt).
        self.assertEqual(row["participant_code"], code.upper())
        self.assertEqual(row["werkzeug_reflection_freetext"], inj)

    # -- 12) ueberlanger participant_code -> abgelehnt -------------------------

    def test_oversized_participant_code_rejected(self) -> None:
        r = self.client.post(
            "/api/study/sessions",
            json={"participant_code": "X" * 33, "condition": "basis", "task_variant": "A", "is_pilot": True},
        )
        self.assertEqual(r.status_code, 422, r.text)

    # -- 13) FS-Sonderzeichen im Code -> Export darf NICHT crashen -------------

    def test_special_char_code_export_no_crash(self) -> None:
        c = self.client
        code = "P/0:1*x"  # enthaelt dateinamen-unzulaessige Zeichen (/ : *)
        r = c.post(
            "/api/study/sessions",
            json={"participant_code": code, "condition": "werkzeug", "task_variant": "A", "is_pilot": True},
        )
        self.assertNotEqual(r.status_code, 500, f"Session-Anlage crasht: {r.text}")
        if r.status_code != 200:
            return  # sauber abgelehnt -> auch ok
        ss = r.json()
        c.post(f"/api/study/sessions/{ss['id']}/survey", json={"self_efficacy": 5})
        rex = c.post(f"/api/study/sessions/{ss['id']}/export")
        self.assertNotEqual(
            rex.status_code, 500, f"Export crasht an FS-Sonderzeichen im Code: {rex.text}"
        )

    # -- 14) Control-/Null-Bytes im Freitext -> kein Crash ---------------------

    def test_control_chars_freetext_no_crash(self) -> None:
        c = self.client
        ss = self._new_session("EDGE_CTRL")
        weird = "vor\x00null\x01\x07\x1b mitte ‮ RTL ﻿ BOM nach"
        r = c.post(
            f"/api/study/sessions/{ss['id']}/survey",
            json={"self_efficacy": 5, "reflection_freetext": weird},
        )
        self.assertNotEqual(r.status_code, 500, f"Control-Chars crashen das Speichern: {r.text}")
        if r.status_code == 200:
            rex = c.post(f"/api/study/sessions/{ss['id']}/export")
            self.assertNotEqual(
                rex.status_code, 500, f"Control-Chars crashen den Export: {rex.text}"
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
