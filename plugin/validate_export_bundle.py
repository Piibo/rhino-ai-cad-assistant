"""Standalone-Vollstaendigkeitscheck fuer ein Studien-Export-ZIP.

Oeffnet ein vom Plugin erzeugtes Export-Bundle und prueft, ob es
vollstaendig und in sich konsistent ist — ohne das Plugin oder seine
Abhaengigkeiten (anthropic, fastapi, …) zu laden. Nur Python-stdlib,
laeuft also in jedem Python 3.

Gedacht als Ein-Klick-Check nach einem Pilot- oder echten Studienlauf,
bevor man dem Bundle vertraut.

Prueft:
- manifest.json vorhanden + Auszug (git_sha, backend_mode, model, …)
- pro Tabelle: JSONL-Zeilenzahl == Zeilenzahl in der Subset-SQLite
- jede JSONL-Tabelle existiert auch in der SQLite
- pro Tabelle: jedes Feld aus den JSONL-Zeilen existiert als Spalte in der
  Subset-SQLite (faengt stille Spalten-Verluste ab)
- Kerntabellen (sessions, study_sessions, agency_survey) sind nicht leer
- viewports/ + sketches/ Datei-Anzahl; Warnung wenn model_states da
  sind, aber keine Viewport-PNGs
- jede in manifest.files gelistete Datei liegt wirklich im ZIP

Nutzung:
    python validate_export_bundle.py                 # neuestes ZIP im Export-Ordner
    python validate_export_bundle.py pfad/zur/bundle.zip
    python validate_export_bundle.py --dir D:/exports

Exit-Code 0 = keine Fehler, 1 = mindestens ein Fehler, 2 = kein Bundle
gefunden.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
import zipfile


_DEFAULT_EXPORT_DIR = os.path.expanduser(
    os.path.join("~", "Documents", "masterarbeit-studie", "exports")
)

# Tables that a genuine, completed study run must have rows in. Empty
# here means the bundle is broken, not just sparse. Everything else may
# legitimately be empty (designer didn't lock anything, didn't build a
# parametric box, etc.) and is reported but not flagged.
_CORE_NONEMPTY = ("sessions", "study_sessions", "agency_survey")


def _resolve_export_dir(explicit: str | None) -> str:
    if explicit:
        return os.path.expanduser(explicit)
    # Prefer config.json's export_dir if set (script lives in
    # rhaino/plugin/, config.json sits one dir up in rhaino/).
    cfg = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "config.json",
    )
    try:
        with open(cfg, "r", encoding="utf-8") as f:
            ed = (json.load(f).get("export_dir") or "").strip()
        if ed:
            return os.path.expanduser(ed)
    except (OSError, ValueError):
        pass
    return _DEFAULT_EXPORT_DIR


def _latest_zip(export_dir: str) -> str | None:
    if not os.path.isdir(export_dir):
        return None
    zips = [
        os.path.join(export_dir, n)
        for n in os.listdir(export_dir)
        if n.lower().endswith(".zip")
    ]
    return max(zips, key=os.path.getmtime) if zips else None


def validate_bundle(zip_path: str) -> int:
    """Print a completeness report for one bundle. Return error count."""
    errors: list[str] = []
    hints: list[str] = []

    print("=" * 62)
    print("Bundle :", os.path.basename(zip_path))
    print("Pfad   :", zip_path)
    print("Groesse: %.1f KB" % (os.path.getsize(zip_path) / 1024))
    print("=" * 62)

    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()

        # --- manifest ---
        manifest: dict = {}
        if "manifest.json" not in names:
            errors.append("manifest.json fehlt im Bundle.")
        else:
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
            plugin = manifest.get("plugin", {})
            ss = manifest.get("study_session", {})
            print("\n--- manifest.json ---")
            print("  git_sha     :", str(plugin.get("git_sha", "?"))[:12])
            print("  backend_mode:", plugin.get("backend_mode", "?"))
            print("  model       :", plugin.get("model", "?"))
            print("  condition   :",
                  manifest.get("tool_set", {}).get("condition", "?"))
            print("  participant :", ss.get("participant_code", "?"))
            print("  is_pilot    :", ss.get("is_pilot", "?"))
            print("  status      :", ss.get("status", "?"))
            if plugin.get("backend_mode") != "api":
                hints.append(
                    "backend_mode ist '%s', nicht 'api' - ein echter "
                    "Studienlauf laeuft immer im API-Modus (Spec 3.5). "
                    "Vermutlich ein Dev-/Pilot-Export." % plugin.get("backend_mode")
                )

        # --- jsonl vs sqlite row counts ---
        jsonl_tables = sorted(
            n[len("jsonl/"):-len(".jsonl")]
            for n in names
            if n.startswith("jsonl/") and n.endswith(".jsonl")
        )
        sqlite_names = [n for n in names if n.endswith(".sqlite")]
        sqlite_counts: dict[str, int] = {}
        sqlite_cols: dict[str, set] = {}
        if not sqlite_names:
            errors.append("Subset-SQLite (.sqlite) fehlt im Bundle.")
        else:
            with tempfile.TemporaryDirectory() as td:
                sp = os.path.join(td, "sub.sqlite")
                with open(sp, "wb") as f:
                    f.write(zf.read(sqlite_names[0]))
                con = sqlite3.connect(sp)
                for t in jsonl_tables:
                    try:
                        sqlite_counts[t] = con.execute(
                            "SELECT COUNT(*) FROM %s" % t
                        ).fetchone()[0]
                        sqlite_cols[t] = {
                            r[1]
                            for r in con.execute(
                                "PRAGMA table_info(%s)" % t
                            ).fetchall()
                        }
                    except sqlite3.OperationalError:
                        sqlite_counts[t] = -1  # table missing in sqlite
                con.close()

        print("\n--- Tabellen  (JSONL / SQLite) ---")
        for t in jsonl_tables:
            jl = len([
                l for l in zf.read("jsonl/%s.jsonl" % t)
                .decode("utf-8").splitlines() if l.strip()
            ])
            sq = sqlite_counts.get(t, -1)
            flag = ""
            if sq == -1:
                flag = "  <-- in SQLite nicht gefunden"
                errors.append("Tabelle %s fehlt in der Subset-SQLite." % t)
            elif jl != sq:
                flag = "  <-- MISMATCH"
                errors.append(
                    "%s: JSONL %d != SQLite %d Zeilen." % (t, jl, sq)
                )
            print("  %-27s %5s / %-5s%s"
                  % (t, "leer" if jl == 0 else jl,
                     sq if sq >= 0 else "-", flag))
            if jl == 0 and t in _CORE_NONEMPTY:
                errors.append("Kerntabelle %s ist leer." % t)

        # --- Spalten-Vollstaendigkeit: jedes Feld, das in den JSONL-Zeilen
        # vorkommt, muss auch als Spalte in der Subset-SQLite existieren. Faengt
        # eine stille Spalten-Regression im Export ab, die der reine
        # Zeilenzahl-Vergleich oben sonst als "BUNDLE OK" durchwinkt.
        for t in jsonl_tables:
            cols = sqlite_cols.get(t)
            if not cols:
                continue  # Tabelle fehlt/leer -> oben bereits behandelt
            jsonl_keys: set = set()
            for line in (
                zf.read("jsonl/%s.jsonl" % t).decode("utf-8").splitlines()
            ):
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    jsonl_keys.update(row.keys())
            missing = sorted(jsonl_keys - cols)
            if missing:
                errors.append(
                    "%s: Felder in JSONL aber nicht als Spalte in der "
                    "Subset-SQLite: %s" % (t, ", ".join(missing))
                )

        # --- image side-cars ---
        viewports = [n for n in names
                     if n.startswith("viewports/") and not n.endswith("/")]
        sketches = [n for n in names
                    if n.startswith("sketches/") and not n.endswith("/")]
        print("\n--- Bild-Seitenpfade ---")
        print("  viewports/:", len(viewports), "Datei(en)")
        print("  sketches/ :", len(sketches), "Datei(en)")
        ms = sqlite_counts.get("model_states", 0)
        if ms and ms > 0 and not viewports:
            hints.append(
                "%d model_states-Zeile(n), aber viewports/ ist leer - "
                "Snapshot-PNGs fehlen (Pfad ungueltig oder Snapshot lief "
                "nicht)." % ms
            )

        # --- manifest.files vs actual contents ---
        listed = set(manifest.get("files", []))
        missing = [f for f in listed
                   if f not in set(names) and f != "manifest.json"]
        if missing:
            errors.append(
                "manifest.files nennt %d Datei(en), die im ZIP fehlen: %s"
                % (len(missing), ", ".join(missing[:5]))
            )

    # --- verdict ---
    print("\n" + "=" * 62)
    if hints:
        print("HINWEISE:")
        for h in hints:
            print("  - " + h)
    if errors:
        print("FEHLER (%d):" % len(errors))
        for e in errors:
            print("  - " + e)
        print("=> BUNDLE UNVOLLSTAENDIG / INKONSISTENT")
    else:
        print("=> BUNDLE OK" + (" (mit Hinweisen)" if hints else ""))
    print("=" * 62)
    return len(errors)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Studien-Export-Bundle auf Vollstaendigkeit pruefen."
    )
    ap.add_argument(
        "zip", nargs="?",
        help="Pfad zum Export-ZIP (Default: neuestes im Export-Ordner)",
    )
    ap.add_argument(
        "--dir",
        help="Export-Ordner, in dem das neueste ZIP gesucht wird",
    )
    args = ap.parse_args()

    path = args.zip
    if not path:
        export_dir = _resolve_export_dir(args.dir)
        path = _latest_zip(export_dir)
        if not path:
            print("Kein .zip gefunden in:", export_dir, file=sys.stderr)
            return 2
        print("Neuestes Bundle:", path, "\n")
    if not os.path.isfile(path):
        print("Datei nicht gefunden:", path, file=sys.stderr)
        return 2

    return 1 if validate_bundle(path) else 0


if __name__ == "__main__":
    sys.exit(main())
