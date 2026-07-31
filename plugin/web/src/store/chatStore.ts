import { create } from "zustand";
import type {
  Consent,
  ContentBlock,
  EditableStructureContext,
  ExposedParameter,
  GatePreviewPayload,
  ImageSource,
  Message,
  ReferenceBlock,
  SelectionBlock,
  Session,
  Settings,
  SketchView,
  StudySession,
  Variant,
} from "@/lib/types";

export interface SketchOverlayState {
  views: SketchView[];
  composite: { source: ImageSource; width: number; height: number };
  // Wieder-Oeffnen einer bestehenden (gesendeten) Skizze aus dem Chat: die
  // gespeicherten SVG-Striche EINER Ansicht, die der Editor als EDITIERBARE
  // Striche zuruecklaedt. Undefiniert bei frischer Aufnahme.
  initialSvg?: string;
  // Wieder-Oeffnen einer gestagten Mehransichts-Skizze: SVG-Striche je Ansicht
  // (view name -> svg). Der Reset-Effekt seedet daraus ALLE Ansichten.
  initialStrokesByView?: Record<string, string>;
  // Gesetzt beim Reopen einer gestagten Gruppe: "Fertig" ersetzt dann die
  // urspruenglichen gestagten Bloecke dieser Gruppe statt zu duplizieren.
  reopenGroupId?: string;
}

