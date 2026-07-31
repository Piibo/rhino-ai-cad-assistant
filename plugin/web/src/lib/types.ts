// TypeScript mirror of backend/schemas.py.
// Keep in lockstep — if the Pydantic model changes, update here too.

export interface ImageSource {
  type: "base64";
  media_type: "image/png" | "image/jpeg" | "image/webp" | "image/gif";
  data: string;
}

export interface TextBlock {
  type: "text";
  text: string;
}

export interface ImageBlock {
  type: "image";
  source: ImageSource;
  // Herkunft: "viewport" = Kamera-Knopf-Schnappschuss, undefined/"upload"
  // = echtes Referenzbild. Studienrelevant fürs W2-Survey-Gate; wird vom
  // Backend persistiert und erst vor dem API-Send gestrippt.
  origin?: "upload" | "viewport";
  // Optional preamble text the frontend expands into a TextBlock before
  // this image when sending to the model. Lets one staged chip carry both
  // a snapshot and its scene metadata: the user sees a single image chip
  // to manage, and the model receives `[text, image]` as paired blocks.
  // Stripped at submit so the backend / model API never sees this.
  caption?: string;
}

export interface ToolUseBlock {
  type: "tool_use";
  id: string;
  name: string;
  input: Record<string, unknown>;
}

export interface ToolResultBlock {
  type: "tool_result";
  tool_use_id: string;
  content: string | Array<Record<string, unknown>>;
  is_error?: boolean;
}

// One selectable view inside the sketch overlay (Multi-View flow). The
// backend pushes exactly four (perspective/top/front/right) via
// viewport.sketch_ready; the user picks which one to draw on.
export interface SketchView {
  name: string;
  label: string;
  source: ImageSource;
  width: number;
  height: number;
}

export interface SketchBlock {
  type: "sketch";
  svg: string;
  // Composite (chosen view + strokes) rendered client-side via
  // canvas.toDataURL. Only present when the user actually drew (has_strokes).
  // Carries the marked single view (Deixis).
  rendered_png?: ImageSource;
  // Multi-View context grid (Perspektive + Top + Front + Right). Always
  // present in the new flow; the attachment chip shows this overview.
  composite?: ImageSource;
  // True pixel dimensions of `composite` (the grid). Separate from width/height
  // (which for a painted block are the single CELL's dims) so re-opening a
  // painted group doesn't carry cell-sized dims with the grid image.
  composite_width?: number;
  composite_height?: number;
  // Which view was annotated, e.g. "Perspektive". Only set when has_strokes.
  view_name?: string;
  has_strokes?: boolean;
  width: number;
  height: number;
  // Legacy raw backdrop — kept optional for back-compat with older DB rows.
  background?: ImageSource;
  // Frontend-only: gruppiert die Bloecke EINER Mehransichts-Sketch-Sitzung,
  // damit eine gestagte Skizze als Ganzes wieder geoeffnet/ersetzt werden kann.
  // Wird beim Senden vom Backend-Schema (extra="ignore") verworfen.
  sketch_group?: string;
  // Frontend-only: ALLE erfassten Ansichts-Backdrops (Perspektive/Top/Front/
  // Right) der Sitzung — liegt einmal pro Gruppe (auf dem ersten Block) vor,
  // damit eine gestagte Skizze beim Wieder-Oeffnen JEDE Ansicht zum Bemalen
  // anbietet, nicht nur die bereits bemalten. Wird beim Senden gestrippt
  // (frontend/DB-lokal, geht nie ans Modell).
  all_views?: SketchView[];
}

export interface SelectionBlock {
  type: "selection";
  object_ids: string[];
  snapshot?: ImageSource;
  names: string[];
  // Coarse Rhino type labels (e.g. "polysurface", "curve", "subd") —
  // aligned 1:1 with object_ids. Optional for back-compat with older DB
  // rows. Used as a fallback label when `names` is empty or all blank.
  types?: string[];
}

