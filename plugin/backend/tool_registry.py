"""Per-condition tool list builder (Studienartefakt-Spec §1.1, §1.2).

Single source of truth for which tools the Anthropic / local-LLM call
receives, gated on the session's study condition. The agent loop calls
``build_tool_list(condition)`` on every turn so the active condition is
the only thing that decides which tool slots the model sees.

Why centralise: §1.2 explicitly forbids scattered ``if condition ==
...`` checks across the codebase. This module is the only place the
condition is allowed to influence the tool surface.

Designkorrektur 11.06.2026 (Spec §1.1): the two conditions no longer
differ in CAD competence. Tools are classified by *role*, not by
condition:

- ``core_cad``: the assistant's internal ability to perceive, create,
  change, inspect and execute geometry. Available in BOTH conditions so
  the basis condition is a fully CAD-capable prompt-based assistant, not
  a three-tool chatbot.
- ``interaction``: tools that open, evaluate or make visible an explicit
  participant-facing affordance (dialog cards, parameter sliders,
  variants, visible viewport marking). Only in ``werkzeug``, because the
  basis condition hides those UI slots entirely. (Das Lock-Feature war
  bis 11.06.2026 ein Interaction-Tool und wurde dann aus der
  Modell-Tool-Surface entfernt; die Persistenz bleibt dormant.)

The decision rule for a tool: *who acts?* If the tool only serves the
assistant (perceive / create / change / check), it is ``core_cad``. If
it spawns a visible operating affordance or asks the participant to act,
it is ``interaction``. Adding a new tool means appending it to
``dedicated_tools.TOOL_SCHEMAS`` (defaults to ``core_cad``); a new
interaction tool additionally goes into ``INTERACTION_TOOL_NAMES``
below — that name set is the only allow-list the split reads.
"""

from __future__ import annotations

from typing import Any

from . import dedicated_tools

# Built-in tool schemas the agent has always shipped (viewport capture +
# Python fallback). Lives here, not in agent.py, so the registry owns the
# full universe of tools the plugin can hand to the model.
BUILTIN_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "capture_viewport",
        "description": (
            "Erzeugt einen JPEG-Screenshot des Rhino-Modells und liefert "
            "ihn als Image-Block zurueck. Default: 2x2-Komposit aus "
            "Perspektive + Top + Front + Right, jeweils mit Label im Bild "
            "und automatischem ZoomExtents — damit hast du fuer geometrische "
            "Entscheidungen sofort alle Hauptansichten ohne mehrere Calls. "
            "Designer-Kamera wird nach der Aufnahme wiederhergestellt. "
            "Wenn du nur eine schnelle Bestaetigung brauchst (z.B. 'hat der "
            "Move funktioniert?'), uebergib ``views=[\"current\"]`` und "
            "ggf. ``fit=\"none\"``, um die Designer-Ansicht 1:1 zu sehen."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "views": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": [
                            "current",
                            "top",
                            "bottom",
                            "front",
                            "back",
                            "left",
                            "right",
                            "perspective",
                        ],
                    },
                    "description": (
                        "Liste der zu erfassenden Ansichten. Default: "
                        "[\"current\",\"top\",\"front\",\"right\"] als "
                        "2x2-Komposit. Bei einer einzelnen Ansicht "
                        "(z.B. [\"current\"]) wird ohne Komposition "
                        "gespeichert."
                    ),
                    "default": ["current", "top", "front", "right"],
                },
                "fit": {
                    "type": "string",
                    "enum": ["none", "extents", "selection"],
                    "description": (
                        "Wie der Rahmen vor der Aufnahme gesetzt wird. "
                        "'extents' = auf alle Objekte zoomen (Default, "
                        "damit das Objekt sicher im Bild ist); 'none' = "
                        "Designer-Zoom uebernehmen (nur sinnvoll fuer "
                        "views=['current']); 'selection' = nur Selektion "
                        "rahmen (fallback auf extents ohne Selektion)."
                    ),
                    "default": "extents",
                },
                "max_size": {
                    "type": "integer",
                    "description": (
                        "Maximale Kantenlaenge in Pixeln. Bei Komposit "
                        "die Gesamtkante (Zellen sind kleiner)."
                    ),
                    "default": 1024,
                },
            },
            "required": [],
        },
    },
    {
        "name": "execute_rhino_code",
        "description": (
            "Letzter Ausweg, wenn KEIN dediziertes Tool fuer Inspektion, "
            "Geometrie, Transformation, Kurven, Flaechen, Booleans, "
            "Backups, SubD oder Grasshopper zum Ziel fuehrt. Bevorzuge "
            "IMMER dedizierte Tools - sie sind getestet, haben klare "
            "Schemas und du musst die Rhino-API nicht raten. Nur wenn "
            "eine Operation wirklich nicht abgedeckt ist, schreibe "
            "Python-Code hier. Vorimportiert: ``rs``, ``rg``, ``sc``, "
            "``math``, ``System``. Setze ``result = <wert>`` am Ende."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Python-3-Quellcode (mehrzeilig erlaubt).",
                },
            },
            "required": ["code"],
        },
    },
]