interface ChatState {
  connected: boolean;
  activeSessionId: string | null;
  sessions: Session[];
  messages: Message[];
  settings: Settings | null;
  pendingSend: boolean;
  // Content blocks staged from the viewport (snapshot/pick/sketch) —
  // sent alongside the next user message and then cleared.
  stagedAttachments: ContentBlock[];
  // Viewport request currently awaiting a response (for UI disabled state).
  viewportPending:
      | null
      | "snapshot"
      | "pick"
      | "point"
      | "component"
      // Parametrisieren-Frischpick (force_object Hover-Picker): ganze Objekte,
      // Shift = mehrere, Strg ist hier no-op -> eigener Banner-Zustand statt "pick"
      // (dessen "vorher markieren"-Text gilt nur fuer die rs.GetObjects-Route).
      | "object"
      | "sketch";
  // Live-Modifier-Status waehrend eines Hover-Picks, vom Picker per WS gepusht
  // ({shift, object}). Treibt die Live-Modus-Zeile im Pick-Banner; null = kein
  // Modifier aktiv / kein Pick. Wird bei Pick-Ende (viewportPending->null) +
  // Session-Wechsel geleert.
  pickModifiers: { shift: boolean; object: boolean } | null;
  // Last viewport error to surface in the UI.
  viewportError: string | null;
  // When non-null, the sketch overlay is open with this backdrop. The
  // backend pushes it via viewport.sketch_ready; the overlay closes by
  // setting it back to null (either after "Fertig" stages the SketchBlock
  // or after "Abbrechen" / reconnect drops the flow).
  sketchOverlay: SketchOverlayState | null;
  activeStructureContext: EditableStructureContext | null;
  exposedParameters: ExposedParameter[];
  // object_ids, auf denen die aktive GH-Parametrisierung basiert (die beim
  // Parametrisieren gepickten Ziele, beim Senden erfasst). Der Verwerfen/Abort
  // stellt genau diese wieder her.
  ghSourceObjectIds: string[];
  pendingParameterChanges: Set<string>;
  // Monotonic counter: bumped on each chat send so the parameter panel
  // collapses itself (the slider strip is tall and crowds the chat once the
  // user has moved on to a new instruction). The panel stays mounted and the
  // backend context is untouched — the user can re-expand via the chevron.
  collapseParametersSignal: number;
  // Monotonic counter: bumped by setExposedParameters when the new list
  // contains parameter names that were not present before (i.e. the agent
  // just exposed genuinely new sliders). The panel reacts by expanding so
  // the user notices the new controls without having to find them manually.
  // Pure current-value sync updates (same names, different values) do NOT
  // bump this counter to avoid re-opening a panel the user deliberately
  // collapsed.
  expandParametersSignal: number;
  // True once the user explicitly collapses (chevron) or dismisses (×) the
  // parameter panel. While set, selection-driven structure exposures must NOT
  // auto-reopen the panel — only a deliberate agent expose (source
  // "agent_exposed" or "gh_slider") overrides it. Lives in the store (not panel
  // state) so the suppression survives the panel rendering null while empty
  // (the panel stays mounted within a session, it does not unmount on ×).
  // Reset per session in setActiveSession so a dismiss in one session does not
  // leak into another.
  parametersUserDismissed: boolean;
  // Structure-context ids (``structure:{session}:{object_id}``) whose parameter
  // panel the user closed with × . The panel stays fully hidden for these
  // objects — re-picking the same object does NOT bring it back; a Rhino undo
  // (viewport.undo_redo → clearDismissedStructures) does. Per object via the
  // GUID-keyed structure id; session-local (reset in setActiveSession).
  dismissedStructureIds: string[];
  // Dialog-card tool_use ids the user dismissed with the × on a QuickReplyCard.
  // The card then collapses to a muted "verworfen" trace instead of staying an
  // active prompt. UI affordance only — dialog tools are non-blocking (the
  // awaiting_user tool_result is already recorded), so dismissing never hangs the
  // run. Session-local (reset in setActiveSession); not reload-durable.
  dismissedDialogIds: string[];
  // Transient notice shown after a successful or no-op bake. Lives outside
  // the ParameterPanel (which unmounts on success) so the banner can survive
  // the unmount. Auto-dismissed after ~6 s by InputBar.
  bakeNotice: string | null;
  variants: Variant[];
  activeVariantId: string | null;
  // Study-mode shell (Studienartefakt-Spec §2). All null when not in
  // study mode or no study run is active.
  activeStudySession: StudySession | null;
  activeConsent: Consent | null;
  preSessionDialogOpen: boolean;
  consentDialogOpen: boolean;
  agencySurveyOpen: boolean;
  // Lean-Fragebogen: Demografie-Block (einmal pro Teilnehmenden-Lane,
  // nach Consent des ersten Laufs) + Vergleichsblock (nach dem
  // Per-Bedingungs-Survey des zweiten Laufs).
  demographicsDialogOpen: boolean;
  finalSurveyOpen: boolean;
  // Live Rhino selection count pushed via viewport.selection_changed WS event.
  // Used by InputBar to show a badge on the K/F/O button before the click.
  rhinoSelectionCount: number;
  // One-shot callback set by the GH-button (and cleared after a single
  // pick_result). When set, the pick_result is routed to the callback
  // instead of staging it as a regular attachment.
  pickCallback: ((block: SelectionBlock | null) => void) | null;
  // Mounted InputBar composer callback. Normal reference picks are routed
  // here first so the block can become an inline token at the saved caret.
  referenceTokenCallback: ((block: ReferenceBlock) => boolean) | null;
  // Mounted InputBar composer callback for Parametrisieren targets. When the
  // backend tags a component pick with nest_target="command", the block is
  // routed here so it nests LIVE into the Parametrisieren command-chip
  // (creating the chip on the first pick, appending on each further pick)
  // instead of becoming a standalone reference token.
  commandTargetCallback: ((block: ReferenceBlock) => boolean) | null;
  // Part C — Gate preview: backend holds a destructive operation and pushes
  // a preview; the designer confirms/reverts. MVP: single gate (backend
  // gates serially), so one entry is sufficient.
  pendingGate: GatePreviewPayload | null;
  // True while a chat run is active (between chat.send and
  // message.complete / message.error / run.cancelled). Used to show the
  // Stop button in the InputBar.
  runActive: boolean;

  setConnected: (v: boolean) => void;
  setActiveSession: (id: string | null) => void;
  setSessions: (sessions: Session[]) => void;
  upsertSession: (session: Session) => void;
  setMessages: (messages: Message[]) => void;
  appendMessage: (message: Message) => void;
  setSettings: (settings: Settings) => void;
  setPendingSend: (pending: boolean) => void;

