#! python 3
# -*- coding: utf-8 -*-
# r: fastapi
# r: uvicorn
# r: pydantic>=2
# r: websockets
# r: python-multipart
# r: anthropic>=0.39
"""Entry point — run from Rhino 8 to launch the plugin.

Usage from Rhino (<Repo-Pfad> = absoluter Pfad des Repository-Checkouts):
    _-RunPythonScript "<Repo-Pfad>/rhaino/plugin/start_plugin.py"

Force-reload behavior: every invocation checks whether any watched backend,
panel, or frontend file on disk is newer than the last time we loaded
them. If yes, the running uvicorn is stopped, all cached modules are
purged from ``sys.modules``, and start-up proceeds with fresh code. So
to pick up code edits, you simply rerun the same RunPythonScript - no
flag, no Rhino restart.

The optional explicit override still works as an escape hatch — set the
env var ``FURNITURE_FORCE_RELOAD=1`` before launching Rhino. Rhino's
own ``RunPythonScript`` command unfortunately does *not* forward CLI
arguments as ``sys.argv``; it interprets them as follow-up Rhino
commands. That's why a ``force`` argument doesn't work and we lean on
file mtime instead.

Why this matters: Rhino's CPython process is shared across
``RunPythonScript`` calls. Once ``backend.server`` etc. are imported
they sit in ``sys.modules`` forever, so a second invocation would
otherwise re-use the *old* code even after you edited the files on
disk. The auto-reload trades a millisecond stat() per file for the
right behavior.
"""

from __future__ import annotations

import logging
import os
import socket
import sys
import threading
import time
import urllib.request

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("FurniturePlugin.Start")

# Ensure the plugin dir is on sys.path so ``backend.*`` imports resolve.
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

# Also put the rhaino package root on sys.path so the plugin can import
# ``shared.code_templates`` — the place where the MCP server and this plugin
# keep their Rhino-Python source templates in lockstep. Without this, the
# templates would have to be duplicated inside backend/.
_PARENT_DIR = os.path.dirname(_THIS_DIR)
if _PARENT_DIR not in sys.path:
    sys.path.insert(0, _PARENT_DIR)

logger.info("Launching plugin from %s", _THIS_DIR)
logger.info("Using rhaino root %s", _PARENT_DIR)

# NOTE: ``config`` is intentionally NOT imported at module-level. Force-reload
# would purge backend.* from sys.modules and the binding here would still
# point at the stale old config singleton. start_backend() imports it lazily
# instead, after the purge has run.

_backend_thread: "threading.Thread | None" = None


def _find_free_port(start: int, tries: int = 100) -> int:
    for offset in range(tries):
        port = start + offset
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"no free port in [{start}, {start + tries})")


def _server_port(server: object | None) -> int | None:
    """Return the port from a previous uvicorn server, if Rhino kept one."""
    if server is None:
        return None
    uvicorn_config = getattr(server, "config", None)
    port = getattr(uvicorn_config, "port", None)
    return int(port) if port else None


def _health_ok(port: int) -> bool:
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/health",
            timeout=0.5,
        ) as resp:
            return resp.status == 200
    except Exception:
        return False


def _run_backend(port: int) -> None:
    import uvicorn

    from backend.server import app

    uvicorn_config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="info",
        access_log=False,
        lifespan="on",
    )
    server = uvicorn.Server(uvicorn_config)
    import sys
    sys._furniture_uvicorn_server = server
    try:
        server.run()
    finally:
        if getattr(sys, "_furniture_uvicorn_server", None) is server:
            del sys._furniture_uvicorn_server
        if getattr(sys, "_furniture_backend_thread", None) is threading.current_thread():
            del sys._furniture_backend_thread


