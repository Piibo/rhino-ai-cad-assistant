"""Session-bundle export pipeline (Studienartefakt-Spec §2.6).

Produces one ZIP per study session containing:
- ``session_<participant>_<order>_<condition>.sqlite`` — same schema
  as plugin.db, only the rows of this session
- ``session_<...>.jsonl/`` — per-table JSONL dumps for Reflexive-TA
  tools that don't speak SQLite
- ``viewports/`` — all PNGs referenced by ``model_states.viewport_path``
- ``sketches/`` — composite Sketch-Canvas exports extracted from
  ``messages`` for the werkzeug condition
- ``model.3dm`` — das finale, sichtbare Lauf-Modell als openbare Geometrie
  (isolierte Variante; nur bei verfuegbarem Rhino + nicht-leerer Szene)
- ``manifest.json`` — plugin git-SHA, model id, tools-hash, system-prompt
  hash, condition, participant code, export timestamp

Bundle path lives under ``settings.export_dir`` (default
``~/Documents/masterarbeit-studie/exports``).
"""

from __future__ import annotations

import base64
import csv
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sqlite3
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from . import schemas
from .config import config
from .fragebogen import fragebogen_version_hash
from .session_store import get_store
from .tool_registry import build_tool_list

logger = logging.getLogger("FurniturePlugin.Export")


# Per-table export plan: which column to filter on and whether the
# filter is the plugin session id (``session_id``), the study session
# id (``study_session_id``) or the participant code (``participant``,
# for per-lane data like demographics that must land in BOTH run
# bundles of the same visit).
@dataclass(frozen=True)
class _TablePlan:
    name: str
    filter_column: str  # column to filter on
    scope: str  # "session" | "study_session" | "participant"


_EXPORT_TABLES: list[_TablePlan] = [
    _TablePlan("sessions", "id", "session"),
    _TablePlan("messages", "session_id", "session"),
    _TablePlan("tool_calls", "session_id", "session"),
    _TablePlan("parameter_changes", "session_id", "session"),
    _TablePlan("model_states", "session_id", "session"),
    _TablePlan("exposed_parameters", "session_id", "session"),
    _TablePlan("active_structure_contexts", "session_id", "session"),
    _TablePlan("variants", "session_id", "session"),
    _TablePlan("locked_objects", "session_id", "session"),
    _TablePlan("snapshots", "session_id", "session"),
    _TablePlan("study_events", "session_id", "session"),
    _TablePlan("study_sessions", "session_id", "session"),
    _TablePlan("consent", "study_session_id", "study_session"),
    _TablePlan("events", "study_session_id", "study_session"),
    _TablePlan("agency_survey", "study_session_id", "study_session"),
    _TablePlan("final_survey", "study_session_id", "study_session"),
    _TablePlan("participant_demographics", "participant_code", "participant"),
]


def _plan_key_value(
    plan: _TablePlan,
    session_id: str,
    study_session_id: str,
    participant_code: str,
) -> str:
    if plan.scope == "session":
        return session_id
    if plan.scope == "participant":
        return participant_code
    return study_session_id


def _filter_participant_rows(
    plan: _TablePlan,
    rows: list[dict[str, Any]],
    is_pilot: bool,
) -> list[dict[str, Any]]:
    """Participant-Scope ist pro LANE (code + is_pilot) modelliert.

    Der Equality-Filter von ``rows_for_export`` kennt nur eine Spalte —
    ohne diesen Nachfilter würde ein Code mit Pilot- UND Echt-Lane beide
    Demografie-Zeilen in jedes Bundle mischen.
    """
    if plan.scope != "participant":
        return rows
    want = int(is_pilot)
    return [r for r in rows if int(r.get("is_pilot") or 0) == want]


@dataclass(frozen=True)
class ExportResult:
    bundle_path: str
    bytes: int
    files_included: list[str]


def _git_sha() -> str:
    """Return the plugin's commit SHA, or 'unknown' if git isn't reachable.

    Runs at export time so the SHA reflects the running plugin, not a
    build-time stamp that might lag. ``subprocess`` because PyGit2 is
    overkill; CWD is the rhaino root (two dirs up from this file).
    """
    repo_root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        logger.warning("git rev-parse failed: %s", e)
    return "unknown"