  addStagedAttachment: (block: ContentBlock) => void;
  removeStagedAttachment: (index: number) => void;
  clearStagedAttachments: () => void;
  removeStagedAttachmentsByGroup: (groupId: string) => void;
  setViewportPending: (kind: ChatState["viewportPending"]) => void;
  setPickModifiers: (m: { shift: boolean; object: boolean } | null) => void;
  setViewportError: (msg: string | null) => void;
  setSketchOverlay: (state: SketchOverlayState | null) => void;
  setActiveStructureContext: (context: EditableStructureContext | null) => void;
  setExposedParameters: (parameters: ExposedParameter[]) => void;
  applyLocalParameterChange: (name: string, newValue: number) => void;
  markParameterPending: (name: string, pending: boolean) => void;
  clearExposedParameters: () => void;
  setGhSourceObjectIds: (ids: string[]) => void;
  requestCollapseParameters: () => void;
  setParametersUserDismissed: (dismissed: boolean) => void;
  dismissStructure: (structureId: string) => void;
  clearDismissedStructures: () => void;
  dismissDialog: (id: string) => void;
  setBakeNotice: (notice: string | null) => void;
  setVariants: (variants: Variant[]) => void;
  upsertVariant: (variant: Variant) => void;
  removeVariant: (variantId: string) => void;
  setActiveVariant: (variantId: string | null) => void;
  clearVariants: () => void;
  setActiveStudySession: (study: StudySession | null) => void;
  setActiveConsent: (consent: Consent | null) => void;
  setPreSessionDialogOpen: (open: boolean) => void;
  setConsentDialogOpen: (open: boolean) => void;
  setAgencySurveyOpen: (open: boolean) => void;
  setDemographicsDialogOpen: (open: boolean) => void;
  setFinalSurveyOpen: (open: boolean) => void;
  setRhinoSelectionCount: (n: number) => void;
  setPickCallback: (cb: ((block: SelectionBlock | null) => void) | null) => void;
  setReferenceTokenCallback: (
    cb: ((block: ReferenceBlock) => boolean) | null,
  ) => void;
  setCommandTargetCallback: (
    cb: ((block: ReferenceBlock) => boolean) | null,
  ) => void;
  setPendingGate: (gate: GatePreviewPayload | null) => void;
  clearPendingGate: (tu_id: string) => void;
  setRunActive: (active: boolean) => void;
}