def start_backend() -> int:
    """Pick a free port, update config, launch uvicorn in a thread."""
    # Lazy import so force-reload (which deletes backend.* from sys.modules
    # *before* this runs) gets a freshly-loaded singleton, not the stale
    # binding we'd cache at module-top.
    from backend.config import config

    existing = getattr(sys, "_furniture_uvicorn_server", None)
    existing_port = _server_port(existing) or getattr(
        sys,
        "_furniture_backend_port",
        None,
    )
    if existing_port and not getattr(existing, "should_exit", False):
        if _health_ok(int(existing_port)):
            config.port = int(existing_port)
            logger.info("Reusing existing backend on port %d", config.port)
            return config.port

    if existing is not None:
        logger.info("Stopping old backend server...")
        existing.should_exit = True
        old_thread = getattr(sys, "_furniture_backend_thread", None)
        if old_thread is not None and old_thread.is_alive():
            old_thread.join(timeout=2.0)
        time.sleep(0.1)

        # Safety valve: never start a second server if the old one is still
        # answering. Rhino's CPython process persists between script runs, and
        # overlapping uvicorn/WebView lifecycles can freeze the UI.
        if existing_port and _health_ok(int(existing_port)):
            config.port = int(existing_port)
            logger.warning(
                "Old backend did not stop cleanly; reusing port %d",
                config.port,
            )
            return config.port

    global _backend_thread
    if _backend_thread and _backend_thread.is_alive():
        logger.info("Backend already running on port %d", config.port)
        return config.port

    port = _find_free_port(config.port, tries=100)
    config.port = port
    _backend_thread = threading.Thread(
        target=_run_backend,
        args=(port,),
        name="FurniturePluginBackend",
        daemon=True,
    )
    sys._furniture_backend_thread = _backend_thread
    sys._furniture_backend_port = port
    _backend_thread.start()
    logger.info("Backend thread started on port %d", port)
    return port


def start_panel(reload_module: bool = False) -> None:
    # Only reload the thin panel wrapper when code actually changed. Reimporting
    # it on every RunPythonScript loses the module-level floating-form handle
    # and can spawn multiple WebViews across restarts, which may freeze Rhino.
    if reload_module and "panel" in sys.modules:
        del sys.modules["panel"]
    import panel  # noqa: WPS433

    # panel.register() auto-discovers a valid owning plugin (script-based
    # CPython plugins have no PlugIn instance of their own). If none is
    # available, register() returns False and show() falls back to a
    # floating Eto.Forms.Form.
    panel.register()
    panel.show()


def _force_purge_backend() -> None:
    """Stop the running backend and purge backend.* modules from sys.modules.

Use case: you edited backend/panel/frontend code while Rhino was still
    running. Rhino's CPython keeps the old module objects cached, so a
    plain re-run of this script would silently use the stale code. Calling
    this first wipes the slate so the next imports pick up the new code
    from disk.
    """
    logger.info("Force-reload: stopping backend and purging modules")

    existing_panel = sys.modules.get("panel")
    if existing_panel is not None:
        try:
            dispose = getattr(existing_panel, "dispose", None)
            if callable(dispose):
                dispose()
        except Exception as e:  # noqa: BLE001
            logger.warning("Panel dispose during reload failed: %s", e)

    # 1. Tell the running uvicorn to exit and wait briefly for the thread.
    existing = getattr(sys, "_furniture_uvicorn_server", None)
    if existing is not None:
        try:
            existing.should_exit = True
        except Exception:  # noqa: BLE001 — defensive, we're tearing down anyway
            pass
        old_thread = getattr(sys, "_furniture_backend_thread", None)
        if old_thread is not None and old_thread.is_alive():
            old_thread.join(timeout=3.0)
        time.sleep(0.2)

    # 2. Drop the cached uvicorn handles so the next start can pick a port
    #    freely instead of trying to reuse the dead server.
    for attr in (
        "_furniture_uvicorn_server",
        "_furniture_backend_thread",
        "_furniture_backend_port",
    ):
        if hasattr(sys, attr):
            try:
                delattr(sys, attr)
            except Exception:  # noqa: BLE001
                pass

    # 3. Also drop this script's own ``_backend_thread`` reference. It
    #    points to a thread that's joined / exiting; we want a fresh one.
    global _backend_thread
    _backend_thread = None

    # 4. Purge backend.* AND shared.* modules so the next imports re-read
    #    from disk. ``shared.*`` (code_templates, action_history_preamble)
    #    holds the Rhino-Python source templates; without purging it here, an
    #    edit under shared/ would trigger the reload (its mtime is watched)
    #    but the stale cached shared module would persist — backend re-imports
    #    would just get the old object back. Also panel so the WebView wrapper
    #    reloads.
    purged: list[str] = []
    for name in list(sys.modules.keys()):
        if (
            name == "backend"
            or name.startswith("backend.")
            or name == "shared"
            or name.startswith("shared.")
        ):
            del sys.modules[name]
            purged.append(name)
    if "panel" in sys.modules:
        del sys.modules["panel"]
        purged.append("panel")
    logger.info(
        "Force-reload purged %d module(s): %s",
        len(purged),
        ", ".join(purged) if purged else "(none)",
    )


