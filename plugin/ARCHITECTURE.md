# ARCHITECTURE.md — Code-Landkarte des Studienartefakt-Plugins

**Zweck:** Diese Datei erklärt den **Code** (wie ist er aufgebaut, was macht welche Datei, wie fließen die Daten). Sie ist die Navigations-Ergänzung zu:
- `FEATURES.md` — *was* das Plugin kann + *warum* so entschieden (Feature-/Design-Sicht)
- `Dokumente/studie/studienartefakt-spec.md` — verbindliche Studien-Spec (nicht Teil dieses Repositorys)

Wenn du „nichts vom Code verstehst", fang hier an: erst §1 (Big Picture), dann §2 (was beim Senden einer Nachricht passiert), dann §7 (wo schaue ich, wenn…).

---

## 1. Big Picture — zwei Prozesse, ein Rhino

```
 Browser (React/Vite)                    Rhino 8 (CPython)
 ┌───────────────────┐   WebSocket /ws   ┌────────────────────────────┐
 │ web/  (Zustand UI)│ ◀───── + REST ───▶│ backend/  (FastAPI/uvicorn)│
 │  Chat, Slider,    │                   │  Agent-Loop, Tool-Dispatch │
 │  Picks, Dialoge   │                   │  → führt Python IM Rhino-   │
 └───────────────────┘                   │    UI-Thread aus            │
                                          └───────────┬────────────────┘
                                                      │ Anthropic API
                                                      ▼
                                              Claude (Sonnet/Opus)
```

- **Backend** (`backend/`) ist ein FastAPI-Server, der **innerhalb des Rhino-Prozesses** in dessen CPython läuft (gestartet über `start_plugin.py`). Er hält den Agent-Loop, ruft die Anthropic-API und führt Geometrie-Code im Rhino-UI-Thread aus.
- **Frontend** (`web/`) ist eine React/Vite-App im Browser-Panel. Reaktiver Zustand kommt über den **WebSocket** (`/ws`); einmalige Aktionen (Session anlegen, Upload, Settings) über **REST**.
- **Shared** (`../shared/`) sind die Rhino-Python-**Code-Templates** (Strings, die an Rhino geschickt werden) — von Plugin **und** MCP-Server gemeinsam genutzt.
- **Zwei Bedingungen** (`basis` / `werkzeug`) entscheiden, welche Interaction-Tools und UI-Affordanzen die Person hat. Seit der Designkorrektur 11.06.2026 (im Code umgesetzt) teilen beide Bedingungen denselben Core-CAD-Toolstack (95 Tools, Stand 02.07.2026); `werkzeug` ergaenzt 13 sichtbare Interaction-Tools (= 108). Einzige Stelle: `tool_registry.build_tool_list(condition)` — seit 02.07.2026 fail-closed (nur exakt `"werkzeug"` oeffnet die Interaction-Surface).

---

## 2. Request-Lebenszyklus — eine Chat-Nachricht, die Geometrie baut

| # | Ort | Funktion |
|---|---|---|
| 1 | Frontend | `InputBar` → `useWebSocket.send({type:"chat.send", …})` |
| 2 | `server.py` | `@app.websocket("/ws")` → `websocket_endpoint()` → `_handle_command()` |
| 3 | `server.py` | speichert Nachricht (`session_store`), startet `asyncio.create_task(agent.run_agent(session_id))` — nicht-blockierend |
| 4 | `agent/` | `run_agent()` → `_run_agent_inner()`: Historie laden, Plugin-Blöcke flachklopfen (`_flatten_*`, `_history_to_api`) |
| 5 | `agent/` | Tool-Liste bauen: `tool_registry.build_tool_list(session.condition)`; System-Prompt + `capability_router.build_capability_router_prompt()` |
| 6 | `agent/` | Anthropic-Call (streaming) → `message.update`-Events via `websocket_manager.broadcast()` |
| 7 | `agent/` | bei `tool_use`: `_execute_tool(name, input, session_id)` |
| 8a | `agent/` | built-in: `_tool_capture_viewport` / `_tool_execute_rhino_code` (→ `rhino_exec.run_code`) |
| 8b | `dedicated_tools/core.py` | dediziert: `dispatch_dedicated_tool()` → `dispatch_tables` → passendes `shared/code_templates_*`-Template → `rhino_exec.run_code()` (Rhino-UI-Thread) |
| 9 | `agent/` | `_persist_tool_call_and_maybe_snapshot()` → `session_store` + ggf. `model_states`-Snapshot |
| 10 | `agent/` | Loop zurück zu 6, bis `end_turn`; dann `message.complete` |
| 11 | Frontend | `useWebSocket` empfängt Events → `chatStore` → ChatView/ParameterPanel re-rendern |