export interface PointPickBlock {
  type: "point_pick";
  point: number[];
  snapshot?: ImageSource;
  object_id?: string | null;
  object_name?: string | null;
  object_type?: string | null;
  snap_type?: string | null;
}

export interface ComponentPickBlock {
  type: "component_pick";
  component_type: "edge" | "face" | "vertex" | "object";
  component_index?: number | null;
  point: number[];
  pick_point?: number[] | null;
  component_info?: Record<string, unknown>;
  snapshot?: ImageSource;
  object_id?: string | null;
  object_name?: string | null;
  object_type_name?: string | null;
  object_class?: string | null;
  geometry_class?: string | null;
  allowed_operations?: string[];
}

export type ContentBlock =
  | TextBlock
  | ImageBlock
  | ToolUseBlock
  | ToolResultBlock
  | SketchBlock
  | SelectionBlock
  | PointPickBlock
  | ComponentPickBlock;

export type ReferenceBlock =
  | SelectionBlock
  | PointPickBlock
  | ComponentPickBlock;

export interface Message {
  id: string;
  session_id: string;
  role: "user" | "assistant" | "system";
  content: ContentBlock[];
  created_at: string;
  model?: string | null;
  stop_reason?: string | null;
  modality?: string[] | null;
}

export interface ParameterAction {
  type:
    | "move_axis"
    | "scale_axis"
    | "scale_uniform"
    | "rotate_axis"
    | "gh_slider"
    | "editable_recipe_value";
  target_object_ids: string[];
  axis: "x" | "y" | "z";
  origin: number[];
  instance_guid?: string | null;
  parameter_key?: string | null;
}

export interface ExposedParameter {
  name: string;
  current: number;
  min: number;
  max: number;
  step?: number | null;
  display_unit: string;
  display_factor: number;
  source: "editable_structure" | "gh_slider" | "agent_exposed";
  source_label?: string | null;
  source_priority: number;
  structure_id?: string | null;
  structure_key?: string | null;
  action?: ParameterAction | null;
  actions?: ParameterAction[];
}

export interface StructureAnchor {
  kind: "object" | "component" | "selection" | "gh_slider";
  label: string;
  object_id?: string | null;
  object_name?: string | null;
  component_type?: "edge" | "face" | "vertex" | "object" | null;
  component_index?: number | null;
  instance_guid?: string | null;
}

export interface EditableStructureContext {
  id: string;
  session_id: string;
  structure_type: "primitive" | "grasshopper";
  structure_key: string;
  title: string;
  summary: string;
  source: "selection" | "component_pick" | "parameters" | "tool_result";
  object_id?: string | null;
  object_name?: string | null;
  object_class?: string | null;
  editable_recipe_type?: string | null;
  editable_operations: string[];
  anchors: StructureAnchor[];
  parameter_names: string[];
  metadata: Record<string, unknown>;
  updated_at: string;
}

export interface Variant {
  id: string;
  session_id: string;
  name: string;
  description: string;
  layer_name: string;
  thumbnail?: ImageSource | null;
  is_active: boolean;
  created_at: string;
}

export interface Session {
  id: string;
  title: string;
  created_at: string;
  use_mode: "normal" | "study";
  participant_id?: string | null;
  rhino_doc_path?: string | null;
  // Study condition (Studienartefakt-Spec §1). Immutable after session
  // creation. Optional in the type so older session rows that predate
  // the field don't break the frontend; useCondition() defaults to
  // "werkzeug" when absent.
  condition?: "basis" | "werkzeug";
}

export interface SessionDebugDump {
  session_id: string;
  generated_at: string;
  text: string;
}