def _system_prompt_hash() -> str:
    """SHA-256 of the agent's SYSTEM_PROMPT constant.

    Lazy import so this module stays load-cheap and we don't import
    the agent's optional anthropic SDK at export time.
    """
    from . import agent

    return hashlib.sha256(agent.SYSTEM_PROMPT.encode("utf-8")).hexdigest()


def _tools_hash(condition: str) -> str:
    tools = build_tool_list(condition)
    # Stable JSON: sorted keys so insertion order doesn't shift the hash.
    payload = json.dumps(tools, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _default_export_dir() -> str:
    return os.path.expanduser(
        os.path.join("~", "Documents", "masterarbeit-studie", "exports")
    )


def _bundle_basename(
    participant_code: str,
    order_index: int,
    condition: str,
    is_pilot: bool = False,
    study_session_id: str = "",
) -> str:
    # Defensive: keep filename portable (no spaces, no umlauts in code paths).
    safe_code = "".join(
        c if c.isalnum() else "_" for c in participant_code
    ) or "P"
    if is_pilot:
        suffix = study_session_id[:8] if study_session_id else "pilot"
        return f"pilot_{safe_code}_{condition}_{suffix}"
    return f"session_{safe_code}_{order_index}_{condition}"


def _rows_for_table(
    plan: _TablePlan,
    session_id: str,
    study_session_id: str,
    participant_code: str,
    is_pilot: bool,
) -> list[dict[str, Any]]:
    key_value = _plan_key_value(plan, session_id, study_session_id, participant_code)
    rows = get_store().rows_for_export(plan.name, plan.filter_column, key_value)
    return _filter_participant_rows(plan, rows, is_pilot)


def _write_jsonl(path: str, rows: Iterable[dict[str, Any]]) -> int:
    """JSONL dump (one JSON object per line). Returns row count."""
    count = 0
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, default=str))
            f.write("\n")
            count += 1
    return count


def _write_survey_csv(path: str, study_session: schemas.StudySession) -> bool:
    """Konsolidierte Fragebogen-CSV (fragebogen-spec §10.2).

    Eine Zeile pro Teilnehmenden-Lane: Demografie (``demo_``-Spalten),
    Per-Bedingungs-Block je Bedingung (``basis_``/``werkzeug_``-Präfix)
    und Vergleichsblock (``final_``-Präfix), damit die deskriptive
    Auswertung ohne SQL-Wrangling auskommt. Gibt ``False`` zurück, wenn
    noch keine Survey-Daten existieren (dann kein CSV im Bundle).
    """
    store = get_store()
    code = study_session.participant_code
    is_pilot = study_session.is_pilot
    runs = [
        r
        for r in store.list_study_sessions_for_participant(code)
        if r.is_pilot == is_pilot and r.status != "aborted"
    ]
    row: dict[str, Any] = {
        "participant_code": code,
        "is_pilot": int(is_pilot),
    }
    has_any = False
    demographics = store.demographics_for_participant(code, is_pilot)
    if demographics is not None:
        has_any = True
        demo = demographics.model_dump(
            mode="json",
            exclude={"id", "study_session_id", "participant_code", "is_pilot"},
        )
        row.update({f"demo_{k}": v for k, v in demo.items()})
    for run in runs:
        survey = store.latest_agency_survey(run.id)
        if survey is not None:
            has_any = True
            data = survey.model_dump(
                mode="json", exclude={"id", "study_session_id", "condition"}
            )
            row.update({f"{run.condition}_{k}": v for k, v in data.items()})
        final = store.latest_final_survey(run.id)
        if final is not None:
            has_any = True
            data = final.model_dump(mode="json", exclude={"id", "study_session_id"})
            row.update({f"final_{k}": v for k, v in data.items()})
    if not has_any:
        return False
    flat = {
        k: json.dumps(v, ensure_ascii=False) if isinstance(v, list) else v
        for k, v in row.items()
    }
    # utf-8-sig: ein BOM voranstellen, damit Excel (Windows/DE) die Datei als
    # UTF-8 erkennt und Umlaute/En-Dash nicht als CP1252-Mojibake ("mÃ¤nnlich",
    # "25â€“29") anzeigt. Die Bytes selbst waren schon korrekt UTF-8; das BOM ist
    # nur das Signal fuer Excels Auto-Erkennung. Andere Tools (pandas etc.) lesen
    # utf-8-sig ebenfalls sauber.
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(flat.keys()))
        writer.writeheader()
        writer.writerow(flat)
    return True


