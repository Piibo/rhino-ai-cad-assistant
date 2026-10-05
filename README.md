# Rhino AI-CAD Assistant — Code zur Masterarbeit

Kuratierte Code-Basis der Masterarbeit **„Entwerfen mit Künstlicher Intelligenz:
Ein KI-gestützter Workflow für den iterativen Möbelentwurf“**
(M.Sc. Medieninformatik, LMU München; in Kooperation mit dem Lehrstuhl für Architekturinformatik der TUM).

Das Repository enthält die zwei technischen Artefakte der Arbeit:

## 1. `mcp-server/` — MCP-Server der Research-through-Design-Phase

Ein Model-Context-Protocol-Server, der einen LLM-Agenten (z. B. Claude) mit
Rhino 8 und Grasshopper verbindet. Er diente in der explorativen RtD-Phase als
Forschungswerkzeug, um Interaktionsmuster zu sichten und die
Designanforderungen des späteren Prototyps abzuleiten.

- `rhino_mcp/` — Server-Module (Geometrie-, Kurven-, Flächen-, SubD-,
  Boolean-, Transformations- und Grasshopper-Werkzeuge)
- `rhino_script.py` — Rhino-seitiger TCP-Listener
- `ATTRIBUTION.md` — Herkunft und Abgrenzung: Der Server baut auf zwei
  MIT-lizenzierten Projekten auf (SerjoschDuering/rhino-mcp als
  Kommunikationsbasis, quocvibui/rhino3d-mcp als Architekturvorbild der
  Werkzeugmodule); die Werkzeugmodule wurden neu implementiert und um eine
  eigene SubD-Schicht ergänzt.

Start: `python main.py` (stdio-Transport), in Rhino 8 den Listener über
`_-RunPythonScript .../rhino_script.py` starten.

## 2. `plugin/` — Studienartefakt (API-Modus)

Das in der qualitativen Nutzerstudie (n = 8, 16 Sitzungen) eingesetzte
Rhino-Plugin: FastAPI-Backend im Rhino-Prozess plus React-Panel. Modell-Aufrufe
gehen direkt an die Anthropic API. Zwei Studienkonfigurationen im selben
Plugin: `basis` (Chat + Bildanhang) und `werkzeug` (zusätzliche sichtbare
Interaktionsschicht: Punkt-/Objektreferenzen, Skizzen, Slider, Varianten,
Dialogkarten, Vorschau-Freigabe). Beide nutzen denselben CAD-fähigen Kern
(95 CAD-Kernwerkzeuge; `werkzeug` ergänzt 13 Interaktionswerkzeuge).

- `backend/` — Agent-Loop, Tool-Registry (`build_tool_list(condition)` als
  einzige Bedingungsverzweigung), dedizierte CAD-Werkzeuge, Studien-Logging
  und Export
- `web/src/` — React-Frontend (Chat, Tool-Call-Karten, Referenz-Chips,
  Skizzen-Overlay, Slider, Variantengalerie)
- `FEATURES.md` / `ARCHITECTURE.md` — vollständige Feature- und Code-Doku
- `validate_hashes.py` — friert Systemprompt und Werkzeuglisten als Golden
  Hashes ein (Versionskonstanz über die Studie)
- `shared/` (neben `plugin/`) — Rhino-Python-Vorlagen, aus denen das Backend den
  Code erzeugt, den es in Rhino ausführt

Build des Frontends: `cd plugin/web && npm install && npm run build`.
Die Python-Pakete des Backends stehen als `# r:`-Zeilen in `plugin/start_plugin.py`.
Das Backend erwartet eine lokale `config.json` mit eigenem API-Schlüssel für die Anthropic API
(bewusst nicht Teil des Repositories).

## Hinweise

- Studien-Rohdaten, Teilnehmendendaten und API-Schlüssel sind nicht enthalten.
- Der wissenschaftliche Beitrag der Arbeit ist nicht dieses Artefakt, sondern
  das daraus und aus der Studie entwickelte Interaktions- und Werkzeugmodell
  (Kapitel 8.4 der Arbeit).

## Lizenz

MIT (siehe `LICENSE`). Enthaltene Anteile der Upstream-Projekte stehen
ebenfalls unter MIT; Details in `mcp-server/ATTRIBUTION.md`.