export interface Settings {
  backend_mode: "mcp" | "api";
  use_mode: "normal" | "study";
  api_key: string;
  model: string;
  max_tokens: number;
  variant_mode: "sequential" | "parallel";
  max_variants: number;
  max_iterations: number;
  port: number;
  participant_id: string;
  consent_given: boolean;
  snapshot_interval_seconds: number;
  // OpenAI-compatible endpoint — only used when `model === "local"`. Calls
  // route through litellm against any OpenAI-compatible server: local
  // (LM Studio / Ollama) or cloud (OpenAI / OpenRouter). No proxy required.
  local_base_url: string;
  local_model: string;
  // API key for the OpenAI-compatible endpoint. Empty for LM Studio /
  // Ollama; set for cloud providers (OpenAI / OpenRouter).
  local_api_key: string;
  // Default study condition for newly created sessions in dev mode
  // (Studienartefakt-Spec §1.2). In study mode the pre-session dialog
  // overrides this per session.
  default_condition: "basis" | "werkzeug";
  // Export-bundle target directory (Studienartefakt-Spec §2.6).
  // Empty string = use the default ~/Documents/masterarbeit-studie/exports.
  export_dir: string;
}

// ---------------------------------------------------------------------
// Studienartefakt-Spec §2 — Study-mode shell
// ---------------------------------------------------------------------

export interface StudySession {
  id: string;
  session_id: string;
  participant_code: string;
  condition: "basis" | "werkzeug";
  task_variant: "A" | "B";
  setting: "privat" | "lab" | "remote";
  is_pilot: boolean;
  order_index: 1 | 2;
  status: "active" | "completed" | "aborted";
  created_at: string;
  ended_at?: string | null;
}

export interface StudySessionCreate {
  participant_code: string;
  condition: "basis" | "werkzeug";
  task_variant: "A" | "B";
  setting: "privat" | "lab" | "remote";
  is_pilot: boolean;
  title?: string;
}

export interface StudyParticipantRun {
  study_session_id: string;
  session_id: string;
  condition: "basis" | "werkzeug";
  task_variant: "A" | "B";
  is_pilot: boolean;
  order_index: number;
  status: "active" | "completed" | "aborted";
  created_at: string;
  ended_at?: string | null;
}

export interface StudyParticipantStatus {
  participant_code: string;
  is_pilot: boolean;
  used_conditions: Array<"basis" | "werkzeug">;
  available_conditions: Array<"basis" | "werkzeug">;
  used_task_variants: Array<"A" | "B">;
  available_task_variants: Array<"A" | "B">;
  next_order_index: number;
  has_reusable_consent: boolean;
  active_run_open: boolean;
  has_demographics: boolean;
  runs: StudyParticipantRun[];
}

export interface Consent {
  id: string;
  study_session_id: string;
  text_hash: string;
  confirmed_at: string;
  checkboxes: Record<string, boolean>;
}

export interface ConsentSubmission {
  study_session_id: string;
  text_hash: string;
  checkboxes: Record<string, boolean>;
}

export interface ConsentTextResponse {
  text: string;
  text_hash: string;
  checkbox_labels: string[];
}

export type StudyContextEventType =
  | "consent_given"
  | "pause_marker"
  | "task_phase_change"
  | "agency_block_shown"
  | "condition_toggle_blocked"
  | "model_change_blocked"
  | "counterbalancing_override"
  | "study_session_started"
  | "study_session_ended"
  | "study_session_aborted"
  | "survey_submitted"
  | "demographics_submitted"
  | "final_survey_submitted";

export interface StudyContextEvent {
  id: string;
  study_session_id: string;
  event_type: StudyContextEventType;
  payload: Record<string, unknown>;
  created_at: string;
}

// ----- Agency survey (§2.5) -----

export interface AgencyItem {
  id: string;
  dimension: string;
  label: string;
}

export interface AgencyScale {
  min: number;
  max: number;
  anchor_min: string;
  anchor_max: string;
}

export interface AgencyItemsResponse {
  scale: AgencyScale;
  items: AgencyItem[];
  note: string;
}

