"""Code templates: subd operations. See code_templates.py."""
from __future__ import annotations

from typing import List

from ._codegen_base import inject_params

__all__ = [
    "create_subd_box_code",
    "create_subd_sphere_code",
    "create_subd_cylinder_code",
    "mesh_to_subd_code",
    "quad_remesh_to_subd_code",
    "subd_to_nurbs_code",
    "subd_to_mesh_code",
    "get_subd_info_code",
    "subd_crease_edges_code",
    "subd_set_vertex_position_code",
    "subd_extrude_faces_code",
    "subd_offset_faces_code",
    "subd_subdivide_code",
]


def create_subd_box_code(
    x: float = 0,
    y: float = 0,
    z: float = 0,
    width: float = 10,
    depth: float = 10,
    height: float = 10,
    x_faces: int = 2,
    y_faces: int = 2,
    z_faces: int = 2,
    name: str = "SubDBox",
) -> str:
    """Create a SubD box by meshing a box and converting to SubD."""
    return inject_params(
        x=x, y=y, z=z, w=width, d=depth, h=height,
        xf=x_faces, yf=y_faces, zf=z_faces, name=name
    ) + """
if _xf < 1 or _yf < 1 or _zf < 1:
    result = "Error: face counts must be >= 1"
else:
    verts = []
    faces = []
    nx = _xf + 1
    ny = _yf + 1
    nz = _zf + 1

    def vi(ix, iy, iz):
        return ix + iy * (_xf + 1) + iz * (_xf + 1) * (_yf + 1)

    for iz in range(nz):
        for iy in range(ny):
            for ix in range(nx):
                px = _x + _w * ix / _xf
                py = _y + _d * iy / _yf
                pz = _z + _h * iz / _zf
                verts.append(rg.Point3d(px, py, pz))

    for iy in range(_yf):
        for ix in range(_xf):
            faces.append(rg.MeshFace(vi(ix,iy,0), vi(ix,iy+1,0), vi(ix+1,iy+1,0), vi(ix+1,iy,0)))
            faces.append(rg.MeshFace(vi(ix,iy,_zf), vi(ix+1,iy,_zf), vi(ix+1,iy+1,_zf), vi(ix,iy+1,_zf)))

    for iz in range(_zf):
        for ix in range(_xf):
            faces.append(rg.MeshFace(vi(ix,0,iz), vi(ix+1,0,iz), vi(ix+1,0,iz+1), vi(ix,0,iz+1)))
            faces.append(rg.MeshFace(vi(ix,_yf,iz), vi(ix,_yf,iz+1), vi(ix+1,_yf,iz+1), vi(ix+1,_yf,iz)))

    for iz in range(_zf):
        for iy in range(_yf):
            faces.append(rg.MeshFace(vi(0,iy,iz), vi(0,iy,iz+1), vi(0,iy+1,iz+1), vi(0,iy+1,iz)))
            faces.append(rg.MeshFace(vi(_xf,iy,iz), vi(_xf,iy+1,iz), vi(_xf,iy+1,iz+1), vi(_xf,iy,iz+1)))

    mesh = rg.Mesh()
    for v in verts:
        mesh.Vertices.Add(v)
    for f in faces:
        mesh.Faces.AddFace(f)
    mesh.Normals.ComputeNormals()

    subd = rg.SubD.CreateFromMesh(mesh)
    if subd and subd.IsValid:
        subd_id = sc.doc.Objects.AddSubD(subd)
        rs.SetUserText(subd_id, "object_class", "subd")
        add_object_metadata(subd_id, _name, "SubD box {0}x{1}x{2}, faces {3}x{4}x{5}".format(
            _w, _d, _h, _xf, _yf, _zf))
        rs.Redraw()
        result = "Created SubD box: " + str(subd_id)
    else:
        result = "Error: could not create SubD from mesh"
"""


def create_subd_sphere_code(
    center_x: float = 0,
    center_y: float = 0,
    center_z: float = 0,
    radius: float = 5,
    subdivisions: int = 3,
    name: str = "SubDSphere",
) -> str:
    """Create a SubD sphere from a mesh sphere."""
    return inject_params(
        cx=center_x, cy=center_y, cz=center_z, r=radius, subdiv=subdivisions, name=name
    ) + """
sphere = rg.Mesh.CreateFromSphere(
    rg.Sphere(rg.Point3d(_cx, _cy, _cz), _r),
    _subdiv * 4,
    _subdiv * 4,
)
subd = rg.SubD.CreateFromMesh(sphere)
if subd and subd.IsValid:
    subd_id = sc.doc.Objects.AddSubD(subd)
    rs.SetUserText(subd_id, "object_class", "subd")
    add_object_metadata(subd_id, _name, "SubD sphere r={0}".format(_r))
    rs.Redraw()
    result = "Created SubD sphere: " + str(subd_id)
else:
    result = "Error: could not create SubD sphere"
"""


