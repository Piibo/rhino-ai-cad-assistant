"""Golden-Hash-Regressionstest fuer das Studienartefakt.

Friert die Reproduzierbarkeits-Anker ein, die ins manifest.json gehen:
- system_prompt_hash = sha256(agent.SYSTEM_PROMPT)
- tools_hash('basis')   = sha256(json.dumps(build_tool_list('basis'), sort_keys=True))
- tools_hash('werkzeug') = dasselbe fuer die Werkzeug-Bedingung

Zweck: Waehrend eines Refactorings sofort sichtbar machen, wenn sich der
an das Modell exponierte System-Prompt oder die Tool-Surface (Namen,
Schemata, Reihenfolge) unbeabsichtigt aendert. Beruehrt KEINEN
Produktionscode, nur Lesezugriff ueber die kanonischen export-Funktionen.

Nutzung:
    python validate_hashes.py            # vergleicht gegen Golden-Werte
    python validate_hashes.py --print    # nur aktuelle Werte ausgeben

Nach einer BEWUSSTEN, freigegebenen Aenderung an Prompt/Tools die Golden-
Werte unten neu verankern (Werte aus --print uebernehmen) und im Commit
dokumentieren, warum sich der Hash aendern durfte.

Exit 0 = identisch, 1 = Drift, 2 = nicht verankert / Importfehler.
"""
from __future__ import annotations

import argparse
import os
import sys
import types

_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
# Mirror start_plugin.py: plugin/ fuer backend.*, rhaino/ fuer shared.*
sys.path.insert(0, os.path.dirname(_PLUGIN_DIR))
sys.path.insert(0, _PLUGIN_DIR)
# anthropic wird von agent.py nur lazy importiert; Stub als Sicherheitsnetz,
# damit der Hash-Check auch ohne installiertes SDK laeuft.
sys.modules.setdefault("anthropic", types.ModuleType("anthropic"))