// Per-Bedingungs-Block (Lean-Fragebogen, fragebogen-spec §5.2). Die
// Werkzeug-Felder sind optional, weil Items je nach Bedingung/Gating
// ausgeblendet sein können — nicht gestellte Items werden weggelassen.
export interface AgencySurveySubmission {
  self_efficacy?: number | null;
  control?: number | null;
  autonomy?: number | null;
  ownership?: number | null;
  csi_exploration?: number | null;
  csi_expressiveness?: number | null;
  csi_immersion?: number | null;
  csi_enjoyment?: number | null;
  csi_results_worth_effort?: number | null;
  csi_collaboration?: number | null;
  tool_text?: number | null;
  tool_text_unused?: boolean | null;
  tool_image_ref?: number | null;
  tool_image_ref_unused?: boolean | null;
  tool_viewport_feedback?: number | null;
  tool_viewport_feedback_unused?: boolean | null;
  tool_selection_badge?: number | null;
  tool_selection_badge_unused?: boolean | null;
  tool_pick?: number | null;
  tool_pick_unused?: boolean | null;
  tool_slider?: number | null;
  tool_slider_unused?: boolean | null;
  tool_call_cards?: number | null;
  tool_call_cards_unused?: boolean | null;
  tool_lock?: number | null;
  tool_lock_unused?: boolean | null;
  tool_variants?: number | null;
  tool_variants_unused?: boolean | null;
  tool_sketch?: number | null;
  tool_sketch_unused?: boolean | null;
  tool_dialog_offers?: number | null;
  tool_dialog_offers_unused?: boolean | null;
  collab_editor_trap?: number | null;
  collab_expression_limit?: number | null;
  collab_system_competence?: number | null;
  tools_unused_selection?: string[];
  tools_unused_freetext?: string;
  reflection_freetext?: string;
  notes?: string;
  fragebogen_version_hash?: string | null;
}

export interface AgencySurveyRecord extends AgencySurveySubmission {
  id: string;
  study_session_id: string;
  condition: "basis" | "werkzeug";
  submitted_at: string;
}

// ----- Lean-Fragebogen: Survey-Config + Demografie + Vergleichsblock -----

export interface SurveyLikertItem {
  id: string;
  dimension: string;
  label: string;
}

export interface SurveyConfigResponse {
  study_session_id: string;
  condition: "basis" | "werkzeug";
  fragebogen_version_hash: string;
  intro: string;
  scale: AgencyScale;
  csi_items: SurveyLikertItem[];
  agency_items: SurveyLikertItem[];
  collaboration_items: SurveyLikertItem[];
  competence_items: SurveyLikertItem[];
  tool_items: SurveyLikertItem[];
  tool_not_used_label: string;
  unused_question_label: string;
  unused_options: SurveyLikertItem[];
  reflection_label: string;
  reflection_max_chars: number;
  is_final_run: boolean;
  note: string;
}

export interface DemographicsField {
  id: string;
  label: string;
  kind: "select" | "multiselect" | "text" | "scale" | "dropdown";
  options: string[];
  allow_other: boolean;
  placeholder: string;
  scale_min: number | null;
  scale_max: number | null;
  anchor_min: string;
  anchor_max: string;
}

export interface DemographicsConfigResponse {
  fragebogen_version_hash: string;
  intro: string;
  fields: DemographicsField[];
}

export interface DemographicsSubmission {
  age_range?: string | null;
  gender?: string | null;
  field?: string | null;
  design_experience_years?: string | null;
  cad_experience_years?: string | null;
  rhino_self_assessment?: number | null;
  grasshopper_self_assessment?: number | null;
  other_cad_tools?: string[];
  genai_usage_frequency?: string | null;
  genai_design_tools?: string[];
  furniture_design_experience?: string | null;
  fragebogen_version_hash?: string | null;
}

export interface DemographicsRecord extends DemographicsSubmission {
  id: string;
  study_session_id: string;
  participant_code: string;
  is_pilot: boolean;
  submitted_at: string;
}