def create_subd_cylinder_code(
    base_x: float = 0,
    base_y: float = 0,
    base_z: float = 0,
    radius: float = 5,
    height: float = 10,
    radial_faces: int = 8,
    height_faces: int = 4,
    cap: bool = True,
    name: str = "SubDCylinder",
) -> str:
    """Create a SubD cylinder from a cylindrical quad mesh."""
    return inject_params(
        bx=base_x, by=base_y, bz=base_z, r=radius, h=height,
        rf=radial_faces, hf=height_faces, cap=cap, name=name
    ) + """
if _rf < 3 or _hf < 1:
    result = "Error: radial_faces must be >= 3 and height_faces >= 1"
else:
    verts = []
    faces = []
    n_radial = _rf
    n_height = _hf + 1

    for hi in range(n_height):
        z = _bz + _h * hi / _hf
        for ri in range(n_radial):
            angle = 2.0 * math.pi * ri / n_radial
            verts.append(rg.Point3d(
                _bx + _r * math.cos(angle),
                _by + _r * math.sin(angle),
                z
            ))

    for hi in range(n_height - 1):
        for ri in range(n_radial):
            ri_next = (ri + 1) % n_radial
            v0 = hi * n_radial + ri
            v1 = hi * n_radial + ri_next
            v2 = (hi + 1) * n_radial + ri_next
            v3 = (hi + 1) * n_radial + ri
            faces.append(rg.MeshFace(v0, v1, v2, v3))

    mesh = rg.Mesh()
    for v in verts:
        mesh.Vertices.Add(v)
    for f in faces:
        mesh.Faces.AddFace(f)

    if _cap:
        bc = len(verts)
        mesh.Vertices.Add(rg.Point3d(_bx, _by, _bz))
        for ri in range(n_radial):
            ri_next = (ri + 1) % n_radial
            mesh.Faces.AddFace(rg.MeshFace(bc, ri_next, ri))
        tc = mesh.Vertices.Count
        mesh.Vertices.Add(rg.Point3d(_bx, _by, _bz + _h))
        top_start = (n_height - 1) * n_radial
        for ri in range(n_radial):
            ri_next = (ri + 1) % n_radial
            mesh.Faces.AddFace(rg.MeshFace(tc, top_start + ri, top_start + ri_next))

    mesh.Normals.ComputeNormals()
    subd = rg.SubD.CreateFromMesh(mesh)
    if subd and subd.IsValid:
        subd_id = sc.doc.Objects.AddSubD(subd)
        rs.SetUserText(subd_id, "object_class", "subd")
        add_object_metadata(subd_id, _name, "SubD cylinder r={0} h={1}".format(_r, _h))
        rs.Redraw()
        result = "Created SubD cylinder: " + str(subd_id)
    else:
        result = "Error: could not create SubD cylinder"
"""


def mesh_to_subd_code(
    mesh_id: str,
    name: str = "MeshToSubD",
) -> str:
    """Convert an existing mesh object to SubD."""
    return inject_params(mid=mesh_id, name=name) + """
obj = sc.doc.Objects.Find(System.Guid(_mid))
if obj is None:
    result = "Error: object not found"
else:
    mesh = obj.Geometry
    if not isinstance(mesh, rg.Mesh):
        result = "Error: object is not a mesh"
    else:
        subd = rg.SubD.CreateFromMesh(mesh)
        if subd and subd.IsValid:
            subd_id = sc.doc.Objects.AddSubD(subd)
            rs.SetUserText(subd_id, "object_class", "subd")
            add_object_metadata(subd_id, _name, "SubD from mesh")
            rs.Redraw()
            result = "Converted mesh to SubD: " + str(subd_id)
        else:
            result = "Error: could not convert mesh to SubD"
"""