def _build_subset_sqlite(
    dest_path: str,
    session_id: str,
    study_session_id: str,
    participant_code: str,
    is_pilot: bool,
) -> None:
    """Create a session-scoped SQLite copy.

    Copies the same schema as plugin.db and inserts only the rows that
    belong to this session. Done by ATTACHing the source DB and running
    ``INSERT INTO ... SELECT ... WHERE ...`` per table, all inside one
    transaction so a partial failure doesn't ship a half-written file.
    """
    source_path = get_store().db_path
    if os.path.exists(dest_path):
        os.remove(dest_path)

    # Copy schema first via .sql of source. sqlite_master.sql stores
    # CREATE statements without trailing semicolons, so we add them so
    # ``executescript`` can split them apart. Sort by name to make
    # ordering deterministic (indexes come last alphabetically, which
    # is fine — they depend only on the tables they index).
    src = sqlite3.connect(source_path)
    src.row_factory = sqlite3.Row
    schema_sql = ";\n".join(
        row["sql"]
        for row in src.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE sql IS NOT NULL "
            "ORDER BY type DESC, name ASC"
        ).fetchall()
    ) + ";"
    src.close()

    # Read each table's rows from the live DB via the SessionStore
    # helper (handles WAL + locking correctly), then bulk-insert into
    # the dest. Avoids ATTACH, which holds a write lock on the source
    # WAL and races with concurrent backend writes.
    store = get_store()
    dest = sqlite3.connect(dest_path)
    try:
        dest.executescript(schema_sql)
        for plan in _EXPORT_TABLES:
            key_value = _plan_key_value(
                plan, session_id, study_session_id, participant_code
            )
            try:
                rows = store.rows_for_export(
                    plan.name, plan.filter_column, key_value
                )
            except (sqlite3.OperationalError, ValueError) as e:
                logger.warning(
                    "subset read for %s skipped: %s", plan.name, e
                )
                continue
            rows = _filter_participant_rows(plan, rows, is_pilot)
            if not rows:
                continue
            columns = list(rows[0].keys())
            placeholders = ", ".join(["?"] * len(columns))
            col_list = ", ".join(columns)
            try:
                dest.executemany(
                    f"INSERT INTO {plan.name} ({col_list}) "
                    f"VALUES ({placeholders})",
                    [tuple(r[c] for c in columns) for r in rows],
                )
            except sqlite3.OperationalError as e:
                logger.warning(
                    "subset insert for %s skipped: %s", plan.name, e
                )
        dest.commit()
    finally:
        dest.close()


def _rewrite_sqlite_viewport_paths(
    sqlite_path: str, viewport_map: dict[str, str]
) -> None:
    """Point ``model_states.viewport_path`` at the bundled image (portable
    ``viewports/...`` arc) in the subset SQLite too, mirroring the JSONL rewrite.

    Best-effort: a failure here must not sink the export — the images are already
    copied and the JSONL is already corrected.
    """
    if not viewport_map:
        return
    try:
        con = sqlite3.connect(sqlite_path)
        try:
            con.executemany(
                "UPDATE model_states SET viewport_path = ? WHERE id = ?",
                [(rel, mid) for mid, rel in viewport_map.items()],
            )
            con.commit()
        finally:
            con.close()
    except sqlite3.Error as e:  # pragma: no cover — defensive
        logger.warning("sqlite viewport_path rewrite failed: %s", e)