export const useChatStore = create<ChatState>((set) => ({
  connected: false,
  activeSessionId: null,
  sessions: [],
  messages: [],
  settings: null,
  pendingSend: false,
  stagedAttachments: [],
  viewportPending: null,
  pickModifiers: null,
  viewportError: null,
  sketchOverlay: null,
  activeStructureContext: null,
  exposedParameters: [],
  ghSourceObjectIds: [],
  pendingParameterChanges: new Set(),
  collapseParametersSignal: 0,
  expandParametersSignal: 0,
  parametersUserDismissed: false,
  dismissedStructureIds: [],
  dismissedDialogIds: [],
  bakeNotice: null,
  variants: [],
  activeVariantId: null,
  activeStudySession: null,
  activeConsent: null,
  preSessionDialogOpen: false,
  consentDialogOpen: false,
  agencySurveyOpen: false,
  demographicsDialogOpen: false,
  finalSurveyOpen: false,
  rhinoSelectionCount: 0,
  pickCallback: null,
  referenceTokenCallback: null,
  commandTargetCallback: null,
  pendingGate: null,
  runActive: false,

  setConnected: (connected) => set({ connected }),
  setActiveSession: (id) =>
    set({
      activeSessionId: id,
      messages: [],
      stagedAttachments: [],
      viewportPending: null,
      pickModifiers: null,
      viewportError: null,
      sketchOverlay: null,
      activeStructureContext: null,
      exposedParameters: [],
      ghSourceObjectIds: [],
      pendingParameterChanges: new Set(),
      // The dismiss latch + per-object × -hides are per-session intent: neither
      // a collapse nor a closed object-panel from one session must carry into
      // another. Reset both on switch.
      parametersUserDismissed: false,
      dismissedStructureIds: [],
      dismissedDialogIds: [],
      bakeNotice: null,
      variants: [],
      activeVariantId: null,
      // Study-session metadata is repopulated on session switch by the
      // App.tsx loader; reset to null here so the consent gate doesn't
      // briefly use stale data.
      activeStudySession: null,
      activeConsent: null,
      // pickCallback IS dropped here: it's a one-shot closure registered
      // per-pick (e.g. the GH flow) that captures the previous session — a
      // stale in-flight pick must not resolve against the new session.
      pickCallback: null,
      // referenceTokenCallback is NOT reset here. It is a persistent UI
      // wiring owned by InputBar's MOUNT lifecycle (registered once in a
      // useEffect keyed only on [setter, showWerkzeugSlots]); InputBar does
      // not remount on session switch, so it never re-registers. Clobbering
      // it on every setActiveSession (incl. the boot loader) left it null
      // forever → every pick fell through to addStagedAttachment and chips
      // landed ABOVE the field instead of inline. Condition changes are
      // handled by InputBar's own effect (showWerkzeugSlots dep). Do NOT
      // re-add it to this reset list.
      pendingGate: null,
      runActive: false,
    }),
  setSessions: (sessions) => set({ sessions }),
  upsertSession: (session) =>
    set((state) => {
      const idx = state.sessions.findIndex((s) => s.id === session.id);
      if (idx === -1) return { sessions: [session, ...state.sessions] };
      const next = [...state.sessions];
      next[idx] = session;
      return { sessions: next };
    }),
  setMessages: (messages) => set({ messages }),
  appendMessage: (message) =>
    set((state) => {
      if (state.messages.some((m) => m.id === message.id)) return {};
      if (message.session_id !== state.activeSessionId) return {};
      return { messages: [...state.messages, message] };
    }),
  setSettings: (settings) => set({ settings }),
  setPendingSend: (pendingSend) => set({ pendingSend }),

  addStagedAttachment: (block) =>
    set((state) => ({ stagedAttachments: [...state.stagedAttachments, block] })),
  removeStagedAttachment: (index) =>
    set((state) => ({
      stagedAttachments: state.stagedAttachments.filter((_, i) => i !== index),
    })),
  clearStagedAttachments: () => set({ stagedAttachments: [] }),
  removeStagedAttachmentsByGroup: (groupId) =>
    set((state) => ({
      stagedAttachments: state.stagedAttachments.filter(
        (b) => !(b.type === "sketch" && b.sketch_group === groupId),
      ),
    })),
  setViewportPending: (viewportPending) =>
    // Pick zu Ende (null) -> auch den Live-Modifier-Status leeren, damit keine
    // Modus-Zeile aus einem vergangenen Pick haengen bleibt.
    set(
      viewportPending === null
        ? { viewportPending, pickModifiers: null }
        : { viewportPending },
    ),
  setPickModifiers: (pickModifiers) => set({ pickModifiers }),
  setViewportError: (viewportError) => set({ viewportError }),
  setSketchOverlay: (sketchOverlay) => set({ sketchOverlay }),
  setActiveStructureContext: (activeStructureContext) =>
    set({ activeStructureContext }),
  setExposedParameters: (parameters) =>
    set((state) => {
      const existingNames = new Set(state.exposedParameters.map((p) => p.name));
      const hasNewNames = parameters.some((p) => !existingNames.has(p.name));
      // Auto-Aufklappen NUR bei einem BEWUSSTEN agent-expose (der Assistent will
      // Regler aktiv zeigen) oder einer GH-Parametrisierung. Ein passiver Pick auf
      // eine editierbare Struktur ("editable_structure") klappt das Panel NICHT
      // mehr auf — es erscheint nur als schmaler, eingeklappter Header, den der
      // Nutzer bei Bedarf oeffnet. Vorher poppte die Slider-Leiste bei jedem
      // (Neu-)Pick auf, besonders nach Clear-on-Destructive + naechstem Pick
      // (Nutzerfeedback 01.07.2026: "erscheint random und klappt sich aus").
      // NOTE: der Backend rewrite'd einen gh_slider-expose von "agent_exposed" auf
      // "gh_slider" (schemas.py _normalise_actions), daher zaehlen beide als
      // bewusst; nur "editable_structure" (passiver Pick) bleibt eingeklappt.
      const hasDeliberateExpose = parameters.some(
        (p) => p.source === "agent_exposed" || p.source === "gh_slider",
      );
      const shouldExpand = hasNewNames && hasDeliberateExpose;
      // A deliberate expose for the active structure also lifts a per-object ×
      // hide (dismissedStructureIds), so the designer who asks the agent to
      // surface controls for an object they previously closed sees them again.
      // A passive editable_structure re-pick does NOT lift it (stays hidden
      // until a Rhino undo), matching "× hides this object until undo".
      const activeId = state.activeStructureContext?.id;
      const liftDismiss =
        hasDeliberateExpose &&
        !!activeId &&
        state.dismissedStructureIds.includes(activeId);
      return {
        exposedParameters: parameters,
        pendingParameterChanges: new Set(),
        ...(liftDismiss
          ? {
              dismissedStructureIds: state.dismissedStructureIds.filter(
                (id) => id !== activeId,
              ),
            }
          : {}),
        ...(shouldExpand
          ? {
              expandParametersSignal: state.expandParametersSignal + 1,
              // Auto-expand clears the dismiss latch: from here on the panel
              // behaves normally again.
              parametersUserDismissed: false,
            }
          : {}),
      };
    }),
  applyLocalParameterChange: (name, newValue) =>
    set((state) => ({
      exposedParameters: state.exposedParameters.map((p) =>
        p.name === name ? { ...p, current: newValue } : p,
      ),
    })),
  markParameterPending: (name, pending) =>
    set((state) => {
      const next = new Set(state.pendingParameterChanges);
      if (pending) next.add(name);
      else next.delete(name);
      return { pendingParameterChanges: next };
    }),
  clearExposedParameters: () =>
    set({
      exposedParameters: [],
      pendingParameterChanges: new Set(),
      // Die GH-Quell-Objekte gehoeren zur jetzt beendeten Parametrisierung
      // (Bake/×/Abort loesen parameter.cleared aus) -> mit aufraeumen.
      ghSourceObjectIds: [],
    }),
  setGhSourceObjectIds: (ghSourceObjectIds) => set({ ghSourceObjectIds }),
  requestCollapseParameters: () =>
    set((state) => ({
      collapseParametersSignal: state.collapseParametersSignal + 1,
    })),
  setParametersUserDismissed: (parametersUserDismissed) =>
    set({ parametersUserDismissed }),
  dismissStructure: (structureId) =>
    set((state) =>
      !structureId || state.dismissedStructureIds.includes(structureId)
        ? {}
        : {
            dismissedStructureIds: [
              ...state.dismissedStructureIds,
              structureId,
            ],
          },
    ),
  dismissDialog: (id) =>
    set((state) =>
      !id || state.dismissedDialogIds.includes(id)
        ? {}
        : { dismissedDialogIds: [...state.dismissedDialogIds, id] },
    ),
  clearDismissedStructures: () =>
    set((state) =>
      state.dismissedStructureIds.length === 0
        ? {}
        : { dismissedStructureIds: [] },
    ),
  setBakeNotice: (bakeNotice) => set({ bakeNotice }),
  setVariants: (variants) =>
    set({
      variants,
      activeVariantId: variants.find((v) => v.is_active)?.id ?? null,
    }),
  upsertVariant: (variant) =>
    set((state) => {
      const idx = state.variants.findIndex((v) => v.id === variant.id);
      const next =
        idx === -1
          ? [...state.variants, variant]
          : state.variants.map((v, i) => (i === idx ? variant : v));
      const normalized = variant.is_active
        ? next.map((v) => (v.id === variant.id ? v : { ...v, is_active: false }))
        : next;
      return {
        variants: normalized,
        activeVariantId: variant.is_active ? variant.id : state.activeVariantId,
      };
    }),
  removeVariant: (variantId) =>
    set((state) => ({
      variants: state.variants.filter((v) => v.id !== variantId),
      activeVariantId:
        state.activeVariantId === variantId ? null : state.activeVariantId,
    })),
  setActiveVariant: (variantId) =>
    set((state) => ({
      activeVariantId: variantId,
      variants: state.variants.map((v) => ({
        ...v,
        is_active: v.id === variantId,
      })),
    })),
  clearVariants: () => set({ variants: [], activeVariantId: null }),
  setActiveStudySession: (activeStudySession) => set({ activeStudySession }),
  setActiveConsent: (activeConsent) => set({ activeConsent }),
  setPreSessionDialogOpen: (preSessionDialogOpen) =>
    set({ preSessionDialogOpen }),
  setConsentDialogOpen: (consentDialogOpen) => set({ consentDialogOpen }),
  setAgencySurveyOpen: (agencySurveyOpen) => set({ agencySurveyOpen }),
  setDemographicsDialogOpen: (demographicsDialogOpen) =>
    set({ demographicsDialogOpen }),
  setFinalSurveyOpen: (finalSurveyOpen) => set({ finalSurveyOpen }),
  setRhinoSelectionCount: (rhinoSelectionCount) => set({ rhinoSelectionCount }),
  setPickCallback: (pickCallback) => set({ pickCallback }),
  setReferenceTokenCallback: (referenceTokenCallback) =>
    set({ referenceTokenCallback }),
  setCommandTargetCallback: (commandTargetCallback) =>
    set({ commandTargetCallback }),
  setPendingGate: (pendingGate) => set({ pendingGate }),
  clearPendingGate: (tu_id) =>
    set((state) =>
      state.pendingGate?.tu_id === tu_id ? { pendingGate: null } : {},
    ),
  setRunActive: (runActive) => set({ runActive }),
}));

// Dev-only: Store für Debugging/visuelle Prüfungen in der Browser-Konsole
// erreichbar machen (im Produktions-Build wegoptimiert).
if (import.meta.env.DEV) {
  (window as unknown as Record<string, unknown>).__chatStore = useChatStore;
}
