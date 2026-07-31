"""agent.gate — selektives Vorschau-Gate (Part C, MVP, Design A).

Design A = ECHTE In-Turn-Pause: ruft das Modell in der ``werkzeug``-Bedingung
ein destruktives Tool auf, hält der Tool-Dispatch in ``dispatch._execute_tool``
an, zeigt dem Designer eine Vorschaukarte und **blockiert auf einem
``asyncio.Future``**, bis "Übernehmen" (accept) oder "Verwerfen" (revert)
kommt — BEVOR die Mutation passiert. Es wird KEIN ``awaiting_user``-Dialog-
Sentinel zurückgegeben (der würde den Turn beenden); stattdessen liefert das
Tool nach dem Await ein ganz normales Tool-Result, der Loop läuft also weiter
("der Tool-Call dauert nur länger").

Diese Datei hält nur die Registry + die Klassifikation/Beschreibung. Das
eigentliche Await sitzt in ``dispatch.py``; aufgelöst wird von außen über
``server.py`` (``gate.resolve``-Command) bzw. ``chat.cancel``.

Robustheit:
- Die ``Future``-Objekte werden auf dem laufenden Event-Loop erzeugt
  (``register`` wird aus ``_execute_tool`` heraus gerufen, also im Loop). Das
  ``await`` blockiert NUR diesen einen Tool-Call, nicht den Event-Loop —
  andere WS-Messages (inkl. ``gate.resolve``) werden weiter verarbeitet.
- ``resolve`` ist idempotent: ein zweiter Resolve auf dieselbe ``tu_id`` (oder
  auf eine bereits erledigte/abgebrochene Future) ist ein harmloser No-Op.
- ``cancel_session`` löst alle offenen Gates einer Session als "revert" auf
  (Cancel-Pfad). Auch idempotent.
- Aufräumen: ``register`` setzt einen Done-Callback, der den Registry-Eintrag
  entfernt, sobald die Future fertig ist (resolve/cancel/Timeout durch den
  Wartenden). Kein Leak.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from ._describe_format import _count, _fmt_num, _int_list

logger = logging.getLogger("FurniturePlugin.Gate")

# Auto-Skip-Timeout: nie ewiges Hängen. Läuft die Vorschaukarte ohne
# Designer-Entscheidung ab, behandelt ``dispatch`` das wie "revert".
GATE_TIMEOUT = 600.0  # Sekunden

# Boolean-Operationen (irreversibel verschmelzend/abziehend).
_GATED_BOOLEAN_TOOLS = frozenset(
    {
        "boolean_union",
        "boolean_difference",
        "boolean_intersection",
        "boolean_split",
    }
)

# SubD-Edit-Tools, die bestehende Topologie destruktiv verändern
# (Objects.Replace / rs.Command umgehen den Rhino-Undo-Stack).
# Verifiziert gegen dispatch_tables._DEDICATED_DISPATCH / code_templates_subd.
_GATED_SUBD_EDIT_TOOLS = frozenset(
    {
        "subd_crease_edges",
        "subd_set_vertex_position",
        "subd_extrude_faces",
        "subd_offset_faces",
        "subd_subdivide",
    }
)

# Objekt-löschend.
_GATED_DELETE_TOOLS = frozenset({"delete_object"})

# Gesamtmenge der destruktiven Tools, die in der werkzeug-Bedingung ein
# Vorschau-Gate auslösen. Bewusst NUR die wirklich folgenreichen Ops:
# Objekt-Löschen und Booleans (die Input-Objekte verschlucken/verschmelzen).
# Reversible, archiv-gesicherte Brep-Topologie-Edits (fillet_brep_edge/
# chamfer_brep_edge/round_edges_by_rule/create_hole/create_slot =
# _STRUCTURE_BREAKING_TOOLS) gaten seit 01.07.2026 NICHT mehr: der Pre-Confirm
# war dort mehr Reibung als Schutz (Archive-Backup + Revert decken das
# Rückgängigmachen ab). Bewusste Studien-Entscheidung (Nutzerwunsch) — Gate
# bleibt werkzeug-only. SubD-Edits bleiben vorerst gegated (eigene Kategorie).
_GATED_DESTRUCTIVE_TOOLS: frozenset[str] = frozenset(
    _GATED_BOOLEAN_TOOLS
    | _GATED_SUBD_EDIT_TOOLS
    | _GATED_DELETE_TOOLS
)


def should_gate(name: str, condition: Optional[str]) -> bool:
    """True nur, wenn ``name`` destruktiv ist UND die Session in der
    werkzeug-Bedingung läuft. In basis (oder bei unbekannter Condition)
    nie — das Gate ist eine Interaction-Affordanz und damit werkzeug-only.
    """
    return condition == "werkzeug" and name in _GATED_DESTRUCTIVE_TOOLS


# ---------------------------------------------------------------------------
# Beschreibung der Operation (Vorschaukarte)
# ---------------------------------------------------------------------------


def _as_id_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        out: list[str] = []
        for entry in value:
            if isinstance(entry, str) and entry.strip():
                out.append(entry.strip())
        return out
    return []


def describe(tool_name: str, tool_input: dict) -> tuple[str, list[str]]:
    """Kurze deutsche Operationsbeschreibung + betroffene Objekt-GUIDs.

    Wird für die ``gate.preview``-Karte (Text + Highlight) genutzt. Rein
    aus dem Tool-Input abgeleitet, defensiv: fehlende/unerwartete Felder
    führen nie zu einem Fehler, nur zu einer generischeren Beschreibung.
    """
    ti = tool_input if isinstance(tool_input, dict) else {}

    if tool_name == "delete_object":
        ids = _as_id_list(ti.get("object_ids"))
        return (f"Loeschen: {_count(len(ids), 'Objekt', 'Objekte')} entfernen", ids)

    if tool_name == "boolean_difference":
        keep = _as_id_list(ti.get("keep_id"))
        remove = _as_id_list(ti.get("remove_ids"))
        return (
            "Boolean-Differenz: "
            f"{_count(len(remove), 'Objekt', 'Objekte')} vom Zielobjekt abziehen",
            keep + remove,
        )

    if tool_name in ("boolean_union", "boolean_intersection"):
        ids = _as_id_list(ti.get("object_ids"))
        verb = "vereinigen" if tool_name == "boolean_union" else "verschneiden"
        return (f"Boolean: {_count(len(ids), 'Objekt', 'Objekte')} {verb}", ids)

    if tool_name == "boolean_split":
        obj = _as_id_list(ti.get("object_id"))
        cutter = _as_id_list(ti.get("cutter_id"))
        return ("Boolean-Split: Zielobjekt mit Schneider teilen", obj + cutter)

    if tool_name == "create_hole":
        obj = _as_id_list(ti.get("object_id"))
        parts: list[str] = []
        dia = _fmt_num(ti.get("diameter"))
        if dia:
            parts.append(f"Ø{dia} mm")
        if ti.get("through"):
            parts.append("Durchgang")
        else:
            depth = _fmt_num(ti.get("depth"))
            if depth:
                parts.append(f"Tiefe {depth} mm")
        return ("Bohrung" + (": " + ", ".join(parts) if parts else ""), obj)

    if tool_name == "create_slot":
        obj = _as_id_list(ti.get("object_id"))
        parts = []
        length = _fmt_num(ti.get("length"))
        width = _fmt_num(ti.get("width"))
        if length and width:
            parts.append(f"{length}×{width} mm")
        elif length:
            parts.append(f"Laenge {length} mm")
        if ti.get("through"):
            parts.append("Durchgang")
        else:
            depth = _fmt_num(ti.get("depth"))
            if depth:
                parts.append(f"Tiefe {depth} mm")
        return ("Langloch" + (": " + ", ".join(parts) if parts else ""), obj)

    if tool_name == "fillet_brep_edge":
        obj = _as_id_list(ti.get("object_id"))
        n = len(_int_list(ti.get("edge_indices")) or _int_list(ti.get("edge_index")))
        radius = _fmt_num(ti.get("radius"))
        parts = []
        if n:
            parts.append(_count(n, "Kante", "Kanten"))
        if radius:
            parts.append(f"Radius {radius} mm")
        return ("Verrunden (Fillet)" + (": " + ", ".join(parts) if parts else ""), obj)

    if tool_name == "chamfer_brep_edge":
        obj = _as_id_list(ti.get("object_id"))
        n = len(_int_list(ti.get("edge_indices")) or _int_list(ti.get("edge_index")))
        dist = _fmt_num(ti.get("distance"))
        parts = []
        if n:
            parts.append(_count(n, "Kante", "Kanten"))
        if dist:
            parts.append(f"{dist} mm")
        return ("Abfasen (Chamfer)" + (": " + ", ".join(parts) if parts else ""), obj)

    if tool_name == "round_edges_by_rule":
        obj = _as_id_list(ti.get("object_id"))
        rule = ti.get("rule")
        radius = _fmt_num(ti.get("radius"))
        parts = []
        if isinstance(rule, str) and rule.strip():
            parts.append(f"Regel '{rule.strip()}'")
        if radius:
            parts.append(f"Radius {radius} mm")
        return ("Kanten verrunden" + (": " + ", ".join(parts) if parts else ""), obj)

    if tool_name in _GATED_SUBD_EDIT_TOOLS:
        obj = _as_id_list(ti.get("subd_id"))
        if tool_name == "subd_crease_edges":
            n = len(_int_list(ti.get("edge_indices")))
            if "crease" in ti:
                verb = "schaerfen" if ti.get("crease") else "glaetten"
            else:
                verb = "schaerfen/glaetten"
            tail = f": {_count(n, 'Kante', 'Kanten')}" if n else ""
            return (f"SubD-Kanten {verb}{tail}", obj)
        if tool_name == "subd_set_vertex_position":
            idx = _int_list(ti.get("vertex_index"))
            tail = f" (Index {idx[0]})" if idx else ""
            return (f"SubD-Vertex verschieben{tail}", obj)
        if tool_name in ("subd_extrude_faces", "subd_offset_faces"):
            n = len(_int_list(ti.get("face_indices")))
            dist = _fmt_num(ti.get("distance"))
            verb = "extrudieren" if tool_name == "subd_extrude_faces" else "versetzen"
            parts = []
            if n:
                parts.append(_count(n, "Flaeche", "Flaechen"))
            if dist:
                parts.append(f"{dist} mm")
            return (
                f"SubD-Flaechen {verb}" + (": " + ", ".join(parts) if parts else ""),
                obj,
            )
        if tool_name == "subd_subdivide":
            lv = _int_list(ti.get("levels"))
            tail = f": {_count(lv[0], 'Stufe', 'Stufen')}" if lv else ""
            return (f"SubD verfeinern{tail}", obj)
        return ("SubD-Aenderung", obj)

    # Fallback: generische Beschreibung, GUIDs best effort aus gaengigen
    # Feldern sammeln.
    collected: list[str] = []
    for key in ("object_id", "object_ids", "subd_id", "keep_id", "remove_ids"):
        collected.extend(_as_id_list(ti.get(key)))
    # Reihenfolge erhalten, Duplikate raus.
    seen: set[str] = set()
    unique = [x for x in collected if not (x in seen or seen.add(x))]
    return (f"Aenderung: {tool_name}", unique)


# ---------------------------------------------------------------------------
# Highlight-Ziele der Vorschaukarte (nur die betroffene Komponente)
# ---------------------------------------------------------------------------


def _first_id(value) -> Optional[str]:
    ids = _as_id_list(value)
    return ids[0] if ids else None


def _component_blocks(object_id: str, component_type: str, indices: list[int]) -> list[dict]:
    return [
        {
            "type": "component_pick",
            "object_id": object_id,
            "component_type": component_type,
            "component_index": idx,
        }
        for idx in indices
    ]


def _whole_object_blocks(value) -> list[dict]:
    ids = _as_id_list(value)
    return [{"type": "selection", "object_ids": ids}] if ids else []


def highlight_targets(tool_name: str, tool_input: dict) -> list[dict]:
    """Reference-Bloecke fuer das non-mutating Gate-Highlight.

    KERNZWECK: bei komponenten-gezielten Operationen leuchtet NUR die
    betroffene Kante/Flaeche/Vertex — nicht das ganze Objekt. Brep-Kanten-
    Tools (Fillet/Chamfer) und SubD-Edits geben ``component_pick``-Bloecke
    mit konkretem Index zurueck (gleicher Highlight-Pfad wie der Hover ueber
    einen Inline-Referenz-Chip). Bohrung/Langloch markieren einen Punkt am
    Zentrum. Ganz-Objekt-Operationen (Loeschen, Boolean, subdivide, regel-
    basiertes Verrunden) geben einen ``selection``-Block. Defensiv: fehlt der
    Komponenten-Index, faellt es auf das ganze Objekt zurueck. Rein aus dem
    (ggf. via Pick augmentierten) Tool-Input abgeleitet — kein Rhino-Zugriff.
    """
    ti = tool_input if isinstance(tool_input, dict) else {}

    # Brep-Kanten: nur die Zielkante(n)
    if tool_name in ("fillet_brep_edge", "chamfer_brep_edge"):
        oid = _first_id(ti.get("object_id"))
        idxs = _int_list(ti.get("edge_indices")) or _int_list(ti.get("edge_index"))
        if oid and idxs:
            return _component_blocks(oid, "edge", idxs)
        return _whole_object_blocks(ti.get("object_id"))

    # SubD-Kanten
    if tool_name == "subd_crease_edges":
        oid = _first_id(ti.get("subd_id"))
        idxs = _int_list(ti.get("edge_indices"))
        if oid and idxs:
            return _component_blocks(oid, "edge", idxs)
        return _whole_object_blocks(ti.get("subd_id"))

    # SubD-Vertex
    if tool_name == "subd_set_vertex_position":
        oid = _first_id(ti.get("subd_id"))
        idxs = _int_list(ti.get("vertex_index"))
        if oid and idxs:
            return _component_blocks(oid, "vertex", idxs)
        return _whole_object_blocks(ti.get("subd_id"))

    # SubD-Flaechen
    if tool_name in ("subd_extrude_faces", "subd_offset_faces"):
        oid = _first_id(ti.get("subd_id"))
        idxs = _int_list(ti.get("face_indices"))
        if oid and idxs:
            return _component_blocks(oid, "face", idxs)
        return _whole_object_blocks(ti.get("subd_id"))

    # Bohrung / Langloch: kein Komponenten-Index, aber eine Position ->
    # Punkt-Marker am Zentrum statt das ganze Objekt zu fluten.
    if tool_name in ("create_hole", "create_slot"):
        center = ti.get("center")
        if isinstance(center, (list, tuple)) and len(center) >= 3:
            try:
                pt = [float(center[0]), float(center[1]), float(center[2])]
                return [{"type": "point_pick", "point": pt}]
            except (TypeError, ValueError):
                pass
        return _whole_object_blocks(ti.get("object_id"))

    # round_edges_by_rule: regelbasiert (kein expliziter Kantenindex). Statt
    # das ganze Objekt zu fluten, gibt ein ``edge_rule``-Block die Regel weiter;
    # die UI-seitige Aufloesung (highlight._stage_edge_rule) bestimmt EXAKT
    # dieselben Kanten wie die Operation und hebt nur die hervor.
    if tool_name == "round_edges_by_rule":
        oid = _first_id(ti.get("object_id"))
        rule = ti.get("rule")
        if oid and isinstance(rule, str) and rule.strip():
            return [{"type": "edge_rule", "object_id": oid, "rule": rule}]
        return _whole_object_blocks(ti.get("object_id"))

    # Rest (delete_object, Boolean-Ops, subd_subdivide): das ganze Objekt /
    # die ganzen Objekte (dort ist das korrekt — alles ist betroffen).
    _summary, ids = describe(tool_name, tool_input)
    return [{"type": "selection", "object_ids": ids}] if ids else []


# ---------------------------------------------------------------------------
# Future-Registry
# ---------------------------------------------------------------------------


class _GateRegistry:
    """Singleton: ``tu_id`` -> offene ``asyncio.Future[str]``.

    Die Future wird in ``register`` auf dem aktuell laufenden Event-Loop
    erzeugt (Aufruf erfolgt aus ``_execute_tool``, also im Loop). ``resolve``
    und ``cancel_session`` werden ebenfalls aus dem Loop heraus gerufen
    (WS-Dispatch / done-callback), daher genügt ``future.set_result`` ohne
    ``call_soon_threadsafe``. Sollte ``resolve`` doch einmal aus einem
    Fremd-Thread kommen, fällt es defensiv auf den gespeicherten Loop zurück.
    """

    def __init__(self) -> None:
        # tu_id -> (future, session_id, loop)
        self._pending: dict[str, tuple[asyncio.Future, Optional[str], asyncio.AbstractEventLoop]] = {}
        # (session_id, correlation_id) -> "accept"|"revert": die EINE Gate-
        # Entscheidung pro Run. Eine User-Instruktion ist EIN Vorgang, der
        # intern mehrfach loeschen/aendern kann (z.B. "Durchmesser aendern" =
        # altes Objekt loeschen -> neu bauen -> Hilfsgeometrie loeschen). Der
        # erste Gate-Entscheid gilt daher fuer alle weiteren destruktiven
        # Schritte desselben Runs: accept -> Rest laeuft ohne erneute Karte;
        # revert -> Rest wird uebersprungen. Wird bei Run-Start geleert
        # (loop.run_agent). Schluessel ist (session_id, correlation_id), damit
        # zwei (theoretisch) ueberlappende Runs derselben Session sich nicht
        # gegenseitig die Entscheidung ueberschreiben — die UI serialisiert
        # Runs zwar (pendingSend), aber die Isolation ist hier billig + robust.
        self._run_decisions: dict[tuple[Optional[str], Optional[str]], str] = {}
        # (session_id, tu_id) -> bool: war die Gate-Entscheidung DIESES Schritts
        # aus der Run-Entscheidung GEERBT (True) oder eine eigene (interaktiv/
        # Timeout, False)? Reine Auswertungsspur fuer is_gate_autoresolved (F2):
        # bei N destruktiven Schritten trifft der Designer EINE Entscheidung,
        # die Folgeschritte erben sie — damit ist die eine echte von den
        # geerbten unterscheidbar. Normalerweise pop-on-read beim Persistieren
        # des tool_calls-Eintrags. ABER: wird der Run waehrend eines
        # cached-accept-Tools per chat.cancel abgebrochen, propagiert
        # CancelledError (BaseException, NICHT vom except Exception im Loop
        # gefangen) und das Persistieren entfaellt -> der Eintrag wuerde
        # verwaisen. Deshalb zusaetzlich session-gekeyt und am Run-Ende
        # (loop.run_agent finally, laeuft auch bei Cancel) per
        # clear_session_provenance gefegt. Folgenlos fuer die Daten (tu_id
        # global eindeutig -> keine Fehl-Markierung), nur Speicher-Hygiene.
        self._decision_provenance: dict[tuple[Optional[str], str], bool] = {}

    def register(self, tu_id: str, session_id: Optional[str] = None) -> asyncio.Future:
        """Lege eine offene Future für ``tu_id`` an und gib sie zurück.

        Ein bereits registriertes ``tu_id`` (sollte nicht vorkommen, Tool-
        Use-IDs sind eindeutig) wird defensiv zuerst als "revert" aufgelöst,
        damit kein verwaister Warter zurückbleibt.
        """
        loop = asyncio.get_running_loop()
        existing = self._pending.get(tu_id)
        if existing is not None:
            logger.warning("gate register: tu_id %s schon offen — alte Future revert", tu_id)
            self._safe_set(existing[0], "revert", existing[2])
            self._pending.pop(tu_id, None)
        fut: asyncio.Future = loop.create_future()
        self._pending[tu_id] = (fut, session_id, loop)

        def _cleanup(_f: asyncio.Future, _tu_id: str = tu_id) -> None:
            # Entferne den Eintrag, sobald die Future fertig ist (egal ob
            # resolve, cancel oder der Wartende per Timeout/Cancel aufgibt).
            entry = self._pending.get(_tu_id)
            if entry is not None and entry[0] is _f:
                self._pending.pop(_tu_id, None)

        fut.add_done_callback(_cleanup)
        return fut

    def resolve(self, tu_id: str, decision: str) -> bool:
        """Löse das Gate für ``tu_id`` mit "accept" oder "revert" auf.

        Idempotent: ein zweiter Aufruf (oder ein Aufruf auf ein unbekanntes /
        bereits erledigtes ``tu_id``) gibt ``False`` zurück und tut nichts.
        Eine unbekannte ``decision`` wird defensiv als "revert" behandelt.
        """
        if decision not in ("accept", "revert"):
            logger.warning("gate resolve: unbekannte decision %r -> revert", decision)
            decision = "revert"
        entry = self._pending.get(tu_id)
        if entry is None:
            return False
        fut, _session_id, loop = entry
        ok = self._safe_set(fut, decision, loop)
        # Eintrag wird vom done-callback entfernt.
        return ok

    def cancel_session(self, session_id: str) -> int:
        """Löse alle offenen Gates dieser Session als "revert" auf.

        Aufgerufen vom ``chat.cancel``-Pfad. Gibt die Anzahl aufgelöster
        Gates zurück. Idempotent (ein zweiter Aufruf findet nichts mehr).
        """
        count = 0
        # Snapshot, da _safe_set über den done-callback self._pending mutiert.
        for tu_id, (fut, sid, loop) in list(self._pending.items()):
            if sid == session_id:
                if self._safe_set(fut, "revert", loop):
                    count += 1
        if count:
            logger.info("gate cancel_session %s — %d offene(s) Gate(s) verworfen", session_id, count)
        return count

    def has_pending(self, session_id: str) -> bool:
        return any(sid == session_id for _f, sid, _loop in self._pending.values())

    # --- Run-weite Entscheidung (eine Karte pro Operation) ------------------

    def get_run_decision(
        self, session_id: Optional[str], correlation_id: Optional[str] = None
    ) -> Optional[str]:
        """Die fuer diesen Run schon getroffene Gate-Entscheidung, oder None."""
        if not session_id:
            return None
        return self._run_decisions.get((session_id, correlation_id))

    def set_run_decision(
        self, session_id: Optional[str], correlation_id: Optional[str], decision: str
    ) -> None:
        """Merke die Entscheidung fuer den Rest des Runs (nur accept/revert)."""
        if session_id and decision in ("accept", "revert"):
            self._run_decisions[(session_id, correlation_id)] = decision

    def clear_run_decision(
        self, session_id: Optional[str], correlation_id: Optional[str] = None
    ) -> None:
        """Setze die Run-Entscheidung zurueck (bei Run-Start/-Ende)."""
        if session_id:
            self._run_decisions.pop((session_id, correlation_id), None)

    # --- Provenance der Einzel-Entscheidung (is_gate_autoresolved) ----------

    def set_decision_provenance(
        self, session_id: Optional[str], tu_id: Optional[str], autoresolved: bool
    ) -> None:
        """Merke fuer DIESEN Schritt (tu_id), ob die Gate-Entscheidung geerbt
        (autoresolved=True) oder eine eigene (False) war."""
        if tu_id:
            self._decision_provenance[(session_id, tu_id)] = bool(autoresolved)

    def pop_decision_provenance(
        self, session_id: Optional[str], tu_id: Optional[str]
    ) -> Optional[bool]:
        """Lies + entferne die Provenance dieses Schritts. None = der Schritt
        war kein gegatetes Tool (keine Gate-Entscheidung)."""
        if not tu_id:
            return None
        return self._decision_provenance.pop((session_id, tu_id), None)

    def clear_session_provenance(self, session_id: Optional[str]) -> None:
        """Entferne alle Provenance-Eintraege dieser Session. Am Run-Ende
        (loop.run_agent finally, laeuft auch bei Cancel) aufgerufen, damit ein
        per chat.cancel abgebrochener cached-accept-Schritt — bei dem das
        Persistieren (und damit pop_decision_provenance) entfaellt — keinen
        verwaisten Eintrag hinterlaesst."""
        stale = [k for k in self._decision_provenance if k[0] == session_id]
        for k in stale:
            self._decision_provenance.pop(k, None)

    @staticmethod
    def _safe_set(fut: asyncio.Future, value: str, loop: asyncio.AbstractEventLoop) -> bool:
        """Setze das Future-Result idempotent + thread-defensiv.

        Normalfall (gleicher Loop): direktes ``set_result``. Fremd-Thread:
        über ``loop.call_soon_threadsafe``. Bereits erledigt/cancelled: No-Op.
        """
        if fut.done():
            return False
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            if not fut.done():
                fut.set_result(value)
                return True
            return False
        # Fremd-Thread / kein laufender Loop hier: ins Ziel-Loop marshallen.
        def _do() -> None:
            if not fut.done():
                fut.set_result(value)
        try:
            loop.call_soon_threadsafe(_do)
            return True
        except Exception as e:  # pragma: no cover — defensiv
            logger.warning("gate _safe_set marshalling fehlgeschlagen: %s", e)
            return False


# Singleton — eine Registry pro Plugin-Prozess.
gate_registry = _GateRegistry()


__all__ = [
    "GATE_TIMEOUT",
    "gate_registry",
    "should_gate",
    "describe",
    "highlight_targets",
    "_GATED_DESTRUCTIVE_TOOLS",
]