def quad_remesh_to_subd_code(
    object_id: str,
    target_quad_count: int = 500,
    adaptive_quad_count: bool = True,
    name: str = "QuadRemeshSubD",
) -> str:
    """QuadRemesh a brep/mesh/subd, then convert to SubD."""
    return inject_params(
        oid=object_id, tqc=target_quad_count, adaptive=adaptive_quad_count, name=name
    ) + """
obj = sc.doc.Objects.Find(System.Guid(_oid))
if obj is None:
    result = "Error: object not found"
else:
    geom = obj.Geometry
    params = rg.QuadRemeshParameters()
    params.TargetQuadCount = _tqc
    params.AdaptiveQuadCount = _adaptive

    quad_mesh = None
    if isinstance(geom, rg.Brep):
        quad_mesh = rg.Mesh.QuadRemeshBrep(geom, params)
    elif isinstance(geom, rg.Mesh):
        quad_mesh = geom.QuadRemesh(params)
    elif isinstance(geom, rg.SubD):
        brep = geom.ToBrep(rg.SubDToBrepOptions())
        quad_mesh = rg.Mesh.QuadRemeshBrep(brep, params)

    if quad_mesh:
        subd = rg.SubD.CreateFromMesh(quad_mesh)
        if subd and subd.IsValid:
            subd_id = sc.doc.Objects.AddSubD(subd)
            rs.SetUserText(subd_id, "object_class", "subd")
            add_object_metadata(subd_id, _name, "QuadRemesh SubD (~{0} quads)".format(_tqc))
            rs.Redraw()
            result = "Created SubD via QuadRemesh: " + str(subd_id)
        else:
            result = "Error: SubD creation from quad mesh failed"
    else:
        result = "Error: QuadRemesh failed"
"""


def subd_to_nurbs_code(
    subd_id: str,
    name: str = "SubDToNURBS",
) -> str:
    """Convert a SubD object to a NURBS brep."""
    return inject_params(sid=subd_id, name=name) + """
obj = sc.doc.Objects.Find(System.Guid(_sid))
if obj is None:
    result = "Error: object not found"
else:
    geom = obj.Geometry
    if not isinstance(geom, rg.SubD):
        result = "Error: object is not a SubD"
    else:
        brep = geom.ToBrep(rg.SubDToBrepOptions())
        if brep:
            brep_id = sc.doc.Objects.AddBrep(brep)
            add_object_metadata(brep_id, _name, "NURBS from SubD")
            rs.Redraw()
            result = "Converted SubD to NURBS: " + str(brep_id)
        else:
            result = "Error: SubD to NURBS conversion failed"
"""


def subd_to_mesh_code(
    subd_id: str,
    density: int = 1,
    name: str = "SubDToMesh",
) -> str:
    """Convert a SubD object to a mesh."""
    return inject_params(sid=subd_id, density=density, name=name) + """
obj = sc.doc.Objects.Find(System.Guid(_sid))
if obj is None:
    result = "Error: object not found"
else:
    geom = obj.Geometry
    if not isinstance(geom, rg.SubD):
        result = "Error: object is not a SubD"
    else:
        mesh = rg.Mesh.CreateFromSubD(geom, int(_density))
        if mesh is None:
            mesh = rg.Mesh.CreateFromSubDControlNet(geom)
        if mesh:
            mesh_id = sc.doc.Objects.AddMesh(mesh)
            add_object_metadata(mesh_id, _name, "Mesh from SubD")
            rs.Redraw()
            result = "Converted SubD to mesh: " + str(mesh_id)
        else:
            result = "Error: SubD to mesh conversion failed"
"""


def get_subd_info_code(subd_id: str) -> str:
    """Get detailed info about a SubD object."""
    return inject_params(sid=subd_id) + """
obj = sc.doc.Objects.Find(System.Guid(_sid))
if obj is None:
    result = "Error: object not found"
else:
    geom = obj.Geometry
    if not isinstance(geom, rg.SubD):
        result = "Error: object is not a SubD (type: {0})".format(geom.GetType().Name)
    else:
        creased = 0
        for i in range(geom.Edges.Count):
            edge = geom.Edges.Find(i)
            if edge and edge.Tag == rg.SubDEdgeTag.Crease:
                creased += 1

        bbox = geom.GetBoundingBox(True)
        info = {
            "face_count": geom.Faces.Count,
            "edge_count": geom.Edges.Count,
            "vertex_count": geom.Vertices.Count,
            "creased_edges": creased,
            "is_solid": geom.IsSolid,
            "bounding_box": {
                "min": [bbox.Min.X, bbox.Min.Y, bbox.Min.Z],
                "max": [bbox.Max.X, bbox.Max.Y, bbox.Max.Z],
            }
        }
        result = json.dumps(info, indent=2)
"""


def subd_crease_edges_code(
    subd_id: str,
    edge_indices: List[int],
    crease: bool = True,
) -> str:
    """Set or remove creases on SubD edges."""
    return inject_params(sid=subd_id, edges=edge_indices, crease=crease) + """
obj = sc.doc.Objects.Find(System.Guid(_sid))
if obj is None:
    result = "Error: object not found"
else:
    geom = obj.Geometry.Duplicate()
    if not isinstance(geom, rg.SubD):
        result = "Error: not a SubD"
    else:
        changed = 0
        for idx in _edges:
            edge = geom.Edges.Find(idx)
            if edge:
                edge.SetCreaseForExperts(bool(_crease))
                changed += 1
        archive_object(System.Guid(_sid))
        sc.doc.Objects.Replace(System.Guid(_sid), geom)
        rs.Redraw()
        result = "Set crease={0} on {1} edge(s)".format(_crease, changed)
"""


