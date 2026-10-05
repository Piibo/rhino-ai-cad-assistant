"""Code templates: inspection operations. See code_templates.py."""
from __future__ import annotations

from typing import List

from ._codegen_base import inject_params

__all__ = [
    "get_scene_info_code",
    "get_layer_info_code",
    "get_object_info_code",
    "get_layers_code",
    "get_scene_objects_with_metadata_code",
    "get_selected_code",
    "get_brep_component_info_code",
    "resolve_reference_code",
]


def get_scene_info_code(max_objects_per_layer: int = 5) -> str:
    """Get a lightweight overview of the current Rhino scene."""
    return inject_params(limit=max_objects_per_layer) + """
all_objects = rs.AllObjects() or []
selected = rs.SelectedObjects() or []
layers = []
for idx in range(sc.doc.Layers.Count):
    layer = sc.doc.Layers[idx]
    if layer is None or layer.IsDeleted:
        continue
    obj_ids = rs.ObjectsByLayer(layer.FullPath) or []
    samples = []
    for oid in obj_ids[:_limit]:
        obj = sc.doc.Objects.Find(oid)
        geom = obj.Geometry if obj else None
        samples.append({
            "id": str(oid),
            "name": rs.ObjectName(oid) or "Unnamed",
            "type": geom.GetType().Name if geom else "Unknown",
        })
    color = layer.Color
    layers.append({
        "name": layer.FullPath,
        "visible": layer.IsVisible,
        "locked": layer.IsLocked,
        "color": [color.R, color.G, color.B],
        "object_count": len(obj_ids),
        "sample_objects": samples,
    })

scene = {
    "document_name": sc.doc.Name or "Untitled",
    "model_unit_system": str(sc.doc.ModelUnitSystem),
    "object_count": len(all_objects),
    "selected_count": len(selected),
    "layer_count": len(layers),
    "layers": layers,
}
result = json.dumps(scene, indent=2)
"""


def get_layer_info_code(
    layer_name: str = "",
    include_objects: bool = True,
    max_objects: int = 50,
) -> str:
    """Get info about one layer or all layers."""
    return inject_params(layer=layer_name, include=include_objects, limit=max_objects) + """
layers_out = []
for idx in range(sc.doc.Layers.Count):
    layer = sc.doc.Layers[idx]
    if layer is None or layer.IsDeleted:
        continue
    if _layer and layer.FullPath != _layer:
        continue
    obj_ids = rs.ObjectsByLayer(layer.FullPath) or []
    color = layer.Color
    entry = {
        "name": layer.FullPath,
        "visible": layer.IsVisible,
        "locked": layer.IsLocked,
        "color": [color.R, color.G, color.B],
        "object_count": len(obj_ids),
    }
    if _include:
        objects = []
        for oid in obj_ids[:_limit]:
            obj = sc.doc.Objects.Find(oid)
            geom = obj.Geometry if obj else None
            objects.append({
                "id": str(oid),
                "name": rs.ObjectName(oid) or "Unnamed",
                "type": geom.GetType().Name if geom else "Unknown",
                "short_id": rs.GetUserText(oid, "short_id") or "",
            })
        entry["objects"] = objects
    layers_out.append(entry)

if _layer and not layers_out:
    result = "Error: layer not found: {0}".format(_layer)
else:
    payload = layers_out[0] if _layer and len(layers_out) == 1 else layers_out
    result = json.dumps(payload, indent=2)
"""


