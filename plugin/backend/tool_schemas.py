"""JSON input schemas for the dedicated (werkzeug-condition) tool set.

Pure data: the schema definitions the model sees for every dedicated
tool, kept separate from the dispatch tables and handler logic in
dedicated_tools.py. Extracted verbatim from dedicated_tools.py -- the
list content and order are unchanged, so the tools_hash written into the
study export manifest stays identical.

Navigation: ``TOOL_SCHEMAS`` is one flat list (order matters -> golden
hash). It is NOT physically split into modules precisely because the
order pins the hash; instead the runs are marked with ``# ===== <Kategorie>
=====`` section comments so you can jump to a category. Inhalt in
Listen-Reihenfolge:

  1. Primitive & einfache Erzeugung   (create_box, create_sphere, ...)
  2. Transformation & Layer           (move/copy/rotate/scale/array, set_layer)
  3. Kurven-Operationen               (offset/join/fillet/loft/sweep1/revolve)
  4. Flaechen                         (planar_surface, cap, join_surfaces)
  5. Boolean                          (union/difference/intersection/split)
  6. Feature-Schnitte                 (create_hole, create_slot)
  7. Inspektion / Szene lesen         (get_scene_info, get_object_info, ...)
  8. Brep-Flaechen-/Kanten-Edits      (resize_*_face, fillet/round/chamfer, move_face)
  9. SubD                             (create_subd_*, subd_*)
 10. Selektion, Layer, Metadaten      (get_layers, get_selected, select_objects)
 11. Verlauf / Reversibilitaet        (list_backups, restore, undo, redo)
 12. Spaetere Ergaenzungen            (arc/torus/interp-/cp-curve, sweep2, thicken, ...)
 13. Grasshopper                      (execute_gh_code, get_gh_context, update_script)
"""
from __future__ import annotations