**Viewport-Affordanzen** (Pick, Point, Component, Sketch) laufen NICHT durch den Agent-Loop, sondern über eigene WS-Handler in `server.py` (`_handle_sketch_request`, `_handle_pick_request`, …) und das **`viewport_bridge/`**-Paket, das modale Rhino-Eingaben (`GetPoint`/`GetObjects`) im UI-Thread ausführt. **Seit 16.06.2026** ist der frühere manuelle Snapshot/Kamera-Button ins Sketch-Werkzeug eingeschmolzen: `_handle_sketch_request` erfasst jetzt Perspektive/Top/Front/Right einzeln + ein Multi-View-Composite (`viewport.sketch_ready {views, composite}`), das Overlay hat einen Ansichts-Wähler, Senden ohne Striche = reiner Screenshot. `_handle_snapshot_request` (+ `viewport.request_snapshot`) bleibt dormant ohne Frontend-Caller; die autonome Perzeption des Modells läuft über das Tool `capture_viewport` (eigener Pfad in `agent/dispatch.py`).

---

## 3. Backend-Module (`backend/`)

| Modul | Aufgabe (1 Zeile) | Wichtige Einstiege |
|---|---|---|
| `server.py` ⚠️groß | FastAPI: alle REST-Endpoints + der `/ws`-WebSocket inkl. aller Handler | `websocket_endpoint`, `_handle_command`, `_handle_*_request`, Session-/Study-/GH-Endpoints |
| `agent/` *(Paket)* | Anthropic-Agent-Loop: API-Call, Tool-Routing, Historie, Persistenz. Module: `loop` (Loops), `dispatch` (Tool-Exec), `history`, `message_flattener`, `local_adapter`, `errors`, `prompts` (SYSTEM_PROMPT) | `run_agent`, `SYSTEM_PROMPT` (re-exportiert via `__init__`) |
| `tool_schemas.py` ⚠️groß | Reine Daten: JSON-Schemas aller 101 dedizierten Tools (`TOOL_SCHEMAS`); zusammen mit den 2 Built-ins + 5 Dialog-Tools = 108 werkzeug / 95 basis | `TOOL_SCHEMAS` |
| `dedicated_tools/` *(Paket)* | Dispatcher Tool→Template→Rhino + Varianten/Parameter/GH. Module: `core` (Dispatch), `dispatch_tables`, `parameters`, `variants`, `_shared` | `dispatch_dedicated_tool`, `is_dedicated_tool`, `apply_parameter_change`, `sync_variant_state` |
| `tool_registry.py` | **Bedingungs-Gate**: Core-CAD-Tools (95) in beiden Bedingungen, Interaction-Tools (13) nur in `werkzeug` (Spec §1.2; Deny-List `INTERACTION_TOOL_NAMES`, seit 02.07. fail-closed; Lock-Tools am 11.06. entfernt, `LOCK_TOOL_SCHEMAS` dormant) | `build_tool_list`, `INTERACTION_TOOL_NAMES` |
| `rhino_exec.py` | Führt Python im Rhino-UI-Thread aus; injiziert `CODE_PREAMBLE` (rs/rg/sc + Undo/Archiv). `run_code` liefert `repr(result)` (`_stringify`) → strukturierte Rückgaben mit `decode_run_code_result` entwrappen, nie direkt `json.loads` (06.07.) | `run_code`, `CODE_PREAMBLE`, `decode_run_code_result` |
| `session_store.py` | SQLite-Persistenz (WAL): alle Tabellen | `get_store()`, `log_tool_call`, `list_messages`, … |
| `websocket_manager.py` | Async-Broadcast an alle UI-Clients | `manager`, `broadcast`, `broadcast_threadsafe` |
| `structure_context_service.py` | Orchestriert: Strukturkontext anwenden + persistieren + broadcasten | `apply_structure_context`, `refresh_structure_context_in_store` |
| `structure_registry.py` | Löst Rhino/GH-Objekt in editierbares Strukturmodell auf (primitive_box, …) | `resolve_structure_context_from_*` |
| `parameter_derivation.py` | Leitet Slider-Parameter aus dem Strukturkontext ab | `derive_parameters_from_structure`, `annotate_structure_context` |
| `component_router.py` | Routet generische Brep-Edits anhand jüngster Komponenten-Picks auf objekt­spezifische Tools | `_route_/_guard_/_augment_tool_from_recent_component_picks` |
| `capability_router.py` | Baut den System-Prompt-Einschub „welche Parameter/Slider existieren, in welcher Priorität" | `build_capability_router_prompt` |
| `inspection_service.py` | Baut Inspektions-Ergebnisblöcke (Szene/Objekt/Layer) | `inspect_request` |
| `grasshopper_bridge.py` | HTTP-Brücke zum GHPython-Server (Port 9998) für GH-Tools | `dispatch_grasshopper_tool` |
| `viewport_bridge/` | Modale Rhino-Eingaben (Pick/Punkt) im UI-Thread | (Paket; von den WS-Handlern gerufen) |
| `export.py` | Studien-Bundle pro Session (SQLite-Subset, JSONL, Snapshots, Manifest mit Hashes). Die `model.3dm` der Lauf-Geometrie wird schon beim Stop-Button vorab nach `rhaino/run_models/{study_session_id}.3dm` gesichert (Endpoint `POST /api/study/sessions/{id}/save-run-model` in `server.py`); der Export bevorzugt diese Vorab-Datei vor der evtl. schon geleerten Szene (06.07.) | `export_session_bundle` |
| `schemas.py` | Pydantic-Modelle für REST/WS (spiegelt `web/src/lib/types.ts`) | `Message`, `Session`, `ExposedParameter`, … |
| `config.py` | Settings-Singleton (`config.json`): API-Key, Modell, Modus, Studien-Flags | `config` |
| `consent.py` / `agency.py` | Studien-Texte: Einwilligung / Agency-Items | `load_consent_text`, `load_agency_items` |
| `tests/` | Unit-Tests (stdlib `unittest`; lädt Module via `importlib` direkt, ohne Rhino-Deps) | `test_message_flattener.py` (Flatten-/Deixis-Schicht + Entscheidung C) |