def get_object_info_code(
    object_ids: List[str],
    include_user_text: bool = True,
) -> str:
    """Get detailed info for one or more Rhino objects."""
    return inject_params(ids=object_ids, include=include_user_text) + """
objects = []
for oid_str in _ids:
    oid = System.Guid(oid_str)
    obj = sc.doc.Objects.Find(oid)
    if obj is None:
        objects.append({"id": oid_str, "error": "object not found"})
        continue

    geom = obj.Geometry
    bbox = geom.GetBoundingBox(True) if geom else None
    info = {
        "id": str(oid),
        "name": rs.ObjectName(oid) or "Unnamed",
        "type": geom.GetType().Name if geom else "Unknown",
        "layer": rs.ObjectLayer(oid),
        "hidden": rs.IsObjectHidden(oid),
        "locked": rs.IsObjectLocked(oid),
        "selected": rs.IsObjectSelected(oid) != 0,
        "short_id": rs.GetUserText(oid, "short_id") or "",
    }
    if isinstance(geom, rg.Brep):
        naked_edges = 0
        for edge in geom.Edges:
            if edge.Valence == rg.EdgeAdjacency.Naked:
                naked_edges += 1
        info["brep"] = {
            "is_solid": geom.IsSolid,
            "is_manifold": geom.IsManifold,
            "face_count": geom.Faces.Count,
            "edge_count": geom.Edges.Count,
            "naked_edge_count": naked_edges,
        }
    elif isinstance(geom, rg.Curve):
        try:
            curve_length = geom.GetLength()
        except Exception:
            curve_length = None
        info["curve"] = {
            "is_closed": geom.IsClosed,
            "length": curve_length,
        }
    elif isinstance(geom, rg.Mesh):
        info["mesh"] = {
            "is_closed": geom.IsClosed,
            "face_count": geom.Faces.Count,
            "vertex_count": geom.Vertices.Count,
        }
    if bbox:
        info["bounding_box"] = {
            "min": [bbox.Min.X, bbox.Min.Y, bbox.Min.Z],
            "max": [bbox.Max.X, bbox.Max.Y, bbox.Max.Z],
        }
    if _include:
        user_text = {}
        keys = rs.GetUserText(oid)
        if keys:
            for key in keys:
                user_text[key] = rs.GetUserText(oid, key)
        info["user_text"] = user_text
    objects.append(info)

result = json.dumps(objects[0] if len(objects) == 1 else objects, indent=2)
"""


def get_layers_code() -> str:
    """Get a lightweight list of all layers in the document."""
    return """
layers = []
for idx in range(sc.doc.Layers.Count):
    layer = sc.doc.Layers[idx]
    if layer is None or layer.IsDeleted:
        continue
    color = layer.Color
    layers.append({
        "name": layer.FullPath,
        "visible": layer.IsVisible,
        "locked": layer.IsLocked,
        "color": [color.R, color.G, color.B],
    })
result = json.dumps({"count": len(layers), "layers": layers}, indent=2)
"""


def get_scene_objects_with_metadata_code(
    filters: dict | None = None,
    metadata_fields: List[str] | None = None,
) -> str:
    """Get object info with metadata and optional filtering."""
    return inject_params(filters=filters or {}, fields=metadata_fields or []) + """
import fnmatch

objects = []
for obj in sc.doc.Objects:
    if obj is None or obj.IsDeleted:
        continue

    oid = obj.Id
    name = rs.ObjectName(oid) or "Unnamed"
    layer = rs.ObjectLayer(oid) or ""
    short_id = rs.GetUserText(oid, "short_id") or ""

    layer_filter = _filters.get("layer")
    if layer_filter and not fnmatch.fnmatch(layer, layer_filter):
        continue
    name_filter = _filters.get("name")
    if name_filter and not fnmatch.fnmatch(name, name_filter):
        continue
    short_filter = _filters.get("short_id")
    if short_filter and short_id != short_filter:
        continue

    geom = obj.Geometry
    entry = {
        "id": str(oid),
        "name": name,
        "type": geom.GetType().Name if geom else "Unknown",
        "layer": layer,
        "short_id": short_id,
    }

    user_text = {}
    keys = rs.GetUserText(oid)
    if keys:
        for key in keys:
            if _fields and key not in _fields:
                continue
            user_text[key] = rs.GetUserText(oid, key)
    entry["metadata"] = user_text
    objects.append(entry)

result = json.dumps({"count": len(objects), "objects": objects}, indent=2)
"""


