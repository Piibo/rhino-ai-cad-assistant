"""Pydantic v2 schemas for messages, content blocks, events, and settings.

Content blocks follow Anthropic's Messages API format so they can be passed
through to the Anthropic API without translation. Plugin-specific blocks
(sketch, selection, point_pick, component_pick) are transformed to native
text/image blocks before API dispatch — see ``_flatten_plugin_blocks`` in
``agent/message_flattener.py`` (driven by ``agent/history._history_to_api``).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator


# ---------------------------------------------------------------------------
# Native Anthropic content blocks
# ---------------------------------------------------------------------------


class ImageSource(BaseModel):
    """Base64-encoded inline image (Anthropic format)."""

    type: Literal["base64"] = "base64"
    media_type: Literal["image/png", "image/jpeg", "image/webp", "image/gif"]
    data: str  # base64-encoded, no data-URL prefix


class TextBlock(BaseModel):
    type: Literal["text"] = "text"
    text: str


class ImageBlock(BaseModel):
    type: Literal["image"] = "image"
    source: ImageSource
    # Herkunft des Bildes: "viewport" = Kamera-Knopf-Schnappschuss des
    # eigenen Rhino-Viewports, None/"upload" = echtes Referenzbild.
    # Studienrelevant: das W2-Gate (fragebogen-spec §5.2.4) und der
    # Tool-Nutzungs-Report zählen nur echte Uploads als Referenzbild.
    # Wird vor dem API-Send gestrippt (message_flattener) — die
    # Anthropic-API kennt das Feld nicht.
    origin: Optional[Literal["upload", "viewport"]] = None


class ToolUseBlock(BaseModel):
    type: Literal["tool_use"] = "tool_use"
    id: str
    name: str
    input: dict[str, Any] = Field(default_factory=dict)


class ToolResultBlock(BaseModel):
    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str
    content: Union[str, list[dict[str, Any]]]
    is_error: bool = False


# ---------------------------------------------------------------------------
# Plugin-specific extension blocks (UI-only; converted before API send)
# ---------------------------------------------------------------------------


class SketchBlock(BaseModel):
    """Free-hand sketch captured via the sketch-canvas overlay.

    The overlay shows a viewport snapshot (``background``) as a backdrop and
    lets the user draw strokes on top. ``svg`` keeps those strokes as SVG
    markup so the sketch can be edited or re-rendered later. ``rendered_png``
    is the already-composited bitmap (background + strokes) the frontend
    produces via ``canvas.toDataURL`` when the user hits "Fertig" — this is
    what goes to the Anthropic API as a single image, so the model sees a
    coherent annotated picture rather than two overlaid layers.
    """

    type: Literal["sketch"] = "sketch"
    svg: str
    background: Optional[ImageSource] = None
    # Composite (background + strokes) rendered by the frontend — preferred
    # when flattening for the API. Optional for back-compat with old DB rows
    # that only carry ``svg`` + ``background``.
    rendered_png: Optional[ImageSource] = None
    width: int
    height: int


class SelectionBlock(BaseModel):
    """Reference to one or more Rhino objects selected in the viewport."""

    type: Literal["selection"] = "selection"
    object_ids: list[str] = Field(default_factory=list)
    snapshot: Optional[ImageSource] = None
    names: list[str] = Field(default_factory=list)
    # Coarse Rhino type labels (e.g. "polysurface", "curve", "subd") aligned
    # 1:1 with object_ids. Used as a human-readable fallback when the user
    # hasn't named the picked objects. Optional for back-compat with older
    # DB rows that predate the field.
    types: list[str] = Field(default_factory=list)


class PointPickBlock(BaseModel):
    """Single point picked in the viewport, optionally snapped on geometry."""

    type: Literal["point_pick"] = "point_pick"
    point: list[float] = Field(default_factory=list)  # [x, y, z]
    snapshot: Optional[ImageSource] = None
    object_id: Optional[str] = None
    object_name: Optional[str] = None
    object_type: Optional[str] = None
    snap_type: Optional[str] = None


class ComponentPickBlock(BaseModel):
    """Single edge/face/object picked on existing geometry in the viewport."""

    type: Literal["component_pick"] = "component_pick"
    component_type: Literal["edge", "face", "vertex", "object"]
    component_index: Optional[int] = None
    geometry_class: Optional[str] = None  # "brep" | "extrusion" | "subd" | "mesh" | None
    point: list[float] = Field(default_factory=list)  # stable reference point
    pick_point: Optional[list[float]] = None  # raw viewport click point
    component_info: dict[str, Any] = Field(default_factory=dict)
    snapshot: Optional[ImageSource] = None
    object_id: Optional[str] = None
    object_name: Optional[str] = None
    object_type_name: Optional[str] = None
    object_class: Optional[str] = None
    allowed_operations: list[str] = Field(default_factory=list)


ContentBlock = Annotated[
    Union[
        TextBlock,
        ImageBlock,
        ToolUseBlock,
        ToolResultBlock,
        SketchBlock,
        SelectionBlock,
        PointPickBlock,
        ComponentPickBlock,
    ],
    Field(discriminator="type"),
]


# ---------------------------------------------------------------------------
# Messages & sessions
# ---------------------------------------------------------------------------


def _uid() -> str:
    return uuid.uuid4().hex[:16]


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Message(BaseModel):
    """A single message in a conversation."""

    id: str = Field(default_factory=_uid)
    session_id: str
    role: Literal["user", "assistant", "system"]
    content: list[ContentBlock]
    created_at: datetime = Field(default_factory=_now)
    model: Optional[str] = None  # set for assistant messages
    stop_reason: Optional[str] = None
    # FF2 modality tagging - which input modes the user/system used in this
    # message. Derived from content blocks at write-time + augmented by
    # synthetic events (e.g. "parameter" for slider-driven updates).
    # None = legacy/untagged (older rows). Empty list = explicitly no
    # modality (assistant turns, system messages).
    modality: Optional[list[str]] = None

    model_config = ConfigDict(json_schema_extra={"examples": []})


# ---------------------------------------------------------------------------
# Exposed parameters - slider-driven direct manipulation (FF2)
# ---------------------------------------------------------------------------


class ParameterAction(BaseModel):
    """How a slider change maps to a Rhino-side mutation.

    Kept narrow on purpose. Only axis-aligned transformations are allowed
    so the GUID of the target object stays stable across slider moves -
    crucial because the slider's ``current`` value tracks against that
    same ID. Full rebuilds would invalidate the ID and lose the slider's
    connection to the object.
    """

    type: Literal[
        "move_axis",
        "scale_axis",
        "scale_uniform",
        "rotate_axis",
        "gh_slider",
        "editable_recipe_value",
    ]
    target_object_ids: list[str] = Field(default_factory=list)
    axis: Literal["x", "y", "z"] = "z"
    origin: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    instance_guid: Optional[str] = None
    parameter_key: Optional[str] = None

    @model_validator(mode="after")
    def _validate_targeting(self) -> "ParameterAction":
        if self.type == "gh_slider":
            if not self.instance_guid:
                raise ValueError("gh_slider action braucht instance_guid.")
        elif self.type == "editable_recipe_value":
            if not self.target_object_ids:
                raise ValueError(
                    "editable_recipe_value braucht target_object_ids."
                )
            if not self.parameter_key:
                raise ValueError(
                    "editable_recipe_value braucht parameter_key."
                )
        elif not self.target_object_ids:
            raise ValueError(
                "Transformations-Aktionen brauchen target_object_ids."
            )
        return self


class ExposedParameter(BaseModel):
    """A slider definition the LLM hands to the UI."""

    name: str
    current: float
    min: float
    max: float
    step: Optional[float] = None
    display_unit: str = ""
    display_factor: float = 1.0
    source: Literal[
        "editable_structure",
        "gh_slider",
        "agent_exposed",
    ] = "agent_exposed"
    source_label: Optional[str] = None
    source_priority: int = 50
    structure_id: Optional[str] = None
    structure_key: Optional[str] = None
    action: Optional[ParameterAction] = None
    actions: list[ParameterAction] = Field(default_factory=list)

    @model_validator(mode="after")
    def _normalise_actions(self) -> "ExposedParameter":
        if self.action is not None and not self.actions:
            self.actions = [self.action]
        elif self.action is None and self.actions:
            self.action = self.actions[0]

        if not self.actions:
            raise ValueError("ExposedParameter braucht mindestens eine action.")

        if (
            self.source == "agent_exposed"
            and any(action.type == "gh_slider" for action in self.actions)
        ):
            self.source = "gh_slider"

        if not self.source_label:
            self.source_label = {
                "editable_structure": "Struktur",
                "gh_slider": "GH",
                "agent_exposed": "Agent",
            }[self.source]

        if self.source_priority == 50:
            self.source_priority = {
                "editable_structure": 0,
                "gh_slider": 10,
                "agent_exposed": 30,
            }[self.source]
        return self


class Session(BaseModel):
    """A conversation session. One session per Rhino document / study run."""

    id: str = Field(default_factory=_uid)
    title: str = "Neue Sitzung"
    created_at: datetime = Field(default_factory=_now)
    use_mode: Literal["normal", "study"] = "normal"
    participant_id: Optional[str] = None
    rhino_doc_path: Optional[str] = None
    # Study condition for FF1/FF2 within-subjects comparison (Studienartefakt-Spec §1,
    # Designkorrektur 11.06.2026). Both conditions share the same core-CAD tool
    # stack — the basis condition is a fully CAD-capable prompt-based assistant,
    # not a three-tool chatbot. "basis" = shared core-CAD tools only, interaction
    # UI slots hidden; "werkzeug" = same core-CAD tools plus the interaction tools
    # (dialogs, sliders, variants, locks, visible selection) and their UI slots.
    # The split lives in tool_registry.INTERACTION_TOOL_NAMES (the only place a
    # tool is gated by condition). Set at session creation from
    # Settings.default_condition (dev mode) or the pre-session dialog (study mode,
    # Schritt 2). Immutable afterwards.
    # Fail-safe default (Pilot 01.07.2026): "basis" is the smaller,
    # interaction-free surface. Every real creation path sets condition
    # explicitly (study dialog / config.default_condition), so this default
    # only bites when the field is omitted — and then it must fail SAFE to
    # basis, never open into the full interaction surface.
    condition: Literal["basis", "werkzeug"] = "basis"


# ---------------------------------------------------------------------------
# Editable structures - shared layer between free geometry and explicit control
# ---------------------------------------------------------------------------


class StructureAnchor(BaseModel):
    """Participant-facing anchor inside a structure context."""

    kind: Literal[
        "object",
        "component",
        "selection",
        "gh_slider",
    ]
    label: str
    object_id: Optional[str] = None
    object_name: Optional[str] = None
    component_type: Optional[Literal["edge", "face", "object"]] = None
    component_index: Optional[int] = None
    instance_guid: Optional[str] = None


class EditableStructureContext(BaseModel):
    """Minimal persisted context for an explicitly editable structure."""

    id: str = Field(default_factory=_uid)
    session_id: str
    structure_type: Literal["primitive", "grasshopper"]
    structure_key: str
    title: str
    summary: str = ""
    source: Literal[
        "selection",
        "component_pick",
        "parameters",
        "tool_result",
    ]
    object_id: Optional[str] = None
    object_name: Optional[str] = None
    object_class: Optional[str] = None
    editable_recipe_type: Optional[str] = None
    editable_operations: list[str] = Field(default_factory=list)
    anchors: list[StructureAnchor] = Field(default_factory=list)
    parameter_names: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime = Field(default_factory=_now)


# ---------------------------------------------------------------------------
# Variants - multiple design alternatives (FF1 / FF3)
# ---------------------------------------------------------------------------


class Variant(BaseModel):
    """One of several design alternatives generated in parallel.

    Each variant lives on its own Rhino layer (typically ``Variant_<name>``).
    Only one variant is active at a time; inactive variants stay hidden so
    the viewport remains readable. ``thumbnail`` is captured once the agent
    calls ``finish_variants`` and is what the frontend gallery renders.
    """

    id: str = Field(default_factory=_uid)
    session_id: str
    name: str
    description: str = ""
    layer_name: str
    thumbnail: Optional[ImageSource] = None
    is_active: bool = False
    created_at: datetime = Field(default_factory=_now)


# ---------------------------------------------------------------------------
# Inspection - participant-facing read path
# ---------------------------------------------------------------------------


class InspectionFact(BaseModel):
    label: str
    value: str
    emphasis: Literal["default", "success", "warning"] = "default"


class InspectionResult(BaseModel):
    id: str = Field(default_factory=_uid)
    session_id: str
    scope: Literal["selection", "component_pick", "structure_context"]
    title: str
    summary: str = ""
    facts: list[InspectionFact] = Field(default_factory=list)
    related_structure: Optional[EditableStructureContext] = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_now)


class InspectRequest(BaseModel):
    target: Literal["selection", "component_pick", "active_structure"]
    selection: Optional[SelectionBlock] = None
    component_pick: Optional[ComponentPickBlock] = None


# ---------------------------------------------------------------------------
# WebSocket events
# ---------------------------------------------------------------------------


WsEventType = Literal[
    # Chat
    "message.new",
    "message.delta",
    "message.complete",
    "message.error",
    # Viewport
    "viewport.snapshot",
    "viewport.selection_changed",
    "viewport.pick_result",
    "viewport.point_result",
    "viewport.component_result",
    # Live-Modifier-Status waehrend eines Hover-Picks ({shift, object}) — der
    # Picker pusht bei jeder Modifier-Aenderung, damit das Panel-Banner den
    # aktiven Modus live anzeigt (das Panel sieht die Tasten waehrend des
    # modalen Rhino-Picks selbst nicht). Ephemer, ohne session_id.
    "viewport.pick_modifiers",
    # Hands the frontend a fresh viewport JPEG to use as the sketch-overlay
    # backdrop. The finished sketch never round-trips through the server —
    # it's composited client-side and staged as a SketchBlock attachment on
    # the next chat.send.
    "viewport.sketch_ready",
    "viewport.error",
    # Rhino undo/redo happened — clears the PER-OBJECT UI dismissals
    # (dismissedStructureIds), sodass ein Ctrl+Z ein weggeklicktes EDITABLE/
    # primitives Parameter-Panel zurueckholt. GH-/agent-Panels werden beim × dagegen
    # hart gecleart (clear_grasshopper_context) und kommen NICHT ueber Undo zurueck —
    # dafuer der Verwerfen-Button (GH) bzw. ein bewusstes Re-Expose der KI. WS-only
    # Interaction-Layer, hash-neutral (kein Modell-Tool, kein system_prompt-Eintrag).
    "viewport.undo_redo",
    # Active editable structure context
    "structure_context.updated",
    "structure_context.cleared",
    # Parameter sliders (FF2 direct manipulation)
    "parameter.exposed",
    "parameter.cleared",
    "parameter.updated",
    # Variants (FF1 co-creator pattern, FF3 ownership)
    "variant.added",
    "variant.selected",
    "variant.removed",
    "variant.cleared",
    "variant.thumbnail_ready",
    # Sessions
    "session.created",
    "session.switched",
    # Selektives Vorschau-Gate (Part C, Design A). WS-only Interaction-Layer
    # — keine Modell-Tools, kein Eintrag in tool_schemas/system_prompt, daher
    # hash-neutral. ``gate.preview`` zeigt die Vorschaukarte, ``gate.cleared``
    # entfernt sie (Entscheidung/Timeout/Cancel), ``run.cancelled`` quittiert
    # einen abgebrochenen Run.
    "gate.preview",
    "gate.cleared",
    "run.cancelled",
    # Config & study
    "settings.updated",
    "study.event_logged",
    # Generic
    "ack",
    "error",
]


class WsEvent(BaseModel):
    """Envelope for all WebSocket traffic between backend and frontend."""

    type: WsEventType
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=_now)
    correlation_id: Optional[str] = None  # for request/response matching


class WsCommand(BaseModel):
    """Inbound command from frontend (user actions)."""

    type: Literal[
        "chat.send",
        "chat.cancel",
        "viewport.request_snapshot",
        "viewport.request_pick",
        "viewport.request_point",
        "viewport.request_component",
        "viewport.request_sketch",
        # WS-only window-resize signal (grow/restore the floating window around
        # the active viewport while the sketch overlay is open). Not a model
        # tool — never reaches tool_schemas/system_prompt, so hash-neutral.
        "viewport.sketch_window",
        # WS-only transient hover-highlight of an inline reference token. Drawn
        # via a non-mutating DisplayConduit (no selection change, no doc edit),
        # so the selection_watcher / study logging stays untouched. Not a model
        # tool — never reaches tool_schemas/system_prompt, so hash-neutral.
        "viewport.highlight_reference",
        "viewport.highlight_clear",
        # WS-only persistente Punkt-Referenz-Marker (kleine Dots an gepickten
        # Punkten, Referenz-Ansicht). payload: {points: [[x,y,z], ...]};
        # leere Liste loescht. Non-mutating DisplayConduit, kein Modell-Tool ->
        # hash-neutral.
        "viewport.point_markers",
        # WS-only persistente DUENNE Komponenten-Markierung der gestagten K/F/O-
        # Referenzen (Kante/Flaeche/Objekt bleiben duenn blau, solange das Token im
        # Composer steht). payload: {blocks: [{type:"component_pick", object_id,
        # component_type, component_index}, ...]}; leere Liste loescht. Eigener
        # DisplayConduit, non-mutating -> hash-neutral.
        "viewport.persistent_refs",
        # Selektives Vorschau-Gate (Part C): der Designer löst die
        # Vorschaukarte eines destruktiven Tool-Calls auf
        # ({tu_id, decision: "accept"|"revert"}). WS-only Interaction-Layer,
        # kein Modell-Tool — hash-neutral.
        "gate.resolve",
        "parameter.changed",
        "parameter.clear",
        "variant.select",
        "variant.delete",
        "variant.show_original",
        "variants.clear",
        "variant.commit",
        "session.create",
        "session.switch",
        "settings.update",
        "study.log_event",
    ]
    payload: dict[str, Any] = Field(default_factory=dict)
    correlation_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Settings (serialised view of Config for frontend)
# ---------------------------------------------------------------------------


class Settings(BaseModel):
    """All user-facing plugin settings. Mirrors backend/config.py."""

    backend_mode: Literal["mcp", "api"] = "mcp"
    use_mode: Literal["normal", "study"] = "normal"
    api_key: str = ""
    model: str = "claude-sonnet-5"
    max_tokens: int = 8192
    variant_mode: Literal["sequential", "parallel"] = "sequential"
    max_variants: int = 3
    max_iterations: int = 50
    port: int = 8765
    participant_id: str = ""
    consent_given: bool = False
    snapshot_interval_seconds: int = 30
    # OpenAI-compatible endpoint -- LM Studio / Ollama (local) or OpenAI /
    # OpenRouter (cloud). Only used when ``model == "local"``. Calls route
    # through ``litellm``, so no extra proxy process is required.
    local_base_url: str = "http://localhost:1234/v1"
    local_model: str = "qwen2.5-coder-14b-instruct"
    # API key for the OpenAI-compatible endpoint. Empty for LM Studio /
    # Ollama (they ignore auth); set for cloud providers (OpenAI / OpenRouter).
    local_api_key: str = ""
    # Default study condition for newly created sessions in dev mode
    # (Studienartefakt-Spec §1.2). In study mode the pre-session dialog
    # overrides this per session.
    default_condition: Literal["basis", "werkzeug"] = "werkzeug"
    # Export-bundle target directory (Studienartefakt-Spec §2.6).
    # Empty string = use the default ~/Documents/masterarbeit-studie/exports.
    export_dir: str = ""


# ---------------------------------------------------------------------------
# Study-mode event log entry
# ---------------------------------------------------------------------------


StudyEventType = Literal[
    "session_started",
    "session_ended",
    "consent_given",
    "message_sent",
    "message_received",
    "viewport_snapshot",
    "selection_changed",
    "sketch_submitted",
    "picker_used",
    "settings_changed",
    "survey_response",
    "free_comment",
]


class StudyEvent(BaseModel):
    """A single entry in the study-mode event log (only written in study mode)."""

    id: str = Field(default_factory=_uid)
    session_id: str
    participant_id: Optional[str] = None
    event_type: StudyEventType
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=_now)


# ---------------------------------------------------------------------------
# Studienartefakt-Spec §2 — Study-mode shell (Schritt 2)
# ---------------------------------------------------------------------------


class StudySession(BaseModel):
    """One row per study run (Studienartefakt-Spec §2.1 / §2.4).

    A single participant visit produces two ``study_sessions`` rows: same
    ``participant_code``, ``order_index`` 1 and 2, complementary
    ``condition`` values. Pilot lanes count their order independently
    (1..n, repeatable), so the "first/second condition" direction of the
    final-survey comparison block stays reconstructable there too.
    The matching plugin session (one per study
    session) is referenced via ``session_id`` (1:1, enforced by a UNIQUE
    constraint at the DB layer).
    """

    id: str = Field(default_factory=_uid)
    session_id: str
    participant_code: str
    condition: Literal["basis", "werkzeug"]
    task_variant: Literal["A", "B"]
    setting: Literal["privat", "lab", "remote"] = "privat"
    is_pilot: bool = False
    # ge=1 ohne Obergrenze: echte Laeufe zaehlen 1..2 (Guard limitiert),
    # Pilot-Laeufe zaehlen ihre eigene Lane fortlaufend (1..n).
    order_index: int = Field(ge=1)
    status: Literal["active", "completed", "aborted"] = "active"
    created_at: datetime = Field(default_factory=_now)
    ended_at: Optional[datetime] = None


class StudySessionCreate(BaseModel):
    """Inbound payload from the pre-session dialog (§2.1).

    ``order_index`` is computed server-side from the participant's
    existing study_sessions count, so it's not part of the request.
    """

    participant_code: str = Field(min_length=1, max_length=32)
    condition: Literal["basis", "werkzeug"]
    task_variant: Literal["A", "B"]
    setting: Literal["privat", "lab", "remote"] = "privat"
    is_pilot: bool = False
    # Optional human-readable session title for the sidebar; if omitted
    # the backend builds one from participant_code + order_index.
    title: Optional[str] = None


class StudyParticipantRun(BaseModel):
    """Compact run summary for the researcher-facing pre-session checks."""

    study_session_id: str
    session_id: str
    condition: Literal["basis", "werkzeug"]
    task_variant: Literal["A", "B"]
    is_pilot: bool
    order_index: int
    status: Literal["active", "completed", "aborted"]
    created_at: datetime
    ended_at: Optional[datetime] = None


class StudyParticipantStatus(BaseModel):
    """Availability for the next study run of one participant code.

    Real study runs are limited to one run per condition. Pilot runs are
    intentionally excluded from this lock so dry-runs can be repeated without
    poisoning the actual participant schedule.
    """

    participant_code: str
    is_pilot: bool = False
    used_conditions: list[Literal["basis", "werkzeug"]] = Field(default_factory=list)
    available_conditions: list[Literal["basis", "werkzeug"]] = Field(
        default_factory=list
    )
    # Parallel zu used/available_conditions, aber fuer die Aufgabenvariante (A/B).
    # Im zweiten Lauf eines Kuerzels ist die in Lauf 1 genutzte Aufgabe "used" und
    # die andere "available" -> der Pre-Session-Dialog graut die genutzte aus
    # (Counterbalancing: nie dieselbe Aufgabe zweimal beim selben Kuerzel).
    used_task_variants: list[Literal["A", "B"]] = Field(default_factory=list)
    available_task_variants: list[Literal["A", "B"]] = Field(default_factory=list)
    next_order_index: int = 1
    has_reusable_consent: bool = False
    active_run_open: bool = False
    # Ob für diese Teilnehmenden-Lane (code + pilot-Flag) bereits ein
    # Demografie-Datensatz existiert — steuert, ob der Demografie-Block
    # nach dem Consent des ersten Laufs gezeigt wird (fragebogen-spec §4.1).
    has_demographics: bool = False
    runs: list[StudyParticipantRun] = Field(default_factory=list)


class Consent(BaseModel):
    """Persisted consent record (§2.2).

    ``text_hash`` is a SHA-256 of the displayed consent text so that
    later wording changes are detectable. ``checkboxes_json`` carries
    the IDs and bool states of every consent checkbox the participant
    ticked, so retroactive audits don't have to guess.
    """

    id: str = Field(default_factory=_uid)
    study_session_id: str
    text_hash: str
    confirmed_at: datetime = Field(default_factory=_now)
    checkboxes: dict[str, bool] = Field(default_factory=dict)


class ConsentSubmission(BaseModel):
    """Inbound consent confirmation (§2.2)."""

    study_session_id: str
    text_hash: str
    checkboxes: dict[str, bool]


# Study-scoped event log per §2.4 — distinct from the older
# ``study_events`` table, which logs plugin-session-scoped events and
# stays in place for backward compat.
StudyContextEventType = Literal[
    "consent_given",
    "pause_marker",
    "task_phase_change",
    "agency_block_shown",
    "condition_toggle_blocked",
    "model_change_blocked",
    "study_session_started",
    "study_session_ended",
    "study_session_aborted",
    # Fragebogen-Marker (fragebogen-spec §2.4): ein Eintrag pro
    # abgeschicktem Block, payload trägt den Block-Identifier.
    "survey_submitted",
    "demographics_submitted",
    "final_survey_submitted",
    # Lauf-Fehler (Tier-2-Haertung): API-/Agent-Fehler werden zusaetzlich zur
    # WS-Meldung als Studien-Event persistiert, damit die Auswertung sie sieht,
    # auch wenn der WS-Broadcast den Client nicht erreicht.
    "agent_error",
    # Counterbalancing-Audit-Trail (Spec paragraph 2.1): der Studienleiter ist
    # vom automatischen Kuerzel-Zyklus-Vorschlag abgewichen; payload traegt
    # suggested_*/chosen_* fuer Bedingung und Aufgabenvariante.
    "counterbalancing_override",
    # Designer-Affordanz-Nutzung (FF1/FF2): typisierte Spur fuer Interaktionen,
    # die sonst nicht aus dem Transkript rekonstruierbar sind — ein Quick-Reply-
    # Klick sieht im messages-Log wie getippter Text aus, eine Varianten-Wahl
    # hinterlaesst keine tool_calls-Zeile. Picks/Sketches tragen zwar
    # messages.modality, werden hier aber zusaetzlich als eigener Event-Typ
    # gefuehrt, damit FF1/FF2 eine einheitliche Event-Tabelle abfragen koennen.
    "quick_reply_used",
    "pick_used",
    "sketch_submitted",
    "variant_selected",
    # Condition-Audit-Trail (Pilot-Fix 01.07.): die tatsaechlich ans Modell
    # gereichte Tool-Surface pro (Session, Surface) — interaction_tools_present
    # MUSS in basis leer sein. Fehlte bis 06.07. im Literal, wodurch die
    # Emission in loop._log_tool_surface still an der Validierung scheiterte
    # und das Event NIE persistiert wurde (Bundles ohne Leak-Beleg).
    "tool_surface",
    # Turn-Limit erreicht (graceful "weiter?"-Pause statt Fehler) — gleicher
    # Literal-Luecken-Bug wie tool_surface, gleiche Reparatur 06.07.
    "max_iterations_reached",
]


class StudyContextEvent(BaseModel):
    """Row of the ``events`` table (§2.4): study-session-scoped marker."""

    id: str = Field(default_factory=_uid)
    study_session_id: str
    event_type: StudyContextEventType
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_now)


class StudyContextEventCreate(BaseModel):
    """Inbound payload to log a study-scoped event."""

    study_session_id: str
    event_type: StudyContextEventType
    payload: dict[str, Any] = Field(default_factory=dict)


class ConsentTextResponse(BaseModel):
    """Response of the consent-text endpoint.

    The frontend uses ``text`` for the dialog body and ``text_hash`` to
    pass through unchanged on submission, so the server can verify the
    participant confirmed the exact wording it served.
    """

    text: str
    text_hash: str
    checkbox_labels: list[str]


# ----- Agency survey (§2.5) -----


class AgencyItemModel(BaseModel):
    id: str
    dimension: str
    label: str


class AgencyScaleModel(BaseModel):
    min: int
    max: int
    anchor_min: str
    anchor_max: str


class AgencyItemsResponse(BaseModel):
    scale: AgencyScaleModel
    items: list[AgencyItemModel]
    note: str = ""


class _PerConditionSurveyFields(BaseModel):
    """Shared field set of the per-condition survey block.

    Historisch hieß die Tabelle ``agency_survey`` und trug nur die vier
    Rafner-Items; seit dem Lean-Fragebogen (fragebogen-spec §10.1) trägt
    sie den gesamten Per-Bedingungs-Block: Mini-CSI, Agency,
    Werkzeug-Items (mit „nicht genutzt“-Flags), W-OPEN1 und R1. Alle
    Felder sind optional, weil Items je nach Bedingung/Gating
    ausgeblendet sein können (Spec §4.3).
    """

    # Agency (Rafner-4, Wortlaut aus agency_items.json)
    self_efficacy: Optional[int] = Field(default=None, ge=1, le=7)
    control: Optional[int] = Field(default=None, ge=1, le=7)
    autonomy: Optional[int] = Field(default=None, ge=1, le=7)
    ownership: Optional[int] = Field(default=None, ge=1, le=7)
    # Mini-CSI (C1–C6)
    csi_exploration: Optional[int] = Field(default=None, ge=1, le=7)
    csi_expressiveness: Optional[int] = Field(default=None, ge=1, le=7)
    csi_immersion: Optional[int] = Field(default=None, ge=1, le=7)
    csi_enjoyment: Optional[int] = Field(default=None, ge=1, le=7)
    csi_results_worth_effort: Optional[int] = Field(default=None, ge=1, le=7)
    csi_collaboration: Optional[int] = Field(default=None, ge=1, le=7)
    # Werkzeug-/Modalitäts-Items (Lean-Set; W12 nur Vollerhebung); ``*_unused`` =
    # die „habe ich nicht genutzt“-Option, dann bleibt der Likert-Wert None.
    tool_text: Optional[int] = Field(default=None, ge=1, le=7)
    tool_text_unused: Optional[bool] = None
    tool_image_ref: Optional[int] = Field(default=None, ge=1, le=7)
    tool_image_ref_unused: Optional[bool] = None
    tool_viewport_feedback: Optional[int] = Field(default=None, ge=1, le=7)
    tool_viewport_feedback_unused: Optional[bool] = None
    tool_selection_badge: Optional[int] = Field(default=None, ge=1, le=7)
    tool_selection_badge_unused: Optional[bool] = None
    tool_pick: Optional[int] = Field(default=None, ge=1, le=7)
    tool_pick_unused: Optional[bool] = None
    tool_slider: Optional[int] = Field(default=None, ge=1, le=7)
    tool_slider_unused: Optional[bool] = None
    tool_call_cards: Optional[int] = Field(default=None, ge=1, le=7)
    tool_call_cards_unused: Optional[bool] = None
    tool_lock: Optional[int] = Field(default=None, ge=1, le=7)
    tool_lock_unused: Optional[bool] = None
    tool_variants: Optional[int] = Field(default=None, ge=1, le=7)
    tool_variants_unused: Optional[bool] = None
    tool_sketch: Optional[int] = Field(default=None, ge=1, le=7)
    tool_sketch_unused: Optional[bool] = None
    tool_dialog_offers: Optional[int] = Field(default=None, ge=1, le=7)
    tool_dialog_offers_unused: Optional[bool] = None
    # Zusammenarbeit mit der KI (Z1/Z2 invers ausgewertet; beide Bedingungen)
    collab_editor_trap: Optional[int] = Field(default=None, ge=1, le=7)
    collab_expression_limit: Optional[int] = Field(default=None, ge=1, le=7)
    # Z3 (02.07.2026): wahrgenommene Systemkompetenz — Confound-Check zur
    # 11.06.-Designkorrektur. Der CAD-Kern ist in beiden Bedingungen identisch;
    # ob Teilnehmende ihn auch als gleich kompetent ERLEBEN, ist empirisch offen
    # (Pilot: "interaktion gut, ai schlecht"). Nicht invers.
    collab_system_competence: Optional[int] = Field(default=None, ge=1, le=7)
    # W-OPEN1: Multi-Select der kaum genutzten Werkzeuge + Freitext
    tools_unused_selection: list[str] = Field(default_factory=list)
    tools_unused_freetext: str = ""
    # R1: offene Reflexion pro Bedingung
    reflection_freetext: str = ""
    # Legacy-Freitext der alten 4-Item-Fassung; bleibt für Altdaten.
    notes: str = ""


class AgencySurveyResponse(_PerConditionSurveyFields):
    """Inbound payload from the per-condition survey wizard.

    ``fragebogen_version_hash`` wird vom Frontend aus der Survey-Config
    durchgereicht, damit die Antwort an den exakt gesehenen Wortlaut
    gebunden ist (fragebogen-spec §7.5/§10.3).
    """

    fragebogen_version_hash: Optional[str] = None


class AgencySurveyRecord(_PerConditionSurveyFields):
    id: str = Field(default_factory=_uid)
    study_session_id: str
    condition: Literal["basis", "werkzeug"]
    fragebogen_version_hash: Optional[str] = None
    submitted_at: datetime = Field(default_factory=_now)


# ----- Lean-Fragebogen (fragebogen-spec v1.2.7): Config + neue Blöcke -----


class SurveyLikertItem(BaseModel):
    """Ein Likert-Item, wie es das Frontend rendern soll."""

    id: str
    dimension: str
    label: str


class SurveyToolItem(BaseModel):
    """Werkzeug-Item (7-Punkt + „nicht genutzt“-Option)."""

    id: str
    dimension: str
    label: str


class SurveyConfigResponse(BaseModel):
    """Per-Bedingungs-Survey, fertig gegated für genau diesen Lauf.

    Das Backend wendet die Gating-Logik aus fragebogen-spec §4.3 an
    (Bedingung, Bild-Upload, Slider-Nutzung, Feature-Flags), damit das
    Frontend nur noch rendert und keine Studienlogik kennt.
    """

    study_session_id: str
    condition: Literal["basis", "werkzeug"]
    fragebogen_version_hash: str
    intro: str
    scale: AgencyScaleModel
    csi_items: list[SurveyLikertItem]
    agency_items: list[SurveyLikertItem]
    collaboration_items: list[SurveyLikertItem] = Field(default_factory=list)
    # Z3 Systemkompetenz-Wahrnehmungs-Check — eigenes Feld, weil es am
    # ABSOLUTEN ENDE des Per-Bedingungs-Blocks gerendert wird (nach den
    # Werkzeug-Items/offenen Feldern), damit es die vorangehenden Ratings
    # nicht framet (Manipulation-Check-Reihenfolge-Literatur, 02.07.2026).
    competence_items: list[SurveyLikertItem] = Field(default_factory=list)
    tool_items: list[SurveyToolItem]
    tool_not_used_label: str
    unused_question_label: str
    unused_options: list[SurveyToolItem]
    reflection_label: str
    reflection_max_chars: int = 500
    # True beim zweiten Lauf des Termins → danach folgt der Vergleichsblock.
    is_final_run: bool = False
    note: str = ""


class DemographicsFieldOption(BaseModel):
    id: str
    label: str


class DemographicsField(BaseModel):
    """Ein Feld des Demografie-Blocks (D1–D11), generisch beschrieben."""

    id: str
    label: str
    kind: Literal["select", "multiselect", "text", "scale", "dropdown"]
    options: list[str] = Field(default_factory=list)
    allow_other: bool = False
    placeholder: str = ""
    scale_min: Optional[int] = None
    scale_max: Optional[int] = None
    anchor_min: str = ""
    anchor_max: str = ""


class DemographicsConfigResponse(BaseModel):
    fragebogen_version_hash: str
    intro: str
    fields: list[DemographicsField]


class DemographicsSubmission(BaseModel):
    """Inbound Demografie-Antworten (alle Angaben freiwillig)."""

    age_range: Optional[str] = None
    gender: Optional[str] = None
    field: Optional[str] = None
    design_experience_years: Optional[str] = None
    cad_experience_years: Optional[str] = None
    rhino_self_assessment: Optional[int] = Field(default=None, ge=1, le=5)
    grasshopper_self_assessment: Optional[int] = Field(default=None, ge=1, le=5)
    other_cad_tools: list[str] = Field(default_factory=list)
    genai_usage_frequency: Optional[str] = None
    genai_design_tools: list[str] = Field(default_factory=list)
    furniture_design_experience: Optional[str] = None
    fragebogen_version_hash: Optional[str] = None


class DemographicsRecord(DemographicsSubmission):
    id: str = Field(default_factory=_uid)
    # Lauf, in dem der Block erhoben wurde (i. d. R. order_index 1).
    study_session_id: str
    participant_code: str
    is_pilot: bool = False
    submitted_at: datetime = Field(default_factory=_now)


class FinalSurveyV2Scale(BaseModel):
    min: int = -2
    max: int = 2
    anchor_min: str = ""
    anchor_mid: str = ""
    anchor_max: str = ""


class FinalSurveyOpenQuestion(BaseModel):
    id: str
    label: str
    max_chars: int = 500


class FinalSurveyConfigResponse(BaseModel):
    """Vergleichsblock V1–V4 + Schlussfragen S1–S3 (fragebogen-spec §5.3/§5.4).

    ``ranking_options`` enthält nur Werkzeuge, die im werkzeug-Lauf
    dieser Teilnehmenden tatsächlich sichtbar waren (gleiche Gating-
    Logik wie der Per-Bedingungs-Block).
    """

    study_session_id: str
    fragebogen_version_hash: str
    intro: str
    preference_label: str
    preference_options: list[DemographicsFieldOption]
    preference_why_label: str
    preference_why_max_chars: int = 300
    v2_label: str
    v2_scale: FinalSurveyV2Scale
    v2_dimensions: list[DemographicsFieldOption]
    ranking_label: str
    ranking_max_rank: int = 3
    ranking_options: list[SurveyToolItem]
    hybrid_label: str
    hybrid_max_chars: int = 500
    open_questions: list[FinalSurveyOpenQuestion]


class FinalSurveySubmission(BaseModel):
    """Inbound Vergleichsblock-Antworten (V1–V4, S1–S3)."""

    preference_choice: Optional[Literal["first", "second", "depends", "unknown"]] = (
        None
    )
    preference_freetext: str = ""
    v2_control: Optional[int] = Field(default=None, ge=-2, le=2)
    v2_expressiveness: Optional[int] = Field(default=None, ge=-2, le=2)
    v2_exploration: Optional[int] = Field(default=None, ge=-2, le=2)
    v2_speed: Optional[int] = Field(default=None, ge=-2, le=2)
    v2_trust: Optional[int] = Field(default=None, ge=-2, le=2)
    v2_ownership: Optional[int] = Field(default=None, ge=-2, le=2)
    tool_importance_rank_1: Optional[str] = None
    tool_importance_rank_2: Optional[str] = None
    tool_importance_rank_3: Optional[str] = None
    hybrid_mode_freetext: str = ""
    surprise_freetext: str = ""
    missing_freetext: str = ""
    wish_freetext: str = ""
    fragebogen_version_hash: Optional[str] = None


class FinalSurveyRecord(FinalSurveySubmission):
    id: str = Field(default_factory=_uid)
    # Referenziert den letzten der beiden Durchgänge (fragebogen-spec §10.1).
    study_session_id: str
    submitted_at: datetime = Field(default_factory=_now)


# ----- Auto-logged study-mode records (§2.4 writers) -----


class ToolCallRecord(BaseModel):
    """One row in ``tool_calls`` (Studienartefakt-Spec §2.4).

    Written automatically by the agent loop alongside the existing
    ``messages`` log so post-hoc queries can answer "how often did the
    model call create_box" without parsing every message blob.
    """

    id: str = Field(default_factory=_uid)
    session_id: str
    message_id: Optional[str] = None
    tool_name: str
    args: dict[str, Any] = Field(default_factory=dict)
    result: Optional[str] = None
    started_at: datetime = Field(default_factory=_now)
    duration_ms: Optional[int] = None
    # --- additive HITL-loop auswertbarkeit (P3, 15.06.2026) ---
    # Beide Felder sind rein additiv (Default None/False) und werden NICHT als
    # eigene DB-Spalten geschrieben, sondern in log_tool_call unter reservierten
    # '_'-Keys in den bestehenden args_json-Blob mitserialisiert -> keine
    # Migration, keine Bruch bestehender Reads/Exports.
    # triggered_by: synthetische Referenzen auf die Pick-/Selektions-Bloecke der
    # letzten Nutzer-Message, die dieses Tool ausgeloest haben (Kandidatenmenge,
    # keine 1:1-Kante). Pro Eintrag z.B.:
    # {kind, object_id, component_type, component_index, ref}.
    triggered_by: Optional[list[dict[str, Any]]] = None
    # is_repair: True fuer undo_last_action / redo_last_action — markiert den
    # Tool-Call als Reparatur-Event fuer die Auswertung.
    is_repair: bool = False
    # is_gate_skip: True, wenn ein destruktiver Tool-Call im selektiven
    # Vorschau-Gate (Part C) vom Designer verworfen wurde (revert/Timeout).
    # Der Call wird geloggt (auswertbar fuer FF2/Kontrolle), aber NICHT
    # ausgefuehrt -> kein model_states-Snapshot. Wie is_repair rein additiv
    # und unter dem '_'-Key '_is_gate_skip' in args_json serialisiert.
    is_gate_skip: bool = False
    # is_gate_autoresolved: True, wenn die Gate-Entscheidung dieses Schritts aus
    # der Run-Entscheidung GEERBT wurde (eine User-Instruktion = ein Vorgang mit
    # ggf. mehreren destruktiven Schritten; nur der erste ist die aktive
    # Entscheidung, der Rest erbt sie). Macht im Export die eine echte
    # Entscheidung von den geerbten unterscheidbar -> is_gate_skip korrekt
    # interpretierbar. Rein additiv, unter '_is_gate_autoresolved' in args_json.
    is_gate_autoresolved: bool = False


class ParameterChangeRecord(BaseModel):
    """One row in ``parameter_changes`` — slider movement (§2.4)."""

    id: str = Field(default_factory=_uid)
    session_id: str
    parameter_name: str
    old_value: Optional[float] = None
    new_value: float
    source: Literal["user", "model"] = "user"
    created_at: datetime = Field(default_factory=_now)


ModelStateTrigger = Literal["tool_call", "parameter", "manual", "variant"]


class ModelStateRecord(BaseModel):
    """One snapshot of the live Rhino document (§2.4).

    Trigger ``tool_call`` snapshots are fire-and-forget after every
    modifying tool call; ``parameter`` snapshots accompany a slider drag
    (the direct-manipulation path that bypasses tool dispatch and therefore
    carries no ``triggering_tool_call_id``); ``manual`` snapshots come from
    a researcher hotkey (future); ``variant`` snapshots accompany variant
    creation (future).
    """

    id: str = Field(default_factory=_uid)
    session_id: str
    label: Optional[str] = None
    trigger: ModelStateTrigger
    triggering_tool_call_id: Optional[str] = None
    rhino_objects: dict[str, Any] = Field(default_factory=dict)
    viewport_path: Optional[str] = None
    created_at: datetime = Field(default_factory=_now)


# ----- Lock-Panel (§1.1, P5) -----


class LockedObject(BaseModel):
    """One designer-locked Rhino object per (session, object_id).

    The chat agent is shown these as a "do not modify" list in the
    system prompt addendum, so the model treats them as off-limits
    unless the designer explicitly removes the lock.
    """

    session_id: str
    object_id: str
    object_name: str = ""
    note: str = ""
    created_at: datetime = Field(default_factory=_now)


class LockObjectRequest(BaseModel):
    """Inbound payload: lock one or more object IDs at once."""

    object_ids: list[str] = Field(default_factory=list, min_length=1)
    object_names: list[str] = Field(default_factory=list)
    note: str = ""


# ----- Export bundle (§2.6) -----


class ExportResponse(BaseModel):
    """Result returned by the export endpoint.

    ``bundle_path`` is an absolute path to the produced ZIP file on
    the researcher's machine. The frontend surfaces it so the
    researcher can show / copy it for archiving.
    """

    bundle_path: str
    bytes: int
    files_included: list[str]
