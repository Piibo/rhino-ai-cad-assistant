"""Shared Rhino-Python source for action-history / undo tracking.

This module exports ``ACTION_HISTORY_PREAMBLE`` — a Python source string
containing ``begin_action`` / ``finish_action`` / ``undo_last_action``
plus the private helpers ``_get_action_history``, ``_record_created_object``
and ``_record_backup``.

Both ``rhino_mcp/helpers.py`` (MCP-server CODE_PREAMBLE) and
``plugin/backend/rhino_exec.py`` (Plugin CODE_PREAMBLE) concatenate this
string into their own CODE_PREAMBLE so the runtime Rhino-Python context
has identical undo semantics in both modes. Before this module existed
the same ~170 lines lived in three places (helpers.py, rhino_exec.py,
and the orphan tool_executor.py) and had to be patched three times
whenever the undo logic changed.

The source assumes ``rs`` (rhinoscriptsyntax), ``sc`` (scriptcontext),
``System``, ``time`` and ``datetime`` are already imported by the
surrounding preamble — every consumer brings those in their own
boilerplate header.

Compatibility note: must stay IronPython 2.7 compatible (no f-strings,
no walrus, no ``match``) because the MCP server still sends code to
Rhino's internal Python that might be IronPython on older Rhino
versions. Same constraint that already applies to the surrounding
helpers.py PREAMBLE.
"""

from __future__ import annotations