def _extract_viewport_pngs(
    model_states_rows: list[dict[str, Any]],
    dest_dir: str,
) -> tuple[list[str], dict[str, str]]:
    """Copy referenced viewport PNGs into the bundle's viewports/ dir.

    ``model_states.viewport_path`` is set by the snapshot trigger
    (Schritt 4+ for the auto-trigger; manual snapshots could land here
    earlier). Missing paths are logged and skipped, not fatal.

    Returns ``(relative bundle paths, {model_state_id: relative bundle path})``.
    The mapping lets the caller rewrite ``model_states.viewport_path`` from the
    absolute on-disk path — which leaks the researcher's local directory AND does
    not resolve inside the bundle — to the portable ``viewports/...`` arc, so an
    exported snapshot row joins directly onto its image.
    """
    if not model_states_rows:
        return [], {}
    os.makedirs(dest_dir, exist_ok=True)
    copied: list[str] = []
    rel_by_id: dict[str, str] = {}
    for row in model_states_rows:
        src_path = row.get("viewport_path")
        if not src_path or not os.path.isfile(src_path):
            continue
        base = os.path.basename(src_path)
        # Disambiguate with the model_state id so two snapshots that
        # happen to live at the same path don't overwrite each other.
        dest_name = f"{row['id']}_{base}"
        dest_path = os.path.join(dest_dir, dest_name)
        try:
            shutil.copyfile(src_path, dest_path)
            # Forward-Slash erzwingen: das ZIP normalisiert Arcnames auf "/",
            # os.path.relpath liefert auf Windows aber "\" -> sonst weicht der
            # Manifest-files-Eintrag vom ZIP-Eintrag ab und validate_export_bundle
            # meldet das Bundle faelschlich als unvollstaendig.
            rel = os.path.relpath(
                dest_path, os.path.dirname(dest_dir)
            ).replace(os.sep, "/")
            copied.append(rel)
            rel_by_id[row["id"]] = rel
        except OSError as e:
            logger.warning("viewport copy failed for %s: %s", src_path, e)
    return copied, rel_by_id


def _extract_sketch_pngs(
    messages_rows: list[dict[str, Any]],
    dest_dir: str,
) -> list[str]:
    """Pull SketchBlock.rendered_png payloads out of message content.

    Sketches live inline as base64 in messages.content_json — there is
    no dedicated sketches table. Werkzeug-only by configuration (the
    sketch button is hidden in basis), but the extraction itself runs
    regardless of condition so post-hoc reviewers can still see them
    if they appear.
    """
    os.makedirs(dest_dir, exist_ok=True)
    extracted: list[str] = []
    for row in messages_rows:
        raw = row.get("content_json")
        if not raw:
            continue
        try:
            blocks = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(blocks, list):
            continue
        for idx, block in enumerate(blocks):
            if not isinstance(block, dict) or block.get("type") != "sketch":
                continue
            image = block.get("rendered_png") or block.get("background")
            if not isinstance(image, dict) or not image.get("data"):
                continue
            media_type = image.get("media_type", "image/png")
            ext = media_type.split("/")[-1] or "png"
            fname = f"{row['id']}_{idx}.{ext}"
            try:
                with open(os.path.join(dest_dir, fname), "wb") as f:
                    f.write(base64.b64decode(image["data"]))
                # Forward-Slash (wie viewports/ + jsonl/): sonst Backslash auf
                # Windows -> Manifest-files != ZIP-Arcname -> Validator-Fehlalarm.
                extracted.append("sketches/" + fname)
            except (OSError, ValueError) as e:
                logger.warning("sketch extract failed (%s): %s", fname, e)
    return extracted


