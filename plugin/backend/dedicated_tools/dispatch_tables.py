"""dedicated_tools.dispatch_tables - reine Dispatch-Daten + Predicate.
Leaf: referenziert nur shared.code_templates."""
from __future__ import annotations

from shared import code_templates

_DISPATCH: dict[str, tuple] = {
    "create_box": (
        code_templates.create_box_code,
        ("x", "y", "z", "width", "depth", "height", "name"),
    ),
    "create_point": (
        code_templates.create_point_code,
        ("x", "y", "z", "name"),
    ),
    "create_line": (
        code_templates.create_line_code,
        ("x1", "y1", "z1", "x2", "y2", "z2", "name"),
    ),
    "create_polyline": (
        code_templates.create_polyline_code,
        ("points", "closed", "name"),
    ),
    "create_circle": (
        code_templates.create_circle_code,
        ("center_x", "center_y", "center_z", "radius", "name"),
    ),
    "create_rectangle": (
        code_templates.create_rectangle_code,
        ("x", "y", "z", "width", "height", "name"),
    ),
    "create_sphere": (
        code_templates.create_sphere_code,
        ("center_x", "center_y", "center_z", "radius", "name"),
    ),
    "create_cylinder": (
        code_templates.create_cylinder_code,
        ("base_x", "base_y", "base_z", "radius", "height", "cap", "name"),
    ),
    "create_cone": (
        code_templates.create_cone_code,
        ("base_x", "base_y", "base_z", "radius", "height", "cap", "name"),
    ),
    "create_pipe": (
        code_templates.create_pipe_code,
        ("curve_id", "radius", "cap", "name"),
    ),
    "move_object": (
        code_templates.move_objects_code,
        ("object_ids", "dx", "dy", "dz"),
    ),
    "copy_object": (
        code_templates.copy_objects_code,
        ("object_ids", "dx", "dy", "dz"),
    ),
    "rotate_object": (
        code_templates.rotate_objects_code,
        ("object_ids", "angle_degrees", "center", "axis"),
    ),
    "scale_object": (
        code_templates.scale_objects_code,
        ("object_ids", "scale_factor", "origin_x", "origin_y", "origin_z"),
    ),
    "set_bbox_dimension": (
        code_templates.set_bbox_dimension_code,
        ("object_id", "axis", "target_size", "anchor"),
    ),
    "mirror_object": (
        code_templates.mirror_objects_code,
        ("object_ids", "mirror_plane_origin", "mirror_plane_normal", "copy"),
    ),
    "array_linear": (
        code_templates.array_linear_code,
        ("object_id", "count", "dx", "dy", "dz"),
    ),
    "array_polar": (
        code_templates.array_polar_code,
        ("object_id", "count", "center", "axis", "total_angle"),
    ),
    "delete_object": (
        code_templates.delete_objects_code,
        ("object_ids", "archive_first"),
    ),
    "set_layer": (
        code_templates.set_layer_code,
        ("object_ids", "layer", "create_if_missing"),
    ),
    "rename_object": (
        code_templates.rename_object_code,
        ("object_id", "name"),
    ),
    "offset_curve": (
        code_templates.offset_curve_code,
        ("curve_id", "distance", "direction_point", "name"),
    ),
    "join_curves": (
        code_templates.join_curves_code,
        ("curve_ids", "name"),
    ),
    "curve_boolean_union": (
        code_templates.curve_boolean_union_code,
        ("curve_ids", "delete_input", "name"),
    ),
    "fillet_curve": (
        code_templates.fillet_curves_code,
        ("curve_id_1", "curve_id_2", "radius", "name"),
    ),
    "divide_curve": (
        code_templates.divide_curve_code,
        ("curve_id", "segment_count"),
    ),
    "extrude_curve": (
        code_templates.extrude_curve_code,
        ("curve_id", "dx", "dy", "dz", "cap", "name"),
    ),
    "loft_curves": (
        code_templates.loft_curves_code,
        ("curve_ids", "loft_type", "closed", "name"),
    ),
    "sweep1": (
        code_templates.sweep1_code,
        ("rail_id", "cross_section_ids", "closed", "name"),
    ),
    "revolve_curve": (
        code_templates.revolve_curve_code,
        ("curve_id", "axis_start", "axis_end", "start_angle", "end_angle", "name"),
    ),
    "planar_surface": (
        code_templates.planar_surface_code,
        ("curve_ids", "name"),
    ),
    "cap_planar_holes": (
        code_templates.cap_planar_holes_code,
        ("brep_id",),
    ),
    "join_surfaces": (
        code_templates.join_surfaces_code,
        ("object_ids", "delete_input", "name"),
    ),
    "boolean_union": (
        code_templates.boolean_union_code,
        ("object_ids", "delete_input", "name"),
    ),
    "boolean_difference": (
        code_templates.boolean_difference_code,
        ("keep_id", "remove_ids", "delete_input", "name"),
    ),
    "boolean_intersection": (
        code_templates.boolean_intersection_code,
        ("object_ids", "delete_input", "name"),
    ),
    "boolean_split": (
        code_templates.boolean_split_code,
        ("object_id", "cutter_id", "delete_input", "name"),
    ),
    "create_hole": (
        code_templates.create_hole_code,
        ("object_id", "center", "diameter", "direction", "depth", "through"),
    ),
    "create_slot": (
        code_templates.create_slot_code,
        (
            "object_id",
            "center",
            "length",
            "width",
            "slot_axis",
            "direction",
            "depth",
            "through",
            "end_shape",
        ),
    ),
    "get_scene_info": (
        code_templates.get_scene_info_code,
        ("max_objects_per_layer",),
    ),
    "get_layer_info": (
        code_templates.get_layer_info_code,
        ("layer_name", "include_objects", "max_objects"),
    ),
    "get_object_info": (
        code_templates.get_object_info_code,
        ("object_ids", "include_user_text"),
    ),
    "get_brep_component_info": (
        code_templates.get_brep_component_info_code,
        ("object_id", "component_type", "component_index"),
    ),
    "resolve_reference": (
        code_templates.resolve_reference_code,
        ("query", "component_type", "scope_object_ids", "max_results"),
    ),
    "resize_box_face": (
        code_templates.resize_box_face_code,
        ("object_id", "face_index", "distance"),
    ),
    "resize_cylinder_face": (
        code_templates.resize_cylinder_face_code,
        ("object_id", "face_index", "distance"),
    ),
    "resize_extrusion_face": (
        code_templates.resize_extrusion_face_code,
        ("object_id", "face_index", "distance"),
    ),
    "fillet_brep_edge": (
        code_templates.fillet_brep_edge_code,
        ("object_id", "edge_index", "edge_indices", "radius", "rail_type"),
    ),
    "round_edges_by_rule": (
        code_templates.round_edges_by_rule_code,
        ("object_id", "rule", "radius", "rail_type"),
    ),
    "chamfer_brep_edge": (
        code_templates.chamfer_brep_edge_code,
        ("object_id", "edge_index", "edge_indices", "distance", "rail_type"),
    ),
    "move_brep_face_along_normal": (
        code_templates.move_brep_face_along_normal_code,
        ("object_id", "face_index", "distance"),
    ),
    "move_brep_face_in_direction": (
        code_templates.move_brep_face_in_direction_code,
        ("object_id", "face_index", "dx", "dy", "dz"),
    ),
    "create_subd_box": (
        code_templates.create_subd_box_code,
        ("x", "y", "z", "width", "depth", "height", "x_faces", "y_faces", "z_faces", "name"),
    ),
    "create_subd_sphere": (
        code_templates.create_subd_sphere_code,
        ("center_x", "center_y", "center_z", "radius", "subdivisions", "name"),
    ),
    "create_subd_cylinder": (
        code_templates.create_subd_cylinder_code,
        ("base_x", "base_y", "base_z", "radius", "height", "radial_faces", "height_faces", "cap", "name"),
    ),
    "mesh_to_subd": (
        code_templates.mesh_to_subd_code,
        ("mesh_id", "name"),
    ),
    "quad_remesh_to_subd": (
        code_templates.quad_remesh_to_subd_code,
        ("object_id", "target_quad_count", "adaptive_quad_count", "name"),
    ),
    "subd_to_nurbs": (
        code_templates.subd_to_nurbs_code,
        ("subd_id", "name"),
    ),
    "subd_to_mesh": (
        code_templates.subd_to_mesh_code,
        ("subd_id", "density", "name"),
    ),
    "get_subd_info": (
        code_templates.get_subd_info_code,
        ("subd_id",),
    ),
    "subd_crease_edges": (
        code_templates.subd_crease_edges_code,
        ("subd_id", "edge_indices", "crease"),
    ),
    "subd_set_vertex_position": (
        code_templates.subd_set_vertex_position_code,
        ("subd_id", "vertex_index", "x", "y", "z"),
    ),
    "subd_extrude_faces": (
        code_templates.subd_extrude_faces_code,
        ("subd_id", "face_indices", "distance", "name"),
    ),
    "subd_offset_faces": (
        code_templates.subd_offset_faces_code,
        ("subd_id", "face_indices", "distance"),
    ),
    "subd_subdivide": (
        code_templates.subd_subdivide_code,
        ("subd_id", "levels"),
    ),
    "get_layers": (
        code_templates.get_layers_code,
        (),
    ),
    "get_scene_objects_with_metadata": (
        code_templates.get_scene_objects_with_metadata_code,
        ("filters", "metadata_fields"),
    ),
    "get_selected": (
        code_templates.get_selected_code,
        (),
    ),
    "select_objects": (
        code_templates.select_objects_code,
        ("object_ids", "replace", "zoom"),
    ),
    "list_backups": (
        code_templates.list_backups_code,
        ("object_name",),
    ),
    "restore_object": (
        code_templates.restore_object_code,
        ("backup_id", "object_name"),
    ),
    "undo_last_action": (
        code_templates.undo_last_action_code,
        (),
    ),
    "redo_last_action": (
        code_templates.redo_last_action_code,
        (),
    ),
    "create_arc": (
        code_templates.create_arc_code,
        ("center_x", "center_y", "center_z", "radius", "start_angle", "end_angle", "name"),
    ),
    "create_torus": (
        code_templates.create_torus_code,
        ("center_x", "center_y", "center_z", "major_radius", "minor_radius", "name"),
    ),
    "create_interpolated_curve": (
        code_templates.create_interpolated_curve_code,
        ("points", "degree", "name"),
    ),
    "create_control_point_curve": (
        code_templates.create_control_point_curve_code,
        ("points", "degree", "name"),
    ),
    "explode_curves": (
        code_templates.explode_curves_code,
        ("curve_id",),
    ),
    "extend_curve": (
        code_templates.extend_curve_code,
        ("curve_id", "length", "side", "extension_type"),
    ),
    "rebuild_curve": (
        code_templates.rebuild_curve_code,
        ("curve_id", "point_count", "degree"),
    ),
    "project_curve_to_surface": (
        code_templates.project_curve_to_surface_code,
        ("curve_id", "surface_id", "direction", "name"),
    ),
    "sweep2": (
        code_templates.sweep2_code,
        ("rail1_id", "rail2_id", "cross_section_ids", "name"),
    ),
    "offset_surface": (
        code_templates.offset_surface_code,
        ("surface_id", "distance", "name"),
    ),
    "thicken_surface_to_solid": (
        code_templates.thicken_surface_to_solid_code,
        ("surface_id", "distance", "both_sides", "extend", "delete_input", "name"),
    ),
}

