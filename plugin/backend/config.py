"""Plugin configuration — backend mode, use mode, API key, model, ports."""

import json
import logging
import os
import tempfile

logger = logging.getLogger("FurniturePlugin.Config")

# Default config path: rhaino/config.json (three dirs up from backend/config.py)
_CONFIG_DIR = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
_CONFIG_PATH = os.path.join(_CONFIG_DIR, "config.json")

# Allowed Anthropic model IDs. Dated suffixes (e.g. "-20250514") were the
# convention up through Sonnet 4.5; current models use version-only IDs.
# See claude-api skill shared/models.md for the live capability lookup.
# Sentinel "local" routes to an OpenAI-compatible endpoint via litellm --
# local (LM Studio / Ollama / llama.cpp) or cloud (OpenAI / OpenRouter).
# See agent.py and config.local_*.
_KNOWN_MODELS = {
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-sonnet-5",
    "claude-sonnet-4-6",
    "claude-haiku-4-5",
    "local",
}

_DEFAULTS = {
    # Backend: "mcp" routes chat through Claude Code / Claude Desktop via MCP,
    # "api" calls Anthropic's API directly from the plugin.
    "backend_mode": "mcp",

    # Use-mode: "normal" = just CAD assistant (no logging),
    # "study" = additive research layer (consent, event log, snapshots, surveys).
    "use_mode": "normal",

    # Cloud LLM access (only used when backend_mode == "api"). api_key +
    # model drive the Anthropic path. For an OpenAI-compatible provider,
    # set model == "local" and use the local_* fields below instead.
    "api_key": "",
    "model": "claude-sonnet-5",
    "max_tokens": 8192,

    # OpenAI-compatible endpoint -- only consulted when model == "local".
    # Calls go through litellm.acompletion against any OpenAI-compatible
    # server: local (LM Studio port 1234, Ollama 11434) or cloud (OpenAI
    # https://api.openai.com/v1, OpenRouter, etc.). local_api_key stays
    # empty for LM Studio / Ollama (they ignore auth), set it for cloud.
    "local_base_url": "http://localhost:1234/v1",
    "local_model": "qwen2.5-coder-14b-instruct",
    "local_api_key": "",

    # Variant generation
    "variant_mode": "sequential",
    "max_variants": 3,

    # Cap on tool-use rounds per assistant turn. The agent loops
    # "model → tools → model → tools" until the LLM returns end_turn.
    # Raised 25 → 50 (Pilot 01.07.2026): a single furniture request
    # routinely needs 25+ rounds (curve build → extrude → round → verify),
    # so 25 cut complex tasks off mid-step and read to participants like the
    # assistant had "stopped". On exhaustion the loop now pauses gracefully
    # with a resumable "weiter?"-message instead of erroring (agent/loop.py).
    "max_iterations": 50,

    # FastAPI server (localhost)
    "port": 8765,

    # Study-mode fields (ignored in normal mode)
    "participant_id": "",
    "consent_given": False,
    "snapshot_interval_seconds": 30,

    # Default condition for newly created sessions when study mode is off.
    # In study mode the pre-session dialog overrides this per session.
    # See Studienartefakt-Spec §1.2.
    "default_condition": "werkzeug",

    # Directory where study export bundles get dropped
    # (Studienartefakt-Spec §2.6). Empty string = use the default
    # ~/Documents/masterarbeit-studie/exports. Path-expanding happens
    # in plugin.backend.export so a researcher can edit config.json
    # with a literal "~" too.
    "export_dir": "",
}

# Settings that must be positive integers. config.json is free to hand-edit
# (and a buggy client can PATCH a wrong type), so a string/null/negative here
# would otherwise crash a session deep in the agent loop. Normalised on load.
_INT_FIELDS = (
    "max_tokens",
    "max_iterations",
    "port",
    "snapshot_interval_seconds",
    "max_variants",
)


class Config:
    """Read/write plugin settings from config.json."""

    def __init__(self, path=None):
        self._path = path or _CONFIG_PATH
        self._data = dict(_DEFAULTS)
        self.load()

    def load(self):
        if os.path.exists(self._path):
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    stored = json.load(f)
                self._data.update(stored)
                self._migrate()
                self._coerce_int_fields()
            except Exception as e:
                logger.warning(
                    "config: konnte %s nicht laden, nutze Defaults (%s)",
                    self._path,
                    e,
                )

    def _migrate(self):
        """One-shot fixups for deprecated values in persisted configs."""
        dirty = False
        model = self._data.get("model", "")
        if model and model not in _KNOWN_MODELS:
            logger.warning(
                "config: deprecated model_id %r → resetting to default %r",
                model,
                _DEFAULTS["model"],
            )
            self._data["model"] = _DEFAULTS["model"]
            dirty = True
        if dirty:
            try:
                self.save()
            except Exception as e:
                logger.warning("config migration save failed: %s", e)

    def _coerce_int_fields(self):
        """Normalise the positive-int settings, falling back to the default
        (with a warning) on a non-numeric or non-positive value. Keeps a
        hand-edited or partially-written config.json from turning a stray
        ``"4096px"`` / ``null`` / ``-1`` into an 'Agent abgestürzt' crash deep
        in the agent loop. A valid config is left byte-identical."""
        for key in _INT_FIELDS:
            raw = self._data.get(key, _DEFAULTS[key])
            try:
                value = int(raw)
            except (TypeError, ValueError):
                value = None
            if value is None or value < 1:
                logger.warning(
                    "config: %s=%r ist kein positiver Integer - nutze Default %r",
                    key,
                    raw,
                    _DEFAULTS[key],
                )
                value = _DEFAULTS[key]
            self._data[key] = value

    def save(self):
        """Persist settings atomically: write a temp file in the same
        directory, fsync it, then os.replace() onto config.json. A crash or
        full disk mid-write can therefore never leave a truncated/0-byte
        config.json — which would silently reset study mode, API key and
        participant id to defaults. Errors are re-raised (callers handle them)
        after cleaning up the temp file."""
        directory = os.path.dirname(self._path) or "."
        fd, tmp_path = tempfile.mkstemp(
            dir=directory, prefix=".config-", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self._path)
        except Exception:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            raise

    def __getattr__(self, name):
        if name.startswith("_"):
            return super().__getattribute__(name)
        if name in self._data:
            return self._data[name]
        raise AttributeError(name)

    def __setattr__(self, name, value):
        if name.startswith("_"):
            super().__setattr__(name, value)
        else:
            self._data[name] = value

    def as_dict(self):
        """Return a copy of all settings (for serialisation to frontend)."""
        return dict(self._data)

    @property
    def is_api_mode(self):
        return self._data.get("backend_mode") == "api"

    @property
    def is_mcp_mode(self):
        return self._data.get("backend_mode") == "mcp"

    @property
    def is_study_mode(self):
        return self._data.get("use_mode") == "study"

    @property
    def is_local_model(self):
        """``True`` when the selected model points to a local OpenAI-compatible
        endpoint (LM Studio / Ollama) routed through litellm rather than the
        Anthropic SDK."""
        return self._data.get("model") == "local"


# Singleton
config = Config()
