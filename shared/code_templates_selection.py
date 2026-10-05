"""Code templates: selection operations. See code_templates.py."""
from __future__ import annotations

from typing import List

from ._codegen_base import inject_params

__all__ = [
    "select_objects_code",
    "list_backups_code",
    "restore_object_code",
    "undo_last_action_code",
    "redo_last_action_code",
]


def select_objects_code(
    object_ids: List[str],
    replace: bool = True,
    zoom: bool = False,
) -> str:
    """Select one or more Rhino objects so the designer sees the target."""
    return inject_params(ids=object_ids, replace=replace, zoom=zoom) + """
selected = []
missing = []
invalid = []

if _replace:
    rs.UnselectAllObjects()

for raw_id in (_ids or []):
    try:
        guid = System.Guid(str(raw_id))
    except Exception:
        invalid.append(str(raw_id))
        continue
    obj = sc.doc.Objects.Find(guid)
    if obj is None:
        missing.append(str(raw_id))
        continue
    try:
        ok = bool(rs.SelectObject(guid))
    except Exception:
        ok = False
    if ok:
        selected.append(str(guid))

if _zoom and selected:
    try:
        rs.Command("_Zoom _Selected", False)
    except Exception:
        pass

rs.Redraw()
result = json.dumps({
    "status": "ok" if selected else "error",
    "selected_count": len(selected),
    "selected_ids": selected,
    "missing_ids": missing,
    "invalid_ids": invalid,
    "replaced_previous_selection": bool(_replace),
}, indent=2)
"""


def list_backups_code(object_name: str = "") -> str:
    """List archived backups, optionally filtered by object name."""
    return inject_params(name_filter=object_name) + """
backups = []
archive_objs = rs.ObjectsByLayer("Archive") if rs.IsLayer("Archive") else []
if archive_objs:
    for oid in archive_objs:
        source_id = rs.GetUserText(oid, "archive_source_id") or ""
        source_name = rs.GetUserText(oid, "archive_source_name") or rs.ObjectName(oid) or "Unnamed"
        timestamp = rs.GetUserText(oid, "archive_timestamp") or "unknown"
        version = rs.GetUserText(oid, "archive_version") or "?"

        if _name_filter and _name_filter.lower() not in source_name.lower():
            continue

        source_exists = False
        if source_id:
            try:
                source_exists = sc.doc.Objects.Find(System.Guid(source_id)) is not None
            except Exception:
                pass

        backups.append({
            "backup_id": str(oid),
            "source_name": source_name,
            "source_id": source_id,
            "source_exists": source_exists,
            "version": version,
            "timestamp": timestamp,
            "display_name": rs.ObjectName(oid) or "Unnamed",
        })

backups.sort(key=lambda b: b["timestamp"], reverse=True)
result = json.dumps({"count": len(backups), "backups": backups}, indent=2)
"""


def restore_object_code(
    backup_id: str = "",
    object_name: str = "",
) -> str:
    """Restore an object from the Archive layer."""
    return inject_params(backup_id=backup_id, obj_name=object_name) + """
if not rs.IsLayer("Archive"):
    result = json.dumps({"status": "error", "message": "No Archive layer found"})
else:
    archive_objs = rs.ObjectsByLayer("Archive")
    target_backup = None

    if _backup_id:
        try:
            target_backup = System.Guid(_backup_id)
            if not sc.doc.Objects.Find(target_backup):
                target_backup = None
        except Exception:
            target_backup = None
    elif _obj_name:
        # Match against both the original source_name ("Box" → picks the
        # latest backup version) and the display_name as set by
        # archive_object ("Box_v3" → picks exactly version 3). Without
        # the display_name fallback the model gets "No backup found"
        # when it passes a name straight from list_backups' display_name.
        target = _obj_name.lower()
        best_ts = ""
        if archive_objs:
            for oid in archive_objs:
                source_name = (rs.GetUserText(oid, "archive_source_name") or "").lower()
                display_name = (rs.ObjectName(oid) or "").lower()
                if source_name != target and display_name != target:
                    continue
                ts = rs.GetUserText(oid, "archive_timestamp") or ""
                if ts > best_ts:
                    best_ts = ts
                    target_backup = oid

    if not target_backup:
        result = json.dumps({"status": "error", "message": "No backup found. Use list_backups to see available versions."})
    else:
        source_id = rs.GetUserText(target_backup, "archive_source_id") or ""
        source_name = rs.GetUserText(target_backup, "archive_source_name") or "Unnamed"
        version = rs.GetUserText(target_backup, "archive_version") or "?"

        # Remove the CURRENT live version so restore REPLACES it instead of
        # leaving a duplicate. Prefer the stored source GUID; if that object is
        # gone (its GUID churned through earlier undo/redo/edits) fall back to a
        # single unambiguous non-Archive name match. Several same-named live
        # objects -> don't guess (a possible duplicate is safer than deleting
        # the wrong one). Pilot 01.07.2026: restore left "4 Tischplatten".
        _removed_live = False
        if source_id:
            try:
                source_guid = System.Guid(source_id)
                if sc.doc.Objects.Find(source_guid):
                    archive_object(source_guid)
                    rs.DeleteObject(source_guid)
                    _removed_live = True
            except Exception:
                pass
        if not _removed_live and source_name and source_name != "Unnamed":
            _live_matches = []
            try:
                for _cand in (rs.ObjectsByName(source_name) or []):
                    try:
                        if rs.ObjectLayer(_cand) == "Archive":
                            continue
                        _live_matches.append(_cand)
                    except Exception:
                        pass
            except Exception:
                _live_matches = []
            if len(_live_matches) == 1:
                try:
                    archive_object(_live_matches[0])
                    rs.DeleteObject(_live_matches[0])
                except Exception:
                    pass

        if not rs.IsLayer("Active"):
            rs.AddLayer("Active", [0, 255, 0])

        # COPY the backup instead of moving it, so the Archive version (and its
        # UserText version history) stays intact and remains restorable again.
        restored_id = rs.CopyObject(target_backup)
        if not restored_id:
            result = json.dumps({"status": "error", "message": "Restore failed: could not copy backup."})
        else:
            rs.ObjectLayer(restored_id, "Active")
            rs.ObjectName(restored_id, source_name)
            # Strip archive metadata from the RESTORED copy (not the backup).
            rs.SetUserText(restored_id, "archive_source_id")
            rs.SetUserText(restored_id, "archive_source_name")
            rs.SetUserText(restored_id, "archive_timestamp")
            rs.SetUserText(restored_id, "archive_version")
            # Record it on the open action so the restore itself is undoable
            # (and, being an effective action, invalidates a stale redo stack).
            try:
                _record_created_object(restored_id)
            except Exception:
                pass
            rs.Redraw()
            result = json.dumps({
                "status": "success",
                "message": "Restored '{0}' (version {1}) to Active layer".format(source_name, version),
                "restored_id": str(restored_id),
            })
"""


def undo_last_action_code() -> str:
    """Undo safely, respecting Rhino-native steps made after plugin actions."""
    return """
result = json.dumps(smart_undo_last_action(), indent=2)
"""


def redo_last_action_code() -> str:
    """Redo safely across Rhino-native and plugin backup histories."""
    return """
result = json.dumps(smart_redo_last_action(), indent=2)
"""