def _write_manifest(
    dest_path: str,
    plugin_session: schemas.Session,
    study_session: schemas.StudySession,
    tools_hash: str,
    system_prompt_hash: str,
    git_sha: str,
    files_included: list[str],
) -> None:
    # Reihenfolge-Autonomie des Bundles (Pilot-Befund 01.07.2026): der
    # final_survey kodiert V1/V2 relativ zur Bearbeitungs-REIHENFOLGE
    # ("erste/zweite Bedingung"). Damit die Zuordnung Reihenfolge->Bedingung
    # aus jedem Bundle allein rekonstruierbar ist (ohne Schwester-Bundle),
    # traegt das Manifest die chronologische Lauf-Sequenz des Teilnehmenden.
    # Defensiv: ein Fehler hier darf den Export nie verhindern.
    condition_sequence: list[dict[str, Any]] = []
    try:
        for r in get_store().list_study_sessions_for_participant(
            study_session.participant_code
        ):
            condition_sequence.append(
                {
                    "order_index": r.order_index,
                    "condition": r.condition,
                    "task_variant": r.task_variant,
                    "is_pilot": r.is_pilot,
                    "status": r.status,
                    "created_at": r.created_at.isoformat(),
                    "is_this_bundle": r.id == study_session.id,
                }
            )
    except Exception as e:
        logger.warning("participant_condition_sequence unavailable: %s", e)

    manifest = {
        "format_version": 1,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "plugin": {
            "git_sha": git_sha,
            "backend_mode": config.backend_mode,
            "model": config.model,
            "max_tokens": int(config.max_tokens),
        },
        "session": {
            "id": plugin_session.id,
            "title": plugin_session.title,
            "created_at": plugin_session.created_at.isoformat(),
            "condition": plugin_session.condition,
        },
        "study_session": {
            "id": study_session.id,
            "participant_code": study_session.participant_code,
            "condition": study_session.condition,
            "task_variant": study_session.task_variant,
            "setting": study_session.setting,
            "is_pilot": study_session.is_pilot,
            "order_index": study_session.order_index,
            "status": study_session.status,
            "created_at": study_session.created_at.isoformat(),
            "ended_at": study_session.ended_at.isoformat()
            if study_session.ended_at is not None
            else None,
        },
        "participant_condition_sequence": condition_sequence,
        "tool_set": {
            "condition": plugin_session.condition,
            "hash": tools_hash,
        },
        "system_prompt": {
            "hash": system_prompt_hash,
        },
        # fragebogen-spec §7.5: jede Erhebung trägt den Wortlaut-Hash,
        # damit nachvollziehbar ist, welche Items die Person gesehen hat.
        "fragebogen": {
            "version_hash": fragebogen_version_hash(),
        },
        "files": files_included,
    }
    with open(dest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)


