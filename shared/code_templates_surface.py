"""Code templates: surface operations. See code_templates.py."""
from __future__ import annotations

from typing import List

from ._codegen_base import inject_params

__all__ = [
    "extrude_curve_code",
    "loft_curves_code",
    "sweep1_code",
    "revolve_curve_code",
    "planar_surface_code",
    "cap_planar_holes_code",
    "join_surfaces_code",
    "sweep2_code",
    "offset_surface_code",
    "thicken_surface_to_solid_code",
]


def extrude_curve_code(
    curve_id: str,
    dx: float = 0,
    dy: float = 0,
    dz: float = 10,
    cap: bool = True,
    name: str = "Extrusion",
) -> str:
    """Extrude a curve along the direction vector (dx, dy, dz)."""
    return inject_params(
        cid=curve_id, dx=dx, dy=dy, dz=dz, cap=cap, name=name
    ) + """
path = rs.AddLine((0,0,0), (_dx,_dy,_dz))
srf_id = rs.ExtrudeCurve(System.Guid(_cid), path)
rs.DeleteObject(path)
if srf_id and _cap:
    rs.CapPlanarHoles(srf_id)
if srf_id:
    # Geschlossene Knick-Profile (Dreieck/Rechteck-Polyline) ergeben sonst eine
    # Seitenwand mit Naht statt echter Eckkanten -> Ecken nicht filletbar,
    # Pick-Indizes inkonsistent. SplitKinkyFaces zerlegt die Naht-Flaeche in
    # echte Brep-Flaechen, sodass die Knickkanten zu echten Brep-Kanten werden.
    try:
        kink_doc_obj = sc.doc.Objects.Find(srf_id)
        if kink_doc_obj and isinstance(kink_doc_obj.Geometry, rg.Brep):
            kink_brep = kink_doc_obj.Geometry.DuplicateBrep()
            before_face_count = kink_brep.Faces.Count
            kink_brep.Faces.SplitKinkyFaces()
            if kink_brep.IsValid and kink_brep.Faces.Count > before_face_count:
                sc.doc.Objects.Replace(srf_id, kink_brep)
    except Exception:
        pass
    add_object_metadata(srf_id, _name, "Extrusion")
    try:
        is_closed = bool(rs.IsCurveClosed(System.Guid(_cid)))
    except Exception:
        is_closed = False
    try:
        is_planar = bool(rs.IsCurvePlanar(System.Guid(_cid)))
    except Exception:
        is_planar = False
    vec_len = math.sqrt((_dx * _dx) + (_dy * _dy) + (_dz * _dz))
    if _cap and is_closed and is_planar and vec_len > max(sc.doc.ModelAbsoluteTolerance, 1e-6):
        rs.SetUserText(srf_id, "object_class", "primitive_extrusion")
        rs.SetUserText(srf_id, "editable_recipe_type", "axis_extrusion")
        rs.SetUserText(srf_id, "editable_operations", "resize_extrusion_face,fillet_brep_edge,chamfer_brep_edge")
        rs.SetUserText(
            srf_id,
            "editable_recipe",
            json.dumps(
                {
                    "dx": float(_dx),
                    "dy": float(_dy),
                    "dz": float(_dz),
                    "cap": bool(_cap),
                }
            ),
        )
rs.Redraw()
result = "Created extrusion: " + str(srf_id)
"""


def loft_curves_code(
    curve_ids: List[str],
    loft_type: int = 0,
    closed: bool = False,
    name: str = "Loft",
) -> str:
    """Create a lofted surface through multiple cross-section curves."""
    return inject_params(ids=curve_ids, lt=loft_type, closed=closed, name=name) + """
guids = [System.Guid(i) for i in _ids]
loft = rs.AddLoftSrf(guids, loft_type=_lt, closed=_closed)
if loft:
    for lid in loft:
        add_object_metadata(lid, _name, "Loft ({0} sections)".format(len(_ids)))
rs.Redraw()
loft_str = [str(lid) for lid in loft] if loft else []
result = "Created loft: " + str(loft_str)
"""