export interface SurveyOption {
  id: string;
  label: string;
}

export interface FinalSurveyV2Scale {
  min: number;
  max: number;
  anchor_min: string;
  anchor_mid: string;
  anchor_max: string;
}

export interface FinalSurveyOpenQuestion {
  id: string;
  label: string;
  max_chars: number;
}

export interface FinalSurveyConfigResponse {
  study_session_id: string;
  fragebogen_version_hash: string;
  intro: string;
  preference_label: string;
  preference_options: SurveyOption[];
  preference_why_label: string;
  preference_why_max_chars: number;
  v2_label: string;
  v2_scale: FinalSurveyV2Scale;
  v2_dimensions: SurveyOption[];
  ranking_label: string;
  ranking_max_rank: number;
  ranking_options: SurveyLikertItem[];
  hybrid_label: string;
  hybrid_max_chars: number;
  open_questions: FinalSurveyOpenQuestion[];
}

export interface FinalSurveySubmission {
  preference_choice?: "first" | "second" | "depends" | "unknown" | null;
  preference_freetext?: string;
  v2_control?: number | null;
  v2_expressiveness?: number | null;
  v2_exploration?: number | null;
  v2_speed?: number | null;
  v2_trust?: number | null;
  v2_ownership?: number | null;
  tool_importance_rank_1?: string | null;
  tool_importance_rank_2?: string | null;
  tool_importance_rank_3?: string | null;
  hybrid_mode_freetext?: string;
  surprise_freetext?: string;
  missing_freetext?: string;
  wish_freetext?: string;
  fragebogen_version_hash?: string | null;
}

export interface FinalSurveyRecord extends FinalSurveySubmission {
  id: string;
  study_session_id: string;
  submitted_at: string;
}

// ----- Export bundle (§2.6) -----

export interface ExportResponse {
  bundle_path: string;
  bytes: number;
  files_included: string[];
}

export interface WsEvent {
  type: string;
  payload: Record<string, unknown>;
  timestamp: string;
  correlation_id?: string | null;
}

// ----- Gate preview (Part C — selektives Vorschau-Gate) -----

export interface GatePreviewPayload {
  session_id?: string;
  tu_id: string;
  tool_name: string;
  summary: string;
  object_ids: string[];
}

export interface GateClearedPayload {
  session_id?: string;
  tu_id: string;
}

export interface RunCancelledPayload {
  session_id: string;
}

export interface WsCommand {
  type:
    | "chat.send"
    | "chat.cancel"
    | "gate.resolve"
    | "viewport.request_snapshot"
    | "viewport.request_pick"
    | "viewport.request_point"
    | "viewport.request_component"
    | "viewport.request_sketch"
    // WS-only: grow/restore the floating plugin window around the active
    // Rhino viewport while the sketch overlay is open. payload: { state }.
    | "viewport.sketch_window"
    | "parameter.changed"
    | "parameter.clear"
    | "variant.select"
    | "variant.show_original"
    | "variant.delete"
    | "variants.clear"
    // WS-only: designer closed the gallery with a variant selected → keep it as
    // the main model (promote to Active) instead of discarding all.
    | "variant.commit"
    | "session.create"
    | "session.switch"
    | "settings.update"
    | "study.log_event"
    // Viewport hover-highlight for inline reference tokens (Feature A).
    // highlight_reference: Rhino highlights the referenced geometry.
    // highlight_clear: Rhino clears the highlight.
    | "viewport.highlight_reference"
    | "viewport.highlight_clear"
    // Persistent small orange dots at picked point-references (reference view
    // while composing). payload: { points: number[][] }; empty list clears.
    | "viewport.point_markers"
    // Persistent THIN blue outline of staged K/F/O component references (stay
    // marked while their tokens are in the composer). payload: { blocks: [...] };
    // empty list clears.
    | "viewport.persistent_refs";
  payload: Record<string, unknown>;
  correlation_id?: string;
}
