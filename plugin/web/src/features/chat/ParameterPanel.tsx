import {
  memo,
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { Box, ChevronDown, RotateCcw, SlidersHorizontal, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { useChatStore } from "@/store/chatStore";
import { cn } from "@/lib/utils";
import type {
  EditableStructureContext,
  ExposedParameter,
  WsCommand,
} from "@/lib/types";

interface ParameterPanelProps {
  onSend: (cmd: WsCommand) => boolean;
}

/**
 * Slider strip the LLM exposes via the ``expose_parameters`` tool.
 *
 * Direct-manipulation path for FF2: the designer drags a slider and the
 * backend's parameter.changed handler applies the bound transformation
 * (Rhino move/scale/rotate or a Grasshopper Number Slider) without
 * another chat round-trip. State stays in sync via parameter.exposed /
 * parameter.updated WS events and a REST refresh on reconnect.
 *
 * Renders nothing when no parameters and no active editable structure are
 * available so the surrounding layout doesn't shift while the panel sleeps.
 */
export function ParameterPanel({ onSend }: ParameterPanelProps) {
  const parameters = useChatStore((s) => s.exposedParameters);
  const structureContext = useChatStore((s) => s.activeStructureContext);
  const pendingChanges = useChatStore((s) => s.pendingParameterChanges);
  const activeSessionId = useChatStore((s) => s.activeSessionId);
  const connected = useChatStore((s) => s.connected);
  const applyLocalParameterChange = useChatStore(
    (s) => s.applyLocalParameterChange,
  );
  const markParameterPending = useChatStore((s) => s.markParameterPending);
  const setViewportError = useChatStore((s) => s.setViewportError);
  const setBakeNotice = useChatStore((s) => s.setBakeNotice);
  // Die beim Parametrisieren gepickten Quell-Objekte — der Verwerfen/Abort holt
  // genau diese zurueck.
  const ghSourceObjectIds = useChatStore((s) => s.ghSourceObjectIds);
  const collapseSignal = useChatStore((s) => s.collapseParametersSignal);
  const expandSignal = useChatStore((s) => s.expandParametersSignal);
  const setParametersUserDismissed = useChatStore(
    (s) => s.setParametersUserDismissed,
  );
  const dismissedStructureIds = useChatStore((s) => s.dismissedStructureIds);
  const dismissStructure = useChatStore((s) => s.dismissStructure);
  // Local UI state only: collapsing this panel must not clear the backend
  // structure context, because the agent still uses it for routing. `collapsed`
  // starts COLLAPSED (Nutzerfeedback 01.07.2026): ein passiver Pick soll die
  // Slider-Leiste nicht aufpoppen lassen — sie erscheint als schmaler Header und
  // wird bei Bedarf per Chevron geoeffnet. Nur ein BEWUSSTER agent-expose klappt
  // sie via expandSignal auf (s. store.setExposedParameters). Cross-session reset
  // via key={activeSessionId} remount in App.tsx. Two dismiss tiers: the chevron
  // *folds*, while × *hides this object's panel entirely* via the store
  // dismissedStructureIds set (below) — that one only comes back on a Rhino undo.
  const [collapsed, setCollapsed] = useState(true);
  const [baking, setBaking] = useState(false);
  const [aborting, setAborting] = useState(false);
  const showStructureContext = structureContext !== null;
  // × hid this object's panel (per-object, until a Rhino undo). Render nothing
  // for it regardless of params/structure — re-picking the SAME object stays
  // hidden; a different object is unaffected; a Rhino undo clears the set.
  const isStructureDismissed =
    structureContext !== null &&
    dismissedStructureIds.includes(structureContext.id);
  const hasPanelContent = parameters.length > 0 || showStructureContext;
  const hasGhBackedParameter = parameters.some(
    (p) =>
      p.actions?.some((a) => a.type === "gh_slider") ||
      p.action?.type === "gh_slider",
  );
  // When every slider shares one source (e.g. all "Struktur" because they
  // come from the active structure context above), repeating that label
  // per row is just noise — the panel header + structure summary already
  // communicate it. Only surface the badge when sources actually differ.
  const showSourceBadge =
    parameters.length > 0 &&
    !parameters.every((p) => p.source === parameters[0].source);

  // React to the collapse/expand SIGNALS only when they actually CHANGE —
  // never on (re)mount. On a fresh mount the refs start at the current signal
  // values, so neither effect fires and the initial `collapsed` (from the
  // persisted dismiss latch) holds. This is what keeps a dismissed panel from
  // popping open again when it remounts on the next selection.
  //
  // collapse-on-send (collapseSignal): the slider strip is tall; once the user
  // sends a new instruction it shouldn't keep crowding the chat. Transient —
  // does NOT latch the dismiss flag, so a later selection may re-expand.
  const lastCollapseSignal = useRef(collapseSignal);
  useEffect(() => {
    if (collapseSignal !== lastCollapseSignal.current) {
      lastCollapseSignal.current = collapseSignal;
      setCollapsed(true);
    }
  }, [collapseSignal]);

  // Auto-expand when genuinely new parameters are exposed (the store already
  // suppresses this signal for selection-driven exposures the user dismissed —
  // see setExposedParameters), so the designer notices new controls.
  const lastExpandSignal = useRef(expandSignal);
  useEffect(() => {
    if (expandSignal !== lastExpandSignal.current) {
      lastExpandSignal.current = expandSignal;
      setCollapsed(false);
    }
  }, [expandSignal]);

  const closePanel = useCallback(() => {
    // × = hide THIS object's panel until a Rhino undo. Keyed by the active
    // structure id, so re-picking the SAME object keeps it hidden while a
    // different object still shows its own panel. We deliberately do NOT clear
    // the backend params/structure here: the agent still uses them for routing,
    // and a Rhino undo (viewport.undo_redo → clearDismissedStructures) then
    // brings the panel straight back without the designer having to re-pick.
    //
    // Only PER-OBJECT structures (primitive/editable, GUID-keyed id
    // "structure:{session}:{guid}") get this hard hide. Grasshopper structures
    // share ONE session-global id ("structure:{session}:gh"), so hiding by id
    // would wrongly suppress every later (different) GH panel and block
    // deliberate agent re-exposes — GH therefore takes the lighter
    // collapse+latch+clear fallback below.
    if (structureContext && structureContext.structure_type !== "grasshopper") {
      dismissStructure(structureContext.id);
      return;
    }
    // Fallback (GH structures, or agent-exposed params with no structure
    // context to key a hide on): collapse + latch + clear the sliders.
    setCollapsed(true);
    setParametersUserDismissed(true);
    if (!activeSessionId || parameters.length === 0) return;
    onSend({
      type: "parameter.clear",
      payload: { session_id: activeSessionId },
    });
  }, [
    activeSessionId,
    dismissStructure,
    onSend,
    parameters.length,
    setParametersUserDismissed,
    structureContext,
  ]);

  // HITL commit: bake the live GH geometry into real Rhino objects. On
  // success the backend clears the exposed parameters, so the panel
  // unmounts on its own via the parameter.cleared WS event. The success
  // notice lives in the store (bakeNotice) so InputBar can render it even
  // after this panel has unmounted.
  const onBake = useCallback(async () => {
    if (!activeSessionId || baking) return;
    setBaking(true);
    setViewportError(null);
    try {
      const resp = await api.bakeGrasshopper(activeSessionId);
      if (resp.status === "ok") {
        // Derive count: prefer explicit baked_count field, fall back to
        // regex over rhino_response for older backend versions.
        let count: number | null = resp.baked_count ?? null;
        if (count === null && resp.rhino_response) {
          const m = /baked\s+(\d+)/i.exec(resp.rhino_response);
          if (m) count = parseInt(m[1], 10);
        }
        const label =
          count !== null
            ? `${count} Objekt${count === 1 ? "" : "e"} gebacken — Geometrie liegt jetzt auf dem Active-Layer`
            : "Geometrie gebacken — liegt jetzt auf dem Active-Layer";
        setBakeNotice(label);
      } else if (resp.status === "nothing_baked") {
        setBakeNotice("Nichts zu backen");
      } else {
        setViewportError(
          `Backen: ${resp.rhino_response ?? "es wurde keine Geometrie gebacken"}`,
        );
      }
    } catch (err) {
      setViewportError(
        `Backen fehlgeschlagen: ${err instanceof Error ? err.message : String(err)}`,
      );
    } finally {
      setBaking(false);
    }
  }, [activeSessionId, baking, setBakeNotice, setViewportError]);

  // HITL-Gegenstueck zum Bake: die aktive GH-Parametrisierung verwerfen. Der
  // Backend-Endpoint baut die GH-Chain ab und holt die beim Parametrisieren
  // gepickten Original-Objekte zurueck. Das Panel verschwindet danach von selbst
  // ueber das parameter.cleared-Event (gleicher Pfad wie nach Bake / beim ×).
  const onAbort = useCallback(async () => {
    if (!activeSessionId || aborting) return;
    setAborting(true);
    setViewportError(null);
    try {
      const resp = await api.abortGrasshopper(
        activeSessionId,
        ghSourceObjectIds,
      );
      if (resp.status === "ok") {
        setBakeNotice(
          ghSourceObjectIds.length > 0
            ? "Parametrisierung verworfen — Original wiederhergestellt"
            : "Parametrisierung verworfen — GH-Geometrie entfernt",
        );
      } else {
        setViewportError(
          `Verwerfen: ${resp.restored ?? "Abbruch fehlgeschlagen"}`,
        );
      }
    } catch (err) {
      setViewportError(
        `Verwerfen fehlgeschlagen: ${err instanceof Error ? err.message : String(err)}`,
      );
    } finally {
      setAborting(false);
    }
  }, [
    activeSessionId,
    aborting,
    ghSourceObjectIds,
    setBakeNotice,
    setViewportError,
  ]);

  // Stabile Referenz fuer die memoisierten Slider-Zeilen: ein Inline-Handler
  // wuerde bei jedem Panel-Render (z. B. pro Pending-Tick waehrend des Drags)
  // eine neue Identitaet bekommen und die Memoisierung aushebeln.
  const handleSliderChange = useCallback(
    (name: string, value: number) => {
      // Drop the change while offline: onSend would fail and no
      // parameter.updated would ever clear the pending badge (it would
      // stick until the reconnect refresh).
      if (!activeSessionId || !connected) return;
      applyLocalParameterChange(name, value);
      markParameterPending(name, true);
      onSend({
        type: "parameter.changed",
        payload: { session_id: activeSessionId, name, value },
      });
    },
    [
      activeSessionId,
      applyLocalParameterChange,
      connected,
      markParameterPending,
      onSend,
    ],
  );

  if (!hasPanelContent || isStructureDismissed) return null;

  return (
    <div className="px-3 py-1.5">
      <div className="mx-auto flex w-full max-w-3xl flex-col rounded-2xl bg-card p-3 shadow-panel">
        <div className="flex items-center justify-between">
          <span className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            <SlidersHorizontal className="h-3.5 w-3.5 text-primary" />
            <span>
              {parameters.length > 0
                ? `Parameter (${parameters.length})`
                : "Parameter"}
            </span>
            {/* Show the structure title here only while the panel is
                collapsed. When expanded, the StructureContextSummary
                box below owns the title, so keep the header minimal to
                avoid the duplication the user flagged. */}
            {collapsed && showStructureContext && structureContext ? (
              <span className="min-w-0 truncate font-medium normal-case tracking-normal text-muted-foreground">
                {structureContext.title}
              </span>
            ) : null}
          </span>
          <div className="flex items-center gap-0.5">
            <Button
              variant="ghost"
              size="icon"
              className="h-7 w-7 rounded-full"
              onClick={() => {
                // Manual collapse latches the dismiss flag (stays closed on
                // re-selection); manual expand clears it (normal behaviour).
                const next = !collapsed;
                setCollapsed(next);
                setParametersUserDismissed(next);
              }}
              aria-label={
                collapsed ? "Slider-Panel ausklappen" : "Slider-Panel einklappen"
              }
              title={collapsed ? "Ausklappen" : "Einklappen"}
            >
              {/* Panel sits at the bottom of the chat area: content
                  folds downward, so the chevron points down to collapse
                  (expanded state) and up to expand (collapsed state).
                  This is the mirror image of VariantGallery, which is
                  anchored at the top. */}
              <ChevronDown
                className={cn(
                  "h-3.5 w-3.5 transition-transform",
                  collapsed && "rotate-180",
                )}
              />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className="h-7 w-7 rounded-full"
              onClick={closePanel}
              aria-label="Parameter-Panel ausblenden"
              title="Panel ausblenden"
            >
              <X className="h-3.5 w-3.5" />
            </Button>
          </div>
        </div>
        {/* Accordion statt Unmount: der Inhalt bleibt gemountet (Slider-
            Drafts/Timer ueberleben das Einklappen), kollabiert aber weich
            ueber grid-template-rows. Der Innenabstand (pt-2) kollabiert mit. */}
        <div
          className={cn(
            "grid transition-[grid-template-rows] duration-200 ease-out motion-reduce:transition-none",
            collapsed ? "[grid-template-rows:0fr]" : "[grid-template-rows:1fr]",
          )}
        >
          <div
            className="min-h-0 overflow-hidden"
            // inert im kollabierten Zustand: der Inhalt bleibt gemountet, darf
            // aber keine (unsichtbaren) Tab-Stops/Klickziele bieten.
            ref={(el) => {
              if (!el) return;
              if (collapsed) el.setAttribute("inert", "");
              else el.removeAttribute("inert");
            }}
          >
            <div className="flex flex-col gap-2 pt-2">
            {(() => {
              const sliderList =
                parameters.length > 0 ? (
                  <div className="flex flex-col gap-2">
                    {parameters.map((p) => (
                      <MemoParameterSlider
                        key={p.name}
                        parameter={p}
                        pending={pendingChanges.has(p.name)}
                        showSourceBadge={showSourceBadge}
                        connected={connected}
                        onChange={handleSliderChange}
                      />
                    ))}
                  </div>
                ) : null;

              if (showStructureContext && structureContext) {
                return (
                  <StructureContextSummary context={structureContext}>
                    {sliderList}
                  </StructureContextSummary>
                );
              }
              return sliderList;
            })()}
            {hasGhBackedParameter && (
              <div className="mt-1 flex items-center justify-center gap-2">
                <button
                  type="button"
                  onClick={onBake}
                  disabled={baking || aborting}
                  aria-label="Aktuelle Grasshopper-Geometrie backen"
                  title="Aktuelle Grasshopper-Geometrie als echtes Rhino-Objekt übernehmen"
                  className="flex cursor-pointer items-center justify-center gap-1.5 rounded-full bg-primary px-4 py-2 text-xs font-semibold text-primary-foreground transition-colors duration-200 hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <Box className="h-3.5 w-3.5" />
                  {baking ? "Backe…" : "Geometrie backen"}
                </button>
                <button
                  type="button"
                  onClick={onAbort}
                  disabled={baking || aborting}
                  aria-label="Parametrisierung verwerfen und Original wiederherstellen"
                  title="Parametrisierung verwerfen — GH-Geometrie entfernen, das vorherige Original kommt zurück"
                  className="flex cursor-pointer items-center justify-center gap-1.5 rounded-full border border-border bg-card px-4 py-2 text-xs font-semibold text-muted-foreground transition-colors duration-200 hover:border-destructive/40 hover:text-destructive focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <RotateCcw className="h-3.5 w-3.5" />
                  {aborting ? "Verwerfe…" : "Verwerfen"}
                </button>
              </div>
            )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function StructureContextSummary({
  context,
  children,
}: {
  context: EditableStructureContext;
  children?: ReactNode;
}) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="overflow-hidden rounded-xl border border-border/60 bg-muted/30 text-xs">
      <button
        type="button"
        className="flex w-full cursor-pointer items-center justify-between gap-2 px-3 py-1.5 text-left transition-colors duration-200 hover:bg-muted/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
        onClick={() => setExpanded((value) => !value)}
        aria-label={
          expanded
            ? "Strukturdetails einklappen"
            : "Strukturdetails ausklappen"
        }
        title={expanded ? "Details einklappen" : "Details ausklappen"}
      >
        <span className="min-w-0 truncate text-sm font-medium text-foreground">
          {context.title}
        </span>
        <span className="flex shrink-0 items-center gap-2">
          <span className="rounded-full bg-primary/10 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-primary">
            {context.structure_type === "grasshopper"
              ? "Grasshopper"
              : "Bearbeitbar"}
          </span>
          <ChevronDown
            className={cn(
              "h-3.5 w-3.5 text-muted-foreground transition-transform",
              expanded && "rotate-180",
            )}
          />
        </span>
      </button>
      {children ? (
        <div className="border-t border-border/60 px-3 py-2">{children}</div>
      ) : null}
      {expanded && (
        <div className="border-t border-border/60 px-3 py-2">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div className="min-w-0">
              {context.summary ? (
                <div className="text-muted-foreground">{context.summary}</div>
              ) : null}
            </div>
          </div>
          {context.object_name ? (
            <div className="mt-2 text-foreground/80">
              Objekt: {context.object_name}
            </div>
          ) : null}
          {context.parameter_names.length > 0 ? (
            <div className="mt-2 flex flex-wrap gap-1">
              {context.parameter_names.map((name) => (
                <span
                  key={name}
                  className="rounded-full bg-muted px-2.5 py-0.5 text-[11px] font-medium text-muted-foreground"
                >
                  {name}
                </span>
              ))}
            </div>
          ) : null}
          {context.editable_operations.length > 0 ? (
            <div className="mt-2 text-[11px] text-muted-foreground">
              Direkt möglich: {context.editable_operations.join(", ")}
            </div>
          ) : null}
        </div>
      )}
    </div>
  );
}

interface SliderRowProps {
  parameter: ExposedParameter;
  pending: boolean;
  showSourceBadge: boolean;
  connected: boolean;
  onChange: (name: string, value: number) => void;
}

// Minimum gap between two backend updates while the user is dragging.
// Backend coalescing drops any intermediate ticks, so a low value can't
// flood Rhino/GH — it only raises the visible update rate when the recompute
// is fast. The editable-recipe (box/cylinder resize) path rebuilds in
// ~10-30 ms, so 60 ms (~16 fps) tracks the knob noticeably better than the
// old 120 ms (~8 fps); heavy GH chains stay recompute-bound regardless.
const LIVE_UPDATE_THROTTLE_MS = 60;

// Dezimalstellen, die der ECHTE Slider-Step braucht. Sonst rundet die Anzeige
// grob (z. B. auf ganze mm), waehrend der Slider in feineren Schritten laeuft
// — das Label zeigt dann etwas anderes an, als der Slider tatsaechlich tut.
function decimalsForStep(step: number): number {
  if (!Number.isFinite(step) || step <= 0) return 2;
  const s = step.toString();
  if (s.includes("e")) return 4; // sehr kleiner Step (wissenschaftliche Notation)
  const dot = s.indexOf(".");
  return dot < 0 ? 0 : Math.min(s.length - dot - 1, 4);
}

function ParameterSlider({
  parameter,
  pending,
  showSourceBadge,
  connected,
  onChange,
}: SliderRowProps) {
  const [draft, setDraft] = useState(parameter.current);
  const lastCurrentRef = useRef(parameter.current);
  // True while the user is actively dragging this knob — see the resync effect.
  const draggingRef = useRef(false);
  // Timestamp + last-sent-value pair used to throttle live updates while
  // the user drags. We want the GH recompute to run during the drag (so
  // the viewport tracks the slider), but no faster than the backend can
  // keep up — otherwise the visible geometry lags behind the slider knob.
  const lastSentAtRef = useRef<number>(0);
  const lastSentValueRef = useRef<number>(parameter.current);
  // Trailing-edge timer: if the user holds the slider still for ≥throttle
  // after a throttled-skip, we still want to fire that final value. This
  // covers the case where the user releases right between throttle ticks.
  const trailingTimerRef = useRef<number | null>(null);

  // Anzeige-Praezision aus dem echten Step ableiten (s. decimalsForStep) — die
  // Wertanzeige zeigt damit GENAU den Wert, den der Slider/das Feld sendet.
  const rawStep = parameter.step ?? (parameter.max - parameter.min) / 100;
  const digits = decimalsForStep(rawStep * parameter.display_factor);
  const toDisplay = (v: number) => (v * parameter.display_factor).toFixed(digits);
  // Editierbares Eingabefeld: eigener Text-State, der dem Slider folgt, solange
  // nicht getippt wird (editingRef). Erlaubt exakte manuelle Eingabe statt nur Slider.
  const [inputText, setInputText] = useState(() => toDisplay(parameter.current));
  const editingRef = useRef(false);

  useEffect(() => {
    // Don't snap the knob back to an inbound INTERMEDIATE value while the user
    // is dragging or a change is still in flight (slow GH recompute): the
    // coalescing worker applies an earlier value first, which would bounce the
    // knob backward for a frame. Resync only once no change is outstanding.
    if (draggingRef.current || pending) return;
    if (parameter.current !== lastCurrentRef.current) {
      setDraft(parameter.current);
      lastCurrentRef.current = parameter.current;
      lastSentValueRef.current = parameter.current;
    }
  }, [parameter.current, pending]);

  useEffect(() => {
    return () => {
      if (trailingTimerRef.current !== null) {
        window.clearTimeout(trailingTimerRef.current);
      }
    };
  }, []);

  // inputText folgt dem Slider-Wert, solange das Eingabefeld NICHT fokussiert
  // ist (waehrend der Nutzer tippt, nicht ueberschreiben).
  useEffect(() => {
    if (!editingRef.current) setInputText(toDisplay(draft));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft]);

  const display = toDisplay(draft);
  const minLabel = toDisplay(parameter.min);
  const maxLabel = toDisplay(parameter.max);

  const sendValue = useCallback(
    (value: number) => {
      lastSentAtRef.current = Date.now();
      lastSentValueRef.current = value;
      if (trailingTimerRef.current !== null) {
        window.clearTimeout(trailingTimerRef.current);
        trailingTimerRef.current = null;
      }
      onChange(parameter.name, value);
    },
    [onChange, parameter.name],
  );

  // Manuelle Eingabe uebernehmen (Enter/Blur): parsen (Dezimalkomma erlaubt),
  // in Basiseinheiten zurueckrechnen, auf [min,max] klemmen, senden. Ungueltige
  // Eingabe -> auf den aktuellen Wert zuruecksetzen (kein Datenmuell).
  const commitInput = (text: string) => {
    editingRef.current = false;
    const parsed = parseFloat(text.replace(",", "."));
    if (!Number.isFinite(parsed)) {
      setInputText(display);
      return;
    }
    const base = parsed / parameter.display_factor;
    const clamped = Math.min(parameter.max, Math.max(parameter.min, base));
    setDraft(clamped);
    sendValue(clamped);
    setInputText(toDisplay(clamped));
  };

  // Live drag handler: smooth UI via setDraft, throttled backend updates
  // via sendValue + a trailing-edge timer that catches the final position
  // when the user stops dragging between throttle ticks.
  const onDrag = useCallback(
    (rawValue: string) => {
      draggingRef.current = true;
      const value = parseFloat(rawValue);
      setDraft(value);
      const now = Date.now();
      const elapsed = now - lastSentAtRef.current;
      if (elapsed >= LIVE_UPDATE_THROTTLE_MS) {
        if (value !== lastSentValueRef.current) {
          sendValue(value);
        }
      } else {
        // Schedule a trailing update once the throttle window closes.
        if (trailingTimerRef.current !== null) {
          window.clearTimeout(trailingTimerRef.current);
        }
        trailingTimerRef.current = window.setTimeout(() => {
          trailingTimerRef.current = null;
          if (value !== lastSentValueRef.current) {
            sendValue(value);
          }
        }, LIVE_UPDATE_THROTTLE_MS - elapsed);
      }
    },
    [sendValue],
  );

  // Release handler: guarantees the final value reaches the backend even
  // if the user released right at a throttled-skip moment. Idempotent —
  // if sendValue already saw this exact value, the backend just confirms
  // the parameter.updated event.
  const onRelease = useCallback(
    (rawValue: string) => {
      draggingRef.current = false;
      const value = parseFloat(rawValue);
      if (value !== lastSentValueRef.current) {
        sendValue(value);
      }
    },
    [sendValue],
  );

  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-baseline justify-between gap-2">
        <div className="flex min-w-0 items-center gap-1.5">
          <span className="truncate text-sm font-medium text-foreground">
            {parameter.name}
          </span>
          {showSourceBadge && (
            <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
              {parameter.source_label ?? parameter.source}
            </span>
          )}
        </div>
        <span
          className={
            pending
              ? "flex shrink-0 items-baseline gap-0.5 rounded-full bg-primary/10 px-2 py-0.5 text-xs font-medium tabular-nums text-primary animate-pending-pulse motion-reduce:animate-none motion-reduce:opacity-60"
              : "flex shrink-0 items-baseline gap-0.5 rounded-full bg-primary/10 px-2 py-0.5 text-xs font-medium tabular-nums text-primary"
          }
        >
          <input
            type="text"
            inputMode="decimal"
            value={inputText}
            disabled={!connected}
            aria-label={`Wert für ${parameter.name}`}
            title="Wert direkt eingeben (Enter bestätigt)"
            onFocus={(e) => {
              editingRef.current = true;
              e.currentTarget.select();
            }}
            onChange={(e) => setInputText(e.target.value)}
            onBlur={(e) => commitInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") e.currentTarget.blur();
            }}
            className="w-14 bg-transparent text-right tabular-nums text-primary outline-none focus:underline disabled:cursor-not-allowed"
          />
          {parameter.display_unit ? <span>{parameter.display_unit}</span> : null}
        </span>
      </div>
      <input
        type="range"
        min={parameter.min}
        max={parameter.max}
        step={parameter.step ?? (parameter.max - parameter.min) / 100}
        value={draft}
        disabled={!connected}
        onChange={(e) => onDrag(e.target.value)}
        onMouseUp={(e) => onRelease((e.target as HTMLInputElement).value)}
        onTouchEnd={(e) => onRelease((e.target as HTMLInputElement).value)}
        onKeyUp={(e) => onRelease((e.target as HTMLInputElement).value)}
        className="h-1.5 w-full cursor-pointer appearance-none rounded-full bg-border accent-[hsl(var(--primary))] disabled:cursor-not-allowed disabled:opacity-50"
      />
      <div className="flex items-center justify-between text-[10px] text-muted-foreground tabular-nums">
        <span>{minLabel}</span>
        <span>{maxLabel}</span>
      </div>
    </div>
  );
}

// Memoisiert: waehrend eines Drags aendert sich pro Tick nur das gezogene
// Parameter-Objekt (Store ersetzt genau dieses) und dessen pending-Flag —
// alle anderen Zeilen behalten identische Props und rendern nicht mit.
// Voraussetzung ist der stabile handleSliderChange-Callback im Panel.
const MemoParameterSlider = memo(ParameterSlider);