def sweep1_code(
    rail_id: str,
    cross_section_ids: List[str],
    closed: bool = False,
    name: str = "Sweep1",
) -> str:
    """Sweep cross-section curves along a single rail curve."""
    return inject_params(
        rail=rail_id, sections=cross_section_ids, closed=closed, name=name
    ) + """
rail_guid = System.Guid(_rail)
section_guids = [System.Guid(s) for s in _sections]
sweep = rs.AddSweep1(rail_guid, section_guids, _closed)
if sweep:
    for sid in sweep:
        add_object_metadata(sid, _name, "Sweep1")
rs.Redraw()
sweep_str = [str(sid) for sid in sweep] if sweep else []
result = "Created sweep1: " + str(sweep_str)
"""


def revolve_curve_code(
    curve_id: str,
    axis_start: List[float] = [0, 0, 0],
    axis_end: List[float] = [0, 0, 1],
    start_angle: float = 0,
    end_angle: float = 360,
    name: str = "Revolve",
) -> str:
    """Revolve a profile curve around an axis."""
    return inject_params(
        cid=curve_id,
        a0=axis_start,
        a1=axis_end,
        sa=start_angle,
        ea=end_angle,
        name=name,
    ) + """
# rs.AddRevSrf erwartet die Winkel in GRAD (Default end_angle=360.0), NICHT in
# Bogenmass — ein frueheres math.radians(_ea) machte aus 360 Grad ~6.28 "Grad"
# und damit nur einen duennen Splitter statt eines vollen Rotationskoerpers.
rev = rs.AddRevSrf(System.Guid(_cid), rs.AddLine(_a0, _a1),
                   _sa, _ea)
if rev:
    add_object_metadata(rev, _name, "Revolve {0}-{1} deg".format(_sa, _ea))
rs.Redraw()
result = "Created revolve: " + str(rev)
"""


def planar_surface_code(
    curve_ids: List[str],
    name: str = "PlanarSrf",
) -> str:
    """Create a planar surface from one or more closed, planar curves."""
    return inject_params(ids=curve_ids, name=name) + """
guids = [System.Guid(i) for i in _ids]
srf = rs.AddPlanarSrf(guids)
if srf:
    for s in srf:
        add_object_metadata(s, _name, "Planar surface")
rs.Redraw()
srf_str = [str(s) for s in srf] if srf else []
result = "Created planar surface: " + str(srf_str)
"""


def cap_planar_holes_code(brep_id: str) -> str:
    """Cap all planar holes in a brep/polysurface."""
    return inject_params(bid=brep_id) + """
archive_object(System.Guid(_bid))
capped = rs.CapPlanarHoles(System.Guid(_bid))
rs.Redraw()
result = "Capped: " + str(capped)
"""


def join_surfaces_code(
    object_ids: List[str],
    delete_input: bool = True,
    name: str = "JoinedPolysurface",
) -> str:
    """Join adjacent surfaces/polysurfaces into one or more polysurfaces."""
    return inject_params(ids=object_ids, delete=delete_input, name=name) + """
guids = [System.Guid(i) for i in _ids]
breps = []
for g in guids:
    obj = sc.doc.Objects.Find(g)
    if not obj or not obj.Geometry:
        continue
    geom = obj.Geometry
    brep = None
    if isinstance(geom, rg.Brep):
        brep = geom.DuplicateBrep()
    else:
        try:
            brep = geom.ToBrep()
        except Exception:
            brep = None
    if brep:
        breps.append(brep)

if len(breps) < 2:
    result = "Join surfaces failed: need at least 2 surface-like objects"
else:
    joined = rg.Brep.JoinBreps(breps, sc.doc.ModelAbsoluteTolerance)
    result_ids = []
    if joined:
        for idx, brep in enumerate(joined):
            jid = sc.doc.Objects.AddBrep(brep)
            if jid and jid != System.Guid.Empty:
                jname = _name if len(joined) == 1 else "{0}_{1}".format(_name, idx + 1)
                add_object_metadata(jid, jname, "Joined surfaces/polysurfaces")
                result_ids.append(str(jid))

    if result_ids:
        if _delete:
            for g in guids:
                archive_object(g)
                rs.DeleteObject(g)
        rs.Redraw()
        if len(result_ids) == 1:
            result = "Joined surfaces into 1 polysurface: " + str(result_ids)
        else:
            result = "Joined surfaces produced {0} polysurfaces: {1}".format(len(result_ids), result_ids)
    else:
        result = "Join surfaces failed"
"""