def get_selected_code() -> str:
    """Get details about the currently selected objects."""
    return """
selected = []
for obj in sc.doc.Objects.GetSelectedObjects(False, False):
    oid = obj.Id
    info = {
        "id": str(oid),
        "name": obj.Name or "Unnamed",
        "type": obj.Geometry.GetType().Name if obj.Geometry else "Unknown",
        "layer": rs.ObjectLayer(oid),
    }

    sub_vertices = []
    sub_edges = []
    sub_faces = []

    try:
        sub_objs = obj.GetSelectedSubObjects()
        if sub_objs:
            for ci in sub_objs:
                if ci.ComponentIndexType == rg.ComponentIndexType.SubdVertex:
                    sub_vertices.append(ci.Index)
                elif ci.ComponentIndexType == rg.ComponentIndexType.SubdEdge:
                    sub_edges.append(ci.Index)
                elif ci.ComponentIndexType == rg.ComponentIndexType.SubdFace:
                    sub_faces.append(ci.Index)
    except Exception:
        pass

    if isinstance(obj.Geometry, rg.SubD):
        subd = obj.Geometry
        info["subd_info"] = {
            "vertex_count": subd.Vertices.Count,
            "edge_count": subd.Edges.Count,
            "face_count": subd.Faces.Count,
        }

    if sub_vertices:
        info["selected_vertices"] = sub_vertices
    if sub_edges:
        info["selected_edges"] = sub_edges
    if sub_faces:
        info["selected_faces"] = sub_faces

    selected.append(info)

result = json.dumps({"count": len(selected), "objects": selected}, indent=2)
"""


def get_brep_component_info_code(
    object_id: str,
    component_type: str,
    component_index: int,
) -> str:
    """Inspect a Brep edge or face by index."""
    return inject_params(oid=object_id, ctype=component_type, cidx=component_index) + """
guid = System.Guid(_oid)
obj = sc.doc.Objects.Find(guid)
if obj is None or obj.Geometry is None:
    result = json.dumps({"status": "error", "message": "object not found"}, indent=2)
else:
    geom = obj.Geometry
    original_name = rs.ObjectName(guid) or "FilletedBrep"
    if isinstance(geom, rg.Brep):
        brep = geom.DuplicateBrep()
    else:
        try:
            brep = rg.Brep.TryConvertBrep(geom)
        except Exception:
            brep = None

    if brep is None:
        result = json.dumps({"status": "error", "message": "object is not a Brep"}, indent=2)
    else:
        info = {
            "status": "ok",
            "object_id": str(guid),
            "object_name": rs.ObjectName(guid) or "Unnamed",
            "object_type": geom.GetType().Name if geom else "Unknown",
            "component_type": _ctype,
            "component_index": int(_cidx),
        }

        if _ctype == "edge":
            if _cidx < 0 or _cidx >= brep.Edges.Count:
                result = json.dumps({"status": "error", "message": "edge index {0} out of range; Brep hat {1} Kanten (gueltig 0-{2})".format(int(_cidx), brep.Edges.Count, brep.Edges.Count - 1)}, indent=2)
            else:
                edge = brep.Edges[_cidx]
                dom = edge.Domain
                t = 0.5 * (dom.T0 + dom.T1)
                mid = edge.PointAt(t)
                tan = edge.TangentAt(t)
                adj = []
                try:
                    for fi in edge.AdjacentFaces():
                        adj.append(int(fi))
                except Exception:
                    pass
                info["length"] = float(edge.GetLength())
                info["midpoint"] = [mid.X, mid.Y, mid.Z]
                info["start_point"] = [edge.PointAtStart.X, edge.PointAtStart.Y, edge.PointAtStart.Z]
                info["end_point"] = [edge.PointAtEnd.X, edge.PointAtEnd.Y, edge.PointAtEnd.Z]
                info["tangent"] = [tan.X, tan.Y, tan.Z]
                info["adjacent_face_indices"] = adj
                try:
                    info["valence"] = edge.Valence.ToString()
                except Exception:
                    pass
                result = json.dumps(info, indent=2)
        else:
            if _cidx < 0 or _cidx >= brep.Faces.Count:
                result = json.dumps({"status": "error", "message": "face index {0} out of range; Brep hat {1} Flaechen (gueltig 0-{2})".format(int(_cidx), brep.Faces.Count, brep.Faces.Count - 1)}, indent=2)
            else:
                face = brep.Faces[_cidx]
                du = face.Domain(0)
                dv = face.Domain(1)
                u = 0.5 * (du.T0 + du.T1)
                v = 0.5 * (dv.T0 + dv.T1)
                pt = face.PointAt(u, v)
                normal = face.NormalAt(u, v)
                if face.OrientationIsReversed:
                    normal.Reverse()
                edge_indices = []
                try:
                    for loop in face.Loops:
                        for trim in loop.Trims:
                            if trim.Edge is not None:
                                idx = int(trim.Edge.EdgeIndex)
                                if idx not in edge_indices:
                                    edge_indices.append(idx)
                except Exception:
                    pass
                info["midpoint"] = [pt.X, pt.Y, pt.Z]
                info["normal"] = [normal.X, normal.Y, normal.Z]
                info["is_planar"] = bool(face.IsPlanar(sc.doc.ModelAbsoluteTolerance))
                info["adjacent_edge_indices"] = edge_indices
                info["orientation_reversed"] = bool(face.OrientationIsReversed)
                try:
                    amp = rg.AreaMassProperties.Compute(face.DuplicateFace(False))
                    if amp:
                        info["area"] = float(amp.Area)
                except Exception:
                    pass
                result = json.dumps(info, indent=2)
"""