---

## 4. Shared Code-Templates (`../shared/`)

Reine Funktionen, die **Rhino-Python-Quelltext als String** zurückgeben (kein direkter Rhino-Zugriff hier). `dedicated_tools` ruft sie, `rhino_exec.run_code` führt das Ergebnis aus. **Wichtig:** keine f-strings in den Templates (IronPython-/GH-Kompatibilität) — Parameter via `inject_params()`.

| Modul | Inhalt |
|---|---|
| `code_templates.py` | Index/Re-Export aller Kategorien |
| `code_templates_geometry.py` | Primitive (Box, Sphere, Cylinder, …) |
| `code_templates_transform.py` | Move/Copy/Rotate/Scale/Mirror/Array |
| `code_templates_curve.py` | Kurven (Polyline, Offset, Loft, Sweep, …) |
| `code_templates_surface.py` | Flächen (planar, cap, join, offset, thicken) |
| `code_templates_boolean.py` | Boolean union/difference/intersection/split |
| `code_templates_subd.py` | SubD erstellen/bearbeiten |
| `code_templates_brep_edit.py` | Primitiv-Edits (resize_box_face, create_hole/slot, fillet, chamfer, move_face) |
| `code_templates_inspection.py` | Lese-Queries (scene/object/brep-component info, resolve_reference) |
| `code_templates_selection.py` | Selektion/Layer (select, set_layer, delete, rename) |
| `action_history_preamble.py` | Geteilter Undo-/Aktionshistorie-Quelltext (`ACTION_HISTORY_PREAMBLE`) — in `rhino_exec.CODE_PREAMBLE` **und** den MCP-Server eingebaut |
| `_codegen_base.py` | `inject_params()` — sichere Parameter-Injektion |

---

## 5. Frontend (`web/src/`)