# Interaction tools (Studienartefakt-Spec §1.1, Designkorrektur
# 11.06.2026). These open, structure or make visible an explicit
# participant-facing affordance and are therefore the experimental
# variable. They are present in ``werkzeug`` and stripped from ``basis``;
# everything else in the registry is ``core_cad`` (both conditions).
#
# Categories (matches the handover doc's "Konkrete Kategorisierung"):
#   - Dialogs / quick replies: request_confirmation, request_parameter,
#     request_choice, request_reference_pick
#   - Parameter sliders:       expose_parameters, clear_parameters
#   - Variants:                create/select/delete/clear/finish_variants
#
# Lock-Feature am 11.06.2026 aus der Modell-Tool-Surface entfernt; die
# drei Lock-Namen sind daher NICHT mehr Teil dieser Menge. Die
# Persistenz (DB/REST/dispatch) bleibt dormant, siehe LOCK_TOOL_SCHEMAS.
#
# Borderline case (decided 11.06.2026, not silently):
#   - select_objects -> interaction: the AI-side visible viewport
#     marking IS the marking affordance under study. Leaving it in basis
#     would give the basis condition half a marking tool.
#
# Explicitly NOT interaction (stay core_cad, both conditions):
#   - resolve_reference: pure internal text->object resolution, no UI.
#   - get_selected, get_gh_selected -> core_cad (reclassified 30.06.2026,
#     tools_basis hash re-anchored): read-only AI inspection tools the
#     designer never invokes or sees — no participant-facing affordance
#     and no eval trace of their own (what they read already surfaces via
#     the selection badge / pick blocks / select_objects). Keeping them
#     werkzeug-only would even hand werkzeug a capability basis lacks
#     (reading a selection the designer set directly in Rhino), which
#     contradicts the 11.06. shared-core-CAD correction. get_gh_context
#     (functionally identical GH read) was already core_cad.
#   - undo_last_action, redo_last_action, restore_object, list_backups:
#     the reversibility safety net must protect participants in BOTH
#     conditions (in basis it is reachable by asking in chat). Only the
#     *visible* version-timeline UI stays werkzeug-exclusive, and that
#     is frontend gating, not a backend tool.
INTERACTION_TOOL_NAMES: frozenset[str] = frozenset(
    {
        # Dialogs / quick-reply cards (DIALOG_TOOL_SCHEMAS)
        "request_confirmation",
        "request_parameter",
        "request_parameters",
        "request_choice",
        "request_reference_pick",
        # Parameter sliders
        "expose_parameters",
        "clear_parameters",
        # Variants
        "create_variant",
        "select_variant",
        "delete_variant",
        "clear_variants",
        "finish_variants",
        # Locks am 11.06.2026 aus der Modell-Tool-Surface entfernt
        # (lock_object/unlock_object/list_locked_objects); LOCK_TOOL_SCHEMAS
        # bleibt dormant, ist aber nicht mehr Teil der Interaction-Tools.
        # Borderline -> interaction: select_objects ist die KI-zeigt-Deixis-
        # Affordanz (sichtbare Viewport-Markierung). get_selected/
        # get_gh_selected wurden am 30.06.2026 nach core_cad verschoben
        # (read-only KI-Inspektion ohne Designer-Affordanz/FF-Spur) und
        # stehen bewusst NICHT mehr hier.
        "select_objects",
    }
)