def resolve_reference_code(
    query: str,
    component_type: str = "auto",
    scope_object_ids: List[str] = [],
    max_results: int = 5,
) -> str:
    """Resolve natural-language object/component references read-only."""
    return inject_params(
        query=query,
        ctype=component_type,
        scope_ids=scope_object_ids,
        max_results=max_results,
    ) + """
import re

def _words(text):
    return [w for w in re.split(r"[^a-zA-Z0-9_]+", (text or "").lower()) if w]

def _has_any(words, *items):
    return any(item in words for item in items)

def _pt_list(pt):
    return [round(float(pt.X), 4), round(float(pt.Y), 4), round(float(pt.Z), 4)]

def _bbox_info(guid):
    bbox = rs.BoundingBox(guid)
    if not bbox:
        return None
    xs = [p.X for p in bbox]
    ys = [p.Y for p in bbox]
    zs = [p.Z for p in bbox]
    mn = rg.Point3d(min(xs), min(ys), min(zs))
    mx = rg.Point3d(max(xs), max(ys), max(zs))
    center = rg.Point3d(
        0.5 * (mn.X + mx.X),
        0.5 * (mn.Y + mx.Y),
        0.5 * (mn.Z + mx.Z),
    )
    size = [float(mx.X - mn.X), float(mx.Y - mn.Y), float(mx.Z - mn.Z)]
    return {"min": mn, "max": mx, "center": center, "size": size}

def _norm01(value, lo, hi):
    span = float(hi - lo)
    if abs(span) < 1e-9:
        return 0.5
    return max(0.0, min(1.0, float(value - lo) / span))

def _score_direction(words, point, normal, bbox):
    score = 0.0
    reasons = []
    if bbox:
        x_pos = _norm01(point.X, bbox["min"].X, bbox["max"].X)
        y_pos = _norm01(point.Y, bbox["min"].Y, bbox["max"].Y)
        z_pos = _norm01(point.Z, bbox["min"].Z, bbox["max"].Z)
    else:
        x_pos = y_pos = z_pos = 0.5

    if _has_any(words, "oben", "top", "deck", "deckflaeche", "upper"):
        add = z_pos * 3.0 + max(0.0, normal.Z) * 2.0
        score += add
        reasons.append("top")
    if _has_any(words, "unten", "bottom", "boden", "unterseite", "lower"):
        add = (1.0 - z_pos) * 3.0 + max(0.0, -normal.Z) * 2.0
        score += add
        reasons.append("bottom")
    if _has_any(words, "rechts", "right"):
        add = x_pos * 2.5 + max(0.0, normal.X) * 1.5
        score += add
        reasons.append("right")
    if _has_any(words, "links", "left"):
        add = (1.0 - x_pos) * 2.5 + max(0.0, -normal.X) * 1.5
        score += add
        reasons.append("left")
    if _has_any(words, "vorne", "vorn", "front", "vorder", "vordere"):
        add = (1.0 - y_pos) * 2.5 + max(0.0, -normal.Y) * 1.5
        score += add
        reasons.append("front")
    if _has_any(words, "hinten", "back", "rueck", "rueckseite", "rear"):
        add = y_pos * 2.5 + max(0.0, normal.Y) * 1.5
        score += add
        reasons.append("back")
    return score, reasons

def _object_type_name(obj):
    try:
        return obj.Geometry.GetType().Name if obj and obj.Geometry else "Unknown"
    except Exception:
        return "Unknown"

def _brep_from_obj(obj):
    if obj is None or obj.Geometry is None:
        return None
    geom = obj.Geometry
    if isinstance(geom, rg.Brep):
        return geom.DuplicateBrep()
    try:
        return rg.Brep.TryConvertBrep(geom)
    except Exception:
        return None

def _target_component_type(words, requested):
    req = (requested or "auto").lower()
    if req in ("object", "face", "edge"):
        return req
    if _has_any(words, "kante", "edge", "edges", "kanten"):
        return "edge"
    if _has_any(words, "flaeche", "flaechen", "face", "faces", "seite", "seiten"):
        return "face"
    if _has_any(words, "objekt", "object", "box", "zylinder", "cylinder"):
        return "object"
    return "object"

def _object_candidates(words, object_ids):
    selected = set([str(x).lower() for x in (rs.SelectedObjects() or [])])
    rows = []
    for guid in object_ids:
        obj = sc.doc.Objects.Find(guid)
        if obj is None or obj.Geometry is None:
            continue
        name = rs.ObjectName(guid) or ""
        layer = rs.ObjectLayer(guid) or ""
        bbox = _bbox_info(guid)
        type_name = _object_type_name(obj)
        score = 0.0
        reasons = []

        if str(guid).lower() in selected:
            score += 4.0
            reasons.append("selected")
        if name and name.lower() in " ".join(words):
            score += 5.0
            reasons.append("name")
        for word in words:
            if word and name and word in name.lower():
                score += 1.5
                reasons.append("name-token:{0}".format(word))
            if word and word in type_name.lower():
                score += 1.0
                reasons.append("type-token:{0}".format(word))
        if layer.lower() == "archive":
            score -= 5.0
            reasons.append("archive-penalty")
        try:
            if layer and not rs.LayerVisible(layer):
                score -= 5.0
                reasons.append("hidden-layer-penalty")
        except Exception:
            pass
        if _has_any(words, "selektiert", "selected", "auswahl", "dieses", "diese"):
            if str(guid).lower() in selected:
                score += 4.0
            else:
                score -= 1.0
        if bbox:
            center = bbox["center"]
            direction_score, direction_reasons = _score_direction(
                words, center, rg.Vector3d(0, 0, 0), bbox
            )
            score += direction_score * 0.5
            reasons.extend(["object-" + r for r in direction_reasons])

        rows.append({
            "kind": "object",
            "score": round(score, 4),
            "reasons": sorted(list(set(reasons))),
            "object_id": str(guid),
            "object_name": name or "Unnamed",
            "object_type": type_name,
            "layer": layer,
            "selected": str(guid).lower() in selected,
            "bounding_box": None if not bbox else {
                "min": _pt_list(bbox["min"]),
                "max": _pt_list(bbox["max"]),
                "center": _pt_list(bbox["center"]),
                "size": [round(x, 4) for x in bbox["size"]],
            },
        })
    rows.sort(key=lambda item: item.get("score", 0), reverse=True)
    return rows

def _component_candidates(words, object_rows, target_type, limit_per_object=64):
    rows = []
    for object_row in object_rows:
        try:
            guid = System.Guid(object_row["object_id"])
        except Exception:
            continue
        obj = sc.doc.Objects.Find(guid)
        brep = _brep_from_obj(obj)
        if brep is None:
            continue
        bbox = _bbox_info(guid)
        base = max(0.0, float(object_row.get("score", 0))) * 0.25
        if target_type in ("face", "auto"):
            for i, face in enumerate(brep.Faces):
                if i >= limit_per_object:
                    break
                du = face.Domain(0)
                dv = face.Domain(1)
                u = 0.5 * (du.T0 + du.T1)
                v = 0.5 * (dv.T0 + dv.T1)
                pt = face.PointAt(u, v)
                normal = face.NormalAt(u, v)
                if face.OrientationIsReversed:
                    normal.Reverse()
                score, reasons = _score_direction(words, pt, normal, bbox)
                if _has_any(words, "flaeche", "flaechen", "face", "faces", "seite", "seiten"):
                    score += 2.0
                    reasons.append("component-type-face")
                if _has_any(words, "plan", "planar", "flach"):
                    try:
                        if face.IsPlanar(sc.doc.ModelAbsoluteTolerance):
                            score += 1.0
                            reasons.append("planar")
                    except Exception:
                        pass
                rows.append({
                    "kind": "face",
                    "score": round(score + base, 4),
                    "reasons": sorted(list(set(reasons))),
                    "object_id": str(guid),
                    "object_name": object_row.get("object_name", ""),
                    "component_type": "face",
                    "component_index": int(i),
                    "point": _pt_list(pt),
                    "normal": [round(float(normal.X), 4), round(float(normal.Y), 4), round(float(normal.Z), 4)],
                })
        if target_type in ("edge", "auto"):
            edge_infos = []
            for i, edge in enumerate(brep.Edges):
                if i >= limit_per_object:
                    break
                dom = edge.Domain
                t = 0.5 * (dom.T0 + dom.T1)
                mid = edge.PointAt(t)
                tan = edge.TangentAt(t)
                length = float(edge.GetLength())
                edge_infos.append((i, edge, mid, tan, length))
            lengths = [item[4] for item in edge_infos] or [0.0]
            lo_len = min(lengths)
            hi_len = max(lengths)
            for i, edge, mid, tan, length in edge_infos:
                tangent = rg.Vector3d(float(tan.X), float(tan.Y), float(tan.Z))
                try:
                    tangent.Unitize()
                except Exception:
                    pass
                score, reasons = _score_direction(words, mid, rg.Vector3d(0, 0, 0), bbox)
                if _has_any(words, "kante", "edge", "edges", "kanten"):
                    score += 2.0
                    reasons.append("component-type-edge")
                if _has_any(words, "vertikal", "vertical"):
                    add = abs(float(tangent.Z)) * 2.5
                    score += add
                    reasons.append("vertical")
                if _has_any(words, "horizontal", "waagerecht"):
                    add = (1.0 - abs(float(tangent.Z))) * 2.0
                    score += add
                    reasons.append("horizontal")
                if _has_any(words, "lang", "lange", "laengste", "long"):
                    score += _norm01(length, lo_len, hi_len) * 2.0
                    reasons.append("long")
                if _has_any(words, "kurz", "kuerzeste", "short"):
                    score += (1.0 - _norm01(length, lo_len, hi_len)) * 2.0
                    reasons.append("short")
                rows.append({
                    "kind": "edge",
                    "score": round(score + base, 4),
                    "reasons": sorted(list(set(reasons))),
                    "object_id": str(guid),
                    "object_name": object_row.get("object_name", ""),
                    "component_type": "edge",
                    "component_index": int(i),
                    "point": _pt_list(mid),
                    "tangent": [round(float(tangent.X), 4), round(float(tangent.Y), 4), round(float(tangent.Z), 4)],
                    "length": round(length, 4),
                })
    rows.sort(key=lambda item: item.get("score", 0), reverse=True)
    return rows

query_text = _query or ""
words = _words(query_text)
try:
    max_results = int(_max_results or 5)
except Exception:
    max_results = 5
max_results = max(1, min(50, max_results))

scope = []
for value in (_scope_ids or []):
    try:
        scope.append(System.Guid(str(value)))
    except Exception:
        pass
if not scope:
    selected_objects = rs.SelectedObjects() or []
    if selected_objects and _has_any(words, "selektiert", "selected", "auswahl", "dieses", "diese"):
        scope = selected_objects
if not scope:
    scope = rs.AllObjects() or []

target_type = _target_component_type(words, _ctype)
object_rows = _object_candidates(words, scope)
matches = object_rows
if target_type in ("face", "edge"):
    matches = _component_candidates(words, object_rows[:max(1, max_results)], target_type)

payload = {
    "status": "ok",
    "query": query_text,
    "inferred_component_type": target_type,
    "scope_object_count": len(scope),
    "object_candidates": object_rows[:max_results],
    "matches": matches[:max_results],
    "top_match": matches[0] if matches else None,
    "notes": [
        "front/vorne maps to lower Y; back/hinten maps to higher Y.",
        "This is read-only heuristic reference resolution; ask the user to pick if confidence is low.",
    ],
}
result = json.dumps(payload, indent=2)
"""