def sweep2_code(
    rail1_id: str,
    rail2_id: str,
    cross_section_ids: List[str],
    name: str = "Sweep2",
) -> str:
    """Sweep cross-section curves along two rail curves."""
    return inject_params(r1=rail1_id, r2=rail2_id, sections=cross_section_ids, name=name) + """
rail_guids = [System.Guid(_r1), System.Guid(_r2)]
section_guids = [System.Guid(s) for s in _sections]
sweep = rs.AddSweep2(rail_guids, section_guids)
if sweep:
    for sid in sweep:
        add_object_metadata(sid, _name, "Sweep2")
rs.Redraw()
sweep_str = [str(sid) for sid in sweep] if sweep else []
result = "Created sweep2: " + str(sweep_str)
"""


def offset_surface_code(
    surface_id: str,
    distance: float = 1.0,
    name: str = "OffsetSrf",
) -> str:
    """Offset a surface by a given distance."""
    return inject_params(sid=surface_id, dist=distance, name=name) + """
off = rs.OffsetSurface(System.Guid(_sid), _dist)
if off:
    add_object_metadata(off, _name, "Offset d={0}".format(_dist))
rs.Redraw()
result = "Offset surface: " + str(off)
"""


def thicken_surface_to_solid_code(
    surface_id: str,
    distance: float = 1.0,
    both_sides: bool = False,
    extend: bool = False,
    delete_input: bool = False,
    name: str = "ThickenedSolid",
) -> str:
    """Offset an open surface/brep and try to close the side walls into a solid."""
    return inject_params(
        sid=surface_id,
        dist=distance,
        both=both_sides,
        extend=extend,
        delete=delete_input,
        name=name,
    ) + """
guid = System.Guid(_sid)
obj = sc.doc.Objects.Find(guid)
if obj is None or obj.Geometry is None:
    result = "Error: object not found"
else:
    geom = obj.Geometry
    if isinstance(geom, rg.Brep):
        brep = geom.DuplicateBrep()
    else:
        try:
            brep = rg.Brep.TryConvertBrep(geom)
        except Exception:
            brep = None

    if brep is None:
        result = "Error: object cannot be converted to Brep"
    else:
        tol = sc.doc.ModelAbsoluteTolerance
        candidates = []
        attempts = []

        if brep.Faces.Count == 1:
            try:
                face_result = rg.Brep.CreateFromOffsetFace(
                    brep.Faces[0], _dist, tol, _both, True
                )
                if face_result:
                    if isinstance(face_result, (list, tuple)):
                        for item in face_result:
                            if item and item.IsValid:
                                candidates.append(item)
                    elif face_result.IsValid:
                        candidates.append(face_result)
                    attempts.append("CreateFromOffsetFace")
            except Exception as e:
                attempts.append("CreateFromOffsetFace failed: {0}".format(e))

        if not candidates:
            try:
                offset_parts, out_blends, out_walls = rg.Brep.CreateOffsetBrep(
                    brep, _dist, True, _extend, tol
                )
                for seq in (offset_parts, out_blends, out_walls):
                    if seq:
                        for item in seq:
                            if item and item.IsValid:
                                candidates.append(item)
                attempts.append("CreateOffsetBrep")
            except Exception as e:
                attempts.append("CreateOffsetBrep failed: {0}".format(e))

        if not candidates:
            result = "Thicken surface failed ({0})".format("; ".join(attempts))
        else:
            joined = rg.Brep.JoinBreps(candidates, tol)
            final_breps = []
            if joined:
                for item in joined:
                    if item and item.IsValid:
                        final_breps.append(item)
            else:
                final_breps = [item for item in candidates if item and item.IsValid]

            solids = [item for item in final_breps if item.IsSolid]
            chosen = solids or final_breps

            created_ids = []
            if _delete:
                try:
                    archive_object(guid)
                except Exception:
                    pass
                rs.DeleteObject(guid)

            for idx, item in enumerate(chosen):
                new_id = sc.doc.Objects.AddBrep(item)
                if new_id and new_id != System.Guid.Empty:
                    obj_name = _name if len(chosen) == 1 else "{0}_{1}".format(_name, idx + 1)
                    desc = "Thickened surface d={0}".format(_dist)
                    add_object_metadata(new_id, obj_name, desc)
                    created_ids.append(str(new_id))

            rs.Redraw()
            solid_count = len([item for item in chosen if item.IsSolid])
            if created_ids:
                if solid_count:
                    result = "Created {0} closed solid(s): {1}".format(solid_count, created_ids)
                else:
                    result = "Created thickened brep(s), but none are closed solids: {0}".format(created_ids)
            else:
                result = "Thicken surface failed to add result to document"
"""