# Lock-Panel tool schemas (Studienartefakt-Spec §1.1, P5). These don't
# touch Rhino — they mutate plugin state (the locked_objects table) so
# the system-prompt addendum can warn the model off specific GUIDs.
#
# DORMANT seit 11.06.2026: Das Lock-Feature wurde aus der Modell-Tool-
# Surface entfernt (build_tool_list nimmt LOCK_TOOL_SCHEMAS nicht mehr in
# die Liste auf, INTERACTION_TOOL_NAMES enthaelt die Lock-Namen nicht
# mehr). Die Definitionen bleiben absichtlich erhalten, weil die
# Persistenz toter Code bleibt: dispatch.py importiert LOCK_TOOL_NAMES,
# und DB/REST-Endpoints/session_store/schemas/export referenzieren das
# Lock-Modell weiterhin unveraendert. Das Modell bekommt diese Tools
# nicht mehr angeboten.
LOCK_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "lock_object",
        "description": (
            "Sperrt ein oder mehrere Rhino-Objekte vor weiteren "
            "Modifikationen. Gesperrte Objekte erscheinen in jedem "
            "weiteren Turn im System-Prompt mit dem Hinweis, sie nicht "
            "anzufassen. NUTZE DIES NUR, wenn der Designer das explizit "
            "verlangt ('lock', 'sperr X', 'X soll bleiben'). Du darfst "
            "es NICHT aus eigenem Antrieb aufrufen."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "object_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "description": "GUIDs der zu sperrenden Rhino-Objekte.",
                },
                "note": {
                    "type": "string",
                    "default": "",
                    "description": (
                        "Optionaler Kommentar, was an dem Objekt nicht "
                        "verändert werden darf."
                    ),
                },
            },
            "required": ["object_ids"],
        },
    },
    {
        "name": "unlock_object",
        "description": (
            "Hebt die Sperre für ein oder mehrere zuvor gesperrte "
            "Rhino-Objekte wieder auf. NUR auf Wunsch des Designers "
            "aufrufen."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "object_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                },
            },
            "required": ["object_ids"],
        },
    },
    {
        "name": "list_locked_objects",
        "description": (
            "Listet alle aktuell vom Designer gesperrten Objekte dieser "
            "Sitzung. Nutze dies, wenn du dir nicht sicher bist, ob ein "
            "Objekt gesperrt ist, bevor du es bearbeitest."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
]

# Names of the lock-related tools; the agent dispatcher routes these
# off the normal Rhino path because they only touch the plugin DB.
LOCK_TOOL_NAMES: frozenset[str] = frozenset(
    {t["name"] for t in LOCK_TOOL_SCHEMAS}
)


DIALOG_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "request_confirmation",
        "description": (
            "Stellt eine kurze Ja/Weiter-Rueckfrage an den Designer und "
            "pausiert den Agentenlauf. Nutze dies, wenn du ohne explizite "
            "Bestaetigung nicht fortfahren solltest, z.B. bevor du einen "
            "angebotenen Slider oder eine mehrdeutige Aenderung wirklich "
            "ausfuehrst. Nach diesem Toolcall keine weitere Aktion im "
            "gleichen Turn."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Kurze Frage, die in der Chat-Karte erscheint.",
                },
                "details": {
                    "type": "string",
                    "default": "",
                    "description": "Optionale knappe Zusatzinfo.",
                },
                "confirm_label": {"type": "string", "default": "Weiter"},
                "confirm_response": {
                    "type": "string",
                    "default": "Ja, weiter.",
                    "description": "Text, der beim Klick als User-Antwort gesendet wird.",
                },
                "cancel_label": {"type": "string", "default": "Abbrechen"},
                "cancel_response": {
                    "type": "string",
                    "default": "Nein, abbrechen.",
                },
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "request_parameter",
        "description": (
            "Fragt einen fehlenden numerischen oder kurzen Parameterwert "
            "als Inline-Karte ab. Nutze dies fuer Rueckfragen wie Radius, "
            "Laenge, Abstand, Anzahl oder Winkel, wenn ein sinnvoller "
            "Default vorgeschlagen werden kann. Gib immer kompakte "
            "Schnellwerte an. Nach diesem Toolcall keine weitere Aktion "
            "im gleichen Turn."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string"},
                "parameter_name": {
                    "type": "string",
                    "description": "Name des abgefragten Werts, z.B. Radius.",
                },
                "unit": {"type": "string", "default": "mm"},
                "default_value": {
                    "description": "Vorgeschlagener Wert.",
                    "anyOf": [{"type": "number"}, {"type": "string"}],
                },
                "suggestions": {
                    "type": "array",
                    "items": {
                        "anyOf": [{"type": "number"}, {"type": "string"}],
                    },
                    "description": "3-5 Schnellwerte, z.B. [1,2,5,10].",
                    "default": [],
                },
                "min": {"type": "number"},
                "max": {"type": "number"},
                "step": {"type": "number", "default": 1},
                "response_template": {
                    "type": "string",
                    "default": "",
                    "description": (
                        "Optionaler Antworttext mit {value} und {unit}, "
                        "z.B. 'Radius: {value}{unit}'."
                    ),
                },
            },
            "required": ["prompt", "parameter_name", "default_value"],
        },
    },
    {
        "name": "request_reference_pick",
        "description": (
            "Fordert den Designer auf, eine Referenz im Rhino-Viewport "
            "anzuklicken, wenn Sprache allein nicht eindeutig genug ist. "
            "Nutze dies, statt zu raten, welche Kante/Flaeche/Objekt/Punkt "
            "gemeint war, oder bevor du eine destruktive Aktion auf einer "
            "mehrdeutigen Referenz ausfuehrst. Die Antwort kommt im "
            "naechsten Turn als ``component_pick`` (face/edge), "
            "``point_pick`` oder ``selection`` (object) im Chatverlauf "
            "zurueck — du kannst sie dort direkt referenzieren. Nach "
            "diesem Toolcall keine weitere Aktion im gleichen Turn."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": (
                        "Kurze Frage, die auf der Card erscheint, z.B. "
                        "'Welche Kante meinst du?' oder 'Klick die "
                        "Bodenflaeche.'"
                    ),
                },
                "target_type": {
                    "type": "string",
                    "enum": ["object", "face", "edge", "point"],
                    "description": (
                        "Was soll der Designer picken: ein ganzes "
                        "``object``, eine Brep-``face``, eine Brep-"
                        "``edge``, oder einen einzelnen ``point`` mit "
                        "Snap. Bestimmt, welcher Rhino-Pick-Modus "
                        "ausgeloest wird."
                    ),
                },
                "hint": {
                    "type": "string",
                    "default": "",
                    "description": (
                        "Optionaler Zusatz-Hint, z.B. 'die Kante an der "
                        "Vorderseite' oder 'der obere Eckpunkt'."
                    ),
                },
            },
            "required": ["prompt", "target_type"],
        },
    },
    {
        "name": "request_choice",
        "description": (
            "Bietet dem Designer 2-4 klare Optionen als Inline-Karte an. "
            "Nutze dies bei echten Alternativen wie abrunden vs. fasen "
            "oder links vs. rechts. Nach diesem Toolcall keine weitere "
            "Aktion im gleichen Turn."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string"},
                "options": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 4,
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string"},
                            "response_text": {
                                "type": "string",
                                "description": (
                                    "Text, der beim Klick als User-Antwort "
                                    "gesendet wird. Fallback ist label."
                                ),
                            },
                            "description": {"type": "string"},
                            "reference": {
                                "type": "object",
                                "description": (
                                    "Optional. Wenn diese Option eine konkrete "
                                    "Geometrie meint (z.B. 'linke Seite' = ein "
                                    "bestimmtes Objekt oder eine Flaeche), gib "
                                    "hier deren Referenz an. Die UI hebt sie "
                                    "dann beim Hovern ueber die Option non-"
                                    "mutating im Viewport hervor (keine "
                                    "Auswahl, kein Eingriff). Nur ausfuellen, "
                                    "wenn du die object_id sicher kennst (aus "
                                    "einem frueheren Tool-Ergebnis oder Pick)."
                                ),
                                "properties": {
                                    "object_id": {
                                        "type": "string",
                                        "description": (
                                            "GUID des Rhino-Objekts, das die "
                                            "Option meint."
                                        ),
                                    },
                                    "component_type": {
                                        "type": "string",
                                        "enum": [
                                            "object",
                                            "face",
                                            "edge",
                                            "vertex",
                                        ],
                                        "description": (
                                            "Optional. 'object' (Default) hebt "
                                            "das ganze Objekt hervor; 'face'/"
                                            "'edge'/'vertex' eine Sub-Komponente "
                                            "(dann component_index angeben)."
                                        ),
                                    },
                                    "component_index": {
                                        "type": "integer",
                                        "description": (
                                            "Optional. Index der Sub-Komponente "
                                            "(Brep/SubD/Mesh) fuer "
                                            "component_type face/edge/vertex."
                                        ),
                                    },
                                },
                                "required": ["object_id"],
                            },
                        },
                        "required": ["label"],
                    },
                },
            },
            "required": ["prompt", "options"],
        },
    },
    {
        "name": "request_parameters",
        "description": (
            "Fragt MEHRERE zusammengehoerige numerische/kurze Werte in "
            "EINER gebuendelten Inline-Karte ab — z.B. Tiefe UND Breite "
            "einer Nut. Nutze dies STATT ``request_parameter`` mehrfach "
            "hintereinander, wenn fuer EINE Operation mehrere Masse "
            "fehlen: der Designer fuellt alle Felder und bestaetigt EINMAL. "
            "Fuer ein EINZELnes Mass stattdessen ``request_parameter``. "
            "Gib pro Feld einen Default und 3-5 Schnellwerte an. Nach "
            "diesem Toolcall keine weitere Aktion im gleichen Turn."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Optionaler Titel der Karte, z.B. 'Nut-Masse'.",
                },
                "parameters": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 6,
                    "description": "Die abgefragten Werte, je ein Feld.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "parameter_name": {
                                "type": "string",
                                "description": "Name des Werts, z.B. Tiefe.",
                            },
                            "label": {
                                "type": "string",
                                "description": (
                                    "Optionale Feldfrage; Default ist "
                                    "parameter_name."
                                ),
                            },
                            "unit": {"type": "string", "default": "mm"},
                            "default_value": {
                                "description": "Vorgeschlagener Wert.",
                                "anyOf": [{"type": "number"}, {"type": "string"}],
                            },
                            "suggestions": {
                                "type": "array",
                                "items": {
                                    "anyOf": [
                                        {"type": "number"},
                                        {"type": "string"},
                                    ],
                                },
                                "description": "3-5 Schnellwerte, z.B. [1,2,5,10].",
                                "default": [],
                            },
                            "min": {"type": "number"},
                            "max": {"type": "number"},
                            "step": {"type": "number", "default": 1},
                            "response_template": {
                                "type": "string",
                                "default": "",
                                "description": (
                                    "Optionaler Antworttext mit {value} und "
                                    "{unit} fuer dieses Feld."
                                ),
                            },
                        },
                        "required": ["parameter_name", "default_value"],
                    },
                },
            },
            "required": ["parameters"],
        },
    },
]