from typing import Any


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        # ===== 1. Primitive & einfache Erzeugung =====
        "name": "create_box",
        "description": (
            "Erstellt eine achsenparallele Box (Quader) mit Ursprung am "
            "unteren-vorderen-linken Eckpunkt ``(x, y, z)`` und den "
            "Abmessungen ``width`` (X), ``depth`` (Y), ``height`` (Z)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "x": {"type": "number", "default": 0},
                "y": {"type": "number", "default": 0},
                "z": {"type": "number", "default": 0},
                "width": {"type": "number", "description": "Breite entlang X.", "default": 100},
                "depth": {"type": "number", "description": "Tiefe entlang Y.", "default": 100},
                "height": {"type": "number", "description": "Hoehe entlang Z.", "default": 100},
                "name": {
                    "type": "string",
                    "description": "Name des Objekts.",
                    "default": "Box",
                },
            },
            "required": [],
        },
    },
    {
        "name": "create_point",
        "description": "Erstellt einen Punkt bei ``(x, y, z)``.",
        "input_schema": {
            "type": "object",
            "properties": {
                "x": {"type": "number", "default": 0},
                "y": {"type": "number", "default": 0},
                "z": {"type": "number", "default": 0},
                "name": {"type": "string", "default": "Point"},
            },
            "required": [],
        },
    },
    {
        "name": "create_line",
        "description": "Erstellt eine Linie von ``(x1, y1, z1)`` nach ``(x2, y2, z2)``.",
        "input_schema": {
            "type": "object",
            "properties": {
                "x1": {"type": "number", "default": 0},
                "y1": {"type": "number", "default": 0},
                "z1": {"type": "number", "default": 0},
                "x2": {"type": "number", "default": 10},
                "y2": {"type": "number", "default": 0},
                "z2": {"type": "number", "default": 0},
                "name": {"type": "string", "default": "Line"},
            },
            "required": [],
        },
    },
    {
        "name": "create_polyline",
        "description": (
            "Erstellt eine Polyline durch eine Punktliste ``[[x,y,z], ...]``. "
            "Mit ``closed=true`` wird sie geschlossen."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "points": {
                    "type": "array",
                    "items": {
                        "type": "array",
                        "items": {"type": "number"},
                    },
                    "description": "Punktliste im Format ``[[x,y,z], ...]``.",
                },
                "closed": {"type": "boolean", "default": False},
                "name": {"type": "string", "default": "Polyline"},
            },
            "required": ["points"],
        },
    },
    {
        "name": "create_circle",
        "description": "Erstellt einen Kreis in der XY-Ebene am Zentrum ``(center_x, center_y, center_z)``.",
        "input_schema": {
            "type": "object",
            "properties": {
                "center_x": {"type": "number", "default": 0},
                "center_y": {"type": "number", "default": 0},
                "center_z": {"type": "number", "default": 0},
                "radius": {"type": "number", "default": 5},
                "name": {"type": "string", "default": "Circle"},
            },
            "required": [],
        },
    },
    {
        "name": "create_rectangle",
        "description": (
            "Erstellt ein Rechteck in der XY-Ebene mit Startpunkt ``(x, y, z)`` "
            "und den Massen ``width`` und ``height``."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "x": {"type": "number", "default": 0},
                "y": {"type": "number", "default": 0},
                "z": {"type": "number", "default": 0},
                "width": {"type": "number", "default": 10},
                "height": {"type": "number", "default": 10},
                "name": {"type": "string", "default": "Rectangle"},
            },
            "required": [],
        },
    },
    {
        "name": "create_sphere",
        "description": "Erstellt eine Kugel am Zentrum ``(center_x, center_y, center_z)``.",
        "input_schema": {
            "type": "object",
            "properties": {
                "center_x": {"type": "number", "default": 0},
                "center_y": {"type": "number", "default": 0},
                "center_z": {"type": "number", "default": 0},
                "radius": {"type": "number", "default": 5},
                "name": {"type": "string", "default": "Sphere"},
            },
            "required": [],
        },
    },
    {
        "name": "create_cylinder",
        "description": (
            "Erstellt einen vertikalen Zylinder mit Basismittelpunkt "
            "``(base_x, base_y, base_z)``, ``radius`` und ``height``."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "base_x": {"type": "number", "default": 0},
                "base_y": {"type": "number", "default": 0},
                "base_z": {"type": "number", "default": 0},
                "radius": {"type": "number", "default": 5},
                "height": {"type": "number", "default": 10},
                "cap": {"type": "boolean", "default": True},
                "name": {"type": "string", "default": "Cylinder"},
            },
            "required": [],
        },
    },
    {
        "name": "create_cone",
        "description": (
            "Erstellt einen vertikalen Kegel mit Basismittelpunkt "
            "``(base_x, base_y, base_z)``, ``radius`` und ``height``."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "base_x": {"type": "number", "default": 0},
                "base_y": {"type": "number", "default": 0},
                "base_z": {"type": "number", "default": 0},
                "radius": {"type": "number", "default": 5},
                "height": {"type": "number", "default": 10},
                "cap": {"type": "boolean", "default": True},
                "name": {"type": "string", "default": "Cone"},
            },
            "required": [],
        },
    },
    {
        "name": "create_pipe",
        "description": (
            "Erstellt ein Rohr entlang einer vorhandenen Kurve ``curve_id`` "
            "mit Radius ``radius``."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "curve_id": {"type": "string", "description": "GUID der Rail-Kurve."},
                "radius": {"type": "number", "default": 1},
                "cap": {"type": "boolean", "default": True},
                "name": {"type": "string", "default": "Pipe"},
            },
            "required": ["curve_id"],
        },
    },
    {
        # ===== 2. Transformation & Layer =====
        "name": "move_object",
        "description": (
            "Verschiebt ein oder mehrere Objekte um den Vektor ``(dx, dy, dz)``. "
            "IDs als String-GUIDs uebergeben."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "object_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "GUIDs der zu verschiebenden Objekte.",
                },
                "dx": {"type": "number", "default": 0},
                "dy": {"type": "number", "default": 0},
                "dz": {"type": "number", "default": 0},
            },
            "required": ["object_ids"],
        },
    },
    {
        "name": "copy_object",
        "description": (
            "Kopiert ein oder mehrere Objekte und verschiebt die Kopien um "
            "den Vektor ``(dx, dy, dz)``."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "object_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "GUIDs der zu kopierenden Objekte.",
                },
                "dx": {"type": "number", "default": 0},
                "dy": {"type": "number", "default": 0},
                "dz": {"type": "number", "default": 0},
            },
            "required": ["object_ids"],
        },
    },
    {
        "name": "rotate_object",
        "description": (
            "Rotiert ein oder mehrere Objekte um ``angle_degrees`` Grad "
            "um ein Zentrum ``center`` und eine Achse ``axis``."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "object_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "GUIDs der zu rotierenden Objekte.",
                },
                "angle_degrees": {"type": "number", "default": 90},
                "center": {
                    "type": "array",
                    "items": {"type": "number"},
                    "default": [0, 0, 0],
                },
                "axis": {
                    "type": "array",
                    "items": {"type": "number"},
                    "default": [0, 0, 1],
                },
            },
            "required": ["object_ids"],
        },
    },
    {
        "name": "scale_object",
        "description": (
            "Skaliert ein oder mehrere Objekte uniform vom Ursprung "
            "``(origin_x, origin_y, origin_z)`` mit Faktor ``scale_factor``."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "object_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "GUIDs der zu skalierenden Objekte.",
                },
                "scale_factor": {"type": "number"},
                "origin_x": {"type": "number", "default": 0},
                "origin_y": {"type": "number", "default": 0},
                "origin_z": {"type": "number", "default": 0},
            },
            "required": ["object_ids", "scale_factor"],
        },
    },
    {
        "name": "set_bbox_dimension",
        "description": (
            "Setzt eine Welt-Bounding-Box-Abmessung eines Objekts auf einen "
            "Zielwert in mm, z.B. Breite/X, Tiefe/Y oder Hoehe/Z. Nutze "
            "dies fuer Sprache wie 'mach die Box 120 mm breit' statt "
            "generisches Skalieren. ``anchor`` steuert, ob die Mitte oder "
            "die Min-/Max-Seite fest bleibt."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "object_id": {"type": "string", "description": "GUID des Objekts."},
                "axis": {
                    "type": "string",
                    "enum": [
                        "x",
                        "y",
                        "z",
                        "width",
                        "depth",
                        "height",
                        "breite",
                        "tiefe",
                        "hoehe",
                    ],
                    "description": "Zu setzende Abmessung: X/Breite, Y/Tiefe oder Z/Hoehe.",
                },
                "target_size": {
                    "type": "number",
                    "description": "Zielmass der Bounding-Box-Abmessung in mm.",
                },
                "anchor": {
                    "type": "string",
                    "enum": ["center", "min", "max"],
                    "default": "center",
                    "description": (
                        "Welche Seite beim Skalieren stehen bleibt: Mitte, "
                        "Min-Seite oder Max-Seite der gewaehlten Achse."
                    ),
                },
            },
            "required": ["object_id", "axis", "target_size"],
        },
    },
    {
        "name": "mirror_object",
        "description": (
            "Spiegelt ein oder mehrere Objekte relativ zu ``mirror_plane_origin`` "
            "und ``mirror_plane_normal``. Mit ``copy=true`` bleiben die Originale erhalten."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "object_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "GUIDs der zu spiegelnden Objekte.",
                },
                "mirror_plane_origin": {
                    "type": "array",
                    "items": {"type": "number"},
                    "default": [0, 0, 0],
                },
                "mirror_plane_normal": {
                    "type": "array",
                    "items": {"type": "number"},
                    "default": [1, 0, 0],
                },
                "copy": {"type": "boolean", "default": True},
            },
            "required": ["object_ids"],
        },
    },
    {
        "name": "array_linear",
        "description": (
            "Erstellt eine lineare Reihe von Kopien eines Objekts mit ``count`` "
            "Schritten und Abstand ``(dx, dy, dz)``."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "object_id": {"type": "string", "description": "GUID des Quellobjekts."},
                "count": {"type": "integer", "default": 5},
                "dx": {"type": "number", "default": 10},
                "dy": {"type": "number", "default": 0},
                "dz": {"type": "number", "default": 0},
            },
            "required": ["object_id"],
        },
    },
    {
        "name": "array_polar",
        "description": (
            "Erstellt eine polare Reihe von Kopien eines Objekts mit ``count`` "
            "Schritten um ``center`` entlang ``axis`` ueber ``total_angle`` Grad."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "object_id": {"type": "string", "description": "GUID des Quellobjekts."},
                "count": {"type": "integer", "default": 6},
                "center": {
                    "type": "array",
                    "items": {"type": "number"},
                    "default": [0, 0, 0],
                },
                "axis": {
                    "type": "array",
                    "items": {"type": "number"},
                    "default": [0, 0, 1],
                },
                "total_angle": {"type": "number", "default": 360},
            },
            "required": ["object_id"],
        },
    },
    {
        "name": "delete_object",
        "description": (
            "Loescht ein oder mehrere Objekte. Standardmaessig wird vor dem "
            "Loeschen eine Kopie auf den versteckten ``Archive``-Layer gelegt."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "object_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "GUIDs der zu loeschenden Objekte.",
                },
                "archive_first": {
                    "type": "boolean",
                    "description": "Vor dem Loeschen auf Archive-Layer sichern.",
                    "default": True,
                },
            },
            "required": ["object_ids"],
        },
    },
    {
        "name": "set_layer",
        "description": (
            "Setzt ein oder mehrere Objekte auf einen Layer. Optional wird der "
            "Layer angelegt, falls er noch nicht existiert."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "object_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "layer": {
                    "type": "string",
                    "description": "Layer-Name.",
                },
                "create_if_missing": {
                    "type": "boolean",
                    "default": True,
                },
            },
            "required": ["object_ids", "layer"],
        },
    },
    {
        "name": "rename_object",
        "description": "Benennt ein Objekt ``object_id`` in ``name`` um.",
        "input_schema": {
            "type": "object",
            "properties": {
                "object_id": {"type": "string"},
                "name": {"type": "string"},
            },
            "required": ["object_id", "name"],
        },
    },
    {
        # ===== 3. Kurven-Operationen =====
        "name": "offset_curve",
        "description": (
            "Offsettet eine Kurve ``curve_id`` um ``distance``. Optional "
            "bestimmt ``direction_point`` die Offset-Seite."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "curve_id": {"type": "string", "description": "GUID der Quellkurve."},
                "distance": {"type": "number", "default": 1},
                "direction_point": {
                    "type": "array",
                    "items": {"type": "number"},
                    "description": "Punkt zur Bestimmung der Offset-Seite.",
                },
                "name": {"type": "string", "default": "OffsetCurve"},
            },
            "required": ["curve_id"],
        },
    },
    {
        "name": "join_curves",
        "description": "Fuegt mehrere Kurven zu einer oder mehreren verbundenen Kurven zusammen.",
        "input_schema": {
            "type": "object",
            "properties": {
                "curve_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 2,
                },
                "name": {"type": "string", "default": "JoinedCurve"},
            },
            "required": ["curve_ids"],
        },
    },
    {
        "name": "curve_boolean_union",
        "description": (
            "Verschmilzt zwei oder mehr GESCHLOSSENE, koplanare, sich "
            "ueberlappende planare Kurven zu ihrer gemeinsamen Aussenkontur "
            "(2D-Regionen-Union). Typischer Einsatz: zwei ueberlappende "
            "Kreise zu EINER fliessenden Gesamtkontur verschmelzen — weiche "
            "Uebergaenge danach mit ``fillet_curve`` an den Knickpunkten. "
            "NICHT fuer Volumenkoerper (dafuer ``boolean_union``) und nicht "
            "fuer offene Kurvenzuege (dafuer ``join_curves``). Bei "
            "``delete_input=true`` ersetzen die Ergebnis-Konturen die "
            "Eingangskurven; Backups landen automatisch im Archive."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "curve_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 2,
                    "description": (
                        "IDs der geschlossenen, in derselben Ebene liegenden "
                        "Kurven, deren Regionen vereinigt werden sollen."
                    ),
                },
                "delete_input": {"type": "boolean", "default": True},
                "name": {"type": "string", "default": "UnionKontur"},
            },
            "required": ["curve_ids"],
        },
    },
    {
        "name": "fillet_curve",
        "description": (
            "Erstellt einen Fillet-Bogen zwischen zwei Kurven "
            "``curve_id_1`` und ``curve_id_2`` mit Radius ``radius``."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "curve_id_1": {"type": "string"},
                "curve_id_2": {"type": "string"},
                "radius": {"type": "number", "default": 1},
                "name": {"type": "string", "default": "Fillet"},
            },
            "required": ["curve_id_1", "curve_id_2"],
        },
    },
    {
        "name": "divide_curve",
        "description": (
            "Teilt eine Kurve ``curve_id`` in ``segment_count`` gleiche Segmente "
            "und liefert die Teilungspunkte zurueck."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "curve_id": {"type": "string"},
                "segment_count": {"type": "integer", "default": 10},
            },
            "required": ["curve_id"],
        },
    },
    {
        "name": "extrude_curve",
        "description": (
            "Extrudiert eine vorhandene Kurve entlang des Vektors "
            "``(dx, dy, dz)``. Mit ``cap=true`` werden planare Oeffnungen gekappt."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "curve_id": {"type": "string", "description": "GUID der Profilkurve."},
                "dx": {"type": "number", "default": 0},
                "dy": {"type": "number", "default": 0},
                "dz": {"type": "number", "default": 10},
                "cap": {"type": "boolean", "default": True},
                "name": {"type": "string", "default": "Extrusion"},
            },
            "required": ["curve_id"],
        },
    },
    {
        "name": "loft_curves",
        "description": (
            "Erstellt eine Loft-Flaeche durch zwei oder mehr Profilkurven "
            "``curve_ids``. Verwenden, wenn vorhandene Rand- oder "
            "Profilkurven direkt miteinander verbunden werden sollen, ohne "
            "separate Rail-Kurven. ``loft_type``: 0=Normal, 1=Loose, "
            "2=Tight, 3=Straight."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "curve_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 2,
                },
                "loft_type": {"type": "integer", "default": 0},
                "closed": {"type": "boolean", "default": False},
                "name": {"type": "string", "default": "Loft"},
            },
            "required": ["curve_ids"],
        },
    },
    {
        "name": "sweep1",
        "description": (
            "Sweep einer oder mehrerer Querschnittskurven ``cross_section_ids`` "
            "entlang einer Rail-Kurve ``rail_id``. Nur verwenden, wenn eine "
            "eigene Rail-Kurve und mindestens ein separates Querschnittsprofil "
            "vorliegen."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "rail_id": {"type": "string"},
                "cross_section_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                },
                "closed": {"type": "boolean", "default": False},
                "name": {"type": "string", "default": "Sweep1"},
            },
            "required": ["rail_id", "cross_section_ids"],
        },
    },
    {
        "name": "revolve_curve",
        "description": (
            "Rotationsflaeche aus einer Profilkurve ``curve_id`` um die Achse "
            "von ``axis_start`` nach ``axis_end`` zwischen ``start_angle`` und ``end_angle``."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "curve_id": {"type": "string"},
                "axis_start": {
                    "type": "array",
                    "items": {"type": "number"},
                    "default": [0, 0, 0],
                },
                "axis_end": {
                    "type": "array",
                    "items": {"type": "number"},
                    "default": [0, 0, 1],
                },
                "start_angle": {"type": "number", "default": 0},
                "end_angle": {"type": "number", "default": 360},
                "name": {"type": "string", "default": "Revolve"},
            },
            "required": ["curve_id"],
        },
    },
    {
        # ===== 4. Flaechen =====
        "name": "planar_surface",
        "description": "Erstellt eine planare Flaeche aus geschlossenen, planaren Randkurven.",
        "input_schema": {
            "type": "object",
            "properties": {
                "curve_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                },
                "name": {"type": "string", "default": "PlanarSrf"},
            },
            "required": ["curve_ids"],
        },
    },
    {
        "name": "cap_planar_holes",
        "description": "Kappt planare Oeffnungen eines Breps oder einer Polysurface ``brep_id``.",
        "input_schema": {
            "type": "object",
            "properties": {
                "brep_id": {"type": "string"},
            },
            "required": ["brep_id"],
        },
    },
    {
        "name": "join_surfaces",
        "description": (
            "Fuegt angrenzende offene Flaechen oder Polysurfaces aus "
            "``object_ids`` zu einer oder mehreren Polysurfaces zusammen. "
            "Verwenden fuer offene Sweep-/Loft-/Patch-Flaechen; nicht fuer "
            "Boolesche Volumenoperationen."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "object_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 2,
                },
                "delete_input": {"type": "boolean", "default": True},
                "name": {"type": "string", "default": "JoinedPolysurface"},
            },
            "required": ["object_ids"],
        },
    },
    {
        # ===== 5. Boolean =====
        "name": "boolean_union",
        "description": (
            "Vereint zwei oder mehr geschlossene Solids oder Polysurfaces zu "
            "einem Objekt. Mindestens 2 ``object_ids`` erforderlich."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "object_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 2,
                },
                "delete_input": {"type": "boolean", "default": True},
                "name": {"type": "string", "default": "BoolUnion"},
            },
            "required": ["object_ids"],
        },
    },
    {
        "name": "boolean_difference",
        "description": "Boolesche Differenz: subtrahiert ``remove_ids`` von ``keep_id``. Das Ergebnis ersetzt ``keep_id``; die abgezogenen ``remove_ids`` bleiben standardmaessig erhalten (sie sind meist eigene Bauteile, z. B. Beine). Nur ``delete_input=true`` setzen, wenn der Cutter ein Wegwerf-Werkzeug ist, das verschwinden soll.",
        "input_schema": {
            "type": "object",
            "properties": {
                "keep_id": {"type": "string"},
                "remove_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "delete_input": {"type": "boolean", "default": False},
                "name": {"type": "string", "default": "BoolDiff"},
            },
            "required": ["keep_id", "remove_ids"],
        },
    },
    {
        "name": "boolean_intersection",
        "description": (
            "Boolesche Schnittmenge: behaelt nur das ueberlappende Volumen "
            "von mindestens zwei ``object_ids``."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "object_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 2,
                },
                "delete_input": {"type": "boolean", "default": True},
                "name": {"type": "string", "default": "BoolIntersect"},
            },
            "required": ["object_ids"],
        },
    },
    {
        "name": "boolean_split",
        "description": (
            "Teilt ein Objekt ``object_id`` mit einem Schneidobjekt ``cutter_id``. "
            "Standardmaessig bleiben die Eingaben erhalten."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "object_id": {"type": "string"},
                "cutter_id": {"type": "string"},
                "delete_input": {"type": "boolean", "default": False},
                "name": {"type": "string", "default": "BoolSplit"},
            },
            "required": ["object_id", "cutter_id"],
        },
    },
    {
        # ===== 6. Feature-Schnitte (Loch/Schlitz) =====
        "name": "create_hole",
        "description": (
            "Schneidet ein rundes zylindrisches Loch in ein Brep-/Solid-Objekt. "
            "Nutze dies fuer 'bohr ein Loch' statt freie Boolean-Cutter zu "
            "improvisieren. ``center`` ist der Mittelpunkt der Oeffnung, "
            "``direction`` zeigt in Schnittrichtung in das Objekt hinein. "
            "Mit ``through=true`` wird ein Durchgangsloch erzeugt; sonst "
            "wird ``depth`` als Sackloch-Tiefe genutzt."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "object_id": {"type": "string"},
                "center": {
                    "type": "array",
                    "items": {"type": "number"},
                    "minItems": 3,
                    "maxItems": 3,
                    "description": "Mittelpunkt der Loch-Oeffnung [x, y, z].",
                },
                "diameter": {
                    "type": "number",
                    "description": "Lochdurchmesser in mm.",
                },
                "direction": {
                    "type": "array",
                    "items": {"type": "number"},
                    "minItems": 3,
                    "maxItems": 3,
                    "default": [0, 0, -1],
                    "description": "Schnittrichtung in das Objekt hinein.",
                },
                "depth": {
                    "type": "number",
                    "default": 0,
                    "description": "Tiefe fuer Sackloecher; bei through=true ignoriert.",
                },
                "through": {
                    "type": "boolean",
                    "default": True,
                    "description": "Durchgangsloch statt Sackloch.",
                },
            },
            "required": ["object_id", "center", "diameter"],
        },
    },
    {
        "name": "create_slot",
        "description": (
            "Schneidet eine Nut, ein Langloch oder einen Schlitz in ein "
            "Brep-/Solid-Objekt. ``center`` ist die Mitte der Oeffnung, "
            "``slot_axis`` die Laengsrichtung auf der Oberflaeche, und "
            "``direction`` zeigt in das Objekt hinein. Mit "
            "``through=true`` wird ein Durchgang erzeugt; sonst nutzt das "
            "Tool ``depth`` als Sacknut-Tiefe. ``end_shape=\"square\"`` "
            "(Default) macht GERADE Enden — passend zu einer Moebel-Nut, "
            "die ueber die ganze Laenge einer Flaeche laeuft. "
            "``end_shape=\"rounded\"`` macht halbkreisfoermige Enden — "
            "passend zu einem klassischen Langloch (z.B. Schraubenschlitz)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "object_id": {"type": "string"},
                "center": {
                    "type": "array",
                    "items": {"type": "number"},
                    "minItems": 3,
                    "maxItems": 3,
                    "description": "Mittelpunkt der Slot-Oeffnung [x, y, z].",
                },
                "length": {
                    "type": "number",
                    "description": "Gesamtlaenge des Slots in mm.",
                },
                "width": {
                    "type": "number",
                    "description": "Breite bzw. Endradius-Durchmesser in mm.",
                },
                "slot_axis": {
                    "type": "array",
                    "items": {"type": "number"},
                    "minItems": 3,
                    "maxItems": 3,
                    "default": [1, 0, 0],
                    "description": "Laengsrichtung des Slots auf der Oberflaeche.",
                },
                "direction": {
                    "type": "array",
                    "items": {"type": "number"},
                    "minItems": 3,
                    "maxItems": 3,
                    "default": [0, 0, -1],
                    "description": "Schnittrichtung in das Objekt hinein.",
                },
                "depth": {
                    "type": "number",
                    "default": 0,
                    "description": "Tiefe fuer Sacknuten; bei through=true ignoriert.",
                },
                "through": {
                    "type": "boolean",
                    "default": True,
                    "description": "Durchgang statt Sacknut.",
                },
                "end_shape": {
                    "type": "string",
                    "enum": ["square", "rounded"],
                    "default": "square",
                    "description": (
                        "Form der Slot-Enden. ``square`` = gerade Enden "
                        "(Moebel-Nut, Default). ``rounded`` = "
                        "halbkreisfoermig (Langloch/Obround)."
                    ),
                },
            },
            "required": ["object_id", "center", "length", "width"],
        },
    },
    {
        # ===== 7. Inspektion / Szene lesen =====
        "name": "get_scene_info",
        "description": (
            "Liefert einen kompakten Ueberblick ueber die Rhino-Szene mit "
            "Layern, Objektanzahlen und Beispielobjekten."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "max_objects_per_layer": {"type": "integer", "default": 5},
            },
            "required": [],
        },
    },
    {
        "name": "get_layer_info",
        "description": (
            "Liefert Details zu einem Layer oder zu allen Layern, optional "
            "inklusive einer begrenzten Objektliste."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "layer_name": {"type": "string", "default": ""},
                "include_objects": {"type": "boolean", "default": True},
                "max_objects": {"type": "integer", "default": 50},
            },
            "required": [],
        },
    },
    {
        "name": "get_object_info",
        "description": (
            "Liefert detaillierte Informationen zu einem oder mehreren "
            "Objekten anhand ihrer GUIDs. Fuer Breps/Polysurfaces enthaelt "
            "die Antwort auch ``brep.is_solid``, ``brep.face_count`` und "
            "``brep.naked_edge_count`` zum Pruefen auf offene Kanten."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "object_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "include_user_text": {"type": "boolean", "default": True},
            },
            "required": ["object_ids"],
        },
    },
    {
        "name": "get_brep_component_info",
        "description": (
            "Liefert Details zu einer einzelnen Brep-Komponente eines "
            "Objekts: bei ``component_type='edge'`` z. B. Endpunkte, "
            "Tangente, Laenge und Nachbarflaechen; bei "
            "``component_type='face'`` z. B. Mittelpunkt, Normale, "
            "Planaritaet und angrenzende Kanten."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "object_id": {"type": "string"},
                "component_type": {
                    "type": "string",
                    "enum": ["edge", "face"],
                },
                "component_index": {"type": "integer"},
            },
            "required": ["object_id", "component_type", "component_index"],
        },
    },
    {
        "name": "resolve_reference",
        "description": (
            "Loest eine natuerlichsprachliche Referenz lesend in Rhino-"
            "Objekte, Brep-Flaechen oder Brep-Kanten auf. Beispiele: "
            "'die obere Kante', 'rechte Flaeche', 'selektiertes Objekt', "
            "'der kleine Wuerfel oben'. Nutze dieses Tool, bevor du bei "
            "unklaren Objekt-/Komponentenbezeichnungen raetst. Wenn der "
            "Top-Match unsicher wirkt, frage den Designer oder nutze Picking."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Die Referenzbeschreibung des Designers.",
                },
                "component_type": {
                    "type": "string",
                    "enum": ["auto", "object", "face", "edge"],
                    "default": "auto",
                    "description": (
                        "Optionaler Zieltyp. 'auto' leitet ihn aus der "
                        "Query ab."
                    ),
                },
                "scope_object_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "default": [],
                    "description": (
                        "Optional: auf diese Objekte beschraenken. Ohne "
                        "Scope werden bei 'selektiert/dieses' die aktuell "
                        "selektierten Objekte bevorzugt, sonst die Szene."
                    ),
                },
                "max_results": {"type": "integer", "default": 5},
            },
            "required": ["query"],
        },
    },
    {
        # ===== 8. Brep-Flaechen-/Kanten-Edits =====
        "name": "resize_box_face",
        "description": (
            "Verschiebt genau eine Flaeche einer als ``primitive_box`` "
            "markierten Box entlang ihrer eigenen Aussennormale um "
            "``distance``. Dabei wird nicht generisch ein Brep editiert, "
            "sondern das gespeicherte Box-Rezept (x/y/z/width/depth/height) "
            "aktualisiert und die Box deterministisch neu aufgebaut."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "object_id": {"type": "string"},
                "face_index": {"type": "integer"},
                "distance": {"type": "number", "default": 1.0},
            },
            "required": ["object_id", "face_index", "distance"],
        },
    },
    {
        "name": "resize_cylinder_face",
        "description": (
            "Verschiebt genau eine Flaeche eines als ``primitive_cylinder`` "
            "markierten vertikalen Zylinders. Deckflaechen veraendern die "
            "Hoehe, die Mantelflaeche veraendert den Radius. Der Zylinder "
            "wird dabei deterministisch aus seinem gespeicherten Rezept neu "
            "aufgebaut."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "object_id": {"type": "string"},
                "face_index": {"type": "integer"},
                "distance": {"type": "number", "default": 1.0},
            },
            "required": ["object_id", "face_index", "distance"],
        },
    },
    {
        "name": "resize_extrusion_face",
        "description": (
            "Verschiebt genau eine Endkappe einer als ``primitive_extrusion`` "
            "markierten geraden Extrusion. Die Extrusionslaenge wird dabei "
            "deterministisch ueber das gespeicherte Achs-Rezept geaendert. "
            "Seitliche Flaechen sind fuer dieses Tool nicht erlaubt."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "object_id": {"type": "string"},
                "face_index": {"type": "integer"},
                "distance": {"type": "number", "default": 1.0},
            },
            "required": ["object_id", "face_index", "distance"],
        },
    },
    {
        "name": "fillet_brep_edge",
        "description": (
            "Rundet eine oder mehrere Brep-Kanten eines Objekts "
            "``object_id`` mit ``radius`` ab. Uebergib entweder "
            "``edge_index`` fuer eine einzelne Kante oder ``edge_indices`` "
            "fuer mehrere Kanten desselben Original-Breps in einem "
            "gemeinsamen Fillet-Schritt."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "object_id": {"type": "string"},
                "edge_index": {"type": "integer"},
                "edge_indices": {
                    "type": "array",
                    "items": {"type": "integer"},
                },
                "radius": {"type": "number", "default": 1.0},
                "rail_type": {
                    "type": "string",
                    "enum": [
                        "rolling_ball",
                        "distance_from_edge",
                        "distance_between_rails",
                    ],
                    "default": "rolling_ball",
                },
            },
            "required": ["object_id", "radius"],
        },
    },
    {
        "name": "round_edges_by_rule",
        "description": (
            "Rundet mehrere Brep-Kanten eines Objekts nach einer semantischen "
            "Regel ab, z.B. alle oberen, unteren, vertikalen, horizontalen, "
            "vorderen, hinteren, linken, rechten oder alle ausser unteren "
            "Kanten. Nutze dies fuer Sprache wie 'alle oberen Kanten "
            "abrunden' statt einzelne Edge-Indizes zu raten."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "object_id": {"type": "string"},
                "rule": {
                    "type": "string",
                    "enum": [
                        "all",
                        "all_except_bottom",
                        "top",
                        "bottom",
                        "vertical",
                        "horizontal",
                        "x_edges",
                        "y_edges",
                        "z_edges",
                        "front",
                        "back",
                        "left",
                        "right",
                        "top_front",
                        "top_back",
                        "top_left",
                        "top_right",
                    ],
                    "description": "Welche Kanten abgerundet werden sollen.",
                    "default": "top",
                },
                "radius": {"type": "number", "default": 1.0},
                "rail_type": {
                    "type": "string",
                    "enum": [
                        "rolling_ball",
                        "distance_from_edge",
                        "distance_between_rails",
                    ],
                    "default": "rolling_ball",
                },
            },
            "required": ["object_id", "rule", "radius"],
        },
    },
    {
        "name": "chamfer_brep_edge",
        "description": (
            "Fast eine oder mehrere Brep-Kanten eines Objekts "
            "``object_id`` mit ``distance`` an. Uebergib entweder "
            "``edge_index`` fuer eine einzelne Kante oder ``edge_indices`` "
            "fuer mehrere Kanten desselben Original-Breps in einem "
            "gemeinsamen Chamfer-Schritt."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "object_id": {"type": "string"},
                "edge_index": {"type": "integer"},
                "edge_indices": {
                    "type": "array",
                    "items": {"type": "integer"},
                },
                "distance": {"type": "number", "default": 1.0},
                "rail_type": {
                    "type": "string",
                    "enum": [
                        "distance_from_edge",
                        "rolling_ball",
                        "distance_between_rails",
                    ],
                    "default": "distance_from_edge",
                },
            },
            "required": ["object_id", "distance"],
        },
    },
    {
        "name": "move_brep_face_along_normal",
        "description": (
            "Verschiebt genau eine planare Brep-Flaeche ``face_index`` eines "
            "Objekts ``object_id`` entlang ihrer Flaechennormale um "
            "``distance`` und rekonstruiert angrenzende planare Seiten."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "object_id": {"type": "string"},
                "face_index": {"type": "integer"},
                "distance": {"type": "number", "default": 1.0},
            },
            "required": ["object_id", "face_index", "distance"],
        },
    },
    {
        "name": "move_brep_face_in_direction",
        "description": (
            "Verschiebt genau eine planare Brep-Flaeche ``face_index`` eines "
            "Objekts ``object_id`` entlang eines expliziten Vektors "
            "``(dx, dy, dz)`` und rekonstruiert angrenzende planare Seiten."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "object_id": {"type": "string"},
                "face_index": {"type": "integer"},
                "dx": {"type": "number", "default": 0.0},
                "dy": {"type": "number", "default": 0.0},
                "dz": {"type": "number", "default": 0.0},
            },
            "required": ["object_id", "face_index", "dx", "dy", "dz"],
        },
    },
    {
        # ===== 9. SubD =====
        "name": "create_subd_box",
        "description": (
            "Erstellt eine editierbare SubD-Box mit Ursprung ``(x, y, z)``, "
            "Abmessungen ``width``/``depth``/``height`` und Face-Aufloesung "
            "``x_faces``/``y_faces``/``z_faces``."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "x": {"type": "number", "default": 0},
                "y": {"type": "number", "default": 0},
                "z": {"type": "number", "default": 0},
                "width": {"type": "number", "default": 10},
                "depth": {"type": "number", "default": 10},
                "height": {"type": "number", "default": 10},
                "x_faces": {"type": "integer", "default": 2},
                "y_faces": {"type": "integer", "default": 2},
                "z_faces": {"type": "integer", "default": 2},
                "name": {"type": "string", "default": "SubDBox"},
            },
            "required": [],
        },
    },
    {
        "name": "create_subd_sphere",
        "description": (
            "Erstellt eine editierbare SubD-Kugel am Zentrum "
            "``(center_x, center_y, center_z)``."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "center_x": {"type": "number", "default": 0},
                "center_y": {"type": "number", "default": 0},
                "center_z": {"type": "number", "default": 0},
                "radius": {"type": "number", "default": 5},
                "subdivisions": {"type": "integer", "default": 3},
                "name": {"type": "string", "default": "SubDSphere"},
            },
            "required": [],
        },
    },
    {
        "name": "create_subd_cylinder",
        "description": (
            "Erstellt einen editierbaren SubD-Zylinder mit Basismittelpunkt "
            "``(base_x, base_y, base_z)``, Radius und Hoehe."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "base_x": {"type": "number", "default": 0},
                "base_y": {"type": "number", "default": 0},
                "base_z": {"type": "number", "default": 0},
                "radius": {"type": "number", "default": 5},
                "height": {"type": "number", "default": 10},
                "radial_faces": {"type": "integer", "default": 8},
                "height_faces": {"type": "integer", "default": 4},
                "cap": {"type": "boolean", "default": True},
                "name": {"type": "string", "default": "SubDCylinder"},
            },
            "required": [],
        },
    },
    {
        "name": "mesh_to_subd",
        "description": "Konvertiert ein Mesh ``mesh_id`` in ein SubD-Objekt.",
        "input_schema": {
            "type": "object",
            "properties": {
                "mesh_id": {"type": "string"},
                "name": {"type": "string", "default": "MeshToSubD"},
            },
            "required": ["mesh_id"],
        },
    },
    {
        "name": "quad_remesh_to_subd",
        "description": (
            "Fuehrt QuadRemesh auf ``object_id`` aus und konvertiert das "
            "Ergebnis danach zu SubD."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "object_id": {"type": "string"},
                "target_quad_count": {"type": "integer", "default": 500},
                "adaptive_quad_count": {"type": "boolean", "default": True},
                "name": {"type": "string", "default": "QuadRemeshSubD"},
            },
            "required": ["object_id"],
        },
    },
    {
        "name": "subd_to_nurbs",
        "description": "Konvertiert ein SubD ``subd_id`` in NURBS/Brep.",
        "input_schema": {
            "type": "object",
            "properties": {
                "subd_id": {"type": "string"},
                "name": {"type": "string", "default": "SubDToNURBS"},
            },
            "required": ["subd_id"],
        },
    },
    {
        "name": "subd_to_mesh",
        "description": "Konvertiert ein SubD ``subd_id`` in ein Mesh.",
        "input_schema": {
            "type": "object",
            "properties": {
                "subd_id": {"type": "string"},
                "density": {"type": "integer", "default": 1},
                "name": {"type": "string", "default": "SubDToMesh"},
            },
            "required": ["subd_id"],
        },
    },
    {
        "name": "get_subd_info",
        "description": (
            "Liefert Face-, Edge- und Vertex-Counts sowie Bounding-Box und "
            "Crease-Informationen fuer ein SubD."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "subd_id": {"type": "string"},
            },
            "required": ["subd_id"],
        },
    },
    {
        "name": "subd_crease_edges",
        "description": (
            "Setzt oder entfernt Creases auf SubD-Kanten anhand ihrer "
            "``edge_indices``."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "subd_id": {"type": "string"},
                "edge_indices": {
                    "type": "array",
                    "items": {"type": "integer"},
                },
                "crease": {"type": "boolean", "default": True},
            },
            "required": ["subd_id", "edge_indices"],
        },
    },
    {
        "name": "subd_set_vertex_position",
        "description": (
            "Verschiebt einen SubD-Control-Vertex ``vertex_index`` auf die "
            "Position ``(x, y, z)``."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "subd_id": {"type": "string"},
                "vertex_index": {"type": "integer"},
                "x": {"type": "number"},
                "y": {"type": "number"},
                "z": {"type": "number"},
            },
            "required": ["subd_id", "vertex_index", "x", "y", "z"],
        },
    },
    {
        "name": "subd_extrude_faces",
        "description": (
            "Extrudiert SubD-Flaechen entlang ihrer Normalen fuer die "
            "angegebenen ``face_indices``."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "subd_id": {"type": "string"},
                "face_indices": {
                    "type": "array",
                    "items": {"type": "integer"},
                },
                "distance": {"type": "number", "default": 2},
                "name": {"type": "string", "default": "SubDExtrude"},
            },
            "required": ["subd_id", "face_indices"],
        },
    },
    {
        "name": "subd_offset_faces",
        "description": (
            "Insettet oder outsettet SubD-Flaechen fuer die angegebenen "
            "``face_indices``."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "subd_id": {"type": "string"},
                "face_indices": {
                    "type": "array",
                    "items": {"type": "integer"},
                },
                "distance": {"type": "number", "default": -0.5},
            },
            "required": ["subd_id", "face_indices"],
        },
    },
    {
        "name": "subd_subdivide",
        "description": "Unterteilt ein SubD ``subd_id`` um ``levels`` Stufen weiter.",
        "input_schema": {
            "type": "object",
            "properties": {
                "subd_id": {"type": "string"},
                "levels": {"type": "integer", "default": 1},
            },
            "required": ["subd_id"],
        },
    },
    {
        # ===== 10. Selektion, Layer, Metadaten =====
        "name": "get_layers",
        "description": "Liefert eine kompakte Liste aller Layer im Dokument.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_scene_objects_with_metadata",
        "description": (
            "Liefert Objekte mit Metadaten und optionalen Filtern fuer Layer, "
            "Name oder ``short_id``."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "filters": {
                    "type": "object",
                    "properties": {
                        "layer": {"type": "string"},
                        "name": {"type": "string"},
                        "short_id": {"type": "string"},
                    },
                },
                "metadata_fields": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_selected",
        "description": (
            "Liefert Details zu aktuell selektierten Objekten, inklusive "
            "SubD-Subselektion wenn vorhanden. Verwenden, wenn der Nutzer "
            "auf 'dieses Objekt', 'die ausgewaehlte Flaeche' oder "
            "'die selektierte Kurve' verweist."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "select_objects",
        "description": (
            "Setzt die Rhino-Selektion auf eine Liste von Objekt-IDs, damit "
            "der Designer im Viewport sieht, welches Objekt du meinst. "
            "Nutze dies nach `resolve_reference`, wenn du eine Referenz "
            "visuell bestaetigen oder kommunizieren willst. Es veraendert "
            "keine Geometrie."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "object_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "description": "GUIDs der zu selektierenden Objekte.",
                },
                "replace": {
                    "type": "boolean",
                    "default": True,
                    "description": "Vorherige Auswahl zuerst aufheben.",
                },
                "zoom": {
                    "type": "boolean",
                    "default": False,
                    "description": "Optional auf die neue Auswahl zoomen.",
                },
            },
            "required": ["object_ids"],
        },
    },
    {
        # ===== 11. Verlauf / Reversibilitaet =====
        "name": "list_backups",
        "description": (
            "Listet verfuegbare Backups auf dem Archive-Layer auf, optional "
            "gefiltert nach Objektname."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "object_name": {"type": "string", "default": ""},
            },
            "required": [],
        },
    },
    {
        "name": "restore_object",
        "description": (
            "Stellt ein Objekt aus dem Archive-Layer wieder her. Entweder "
            "``backup_id`` (GUID aus list_backups) oder ``object_name`` "
            "angeben. ``object_name`` matcht sowohl den Original-Namen "
            "(z.B. ``\"Box\"`` -> jeweils das neueste Backup dieses Objekts) "
            "als auch den versionierten Display-Namen aus list_backups "
            "(z.B. ``\"Box_v2\"`` -> genau diese Version)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "backup_id": {"type": "string", "default": ""},
                "object_name": {"type": "string", "default": ""},
            },
            "required": [],
        },
    },
    {
        "name": "undo_last_action",
        "description": (
            "Macht den letzten protokollierten Modellierschritt rueckgaengig, "
            "indem neu erzeugte Objekte geloescht und gesicherte Backups "
            "wiederhergestellt werden."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "redo_last_action",
        "description": (
            "Stellt den zuletzt rueckgaengig gemachten protokollierten "
            "Modellierschritt wieder her."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        # ===== 12. Spaetere Ergaenzungen (Geometrie/Kurven/Flaechen) =====
        "name": "create_arc",
        "description": (
            "Erstellt einen Bogen in der XY-Ebene am Zentrum "
            "``(center_x, center_y, center_z)`` mit ``radius`` sowie "
            "``start_angle`` und ``end_angle`` in Grad."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "center_x": {"type": "number", "default": 0},
                "center_y": {"type": "number", "default": 0},
                "center_z": {"type": "number", "default": 0},
                "radius": {"type": "number", "default": 5},
                "start_angle": {"type": "number", "default": 0},
                "end_angle": {"type": "number", "default": 90},
                "name": {"type": "string", "default": "Arc"},
            },
            "required": [],
        },
    },
    {
        "name": "create_torus",
        "description": (
            "Erstellt einen Torus am Zentrum ``(center_x, center_y, center_z)`` "
            "mit ``major_radius`` und ``minor_radius``."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "center_x": {"type": "number", "default": 0},
                "center_y": {"type": "number", "default": 0},
                "center_z": {"type": "number", "default": 0},
                "major_radius": {"type": "number", "default": 10},
                "minor_radius": {"type": "number", "default": 2},
                "name": {"type": "string", "default": "Torus"},
            },
            "required": [],
        },
    },
    {
        "name": "create_interpolated_curve",
        "description": (
            "Erstellt eine Interpolationskurve durch die Punktliste "
            "``[[x,y,z], ...]``."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "points": {
                    "type": "array",
                    "items": {
                        "type": "array",
                        "items": {"type": "number"},
                    },
                },
                "degree": {"type": "integer", "default": 3},
                "name": {"type": "string", "default": "InterpCurve"},
            },
            "required": ["points"],
        },
    },
    {
        "name": "create_control_point_curve",
        "description": (
            "Erstellt eine NURBS-Kurve aus Kontrollpunkten "
            "``[[x,y,z], ...]``."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "points": {
                    "type": "array",
                    "items": {
                        "type": "array",
                        "items": {"type": "number"},
                    },
                },
                "degree": {"type": "integer", "default": 3},
                "name": {"type": "string", "default": "CPCurve"},
            },
            "required": ["points"],
        },
    },
    {
        "name": "explode_curves",
        "description": "Zerlegt eine Polycurve ``curve_id`` in ihre Segmente.",
        "input_schema": {
            "type": "object",
            "properties": {
                "curve_id": {"type": "string"},
            },
            "required": ["curve_id"],
        },
    },
    {
        "name": "extend_curve",
        "description": (
            "Verlaengert eine Kurve ``curve_id`` um ``length``. "
            "``side``: 0=Start, 1=Ende, 2=beide. "
            "``extension_type``: 0=Linie, 1=Bogen, 2=smooth."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "curve_id": {"type": "string"},
                "length": {"type": "number", "default": 5},
                "side": {"type": "integer", "default": 2},
                "extension_type": {"type": "integer", "default": 0},
            },
            "required": ["curve_id"],
        },
    },
    {
        "name": "rebuild_curve",
        "description": (
            "Baut eine Kurve ``curve_id`` mit neuer Punktanzahl und neuem "
            "Grad neu auf."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "curve_id": {"type": "string"},
                "point_count": {"type": "integer", "default": 10},
                "degree": {"type": "integer", "default": 3},
            },
            "required": ["curve_id"],
        },
    },
    {
        "name": "project_curve_to_surface",
        "description": (
            "Projiziert eine Kurve ``curve_id`` entlang ``direction`` auf "
            "eine Flaeche ``surface_id``."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "curve_id": {"type": "string"},
                "surface_id": {"type": "string"},
                "direction": {
                    "type": "array",
                    "items": {"type": "number"},
                    "default": [0, 0, -1],
                },
                "name": {"type": "string", "default": "ProjectedCurve"},
            },
            "required": ["curve_id", "surface_id"],
        },
    },
    {
        "name": "sweep2",
        "description": (
            "Sweep einer oder mehrerer Querschnittskurven entlang zweier "
            "Rail-Kurven ``rail1_id`` und ``rail2_id``. Erfordert genau zwei "
            "separate Rails plus mindestens eine weitere Querschnittskurve. "
            "Nicht fuer den Fall verwenden, dass nur zwei Profilkurven "
            "vorliegen; dafuer ist meist ``loft_curves`` passender."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "rail1_id": {"type": "string"},
                "rail2_id": {"type": "string"},
                "cross_section_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                },
                "name": {"type": "string", "default": "Sweep2"},
            },
            "required": ["rail1_id", "rail2_id", "cross_section_ids"],
        },
    },
    {
        "name": "offset_surface",
        "description": (
            "Offsettet eine Flaeche oder Polysurface ``surface_id`` um "
            "``distance``."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "surface_id": {"type": "string"},
                "distance": {"type": "number", "default": 1},
                "name": {"type": "string", "default": "OffsetSrf"},
            },
            "required": ["surface_id"],
        },
    },
    {
        "name": "thicken_surface_to_solid",
        "description": (
            "Verdickt eine offene Flaeche oder Polysurface ``surface_id`` um "
            "``distance`` und versucht, daraus direkt einen geschlossenen "
            "messbaren Solid zu erzeugen. Verwenden fuer Shell-/Wandstaerke-"
            "Faelle statt manueller Offset+Loft+Join-Ketten."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "surface_id": {"type": "string"},
                "distance": {"type": "number", "default": 1},
                "both_sides": {"type": "boolean", "default": False},
                "extend": {"type": "boolean", "default": False},
                "delete_input": {"type": "boolean", "default": False},
                "name": {"type": "string", "default": "ThickenedSolid"},
            },
            "required": ["surface_id", "distance"],
        },
    },
    {
        # ===== 13. Grasshopper =====
        "name": "execute_gh_code",
        "description": "Fuehrt Python-Code im Grasshopper-Server aus.",
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {"type": "string"},
            },
            "required": ["code"],
        },
    },
    {
        "name": "get_gh_context",
        "description": "Liefert den aktuellen Grasshopper-Definitiongraphen.",
        "input_schema": {
            "type": "object",
            "properties": {
                "simplified": {"type": "boolean", "default": False},
            },
            "required": [],
        },
    },
    {
        "name": "get_objects",
        "description": (
            "Liefert Informationen zu bestimmten Grasshopper-Komponenten "
            "anhand ihrer ``instance_guids``."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "instance_guids": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "simplified": {"type": "boolean", "default": False},
                "context_depth": {"type": "integer", "default": 0},
            },
            "required": ["instance_guids"],
        },
    },
    {
        "name": "get_gh_selected",
        "description": "Liefert Informationen zu aktuell selektierten GH-Komponenten.",
        "input_schema": {
            "type": "object",
            "properties": {
                "simplified": {"type": "boolean", "default": False},
                "context_depth": {"type": "integer", "default": 0},
            },
            "required": [],
        },
    },
    {
        "name": "update_script",
        "description": (
            "Aktualisiert eine Grasshopper-Script-Komponente anhand ihrer "
            "``instance_guid`` — Code und/oder Input/Output-Parameter. "
            "Wenn der Script-Code Variablen wie ``seat_w`` liest, MUSST du "
            "passende ``param_definitions`` mitgeben, sonst hat die "
            "Komponente keine Inputs und das Script wirft "
            "'name ... is not defined'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "instance_guid": {"type": "string"},
                "code": {"type": "string"},
                "description": {"type": "string"},
                "message_to_user": {"type": "string"},
                "param_definitions": {
                    "type": "array",
                    "description": (
                        "Definiert die Parameter der Script-Komponente. "
                        "Ein Eintrag pro Input/Output. ``name``/``nickName`` "
                        "ist der Variablenname im Script-Code. ``type`` ist "
                        "'input' (Standard) oder 'output'. ``typehint`` z.B. "
                        "'float', 'int', 'bool', 'str', 'point', 'curve', "
                        "'brep', 'generic'. Beispiel-Eintrag: "
                        "{'type':'input','name':'seat_w','nickName':'seat_w',"
                        "'typehint':'float'}."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "enum": ["input", "output"],
                            },
                            "name": {"type": "string"},
                            "nickName": {"type": "string"},
                            "typehint": {"type": "string"},
                            "access": {
                                "type": "string",
                                "enum": ["item", "list", "tree"],
                            },
                            "description": {"type": "string"},
                        },
                        "required": ["name"],
                    },
                },
            },
            "required": ["instance_guid"],
        },
    },
    {
        "name": "update_script_with_code_reference",
        "description": (
            "Stellt eine GH-Script-Komponente auf extern referenzierten "
            "Python-Code um."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "instance_guid": {"type": "string"},
                "file_path": {"type": "string"},
                "param_definitions": {
                    "type": "array",
                    "items": {"type": "object"},
                },
                "description": {"type": "string"},
                "name": {"type": "string"},
                "force_code_reference": {"type": "boolean", "default": False},
            },
            "required": ["instance_guid"],
        },
    },
    {
        "name": "expire_and_get_info",
        "description": (
            "Expiriert eine GH-Komponente und liefert danach ihren "
            "aktuellen Zustand zurueck."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "instance_guid": {"type": "string"},
            },
            "required": ["instance_guid"],
        },
    },
    {
        "name": "add_component",
        "description": (
            "Fuegt eine native Grasshopper-Komponente zur Canvas hinzu und "
            "verbindet sie optional mit vorhandenen Komponenten."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "component_name": {"type": "string"},
                "position_x": {"type": "number", "default": 0},
                "position_y": {"type": "number", "default": 0},
                "nickname": {"type": "string"},
                "connections": {
                    "type": "array",
                    "items": {"type": "object"},
                },
            },
            "required": ["component_name"],
        },
    },
    {
        "name": "clear_gh_session",
        "description": (
            "Loescht alle Grasshopper-Komponenten, die seit dem letzten "
            "GH-Connect von der KI angelegt wurden (slider, scripts, native "
            "Komponenten, alles was via add_component / execute_gh_code / "
            "update_script entstand). Vom Nutzer manuell hinzugefuegte "
            "Komponenten und die MCP-Server-Komponente bleiben erhalten. "
            "Aufrufen wenn der Designer 'neues Design', 'Grasshopper "
            "aufraeumen' oder aehnliches sagt — oder zwischen zwei voellig "
            "unterschiedlichen Bauten, damit der Canvas nicht zugemuellt "
            "wird. Nach dem Aufruf solltest du auch ``clear_parameters`` "
            "rufen, falls noch Plugin-Slider zu jetzt entfernten "
            "GH-Slidern existieren."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "arrange_gh_layout",
        "description": (
            "Ordnet alle AI-erzeugten Grasshopper-Komponenten automatisch "
            "in einem sauberen Datenfluss-Layout an: topologische "
            "Sortierung nach Abhaengigkeit, dann links-nach-rechts "
            "platzieren (Slider/Eingaben in Spalte 0, Berechnungen Spalte "
            "1, 2, ..., Outputs ganz rechts). Innerhalb einer Spalte wird "
            "vertikal gestapelt. Nutzer-eigene Komponenten und die "
            "MCP-Server-Komponente werden NICHT bewegt. Standardmaessig "
            "landet das Layout rechts vom MCP-Block in einer farbig "
            "markierten 'AI Arbeitszone'. Rufe das am Ende eines Builds "
            "auf, wenn du mehr als 2-3 Komponenten erzeugt hast — oder "
            "wenn der Designer sagt 'mach das mal aufgeraeumt' / 'sortier "
            "das' / 'alignment'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "base_x": {
                    "type": "number",
                    "default": 0,
                    "description": "Optional. 0/leer = automatisch rechts vom MCP-Block.",
                },
                "base_y": {
                    "type": "number",
                    "description": "Optional. Leer = obere Kante des bestehenden MCP-/User-Bereichs.",
                },
                "step_x": {"type": "number", "default": 260},
                "step_y": {"type": "number", "default": 110},
            },
        },
    },
    {
        "name": "expose_parameters",
        "description": (
            "Blendet dem Designer Schieberegler ein, mit denen er bestehende "
            "Objekte direkt manipulieren kann (ohne neuen Chat-Prompt). "
            "WICHTIG: vorher kurz im Chat anbieten und auf Bestaetigung "
            "warten - Slider sollen nicht ungefragt erscheinen. Pro Slider "
            "wird angegeben: aktueller Wert, Min/Max, Anzeige-Einheit und "
            "welche Transformation(en) der Slider ausloest (move_axis / "
            "scale_axis / scale_uniform / rotate_axis / gh_slider). Ein "
            "Slider kann eine einzelne `action` ODER eine `actions`-Liste "
            "mit mehreren koordinierten Teilaktionen haben. Ein "
            "Slider-Move loest KEINEN weiteren Chat-Turn aus - die Aktion "
            "wird deterministisch ausgefuehrt. `gh_slider` setzt einen "
            "Grasshopper Number Slider und ist fuer parametrische Rebuilds "
            "von Loft/Sweep-Formen gedacht. Funktioniert nur fuer "
            "axis-aligned Transformationen oder GH-getriebene Parameter; "
            "fuer Form-Aenderungen ohne Parametrik nutze stattdessen "
            "normale Tool-Calls im Chat."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "parameters": {
                    "type": "array",
                    "description": (
                        "Liste der Slider. Jeder Eintrag hat name, "
                        "current/min/max, display_unit + display_factor, "
                        "und entweder eine action-Definition oder eine "
                        "actions-Liste."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "current": {"type": "number"},
                            "min": {"type": "number"},
                            "max": {"type": "number"},
                            "step": {"type": "number"},
                            "display_unit": {
                                "type": "string",
                                "default": "",
                            },
                            "display_factor": {
                                "type": "number",
                                "default": 1.0,
                            },
                            "action": {
                                "type": "object",
                                "properties": {
                                    "type": {
                                        "type": "string",
                                        "enum": [
                                            "move_axis",
                                            "scale_axis",
                                            "scale_uniform",
                                            "rotate_axis",
                                            "gh_slider",
                                        ],
                                    },
                                    "target_object_ids": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    },
                                    "axis": {
                                        "type": "string",
                                        "enum": ["x", "y", "z"],
                                        "default": "z",
                                    },
                                    "origin": {
                                        "type": "array",
                                        "items": {"type": "number"},
                                        "default": [0, 0, 0],
                                    },
                                    "instance_guid": {
                                        "type": "string",
                                        "description": (
                                            "Bei `gh_slider`: GUID des "
                                            "Grasshopper Number Sliders."
                                        ),
                                    },
                                },
                                "required": ["type"],
                            },
                            "actions": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "type": {
                                            "type": "string",
                                            "enum": [
                                                "move_axis",
                                                "scale_axis",
                                                "scale_uniform",
                                                "rotate_axis",
                                                "gh_slider",
                                            ],
                                        },
                                        "target_object_ids": {
                                            "type": "array",
                                            "items": {"type": "string"},
                                        },
                                        "axis": {
                                            "type": "string",
                                            "enum": ["x", "y", "z"],
                                            "default": "z",
                                        },
                                        "origin": {
                                            "type": "array",
                                            "items": {"type": "number"},
                                            "default": [0, 0, 0],
                                        },
                                        "instance_guid": {
                                            "type": "string",
                                            "description": (
                                                "Bei `gh_slider`: GUID des "
                                                "Grasshopper Number Sliders."
                                            ),
                                        },
                                    },
                                    "required": ["type"],
                                },
                            },
                        },
                        "required": ["name", "current", "min", "max"],
                    },
                },
            },
            "required": ["parameters"],
        },
    },
    {
        "name": "clear_parameters",
        "description": (
            "Schliesst das Slider-Panel und entfernt alle aktiven Parameter. "
            "Nutze dies, wenn der Designer keine Slider mehr will oder du "
            "Slider zu alter Geometrie aufgesetzt hattest."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "create_variant",
        "description": (
            "Beginnt eine neue Designvariante. Erzeugt einen eigenen Layer "
            "``Variant_<name>`` und setzt ihn als aktive Variante. Mit "
            "``copy_active=true`` wird die aktuelle Active-Layer-Geometrie "
            "als Ausgangspunkt kopiert."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Kurzer Name der Variante, z. B. 'Schlank'.",
                },
                "description": {
                    "type": "string",
                    "description": "Optionaler Untertitel fuer die Gallery.",
                    "default": "",
                },
                "copy_active": {
                    "type": "boolean",
                    "description": (
                        "Wenn true, kopiert das Plugin die Active-Layer-Geometrie "
                        "als Ausgangspunkt in die neue Variante."
                    ),
                    "default": True,
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "select_variant",
        "description": (
            "Wechselt die aktive Variante. Macht den passenden Variant-Layer "
            "sichtbar, blendet die anderen aus und cleared vorhandene Slider."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Name der Variante wie bei create_variant.",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "finish_variants",
        "description": (
            "Schliesst den Variantenlauf ab, captured Thumbnails fuer alle "
            "Varianten und zeigt sie in der Gallery an."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "delete_variant",
        "description": (
            "Entfernt eine Variante und archiviert ihre Geometrie. Falls die "
            "aktive Variante geloescht wird, wird automatisch eine andere "
            "verbleibende Variante aktiv."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "clear_variants",
        "description": (
            "Loescht alle Varianten, archiviert ihre Geometrie und schaltet "
            "auf den normalen Active-Layer zurueck."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
]