# Verankerte Golden-Hashes. Leer = noch nicht gesetzt: erst `--print`
# laufen lassen, dann die Werte hier eintragen.
# Verankert am 08.06.2026 (vor Beginn des Refactorings, Commit 351ba46).
# Re-Anchoring nur nach bewusster, freigegebener Prompt-/Tool-Aenderung.
# 09.06.2026: system_prompt RE-ANCHORED nach Live-Test-Fix gegen Groessenverlust
# — neue Regel im SYSTEM_PROMPT: bei Radius-/Fasen-Aenderung einer gerundeten Box
# diese mit den AKTUELLEN Abmessungen (get_object_info-Bbox) neu aufbauen, nicht
# mit den urspruenglichen Erzeugungswerten.
# 09.06.2026: tools_werkzeug RE-ANCHORED — boolean_difference behaelt jetzt die
# Cutter (delete_input-Default False + Beschreibung), Ergebnis ersetzt nur keep_id.
# Behebt den Live-Test-Bug "Beine von Sitzflaeche abziehen loescht die Beine".
# tools_basis unveraendert (Boolean-Tools sind nicht in der Basis-Bedingung).
# 11.06.2026: tools_basis RE-ANCHORED wegen der Bedingungslogik-Korrektur
# (Studienartefakt-Spec §1.1, Designkorrektur): die Basis-Bedingung ist nicht
# mehr die alte Drei-Tool-Variante, sondern teilt jetzt denselben Core-CAD-Kern
# wie werkzeug; nur die 17 Interaction-Tools (tool_registry.INTERACTION_TOOL_NAMES)
# werden gestript. Bewusste, freigegebene Tool-Surface-Aenderung, kein Refactoring.
# system_prompt UNVERAENDERT (Spec §1.2: identischer Prompt in beiden Bedingungen;
# das Tool-Gating uebernimmt allein build_tool_list). tools_werkzeug UNVERAENDERT
# (volle Liste in Inhalt UND Reihenfolge erhalten -> Prompt-Cache der
# Werkzeug-Bedingung bleibt gueltig).
# 11.06.2026: tools_werkzeug RE-ANCHORED — das Lock-Feature wurde aus der
# Modell-Tool-Surface entfernt (lock_object/unlock_object/list_locked_objects
# nicht mehr in build_tool_list('werkzeug'); INTERACTION_TOOL_NAMES 17 -> 14).
# Bewusste, freigegebene Tool-Surface-Aenderung. Die Lock-Persistenz (DB/REST/
# dispatch/session_store/schemas/export) bleibt dormant. system_prompt
# UNVERAENDERT (Lock-Addendum ist dynamisch in loop.py, nicht im statischen
# SYSTEM_PROMPT) und tools_basis UNVERAENDERT (Locks waren nie in der Basis).
# 15.06.2026: system_prompt RE-ANCHORED — P2 Referenz-Echo (COFI-Rueckkanal):
# Bestaetigung welches Element welchen Objekts nach Aenderung an referenzierter
# Stelle. Bedingungs-symmetrisch formuliert (Spec §1.2: identischer Prompt in
# basis+werkzeug). tools_basis und tools_werkzeug UNVERAENDERT.
# 16.06.2026: ALLE DREI RE-ANCHORED — Auto-Expose der Groessen-Slider beim
# Erstellen editierbarer Primitive, aber NUR wenn der Designer keine Masse
# nennt (Designentscheidung). (a) tool_schemas: create_box width/depth/height
# jetzt optional mit Default 100 (required -> []), damit das Modell sie
# weglassen kann = Signal "keine Masse". create_cylinder war bereits optional.
# -> tools_basis + tools_werkzeug RE-ANCHORED (create_box ist Core-CAD, in
# beiden Bedingungen). (b) system_prompt: neue Regel, dass das Modell die
# Groessen-Argumente bei masslosem Auftrag weglaesst. Bedingungs-symmetrisch
# (Spec §1.2). Die Slider selbst rendern nur in werkzeug (App.tsx gated die
# ParameterPanel) — der Prompt/das Tool-Gating bleibt bedingungsidentisch.
# Bewusste, freigegebene Prompt-/Tool-Surface-Aenderung, kein Refactoring.
# 18.06.2026: system_prompt RE-ANCHORED - Repair-on-Error-Regel:
# Nach einem fehlgeschlagenen Tool-Result soll das Modell nicht blind denselben
# Call wiederholen, sondern Diagnose + konkrete Alternative liefern. In
# werkzeug, falls request_confirmation verfuegbar ist, als Reparaturkarte mit
# Enter-Bestaetigung/Abbrechen; in basis verbal. Bedingungs-symmetrisch
# formuliert, Tool-Surface unveraendert.
# 19.06.2026: tools_werkzeug RE-ANCHORED — request_choice-Optionen koennen jetzt
# eine optionale ``reference`` (object_id + optional component_type/
# component_index) tragen. Hover ueber eine Option hebt damit die gemeinte
# Geometrie non-mutating im Viewport hervor (gleicher DisplayConduit-Pfad wie
# die Inline-Referenz-Tokens). request_choice ist werkzeug-only
# (INTERACTION_TOOL_NAMES) -> tools_basis UNVERAENDERT. Die Modell-Anleitung
# liegt in der Schema-Feldbeschreibung, NICHT im SYSTEM_PROMPT -> system_prompt
# UNVERAENDERT. Bewusste, freigegebene Tool-Surface-Aenderung, kein Refactoring.
# 19.06.2026: system_prompt + tools_werkzeug RE-ANCHORED — neues Dialog-Tool
# ``request_parameters`` (Plural): fragt MEHRERE zusammengehoerige Masse in EINER
# gebuendelten Karte ab (Live-Test-Wunsch: statt zwei Parameter-Karten
# nacheinander eine zusammengefuegte). (a) tools_werkzeug: neues Tool in
# DIALOG_TOOL_SCHEMAS + INTERACTION_TOOL_NAMES (werkzeug-only) -> tools_basis
# UNVERAENDERT (Plural-Tool ist Interaction, wird in basis gestript). (b)
# system_prompt: eine Zeile ergaenzt — Einzahl-Tool fuer EIN Mass, Plural-Tool
# fuer MEHRERE; ohne diese Zeile blieb das Modell beim Einzel-Tool (Ursache der
# zwei Karten). Bedingungs-symmetrisch formuliert (Spec §1.2; das Tool-Gating
# uebernimmt allein build_tool_list). Bewusste, freigegebene Aenderung.
# 19.06.2026: system_prompt RE-ANCHORED — „Geh keine Umwege"-Regel ergaenzt:
# einen fehlgeschlagenen geometrischen Pfad nicht stur mit anderen Indizes
# wiederholen; bei Topologie-Fehlern (z.B. interior kink beim Fillet einer
# geschlossenen Extrusion/Revolve) erst get_brep_component_info pruefen, dann
# round_edges_by_rule bzw. Neuaufbau per loft_curves. Behebt das Umweg-/
# Herumirr-Verhalten aus der Vasen/Tisch-Session. Bedingungs-symmetrisch;
# tools_basis + tools_werkzeug UNVERAENDERT (reine Prompt-Regel).
# 30.06.2026: tools_basis RE-ANCHORED — get_selected + get_gh_selected aus
# INTERACTION_TOOL_NAMES nach core_cad verschoben (Interaction 15 -> 13, basis
# 92 -> 94). Begruendung: beide sind read-only KI-Inspektion ohne Designer-
# Affordanz/FF-Spur; werkzeug-only haetten sie der Werkzeug-Bedingung eine
# Faehigkeit gegeben, die basis fehlt (eine direkt in Rhino gesetzte Selektion
# lesen) — Verstoss gegen die 11.06.-Korrektur (gemeinsamer Core-CAD-Kern).
# Bewusste, freigegebene Tool-Surface-Aenderung. tools_werkzeug UNVERAENDERT
# (Menge gleich, get_* waren dort immer enthalten) und system_prompt UNVERAENDERT.
# 02.07.2026: system_prompt RE-ANCHORED — sechs Pilot-Lessons (01.07.) als
# generalisierte Heuristiken (Default + Begruendung + explizite Ausnahme,
# KEINE Verbote — ein gewollter Einzelbogen bleibt legitim): (R1) Konturen
# ueber Rhinos Kurvenoperationen konstruieren (fillet_curve/Kurven-Boolean/
# interpolierte Kurve) statt Tangenten selbst trigonometrisch zu berechnen
# (PILOT2_basis: 48x create_arc + 25x delete_object Einzelbogen-Marathon);
# (R2) Iterations-Disziplin: wirkt dieselbe Parameter-Korrektur zweimal
# nicht, Ansatz wechseln statt weiterdrehen; (R3) Szenen-Hygiene: Reste
# alter Versuche im selben Schritt loeschen, nie auf vermuellter Szene
# diagnostizieren; (R4) unabhaengige Tool-Calls in einem Zug buendeln;
# (R5) Kontroll-Screenshot am Sequenz-ENDE statt nach jedem Trivialschritt
# (formkritische Schritte weiter sofort pruefbar); (R6) Dialog-Karten-
# Passage um "wenn nicht verfuegbar -> knappe Textfrage" ergaenzt (macht den
# bedingungssymmetrischen Prompt robust fuer basis, wo die Karten-Tools
# gestrippt sind; Spec §1.2 bleibt gewahrt: EIN Prompt fuer beide
# Bedingungen). Bewusste, freigegebene Prompt-Aenderung vor dem Pilot-Rerun.
# tools_basis + tools_werkzeug UNVERAENDERT (keine Schema-/Surface-Aenderung).
# 02.07.2026: ALLE DREI RE-ANCHORED — neues core_cad-Tool ``curve_boolean_union``
# (basis 94 -> 95, werkzeug 107 -> 108, INTERACTION unveraendert 13; Parität
# der Bedingungen bleibt gewahrt, kein Affordanz-Zuwachs). Pilot-Befund
# 01.07.: fuer "zwei ueberlappende Regionen verschmelzen" existierte kein
# dediziertes Tool, das Modell improvisierte Tangenten-Trigonometrie via
# execute_rhino_code (48x create_arc Einzelbogen-Marathon, PILOT2_basis).
# Das Tool kapselt rs.CurveBooleanUnion mit Input-Validierung (geschlossen/
# planar), archive_object-Backups und klaren Fehlermeldungen. system_prompt
# mit-veraendert: R1 + Kurven-Katalog referenzieren jetzt das dedizierte Tool
# statt des execute_rhino_code-Umwegs. Bewusste, freigegebene Aenderung
# (Nutzer-Entscheid 02.07.), RtD-Spur: Pilot -> Luecke -> Tool.
_GOLDEN = {
    "system_prompt": "c34039f4ff28b256ee7b36b8c3ff73ccc926b818338c68542cef6382685b4c43",
    "tools_basis": "58e30eeeaf74d4bcdf5a8dc0362bd010cb1ad6b90c8a9c19aea72306afc22220",
    "tools_werkzeug": "356bbe4f9c341d857b27ed18172c877f0a58eeed09ce2f1097c69d8f8936ca2e",
}