DIALOG_TOOL_NAMES: frozenset[str] = frozenset(
    {t["name"] for t in DIALOG_TOOL_SCHEMAS}
)


def build_tool_list(condition: str) -> list[dict[str, Any]]:
    """Return the Anthropic-shaped ``tools`` list for the given condition.

    - ``"werkzeug"``: the full registry — built-in tools + every
      dedicated Rhino/Grasshopper tool + dialog tools. This is the
      shared core-CAD stack plus all interaction tools. (Lock-Tools sind
      seit 11.06.2026 nicht mehr Teil der Modell-Tool-Surface;
      LOCK_TOOL_SCHEMAS bleibt dormant.)
    - ``"basis"`` (and, fail-closed, ANY other/unknown value): the same
      full registry MINUS ``INTERACTION_TOOL_NAMES`` — i.e. the shared
      core-CAD stack only. The model keeps full modelling competence but
      cannot reach a hidden UI affordance through a tool call. Only the
      exact string ``"werkzeug"`` unlocks the interaction surface, so a
      lost/garbled condition fails SAFE to basis (Pilot 01.07.2026: a
      basis run received dialog tools; the gate now refuses to fail open
      regardless of how the condition got mangled).

    Filter approach on purpose: ``werkzeug`` returns the full list with
    identical content AND order to the historical registry, so the
    Anthropic prompt/tool-definition cache for the werkzeug condition is
    never invalidated by this change. ``basis`` is derived by dropping
    interaction tools from that same ordered list, so it stays a stable
    subsequence.

    The function is pure: identical input ⇒ identical output. The agent
    therefore safely recomputes it per turn without invalidating
    Anthropic's tool-definition cache.
    """
    full: list[dict[str, Any]] = (
        BUILTIN_TOOL_SCHEMAS
        + dedicated_tools.TOOL_SCHEMAS
        # LOCK_TOOL_SCHEMAS bewusst NICHT mehr enthalten (Lock-Feature am
        # 11.06.2026 aus der Modell-Tool-Surface entfernt; Persistenz
        # bleibt dormant). Das Modell bekommt die Lock-Tools nie angeboten.
        + DIALOG_TOOL_SCHEMAS
    )
    # Fail CLOSED: only the exact string "werkzeug" unlocks the full
    # interaction surface. Every other value ("basis" / None-as-str / "" /
    # anything unexpected) gets the interaction-free basis list, so a lost
    # or garbled condition can never hand a participant the interaction
    # tools (Pilot 01.07.2026 leak). Golden-hash unchanged: build_tool_list(
    # "basis") and ("werkzeug") each return byte-identical lists to before.
    if condition == "werkzeug":
        return full
    return [t for t in full if t.get("name") not in INTERACTION_TOOL_NAMES]


