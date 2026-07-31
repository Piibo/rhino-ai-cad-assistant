# AI Furniture Plugin — Features und Designentscheidungen

Stand: 2026-05-28; Designkorrektur Conditions: 2026-06-11; Lock-Feature entfernt: 2026-06-11; UI-Schicht 2026-06-26: K/F/O ohne Vertex (nur Kante/Flaeche/Objekt), Referenz-Chips vereinheitlicht ("#N auf Host"), "GH"-Button → "Parametrisieren" (Composer-Vorbefuellen statt Direkt-Feuern) — alles hash-neutral
Branch: `main`

Dieses Dokument beschreibt was das Plugin kann und warum es so gebaut ist wie es ist. Es ist die zentrale interne Referenz fuer das Studienartefakt der Masterarbeit *„Designing with Artificial Intelligence — Supporting Design Decision-Making in Furniture Design through Human-in-the-Loop Workflows"*.

Weitere Doku in diesem Verzeichnis:
- [ARCHITECTURE.md](ARCHITECTURE.md) — Code-Landkarte: Aufbau, Module und Datenfluss

---

## 1. Was das Plugin ist

Das Plugin ist ein chatbasierter KI-CAD-Assistent fuer Rhino 8, der direkt in Rhino als FastAPI-Backend laeuft und ueber einen Web-Browser bedient wird. Es ist nicht „ein weiterer CAD-Chatbot", sondern das **Studienartefakt** der Masterarbeit: in der Nutzerstudie wird damit qualitativ untersucht, welche Interaktionswerkzeuge ein chatbasierter Assistent braucht, damit Designer:innen vom reinen Prompten zu aktiver, parametrisch gestuetzter Modellinteraktion kommen.

Zwei Bedingungen sind im selben Plugin angelegt:

- **`basis`-Bedingung:** gleicher Core-CAD-Toolstack / gleiche Modellierkompetenz wie `werkzeug`, aber keine sichtbaren HITL-UI-Affordanzen ausser Chat + Bildanhang. Die Person vermittelt ihre Absicht sprachlich/bildlich.
- **`werkzeug`-Bedingung:** derselbe Core-CAD-Toolstack plus alle HITL-UI-Affordanzen (Picks, Slider, Dialoge, Varianten).

**Designkorrektur 11.06.2026:** `basis` ist nicht mehr als Drei-Tool-Chatbot zu verstehen. Die Studie soll nicht einen schwächeren Assistenten gegen einen stärkeren testen, sondern gleiche CAD-Kompetenz bei unterschiedlichem Interaktionskanal: Chat/Bild vs. Chat/Bild + explizite Interaktionswerkzeuge.

Beide Bedingungen sind sichtbar im Settings-Dialog auswaehlbar und werden in der Studie within-subjects verglichen.

---

## 2. Architektur

```
+-----------------------------+
|  Browser (React + Zustand)  |  Chat-UI, Slider, Pick-Buttons, Snapshot-Komposit
+-------------+---------------+
              |  WebSocket (ws://localhost:8765/ws)
              v
+-----------------------------+
|  FastAPI-Backend (in Rhino) |  Agent-Loop, Tool-Dispatch, DB-Logging, WS-Manager
|  (rhaino/plugin/backend) |
+------+----------------+-----+
       |                |
       |  TCP 9876      |  HTTP 9998
       v                v
+-------------+    +-----------+
|  Rhino-     |    | Grasshopper|
|  Listener   |    | Bridge     |
|  (Python)   |    | (GHPython) |
+-------------+    +-----------+
       ^                ^
       +----------------+
                Rhino 8 / Grasshopper
```

**Zwei Kommunikationskanaele:**
- **Rhino:** TCP-Socket auf `localhost:9876`, `rhino_script.py` laeuft als Listener in Rhino.
- **Grasshopper:** HTTP auf `localhost:9998`, `GHCodeMCP`-Component laeuft als GHPython.

**Tool-Pattern:** Dedizierte Tools generieren Python-Code-Strings und schicken sie via `execute_code` an Rhino. Davor wird ein `CODE_PREAMBLE` injiziert (definiert in `rhino_exec.py`), das `rs`, `rg`, `sc`, `math` plus die Sicherheitshelfer `archive_object`, `clear_editable_recipe`, `add_object_metadata` bereitstellt.

**Persistenz:** SQLite (`plugin.db`) im Plugin-Verzeichnis. Tabellen: `sessions`, `messages`, `tool_calls`, `parameter_changes`, `model_states`, `active_structure_contexts`, `exposed_parameters`, `variants`, `snapshots`, `locked_objects`, `study_events`, `study_sessions`. Auto-Logging fuer Studienauswertung integriert.

---

## 3. Tool-Surface (KI-aufrufbar)

Das Modell bekommt pro Turn eine Liste von Tools, abhaengig von der Bedingung. Statt einer harten Drei-Tool-Trennung gilt seit der Designkorrektur 11.06.2026 (im Code umgesetzt) eine Rollen-Trennung:

- **Core-CAD-Tools (95):** interne Ausfuehrungs- und Inspektionsfaehigkeit des Assistenten; in beiden Bedingungen verfuegbar.
- **Interaction-Tools (13):** Werkzeuge, die eine explizite Designer-Affordanz bereitstellen, auswerten oder sichtbar machen; nur in `werkzeug`.

`build_tool_list("werkzeug")` liefert die volle Liste (**108 Tools**, seit 02.07.2026 — `curve_boolean_union` als core_cad-Tool ergaenzt, Pilot-Befund: 2D-Regionen-Union musste zuvor via `execute_rhino_code` improvisiert werden), `build_tool_list("basis")` dieselbe Liste minus der Deny-List `INTERACTION_TOOL_NAMES` (**95 Tools**, seit 02.07.2026). Die 13 interaction-Tools sind: Dialoge/Quick-Replies (`request_confirmation/_parameter/_parameters/_choice/_reference_pick`), Slider (`expose_parameters`, `clear_parameters`), Varianten (`create_/select_/delete_/clear_/finish_variants`) und die KI-zeigt-Deixis-Markierung (`select_objects`). `resolve_reference` (rein interne Text-zu-Objekt-Aufloesung), die read-only Selektions-Inspektion (`get_selected`, `get_gh_selected` — am 30.06.2026 von interaction nach core_cad verschoben, weil reine KI-Inspektion ohne Designer-Affordanz/Auswertungsspur; werkzeug-only haetten sie der Werkzeug-Bedingung eine Faehigkeit gegeben, die `basis` fehlt: eine direkt in Rhino gesetzte Selektion lesen) und die Sicherheitsschicht (`undo_last_action`, `redo_last_action`, `restore_object`, `list_backups`) bleiben bewusst core_cad, weil sie in beiden Bedingungen greifen muessen; nur die sichtbare Version-Timeline-UI bleibt werkzeug-exklusiv (Frontend-Gating). Spec-Grundlage: `Dokumente/studie/studienartefakt-spec.md` §1.

> **Lock-Tools am 11.06.2026 entfernt:** Die drei Lock-Tools (`lock_object`, `unlock_object`, `list_locked_objects`) waren bis 11.06.2026 Teil der Interaction-Tools (damals 17, `werkzeug` = 109). Sie wurden zusammen mit dem Lock-Panel aus dem Studienumfang genommen (siehe §9.4). Die Backend-Persistenz (`locked_objects`-Tabelle, REST-Endpoints) bleibt dormant.

Die Tools werden in `tool_registry.py` aufgebaut: `BUILTIN_TOOL_SCHEMAS` + `dedicated_tools.TOOL_SCHEMAS` + `DIALOG_TOOL_SCHEMAS` (das frühere `LOCK_TOOL_SCHEMAS` ist seit 11.06.2026 dormant und wird nicht mehr eingebaut).

### 3.1 Built-in (immer verfuegbar)

| Tool | Was | Warum |
|---|---|---|
| `capture_viewport` | Liefert per Default ein 2x2-Komposit aus Perspektive + Top + Front + Right mit ZoomExtents, Labels eingebrannt. Pro Tool-Call ein einziges JPEG mit eingepairtem `view_label`-Text-Block. | Eine Perspektive allein ist fuer geometrische Entscheidungen oft mehrdeutig; vier Ansichten gleichzeitig kosten ungefaehr soviel wie eine Single-View und liefern orthographische Disambiguierung. Designer-Kamera wird via `Rhino.DocObjects.ViewportInfo`-Roundtrip exakt restauriert. |
| `execute_rhino_code` | Freier Python-Fallback. | Fuer Faelle, die kein dediziertes Tool abdeckt. System-Prompt forderte explizit den Vorrang dedizierter Tools, damit der Tool-Trace lesbar und der Code-Pfad reproduzierbar bleibt. |
| `get_scene_info` | Kompakter Scene-Ueberblick (Layer, Object-Counts, Beispielobjekte). | In beiden Bedingungen wichtiges Grounding; im `werkzeug`-Modus ggf. durch die sichtbare Strukturkontext-Affordance ergaenzt. |

### 3.2 Dedicated Tools (Core-CAD, in beiden Bedingungen)

Nach Kategorie:

**Inspektion (10):** `get_scene_info`, `get_object_info`, `get_layer_info`, `get_layers`, `get_brep_component_info`, `get_scene_objects_with_metadata`, `get_selected`, `get_subd_info`, `list_backups`, `get_objects`

**Geometrie-Erzeugung primitive (12):** `create_box`, `create_point`, `create_line`, `create_polyline`, `create_circle`, `create_arc`, `create_rectangle`, `create_sphere`, `create_cylinder`, `create_cone`, `create_torus`, `create_pipe`

**Geometrie-Erzeugung Kurven (4):** `create_interpolated_curve`, `create_control_point_curve`, `extrude_curve`, `loft_curves`

**Kurven-Operationen (10):** `offset_curve`, `join_curves`, `fillet_curve`, `divide_curve`, `explode_curves`, `extend_curve`, `rebuild_curve`, `project_curve_to_surface`, `sweep1`, `sweep2`

**Flaechen (5):** `planar_surface`, `cap_planar_holes`, `join_surfaces`, `offset_surface`, `thicken_surface_to_solid`, `revolve_curve`

**Boolean (4):** `boolean_union`, `boolean_difference`, `boolean_intersection`, `boolean_split`

**Transformation (10):** `move_object`, `copy_object`, `rotate_object`, `scale_object`, `mirror_object`, `array_linear`, `array_polar`, `delete_object`, `set_layer`, `rename_object`

**BREP-Component-Edit (sprach-CAD-spezifisch) (9):** `resize_box_face`, `resize_cylinder_face`, `resize_extrusion_face`, `move_brep_face_along_normal`, `move_brep_face_in_direction`, `fillet_brep_edge`, `chamfer_brep_edge`, `round_edges_by_rule`, `set_bbox_dimension`

**Loch- und Nut-Operationen (2):** `create_hole`, `create_slot`

**SubD (10):** `create_subd_box`, `create_subd_sphere`, `create_subd_cylinder`, `mesh_to_subd`, `quad_remesh_to_subd`, `subd_to_nurbs`, `subd_to_mesh`, `subd_crease_edges`, `subd_set_vertex_position`, `subd_extrude_faces`, `subd_offset_faces`, `subd_subdivide`

**Grasshopper (8):** `execute_gh_code`, `get_gh_context`, `get_gh_selected`, `update_script`, `update_script_with_code_reference`, `expire_and_get_info`, `add_component`, `arrange_gh_layout`, `clear_gh_session`

**Parametrik (2):** `expose_parameters`, `clear_parameters`