def export_session_bundle(
    study_session_id: str, model_3dm_path: str | None = None
) -> ExportResult:
    """Build a ZIP bundle for one study session and return its path.

    Pipeline:
        1. resolve study_session + its plugin session
        2. stage everything in a temp dir
        3. zip → ``<export_dir>/session_<...>.zip``
        4. clean temp
    The export endpoint is the only caller and runs synchronously so
    the researcher gets the path back immediately.
    """
    store = get_store()
    study_session = store.get_study_session(study_session_id)
    if study_session is None:
        raise FileNotFoundError(f"study_session {study_session_id} not found")
    plugin_session = store.get_session(study_session.session_id)
    if plugin_session is None:
        raise FileNotFoundError(
            f"plugin session {study_session.session_id} not found "
            f"(study_session {study_session_id})"
        )

    export_dir = getattr(config, "export_dir", None) or _default_export_dir()
    os.makedirs(export_dir, exist_ok=True)

    base = _bundle_basename(
        study_session.participant_code,
        study_session.order_index,
        study_session.condition,
        is_pilot=study_session.is_pilot,
        study_session_id=study_session.id,
    )
    bundle_path = os.path.join(export_dir, f"{base}.zip")

    with tempfile.TemporaryDirectory(prefix="rhaino-export-") as tmp:
        # 1) SQLite subset
        sqlite_path = os.path.join(tmp, f"{base}.sqlite")
        _build_subset_sqlite(
            sqlite_path,
            session_id=plugin_session.id,
            study_session_id=study_session.id,
            participant_code=study_session.participant_code,
            is_pilot=study_session.is_pilot,
        )

        # 2) JSONL per table — collected first because viewport + sketch
        # extractors reuse the row dumps.
        jsonl_dir = os.path.join(tmp, "jsonl")
        os.makedirs(jsonl_dir, exist_ok=True)
        per_table_rows: dict[str, list[dict[str, Any]]] = {}
        files_included: list[str] = [f"{base}.sqlite"]
        for plan in _EXPORT_TABLES:
            try:
                rows = _rows_for_table(
                    plan,
                    plugin_session.id,
                    study_session.id,
                    study_session.participant_code,
                    study_session.is_pilot,
                )
            except ValueError as e:
                logger.warning("rows_for_table refused %s: %s", plan.name, e)
                rows = []
            per_table_rows[plan.name] = rows
            target = os.path.join(jsonl_dir, f"{plan.name}.jsonl")
            count = _write_jsonl(target, rows)
            files_included.append(f"jsonl/{plan.name}.jsonl")
            logger.debug("exported %d row(s) from %s", count, plan.name)

        # 2b) survey_export.csv — konsolidierte Fragebogen-Sicht
        # (fragebogen-spec §10.2): eine Zeile pro Teilnehmenden, Spalten
        # für beide Bedingungen. Best-effort: ein CSV-Fehler darf den
        # Export nicht scheitern lassen.
        try:
            csv_path = os.path.join(tmp, "survey_export.csv")
            if _write_survey_csv(csv_path, study_session):
                files_included.append("survey_export.csv")
        except Exception:
            logger.exception("survey_export.csv fehlgeschlagen — Bundle ohne CSV")

        # 3) viewports/ and sketches/ side-cars
        viewports_dir = os.path.join(tmp, "viewports")
        viewport_files, viewport_map = _extract_viewport_pngs(
            per_table_rows.get("model_states", []), viewports_dir
        )
        files_included.extend(viewport_files)
        # model_states.viewport_path auf die Bundle-Datei umbiegen (portabler
        # ``viewports/...``-Pfad statt absolutem Rechner-Pfad): joinbar fuer die
        # Auswertung + kein Leak der lokalen Verzeichnisstruktur. Betrifft die
        # JSONL (neu schreiben) UND die Subset-SQLite (UPDATE). Nur die Zeilen,
        # deren Bild wirklich kopiert wurde; fehlende bleiben unveraendert.
        if viewport_map:
            for row in per_table_rows.get("model_states", []):
                rel = viewport_map.get(row.get("id"))
                if rel:
                    row["viewport_path"] = rel
            _write_jsonl(
                os.path.join(jsonl_dir, "model_states.jsonl"),
                per_table_rows["model_states"],
            )
            _rewrite_sqlite_viewport_paths(sqlite_path, viewport_map)

        sketches_dir = os.path.join(tmp, "sketches")
        sketch_files = _extract_sketch_pngs(
            per_table_rows.get("messages", []), sketches_dir
        )
        files_included.extend(sketch_files)

        # 3b) model.3dm — das finale, sichtbare Lauf-Modell als openbare
        # Geometrie-Datei (isolierte Variante; vom Endpoint via File3dm erzeugt
        # und hier nur einkopiert). Best-effort: fehlt der Pfad (kein Rhino /
        # leere Szene), wird das Bundle ohne Geometrie-Datei gebaut.
        if model_3dm_path and os.path.isfile(model_3dm_path):
            try:
                shutil.copyfile(model_3dm_path, os.path.join(tmp, "model.3dm"))
                files_included.append("model.3dm")
            except OSError as e:
                logger.warning("model.3dm copy into bundle failed: %s", e)

        # 4) manifest.json
        manifest_path = os.path.join(tmp, "manifest.json")
        _write_manifest(
            manifest_path,
            plugin_session=plugin_session,
            study_session=study_session,
            tools_hash=_tools_hash(plugin_session.condition),
            system_prompt_hash=_system_prompt_hash(),
            git_sha=_git_sha(),
            files_included=files_included,
        )

        # 5) zip everything in ``tmp`` into bundle_path
        if os.path.exists(bundle_path):
            os.remove(bundle_path)
        with zipfile.ZipFile(
            bundle_path, "w", compression=zipfile.ZIP_DEFLATED
        ) as zf:
            for root, _dirs, files in os.walk(tmp):
                for fname in files:
                    full = os.path.join(root, fname)
                    arc = os.path.relpath(full, tmp)
                    zf.write(full, arc)

    size = os.path.getsize(bundle_path)
    return ExportResult(
        bundle_path=bundle_path,
        bytes=size,
        files_included=files_included + ["manifest.json"],
    )
