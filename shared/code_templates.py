"""Rhino Python source-code templates shared between MCP and plugin.

Each function returns a string of Python source ready to be exec()'d
inside Rhino's CPython 3 (or sent via socket to Rhino's internal
Python). The Python preambles used by both consumers define the same
names: ``rs``, ``rg``, ``sc``, ``System``, ``math``, plus the helpers
``get_obj``, ``archive_object``, ``add_object_metadata``. Templates can
assume those names already exist and must set ``result = <value>``.
"""
from __future__ import annotations

from ._codegen_base import inject_params
from .code_templates_geometry import *  # noqa: F401,F403
from .code_templates_transform import *  # noqa: F401,F403
from .code_templates_curve import *  # noqa: F401,F403
from .code_templates_surface import *  # noqa: F401,F403
from .code_templates_boolean import *  # noqa: F401,F403
from .code_templates_subd import *  # noqa: F401,F403
from .code_templates_inspection import *  # noqa: F401,F403
from .code_templates_selection import *  # noqa: F401,F403
from .code_templates_brep_edit import *  # noqa: F401,F403


__all__ = [
    "inject_params",
    "create_box_code",
    "create_point_code",
    "create_line_code",
    "create_polyline_code",
    "create_circle_code",
    "create_rectangle_code",
    "create_sphere_code",
    "create_cylinder_code",
    "create_cone_code",
    "create_pipe_code",
    "move_objects_code",
    "copy_objects_code",
    "rotate_objects_code",
    "scale_objects_code",
    "set_bbox_dimension_code",
    "mirror_objects_code",
    "array_linear_code",
    "array_polar_code",
    "delete_objects_code",
    "set_layer_code",
    "rename_object_code",
    "offset_curve_code",
    "join_curves_code",
    "curve_boolean_union_code",
    "fillet_curves_code",
    "divide_curve_code",
    "extrude_curve_code",
    "loft_curves_code",
    "sweep1_code",
    "revolve_curve_code",
    "planar_surface_code",
    "cap_planar_holes_code",
    "join_surfaces_code",
    "boolean_union_code",
    "boolean_difference_code",
    "boolean_intersection_code",
    "boolean_split_code",
    "create_hole_code",
    "create_slot_code",
    "get_scene_info_code",
    "get_layer_info_code",
    "get_object_info_code",
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
    "get_layers_code",
    "get_scene_objects_with_metadata_code",
    "get_selected_code",
    "select_objects_code",
    "list_backups_code",
    "restore_object_code",
    "undo_last_action_code",
    "redo_last_action_code",
    "create_arc_code",
    "create_torus_code",
    "create_interpolated_curve_code",
    "create_control_point_curve_code",
    "explode_curves_code",
    "extend_curve_code",
    "rebuild_curve_code",
    "project_curve_to_surface_code",
    "sweep2_code",
    "offset_surface_code",
    "thicken_surface_to_solid_code",
    "get_brep_component_info_code",
    "resolve_reference_code",
    "resize_box_face_code",
    "resize_cylinder_face_code",
    "resize_extrusion_face_code",
    "fillet_brep_edge_code",
    "round_edges_by_rule_code",
    "chamfer_brep_edge_code",
    "move_brep_face_along_normal_code",
    "move_brep_face_in_direction_code",
]