| Datei/Bereich | Aufgabe |
|---|---|
| `main.tsx` | Vite-Einstieg, mountet `<App/>` |
| `App.tsx` | UI-Schale: Header, Session-Lifecycle, Dialoge, Undo/Redo |
| `store/chatStore.ts` | Zustand-Store (Sessions, Messages, Parameter, Strukturkontext, Studien-State) |
| `hooks/useWebSocket.ts` | WS-Verbindung + `send()`; Auto-Reconnect |
| `hooks/useCondition.ts` | Bedingungs-Gate (`basis`/`werkzeug`) für UI-Slots |
| `lib/api.ts` | REST-Wrapper |
| `lib/types.ts` | TS-Typen (spiegelt `schemas.py`) |
| `features/chat/ChatView.tsx` | Nachrichtenliste |
| `features/chat/InputBar.tsx` | Texteingabe + Viewport-Buttons (Punkt/Komponente/Sketch/Bildanhang/GH-Parametrik; der Kamera/Snapshot-Button wurde am 16.06.2026 ins Sketch-Werkzeug eingeschmolzen). Reine DOM-/Token-Helfer + Attachment-Subkomponenten sind seit 26.06.2026 ausgelagert (s.u.) |
| `features/chat/composer-dom.ts` | Reine DOM-Helfer des Composers (kein React): Referenz-/Befehls-Token-Builder, Icons, `serializeComposerContent`/`parseReferenceBlock`, Caret-/Range-Helfer, `blockKey`. Aus InputBar.tsx ausgelagert (26.06.2026) |
| `features/chat/AttachmentStrip.tsx` | Anhang-Streifen unter dem Composer (Bild/Auswahl/Punkt/Komponente/Skizze-Kacheln). Aus InputBar.tsx ausgelagert (26.06.2026) |
| `features/chat/MessageBubble.tsx` | Einzelne Nachricht (Text/Bild/Blöcke) |
| `features/chat/QuickReplyCard.tsx` | Dialog-Tool-Karten (Parameter/Choice/Confirm/ReferencePick) |
| `features/chat/ParameterPanel.tsx` | Slider-Leiste (exposed parameters) |
| `features/chat/VariantGallery.tsx` / `InspectPanel.tsx` | Varianten / Inspektion (`LockPanel.tsx` am 11.06.2026 gelöscht) |
| `features/chat/SketchOverlay.tsx` | Vollbild-Skizzen-Canvas mit Ansichts-Wähler (Perspektive/Top/Front/Right; Default Perspektive); Zeichnen optional, ohne Striche = reiner Multi-View-Snapshot |
| `features/study/*` | ConsentDialog, PreSessionDialog, AgencySurveyDialog, RecordingBadge |
| `features/settings/SettingsDialog.tsx` | Config-Editor |
| `components/ui/*` | shadcn/ui-Primitive |

---

## 6. Kernkonzepte (wo sie wohnen)

- **Bedingungen** (`tool_registry.build_tool_list`): `basis` = Core-CAD-Toolstack (95), aber kein UI-Slot außer Chat/Bild; `werkzeug` = derselbe Core-CAD-Toolstack + alle Interaction-Tools (13 = 108 gesamt; Stand 02.07.2026) und Affordanzen. Seit 11.06.2026 (Designkorrektur, im Code umgesetzt) via Deny-List `INTERACTION_TOOL_NAMES` statt der alten Drei-Tool-Allow-List, seit 02.07. fail-closed; Spec §1.2. Frontend gated über `useCondition()`.
- **`editable_recipe`** (`structure_registry` → `parameter_derivation` → `dedicated_tools`): UserText auf einem Primitiv (Box/Cylinder/Extrusion) mit den Maßen. Daraus leitet das Backend Slider ab; bei Slider-Änderung wird das Objekt aus dem Recipe rekonstruiert → parametrisch & reversibel ohne Grasshopper. Die Slider erscheinen beim Referenzieren (Pick/Selektion) und **seit 16.06.2026 auch direkt beim Erstellen** eines Primitivs — aber nur, wenn der Nutzer keine Maße nannte (Gate `_STRUCTURE_CREATED_OBJECT_TOOLS` in `dedicated_tools/_shared.py`: erstellte GUID wird nur dann Strukturkontext-Kandidat, wenn kein Größen-Feld im `tool_input` ist; `create_box`-Maße dafür im Schema optional gemacht).
- **`capability_router` vs `component_router`**: *capability* = **Parameter-Findung** (welche Slider existieren, in welcher Priorität → Prompt-Einschub pro Turn). *component* = **Tool-Routing** (nach einem Face/Edge-Pick generischen Brep-Edit auf objektspezifisches Tool umbiegen).
- **Struktur-Trio**: `structure_registry` (aus Rhino auflösen) → `parameter_derivation` (Slider/Operationen ableiten) → `structure_context_service` (persistieren + broadcasten).
- **Logging-Tabellen** (`session_store`, SQLite WAL, 17 Tabellen): `sessions, messages, tool_calls, parameter_changes, model_states, exposed_parameters, active_structure_contexts, variants, snapshots, locked_objects (dormant seit 11.06.2026), study_sessions, study_events, consent, events, agency_survey, final_survey, participant_demographics`. Die `agency_survey`-Tabelle trägt zusätzlich die Spalten `tool_measure`/`tool_measure_unused` (dormant seit 15.06.2026, Measure aus dem Studienumfang entfernt) und `tool_lock`/`tool_lock_unused` (dormant seit 11.06.2026). Auswertung: `tools/tooluse_report.py`.
- **Reversibilität** (`action_history_preamble`, `rhino_exec`): `archive_object()` sichert vor destruktiven Edits auf den versteckten Archive-Layer; `begin/finish_action` + `undo_last_action`/`redo_last_action` für den Plugin-Undo (REST `/api/rhino/undo|redo`).
- **Varianten**: separate `Variant_*`-Layer (Gallery-Thumbnails). (Das frühere Lock-Feature — `locked_objects`-Tabelle → System-Prompt-Addendum — wurde am 11.06.2026 aus Tool-Surface + UI entfernt; die Tabelle bleibt dormant.)
- **Grasshopper** (`grasshopper_bridge`): GH-Tools gehen per HTTP an den GHPython-Server (Port 9998), getrennt vom Rhino-Pfad.