def _max_code_mtime() -> float:
    """Highest mtime across all watched plugin files.

    Watches:
      - ``backend/`` Python files
      - sibling ``shared/`` Python files
      - ``panel.py`` in the plugin root
      - built frontend assets in ``web/dist/``

    If a file goes away or is unreadable, we silently skip it - the goal
    is "did anything change", not a complete audit.
    """
    top: float = 0.0

    watched_files = [os.path.join(_THIS_DIR, "panel.py")]
    for file_path in watched_files:
        try:
            m = os.path.getmtime(file_path)
            if m > top:
                top = m
        except OSError:
            pass

    watched_dirs = [
        (os.path.join(_THIS_DIR, "backend"), {".py"}),
        (os.path.join(_PARENT_DIR, "shared"), {".py"}),
        (os.path.join(_THIS_DIR, "web", "dist"), None),
    ]
    for d, allowed_suffixes in watched_dirs:
        if not os.path.isdir(d):
            continue
        for root, _dirs, files in os.walk(d):
            _dirs[:] = [x for x in _dirs if not x.startswith(("__pycache__", "."))]
            for f in files:
                if allowed_suffixes is not None:
                    suffix = os.path.splitext(f)[1]
                    if suffix not in allowed_suffixes:
                        continue
                try:
                    m = os.path.getmtime(os.path.join(root, f))
                    if m > top:
                        top = m
                except OSError:
                    pass
    return top


def main() -> None:
    # Auto-reload: did any watched .py file change since the last run?
    # If yes (or the override env var is set), stop the old backend and
    # purge cached modules before importing.
    current_mtime = _max_code_mtime()
    last_mtime = getattr(sys, "_furniture_code_mtime", None)
    last_plugin_dir = getattr(sys, "_furniture_plugin_dir", None)
    env_override = os.environ.get("FURNITURE_FORCE_RELOAD") == "1"
    path_changed = (
        last_plugin_dir is not None
        and os.path.normcase(os.path.abspath(last_plugin_dir))
        != os.path.normcase(os.path.abspath(_THIS_DIR))
    )
    cached_module_path_changed = False
    for module_name in ("panel", "backend.config", "backend.server"):
        module = sys.modules.get(module_name)
        module_file = getattr(module, "__file__", None)
        if not module_file:
            continue
        module_path = os.path.normcase(os.path.abspath(module_file))
        plugin_root = os.path.normcase(os.path.abspath(_THIS_DIR))
        if not module_path.startswith(plugin_root + os.sep):
            cached_module_path_changed = True
            break

    force_reload = env_override or (
        last_mtime is not None and current_mtime > last_mtime
    ) or path_changed or cached_module_path_changed
    if force_reload:
        if env_override:
            reason = "env override"
        elif path_changed:
            reason = f"plugin path changed ({last_plugin_dir} -> {_THIS_DIR})"
        elif cached_module_path_changed:
            reason = "cached plugin modules came from another path"
        else:
            reason = f"code changed (mtime {last_mtime} -> {current_mtime})"
        logger.info("Auto-reload triggered: %s", reason)
        _force_purge_backend()

    sys._furniture_code_mtime = current_mtime
    sys._furniture_plugin_dir = _THIS_DIR
    start_backend()
    start_panel(reload_module=force_reload)


if __name__ == "__main__":
    main()
