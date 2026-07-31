"""agent.message_flattener - Plugin-Bloecke (selection, point_pick, component_pick, sketch) -> native Anthropic-Bloecke."""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger("FurniturePlugin.Agent")

def _flatten_plugin_blocks(
    blocks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Replace plugin-specific blocks (selection, point_pick, component_pick, sketch) with
    native Anthropic blocks (text and optionally image) so the API receives
    them instead of silently dropping them during role-filtering.

    Shape is lossy on purpose — the model sees the human-readable summary
    plus (when available) the preview/background image. The original block
    stays in SQLite for UI replay.
    """
    flat: list[dict[str, Any]] = []
    i = 0
    while i < len(blocks):
        b = blocks[i]
        t = b.get("type")
        if t == "selection":
            flat.extend(_flatten_selection(b))
            i += 1
            continue
        if t == "point_pick":
            flat.extend(_flatten_point_pick(b))
            i += 1
            continue
        if t == "component_pick":
            group: list[dict[str, Any]] = []
            while i < len(blocks) and blocks[i].get("type") == "component_pick":
                group.append(blocks[i])
                i += 1
            flat.extend(_flatten_component_pick_group(group))
            continue
        if t == "sketch":
            flat.extend(_flatten_sketch(b))
            i += 1
            continue
        if t == "image" and "origin" in b:
            # ``origin`` ist Plugin-Metadatum (Upload vs. Viewport-
            # Schnappschuss, fürs W2-Survey-Gate) — die Anthropic-API
            # lehnt unbekannte Felder in Content-Blöcken ab.
            flat.append({k: v for k, v in b.items() if k != "origin"})
            i += 1
            continue
        flat.append(b)
        i += 1
    return flat


def _flatten_selection(b: dict[str, Any]) -> list[dict[str, Any]]:
    names: list[str] = list(b.get("names") or [])
    types: list[str] = list(b.get("types") or [])
    ids: list[str] = list(b.get("object_ids") or [])
    # Prefer user-assigned names; fall back to coarse types grouped by count
    # ("2× polysurface, 1× curve") so the model still gets *something*
    # descriptive when the picked objects are nameless. Last resort: count.
    non_empty_names = [n for n in names if n]
    if non_empty_names:
        label = ", ".join(non_empty_names)
    elif types:
        from collections import Counter

        counts = Counter(types)
        label = ", ".join(f"{c}× {t}" for t, c in counts.items())
    else:
        label = f"{len(ids)} Objekte"
    header = f"Ausgewählte Objekte: {label}"
    if ids:
        header += f"\n(IDs: {', '.join(ids)})"
    out: list[dict[str, Any]] = [{"type": "text", "text": header}]
    snap = b.get("snapshot")
    if isinstance(snap, dict) and snap.get("data"):
        out.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": snap.get("media_type", "image/jpeg"),
                    "data": snap["data"],
                },
            }
        )
    return out


def _flatten_point_pick(b: dict[str, Any]) -> list[dict[str, Any]]:
    point = b.get("point") or []
    if len(point) != 3:
        return [{"type": "text", "text": "Punkt-Pick (ungueltig oder abgebrochen)"}]

    parts = [
        (
            "Gepickter Punkt im Modell (Modell-Einheiten): "
            f"({point[0]:.2f}, {point[1]:.2f}, {point[2]:.2f})"
        )
    ]

    object_id = b.get("object_id")
    object_name = (b.get("object_name") or "").strip()
    object_type = (b.get("object_type") or "").strip()
    snap_type = (b.get("snap_type") or "").strip()

    host_parts: list[str] = []
    if object_name:
        host_parts.append(f"Name: {object_name}")
    if object_type:
        host_parts.append(f"Typ: {object_type}")
    if object_id:
        host_parts.append(f"ID: {object_id}")
    if host_parts:
        parts.append("Liegt auf Objekt - " + "; ".join(host_parts))
    if snap_type:
        parts.append(f"Snap-Typ: {snap_type}")

    out: list[dict[str, Any]] = [{"type": "text", "text": "\n".join(parts)}]
    snap = b.get("snapshot")
    if isinstance(snap, dict) and snap.get("data"):
        out.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": snap.get("media_type", "image/jpeg"),
                    "data": snap["data"],
                },
            }
        )
    return out


def _flatten_component_pick(b: dict[str, Any]) -> list[dict[str, Any]]:
    point = b.get("point") or []
    pick_point = b.get("pick_point") or []
    component_info = b.get("component_info") or {}
    component_type = str(b.get("component_type") or "").strip().lower()
    component_index = b.get("component_index")
    object_id = b.get("object_id")
    object_name = (b.get("object_name") or "").strip()
    object_type_name = (b.get("object_type_name") or "").strip()
    object_class = (b.get("object_class") or "").strip()
    allowed_operations = b.get("allowed_operations") or []

    if component_type == "edge":
        label = "Kante"
    elif component_type == "face":
        label = "Flaeche"
    elif component_type == "vertex":
        label = "Vertex"
    else:
        label = "Objekt"
    geometry_class = str(b.get("geometry_class") or "").strip().lower()
    _gc_prefix = {"subd": "SubD-", "mesh": "Mesh-"}.get(geometry_class, "")
    prefixed_label = _gc_prefix + label
    lines = [f"Gepicktes {prefixed_label.lower()} im Modell."]

    detail_parts: list[str] = []
    if object_name:
        detail_parts.append(f"Name: {object_name}")
    if object_type_name:
        detail_parts.append(f"Typ: {object_type_name}")
    if object_id:
        detail_parts.append(f"ID: {object_id}")
    if object_class:
        detail_parts.append(f"Objektklasse: {object_class}")
    if component_index is not None:
        detail_parts.append(f"{label}-Index: {component_index}")
    if detail_parts:
        lines.append("; ".join(detail_parts))

    if len(point) == 3:
        lines.append(
            "Referenzpunkt (Modell-Einheiten): "
            f"({point[0]:.2f}, {point[1]:.2f}, {point[2]:.2f})"
        )
    info_line = _component_geometry_summary(component_type, component_info)
    if info_line:
        lines.append(info_line)
    if len(pick_point) == 3 and pick_point != point:
        lines.append(
            "Urspruenglicher Klickpunkt (Modell-Einheiten): "
            f"({pick_point[0]:.2f}, {pick_point[1]:.2f}, {pick_point[2]:.2f})"
        )
    if isinstance(allowed_operations, list) and allowed_operations:
        ops = ", ".join(str(op) for op in allowed_operations)
        lines.append(f"Erlaubte lokale Operationen: {ops}")

    out: list[dict[str, Any]] = [{"type": "text", "text": "\n".join(lines)}]
    snap = b.get("snapshot")
    if isinstance(snap, dict) and snap.get("data"):
        out.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": snap.get("media_type", "image/jpeg"),
                    "data": snap["data"],
                },
            }
        )
    return out


def _flatten_component_pick_group(
    blocks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not blocks:
        return []
    if len(blocks) == 1:
        return _flatten_component_pick(blocks[0])

    def _label(kind: str, gc: str = "") -> str:
        if kind == "edge":
            base = "Kante"
        elif kind == "face":
            base = "Flaeche"
        elif kind == "vertex":
            base = "Vertex"
        else:
            base = "Objekt"
        prefix = {"subd": "SubD-", "mesh": "Mesh-"}.get(gc.lower(), "")
        return prefix + base

    lines = [
        "Mehrere Komponenten wurden im Modell gepickt.",
        "Verwende EXAKT diese Referenzen als Zielmenge und rate keine benachbarten Komponenten dazu.",
    ]

    grouped: dict[
        tuple[str, str, str, str, str],
        dict[str, Any],
    ] = {}
    snapshots: list[dict[str, Any]] = []

    for b in blocks:
        component_type = str(b.get("component_type") or "object").strip().lower()
        object_id = str(b.get("object_id") or "").strip()
        object_name = str(b.get("object_name") or "").strip()
        object_type_name = str(b.get("object_type_name") or "").strip()
        object_class = str(b.get("object_class") or "").strip()
        component_info = b.get("component_info") or {}
        key = (
            object_id,
            object_name,
            object_type_name,
            object_class,
            component_type,
        )
        geometry_class_b = str(b.get("geometry_class") or "").strip().lower()
        entry = grouped.setdefault(
            key,
            {
                "components": [],
                "allowed_operations": [],
                "geometry_class": geometry_class_b,
            },
        )
        idx = b.get("component_index")
        if isinstance(idx, int) and idx not in [
            c["index"] for c in entry["components"]
        ]:
            entry["components"].append(
                {"index": idx, "info": component_info}
            )
        for op in b.get("allowed_operations") or []:
            op_str = str(op)
            if op_str not in entry["allowed_operations"]:
                entry["allowed_operations"].append(op_str)
        snap = b.get("snapshot")
        if (
            isinstance(snap, dict)
            and snap.get("data")
            and not snapshots
        ):
            snapshots.append(snap)

    for (
        object_id,
        object_name,
        object_type_name,
        object_class,
        component_type,
    ), entry in grouped.items():
        label = _label(component_type, entry.get("geometry_class", ""))
        detail_parts: list[str] = []
        host = object_name or object_type_name or object_id or "Unbekannt"
        detail_parts.append(f"{label}-Referenzen auf {host}")
        if object_class:
            detail_parts.append(f"Objektklasse: {object_class}")
        if object_id:
            detail_parts.append(f"ID: {object_id}")

        components = entry["components"]
        indices = sorted(
            c["index"] for c in components if isinstance(c.get("index"), int)
        )
        if indices:
            indices_sorted = sorted(indices)
            detail_parts.append(f"Indizes: {indices_sorted}")
        lines.append("- " + " | ".join(detail_parts))

        for comp in sorted(
            components,
            key=lambda c: int(c.get("index", -1)),
        ):
            idx = comp.get("index")
            info_line = _component_geometry_summary(
                component_type, comp.get("info") or {}
            )
            if info_line and isinstance(idx, int):
                lines.append(f"  {label} {idx}: {info_line}")

        ops = entry["allowed_operations"]
        if ops:
            lines.append(
                "  Erlaubte Operationen: " + ", ".join(ops)
            )
        if component_type == "edge" and len(indices) >= 2:
            lines.append(
                "  Wenn der Nutzer von 'diese' oder 'diese beiden' spricht, "
                f"sind GENAU diese Kanten-Indizes gemeint: {sorted(indices)}. "
                "Bearbeite sie in EINEM gemeinsamen Tool-Call mit edge_indices=[...]."
            )

    out: list[dict[str, Any]] = [{"type": "text", "text": "\n".join(lines)}]
    for snap in snapshots:
        out.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": snap.get("media_type", "image/jpeg"),
                    "data": snap["data"],
                },
            }
        )
    return out


def _fmt_xyz(value: Any) -> str | None:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    try:
        x, y, z = float(value[0]), float(value[1]), float(value[2])
    except Exception:
        return None
    return f"({x:.2f}, {y:.2f}, {z:.2f})"


def _component_geometry_summary(
    component_type: str, info: dict[str, Any]
) -> str | None:
    if not isinstance(info, dict) or not info:
        return None
    if component_type == "edge":
        start = _fmt_xyz(info.get("start_point"))
        end = _fmt_xyz(info.get("end_point"))
        length = info.get("length")
        parts: list[str] = []
        if start and end:
            parts.append(f"verlaeuft von {start} nach {end}")
        if isinstance(length, (int, float)):
            parts.append(f"Laenge {float(length):.2f}")
        tangent = _fmt_xyz(info.get("tangent"))
        if tangent:
            parts.append(f"Richtung {tangent}")
        total = info.get("total_edges")
        doc = info.get("doc_edge_count")
        if isinstance(total, int) and isinstance(doc, int):
            parts.append(f"Kantenzahl {doc} im Dokument")
        if info.get("index_mismatch") and isinstance(total, int) and isinstance(doc, int):
            parts.append(
                f"ACHTUNG: Pick-Index basiert auf intern gesplitteter Geometrie "
                f"({total} Kanten), das Dokument-Brep hat {doc} — der Index ist "
                f"NICHT direkt nutzbar. Identifiziere die Kante stattdessen ueber "
                f"den Midpoint (siehe oben) mit get_brep_component_info"
            )
        return "; ".join(parts) if parts else None
    if component_type == "face":
        midpoint = _fmt_xyz(info.get("midpoint"))
        normal = _fmt_xyz(info.get("normal"))
        parts = []
        if midpoint:
            parts.append(f"Mittelpunkt {midpoint}")
        if normal:
            parts.append(f"Normale {normal}")
        if isinstance(info.get("area"), (int, float)):
            parts.append(f"Flaeche {float(info['area']):.2f}")
        total = info.get("total_faces")
        doc = info.get("doc_face_count")
        if isinstance(total, int) and isinstance(doc, int):
            parts.append(f"Flaechenzahl {doc} im Dokument")
        if info.get("index_mismatch") and isinstance(total, int) and isinstance(doc, int):
            parts.append(
                f"ACHTUNG: Pick-Index basiert auf intern gesplitteter Geometrie "
                f"({total} Flaechen), das Dokument-Brep hat {doc} — der Index ist "
                f"NICHT direkt nutzbar. Identifiziere die Flaeche stattdessen ueber "
                f"den Midpoint (siehe oben) mit get_brep_component_info"
            )
        return "; ".join(parts) if parts else None
    if component_type == "vertex":
        parts = []
        pos = _fmt_xyz(info.get("position"))
        if pos:
            parts.append(f"Position {pos}")
        total = info.get("total_vertices")
        if isinstance(total, int):
            parts.append(f"Vertexanzahl: {total}")
        hint = info.get("hint")
        if hint:
            parts.append(str(hint))
        return "; ".join(parts) if parts else None
    center = _fmt_xyz(info.get("bbox_center"))
    hint = info.get("hint")
    fallback_parts: list[str] = []
    if center:
        fallback_parts.append(f"BBox-Zentrum {center}")
    if hint:
        fallback_parts.append(str(hint))
    return "; ".join(fallback_parts) if fallback_parts else None


def _image_block(img: Any, default_media_type: str = "image/png") -> Optional[dict[str, Any]]:
    """Wrap an ImageSource dict as a native Anthropic image block.

    Returns ``None`` when ``img`` isn't a dict with usable ``data`` so
    callers can simply skip it. ``media_type`` is read defensively.
    """
    if isinstance(img, dict) and img.get("data"):
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": img.get("media_type", default_media_type),
                "data": img["data"],
            },
        }
    return None


def _flatten_sketch(b: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert a ``SketchBlock`` dict into native Anthropic blocks.

    Only the persisted SketchBlock fields reach this function: the agent
    flattens validated ``schemas.Message`` history, and that schema declares
    just ``type/svg/background/rendered_png/width/height`` — ``composite``/
    ``has_strokes``/``view_name`` are dropped at validation. So the model
    always receives exactly ONE image plus a caption:

    - the frontend hands over ``rendered_png`` for both cases — the marked
      single view for an annotation, the multi-view composite for a pure
      strokeless snapshot (``background`` is only a fallback for very old DB
      rows that predate ``rendered_png``);
    - ``svg``-emptiness picks the caption: empty -> snapshot-context framing,
      non-empty -> free-hand Deixis framing.

    The multi-view composite never rides along to the model for sketches
    (Entscheidung C, 22.06.2026): a single sharp view carries the strokes
    better, and the model fetches extra angles itself via ``capture_viewport``.
    ``svg`` itself is never sent — it's a frontend edit format, not model input.
    """
    has_drawing = bool((b.get("svg") or "").strip())
    if has_drawing:
        caption = (
            "Skizze des Users: eine Freihand-Annotation, die direkt auf "
            "einen Viewport-Snapshot des aktuellen Modells gezeichnet "
            "wurde. Die Striche drücken eine räumliche Entwurfsabsicht aus "
            "— interpretiere sie gegen die darunterliegende Geometrie im "
            "Bild, nicht als eigenständige Zeichnung. Übliche Lesart: ein "
            "eingekreister/umrandeter Bereich = 'diese Stelle / dieses "
            "Bauteil'; eine Linie = eine Kante, ein Schnitt oder eine "
            "Richtung/Verlängerung; eine geschlossene Form = eine zu "
            "erzeugende oder anzupassende Kontur; mehrfaches Übermalen = "
            "Betonung. Behandle die Skizze als ABSICHT, nicht als exakt zu "
            "reproduzierende Vorlage. Ist die Bedeutung mehrdeutig, frage "
            "kurz nach, statt zu überinterpretieren."
        )
    else:
        caption = (
            "Multi-View-Snapshot des aktuellen Modells (Perspektive/Top/"
            "Front/Right in einem Bild), vom User als visueller Kontext "
            "angehängt — KEINE Striche darauf. Nutze ihn, um die aktuelle "
            "Geometrie und Proportionen zu sehen; interpretiere nichts als "
            "Annotation."
        )
    out: list[dict[str, Any]] = [{"type": "text", "text": caption}]
    block = _image_block(b.get("rendered_png") or b.get("background"))
    if block is not None:
        out.append(block)
    return out
