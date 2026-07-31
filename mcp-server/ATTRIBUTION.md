# Attribution

This MCP server was created by adapting one open-source codebase and consulting a second open-source project as an architecture reference.

---

## Source A: SerjoschDuering/rhino-mcp
**Repository:** https://github.com/SerjoschDuering/rhino-mcp
**Verwendeter Stand:** Commit `f8ed2f0` (2025-04-09) — zugleich der aktuelle Repo-Stand (seit April 2025 keine weiteren Commits).
**License:** MIT (im README angegeben; **keine separate LICENSE-Datei** im Repository, daher von GitHub nicht als lizenziert erkannt).

**Used as the base.** The following files are directly adapted from this repo:

| File | Origin | Changes |
|---|---|---|
| `rhino_script.py` | SerjoschDuering | Adapted for Rhino 8 CPython 3 (removed IronPython fallbacks) |
| `rhino_mcp/rhino_tools.py` | SerjoschDuering | Adapted with Rhino 8 updates |
| `rhino_mcp/grasshopper_tools.py` | SerjoschDuering | Minimal changes |
| `grasshopper/GHCodeMCP_grasshopper.py` | SerjoschDuering | Unchanged |
| `grasshopper/grasshopper_mcp_client.gh` | SerjoschDuering | Unchanged |
| `main.py` | SerjoschDuering | Extended to register new tool modules |
| `server.py` | SerjoschDuering | Extended to import and instantiate new tool classes |

**Key features taken from SerjoschDuering:**
- TCP socket communication with Rhino (Port 9876)
- HTTP communication with Grasshopper (Port 9998)
- Viewport capture
- `execute_rhino_code` / `execute_gh_code` as flexible fallbacks

---

## Source B: quocvibui/rhino3d-mcp
**Repository:** https://github.com/quocvibui/rhino3d-mcp
**Verwendeter Stand:** Upstream-Commit `ed3f12c` (2026-02-19); als lokale Architektur-Referenz herangezogen (kein Code kopiert). Die lokale Referenzkopie unter `reference-mcps/` enthält zusätzlich einen eigenen Fork-Commit (`0fb6fde`, 2026-03-10, select_objects/Undo-Experiment), der nicht Teil des Upstream-Repositories ist.
**License:** MIT (LICENSE-Datei vorhanden, © 2025 Quoc Bui).

**Used as reference for tool design patterns.** No files were copied directly.
The category-based organization of operation-specific tool modules, the tool inventory, parameter design, and naming conventions were used as inspiration and reference. Its structured socket-command dispatch was not adopted.

**Key ideas taken from quocvibui:**
- Category-based organization of operation-specific tools (geometry, curves, surfaces, transformations, etc.)
- Tool inventory, parameter design, and naming conventions

---

## Original Contributions (this repo)

The following is new work not present in either source repo:

| File | Description |
|---|---|
| `rhino_mcp/subd_tools.py` | Dedicated SubD tools using RhinoCommon directly. No other Rhino MCP server has SubD support. Central to the thesis workflow: AI form → SubD → NURBS → parametric design. |
| `rhino_mcp/helpers.py` | `inject_params()` helper for safe parameter embedding, `CODE_PREAMBLE` with pre-imported modules and `add_object_metadata()` |
| `rhino_mcp/geometry_tools.py` | Reimplemented from scratch using the new architecture (not copied from quocvibui) |
| `rhino_mcp/curve_tools.py` | Reimplemented from scratch |
| `rhino_mcp/surface_tools.py` | Reimplemented from scratch |
| `rhino_mcp/transformation_tools.py` | Reimplemented from scratch |
| `rhino_mcp/boolean_tools.py` | Reimplemented from scratch |

---

## Context

This server was developed as part of a Master's thesis:

> *Entwerfen mit Künstlicher Intelligenz: Ein KI-gestützter Workflow für den
> iterativen Möbelentwurf*
> (angemeldeter Titel; englischer Arbeitstitel war „Designing with Artificial
> Intelligence: Supporting Design Decision-Making in Furniture Design through
> Human-in-the-Loop Workflows")
> M.Sc. Medieninformatik, LMU München / TUM School of Engineering and Design
> Author: Peter Trenkle, 2026
