"""Round-trip-Netz fuer den Studien-Fragebogen: SUBMIT -> PERSIST -> EXPORT.

Dieser Pfad (Demografie + Per-Bedingungs-Block + Vergleichsblock -> SQLite ->
Export-Bundle) hatte bisher KEINE automatische Abdeckung. Ein stiller Verlust
(ein Feld landet nicht in der DB oder nicht im Export) wuerde erst bei der
Auswertung auffallen -- zu spaet. Dieser Test trifft die ECHTEN FastAPI-
Endpoints (dieselben Calls wie das Frontend) gegen eine ISOLIERTE Temp-DB und
prueft auf drei Ebenen:

  1. Submit-Response  -> Wert wird gespeichert + korrekt zurueckserialisiert
  2. Persistenz-GET   -> Wert ueberlebt in der DB
  3. Export-CSV       -> Wert landet in der konsolidierten survey_export.csv

Zusaetzlich: Bedingungs-Gating (basis blendet werkzeug-only Items aus).

Kein Rhino noetig. Lauf (aus rhaino/plugin):
    python -m unittest backend.tests.test_survey_roundtrip
"""
from __future__ import annotations

import csv
import io
import os
import sys
import tempfile
import unittest
import zipfile

# --- 'backend' und 'shared' ausserhalb von Rhino importierbar machen ----------
# 'shared' (generierte IronPython-Code-Templates als Strings) liegt in rhaino/,
# eine Ebene ueber plugin/; in Rhino legt der Plugin-Loader das auf den Pfad.
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_PLUGIN = os.path.dirname(os.path.dirname(_TESTS_DIR))   # .../rhaino/plugin
_RHAINO = os.path.dirname(_PLUGIN)                        # .../rhaino  -> 'shared'
for _p in (_PLUGIN, _RHAINO):
    if _p not in sys.path:
        sys.path.insert(0, _p)


class SurveyRoundTripTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # Isolierte Temp-DB + Temp-Export-Ordner setzen, BEVOR die App den Store
        # oder den Export anfasst. Der Store legt sein Schema beim Konstruieren an.
        from backend import session_store
        from backend.config import config

        cls._tmp = tempfile.TemporaryDirectory(prefix="rhaino-survey-test-")
        db_path = os.path.join(cls._tmp.name, "test.db")
        session_store._store = session_store.SessionStore(db_path)
        cls._export_dir = os.path.join(cls._tmp.name, "exports")

        # Studien-Endpoints sind gegated: is_study_mode (use_mode == "study") und
        # backend_mode == "api". Beides fuer den Test setzen + Originale merken.
        cls._cfg = config
        cls._orig = {
            "use_mode": config.use_mode,
            "backend_mode": config.backend_mode,
            "export_dir": config.export_dir,
            "api_key": config.api_key,
        }
        config.use_mode = "study"
        config.backend_mode = "api"
        # Studienmodus verlangt einen hinterlegten API-Key (Presence-Check, kein
        # echter LLM-Call im Survey-/Export-Pfad) -> Dummy genuegt.
        config.api_key = "sk-ant-test-dummy"
        config.export_dir = cls._export_dir  # export.py liest dieselbe Instanz
        assert config.is_study_mode, "Studienmodus liess sich nicht aktivieren"

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

    def _create_session(self, condition: str, task_variant: str) -> dict:
        r = self.client.post(
            "/api/study/sessions",
            json={
                "participant_code": "TESTRT",
                "condition": condition,
                "task_variant": task_variant,
                "is_pilot": True,  # Pilot -> nicht vom "ein Lauf pro Bedingung"-Lock betroffen
            },
        )
        self.assertEqual(r.status_code, 200, f"create {condition}: {r.text}")
        return r.json()

    def _consent(self, ss_id: str) -> None:
        ct = self.client.get("/api/study/consent-text")
        self.assertEqual(ct.status_code, 200, ct.text)
        ctj = ct.json()
        # Checkboxen sind serverseitig cb1..cbN gekeyt (nicht nach Label), alle True.
        checkboxes = {f"cb{i + 1}": True for i in range(len(ctj["checkbox_labels"]))}
        r = self.client.post(
            f"/api/study/sessions/{ss_id}/consent",
            json={
                "study_session_id": ss_id,
                "text_hash": ctj["text_hash"],
                "checkboxes": checkboxes,
            },
        )
        self.assertEqual(r.status_code, 200, f"consent: {r.text}")

    def _read_survey_csv_row(self) -> dict:
        zips = [f for f in os.listdir(self._export_dir) if f.endswith(".zip")]
        self.assertTrue(zips, "kein Export-Bundle (.zip) erzeugt")
        row = None
        for zname in zips:
            with zipfile.ZipFile(os.path.join(self._export_dir, zname)) as z:
                if "survey_export.csv" not in z.namelist():
                    continue
                with z.open("survey_export.csv") as f:
                    # utf-8-sig: die CSV traegt bewusst ein BOM (Excel-Erkennung);
                    # utf-8-sig entfernt es beim Lesen (sonst im ersten Header).
                    rows = list(csv.DictReader(io.TextIOWrapper(f, "utf-8-sig")))
                if rows:
                    row = rows[0]
        self.assertIsNotNone(row, "survey_export.csv fehlt/leer im Bundle")
        return row

    # -- the round trip --------------------------------------------------------

    def test_full_round_trip(self) -> None:
        c = self.client

        # ===== Bedingung 1: basis =====
        ss1 = self._create_session("basis", "A")
        self.assertEqual(ss1["order_index"], 1)
        self.assertEqual(ss1["condition"], "basis")
        self._consent(ss1["id"])

        demo = {
            "age_range": "35-44",
            "gender": "divers",
            "field": "Architektur",
            "design_experience_years": "3-5",
            "cad_experience_years": "1-2",
            "rhino_self_assessment": 4,
            "grasshopper_self_assessment": 2,
            "other_cad_tools": ["Fusion360", "SketchUp"],
            "genai_usage_frequency": "woechentlich",
            "genai_design_tools": ["Midjourney"],
            "furniture_design_experience": "wenig",
        }
        r = c.post(f"/api/study/sessions/{ss1['id']}/demographics", json=demo)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["rhino_self_assessment"], 4)  # Ebene 1
        # Ebene 2: Persistenz ueber die Teilnehmer-Lane
        got = c.get("/api/study/participants/TESTRT/demographics", params={"is_pilot": True})
        self.assertEqual(got.status_code, 200, got.text)
        self.assertEqual(got.json()["age_range"], "35-44")

        # Gating: basis-Survey-Config darf KEINE werkzeug-only Tool-Items zeigen
        cfg_b = c.get(f"/api/study/sessions/{ss1['id']}/survey-config")
        self.assertEqual(cfg_b.status_code, 200, cfg_b.text)
        basis_tool_ids = {t["id"] for t in cfg_b.json()["tool_items"]}
        self.assertIn("tool_text", basis_tool_ids)        # Kern-Item, beide Bedingungen
        self.assertNotIn("tool_slider", basis_tool_ids)   # werkzeug-only -> ausgeblendet
        self.assertNotIn("tool_pick", basis_tool_ids)

        basis_survey = {
            "self_efficacy": 6, "control": 5, "autonomy": 4, "ownership": 7,
            "csi_exploration": 3, "csi_expressiveness": 2, "csi_immersion": 5,
            "csi_enjoyment": 6, "csi_results_worth_effort": 4, "csi_collaboration": 1,
            "tool_text": 5, "tool_image_ref": 4, "tool_viewport_feedback": 6,
            "collab_editor_trap": 2, "collab_expression_limit": 6,
            "tools_unused_freetext": "nichts gefehlt",
            "reflection_freetext": "basis war ok",
        }
        r = c.post(f"/api/study/sessions/{ss1['id']}/survey", json=basis_survey)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["self_efficacy"], 6)       # Ebene 1
        self.assertEqual(r.json()["collab_editor_trap"], 2)
        got = c.get(f"/api/study/sessions/{ss1['id']}/survey")  # Ebene 2
        self.assertEqual(got.status_code, 200, got.text)
        self.assertEqual(got.json()["csi_collaboration"], 1)

        # ===== Bedingung 2: werkzeug =====
        ss2 = self._create_session("werkzeug", "B")
        # Seit 02.07.2026 zaehlen auch Pilot-Laeufe ihre Lane fortlaufend
        # (1..n): die "erste/zweite Bedingung"-Richtung des Vergleichsblocks
        # (V1/V2) muss auch im Pilot ueber order_index rekonstruierbar sein
        # (Pilot-Befund 01.07.: alle Laeufe trugen 1). Locks/Visit-Zaehlung
        # bleiben fuer Pilots weiterhin ausgesetzt.
        self.assertEqual(ss2["order_index"], 2)
        self.assertNotEqual(ss2["id"], ss1["id"])
        self.assertEqual(ss2["condition"], "werkzeug")
        self._consent(ss2["id"])

        cfg_w = c.get(f"/api/study/sessions/{ss2['id']}/survey-config")
        self.assertEqual(cfg_w.status_code, 200, cfg_w.text)
        w_tool_ids = {t["id"] for t in cfg_w.json()["tool_items"]}
        self.assertIn("tool_pick", w_tool_ids)  # werkzeug-only Item -> sichtbar
        # §4.3-Gating positiv verifiziert: nutzungs-gegatete Items ohne Nutzung
        # bleiben AUCH in werkzeug ausgeblendet — Slider (kein User-Slider-Event)
        # und Referenzbild (kein Upload) erscheinen nicht. Speicherbar sind sie
        # trotzdem (Display-Gating != Save-Gating) -> der Export-Check unten
        # beweist, dass ein dennoch uebermittelter Wert round-trippt.
        self.assertNotIn("tool_slider", w_tool_ids)
        self.assertNotIn("tool_image_ref", w_tool_ids)
        # Zusammenarbeit-Items (Z1/Z2) in beiden Bedingungen vorhanden;
        # Z3 Systemkompetenz (02.07.2026) lebt als EIGENES competence_items-
        # Feld, weil es am absoluten Block-ENDE gerendert wird (Manipulation-
        # Check-Reihenfolge-Literatur: ein Kompetenz-Item vor den Werkzeug-
        # Ratings wuerde diese framen).
        w_collab_ids = {i["id"] for i in cfg_w.json()["collaboration_items"]}
        self.assertEqual(
            w_collab_ids, {"collab_editor_trap", "collab_expression_limit"}
        )
        w_comp_ids = {i["id"] for i in cfg_w.json()["competence_items"]}
        self.assertEqual(w_comp_ids, {"collab_system_competence"})

        w_survey = {
            "self_efficacy": 7, "control": 6, "autonomy": 5, "ownership": 6,
            "csi_exploration": 5, "csi_expressiveness": 6, "csi_immersion": 4,
            "csi_enjoyment": 5, "csi_results_worth_effort": 6, "csi_collaboration": 7,
            "tool_text": 6, "tool_image_ref": 5, "tool_viewport_feedback": 7,
            "tool_selection_badge": 4, "tool_pick": 6, "tool_slider": 7,
            "tool_call_cards": 5, "tool_variants": 3, "tool_sketch": 6,
            "tool_dialog_offers": 5,
            "collab_editor_trap": 5, "collab_expression_limit": 3,
            "collab_system_competence": 6,
            "tools_unused_selection": ["tool_lock"],
            "tools_unused_freetext": "Lock nicht genutzt",
            "reflection_freetext": "werkzeug besser",
        }
        r = c.post(f"/api/study/sessions/{ss2['id']}/survey", json=w_survey)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["tool_slider"], 7)
        self.assertEqual(r.json()["tool_dialog_offers"], 5)
        self.assertEqual(r.json()["collab_system_competence"], 6)

        # ===== Vergleichsblock (auf dem 2. Lauf) =====
        final = {
            "preference_choice": "second",
            "preference_freetext": "Werkzeug klar besser",
            "v2_control": 2, "v2_expressiveness": 1, "v2_exploration": 2,
            "v2_speed": -1, "v2_trust": 1, "v2_ownership": 2,
            "tool_importance_rank_1": "tool_slider",
            "tool_importance_rank_2": "tool_pick",
            "tool_importance_rank_3": "tool_sketch",
            "hybrid_mode_freetext": "nach Grobform wechseln",
            "surprise_freetext": "war ueberrascht",
            "missing_freetext": "fehlte etwas",
            "wish_freetext": "wuensche mehr",
        }
        r = c.post(f"/api/study/sessions/{ss2['id']}/final-survey", json=final)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["preference_choice"], "second")
        self.assertEqual(r.json()["v2_control"], 2)

        # ===== Export + Verifikation der konsolidierten survey_export.csv =====
        r = c.post(f"/api/study/sessions/{ss2['id']}/export")
        self.assertEqual(r.status_code, 200, r.text)
        row = self._read_survey_csv_row()

        # Demografie
        self.assertEqual(row["demo_age_range"], "35-44")
        self.assertEqual(row["demo_rhino_self_assessment"], "4")
        self.assertIn("Fusion360", row["demo_other_cad_tools"])
        self.assertIn("Midjourney", row["demo_genai_design_tools"])

        # Per-Bedingungs-Block (basis_ / werkzeug_)
        self.assertEqual(row["basis_self_efficacy"], "6")
        self.assertEqual(row["basis_csi_collaboration"], "1")
        self.assertEqual(row["basis_collab_editor_trap"], "2")
        self.assertEqual(row["basis_tool_viewport_feedback"], "6")
        self.assertEqual(row["werkzeug_self_efficacy"], "7")
        self.assertEqual(row["werkzeug_tool_slider"], "7")
        self.assertEqual(row["werkzeug_tool_dialog_offers"], "5")
        self.assertEqual(row["werkzeug_collab_expression_limit"], "3")
        self.assertIn("tool_lock", row["werkzeug_tools_unused_selection"])

        # Vergleichsblock (final_)
        self.assertEqual(row["final_preference_choice"], "second")
        self.assertEqual(row["final_v2_control"], "2")
        self.assertEqual(row["final_v2_speed"], "-1")
        self.assertEqual(row["final_tool_importance_rank_1"], "tool_slider")


if __name__ == "__main__":
    unittest.main(verbosity=2)