_GRASSHOPPER_DISPATCH: dict[str, tuple[str, ...]] = {
    # is_server_available is intentionally NOT here — it's plumbing, not a
    # design tool. The bridge checks GH reachability itself on every GH
    # command (see grasshopper_bridge.dispatch_grasshopper_tool) so the
    # LLM never has to spend a tool round on a pre-flight check.
    "execute_gh_code": ("code",),
    "get_gh_context": ("simplified",),
    "get_objects": ("instance_guids", "simplified", "context_depth"),
    "get_gh_selected": ("simplified", "context_depth"),
    "update_script": (
        "instance_guid",
        "code",
        "description",
        "message_to_user",
        "param_definitions",
    ),
    "update_script_with_code_reference": (
        "instance_guid",
        "file_path",
        "param_definitions",
        "description",
        "name",
        "force_code_reference",
    ),
    "expire_and_get_info": ("instance_guid",),
    "add_component": (
        "component_name",
        "position_x",
        "position_y",
        "nickname",
        "connections",
    ),
    "clear_gh_session": (),
    "arrange_gh_layout": ("base_x", "base_y", "step_x", "step_y"),
}


_SESSION_AWARE_TOOLS = {
    "expose_parameters",
    "clear_parameters",
    "create_variant",
    "select_variant",
    "finish_variants",
    "delete_variant",
    "clear_variants",
}


def is_dedicated_tool(name: str) -> bool:
    return (
        name in _DISPATCH
        or name in _GRASSHOPPER_DISPATCH
        or name in _SESSION_AWARE_TOOLS
    )