def subd_set_vertex_position_code(
    subd_id: str,
    vertex_index: int = 0,
    x: float = 0,
    y: float = 0,
    z: float = 0,
) -> str:
    """Move a single SubD control vertex to a new position."""
    return inject_params(sid=subd_id, vi=vertex_index, x=x, y=y, z=z) + """
obj = sc.doc.Objects.Find(System.Guid(_sid))
if obj is None:
    result = "Error: object not found"
else:
    geom = obj.Geometry.Duplicate()
    if not isinstance(geom, rg.SubD):
        result = "Error: not a SubD"
    else:
        vert = geom.Vertices.Find(_vi)
        if vert:
            vert.SetControlNetPoint(rg.Point3d(_x, _y, _z), True)
            archive_object(System.Guid(_sid))
            sc.doc.Objects.Replace(System.Guid(_sid), geom)
            rs.Redraw()
            result = "Moved vertex {0} to ({1},{2},{3})".format(_vi, _x, _y, _z)
        else:
            result = "Error: vertex index {0} not found".format(_vi)
"""


def subd_extrude_faces_code(
    subd_id: str,
    face_indices: List[int],
    distance: float = 2.0,
    name: str = "SubDExtrude",
) -> str:
    """Extrude SubD faces outward by a distance."""
    return inject_params(sid=subd_id, faces=face_indices, dist=distance, name=name) + """
obj = sc.doc.Objects.Find(System.Guid(_sid))
if obj is None:
    result = "Error: object not found"
else:
    geom = obj.Geometry
    if not isinstance(geom, rg.SubD):
        result = "Error: not a SubD"
    else:
        archive_object(System.Guid(_sid))
        rs.UnselectAllObjects()
        rs.SelectObject(System.Guid(_sid))
        face_sel = " ".join(["_SubDFace _Index {0}".format(f) for f in _faces])
        cmd = "_ExtrudeSubDFace _Pause {0} _Enter _Distance {1} _Enter".format(face_sel, _dist)
        rs.Command(cmd, False)
        rs.Redraw()
        result = "Extruded {0} SubD face(s) by distance {1}".format(len(_faces), _dist)
"""


def subd_offset_faces_code(
    subd_id: str,
    face_indices: List[int],
    distance: float = -0.5,
) -> str:
    """Inset or outset SubD faces."""
    return inject_params(sid=subd_id, faces=face_indices, dist=distance) + """
obj = sc.doc.Objects.Find(System.Guid(_sid))
if obj is None:
    result = "Error: object not found"
else:
    geom = obj.Geometry
    if not isinstance(geom, rg.SubD):
        result = "Error: not a SubD"
    else:
        archive_object(System.Guid(_sid))
        rs.UnselectAllObjects()
        rs.SelectObject(System.Guid(_sid))
        face_sel = " ".join(["_SubDFace _Index {0}".format(f) for f in _faces])
        cmd = "_InsetSubDFace {0} _Enter _Distance {1} _Enter".format(face_sel, abs(_dist))
        rs.Command(cmd, False)
        rs.Redraw()
        result = "Inset {0} SubD face(s) by {1}".format(len(_faces), _dist)
"""


def subd_subdivide_code(
    subd_id: str,
    levels: int = 1,
) -> str:
    """Subdivide a SubD object to increase its resolution."""
    return inject_params(sid=subd_id, lvl=levels) + """
obj = sc.doc.Objects.Find(System.Guid(_sid))
if obj is None:
    result = "Error: object not found"
else:
    geom = obj.Geometry.Duplicate()
    if not isinstance(geom, rg.SubD):
        result = "Error: not a SubD"
    else:
        _req_lvl = int(_lvl)
        if _req_lvl < 1:
            result = "Fehler: levels muss >= 1 sein (erhalten: {0}).".format(_req_lvl)
        else:
            # Cap at 4: each Catmull-Clark level ~quadruples the face count, so a
            # higher value (e.g. 8 = ~65000x) freezes the Rhino UI thread / OOMs.
            # The "(auf 4 begrenzt)" note tells the model at runtime it was capped
            # (so it doesn't need the schema maximum to learn the limit).
            _safe_lvl = min(_req_lvl, 4)
            for _ in range(_safe_lvl):
                geom.Subdivide()
            archive_object(System.Guid(_sid))
            sc.doc.Objects.Replace(System.Guid(_sid), geom)
            rs.Redraw()
            _note = "" if _safe_lvl == _req_lvl else " (auf 4 begrenzt)"
            result = "Subdivided {0} level(s){1}. New face count: {2}".format(_safe_lvl, _note, geom.Faces.Count)
"""