---

## 7. „Wo schaue ich, wenn …"

| Ich will … | Datei(en) |
|---|---|
| ein neues dediziertes Tool hinzufügen | `tool_schemas.py` (Schema) + `dedicated_tools.py` (Dispatch) + `shared/code_templates_*` (Codegen) + ggf. `tool_registry` |
| ändern, was in welcher Bedingung sichtbar ist | `tool_registry.build_tool_list` (Backend) + `useCondition`/Slot-Gating (Frontend) |
| den System-Prompt anpassen | `agent/prompts.py` (`SYSTEM_PROMPT`) → danach `validate_hashes.py` neu verankern |
| einen neuen REST-/WS-Befehl | `server.py` (`_handle_command` bzw. neuer `@app.*`) + `web/src/lib/api.ts` / `useWebSocket` |
| Slider-/Parameter-Verhalten | `parameter_derivation` + `structure_context_service` + `ParameterPanel.tsx` |
| Logging/Export für die Studie | `session_store.py` (Schreiben) + `export.py` (Bundle) + `tools/tooluse_report.py` (Auswertung) |
| Picks/Sketch | `server.py` `_handle_*_request` + `viewport_bridge/` + `InputBar.tsx`/`SketchOverlay.tsx` |
| eine `run_code`-Rückgabe strukturiert weiterverarbeiten | **`rhino_exec.decode_run_code_result`** verwenden, nie direkt `json.loads` — `run_code` liefert `repr(result)` (`_stringify`), ein direktes `json.loads` scheitert still am Single-Quote-Repr-Mantel (Bug-Klasse hinter `model.3dm`/`model_states`, 06.07.2026, Commit `c3c0b89`) |

---

## 8. Große Dateien & geplante Splits

Vier Dateien tragen den Großteil und sind die „ich-verstehe-nichts"-Hotspots:

| Datei | grob | enthält (Gruppen) | Split-Idee |
|---|---|---|---|
| `server.py` | ~2.6k | Health, Settings, Sessions+Debug-Dump, Locks (REST dormant seit 11.06.2026), Study, GH, Rhino-Actions, `/ws`+Handler | `endpoints/` (sessions/study/grasshopper/rhino) + `ws_handlers/` (viewport/parameters/variants) + `debug_dump.py` |
| `agent/` | ~2.3k | SYSTEM_PROMPT, Loop, Historie, Block-Flattening, Tool-Exec, Local-Adapter | **erledigt 09.06.:** Paket `agent/` (prompts/message_flattener/history/local_adapter/errors/dispatch/loop); SYSTEM_PROMPT-Hash grün, verhaltensgleich verifiziert |
| `tool_schemas.py` | ~2.2k | reine Schema-Daten | **erledigt 09.06.:** in-place sektioniert (TOC + 13 Kategorie-Marker); bewusst NICHT gesplittet (Reihenfolge pinnt den Hash) |
| `dedicated_tools.py` | ~1.6k | Dispatcher + `_handle_*` | **erledigt 09.06.:** Paket `dedicated_tools/` (core/dispatch_tables/parameters/variants/_shared); verhaltensgleich verifiziert |

> **Wichtig:** Splits sind verhaltensgleich durchzuführen, nach jedem Schritt `py_compile` + `npm run build` + `validate_hashes` (`agent.py`/`tool_schemas.py` berühren die golden hashes — Re-Anchoring nur bei *gewollter* Änderung, ein reiner Split darf den Hash NICHT verändern).

---

*Stand 2026-07-02. Bei strukturellen Änderungen am Plugin diese Landkarte mitziehen.*