ACTION_HISTORY_PREAMBLE = r'''
_ACTION_HISTORY_KEY = "__furniture_action_history__"
_ACTION_CURRENT_KEY = "__furniture_current_action__"
_ACTION_REDO_KEY = "__furniture_redo_history__"
_ACTION_NATIVE_REDO_KEY = "__furniture_native_redo_available__"
_ACTION_NATIVE_REDO_ACTION_KEY = "__furniture_native_redo_action__"
_ACTION_SEQ_KEY = "__furniture_action_seq__"

def _get_action_history():
    history = sc.sticky.get(_ACTION_HISTORY_KEY)
    if not isinstance(history, list):
        history = []
        sc.sticky[_ACTION_HISTORY_KEY] = history
    return history

def _get_redo_history():
    history = sc.sticky.get(_ACTION_REDO_KEY)
    if not isinstance(history, list):
        history = []
        sc.sticky[_ACTION_REDO_KEY] = history
    return history

def _get_action_by_id(action_id):
    if not action_id:
        return None
    for action in reversed(_get_action_history()):
        if action.get("id") == action_id:
            return action
    return None

def _next_action_id():
    # Eindeutige Action-ID: Mikrosekunden-Zeitstempel + monotoner Sequenz-
    # Zaehler, damit zwei in derselben Mikrosekunde erzeugte Aktionen nicht
    # kollidieren (sonst schloesse finish_action/_get_action_by_id die falsche
    # Aktion). Verwendet von begin_action UND den undo/redo-Pfaden, damit ALLE
    # Action-IDs konsistent eindeutig sind (nicht nur die von begin_action).
    _seq = (sc.sticky.get(_ACTION_SEQ_KEY) or 0) + 1
    sc.sticky[_ACTION_SEQ_KEY] = _seq
    return "{0}_{1}".format(datetime.now().strftime("%Y%m%d_%H%M%S_%f"), _seq)


def begin_action(action_name="tool"):
    action_id = _next_action_id()
    action = {
        "id": action_id,
        "name": action_name or "tool",
        "created_ids": [],
        "backups": [],
        "started_at": time.time(),
    }
    history = _get_action_history()
    history.append(action)
    sc.sticky[_ACTION_HISTORY_KEY] = history
    # NOTE: the redo stack is deliberately NOT cleared here anymore. Only an
    # EFFECTIVE action (one that recorded created_ids/backups) invalidates
    # redo, and that happens in finish_action. Clearing at begin meant a
    # read-only or no-effect tool call destroyed a legitimate redo the
    # designer might still want (Pilot 01.07.2026).
    sc.sticky[_ACTION_NATIVE_REDO_KEY] = False
    sc.sticky[_ACTION_NATIVE_REDO_ACTION_KEY] = None
    sc.sticky[_ACTION_CURRENT_KEY] = action_id
    return action_id

def finish_action(action_id=None, clear_redo=True):
    history = _get_action_history()
    target_id = action_id or sc.sticky.get(_ACTION_CURRENT_KEY)
    action = _get_action_by_id(target_id)
    if action and not action.get("created_ids") and not action.get("backups"):
        # No-op / ineffective action: prune it and DO NOT touch the redo
        # stack (a read-only or no-effect tool must not wipe a legitimate
        # redo — Pilot 01.07.2026).
        try:
            history.remove(action)
        except Exception:
            pass
        sc.sticky[_ACTION_HISTORY_KEY] = history
    elif action:
        # Store the RECONCILE fingerprint (Archive-excluded, GUID-free) — the
        # SAME function used later to detect a manual intervening edit, so the
        # comparison can actually match after a backup restore re-GUIDs objects.
        action["fingerprint_after"] = _reconcile_fingerprint()
        # A real, effective NEW action after an undo invalidates the redo
        # stack (classic undo semantics). Moved here from begin_action.
        # redo_last_action re-uses finish_action for its OWN bookkeeping and
        # passes clear_redo=False, so replaying one redo step does not wipe
        # the rest of the redo stack (multi-step redo would break otherwise).
        if clear_redo:
            sc.sticky[_ACTION_REDO_KEY] = []
        sc.sticky[_ACTION_HISTORY_KEY] = history
    if sc.sticky.get(_ACTION_CURRENT_KEY) == target_id:
        sc.sticky[_ACTION_CURRENT_KEY] = None
    return target_id

def _record_created_object(obj_id):
    action = _get_action_by_id(sc.sticky.get(_ACTION_CURRENT_KEY))
    if not action:
        return
    obj_str = str(obj_id)
    created_ids = action.setdefault("created_ids", [])
    if obj_str not in created_ids:
        created_ids.append(obj_str)

def _record_backup(source_id, backup_id, source_name):
    action = _get_action_by_id(sc.sticky.get(_ACTION_CURRENT_KEY))
    if not action:
        return
    backup_str = str(backup_id)
    backups = action.setdefault("backups", [])
    for entry in backups:
        if entry.get("backup_id") == backup_str:
            return
    backups.append({
        "source_id": str(source_id) if source_id else "",
        "backup_id": backup_str,
        "source_name": source_name or "",
    })

def _find_live_object_by_name(name):
    if not name:
        return None
    try:
        candidates = rs.ObjectsByName(name)
    except Exception:
        candidates = None
    if not candidates:
        return None
    for oid in candidates:
        try:
            if rs.ObjectLayer(oid) != "Archive":
                return oid
        except Exception:
            pass
    return candidates[0]

def _snapshot_live_object_for_redo(obj_id, name_hint, kind="modified"):
    # ``kind`` distinguishes how redo_last_action must re-apply this snapshot:
    #   "created"  -> the live object was CREATED by the action and got
    #                 deleted on undo. Redo just re-instantiates the snapshot;
    #                 it must NOT look up a live object by name and delete it,
    #                 because several created siblings of ONE action can share
    #                 a name (or be nameless), so a name-based delete would
    #                 wipe a sibling that an earlier redo iteration just
    #                 restored (root cause of the multi-bake undo/redo bug).
    #   "modified" -> the live object EXISTED before and was changed/replaced
    #                 by the action; redo must remove the current live version
    #                 before restoring the snapshot. Kept name-based for back-
    #                 compat, but guarded against same-pass siblings in redo.
    try:
        copy_id = rs.CopyObject(obj_id)
        if not copy_id:
            return None
        if not rs.IsLayer("Archive"):
            rs.AddLayer("Archive", [128, 128, 128])
            rs.LayerVisible("Archive", False)
        rs.ObjectLayer(copy_id, "Archive")
        source_name = name_hint or rs.ObjectName(obj_id) or "RedoObject"
        # Give each redo snapshot a GUID-unique Archive name so several
        # same-named siblings of one action don't collapse on the Archive
        # layer (rs.ObjectsByName / dedup would otherwise see them as one).
        rs.ObjectName(copy_id, "{0}_redo_{1}".format(source_name, str(copy_id)))
        return {
            "backup_id": str(copy_id),
            "source_name": source_name,
            "source_id": str(obj_id) if obj_id else "",
            "kind": kind,
        }
    except Exception:
        return None

def _object_signature_row(oid):
    """Build the per-object signature row used by both the scene
    fingerprint and the scripted-action pre/post diff.

    Mirrors exactly the row shape that ``_scene_fingerprint`` produced
    inline before this helper was extracted, so its JSON output stays
    byte-identical: ``[id_lower, name, layer, geometry_type, hidden,
    locked, bbox_data, topo]`` on success, or
    ``[id_lower, "fingerprint_error", err]`` on failure.
    """
    try:
        bbox_data = []
        bbox = rs.BoundingBox(oid)
        if bbox:
            for p in bbox:
                bbox_data.append([
                    round(float(p.X), 4),
                    round(float(p.Y), 4),
                    round(float(p.Z), 4),
                ])
        obj = sc.doc.Objects.Find(oid)
        geometry_type = ""
        topo = []
        if obj and obj.Geometry:
            geom = obj.Geometry
            geometry_type = geom.GetType().FullName
            # Topology signature: face/edge/vertex counts change on
            # fillet / hole / slot / boolean / SubD edits even when the
            # outer bounding box is unchanged (a fillet rounds inward, so
            # the bbox alone misses it). Geometry-agnostic via duck typing
            # (Brep, SubD, Mesh, Extrusion all expose these or are skipped
            # gracefully). Without this, undo-change-detection can't tell a
            # filleted solid from its sharp original.
            try:
                faces = getattr(geom, "Faces", None)
                edges = getattr(geom, "Edges", None)
                verts = getattr(geom, "Vertices", None)
                topo = [
                    faces.Count if faces is not None else -1,
                    edges.Count if edges is not None else -1,
                    verts.Count if verts is not None else -1,
                ]
            except Exception:
                topo = []
        return [
            str(oid).lower(),
            rs.ObjectName(oid) or "",
            rs.ObjectLayer(oid) or "",
            geometry_type,
            bool(rs.IsObjectHidden(oid)),
            bool(rs.IsObjectLocked(oid)),
            bbox_data,
            topo,
        ]
    except Exception as e:
        return [str(oid).lower(), "fingerprint_error", str(e)]

def _live_object_rows():
    """Signature rows for every live object EXCEPT Archive-layer backups.

    Returns ``(rows, id_map)`` where ``rows`` is a list of
    ``_object_signature_row`` results (sorted by id, same as
    ``_scene_fingerprint``) and ``id_map`` maps the lowercased id string
    to the live object id. Used by begin/finish_scripted_action to take a
    before/after diff of the user-visible scene. Archive objects are
    skipped so the diff isn't polluted by pre-emptive backup copies.
    """
    rows = []
    id_map = {}
    try:
        object_ids = rs.AllObjects() or []
    except Exception:
        object_ids = []
    for oid in object_ids:
        try:
            if rs.ObjectLayer(oid) == "Archive":
                continue
        except Exception:
            pass
        row = _object_signature_row(oid)
        rows.append(row)
        try:
            id_map[str(oid).lower()] = oid
        except Exception:
            pass
    rows.sort(key=lambda item: item[0])
    return rows, id_map

def _scene_fingerprint():
    """Compact snapshot of document objects for manual-change detection."""
    rows = []
    try:
        object_ids = rs.AllObjects() or []
    except Exception:
        object_ids = []

    for oid in object_ids:
        rows.append(_object_signature_row(oid))

    rows.sort(key=lambda item: item[0])
    try:
        return json.dumps(rows, sort_keys=True)
    except Exception:
        return str(rows)

def _reconcile_fingerprint():
    """Change-detection signature that is STABLE across undo/redo cycles.

    Differs from _scene_fingerprint on the two points that made the old exact
    whole-scene match self-defeating (Pilot 01.07.2026 native-cascade): it
    EXCLUDES the hidden Archive-layer backups (so taking a backup copy doesn't
    flip the signature) and it DROPS the per-object GUID (row[0]) so an object
    that undo/redo re-created via rs.CopyObject with a fresh GUID still compares
    equal. Used only as a soft manual-edit warning signal, never as a hard gate.
    """
    rows = []
    try:
        object_ids = rs.AllObjects() or []
    except Exception:
        object_ids = []
    for oid in object_ids:
        try:
            if rs.ObjectLayer(oid) == "Archive":
                continue
        except Exception:
            pass
        row = _object_signature_row(oid)
        if isinstance(row, list) and len(row) > 1:
            rows.append(row[1:])
        else:
            rows.append(row)
    try:
        rows.sort(key=lambda item: json.dumps(item, sort_keys=True))
    except Exception:
        try:
            rows.sort()
        except Exception:
            pass
    try:
        return json.dumps(rows, sort_keys=True)
    except Exception:
        return str(rows)

def _scene_object_ids_from_fingerprint(fingerprint):
    try:
        rows = json.loads(fingerprint or "[]")
    except Exception:
        rows = []
    ids = []
    for row in rows:
        try:
            ids.append(str(row[0]))
        except Exception:
            pass
    return ids

def _run_native_history_command(command_name):
    before = _scene_fingerprint()
    ok = False
    message = ""
    try:
        ok = bool(rs.Command(command_name, False))
    except Exception as e:
        message = str(e)
    after = _scene_fingerprint()
    changed = before != after
    before_ids = set(_scene_object_ids_from_fingerprint(before))
    after_ids = set(_scene_object_ids_from_fingerprint(after))
    return {
        "status": "success" if ok or changed else "error",
        "mode": "rhino_native",
        "command": command_name,
        "ok": ok,
        "changed": changed,
        "added_ids": sorted(list(after_ids - before_ids)),
        "removed_ids": sorted(list(before_ids - after_ids)),
        "message": message or (
            "Rhino command {0} executed.".format(command_name)
            if ok or changed
            else "Rhino command {0} did not change the scene.".format(command_name)
        ),
    }

def _latest_action_matches_scene(action, fingerprint=None):
    expected = action.get("fingerprint_after") if action else None
    if not expected:
        return False
    current = fingerprint if fingerprint is not None else _scene_fingerprint()
    return current == expected

def _action_has_plugin_undo_data(action):
    if not action:
        return False
    return bool(action.get("created_ids") or action.get("backups"))

def _pop_latest_action():
    history = _get_action_history()
    if not history:
        return None
    action = history.pop()
    sc.sticky[_ACTION_HISTORY_KEY] = history
    if sc.sticky.get(_ACTION_CURRENT_KEY) == action.get("id"):
        sc.sticky[_ACTION_CURRENT_KEY] = None
    return action

def _push_action(action):
    if not action:
        return
    history = _get_action_history()
    history.append(action)
    sc.sticky[_ACTION_HISTORY_KEY] = history

def smart_undo_last_action():
    """Deterministic plugin-backup undo — the single source of truth.

    Pilot 01.07.2026 rebuild: the old three-way reconciliation (plugin backup
    vs. Rhino native _Undo, gated by an exact whole-scene fingerprint) cascaded
    into the native path after the first undo and oscillated (one native record
    != one plugin action). Native undo is dropped from this path entirely:
    every mutating tool archives its inputs / records its creations, and raw
    execute_rhino_code is wrapped in begin_scripted_action, so the Archive
    backup already covers everything reliably and session-locally.
    """
    history = _get_action_history()
    # Session-scope guard (Bug A): with no tracked action in this session, do
    # nothing — never reach into Rhino's process-global native undo stack
    # (which could resurrect a previous session's geometry).
    if not history:
        return {
            "status": "success",
            "ok": True,
            "changed": False,
            "mode": "noop",
            "reason": "No tracked action in this session; nothing to undo.",
        }
    action = history[-1]
    if _action_has_plugin_undo_data(action):
        expected = action.get("fingerprint_after")
        before = _reconcile_fingerprint() if expected else None
        result = undo_last_action()
        result["mode"] = "plugin_backup"
        result["reason"] = "Reverted the last tracked action from its Archive backup."
        # Soft manual-edit signal (NOT a gate): if the visible scene no longer
        # matches this action's recorded post-state, the designer may have
        # changed something in Rhino by hand since. Surface it so the model/UI
        # can warn the user — never suppress it silently.
        if expected and before is not None and before != expected:
            result.setdefault("warnings", []).append(
                "Hinweis: Die Szene wurde seit diesem Schritt moeglicherweise "
                "manuell veraendert. Das Undo hat die protokollierte Version "
                "wiederhergestellt; eine zwischenzeitliche Handaenderung kann "
                "dabei ueberschrieben worden sein."
            )
        return result
    # In history but no recoverable backup data (shouldn't happen once
    # finish_action prunes empty actions). Drop it rather than reaching for
    # native undo.
    _pop_latest_action()
    return {
        "status": "success",
        "ok": True,
        "changed": False,
        "mode": "noop",
        "reason": "Last tracked action had no reversible changes.",
    }

def smart_redo_last_action():
    """Deterministic plugin-backup redo, mirroring smart_undo_last_action.

    The native _Redo path is dropped: it desynced with the plugin redo stack
    (a native undo left the plugin redo_history armed too, so a later redo
    double-applied) and hit indeterminate partial records. redo_last_action
    replays the snapshots captured during undo — the reliable path.
    """
    redo_history = _get_redo_history()
    if redo_history:
        result = redo_last_action()
        result["mode"] = "plugin_backup"
        return result
    return {
        "status": "success",
        "ok": True,
        "changed": False,
        "mode": "noop",
        "reason": "Nothing to redo.",
    }

def undo_last_action():
    history = _get_action_history()
    if not history:
        return {
            "status": "error",
            "message": "No undo information available.",
        }

    action = history.pop()
    sc.sticky[_ACTION_HISTORY_KEY] = history
    if sc.sticky.get(_ACTION_CURRENT_KEY) == action.get("id"):
        sc.sticky[_ACTION_CURRENT_KEY] = None

    deleted_created_ids = []
    restored_ids = []
    warnings = []
    redo_entry = {
        "id": action.get("id") or _next_action_id(),
        "name": action.get("name") or "tool",
        "backups": [],
    }

    for obj_str in reversed(action.get("created_ids", [])):
        try:
            guid = System.Guid(obj_str)
            if sc.doc.Objects.Find(guid):
                snap = _snapshot_live_object_for_redo(
                    guid, rs.ObjectName(guid) or "CreatedObject", "created"
                )
                if snap:
                    redo_entry["backups"].append(snap)
                rs.DeleteObject(guid)
                deleted_created_ids.append(obj_str)
        except Exception as e:
            warnings.append("delete created failed for {0}: {1}".format(obj_str, e))

    for entry in reversed(action.get("backups", [])):
        backup_id = entry.get("backup_id") or ""
        try:
            backup_guid = System.Guid(backup_id)
            backup_obj = sc.doc.Objects.Find(backup_guid)
            if backup_obj is None:
                warnings.append("backup missing: {0}".format(backup_id))
                continue

            source_guid = None
            source_id = entry.get("source_id") or ""
            if source_id:
                try:
                    candidate = System.Guid(source_id)
                    if sc.doc.Objects.Find(candidate):
                        source_guid = candidate
                except Exception:
                    source_guid = None
            if source_guid is None:
                source_guid = _find_live_object_by_name(entry.get("source_name") or "")

            if source_guid:
                try:
                    snap = _snapshot_live_object_for_redo(
                        source_guid, entry.get("source_name") or "", "modified"
                    )
                    if snap:
                        redo_entry["backups"].append(snap)
                    rs.DeleteObject(source_guid)
                except Exception:
                    pass

            restored_id = rs.CopyObject(backup_guid)
            if not restored_id:
                warnings.append("restore failed for backup: {0}".format(backup_id))
                continue

            if not rs.IsLayer("Active"):
                rs.AddLayer("Active", [0, 255, 0])
            rs.ObjectLayer(restored_id, "Active")
            source_name = entry.get("source_name") or rs.ObjectName(backup_guid) or "RestoredObject"
            rs.ObjectName(restored_id, source_name)
            for key in ("archive_source_id", "archive_source_name", "archive_timestamp", "archive_version"):
                try:
                    rs.SetUserText(restored_id, key, "")
                except Exception:
                    pass
            restored_ids.append(str(restored_id))
            if source_guid is None:
                # The original input was CONSUMED (deleted) by this action — e.g.
                # boolean_*(delete_input=True). We restored it for undo, but on
                # REDO the action's result is re-created from its own 'created'
                # snapshot, so this restored input must be removed again; else
                # redo leaves BOTH the result and the input (duplicate geometry).
                redo_entry["backups"].append({
                    "kind": "consumed_input",
                    "redo_delete_id": str(restored_id),
                    "source_name": source_name,
                })
        except Exception as e:
            warnings.append("restore failed for backup {0}: {1}".format(backup_id, e))

    rs.Redraw()
    if redo_entry.get("backups"):
        redo_history = _get_redo_history()
        redo_history.append(redo_entry)
        sc.sticky[_ACTION_REDO_KEY] = redo_history
    return {
        "status": "success",
        "message": "Undid action '{0}'".format(action.get("name") or "tool"),
        "action_name": action.get("name") or "tool",
        "deleted_created_count": len(deleted_created_ids),
        "deleted_created_ids": deleted_created_ids,
        "restored_count": len(restored_ids),
        "restored_ids": restored_ids,
        "warnings": warnings,
    }

def redo_last_action():
    redo_history = _get_redo_history()
    if not redo_history:
        return {
            "status": "error",
            "message": "No redo information available.",
        }

    entry = redo_history.pop()
    sc.sticky[_ACTION_REDO_KEY] = redo_history

    restored_ids = []
    warnings = []
    recreated_action = {
        "id": _next_action_id(),
        "name": entry.get("name") or "tool",
        "created_ids": [],
        "backups": [],
        "started_at": time.time(),
    }
    history = _get_action_history()
    history.append(recreated_action)
    sc.sticky[_ACTION_HISTORY_KEY] = history
    sc.sticky[_ACTION_CURRENT_KEY] = recreated_action["id"]

    # Track GUIDs (re)created during THIS redo pass so a later "modified"
    # backup sharing a name with an earlier "created" sibling can't delete it
    # via the name-based lookup below. Without this, several same-named
    # siblings of one action would clobber each other (multi-bake undo/redo
    # bug: redo restored only the last leg).
    restored_this_pass = set()
    for backup in reversed(entry.get("backups", [])):
        backup_id = backup.get("backup_id") or ""
        source_name = backup.get("source_name") or "RedoObject"
        backup_kind = backup.get("kind") or "modified"
        if backup_kind == "consumed_input":
            # Input the original action consumed (deleted); undo restored it and
            # redo must remove it again (the result is re-created from its own
            # 'created' snapshot). Archive first so a wrong delete is recoverable.
            del_id = backup.get("redo_delete_id") or ""
            target = None
            try:
                cand = System.Guid(del_id)
                if sc.doc.Objects.Find(cand):
                    target = cand
            except Exception:
                target = None
            if target is None:
                target = _find_live_object_by_name(backup.get("source_name") or "")
            if target:
                try:
                    archive_object(target)
                except Exception:
                    pass
                try:
                    rs.DeleteObject(target)
                except Exception as e:
                    warnings.append("redo consumed-input delete failed: {0}".format(e))
            continue
        try:
            backup_guid = System.Guid(backup_id)
            backup_obj = sc.doc.Objects.Find(backup_guid)
            if backup_obj is None:
                warnings.append("redo backup missing: {0}".format(backup_id))
                continue

            # Only "modified" backups need to replace a still-live object of
            # the same name. "created" backups are pure re-instantiations of
            # objects undo already deleted — looking them up by name would
            # (a) find a same-named sibling restored earlier in this pass and
            # (b) wrongly delete it. So skip the name-based delete entirely
            # for created objects.
            if backup_kind != "created":
                # Replace the current live version of a modified object. Be
                # conservative about WHICH object to delete: the stored
                # source GUID is stale after undo re-created the object, so we
                # fall back to the name — but only act on an UNAMBIGUOUS single
                # non-Archive match not already restored in this pass. Several
                # same-named siblings -> leave them and warn: a possible
                # duplicate is safer than deleting the wrong sibling (Pilot
                # 01.07.2026 name-churn fragility).
                live_matches = []
                try:
                    for cand in (rs.ObjectsByName(source_name) or []):
                        try:
                            if str(cand) in restored_this_pass:
                                continue
                            if rs.ObjectLayer(cand) == "Archive":
                                continue
                            live_matches.append(cand)
                        except Exception:
                            pass
                except Exception:
                    live_matches = []
                if len(live_matches) == 1:
                    live_obj = live_matches[0]
                    try:
                        archive_object(live_obj)
                    except Exception:
                        pass
                    try:
                        rs.DeleteObject(live_obj)
                    except Exception:
                        pass
                elif len(live_matches) > 1:
                    warnings.append(
                        "redo: mehrere gleichnamige Objekte '{0}' — Live-Version nicht ersetzt (Duplikat moeglich)".format(source_name)
                    )

            restored_id = rs.CopyObject(backup_guid)
            if not restored_id:
                warnings.append("redo restore failed for backup: {0}".format(backup_id))
                continue

            if not rs.IsLayer("Active"):
                rs.AddLayer("Active", [0, 255, 0])
            rs.ObjectLayer(restored_id, "Active")
            rs.ObjectName(restored_id, source_name)
            for key in ("archive_source_id", "archive_source_name", "archive_timestamp", "archive_version"):
                try:
                    rs.SetUserText(restored_id, key, "")
                except Exception:
                    pass
            recreated_action["created_ids"].append(str(restored_id))
            restored_ids.append(str(restored_id))
            restored_this_pass.add(str(restored_id))
        except Exception as e:
            warnings.append("redo failed for backup {0}: {1}".format(backup_id, e))

    # clear_redo=False: this finish_action is redo's OWN bookkeeping for the
    # re-created action — it must NOT wipe the remaining redo stack, or a
    # multi-step redo would only replay its first step.
    finish_action(recreated_action["id"], clear_redo=False)

    rs.Redraw()
    return {
        "status": "success",
        "message": "Redid action '{0}'".format(entry.get("name") or "tool"),
        "action_name": entry.get("name") or "tool",
        "restored_count": len(restored_ids),
        "restored_ids": restored_ids,
        "warnings": warnings,
    }

# Cap above which pre-emptive full-scene archiving is skipped: archiving
# every object on a large scene would bloat the Archive layer and slow the
# tool call. Above this we fall back to created-only diffing (edits to
# existing objects won't be undoable, but creation still is).
_SCRIPTED_ARCHIVE_CAP = 150

def begin_scripted_action(action_name="execute_rhino_code"):
    """Open an action and pre-emptively archive the whole live scene.

    Raw RhinoCommon in user code (doc.Objects.Delete + AddBrep,
    Objects.Replace, …) bypasses the plugin's archive_object/_record_*
    hooks, so undo_last_action would have nothing to restore. This wraps
    such code in the reliable plugin-backup undo path: it snapshots a
    before-signature for every live (non-Archive) object and archives
    each one, so even untracked in-place edits and deletes can be undone.
    The matching finish_scripted_action prunes untouched backups again.
    """
    action_id = begin_action(action_name)
    rows, id_map = _live_object_rows()
    action = _get_action_by_id(action_id)
    if action is None:
        return action_id
    if len(rows) > _SCRIPTED_ARCHIVE_CAP:
        # Scene too large to pre-archive wholesale — record before-rows
        # so finish can still diff for created objects, but skip the
        # per-object backups (created-only undo for this call).
        action["scripted_rows_before"] = rows
        action["scripted_pre_archived"] = False
        sc.sticky[_ACTION_HISTORY_KEY] = _get_action_history()
        return action_id
    for row in rows:
        oid = id_map.get(row[0])
        if oid is None:
            continue
        try:
            archive_object(oid)
        except Exception:
            pass
    action["scripted_rows_before"] = rows
    action["scripted_pre_archived"] = True
    sc.sticky[_ACTION_HISTORY_KEY] = _get_action_history()
    return action_id

def finish_scripted_action(action_id=None):
    """Close a scripted action: record created objects and prune the
    backups of objects the script left untouched.

    Diffs the live scene against the before-snapshot taken in
    begin_scripted_action: ids present after but not before are recorded
    as created (so undo deletes them); for every pre-archived object whose
    source still exists with an UNCHANGED signature row, the Archive copy
    is deleted and its backup entry dropped (prevents Archive bloat and
    avoids GUID churn from "undoing" objects the script never modified).
    Then defers to the normal finish_action.
    """
    target_id = action_id or sc.sticky.get(_ACTION_CURRENT_KEY)
    action = _get_action_by_id(target_id)
    if action is None:
        return finish_action(action_id)

    rows_before = action.get("scripted_rows_before") or []
    rows_after, id_map_after = _live_object_rows()

    before_ids = set()
    for row in rows_before:
        try:
            before_ids.add(row[0])
        except Exception:
            pass
    before_row_by_id = {}
    for row in rows_before:
        try:
            before_row_by_id[row[0]] = row
        except Exception:
            pass

    # (a) created objects = live after, absent before.
    for row in rows_after:
        try:
            rid = row[0]
        except Exception:
            continue
        if rid in before_ids:
            continue
        oid = id_map_after.get(rid)
        if oid is not None:
            try:
                _record_created_object(oid)
            except Exception:
                pass

    # (b) prune backups for objects the script left untouched.
    after_row_by_id = {}
    for row in rows_after:
        try:
            after_row_by_id[row[0]] = row
        except Exception:
            pass
    remaining_backups = []
    for entry in action.get("backups", []):
        source_id = (entry.get("source_id") or "").lower()
        before_row = before_row_by_id.get(source_id)
        after_row = after_row_by_id.get(source_id)
        unchanged = (
            source_id
            and after_row is not None
            and before_row is not None
            and after_row == before_row
        )
        if unchanged:
            backup_id = entry.get("backup_id") or ""
            try:
                rs.DeleteObject(System.Guid(backup_id))
            except Exception:
                pass
            continue
        remaining_backups.append(entry)
    action["backups"] = remaining_backups
    sc.sticky[_ACTION_HISTORY_KEY] = _get_action_history()

    return finish_action(target_id)
'''


__all__ = ["ACTION_HISTORY_PREAMBLE"]