**Referenz und Selektion (2):** `resolve_reference` (heuristisches Aufloesen sprachlicher Referenzen wie „die obere vordere Kante" in konkrete Edge-Indizes), `select_objects` (setzt Rhinos Selektion auf eine ID-Liste, damit die KI dem Designer visuell zeigen kann was sie meint)

**History / Reversibilitaet (4):** `undo_last_action`, `redo_last_action`, `restore_object`, `list_backups`

**Varianten (5):** `create_variant`, `select_variant`, `finish_variants`, `delete_variant`, `clear_variants`

### 3.3 Lock-Panel-Tools (`werkzeug`-Modus, Interaction) — **am 11.06.2026 entfernt**

> **Status 11.06.2026:** Die drei Lock-Tools (`lock_object`, `unlock_object`, `list_locked_objects`) sind aus der Tool-Surface entfernt (Scope-Cut vor dem Pilot). Sie sind nicht mehr in `build_tool_list("werkzeug")`; das `LockPanel.tsx` ist gelöscht. Die `locked_objects`-Tabelle, die REST-Endpoints und die Export-Spiegelung bleiben dormant im Code (kein Schema-Bruch). Die folgende Beschreibung ist historisch.

| Tool (historisch) | Was |
|---|---|
| `lock_object` | Sperrte Objekt(e) gegen weitere Modifikation. Im System-Prompt-Addendum bekam das Modell pro Turn die Liste der gesperrten GUIDs und die Anweisung, sie nicht anzufassen. |
| `unlock_object` | Hob die Sperre auf. |
| `list_locked_objects` | Listete aktuelle Sperren. |

Historische Regel: das Modell durfte `lock_object` NUR auf expliziten Designer-Wunsch aufrufen („sperr X", „X soll bleiben"). Aus eigenem Antrieb verboten.

### 3.4 Dialog-Tools (`werkzeug`-Modus, Interaction/HITL)

Das sind nicht-modifizierende Tools, die nur eine Inline-Karte im Chat erzeugen und den Agent-Lauf pausieren. Antwortet der Designer (Klick oder Text), laeuft der Agent in der naechsten Runde weiter.

| Tool | Wann nutzen |
|---|---|
| `request_confirmation` | Rueckfrage mit DREI Optionen (25.06.2026): **Bestaetigen** / **„Stattdessen…"** (klappt ein Korrektur-Eingabefeld auf, der getippte Text geht als freie Antwort) / **Abbrechen**. Pfeiltasten (← →/↑ ↓) wechseln die Auswahl, Enter loest die gewaehlte aus; Enter bestaetigt auch aus dem (leeren) Composer. Frontend-only, hash-neutral. |
| `request_parameter` | EIN numerischer Wert mit Default + 3-5 Quick-Chips + optional Slider. Fuer Radius, Laenge, Abstand, Anzahl, Winkel. |
| `request_parameters` | MEHRERE zusammengehoerige Werte in EINER gebuendelten Karte (z.B. Tiefe + Breite einer Nut): alle Felder mit Default/Slider/Quick-Chips, EIN „Uebernehmen", EINE kombinierte Antwort — statt mehrfach `request_parameter` hintereinander. Quick-Chips SETZEN hier nur das Feld (kein Auto-Send). (19.06.2026) |
| `request_choice` | 2-4 klare Alternativen (z.B. „abrunden vs. fasen"). |
| `request_reference_pick` | Bittet den Designer im Viewport eine Referenz (Objekt/Flaeche/Kante/Punkt) zu picken. Triggert dieselben WS-Messages wie der manuelle Pick-Button in der InputBar. Nur als Last Resort: wenn kein Pick im User-Turn, `resolve_reference` keinen klaren Treffer hat, UND keine Regel-Anwendung passt. Bei „alle oberen Kanten" greift `round_edges_by_rule`, nicht die Pick-Karte. |

**Repair-on-Error (18.06.2026):** Wenn ein Tool-Call fehlschlaegt, soll das Modell nicht denselben Call blind wiederholen, sondern sofort eine knappe Diagnose plus konkrete Alternative liefern. In `werkzeug` nutzt es dafuer, wenn passend, `request_confirmation` als Reparaturkarte (`Reparaturvorschlag`, `Reparatur versuchen`, `Abbrechen`); Enter bestaetigt die aktive Karte, Abbrechen sendet eine explizite Abbruch-Antwort. In `basis` ist `request_confirmation` nicht in der Tool-Liste, daher erscheint derselbe Reparaturvorschlag verbal im Chat. Hash-Stand: nur `system_prompt` bewusst neu verankert; `tools_basis` und `tools_werkzeug` bleiben unveraendert.

Warum so:
- Quick-Replies reduzieren Tippen ohne den freien Chat zu beschneiden.
- Die Dialog-Tools werden NICHT als Snapshot getriggert (sind read-only fuer Rhino), damit das ModelState-Logging nicht mit „awaiting_user"-Pseudo-Aktionen geflutet wird.
- Sie sind im `basis`-Modus deaktiviert, damit dort keine zusaetzliche teilnehmendenseitige Dialog-Affordance entsteht. Die CAD-Ausfuehrungsfaehigkeit des Assistenten bleibt davon getrennt.

---

## 4. Interaktionswerkzeuge (Designer-initiiert)

Die UI hat eine Reihe von Buttons in der InputBar, die nicht das Modell, sondern direkt Rhino ansteuern. Jeder Button schickt eine `viewport.request_*`-WS-Message, das Backend marshallt auf den Rhino-UI-Thread und liefert das Ergebnis als naechsten User-Block ueber `viewport.snapshot`-/`viewport.pick_result`-/`viewport.point_result`-/etc.-Event zurueck. Pick-/Point-/Component-Resultate werden seit 18.06.2026 als echte Inline-Referenz-Tokens an der aktuellen Cursorposition in den Chat-Composer eingefuegt; Bild- und Sketch-Resultate bleiben als groessere Attachment-Kacheln gestaged. Beim Submit werden alle sichtbaren Tokens wieder in die unveraenderten `ContentBlock`-Payloads serialisiert.

| Werkzeug | UI-Affordance | Resultat-Block |
|---|---|---|
| Punkt-Pick | Fadenkreuz-Icon | `point_pick`-Block mit Koordinaten, Snap-Typ und optional dem gepickten Objekt. |
| Komponenten-Pick | Komponenten-Icon (Kante/Flaeche/Objekt) | `component_pick`-Block mit Component-Type (`edge`/`face`/`object`), -Index, Object-ID, Object-Class, `geometry_class` (brep/extrusion/subd/mesh) und `allowed_operations`-Liste (z.B. `["resize_box_face"]` fuer eine `primitive_box`-Face, `["subd_crease_edges"]` fuer eine SubD-Kante). EIN Button: der Auto-Modus erkennt die Geometrieklasse selbst (siehe [§4.1](#41-komponenten-pick-geometrieklassen-dispatch-brepextrusionsubdmesh)). **Vorauswahl:** sind vor dem Klick bereits Objekte im Viewport markiert, referenziert der Button alle als `selection`-Block (Mehrfachauswahl; loeste den frueheren separaten Objekt-Pick-Button ab). |
| Snapshot + Sketch | Stift-Icon | `sketch`-Block. EIN Werkzeug fuer „Viewport anhaengen" UND „darauf zeichnen" — der frueher separate Kamera-Button wurde am 16.06.2026 hier eingeschmolzen (Begruendung: Screenshot ist ein Sketch ohne Striche; das Modell macht autonom Screenshots via `capture_viewport`; in den RtD-Sessions wurde der manuelle Screenshot nie genutzt). Im Overlay ein **Ansichts-Waehler** (Perspektive/Top/Front/Right), Default Perspektive; Zeichnen ist optional. **Mehransichts-Markierung (19.06.2026):** Striche werden pro Ansicht gehalten — beim Ansichtswechsel werden sie geparkt statt verworfen (oranger Punkt markiert Ansichten mit Strichen), sodass der Designer mehrere Ansichten in einer Sitzung annotieren kann. **Mit Strichen** → pro markierter Ansicht ein eigener `sketch`-Block (`rendered_png` = scharfe annotierte Einzelansicht, Deixis, eigener `view_name`). **Ans Modell geht nur diese Einzelansicht** (Entscheidung C, 22.06.2026): das Backend-`SketchBlock`-Schema deklariert `composite`/`has_strokes`/`view_name` NICHT → sie werden bei der Message-Validierung verworfen und erreichen das Modell nie; der Flattener nimmt den Legacy-Pfad (`rendered_png or background`). Begruendung: die kleine 2x2-Composite-Zelle (~620px nach API-Downscale) wuerde die Striche unschaerfer machen, und raeumlichen Kontext holt sich das Modell bei Bedarf selbst via `capture_viewport`. Das Composite reitet weiterhin als UI-Kachel/Logging mit (am ersten Block), geht aber nicht ans Modell. **Ohne Striche** → reiner Multi-View-Snapshot; die Composite wird hier AUCH als `rendered_png` gestaffelt, damit sie ueber denselben Legacy-Pfad ans Modell kommt (**Bugfix 22.06.2026:** vorher wurde nur `composite` gestaged → vom Schema verworfen → es kam GAR KEIN Bild beim Modell an, nur eine zudem falsche Deixis-Caption). Der Flattener unterscheidet die Caption per `svg`-Leerheit: leere `svg` → Snapshot-Kontext-Caption, sonst Deixis-Caption. Die Anhang-Kachel zeigt das Composite (erster Block) bzw. die markierte Ansicht (weitere Bloecke); `has_strokes`/`view_name` pro Block sind die Auswertungsspur (Perzeption vs. Deixis), jetzt eine Zeile pro markierter Ansicht. |
| Bildanhang | Paperclip | `image`-Block aus lokaler Datei. |

**Parametrisieren-Werkzeug (`SlidersHorizontal`-Icon; frueher der „GH"-Button, umbenannt 26.06.2026).** Macht das markierte/referenzierte Objekt parametrisch (Regler fuer die Masse). Feuert NICHT mehr direkt, sondern **befuellt den Composer vor**: ein Klick legt einen Auswahl-Chip + einen Befehls-Chip „Parametrisieren" (Slider-Icon, auto-breit, Akzent-Ring, als Einheit loeschbar) ins Eingabefeld; der Designer bestaetigt/editiert und sendet selbst. Der Befehls-Chip serialisiert beim Senden zum identischen fixen Parametrik-Text → die Modell-Nachricht ist byte-gleich wie zuvor (`[selection, Text]`), **hash-neutral**. **Verschmelzung mit K/F/O:** liegt schon eine Objekt-Referenz im Feld (Auswahl- oder K/F/O-Chip; eine K/F/O-Kante traegt ihr Host-Objekt → dessen Objekt wird parametrisiert), wirkt „Parametrisieren" DARAUF statt einen Zwangs-Pick zu starten; ein frischer Pick laeuft nur bei leerem Feld. **Pick-Modus:** Standard Einzel-Pick (`rs.GetObject`, sofort, kein Enter); Shift+Klick auf den Button = Mehrfach (`rs.GetObjects`, mit Enter). Aus der frueheren autonomen Aktion wird damit ein **vom Designer bestaetigter** Schritt (HITL). *(Geplant, noch nicht gebaut: ein kontextueller Auto-Vorschlag-Chip ueberm Feld bei reiner Objekt-Auswahl.)*

**Inline-Referenz-Tokens (18.06.2026):** Punkt-, Komponenten- und Selection-Referenzen werden nicht mehr als vorgelagerte Chips unter dem Eingabefeld gesammelt, sondern direkt im Textfluss angezeigt. Der Designer kann vor, zwischen und nach den Tokens schreiben; `x` und Backspace entfernen einzelne Tokens. Kurze Labels bleiben voll sichtbar (`Punkt: (...)`, `Flaeche #3 auf ...`, `Objekt: ...`), lange Labels werden im Chip gekuerzt und zeigen bei Hover/Fokus eine fixed-position Detailblase nach oben, ohne Layout-Shift. Sichtbarkeit und Nutzbarkeit sind `werkzeug`-only; in `basis` wird der Token-Callback nicht registriert und Referenz-Tokens werden beim Wechsel entfernt. Unter der Haube traegt jedes Token den vollstaendigen Pick-Block (`object_id(s)`, Snapshot, Component-Index/-Info, `allowed_operations` usw.) weiter an das Modell. **Immer inline (19.06.2026):** Das Einfuegen ist NICHT mehr daran gekoppelt, ob der Composer gerade aktiv ist — ein Pick-Result waehrend eines laufenden Runs (Composer fuer Tippen gesperrt, `contentEditable=false`) landet trotzdem als Inline-Token IM Feld statt als Chip darueber (`range.insertNode` ist ein DOM-Op, unabhaengig von `contentEditable`; das Token stagt nur fuer die naechste Nachricht, sendet nichts). Den frueheren Chip-ueber-dem-Feld-Fallback gibt es damit praktisch nicht mehr. **Root-Cause-Fix (19.06.2026):** Trotz der obigen Logik landeten Referenzen in der Praxis IMMER als Chip ueber dem Feld — der `referenceTokenCallback` wurde von `setActiveSession` (Store) bei jedem Sitzungswechsel UND beim Boot auf `null` zurueckgesetzt, obwohl ihn `InputBar` nur EINMAL beim Mounten registriert (Effekt-Deps `[setter, showWerkzeugSlots]`, InputBar ist nicht pro Session ge-key-t → kein Remount → keine Neu-Registrierung). Boot's `setActiveSession` toetete den Callback damit dauerhaft → `stageReferenceBlock`'s `cb?.(block)` war stets falsy → `addStagedAttachment` → Chip oben. (Der GH-`pickCallback` funktionierte, weil er pro Klick frisch registriert wird.) Fix: `referenceTokenCallback` aus der `setActiveSession`-Reset-Liste entfernt (es ist UI-Wiring der Mount-Lebensdauer, keine Session-Daten); `pickCallback` bleibt zuruecksetzbar (legitimer One-Shot-Drop). Frontend-only, hash-neutral. **Hover-Viewport-Highlight (18.06.2026):** Beim Hover ueber ein Token hebt das Plugin zusaetzlich die referenzierte Geometrie im Rhino-Viewport hervor (Kante/Flaeche/ganzes Objekt) — als Verifikation „meinst du diese Stelle?" vor dem Senden. Realisiert **non-mutating** ueber ein transientes `Rhino.Display.DisplayConduit`-Overlay (orange), das NICHT die Selektion aendert (kein `rs.SelectObject` → kein selection_watcher-/Badge-/Logging-Seiteneffekt). WS-Roundtrip `viewport.highlight_reference` / `viewport.highlight_clear` → `backend/viewport_bridge/highlight.py` (Sub-Objekt-Aufloesung via `object_id`+`component_index`, defensiver Fallback auf ganzes Objekt bzw. Punkt-Marker). werkzeug-only; hash-neutral (reine WS-/Viewport-Schicht). **Tatsaechlich aktiv erst ab 19.06.2026:** Dieses Hover-Highlight konnte vorher NIE feuern — es haengt an Inline-Tokens, und die erschienen wegen des `referenceTokenCallback`-Bugs (siehe Root-Cause-Fix oben) gar nicht im Feld. Nach dem Inline-Fix kam ein zweiter Bug zum Vorschein: `insertReference` fror den Highlight-Handler beim Mount ein (nicht in den useCallback-Deps), und beim ersten Render ist `onHighlightReference` noch `undefined` (WebSocket verbindet erst async → `showWerkzeugSlots && connected` false). Jedes Token bekam damit dauerhaft KEINE Hover-Listener. Fix: der Handler liegt jetzt in einem Ref (`highlightRef`) + stabiler `emitHighlight`-Wrapper, den die Tokens IMMER aktuell auslesen. Zusaetzlich gefunden+gefixt (adversariale Review): beim Entfernen eines gerade gehoverten Tokens (× / Backspace / Senden-`clear()` / Bedingungswechsel-`removeReferences()`) feuert `mouseleave` nicht zuverlaessig → das Overlay blieb „eingebrannt"; jetzt sendet jeder Entfern-Pfad vorab `emitHighlight(null)`. Frontend-only, hash-neutral. **Erweiterung auf Choice-Optionen (19.06.2026):** Auch eine `request_choice`-Option kann eine konkrete Geometrie meinen ("linke Seite" / "rechte Seite"). Jede Option traegt daher ein optionales `reference`-Feld (`object_id` + optional `component_type`/`component_index`); Hover/Fokus ueber den Options-Button hebt diese Geometrie ueber denselben DisplayConduit-Pfad hervor. Der Frontend-Block wird explizit als `component_pick` (Default `component_type:"object"`) gebaut, damit der Backend-Handler ihn ohne Inferenz aufloest. Dies ist die **einzige hash-relevante** Stelle des Hover-Highlights: das `reference`-Feld erweitert das `request_choice`-Schema → `tools_werkzeug` am 19.06.2026 bewusst neu verankert (request_choice ist werkzeug-only → `tools_basis` unveraendert; die Modell-Anleitung liegt in der Schema-Feldbeschreibung, nicht im SYSTEM_PROMPT → `system_prompt` unveraendert).

**Warum dieser Pfad:** der Designer behaelt die Kontrolle ueber WAS dem Modell gezeigt wird (statt dass das Modell mit `capture_viewport`/`get_scene_info` selbst entscheidet). Picks werden direkt in der Modellumgebung gemacht, wo Spatial Reasoning natuerlich ist. Die `allowed_operations`-Liste auf der Component-Pick-Karte schraenkt die KI auf semantisch sinnvolle Tools ein.

### 4.1 Komponenten-Pick: Geometrieklassen-Dispatch (Brep/Extrusion/SubD/Mesh)

Der „K/F/O"-Pick deckt nicht mehr nur Breps ab, sondern zusaetzlich SubD (Kante/Flaeche) und Mesh — ueber einen Geometrieklassen-Dispatch im Resolver (`viewport_bridge/pick.py`). Wichtig: das bleibt **ein Button im Auto-Modus**; es gibt keinen zusaetzlichen UI-Slot und kein neues Tool. Der Resolver erkennt die Geometrieklasse selbst und bindet die gepickte Komponente an die jeweils passende Operation.

**K/F/O = Kante/Flaeche/Objekt — Vertex gestrippt (26.06.2026).** Unter `component_type="auto"` (= der K/F/O-Button) gibt der SubD/Mesh-Resolver nur noch Kante/Flaeche zurueck; ein Vertex-Treffer faellt auf die Whole-Object-Referenz durch. Vertices/Ecken laufen **ausschliesslich ueber den Crosshair-Punkt-Pick** (Rhino-Osnap, Snap „Endpunkt"/„Vertex"). Vertex-Icon ist aus dem Komponenten-Chip + der MessageBubble entfernt; der explizite (dormante) `component_type="vertex"`-Pfad bleibt im Code, haengt aber an keinem Button. Begruendung: Pick-Granularitaet bewusst nicht evaluiert, und ein Vertex ist konzeptuell Sache des Punkt-Pickers (snappt auf Ecken), nicht von K/F/O. Reine UI-/Pick-Schicht, hash-neutral. **Die folgenden SubD/Mesh-Vertex-Absaetze beschreiben damit den dormanten expliziten Pfad — ueber „auto"/K/F/O sind Vertices nicht mehr erreichbar.**

**Dispatch nach Geometrieklasse.** `_resolve_component_pick` ruft zu Beginn `_geometry_class(obj_ref)` auf, das `obj_ref.Object().Geometry` per `isinstance` klassifiziert (Reihenfolge: `SubD` → `subd`, `Mesh` → `mesh`, `Extrusion` → `extrusion`, `Brep` → `brep`, sonst `None`). Bei `subd`/`mesh` geht es in den indizierten Pfad, fuer jede andere Klasse (Brep, Extrusion und unbekannt/`None`) bleibt der bisherige typisierte Accessor-Pfad ueber `obj_ref.Edge()`/`.Trim()` bzw. `.Face()` der Default-Zweig. Dessen Aufloesungs-Logik ist unveraendert; lediglich die Rueckgabe traegt jetzt zusaetzlich die Geometrieklasse als drittes Tupel-Element.

**SubD/Mesh werden indiziert aufgeloest.** Weil `ObjRef.Edge()`/`.Face()` Brep-only sind und fuer SubD/Mesh `None` liefern, bestimmt der indizierte Pfad den Sub-Komponenten-Typ aus `obj_ref.GeometryComponentIndex`: der `ComponentIndexType`-Name wird per Substring klassifiziert (`edge` → Kante, `face`/`surface` → Flaeche, `vertex` → Vertex). So greifen die von Rhino gelieferten Enum-Werte (`SubdEdge`/`SubdFace`/`SubdVertex` bzw. `MeshTopologyEdge`/`MeshFace`/`MeshTopologyVertex`). Die konkreten Geometrie-APIs (`subd.Edges.Find`, `mesh.TopologyEdges`, `mesh.TopologyVertices[idx]` usw.) kommen erst bei der Positions- und Info-Aufloesung zum Einsatz.

**Sub-Object-Selektion defensiv.** `go.SubObjectSelect = True` wird unbedingt gesetzt und deckt Kanten/Flaechen fuer Brep + SubD + Mesh ab. Anschliessend wird in einem `try/except` der `GeometryAttributeFilter` auf die Veroderung von `SubSurface|EdgeCurve|MeshVertex|MeshEdge|MeshFace|SubDVertex|SubDEdge|SubDFace` gesetzt; ist das Enum/Property in diesem Rhino-Build nicht verfuegbar, verwirft ein stilles `except: pass` den Filter und der bereits gesetzte `SubObjectSelect`-Default bleibt wirksam.

**Bindung an Operationen.** `_component_info` liefert fuer SubD/Mesh den Referenzpunkt plus `total_edges`/`total_faces`/`total_vertices`; `_allowed_operations` schlaegt je nach Geometrieklasse und Komponente die passende Op vor:

| Pick | gebundene Operation |
|---|---|
| SubD-Kante | `subd_crease_edges` |
| SubD-Flaeche | `subd_extrude_faces` (Default), `subd_offset_faces` (teilt dieselbe Parametrik, wenn das Modell den Toolnamen ueberschreibt) |
| SubD-Vertex *(dormant)* | `subd_set_vertex_position` — *seit 26.06.2026 nicht mehr ueber K/F/O-`auto` erreichbar; Tool bleibt, wird aber nicht via Komponenten-Pick gebunden (Vertices via Crosshair-Punkt-Pick)* |
| SubD-Objekt | `subd_subdivide` (Default-Zweig auch fuer nicht abgedeckte Werte) |
| Mesh (jede Komponente) | `quad_remesh_to_subd` (Konversion zu SubD) |

**Mesh: Konversion zuerst.** Ein Mesh-Pick wird nicht nativ editiert (kein natives Mesh-Editing). Er bietet stattdessen die Konversion zu SubD an (`quad_remesh_to_subd`); danach laeuft die Arbeit im SubD-Pfad weiter, und alle SubD-Komponenten-Picks stehen zur Verfuegung. Die `_component_info`-Payload traegt dazu einen `hint` auf `quad_remesh_to_subd`.

**Router-Bindung.** Der Component-Router (`component_router.py`) liest `geometry_class` aus dem persistierten `component_pick`-Block und mappt `object_id` → `subd_id` (das alte `object_id`-Feld wird entfernt) sowie `component_index` → `edge_indices=[idx]` / `face_indices=[idx]` / `vertex_index=idx`. SubD-/Mesh-Picks passieren den Guard (`_guard_tool_from_recent_component_picks` gibt fuer `subd`/`mesh` frueh `None` zurueck) und durchlaufen damit keine der Brep-spezifischen Gates (`_FACE_ONLY_TOOLS` usw.). Bei `subd_set_vertex_position` kommen aus dem Pick nur `subd_id` + `vertex_index`; die Pflichtfelder `x`/`y`/`z` liefert das Modell.

**Logging und Hashes.** Ein Vertex-Pick wird wie Kante/Flaeche/Objekt als `component_pick`-Block persistiert; `_derive_modality` mappt `component_pick` → Modalitaet `pick` (unveraendert, ohne Sonderfall pro Komponente). Die Erweiterung ist reine Pick-Infrastruktur: keine neuen Tools, keine Aenderung an `tool_schemas.py`/`tool_registry.py`/`dispatch_tables.py` — die per `validate_hashes.py` eingefrorenen Golden Hashes (`SYSTEM_PROMPT` + `build_tool_list`) bleiben damit unveraendert. Die `allowed_operations`-Strings im Pick-Payload sind Hinweise, keine Tool-Registry-Eintraege.

**Hover-Picker statt Auswahlmenue (25.06.2026).** Der „K/F/O"-Komponenten-Pick laeuft im `werkzeug`-Pfad nicht mehr ueber Rhinos `GetObject`-Auswahlmenue, sondern ueber einen **GetPoint-basierten Hover-Picker** (`viewport_bridge/pick.py`, Schalter `USE_HOVER_PICKER`): Cursor ueber das Objekt → die nahelegendste Kante/Flaeche leuchtet **live blau** → Klick bestaetigt, kein Menue. Der bewaehrte `GetObject`-Pfad bleibt als automatischer Fallback bei jedem Fehler. Der Hit-Test ist `viewport.GetPickTransform(windowPoint)` → `PickContext` → `doc.Objects.PickObjects`; weil PickObjects keinen Sub-Komponenten-Index liefert, wird die Komponente **punktbasiert** aus dem Trefferpunkt (`SelectionPoint`) bestimmt (`_nearest_component_on_brep`: naechste Kante, sonst getroffene Flaeche; Flaeche robust via `Brep.ClosestPoint`+ComponentIndex). Der Pick **respektiert den Display-Modus** (bewusste Nutzerentscheidung): Wireframe = see-through (Kanten + geschlossene Solids, Durchgriff zur Rueckseite), Shaded/Rendered = Flaechen auch offener Objekte. Da die KI durchweg geschlossene Solids erzeugt (`create_box`/`create_slot`/gekappte `extrude_curve`/Booleans), sind dort Flaechen+Kanten in jedem Modus waehlbar; offene, handgebaute Sweeps sind im Wireframe nur kanten-waehlbar (Flaechen via Shaded). Identischer `ComponentPickResult`-Payload wie der alte Pfad (`_finalize_component_pick`) → keine Aenderung an Resolver-Output, Router, Logging. Reine Viewport-/Pick-Schicht, **hash-neutral** (pick.py nicht im Golden-Hash). Iterations-Verlauf: `log.md` (24./25.06.2026).

**Picker-Modifier (25.06.2026, je per Flag in `pick.py` abschaltbar — `ENABLE_MULTI_PICK` / `ENABLE_OBJECT_MODIFIER`).** Beim K/F/O-Pick:
- **Shift = Mehrfachauswahl** — der Pick bleibt offen und sammelt Komponenten (jedes erscheint sofort als Inline-Token), bis ein Klick ohne Shift, **Enter** oder **Shift-Loslassen** folgt. Konvention „Shift = zur Auswahl hinzufuegen". Esc verwirft alles.
- **Strg = ganzes Objekt** statt Kante/Flaeche (Hover hebt das ganze Objekt hervor, Klick referenziert es). Ersetzt den frueheren separaten Objekt-Pick-Button. **Shift+Strg** = mehrere ganze Objekte.
- Drei Rhino-GetPoint-Eigenheiten gefixt: **Strg-Elevator-Modus** aus (`PermitElevatorMode(0)`, sonst brauchte Strg+Klick zwei Klicks); **Shift-Loslassen beendet den Pick** ueber `AcceptCustomMessage` + `PostCustomMessage` aus OnMouseMove (es gibt keinen Key-Up-Hook); Mehrfach-Picks werden **live** rausgestreamt (`on_component` → `run_coroutine_threadsafe`) und der Picker zeichnet die schon gewaehlten Komponenten **selbst duenn mit** (Accumulator in `OnDynamicDraw`), weil der Backend-Conduit waehrend des modalen Picks nicht zeichnen kann. Mehrfach-Picks ueberspringen den Snapshot je Pick (`capture_snapshot=not multi`).

**Highlight-Schichten + Farbe (`viewport_bridge/highlight.py`).** Zwei getrennte, non-mutating DisplayConduit-Overlays in **Brand-Blau** (durchgaengig, seit 25.06.2026 — vorher orange; `_HV_*` = 40,102,246):
- **Hover** (`highlight_reference(s)` / WS `viewport.highlight_reference`): dick (5px) + transluzenter Flaechen-Fill, nur waehrend der Token-Hover. Punkt-Token-Hover hebt NUR den Punkt hervor (nicht mehr das gesnappte Host-Objekt).
- **Persistent** (`set_persistent_references` / WS `viewport.persistent_refs`, neu 25.06.2026): **duenn** (2px), **kein Fill** — die mit K/F/O gewaehlten Komponenten bleiben markiert, solange ihr Token im Composer steht; eigener `_persist_conduit` (gleiche Klasse, `_thickness`/`_draw_fills` pro Instanz), damit das Hover-Clear sie nicht wegwischt. Das Frontend sendet die gestagten `component_pick`-Refs bei Composer-Aenderung und leert nach Senden/Run-Ende (analog den Punkt-Markern `set_point_markers` / `viewport.point_markers`, jetzt ebenfalls blau).

---

## 5. Direkt-Manipulation: Parameter-Panel und Strukturkontext

### 5.1 Slider-Mechanik

Wenn das Modell `expose_parameters` ruft, erscheinen Slider unter dem Chat-Input. Der Designer kann sie direkt ziehen, jede Aenderung schickt eine `parameter.changed`-WS-Message ans Backend, das `apply_parameter_change` aufruft.

Drei Action-Typen werden unterstuetzt:
- `editable_recipe_value`: das Objekt wird aus seinem `editable_recipe`-UserText neu gebaut. Fuer primitive_box, primitive_cylinder, primitive_extrusion. Praezise, kein Floating-Point-Drift.
- `scale_axis` / `scale_uniform`: relatives `rs.ScaleObject` mit berechnetem Faktor. Fuer beliebige Objekte ohne Recipe.
- `gh_slider`: HTTP-Call an die Grasshopper-Bridge, setzt den Wert eines Number-Slider-Components.

**Smoothness und Korrektheit (28.05.2026 stabilisiert):**

1. **Snapping auf Step-Raster:** `_range_for_value` floored min, ceilt max auf Step-Vielfache. Ohne das wandert ein 1mm-Step-Slider mit min=29.75 nur ueber `29.75, 30.75, ...` — was dann auch die Recipe-Werte drifften liess.
2. **Coalescing pro Slider:** wenn ein Apply laeuft, werden eingehende Werte in `_pending_param_value` gespeichert und sofort zurueck-geACKt. Der Drain-Loop schnappt sich nach Abschluss den NEUESTEN Pending-Wert und ueberspringt alle Zwischenwerte. So bleibt der WS-Event-Loop responsive selbst bei 60-Hz-Drag.
3. **Deferred Refresh:** `apply_parameter_change(defer_refresh=True)` ueberspringt den teuren Strukturkontext-Refresh waehrend des Drags. Der Drain-Loop macht am Ende EIN `refresh_structure_context_in_store`.

### 5.2 Strukturkontext

Wenn ein Objekt ein `editable_recipe`-UserText hat (gesetzt bei `create_box`, `create_cylinder`, `create_extrusion`), kann das Backend daraus automatisch Slider ableiten — auch ohne dass das Modell expose_parameters ruft. Der `structure_context_service` haelt pro Session genau einen aktiven Strukturkontext und synchronisiert ihn nach jeder Tool-Action.

**Recipe-Clear nach Topologie-Brueche (kritisch):** wird ein Loch in eine primitive_box gebohrt, ist die Box keine Box mehr. Der `clear_editable_recipe(guid)`-Helper im CODE_PREAMBLE wird nach jedem `Objects.Replace` in `create_hole`, `create_slot`, `round_edges_by_rule`, `fillet_brep_edge`, `chamfer_brep_edge` aufgerufen. Damit kann ein spaeterer Slider-Drag die Box NICHT mehr als frische Primitive rebuilden und das Loch silent zerstoeren.

Zusaetzlich: `_clear_structure_after_recipe_breaking_tool` cleart den aktiven Strukturkontext nach erfolgreichem Tool. UI-Slider verschwinden automatisch.

**Auto-Expose beim Erstellen — aber nur ohne genannte Masse (16.06.2026):** Frueher tauchten die Groessen-Slider erst beim Pick/Resize eines Primitivs auf, nicht direkt nach dem Erstellen (`_structure_candidate_object_ids` kannte die Create-Tools nicht). Jetzt exponiert das Backend die Slider konsistent schon beim Erstellen von `create_box`/`create_cylinder` — **aber nur, wenn der Designer keine Masse genannt hat**. Mechanik: die Groessen-Argumente (`width`/`depth`/`height` bzw. `radius`/`height`) sind im Tool-Schema optional; der System-Prompt weist das Modell an, sie bei masslosem Auftrag wegzulassen (statt Defaults zu fuellen). Das Set `_STRUCTURE_CREATED_OBJECT_TOOLS` in `dedicated_tools/_shared.py` gibt die erstellte GUID nur dann als Strukturkontext-Kandidat zurueck, wenn KEIN Groessen-Feld im `tool_input` ist (Position/Name/cap zaehlen nicht als Mass). Nennt der Designer ein Mass, erscheinen keine Auto-Slider — er hat die Groesse ja schon gewaehlt. Die Slider rendern wie immer nur in der `werkzeug`-Bedingung (ParameterPanel frontend-gated, `App.tsx`); der Broadcast ist bedingungsneutral. Golden Hashes `system_prompt` + `tools_basis` + `tools_werkzeug` dafuer bewusst neu verankert.

Bewusst KEIN Vor-dem-Bauen-Frageschritt fuer die Masse: eine `request_parameter`- oder Mehrfeld-Groessenkarte VOR dem Bauen wuerde den Flow hart blockieren (das Modell haelt an und wartet auf die Antwort; `request_parameter` ist zudem 1-wertig, eine Box hat drei Masse). „Sofort mit Default bauen + Slider danach" unterbricht dagegen nie: die Slider-Leiste ist ignorierbar und klappt beim Weitertippen automatisch ein, und die Masse lassen sich alternativ direkt im Chat ansagen (→ `set_bbox_dimension`). Designkriterium: den Workflow so wenig wie moeglich unterbrechen. `request_parameter` (der Vor-dem-Bauen-Slider, ParameterCard mit Default + Quick-Chips + Range-Slider) bleibt fuer EINwertige Rueckfragen reserviert (Radius, Laenge, Anzahl, Winkel), nicht fuer Primitiv-Masse.

### 5.3 Capability-Router

`capability_router.py` schreibt pro Turn einen kurzen System-Prompt-Addendum-Block: welcher Strukturkontext aktiv ist, welche Strukturparameter (slider-bindbar) verfuegbar sind, welche Tools darauf priorisiert werden sollen.

Pfad-Prioritaet:
1. Aktiver Strukturkontext + automatisch abgeleitete Strukturparameter
2. GH-Slider-Mirroring (wenn ein GH-Doc offen ist)
3. Bereits sichtbare freie Agent-Parameter
4. Erst danach: neue freie Slider oder freier Workflow

So vermeiden wir, dass das Modell `expose_parameters` mit `scale_axis` aufruft, wenn der saubere `editable_recipe`-Pfad zur Verfuegung steht.

---

## 6. Multi-View-Capture-Komposit

Der Snapshot-Pfad ist nicht trivial:

- Designer-Kamera muss exakt erhalten bleiben → `Rhino.DocObjects.ViewportInfo` Roundtrip (siehe Memory-Reference). PushViewProjection/PopViewProjection ist unzuverlaessig.
- Aspect-Ratio der Viewports kann variieren → Composite-Zellen werden mit der echten Capture-Aspect berechnet, kein Stretching.
- **(Dormant seit 16.06.2026** — galt fuer den manuellen Kamera-Button, der ins Sketch-Werkzeug eingeschmolzen wurde:) Beim manuellen Designer-Snapshot wurde `get_scene_info` automatisch dazu-gefetcht und als versteckter `caption`-Text an den ImageBlock gehaengt; beim Submit expandierte der Frontend-Code das in `[TextBlock(caption), ImageBlock]`. Das **Sketch-Werkzeug** nutzt diese Paarung NICHT (es sendet bei Strichen die markierte Einzelansicht + Deixis-Caption, bei strichlosem Snapshot das Composite + Snapshot-Caption — siehe Tabelle oben); das Modell-Tool `capture_viewport` ist bild-only. Die Capture-Mechanik darunter (ViewportInfo-Roundtrip, Aspect-genaue Composite-Zellen, eingebrannte Labels) gilt weiterhin fuer `capture_viewport` und den Multi-View-Capture des Sketch-Werkzeugs.
- Tool-Errors wie unbekannte Views oder fit-Modi liefern eine klare deutsche Fehlermeldung statt eines Crashs.
- Composite-Cells haben Labels eingebrannt („Top", „Front", „Right", „Aktuelle Ansicht"). System-Prompt instruiert das Modell explizit: 2x2-Raster mit Labels = vier Ansichten DERSELBEN Szene, keine vier verschiedenen Objekte.

Token-Bilanz: ein 1024px-Komposit kostet ungefaehr wie eine einzelne 800px-Single-View. Multi-View ist daher nicht 4x teurer.

**Ortho-Auto-Fit nach dem Turn (23.06.2026):** Am Ende eines erfolgreichen KI-Turns werden die Ortho-Ansichten **Top/Front/Right** automatisch auf die sichtbare Geometrie eingepasst (`fit_ortho_viewports_if_changed` in `viewport_bridge/snapshot.py`, Hook in `agent/loop.run_agent`), damit ein frisch erzeugtes groesseres Objekt nicht zu nah/zu fern im Viewport sitzt. **Perspektive bleibt unberuehrt** (Navigationsraum des Designers). Feuert NUR bei tatsaechlicher Geometrie-Aenderung (gerundete Bbox-Signatur vs. letztem Fit) — ein manueller Ortho-Zoom ueberlebt also reine Chat-/Abfrage-Turns. Gefittet wird ueber `ZoomBoundingBox` (zuverlaessig auch auf nicht-aktiven Viewports; `ZoomExtents` ist es dort nicht), auf dem Rhino-UI-Thread (`InvokeOnUiThread`+Event, via `asyncio.to_thread`), voll defensiv (ein Fehler kippt den Turn nie nachtraeglich) und nur auf dem Normal-Pfad (nicht bei Cancel/Crash). **Bewusst in BEIDEN Bedingungen** (basis + werkzeug): Kern-Viewport-Verhalten, kein sichtbares Interaktionswerkzeug — es haelt die Live-Orthos konsistent mit dem, was das Modell via `capture_viewport` (fit=extents) ohnehin sieht; daher kein Bedingungs-Confound und unkonditional (kein `if condition`).

---

## 7. Studienartefakt-Spezifika

### 7.1 Conditions (basis vs werkzeug)

Im `SettingsDialog.tsx` waehlt der Designer beim Anlegen einer Session die Bedingung. Sie ist danach unveraenderlich.

- **`basis`:** Core-CAD-Toolstack wie `werkzeug`, aber keine Dialog-Tools, keine Pick-/Sketch-/Slider-/Varianten-/Panel-Affordances ausser Chat und Bild-Attachment. Promptbasierte Vermittlung bei gleicher CAD-Kompetenz.
- **`werkzeug`:** derselbe Core-CAD-Toolstack + alle UI-Slots und Interaction-Tools.

`build_tool_list(condition)` ist die einzige Stelle die das entscheidet — `if condition == ...`-Checks sind explizit verboten in §1.2 der Studienartefakt-Spec.

### 7.2 Automatisches Studien-Logging

Drei Tabellen die im Auswertungs-Pfad ausgewertet werden:

- `tool_calls`: jeder Tool-Aufruf mit Args, Result, Dauer.
- `model_states`: vor jedem modifizierenden Tool ein Snapshot der Szene (JPEG-Path + scene_summary JSON). `is_modifying_tool` entscheidet — Unknowns sind fail-safe modifizierend.
- `parameter_changes`: jede Slider-Bewegung mit Source (`user`/`tool_call`).

Dialog-Tools und reine Read-Tools triggern KEINEN model_state-Snapshot — sonst waere die Tabelle mit „awaiting_user"-Pseudo-Aktionen geflutet.

**Auswertung der Tool-Nutzung:** `tools/tooluse_report.py` liest dieses Log (oder ein exportiertes Bundle-SQLite) **read-only** und rechnet pro Bedingung aus, was tatsaechlich benutzt wurde: modellseitig Core-CAD-Tools vs. Interaction-Tools vs. Fallback-Code, probandenseitig Slider (`parameter_changes.source='user'`), Markierungen/Sketches/Referenzbilder (Message-Bloecke), Varianten, Prompt-Anzahl. Nach jeder Sitzung laufbar (Pilot-Fruehwarnung gegen „Interaction-Tools werden gar nicht genutzt"); ASCII-Konsolen-Ausgabe + optional Markdown (`--md`). Stdlib-only, kein Eingriff ins Artefakt. Hinweis: Report-Klassifikation muss zusammen mit der Core-CAD/Interaction-Registry angepasst werden.

**Weitere Logging-Felder (29.06.2026):** `messages.modality` unterscheidet jetzt `point_pick` und `component_pick` getrennt (vorher wurden beide auf `'pick'` gemappt) — die Input-Kanaele Punkt- vs. Kante/Flaeche-Pick sind in der FF2-Auswertung trennbar. Lauf-Fehler (API/Agent) werden zusaetzlich zum WS-Broadcast als `agent_error`-`StudyContextEvent` persistiert (sichtbar im Export, auch wenn der Socket gerade keinen Client erreicht). Alle Fehlertexte laufen vorher durch `errors._sanitize_secrets`, das API-Key-/`sk-…`-Muster maskiert — so kann kein Schluessel ueber Log/WS/Studien-Event ins Export-Bundle sickern.

**Event-Literal-Luecke geschlossen (06.07.2026, Commit `97a1fd2`):** Die Event-Typen `tool_surface` und `max_iterations_reached` fehlten im `StudyContextEventType`-Literal in `schemas.py` → diese Events wurden seit 01.07. still verworfen (der defensive `except` verschluckte den Pydantic-Fehler), der Condition-Audit-Trail fehlte in allen Bundles. Behoben; der `except` wurde von stillem Verwerfen auf `warning` gehoben und mit einem Quelltext-Scan-Regressionstest (`backend/tests/test_event_type_coverage.py`) abgesichert, der alle realen Emit-Sites gegen das Literal prueft.

### 7.3 Consent-Modal

Beim ersten Start einer Studien-Session erscheint ein `ConsentModal` mit Text aus `consent_text.md`. Vier Checkboxen, alle vier zustimmen-Pflicht. Das Backend speichert die Antwort in `study_sessions.consent_*`-Spalten.

### 7.4 Lean-Fragebogen (seit 10.06.2026 vollstaendig)

Das in-App-Erhebungsinstrument implementiert die Lean-Variante aus `Dokumente/studie/fragebogen-spec.md` (v1.2.7) komplett — vorher existierte nur ein 4-Item-Agency-Stub. Drei Bloecke, alle deskriptiv flankierend zur Reflexive TA (siehe `thesis/kapitel4-methodik-und-forschungsdesign.md`):

- **Demografie D1–D11** (`DemographicsDialog`): einmal pro Teilnehmenden-Lane (participant_code + pilot-Flag), oeffnet automatisch nach dem Consent des ersten Laufs. Alle Angaben freiwillig. Tabelle `participant_demographics`.
- **Per-Bedingungs-Block** (`AgencySurveyDialog`, Wizard): Mini-CSI (6) + Agency (4, Wortlaut aus `agency_items.json`) + Werkzeug-Items mit „habe ich nicht genutzt“-Option + W-OPEN1 (Multi-Select + Freitext; in basis nur Freitext mit FF1-Fokus-Wording) + offene Reflexion R1. Trans-Format nach Spec §2.4: ein Likert-Item pro Bildschirm, Anweisungs-Karte vor jedem Block, Fortschrittsleiste. Tabelle `agency_survey` (historischer Name, traegt den ganzen Block).
- **Vergleichsblock + Schlussfragen** (`FinalSurveyDialog`): nach dem Per-Bedingungs-Block des zweiten Laufs — V1 Praeferenz + Begruendung, V2 sechs −2…+2-Dimensions-Vergleiche, V3 Top-3-Werkzeug-Ranking (Klick-Reihenfolge), V4 Hybrid-Frage, S1–S3. Tabelle `final_survey`. Ende + Export der Studien-Session passieren erst nach diesem Block.

**Gating (Spec §4.3)** rechnet das Backend in `GET /api/study/sessions/{id}/survey-config`: Bedingung (basis sieht nur W1–W3), `image_uploaded` aus `messages.modality`, `slider_used` aus `parameter_changes.source='user'`, Varianten gegen `build_tool_list('werkzeug')`, Sketch ueber Flag in `fragebogen_items.json` (das Lock-Item entfiel am 11.06.2026, das Measure-Item am 15.06.2026 — redundant: KI misst selbst, Punkt-Pick lokalisiert; keine eigene Auswertungsspur). Nicht verfuegbare Items werden ausgeblendet, nicht ausgegraut. Das Frontend kennt keine Gating-Logik.

**Versionierung (Spec §10.3/§7.5):** `fragebogen.py` hasht den kompletten Wortlaut beider JSON-Dateien (`fragebogen_items.json` + `agency_items.json`, SHA-256, 16 Hex). Jede Antwort-Zeile und das Export-Manifest tragen den Hash — Wortlaut-Aenderungen zwischen Pilot und Hauptlauf bleiben nachvollziehbar. Submission-Marker landen als `survey_submitted`/`demographics_submitted`/`final_survey_submitted` in `events` (Spec §2.4).

### 7.5 Export

`export.py` erzeugt einen vollstaendigen Debug-Dump pro Session: Chat-Transcript, alle Tool-Calls mit Args/Results, parameter_changes-Summary, alle Raw-Tabellen, Snapshots als referenzierte Files. Format ist Markdown + JSON. Wird im Live-Test als Bug-Findungs-Pfad benutzt — der Designer postet den Dump nach jeder Test-Session direkt in den Chat.

**Lauf-Modell reset-sicher (06.07.2026, Commits `984e16a`/`68a7310`):** Das Bundle enthaelt eine `model.3dm` der sichtbaren Lauf-Geometrie. Diese wird jetzt schon beim **Stop-Button (vor dem Fragebogen)** ueber `POST /api/study/sessions/{id}/save-run-model` nach `rhaino/run_models/{study_session_id}.3dm` gesichert; `export_study_session` bevorzugt diese Vorab-Datei gegenueber der zum Export-Zeitpunkt evtl. schon geleerten Live-Szene und loescht sie nicht (Mehrfach-Export moeglich). Der Wurzelbug davor: die Erfassung parste `run_code`-Rueckgaben mit direktem `json.loads`, das an der repr-Huelle still scheiterte („leere Szene") → `model.3dm` fehlte in ALLEN Bundles; zusaetzlich laeuft die Objekterfassung jetzt ueber direkte `sc.doc.Objects`-Iteration statt `rs.AllObjects()` (letzteres lieferte im UI-Thread leer trotz sichtbarer Geometrie, Commit `c8900fb`).

---

## 8. System-Prompt-Design

`SYSTEM_PROMPT` in `agent.py` ist die wichtigste Verhaltensschicht. Schluessel-Entscheidungen:

**Stil-Regeln**, am Anfang gesetzt:
- Antworten extrem knapp, auf Deutsch, hoechstens 1-2 Saetze.
- KEINE Schritt-fuer-Schritt-Erzaehlung, KEIN „Ich baue jetzt...", KEINE Ankuendigung vor Tool-Aufrufen.
- Nach getaner Arbeit ein einziger knapper Satz, kein Detail-Bericht (Designer sieht das Ergebnis im Viewport).
- Iterationen klein und zeigbar halten.

**Tool-Prioritaeten**, in der Mitte:
- Dialog-Rueckfragen statt freier Textfragen wenn ein Standard-Muster passt (confirmation/parameter/choice/reference_pick).
- `request_reference_pick` NUR als Last Resort: nicht bei „alle oberen Kanten" (da `round_edges_by_rule`), nicht bei „die rechte Flaeche" (eindeutig), nicht wenn schon ein Pick angehaengt ist.
- Dediziertes Tool vor `execute_rhino_code`. Dediziert ist getestet, hat klare Schemas, der Trace ist lesbar.
- Bei primitive_*-Flaechen: `resize_*_face`/`create_hole`/`create_slot`. Keine generischen Brep-Edits.
- Bei Box-/Brep-Kanten mit sprachlichen Regeln („alle oberen", „alle vertikalen"): `round_edges_by_rule`. Bei einzelnen gepickten Kanten: `fillet_brep_edge`/`chamfer_brep_edge`.

**Edge-Move-Klarstellung** (28.05.):
- Einzelkanten verschieben geht im Plugin NICHT (zerstoert Topologie). `move_brep_face_*` ist nicht der Workaround. SubD-Conversion ist nicht der Reflex (`quad_remesh_to_subd` verliert Topologie, frisches `create_subd_box` ueberschreibt Features).
- Designer auf Rhinos Sub-Object-Selection verweisen: Strg+Shift+Klick auf die Kante, dann Gumball.

**Mehr-Picks pro User-Nachricht:**
- Wenn der Designer in einer Nachricht zwei Edges picked, sind das EXAKT die Ziele, keine benachbarten dazu raten.
- Mehrere Edges in EINEM `fillet_brep_edge`/`chamfer_brep_edge`-Call mit `edge_indices=[...]`, damit die Original-Indizes vor der ersten Topologieaenderung stabil bleiben.

**Composite-Bild-Klarstellung:**
- Wenn ein 2x2-Raster mit Labels angehaengt wird (ueber das Sketch-Werkzeug als Multi-View-Kontext oder vom Modell-Tool `capture_viewport`), sind das vier Ansichten derselben Szene. Keine vier verschiedenen Szenen.

**Capability-Router-Addendum** (per Turn ergaenzt) sagt explizit:
- Welche Strukturkontext-Aktion aktiv ist
- Welche Strukturparameter slider-bindbar sind
- Welche Tools priorisiert werden sollen
- Wann `expose_parameters` NICHT aufgerufen werden darf

---

## 9. Sicherheits- und Reversibilitaets-Schicht

### 9.1 Archive-Layer und `archive_object`

Jede destruktive Operation (alles was `sc.doc.Objects.Replace` oder `rs.Command`-basierte Modifikationen macht) ruft vor dem Replace `archive_object(guid)` auf. Das kopiert das Objekt auf einen versteckten `Archive`-Layer mit user-text `archive_source_id`/`archive_version`/`archive_timestamp`/`archive_source_name`.

Warum nicht Rhino-Undo? Weil `Replace` und `rs.Command` Rhinos Undo umgehen — entdeckt am 11.03.2026, als ein `subd_crease_edges`-Aufruf die Stuhlbeine zerstoerte und kein Strg+Z mehr half. Der Designer musste File→Revert ausfuehren und verlor alles dazwischen.

Schlaegt das Backup selbst fehl (z. B. `rs.CopyObject` unter Speicherdruck), wird das seit 29.06.2026 **nicht mehr lautlos verschluckt**: der Fehlschlag wird in der laufenden Action vermerkt (`backup_failures`) und als Warnung in die `run_code`-Ausgabe geschrieben, damit ein unvollstaendiges Undo-Backup erkennbar ist statt unbemerkt. Der `Archive`-Layer ist bewusst **nicht gesperrt** (das Plugin beschreibt ihn bei jeder destruktiven Operation + bei Redo-Snapshots staendig) — Schutz vor versehentlichem Loeschen laeuft daher per Studienaufsicht (Daueranker in `Dokumente/studie/ablaufprotokoll.md`), nicht per Layer-Lock.

### 9.2 `undo_last_action` / `redo_last_action`

Smart-Undo:
1. Prueft ob die Szene mit der zuletzt geloggten Plugin-Action konsistent ist (Fingerprint).
2. Wenn ja: macht den Plugin-Backup-Undo (verlorenes Objekt aus Archive restaurieren + neu erzeugte Objekte loeschen).
3. Wenn nein: Rhino-Native-Undo (Designer hatte zwischenzeitlich was direkt in Rhino gemacht).
4. Logt den Mode-Auswahl-Grund ins Result-JSON.

Redo macht das Spiegelbild.

### 9.3 `restore_object`

Stellt ein Backup vom Archive-Layer wieder her. Matched gegen `archive_source_name` (z.B. „Box" → neuestes Backup dieses Objekts) UND `display_name` (z.B. „Box_v2" → genau diese Version). Das aktuelle Live-Objekt wird vor dem Restore selbst archiviert, sodass nichts verloren geht.

**Zentraler Unwrap von `run_code`-Rueckgaben (06.07.2026, Commit `c3c0b89`):** `rhino_exec.run_code` transportiert das `result` eines Tools als `repr(result)` (`_stringify`). War dieses `result` selbst ein `json.dumps(...)`-String, scheiterte ein naives `json.loads` am Single-Quote-Repr-Mantel — die wiederkehrende Bug-Klasse hinter den `model.3dm`- und `model_states`-Parsefehlern. Statt pro Callsite inline wird jetzt der kanonische Helper `rhino_exec.decode_run_code_result` verwendet (`ast.literal_eval` → `json.loads`, invers zu `_stringify`); `structure_registry` delegiert daran, und die restlichen offenen Callsites (`loop._maybe_short_circuit_after_tools`, `server.rhino_clear_viewport`, `server.lock_from_rhino_selection`) sind umgestellt. Golden-Hash-neutral.

### 9.4 Lock-Panel — **am 11.06.2026 aus dem Studienartefakt entfernt**

> Das Lock-Feature (Objektsperren gegen KI-Eingriff) wurde am 11.06.2026 aus der Tool-Surface und der UI genommen (Scope-Cut vor dem Pilot). Die Backend-Persistenz (`locked_objects`-Tabelle, REST-Endpoints) bleibt dormant.

Historisch: `lock_object` schrieb die GUID in die `locked_objects`-Tabelle. Im naechsten Turn bekam das Modell im System-Prompt-Addendum die Liste der gesperrten GUIDs mit dem Hinweis, sie nicht anzufassen. Versuchte das Modell trotzdem, eine gesperrte ID zu modifizieren, schlug der Router das ab.

### 9.5 Selektives Vorschau-Gate (Part C, 18.06.2026)

Ruft das Modell in der **`werkzeug`-Bedingung** ein destruktives Tool auf — betroffen sind `boolean_union`, `boolean_difference`, `boolean_intersection`, `boolean_split`, SubD-Edits (`subd_crease_edges`, `subd_set_vertex_position`, `subd_extrude_faces`, `subd_offset_faces`, `subd_subdivide`) und `delete_object`, zusammen zehn Tools — hält der Tool-Dispatch **vor der Mutation** an: die betroffene Input-Geometrie wird im Viewport **non-mutating** hervorgehoben (wiederverwendeter `DisplayConduit` aus `viewport_bridge/highlight.py`), und eine **Übernehmen/Verwerfen-Karte** erscheint im Chat. Der Lauf wartet in einer echten In-Turn-Pause (`asyncio.Future`, Design A). **Übernehmen** → echter Tool-Call; **Verwerfen oder Timeout (600 s Auto-Skip)** → kein Call, ein Skip-Result geht ans Modell mit dem Hinweis, dass nachfolgende Schritte auf der ursprünglichen Geometrie laufen. Zusätzlich erscheint während eines laufenden Runs ein **Stop-Button**, der den Lauf abbricht (`chat.cancel`). Der verworfene Skip wird im `tool_calls`-Log als `is_gate_skip=true` markiert, **ohne** `model_states`-Snapshot (keine Mutation stattgefunden) — auswertbar für FF2 / Kontrolle.

**werkzeug-only, hash-neutral** (reine WS-/Backend-/Frontend-Schicht; `validate_hashes` grün). Das MVP zeigt die **Input-Geometrie** (kein echter Ergebnis-Geist).

**Eine Entscheidung pro Operation (19.06.2026):** Eine User-Instruktion ist EIN Vorgang, der intern oft mehrfach löscht/ändert (z.B. „Durchmesser ändern" = altes Objekt löschen → neu bauen → Hilfsgeometrie löschen). Das Gate fragt daher pro Run nur **einmal**: der erste destruktive Schritt zeigt die Karte, **Übernehmen** lässt die restlichen destruktiven Schritte desselben Runs ohne erneute Karte laufen, **Verwerfen** überspringt sie (= die früher aufgeschobene „Auto-Skip Rest-Tools im Turn"-Semantik plus ihr Accept-Pendant). Run-gekeyt über `correlation_id` (zwei theoretisch überlappende Runs derselben Session stören sich nicht; in `gate.py._run_decisions`, Reset bei Run-Start/-Ende in `loop.run_agent`). Ein 600 s-**Timeout** skippt nur den einen Schritt und wird **nicht** run-weit gecacht (Nicht-Entscheid). **Auswertungs-Hinweis:** bei mehreren Schritten erbt der Rest des Runs die eine Entscheidung → `is_gate_skip` ist pro-Run-Entscheidung zu lesen, nicht als Zahl aktiver Klicks; eine explizite interactive-vs-cache-Provenance pro Zeile ist bewusst (noch) nicht geloggt.

**Enter-Bestätigung + ehrliche Highlight-Karte (19.06.2026, Live-Test):** Zwei Befunde behoben. (1) **Enter bestätigte die Gate-Karte nie** — die Karte hatte zwar einen window-capture-Keydown-Listener, aber das in Rhino eingebettete Webview liefert ein blankes Enter ohne fokussiertes Element oft nicht ans `document` (der Listener feuert dann nie). Fix: der **Übernehmen-Button bekommt beim Erscheinen Auto-Fokus** (Ref + `requestAnimationFrame`), sodass Enter ihn nativ auslöst UND das Webview ein konkretes Tastatur-Ziel hat; der window-Listener bleibt als Fallback (early-return bei Button-Target → kein Doppel-Resolve; `resolve`+`clearPendingGate` sind idempotent). Grenze: `.focus()` kann den OS-Fokus nicht erzwingen, wenn er beim Rhino-Viewport liegt — dann bleibt Klicken der Weg. (2) **„Geometrie ist im Viewport hervorgehoben" wurde behauptet, obwohl nichts markiert war** — zwei Ursachen: der Hinweistext stand *unbedingt* da (jetzt nur noch bei `object_ids.length > 0`), und `_stage_object_wires` hatte keinen Zweig für **Konstruktionskurven/Punkte** (häufig beim Rebuild-Delete) sowie für sonstige Typen (Annotation/Text/Hatch/Bemaßung/Licht/Punktwolke). Fix in `highlight.py`: Curve→`DuplicateCurve`, Point→`Location`, plus ein **universeller Bounding-Box-Fallback** (`_stage_bbox`: 12-Kanten-Drahtgitter, bzw. Center-Punkt bei degenerierter Box) für jeden anderen existierenden Objekttyp. Damit ist eine existierende Ziel-Geometrie (Normalfall vor dem Löschen) **immer** sichtbar markiert → der Kartentext ist ehrlich. Frontend + Viewport-Schicht, hash-neutral.

**Nur die betroffene Komponente leuchtet (19.06.2026):** Das Gate-Highlight markierte vorher das GANZE Objekt (das Gate kannte nur die Objekt-GUID). Jetzt hebt es bei komponenten-gezielten Operationen NUR die betroffene Stelle hervor — exakt der Pfad des Chip-Hover-Highlights: Fillet/Chamfer → nur die Zielkante(n) (`edge_index`/`edge_indices`), SubD-Crease → die Kante(n), SubD-Vertex-Move → der Vertex, SubD-Extrude/Offset-Faces → die Fläche(n), Bohrung/Langloch → ein Punkt-Marker am Zentrum. **`round_edges_by_rule` (regelbasiert, kein expliziter Index)** gibt einen `edge_rule`-Block weiter; `highlight._stage_edge_rule` löst die Regel (top/bottom/vertical/front/… inkl. deutscher Aliase) mit der **wortgleich gespiegelten** Auswahl-Logik aus `code_templates_brep_edit.round_edges_by_rule_code` auf (gleicher bbox/tol/Klassifikations-Pfad) und hebt damit **exakt dieselben Kanten** hervor, die die Operation rundet — nicht das ganze Objekt. Echte Ganz-Objekt-Operationen (`delete_object`, Boolean-Ops, `subd_subdivide`) heben weiterhin das ganze Objekt hervor (dort korrekt). Realisiert über `gate.highlight_targets(name, tool_input)` (rein aus dem — via Pick augmentierten — Tool-Input abgeleitete Reference-Bloecke) + `highlight.highlight_references(blocks)` (staget mehrere Bloecke in EIN Conduit-Overlay). Fehlt ein Komponenten-Index, Fallback auf das ganze Objekt. Backend-only, hash-neutral.

**Modell-Umweg-Regel + Gate-Provenance-Logging (19.06.2026):** (a) **System-Prompt**: eine Regel ergänzt — einen fehlgeschlagenen geometrischen Pfad nicht stur mit anderen Indizes wiederholen; bei Topologie-Fehlern (z.B. „interior kink" beim Fillet einer geschlossenen Extrusion/Revolve ohne trennbare Ober-/Unterkanten) erst `get_brep_component_info` prüfen, dann `round_edges_by_rule` bzw. Neuaufbau per `loft_curves` (behebt das Herumirren aus der Vasen/Tisch-Session; `system_prompt` neu verankert, Tools unverändert). (b) **Studien-Log**: additives Feld `is_gate_autoresolved` auf den `tool_calls`-Zeilen unterscheidet die EINE aktive Gate-Entscheidung von den im selben Run geerbten (auto-aufgelösten) — `is_gate_skip` ist damit korrekt pro-Operation interpretierbar. Provenance in `gate_registry` (session-gekeyt, am Run-Ende gefegt — auch bei Cancel), serialisiert als `_is_gate_autoresolved` in `args_json`. Hash-neutral.

**Fillet/Fase/Loch/Nut gaten nicht mehr (01.07.2026):** `create_hole`, `create_slot`, `fillet_brep_edge`, `chamfer_brep_edge` und `round_edges_by_rule` (= `_STRUCTURE_BREAKING_TOOLS`) wurden aus der Gate-Menge genommen — der Pre-Confirm war dort mehr Reibung als Schutz, Archive-Backup und Revert decken das Rueckgaengigmachen ab. Bewusste Studien-Entscheidung (Nutzerwunsch). SubD-Edits bleiben als eigene Kategorie gegated. Massgeblich ist `_GATED_DESTRUCTIVE_TOOLS` in `backend/agent/gate.py`.

Bewusst gestaffelt offen: echter Ergebnis-Geist (Dry-Run pro Tool), Timeout-Countdown/-Konfigurierbarkeit, Composer-Sperre während offenem Gate. (Die explizite Gate-Provenance interactive vs. cache-aufgelöst im `tool_calls`-Log ist seit 19.06.2026 als `is_gate_autoresolved` umgesetzt.) Vollständiger Plan: `dev-notes/2026-06-18-part-c-vorschau-gate-plan.md`.

Dateien: `backend/agent/gate.py` (neu), `backend/agent/dispatch.py`, `backend/agent/loop.py`, `backend/server.py` (Gate-Resolve + Cancel-Handler + Run-Task-Registry), `backend/schemas.py`, `backend/session_store.py`; Frontend: `web/src/lib/types.ts`, `store/chatStore.ts` (`pendingGate`/`runActive`), `hooks/useWebSocket.ts`, `features/chat/GatePreviewCard.tsx` (neu), `features/chat/ChatView.tsx`, `features/chat/InputBar.tsx` (Stop-Button).

### 9.6 Variants

`create_variant` legt eine Kopie des aktiven Objekts auf einen separaten `Variant_*`-Layer. Mehrere Varianten existieren parallel und sind sichtbar im `VariantGallery`-Panel mit Thumbnails. `select_variant` aktiviert eine (versteckt die anderen). `finish_variants` commit-t die aktive und loescht den Rest. Das Thumbnail wird per `capture_viewport` mit `fit=extents` automatisch erzeugt.

### 9.7 Neue Sitzung: Viewport ausblenden (nicht loeschen)

Beim Klick auf „Neue Sitzung" (`+`) erscheint im Normal-/Dev-Modus der `NewSessionDialog` mit drei Optionen: **Ausblenden & neu starten**, **Nur neue Sitzung**, **Abbrechen**. Ein bewusster Bestaetigungsschritt — so kann ein Fehlklick auf `+` das sichtbare Modell nicht wegraeumen.

Bei „Ausblenden & neu starten" ruft das Frontend `POST /api/rhino/clear-viewport`. Der Endpoint (`run_code` off-event-loop) verschiebt **jedes aktuell sichtbare Objekt** auf einen frisch angelegten, versteckten, grauen `Sitzung <Zeitstempel>`-Layer. Nichts wird geloescht: der Layer ist im Rhino-Layer-Panel jederzeit wieder einblendbar — das ist der **explizite Recovery-Pfad**. Bewusst **nicht** mehr per Undo/Strg+Z zurueckholbar: seit dem Pilot-Fix vom 01.07.2026 versiegelt der Endpoint nach dem Move die Plugin-Action-History (sticky) und Rhinos nativen Undo-Stack (`ClearUndoRecords`), damit ein einzelnes Undo in der neuen Sitzung nicht den Layer-Move rueckgaengig macht und die Geometrie der Vorsession zurueckholt (Cross-Session-Leak, Pilot Bug A). Objekte, die bereits versteckt sind oder auf einem versteckten Layer liegen (z.B. die `Archive`-Backups oder andere Sessions' versteckte Layer), bleiben unangetastet. Danach ist `Active` leer + aktuell und ein etwaiger `__active_variant_layer__`-Zeiger ist geloescht, sodass die erste Geometrie der neuen Sitzung sauber auf `Active` landet. Der Sitzungs-Layer wird **lazy** erst beim ersten zu verschiebenden Objekt erzeugt — ein bereits leerer Viewport hinterlaesst keine Karteileichen.

Im Studienmodus ist diese Affordanz bewusst inaktiv: dort routet `+` weiterhin durch den Pre-Session-Dialog (Studienartefakt-Spec §2.1), und ob/wie zwischen Conditions ein Geometrie-Schnitt passiert, gehoert in den Studienablauf, nicht in einen Dev-Confirm.

### 9.8 Reversibilitaets-Pfade im Ueberblick (+ Parametrisierung verwerfen)

Reversibilitaet laeuft bewusst ueber **mehrere** Pfade, je nach Art der Aktion — wichtig fuers Mentalmodell und die Studienauswertung:

- **Header-Undo/Redo** (`undo_last_action`/`redo_last_action`): committete Geometrie-Aenderungen — CAD-Tools, **Slider-Drags inkl. Struktur-/Recipe-Slider (seit 29.06.2026, Box/Zylinder/Extrusion + scale_axis archivieren jetzt vor der Mutation)**, `grasshopper_bake`. Ein Klick = ein Tool-Schritt; restauriert **ausschliesslich ueber die Plugin-Archive-Backups** (`smart_undo_last_action`). Der fruehere Fallback auf Rhinos natives `_Undo` wurde am 01.07.2026 entfernt (Commit `215973f`): ein natives `_Undo` traf nur 1 von ~12–15 Records einer Tool-Action (archive_object-Kopien, UserText, Cutter) und oszillierte — Plugin-Backup ist jetzt die einzige Wahrheit. Konsequenz: der Header-Undo macht **nur Plugin-Aktionen** rueckgaengig; manuelle Rhino-Schritte des Designers gehen weiterhin ueber Rhinos eigenes Strg+Z. Wurde die Szene seit dem letzten Tool-Schritt manuell veraendert, restauriert Undo trotzdem die protokollierte Version und liefert eine explizite Warnung im Ergebnis mit.
- **Parameter-Panel-× **: blendet das Panel aus. Bei **editable/primitiven** Strukturen ein Per-Objekt-Hide (`dismissedStructureIds`), das ein Rhino-Undo zurueckholt; bei **GH-/agent**-Panels ein harter Clear (`clear_grasshopper_context`), der **NICHT** ueber Undo zurueckkommt (technisch begruendet: GH-Strukturen teilen eine session-globale id) — dafuer Verwerfen bzw. ein bewusstes Re-Expose der KI.
- **Verwerfen-Button** (ParameterPanel, aktive GH-Parametrisierung, `POST /api/grasshopper/abort`): das Gegenstueck zum Bake. Baut die KI-GH-Chain ab (`clear_gh_session`) und holt die beim Parametrisieren gepickten **Original-Objekte** deterministisch zurueck (frontend-getrackte `ghSourceObjectIds` → sichtbar machen bzw. via `archive_source_id` aus dem Archive). Da die GH-Geometrie eine Live-Vorschau ist (kein Active-Objekt bis zum Bake), braucht das KEINEN Undo-Eintrag.
- **VariantGallery-Panel**: der Varianten-Lebenszyklus (anlegen/wechseln/loeschen/clearen) ist ueber das Panel selbst + den Archive-Layer reversibel und haengt bewusst **nicht** am Header-Undo-Button.
- **Bake-Undo-Asymmetrie**: ein Undo nach `grasshopper_bake` loescht die gebackenen Objekte, stellt aber die geloeschte GH-Definition **nicht** wieder her (akzeptiert — Re-Parametrisieren ist ein frischer Prompt).

---

## 10. Spezielle Tools im Detail

### 10.1 `create_hole`

Schneidet ein zylindrisches Loch durch ein Brep. Inputs: `object_id`, `center`, `diameter`, `direction` (Default `[0,0,-1]`), `depth` (fuer Sackloecher), `through` (Default `true`).

Implementation:
- Through-Hole: Cylinder mit `length = diag * 3`, zentriert auf `center` — geht garantiert durch jede plausible Box.
- Sackloch: Cylinder von `center - pad * direction` mit `length = depth + pad`. Tiefe wird ab `center` IN das Objekt gemessen.
- Boolean-Difference. Multi-Result-Handling fuer den Fall dass das Brep in mehrere Teile zerfaellt.
- `clear_editable_recipe` nach erfolgreichem Cut.

Auto-Augmentation: wenn der Designer eine Face gepickt und „bohr ein Loch" sagt, fuellt der Router `object_id`, `center` (Pick-Point) und `direction` (negierte Face-Normale) automatisch aus.

### 10.2 `create_slot`

Schneidet eine Nut, Langloch oder Schlitz. Inputs: `object_id`, `center`, `length`, `width`, `slot_axis` (Default `[1,0,0]`), `direction` (Default `[0,0,-1]`), `depth`, `through`, `end_shape` (Default `square`).

`end_shape`:
- **`square` (Moebel-Nut-Default):** Rechteck-Cutter mit voller `length`, KEINE Halbkreis-Caps. Fuer Nuten die quer durch eine Flaeche laufen.
- **`rounded` (Langloch):** Rechteck + zwei Halbkreis-Caps. Fuer klassische Slots (Schraubenschlitz).

Sprachlich: deutsche „Nut" = groove = square. Englisches „slot" = obround = rounded. Default ist square, weil Designer in deutscher Moebelsprache arbeiten.

### 10.3 `round_edges_by_rule`

Filletet mehrere Kanten in EINEM `Brep.CreateFilletEdges`-Call, ausgewaehlt ueber semantische Regel statt einzelner Indizes.

Regeln: `all`, `all_except_bottom`, `top`, `bottom`, `vertical`, `horizontal`, `x_edges`/`y_edges`/`z_edges`, `front`/`back`/`left`/`right`, `top_front`/`top_back`/`top_left`/`top_right`. Deutsche Aliase wie „oben"/„unten"/„vertikal"/„vorne" werden normalisiert.

Kanten-Klassifizierung per Kanten-Mittelpunkt vs. Bounding-Box-Bounds plus Tangentenrichtung. Funktioniert perfekt fuer Box-Primitive, fragiler fuer komplexe Breps mit Stufen.

### 10.4 `set_bbox_dimension`

Setzt eine Welt-Bbox-Achse auf ein Zielmass via `rs.ScaleObject` (non-uniform). Inputs: `object_id`, `axis` (x/width/breite usw.), `target_size`, `anchor` (`center`/`min`/`max`).

Aktualisiert bei `primitive_box` das `editable_recipe`, damit die Box-Slider danach konsistent bleiben.

### 10.5 `resolve_reference`

Read-only heuristischer Resolver. Inputs: `query` (z.B. „die obere vordere Kante"), optional `component_type` (`object`/`face`/`edge`/`auto`), `scope_object_ids`.

Output: gerankte Kandidatenliste mit Scores und Reasons. Direction-Scoring per Wortliste („oben/top/deck", „unten/bottom/boden", etc.) gegen normierte Bbox-Positionen UND Face-/Edge-Normalen.

Wird vom Modell vor jeder potentiell mehrdeutigen Edge/Face-Operation aufgerufen. Wenn der Top-Treffer hohes Konfidenz hat: direkt damit weiterarbeiten. Sonst: `request_reference_pick`.

---

## 11. Frontend-Komponenten

`web/src/features/chat/`:

- **`ChatView.tsx`** — Container, listet Messages.
- **`MessageBubble.tsx`** — Rendert User-/Assistant-/Tool-Messages mit allen Block-Typen (Text, Image, Selection, ComponentPick, PointPick, Sketch, ToolUse, ToolResult, QuickReply).
- **`InputBar.tsx`** — Chat-Eingabe + die fuenf Designer-Werkzeuge (Punkt, Komponente, Snapshot+Sketch, Bildanhang, **Parametrisieren** [`SlidersHorizontal`-Icon, frueher der „GH"-Button]; der separate Kamera-Button wurde am 16.06.2026 ins Sketch-Werkzeug eingeschmolzen). Verwaltet `stagedAttachments` fuer Bild/Sketch, Inline-Referenz-Tokens (inkl. Auswahl- und Befehls-Chips) fuer Punkt-/Komponenten-/Selection-Bloecke und das `viewportPending`-State waehrend Picks laufen. Der „K/F/O"-Komponenten-Pick bleibt ein einziger Button im Auto-Modus. **(26.06.2026)** Das frueher ergaenzte `vertex`-Label wurde aus dem Komponenten-Chip + der MessageBubble wieder entfernt (K/F/O = Kante/Flaeche/Objekt; nur die SubD-/Mesh-Praefixe bleiben). Der **Parametrisieren**-Button feuert nicht mehr direkt, sondern befuellt den Composer vor (Auswahl-Chip + Befehls-Chip „Parametrisieren"; serialisiert beim Senden zum identischen fixen Text) und wirkt auf eine bereits im Feld liegende Objekt-Referenz statt Zwangs-Pick — siehe §4.
- **`QuickReplyCard.tsx`** — Karten fuer alle fuenf Dialog-Tools (`request_confirmation`, `request_parameter`, `request_parameters`, `request_choice`, `request_reference_pick`). **Verwerfen-× (23.06.2026):** die jeweils aktive Karte traegt oben rechts ein × (lucide), das sie auf eine gedaempfte „verworfen"-Spur einklappt (spiegelt den „bereits beantwortet"-Endzustand, kein Layout-Sprung). Speicher: `dismissedDialogIds` im Store, gespiegelt nach dem `dismissedStructureIds`-Muster (session-lokal, Reset in `setActiveSession`, NICHT reload-durabel). Rein optional/kosmetisch: Dialog-Tools sind nicht-blockierend (das `awaiting_user`-tool_result ist bereits gespeichert), das Verwerfen haengt den Run also nie auf und sendet keine Antwort — wer doch antworten will, klickt eine Option oder tippt. **ConfirmationCard mit drei Optionen + Pfeiltasten (25.06.2026):** Bestaetigen / **„Stattdessen…"** (gestrichelter Button -> klappt ein Korrektur-Eingabefeld auf, dessen Text als freie Antwort gesendet wird — Enter sendet, Esc klappt zu) / Abbrechen. ← →/↑ ↓ wechseln die Auswahl (blauer Ring auf der aktiven, Maus-Hover synchron), Enter loest die gewaehlte aus; der window-keydown-Handler faengt Pfeile/Enter auch aus dem leeren contenteditable-Composer ab, nur echte Input/Textarea behalten ihre Cursor-Navigation. Reine freie-Text-Korrektur -> kein Schema-/Tool-Change, hash-neutral.
- **`ParameterPanel.tsx`** — Slider-Strip unter dem Chat-Input. Live-Drag mit throttled Backend-Updates und Trailing-Edge-Timer fuer den letzten Wert. Strukturkontext-Box als Wrapper wenn ein editable Kontext aktiv ist. Composite-Header: nur „Parameter (N)" wenn ausgeklappt, Strukturtitel zusaetzlich wenn eingeklappt. **Zwei Dismiss-Stufen (19.06.2026):** (1) **Chevron = leichtes Einklappen** — setzt `parametersUserDismissed`; `setExposedParameters` unterdrueckt dann das Auto-Aufklappen fuer selektionsgetriebene Expositionen (`source: editable_structure`), eine erneute Auswahl reisst das eingeklappte Panel also nicht wieder auf. (2) **× = hartes per-Objekt-Ausblenden** — fuer per-Objekt-Strukturen (primitive/editable, GUID-basierte id `structure:{session}:{guid}`) wird die Struktur-id in `dismissedStructureIds` aufgenommen; das Panel rendert fuer dieses Objekt `null`, auch bei erneutem Pick. Zurueck kommt es durch: ein **Rhino-Undo/Redo** (Backend `undo_watcher` → `viewport.undo_redo` → `clearDismissedStructures`), einen bewussten Agent-Expose fuer dasselbe Objekt (`source: agent_exposed`/`gh_slider` hebt die Verwerfung auf) oder einen Session-Wechsel. × loescht die Backend-Params NICHT (der Agent nutzt sie weiter; Undo holt das Panel direkt zurueck). **Grasshopper-Strukturen** teilen EINE session-globale id (`structure:{session}:gh`) → fuer GH greift NICHT das per-Objekt-Hide, sondern der leichtere Einklapp-/Clear-Fallback. **Grenze (akzeptiert):** Rhinos Undo-Event nennt das betroffene Objekt nicht → ein Undo hebt die Ausblendung fuer ALLE so geschlossenen Objekte auf; und Undo holt ein ausgeblendetes Panel nur zurueck, solange das Objekt noch der aktive Strukturkontext ist (sonst Re-Pick noetig). Alles session-lokal (Reset in `setActiveSession` + `key={activeSessionId}`-Remount); collapse-on-send bleibt bewusst transient.
- ~~**`InspectPanel.tsx`**~~ — am 30.06.2026 entfernt: das Panel war tote werkzeug-UI (`inspectResult` wurde im Frontend nie gesetzt, `api.inspect` nie aufgerufen → es renderte immer `null`). Entfernt wie das Measure-Werkzeug (15.06.2026): die KI inspiziert autonom; eine nie auslösbare Affordanz würde in der FF1-Auswertung fälschlich als „verfügbar aber ungenutzt" zählen. Der Backend-`inspection_service` + `/api/sessions/{id}/inspect`-Endpoint bleiben ungenutzt/dormant (kein Frontend-Consumer mehr).
- ~~**`LockPanel.tsx`**~~ — am 11.06.2026 gelöscht (Lock-Feature aus dem Studienumfang entfernt).
- **`VariantGallery.tsx`** — Thumbnail-Grid fuer alle Varianten der aktiven Session.
- **`SketchOverlay.tsx`** — Full-Viewport-Overlay fuer Skizzieren ueber einem Rhino-Snapshot. Enthaelt den **Ansichts-Waehler** (Perspektive/Top/Front/Right; Default Perspektive): beim Oeffnen erfasst das Backend alle vier Ansichten einzeln plus ein Multi-View-Composite (`viewport.sketch_ready` mit `{views, composite}`). Striche werden **pro Ansicht** gehalten (`savedStrokes`): der Ansichtswechsel parkt die aktiven Striche und laedt die der Zielansicht (kein Verwerfen mehr; nur ein Backdrop-Refresh setzt alle Striche zurueck). Beim „Fertig" wird pro markierter Ansicht ein eigener `SketchBlock` gestaged (Composite einmal am ersten Block; ohne Striche nur das Composite). Alle Bloecke werden erst lokal gesammelt und dann in einem Rutsch gestaged — ein Fehler beim Rendern einer Ansicht stagt daher nichts halb und ein Retry kann nicht doppelt stagen.
- **`SessionHistoryDialog.tsx`** — Session-Switcher mit Search.
- **`ToolCallCard.tsx`/`ToolCallGroup.tsx`** — Rendering von Tool-Use-/-Result-Bloecken im Verlauf. Konsekutive Steps werden gemerged.
- **`TypingIndicator.tsx`** — „... antwortet" Animation.

Repair-Variante: `QuickReplyCard.tsx` erkennt Reparaturvorschlaege der `request_confirmation`-Karte und rendert sie als orange Reparaturkarte. Die bestehende Enter-Logik bestaetigt nur die aktive Karte; der Abbrechen-Button sendet eine normale, explizite Abbruch-Antwort.

`web/src/features/study/`:

- **`PreSessionDialog.tsx`/`ConsentDialog.tsx`/`RecordingBadge.tsx`** — Studien-Schale (§2.1–§2.3).
- **`AgencySurveyDialog.tsx`** — Per-Bedingungs-Survey-Wizard (siehe 7.4).
- **`FinalSurveyDialog.tsx`** — Vergleichsblock + Schlussfragen (siehe 7.4).
- **`DemographicsDialog.tsx`** — Demografie-Block D1–D11 (siehe 7.4).
- **`survey-ui.tsx`** — geteilte Wizard-Bausteine (LikertScreen, Fortschrittsleiste, Freitext mit Zeichenzaehler, Auswahl-Pills).

Store: `zustand` (`store/chatStore.ts`). Single source of truth fuer Sessions, Messages, gestaged Attachments, exposed Parameters, Viewport-Pending. (Der frühere `lockedObjects`-State wurde am 11.06.2026 mit dem Lock-Feature entfernt.)

WebSocket: `hooks/useWebSocket.ts`. Reconnect-Logic, Event-Dispatch in den Store.

**Design-System (10.06.2026):** Claude-UI-Look, nur Light Mode. Ganz heller, flacher, fast neutraler Grau-Canvas (`--background: 220 16% 96%`); weisse Flaechen werden ueber einen **1px-Hairline** vom Canvas getrennt — `shadow-card` ist als `0 0 0 1px ...` (+ Hauch Schatten) definiert, wirkt also wie ein duenner Rahmen, nicht wie ein schwebender Card-Schatten. `shadow-pop` (Dialoge/Toasts) hat echte Tiefe. Tokens in `web/src/index.css` + `tailwind.config.js`. Akzentfarben: Primary Royal-Blue, `--brand-orange`/`--success` mit `-soft`-Flaechen und `-deep`-Textvarianten (WCAG AA). Fonts: Inter (Body) + Plus Jakarta Sans (Headlines, `font-display`), Google Fonts mit lokaler Fallback-Kette (offline benutzbar). Konventionen: Inhalte in weissen Flaechen (`bg-card rounded-2xl shadow-card`), Grau nur sparsam und am tiefsten Verschachtelungspunkt (Code-/Datenbloecke) — NICHT grau-in-weiss-in-grau schichten; Chips/Badges als `rounded-full`-Pills, Icons nur lucide, alles Klickbare mit `cursor-pointer` + Hover-Transition, Fokus-Ringe `ring-ring/40`.

**Motion- und Render-Konventionen (03.07.2026, UI-Polish-Sprint — hash-neutral):** Bewegung ausschliesslich ueber CSS/tailwindcss-animate (framer-motion entfernt, war ungenutzt). Entrances: `animate-in fade-in-0` (+ `slide-in-from-bottom-1` bei Karten/Bannern), 200-300 ms. Auf-/Zuklappen als grid-rows-Accordion (`[grid-template-rows:0fr]`/`[1fr]` + Kind `overflow-hidden min-h-0`): Inhalt bleibt gemountet, traegt im kollabierten Zustand aber `inert` (keine unsichtbaren Tab-Stops/Klickziele). Exit-Animationen nur wo noetig (AttachmentStrip-Chips, TypingIndicator) via kurzem delayed-unmount (140-160 ms) MIT Unmount-Cleanup des Timers. Pending-Zustaende pulsieren (`animate-pending-pulse`) statt hart die Opacity zu wechseln. Jede Animation traegt einen `motion-reduce:`-Guard. Render-Konvention: die Chat-History laeuft ueber ein `messageNodes`-useMemo (Element-Reuse) plus `React.memo` mit Inhalts-Compare auf `MessageBubble`/`ToolCallGroup`, Slider-Zeilen sind memoisiert (`MemoParameterSlider` + stabiler Change-Handler) — neue Props in diesen Pfaden muessen referenzstabil sein, sonst bricht die Memoisierung.

---

## 12. Wichtige Designentscheidungen — warum so?

### 12.1 Dediziertes Tool > freier Code

Wir haetten 3 Tools haben koennen (`execute_code` + `capture` + `scene_info`). Stattdessen haben wir 101 dedizierte Tools (Stand 08.07.2026, gezaehlt in `backend/tool_schemas.py`; insgesamt 95 core_cad + 13 interaction = 108 Tools in der Werkzeug-Bedingung, gepinnt in `test_tool_registry`). Warum:

- **Reproduzierbarkeit.** Dedizierte Tools sind getestete Code-Pfade mit klaren Schemas. Wenn das Modell `create_box(width=120,...)` aufruft, weiss man genau was passiert. Bei `execute_code` muss man den Code lesen.
- **Tool-Trace-Lesbarkeit.** In der qualitativen Auswertung der Studie ist eine Liste von Tool-Calls (`create_box`, `move_object`, `create_hole`) viel auswertbarer als 50 Zeilen Code.
- **Auto-Snapshot-Logik.** `is_modifying_tool` koennte bei `execute_code` nicht zwischen read-only und modifying unterscheiden — wir behandeln daher jeden Code-Call als modifying (sichere Default).
- **Studienartefakt-Spec §1.2:** alle dedizierten Tools landen in `tool_registry.py`, kein scattered `if condition == ...`.

Trade-off: Tool-Surface ist gross. Aber: das Modell sieht eh die ganze Liste, das ist im Cache und Tokens sind dafuer nicht der Bottleneck.

### 12.2 Dialog-Tools statt freier Frage-Text

Wenn das Modell „Welchen Durchmesser?" als freie Frage stellt, muss der Designer tippen. Mit `request_parameter` klickt er einen der vier vorgeschlagenen Chips. Drei Klicks weniger pro Standardfall.

Wir haben die Frage NICHT in den User-Block mit auto-confirm gepackt, weil das den Designer sprachlich entmuendigen wuerde. Stattdessen: Karte + Quick-Reply, der Designer kann immer noch tippen wenn er was anderes will.

### 12.3 Sicherheits-Default: square Nut, Multi-View-Capture, Recipe-Clear

Drei Defaults wurden am 28.05. nach Live-Test bewusst geaendert:

- `create_slot` → `end_shape="square"`: deutsche Sprachsemantik.
- `capture_viewport` → 2x2-Komposit + ZoomExtents + scene_info-Caption: ohne ZoomExtents war das Objekt oft nicht im Bild, ohne scene_info musste die KI nachfragen, ohne Multi-View hatte sie keine orthographische Disambiguierung.
- Recipe-Clear nach Topologie-Bruch: ohne den Clear hat ein versehentlicher Slider-Drag die ganze Modellierungssession zerstoert.

Designprinzip: der gefaehrliche Pfad ist nie der Default. Wenn ein Designer eine obround-Nut will, kann sie das explizit anfordern.

### 12.4 Coalescing statt Throttling

Der Slider haette einfach im Frontend bei 100ms throttled werden koennen. Stattdessen: pro `(session, parameter)` ein In-Flight-Lock und ein Latest-Pending. Warum:

- Throttling im Frontend droppt Werte BEVOR sie geprueft werden. Wenn das Backend manchmal lang braucht, sieht es trotzdem alle Werte hintereinander.
- Coalescing droppt Werte NACH-DEM ein laufender Call beendet ist. Das Modell sieht weniger Updates, hat aber garantiert den letzten verarbeitet.

Effekt: Frontend kann mit 30-60 Hz schicken, Backend verarbeitet effektiv 2-4 Updates/Sekunde, aber der finale Wert ist immer korrekt.

### 12.5 ViewportInfo statt PushViewProjection

Beim Multi-View-Capture wird die Designer-Kamera mehrmals temporaer umgestellt. Erster Ansatz: `viewport.PushViewProjection()` / `PopViewProjection()`. Resultat: Viewports waren nach Capture subtil anders.

Zweiter Ansatz: Camera/Frustum/Lens einzeln speichern und zurueckschreiben. Resultat: parallele Projektionen (Top/Front/Right) blieben tight gezoomt, weil `RhinoViewport.SetFrustum` schlicht nicht existiert.

Dritter Ansatz (aktuell): `Rhino.DocObjects.ViewportInfo(viewport)` als kompletter Snapshot, `viewport.SetViewProjection(info, True)` als Restore. Roundtrip ist atomar und sauber.

Lesson learned ist als Claude-Memory-Reference festgehalten (`memory/reference_rhino_viewport_state_restore.md` im globalen `~/.claude/projects/`-Verzeichnis — liegt ausserhalb des Repos und ist hier deshalb nicht als Link erreichbar).

### 12.6 Capability-Router statt One-Shot-Tool-Liste

Naiv: dem Modell jede Runde die volle Tool-Liste geben und es entscheiden lassen. Problem: das Modell wechselt zwischen Tools, vergisst dass es schon einen aktiven Strukturkontext hat, ruft `expose_parameters` mehrfach.

Mit Router: pro Turn wird ein kurzer System-Prompt-Block ergaenzt, der dem Modell sagt „du hast einen aktiven `primitive_box`-Kontext mit Strukturparametern Breite/Tiefe/Hoehe — nutze die zuerst". Das reduziert sprunghafte Tool-Wahl deutlich.

### 12.7 Werkzeug- vs. Basis-Bedingung: gleiche CAD-Kompetenz, andere Interaktionsschicht

Wir haetten zwei verschiedene System-Prompts haben koennen. Stattdessen: ein Prompt, gleiche Core-CAD-Faehigkeit und unterschiedliche Interaction-Tools. Warum:

- Konditions-Unterschied ist „welche Interaktionskanaele fuer Designer:innen verfuegbar sind", nicht „wie faehig der Assistent modellieren kann".
- Ein Prompt heisst eine Bewertungsachse fuer die Studie weniger (Modell-Antworten haben dieselbe Stil-Vorgabe).
- Wenn ein Interaction-Tool in `basis` nicht verfuegbar ist, muss die Absicht ueber Chat/Bild vermittelt werden; Core-CAD-Tools bleiben aber verfuegbar, damit die Basis nicht kuenstlich schwach wird.

### 12.8 Tool-Schicht = Affordanzen, keine Faehigkeits-Caps

Leitprinzip der Werkzeugschicht: Die Tools sind Interaktions-**Affordanzen fuer Designer:innen**, keine **Obergrenze fuer die Modellfaehigkeit**. Die CAD-Kompetenz soll modellbegrenzt sein, nicht toolbegrenzt — sonst haengt das Ergebnis an Tool-Hand-Holding statt an Modellstaerke und skaliert nicht mit besseren Modellen mit.

Unterscheidung bei jedem Constraint:

- **Faehigkeits-Cap (vermeiden):** beschneidet/erzwingt/blockiert ein Ergebnis, das das Modell sonst frei waehlen koennte. Beispiel (am 24.06.2026 wieder entfernt): `set_bbox_dimension` koppelte die radialen Achsen eines Zylinders zwangsweise auf kreisrund — ein ovaler Querschnitt war damit unmoeglich.
- **Footgun-Guard (behalten):** haelt das Modell davon ab, die Instrumentierung/Interaktion selbst zu beschaedigen, aendert aber nicht, *was* gebaut werden kann. Beispiele: "GH nie baken" (Baken friert die Parametrik ein und killt die Slider-Steuerung) und `execute_rhino_code` als "letzter Ausweg" (lenkt auf die geloggten/reversiblen dedizierten Tools). Das Modell weicht bei echtem Bedarf via Freicode aus.

**Uncapping-Garantie:** `execute_rhino_code` ist bewusst von allen Gates/Allowlists ausgenommen — ueber den freien RhinoCommon-Zugriff kann ein faehigeres Modell jederzeit auch bauen, was kein dediziertes Tool abdeckt. Das Modell ist nie vollstaendig eingesperrt.

**Die eine strukturelle Faehigkeits-Kappung** ist die `_allowed_operations`-Allowlist (gepickte Komponente -> serverseitig erlaubte Operationen; `viewport_bridge/pick.py` + `component_router.py`). Sie ist **kein** bewusster Studienpfad der Bedingungslogik (Abschnitt 1.1 der Studienartefakt-Spec = der Tool-Split `INTERACTION_TOOL_NAMES`), sondern ein werkzeug-internes Zuverlaessigkeits-Guardrail (kuratiertes, verlaessliches Pick-zu-Operation-Mapping). Fuer die Studie bewusst behalten; fuer eine allgemeine Version auf "advisory" (nur Hinweis ans Modell) umzubauen.

**Post-Studie-Uncap-Liste** (sobald eine nicht-Studien-Version entsteht): (1) `_allowed_operations` auf advisory; (2) Prompt-Mandate (bake / Freicode-Hierarchie) weicher; (3) read-only Clamps grosszuegiger (z.B. `resolve_reference` bereits am 24.06.2026 von 20 auf 50). Audit + Begruendung: `log.md` (24.06.2026).

---

## 13. Was bewusst nicht im Plugin ist

Aus dem Tool-Surface-Audit der Arbeit (Abgleich des Werkzeugumfangs mit der AI-CAD-Literatur):

- **Sketch-Constraints** (parallel/perpendicular/tangent): Rhino ist kein constraint-basiertes CAD wie SolidWorks/Onshape/Fusion. Skizzen sind flache Kurven.
- **Feature-Tree-Operationen:** Rhino hat keinen Feature-Tree. Die Rolle uebernehmen Layer + Varianten + Strukturkontext.
- **Dimension- und Annotation-Erzeugung:** fuer Moebelentwurf in der RtD-Phase irrelevant.
- **Explizite `check_geometry_validity`-Tools:** Booleans/Sweeps geben ihre Fehler klar zurueck.
- **Constraint-Solver / IK:** kein Use-Case in der Studiendomaene.