# Tools that never modify the Rhino document (Studienartefakt-Spec §2.4
# model_states-Trigger, "Nie Snapshot"). Anything else triggers a
# model_state snapshot when study mode is active. ``execute_rhino_code``
# is explicitly on the modifying side — even when the code itself is
# read-only, treating every call as a potential mutation is the safest
# rule (spec wording: "billigste sichere Regel").
_READ_ONLY_TOOLS: frozenset[str] = frozenset(
    {
        "capture_viewport",
        "get_scene_info",
        "get_layer_info",
        "get_object_info",
        "get_brep_component_info",
        "resolve_reference",
        "get_layers",
        "get_scene_objects_with_metadata",
        "get_selected",
        "select_objects",
        "list_backups",
        "get_subd_info",
        "get_gh_context",
        "get_objects",
        "get_gh_selected",
        "expire_and_get_info",
        # Lock-Panel tools mutate plugin state, not Rhino — no
        # model_state snapshot needed. Seit 11.06.2026 nicht mehr in der
        # Modell-Tool-Surface (dormant); Eintraege bleiben harmlos, da das
        # Modell die Tools nie aufrufen kann.
        "lock_object",
        "unlock_object",
        "list_locked_objects",
        # Dialog tools only create participant-facing quick reply cards.
        "request_confirmation",
        "request_parameter",
        "request_parameters",
        "request_choice",
        "request_reference_pick",
        # UI-/Sichtbarkeits-Tools: aendern KEINE Geometrie (Slider-Panel
        # ein-/ausblenden bzw. Varianten-Layer-Sichtbarkeit toggeln) und
        # duerfen daher keinen model_states-Snapshot ausloesen. Die
        # geometrieveraendernde Slider-BEWEGUNG snapshottet separat ueber den
        # WS-Worker (trigger="parameter"). create_variant/delete_variant/
        # clear_variants bleiben modifying — sie kopieren/loeschen Geometrie.
        "expose_parameters",
        "clear_parameters",
        "select_variant",
        "finish_variants",
    }
)


def is_modifying_tool(name: str) -> bool:
    """True when the tool may alter the active Rhino document.

    Drives the ``model_states`` auto-snapshot trigger (Spec §2.4).
    Unknown names default to *modifying* — fail safe so a forgotten
    new tool doesn't silently skip its snapshot.
    """
    return name not in _READ_ONLY_TOOLS