def _current() -> dict:
    """Berechnet die drei Hashes ueber die kanonischen export-Funktionen,
    damit der Test exakt das prueft, was auch ins manifest.json geschrieben
    wird."""
    from backend.export import _system_prompt_hash, _tools_hash

    return {
        "system_prompt": _system_prompt_hash(),
        "tools_basis": _tools_hash("basis"),
        "tools_werkzeug": _tools_hash("werkzeug"),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Golden-Hash-Regressionstest")
    ap.add_argument(
        "--print",
        dest="just_print",
        action="store_true",
        help="nur die aktuellen Hashes ausgeben (zum Neu-Verankern)",
    )
    args = ap.parse_args()

    try:
        cur = _current()
    except Exception as e:  # Import-/Bootstrap-Fehler sichtbar machen
        print("FEHLER beim Berechnen der Hashes:", repr(e), file=sys.stderr)
        return 2

    width = max(len(k) for k in cur)
    print("Aktuelle Hashes:")
    for k, v in cur.items():
        print("  %-*s %s" % (width, k, v))

    if args.just_print:
        return 0

    if not all(_GOLDEN.values()):
        print("\nGolden-Werte noch nicht verankert. Obige Werte in _GOLDEN eintragen.")
        return 2

    drift = [k for k in cur if cur[k] != _GOLDEN[k]]
    print("\n" + "=" * 60)
    if drift:
        print("DRIFT in:", ", ".join(drift))
        for k in drift:
            print("  %s:" % k)
            print("    golden : %s" % _GOLDEN[k])
            print("    aktuell: %s" % cur[k])
        print("=> HASHES WEICHEN AB (Prompt/Tool-Surface veraendert)")
        return 1
    print("=> ALLE HASHES IDENTISCH ZU GOLDEN")
    return 0


if __name__ == "__main__":
    sys.exit(main())
