import { useCallback, useEffect, useRef, useState } from "react";
import {
  Check,
  Circle,
  Pencil,
  RefreshCw,
  RotateCcw,
  Slash,
  Spline,
  Trash2,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { useChatStore } from "@/store/chatStore";
import { cn } from "@/lib/utils";
import type { ImageSource, SketchBlock, WsCommand } from "@/lib/types";
import { smoothCurve, ellipsePoints } from "./sketch-geometry";

// panel.py ruft diesen Hook ueber die WebView auf, wenn das Plugin-Fenster
// waehrend des Sketchs ueber das OS-[X] geschlossen wird: dann schliesst nur der
// Sketch, das Plugin bleibt offen. Nur gesetzt, solange das Overlay offen ist.
declare global {
  interface Window {
    __furnitureCloseSketch?: () => void;
  }
}

/**
 * Full-viewport modal for annotating a Rhino viewport snapshot.
 *
 * The overlay opens when `sketchOverlay` flips to non-null (driven by a
 * `viewport.sketch_ready` event after the user clicks the sketch button).
 * The user draws strokes over the JPEG backdrop; on "Fertig" we composite
 * background + strokes to a PNG via canvas and stage it as a SketchBlock
 * attachment. The sketch never round-trips through the server — the
 * server's only contribution was the JPEG we draw on top of.
 *
 * "Hintergrund neu laden" re-requests a snapshot so the user can orbit in
 * Rhino between sketches without exiting. Strokes reset on a new backdrop
 * (coordinate spaces don't transfer cleanly when resolution changes; we
 * keep the UX predictable rather than half-aligning old strokes).
 */

interface Stroke {
  id: string;
  color: string;
  width: number;
  // SVG coordinates (bgW × bgH space), not CSS pixels. Stroke data stays
  // resolution-independent this way and we avoid rescaling on window
  // resize.
  points: { x: number; y: number }[];
}

// An die Plugin-Akzente angelehnt (Brand-Orange = --brand-orange 16/86/53,
// dazu Blau/Gruen als harmonierende Akzente + neutrale Toene + Rot fuer
// Betonung). Default = Brand-Orange (gut sichtbar, matcht das Plugin).
const COLORS: { value: string; label: string }[] = [
  { value: "#ee5720", label: "Orange" },
  { value: "#2563eb", label: "Blau" },
  { value: "#16a34a", label: "Grün" },
  { value: "#dc2626", label: "Rot" },
  { value: "#1f2937", label: "Anthrazit" },
  { value: "#6b7280", label: "Grau" },
];

const WIDTHS: number[] = [3, 6, 12];

export function SketchOverlay({
  onSend,
}: {
  onSend: (cmd: WsCommand) => boolean;
}) {
  const overlay = useChatStore((s) => s.sketchOverlay);
  const setSketchOverlay = useChatStore((s) => s.setSketchOverlay);
  const addStagedAttachment = useChatStore((s) => s.addStagedAttachment);
  const removeStagedAttachmentsByGroup = useChatStore(
    (s) => s.removeStagedAttachmentsByGroup,
  );
  const setViewportPending = useChatStore((s) => s.setViewportPending);
  const viewportPending = useChatStore((s) => s.viewportPending);

  const svgRef = useRef<SVGSVGElement | null>(null);
  const currentRef = useRef<Stroke | null>(null);
  const startRef = useRef<{ x: number; y: number } | null>(null);
  const rawPointsRef = useRef<{ x: number; y: number }[]>([]);
  const [strokes, setStrokes] = useState<Stroke[]>([]);
  // Strokes of the views the user is NOT currently drawing on. Switching views
  // parks the active strokes here and restores the target view's. Keyed by
  // view name; the active view's strokes live in `strokes`. Enables drawing on
  // several views in one session ("Fertig" emits one marked snapshot per view).
  const [savedStrokes, setSavedStrokes] = useState<Record<string, Stroke[]>>({});
  const [liveStroke, setLiveStroke] = useState<Stroke | null>(null);
  const [color, setColor] = useState<string>(COLORS[0].value);
  const [width, setWidth] = useState<number>(6);
  const [mode, setMode] = useState<"free" | "line" | "ellipse" | "curve">("free");
  const [rendering, setRendering] = useState(false);
  // Which of the four views the user is drawing on. Defaults to perspective
  // (or the first view if the backend ever changes the set).
  const [selectedViewName, setSelectedViewName] = useState<string>("perspective");

  // Reset ALL strokes (active view + every parked view) only on a genuine
  // backdrop refresh or when the overlay (re)opens — NOT on a view switch. A
  // refresh produces a brand-new overlay with a fresh composite (new base64),
  // so keying the reset on the composite data distinguishes "refresh/open"
  // from "switched view". On a view switch the overlay object is unchanged, so
  // this effect stays quiet and `selectView` handles save/restore of per-view
  // strokes itself. Closing (overlay=null) flips the dep to undefined, which
  // also clears any residual in-progress state.
  const refreshKey = overlay?.composite.source.data;
  useEffect(() => {
    // Re-Open einer bestehenden Skizze aus dem Chat: die gespeicherten
    // SVG-Striche als EDITIERBARE Striche auf der (einen) wieder-geoeffneten
    // Ansicht zuruecklesen. Sonst (frische Aufnahme): alles leeren.
    const byView = overlay?.initialStrokesByView;
    const initSvg = overlay?.initialSvg;
    const firstView = overlay?.views[0];
    if (byView && firstView && Object.keys(byView).length > 0) {
      // Mehransicht-Reopen einer gestagten Skizze: ALLE Ansichten mit ihren
      // Strichen seeden (pro Ansicht das gespeicherte svg parsen).
      const restored: Record<string, Stroke[]> = {};
      for (const [name, svg] of Object.entries(byView)) {
        restored[name] = parseSvgToStrokes(svg);
      }
      setSelectedViewName(firstView.name);
      setSavedStrokes(restored);
      setStrokes(restored[firstView.name] ?? []);
    } else if (initSvg && firstView) {
      // Einzelansicht-Reopen (gesendete Skizze aus dem Chat-Verlauf).
      const loaded = parseSvgToStrokes(initSvg);
      setSelectedViewName(firstView.name);
      setSavedStrokes({ [firstView.name]: loaded });
      setStrokes(loaded);
    } else {
      setStrokes([]);
      setSavedStrokes({});
    }
    setLiveStroke(null);
    currentRef.current = null;
    setRendering(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshKey]);

  const cancel = useCallback(() => {
    setSketchOverlay(null);
    // Disown any in-flight "Hintergrund neu laden": clearing viewportPending
    // tells the sketch_ready handler the request was dismissed, so a late
    // response can't resurrect the overlay the user just closed.
    setViewportPending(null);
  }, [setSketchOverlay, setViewportPending]);

  // Escape = cancel. Registered unconditionally and gated inside the
  // handler so hook order stays stable whether the overlay is mounted or
  // not.
  useEffect(() => {
    if (!overlay) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") cancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [overlay, cancel]);

  // Grow the floating plugin window to cover the active Rhino viewport while
  // the sketch overlay is open, then restore it on close. Keyed on overlay
  // visibility (a boolean) so "grow" fires exactly ONCE per open — not on
  // every refreshBackground/backdrop swap — and the cleanup sends "restore"
  // on all close paths (Fertig / Abbrechen / Esc all flip overlay → null,
  // which unmounts/re-runs this effect). `onSend` is read through a ref so a
  // changing prop identity can't re-trigger grow. Fully best-effort: the
  // backend swallows failures and a docked window is a no-op there.
  const sketchOpen = !!overlay;
  const onSendRef = useRef(onSend);
  onSendRef.current = onSend;
  useEffect(() => {
    if (!sketchOpen) return;
    try {
      onSendRef.current({
        type: "viewport.sketch_window",
        payload: { state: "grow" },
      });
    } catch {
      // best-effort; never block the sketch flow
    }
    return () => {
      try {
        onSendRef.current({
          type: "viewport.sketch_window",
          payload: { state: "restore" },
        });
      } catch {
        // best-effort
      }
    };
  }, [sketchOpen]);

  // Schließen-Hook bereitstellen, solange das Overlay offen ist: panel.py
  // verwirft darüber NUR den Sketch (nicht das ganze Plugin), wenn das
  // OS-Fenster-[X] mitten im Sketch gedrückt wird.
  useEffect(() => {
    if (!sketchOpen) return;
    window.__furnitureCloseSketch = cancel;
    return () => {
      if (window.__furnitureCloseSketch === cancel) {
        delete window.__furnitureCloseSketch;
      }
    };
  }, [sketchOpen, cancel]);

  if (!overlay) return null;

  // The active view drives the drawing surface. The whole stroke pipeline
  // (toSvgCoords / composite / serializeSvg) operates on this view's image
  // and pixel dimensions.
  const view = overlay.views.find((v) => v.name === selectedViewName) ?? overlay.views[0];
  const background = view.source;
  const bgW = view.width;
  const bgH = view.height;

  // Did the user draw on ANY view (active or parked)? Drives the "Fertig" vs
  // "Snapshot anhängen" affordance and the per-view "marked" indicator.
  const strokesForView = (name: string): number =>
    (name === selectedViewName ? strokes : savedStrokes[name] ?? []).length;
  const anyStrokes =
    strokes.length > 0 ||
    Object.values(savedStrokes).some((s) => s.length > 0);

  // Switching views KEEPS each view's strokes: the active strokes are parked
  // in `savedStrokes` and the target view's parked strokes are restored.
  // Strokes are never transferred between views (their coordinate space is the
  // view's own image) — only stored and restored. The designer can annotate
  // several views; "Fertig" turns each annotated view into its own marked
  // snapshot. (Only a backdrop refresh clears strokes — see the reset effect.)
  const selectView = (name: string) => {
    if (name === selectedViewName) return;
    setSavedStrokes((prev) => ({ ...prev, [selectedViewName]: strokes }));
    setStrokes(savedStrokes[name] ?? []);
    setLiveStroke(null);
    currentRef.current = null;
    setSelectedViewName(name);
  };

  const toSvgCoords = (
    clientX: number,
    clientY: number,
  ): { x: number; y: number } | null => {
    const svg = svgRef.current;
    if (!svg) return null;
    const rect = svg.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) return null;

    // The SVG and background image both use "contain"/"meet" behavior, so
    // the actual drawable content can be letterboxed inside the element box.
    // Mapping against the full rect causes an increasing cursor offset the
    // farther we move from the center. Instead, project into the real
    // displayed content box first, then into viewBox coordinates.
    const scale = Math.min(rect.width / bgW, rect.height / bgH);
    const contentWidth = bgW * scale;
    const contentHeight = bgH * scale;
    const offsetX = rect.left + (rect.width - contentWidth) / 2;
    const offsetY = rect.top + (rect.height - contentHeight) / 2;

    const localX = clientX - offsetX;
    const localY = clientY - offsetY;
    const clampedX = Math.max(0, Math.min(localX, contentWidth));
    const clampedY = Math.max(0, Math.min(localY, contentHeight));

    return {
      x: (clampedX / contentWidth) * bgW,
      y: (clampedY / contentHeight) * bgH,
    };
  };

  const onPointerDown = (e: React.PointerEvent<SVGSVGElement>) => {
    if (e.pointerType === "mouse" && e.button !== 0) return;
    e.preventDefault();
    svgRef.current?.setPointerCapture(e.pointerId);
    const p = toSvgCoords(e.clientX, e.clientY);
    if (!p) return;
    startRef.current = p;
    rawPointsRef.current = [p];
    const stroke: Stroke = {
      id: `s-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
      color,
      width,
      points: [p],
    };
    currentRef.current = stroke;
    setLiveStroke(stroke);
  };

  const onPointerMove = (e: React.PointerEvent<SVGSVGElement>) => {
    if (!currentRef.current) return;
    const p = toSvgCoords(e.clientX, e.clientY);
    if (!p) return;
    let points: { x: number; y: number }[];
    switch (mode) {
      case "line": {
        const start = startRef.current ?? currentRef.current.points[0];
        points = [start, p];
        break;
      }
      case "ellipse": {
        const start = startRef.current ?? currentRef.current.points[0];
        points = ellipsePoints(start, p);
        break;
      }
      case "curve": {
        // Rohpunkte sammeln, live als flowing Spline (RDP-Simplify +
        // Catmull-Rom) glaetten -> klar geschwungen, anders als Freihand.
        const raw = rawPointsRef.current;
        const last = raw[raw.length - 1];
        if (last && Math.hypot(p.x - last.x, p.y - last.y) < 0.5) return;
        raw.push(p);
        points = smoothCurve(raw);
        break;
      }
      default: {
        // free — append, skip sub-pixel jitter
        const last = currentRef.current.points[currentRef.current.points.length - 1];
        if (last && Math.hypot(p.x - last.x, p.y - last.y) < 0.5) return;
        points = [...currentRef.current.points, p];
        break;
      }
    }
    const next: Stroke = { ...currentRef.current, points };
    currentRef.current = next;
    setLiveStroke(next);
  };

  const onPointerUp = (e: React.PointerEvent<SVGSVGElement>) => {
    const stroke = currentRef.current;
    currentRef.current = null;
    setLiveStroke(null);
    try {
      svgRef.current?.releasePointerCapture(e.pointerId);
    } catch {
      // releasePointerCapture throws if we never captured — fine, ignore.
    }
    if (stroke && stroke.points.length > 0) {
      // For curve mode: finalize with smoothed points from the raw buffer
      if (mode === "curve" && rawPointsRef.current.length > 0) {
        const smoothed = smoothCurve(rawPointsRef.current);
        rawPointsRef.current = [];
        setStrokes((prev) => [...prev, { ...stroke, points: smoothed }]);
      } else {
        rawPointsRef.current = [];
        setStrokes((prev) => [...prev, stroke]);
      }
    }
  };

  const undo = () => setStrokes((prev) => prev.slice(0, -1));
  const clear = () => setStrokes([]);

  const refreshBackground = () => {
    // Block while a request is in flight AND while "Fertig" is compositing:
    // done() captured the current backdrop/strokes, so a backdrop swap mid-
    // render would stage the wrong (old) view and then close the fresh overlay.
    if (viewportPending || rendering) return;
    setViewportPending("sketch");
    onSend({
      type: "viewport.request_sketch",
      payload: { max_size: 1600 },
    });
  };

  const done = async () => {
    if (rendering) return;
    setRendering(true);
    try {
      // Merge the active view's strokes with the parked ones, then emit ONE
      // marked snapshot per annotated view (in the overlay's view order).
      const byView: Record<string, Stroke[]> = {
        ...savedStrokes,
        [selectedViewName]: strokes,
      };
      const markedViews = overlay.views.filter(
        (v) => (byView[v.name]?.length ?? 0) > 0,
      );

      // Eine Sketch-Sitzung = eine Gruppe. Beim Re-Open einer gestagten Skizze
      // (overlay.reopenGroupId gesetzt) werden deren urspruengliche Bloecke
      // ERSETZT statt dupliziert; sonst eine frische Gruppen-ID.
      const groupId = overlay.reopenGroupId ?? newSketchGroupId();
      // Erst alle Bloecke lokal bauen, dann in einem Rutsch stagen. Wirft
      // composite() bei einer Ansicht (kaputter/zu grosser Backdrop), wird
      // NICHTS gestaged und das Overlay bleibt mit den Strichen offen — so kann
      // ein Retry nicht die schon gelungenen Ansichten doppelt stagen.
      const staged: SketchBlock[] = [];
      if (markedViews.length === 0) {
        // No strokes on any view → attach the pure Multi-View snapshot as
        // scene context. Die Composite MUSS hier auch als ``rendered_png``
        // mitgegeben werden: das Backend-Schema verwirft ``composite`` (nicht
        // deklariert), und der Flattener nimmt fuer einen strichlosen Snapshot
        // den Legacy-Pfad ``rendered_png or background``. Ohne rendered_png
        // kaeme gar kein Bild beim Modell an (nur die Caption) -> der reine
        // Snapshot waere unsichtbar.
        staged.push({
          type: "sketch",
          svg: "",
          rendered_png: overlay.composite.source,
          composite: overlay.composite.source,
          composite_width: overlay.composite.width,
          composite_height: overlay.composite.height,
          has_strokes: false,
          width: overlay.composite.width,
          height: overlay.composite.height,
          sketch_group: groupId,
          // Alle Ansichts-Backdrops mitspeichern -> Re-Open bietet jede Ansicht
          // zum Bemalen an, auch wenn dieser Snapshot strichlos ist.
          all_views: overlay.views,
        });
      } else {
        // One marked-view block per annotated view. Each carries its own
        // marked single view (Deixis) + view label; the Multi-View composite
        // rides along ONCE (on the first block) as shared scene context so the
        // model doesn't receive the same overview image N times.
        for (let i = 0; i < markedViews.length; i++) {
          const v = markedViews[i];
          const vStrokes = byView[v.name] ?? [];
          const rendered = await composite(v.source, v.width, v.height, vStrokes);
          staged.push({
            type: "sketch",
            svg: serializeSvg(v.width, v.height, vStrokes),
            rendered_png: rendered,
            // Sauberer (un-bemalter) Hintergrund -> erlaubt das Wieder-Oeffnen
            // im Editor mit den Strichen als EDITIERBARE Linien (background +
            // svg). Bleibt frontend/DB-lokal und geht NIE ans Modell (Flattener
            // nimmt fuer persistierte Bloecke den Legacy-Pfad rendered_png).
            background: v.source,
            view_name: v.label,
            has_strokes: true,
            width: v.width,
            height: v.height,
            sketch_group: groupId,
            // Composite + ALLE Ansichts-Backdrops nur EINMAL (erster Block):
            // all_views erlaubt das Wieder-Oeffnen mit JEDER Ansicht, nicht nur
            // den bemalten.
            ...(i === 0
              ? {
                  composite: overlay.composite.source,
                  composite_width: overlay.composite.width,
                  composite_height: overlay.composite.height,
                  all_views: overlay.views,
                }
              : {}),
          });
        }
      }
      // Beim Reopen die alte Gruppe ZUERST entfernen, dann die neue staffeln
      // (gleiche groupId -> Reihenfolge wichtig, sonst wuerde die neue mit-
      // gefiltert).
      if (overlay.reopenGroupId) {
        removeStagedAttachmentsByGroup(overlay.reopenGroupId);
      }
      staged.forEach(addStagedAttachment);
      setSketchOverlay(null);
    } catch (err) {
      console.error("sketch composite failed", err);
      // Keep the overlay open so the user doesn't lose strokes on error.
      setRendering(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-background/95 text-foreground backdrop-blur-md">
      {/* Top bar */}
      <div className="flex items-center justify-between bg-card px-4 py-2.5 shadow-panel">
        <div className="font-display text-sm font-bold tracking-tight">
          Viewport skizzieren
        </div>
        <Button
          variant="ghost"
          size="icon"
          onClick={cancel}
          aria-label="Abbrechen"
          title="Skizzieren abbrechen"
          className="h-8 w-8 rounded-full"
        >
          <X className="h-4 w-4" />
        </Button>
      </div>

      {/* View picker — choose which view to draw on; the Multi-View snapshot
          always rides along as context. */}
      <div className="flex justify-center px-4 pb-1 pt-3">
        <div className="flex items-center gap-2 rounded-2xl bg-card px-2.5 py-2 shadow-pop">
          {overlay.views.map((v) => {
            const isActive = v.name === selectedViewName;
            const marked = strokesForView(v.name) > 0;
            return (
              <button
                key={v.name}
                type="button"
                onClick={() => selectView(v.name)}
                aria-label={`Ansicht ${v.label}${marked ? " (markiert)" : ""}`}
                aria-pressed={isActive}
                title={
                  marked
                    ? `Auf ${v.label} zeichnen (enthält Striche)`
                    : `Auf ${v.label} zeichnen`
                }
                className={cn(
                  "relative flex cursor-pointer flex-col items-center gap-1 rounded-xl p-1 transition-transform duration-150",
                  isActive ? "scale-[1.02]" : "hover:scale-[1.01]",
                )}
              >
                {/* Marked-view indicator — this view already carries strokes
                    that "Fertig" will turn into a snapshot. */}
                {marked && (
                  <span
                    aria-hidden="true"
                    title="Diese Ansicht enthält Striche"
                    className="absolute right-1 top-1 z-10 h-2.5 w-2.5 rounded-full bg-brand-orange ring-2 ring-card"
                  />
                )}
                <img
                  src={`data:${v.source.media_type};base64,${v.source.data}`}
                  alt={v.label}
                  draggable={false}
                  // Feste Hoehe, AUTO-Breite -> die Kachel nimmt das natuerliche
                  // Seitenverhaeltnis des Viewportbilds an. Dadurch zeigt sie die
                  // VOLLE Ansicht: keine Letterbox-Balken (wie bei object-contain
                  // in einer zu hohen Kachel) UND kein harter Crop/Zoom (wie bei
                  // object-cover). Das Sketch-Fenster ist beim Zeichnen vergroessert,
                  // daher ist Platz fuer die etwas breiteren Kacheln.
                  className={cn(
                    "h-14 w-auto shrink-0 select-none rounded-lg bg-muted/40 object-contain transition-shadow duration-150",
                    isActive
                      ? "ring-2 ring-primary"
                      : "ring-1 ring-border hover:ring-primary/50",
                  )}
                />
                <span
                  className={cn(
                    "text-[11px] font-medium",
                    isActive ? "text-primary" : "text-muted-foreground",
                  )}
                >
                  {v.label}
                </span>
              </button>
            );
          })}
        </div>
      </div>

      {/* Canvas area */}
      <div className="flex flex-1 items-center justify-center overflow-hidden p-4">
        <div
          className="relative max-h-full max-w-full overflow-hidden rounded-2xl bg-card shadow-panel"
          style={{ aspectRatio: `${bgW} / ${bgH}`, width: "100%", height: "100%" }}
        >
          <div className="relative mx-auto h-full" style={{ aspectRatio: `${bgW} / ${bgH}`, maxHeight: "100%", maxWidth: "100%" }}>
            <img
              src={`data:${background.media_type};base64,${background.data}`}
              alt="Viewport"
              className="pointer-events-none absolute inset-0 h-full w-full select-none object-contain"
              draggable={false}
            />
            <svg
              ref={svgRef}
              viewBox={`0 0 ${bgW} ${bgH}`}
              preserveAspectRatio="xMidYMid meet"
              className="absolute inset-0 h-full w-full touch-none"
              onPointerDown={onPointerDown}
              onPointerMove={onPointerMove}
              onPointerUp={onPointerUp}
              onPointerCancel={onPointerUp}
            >
              {strokes.map((s) => (
                <path
                  key={s.id}
                  d={pathD(s.points)}
                  stroke={s.color}
                  strokeWidth={s.width}
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  fill="none"
                />
              ))}
              {liveStroke && (
                <path
                  d={pathD(liveStroke.points)}
                  stroke={liveStroke.color}
                  strokeWidth={liveStroke.width}
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  fill="none"
                />
              )}
            </svg>
          </div>
        </div>
      </div>

      {/* Toolbar — schwebende weiße Pill-Leiste */}
      <div className="flex justify-center px-4 pb-4 pt-1">
        <div className="flex max-w-full flex-wrap items-center justify-center gap-x-4 gap-y-2 rounded-full bg-card px-4 py-2 shadow-pop">
        <div className="flex items-center gap-2">
          {(
            [
              { value: "free", Icon: Pencil, label: "Freihand" },
              { value: "line", Icon: Slash, label: "Gerade Linie" },
              { value: "ellipse", Icon: Circle, label: "Ellipse" },
              { value: "curve", Icon: Spline, label: "Geschwungene Kurve" },
            ] as const
          ).map(({ value: m, Icon, label }) => (
            <button
              key={m}
              type="button"
              onClick={() => setMode(m)}
              title={label}
              aria-label={label}
              className={cn(
                "flex h-7 w-7 cursor-pointer items-center justify-center rounded-full border transition-colors duration-200",
                mode === m
                  ? "border-primary bg-primary text-primary-foreground"
                  : "border-border bg-card text-foreground hover:border-primary/60",
              )}
            >
              <Icon className="h-3.5 w-3.5" />
            </button>
          ))}
        </div>
        <div aria-hidden="true" className="h-5 w-px bg-border/60" />
        <div className="flex items-center gap-2">
          {COLORS.map((c) => (
            <button
              key={c.value}
              type="button"
              onClick={() => setColor(c.value)}
              aria-label={c.label}
              title={c.label}
              className={cn(
                "h-7 w-7 cursor-pointer rounded-full border-2 border-card shadow-sm transition-shadow duration-200",
                color === c.value
                  ? "ring-2 ring-primary"
                  : "ring-1 ring-border hover:ring-primary/50",
              )}
              style={{ backgroundColor: c.value }}
            />
          ))}
        </div>
        <div aria-hidden="true" className="h-5 w-px bg-border/60" />
        <div className="flex items-center gap-2">
          {WIDTHS.map((w) => (
            <button
              key={w}
              type="button"
              onClick={() => setWidth(w)}
              title={`Strichstaerke ${w}`}
              aria-label={`Strichstärke ${w}`}
              className={cn(
                "flex h-7 w-7 cursor-pointer items-center justify-center rounded-full border transition-colors duration-200",
                width === w
                  ? "border-primary bg-primary"
                  : "border-border bg-card hover:border-primary/60",
              )}
            >
              <span
                className="rounded-full"
                style={{ width: w, height: w, backgroundColor: color }}
              />
            </button>
          ))}
        </div>
        <div aria-hidden="true" className="h-5 w-px bg-border/60" />
        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            size="icon"
            onClick={undo}
            disabled={strokes.length === 0}
            aria-label="Rückgängig"
            title="Letzten Strich rückgängig"
            className="h-8 w-8 rounded-full disabled:opacity-30"
          >
            <RotateCcw className="h-4 w-4" />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            onClick={clear}
            disabled={strokes.length === 0}
            aria-label="Alles löschen"
            title="Alle Striche löschen"
            className="h-8 w-8 rounded-full disabled:opacity-30"
          >
            <Trash2 className="h-4 w-4" />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            onClick={refreshBackground}
            disabled={viewportPending !== null}
            aria-label="Hintergrund neu laden"
            title="Viewport-Hintergrund neu aufnehmen (aktuelle Striche gehen dabei verloren)"
            className={cn(
              "h-8 w-8 rounded-full disabled:opacity-30",
              viewportPending === "sketch" && "animate-pulse",
            )}
          >
            <RefreshCw className="h-4 w-4" />
          </Button>
        </div>
        <div aria-hidden="true" className="h-5 w-px bg-border/60" />
        <Button
          type="button"
          onClick={done}
          disabled={rendering}
          aria-label="Fertig"
          title={
            anyStrokes
              ? "Jede markierte Ansicht (+ Multi-View-Snapshot) als Anhang übernehmen"
              : "Reinen Multi-View-Snapshot als Anhang übernehmen (ohne Striche)"
          }
          className="gap-2 rounded-full"
        >
          <Check className="h-4 w-4" />
          {rendering
            ? "Rendere…"
            : anyStrokes
              ? "Fertig"
              : "Snapshot anhängen"}
        </Button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function pathD(pts: { x: number; y: number }[]): string {
  if (pts.length === 0) return "";
  const head = `M ${pts[0].x.toFixed(2)} ${pts[0].y.toFixed(2)}`;
  if (pts.length === 1) {
    // A single-point tap — draw a tiny stub so the round-cap actually
    // renders as a dot instead of nothing.
    return `${head} L ${(pts[0].x + 0.01).toFixed(2)} ${(pts[0].y + 0.01).toFixed(2)}`;
  }
  const tail = pts
    .slice(1)
    .map((p) => `L ${p.x.toFixed(2)} ${p.y.toFixed(2)}`)
    .join(" ");
  return `${head} ${tail}`;
}

// Kehrt serializeSvg um: parst die gespeicherten ``<path>``-Striche zurueck in
// editierbare Stroke[] (Koordinaten sind in Bild-/viewBox-Raum, also direkt mit
// dem gleich grossen Hintergrund deckungsgleich). Best-effort + tolerant.
function newSketchGroupId(): string {
  try {
    if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
      return crypto.randomUUID();
    }
  } catch {
    // fall through to the timestamp fallback below
  }
  return `sk_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
}


function parseSvgToStrokes(svg: string): Stroke[] {
  const out: Stroke[] = [];
  if (!svg) return out;
  const pathRe = /<path\b[^>]*>/g;
  let m: RegExpExecArray | null;
  let n = 0;
  while ((m = pathRe.exec(svg)) !== null) {
    const tag = m[0];
    const d = (/\bd="([^"]*)"/.exec(tag) ?? [])[1] ?? "";
    const color = (/\bstroke="([^"]*)"/.exec(tag) ?? [])[1] ?? COLORS[0].value;
    const widthStr = (/\bstroke-width="([^"]*)"/.exec(tag) ?? [])[1] ?? "6";
    const width = Number.parseFloat(widthStr);
    const points = parsePathPoints(d);
    if (points.length > 0) {
      out.push({
        id: `reopen-${n}`,
        color,
        width: Number.isFinite(width) && width > 0 ? width : 6,
        points,
      });
      n += 1;
    }
  }
  return out;
}

function parsePathPoints(d: string): { x: number; y: number }[] {
  const pts: { x: number; y: number }[] = [];
  const re = /[MLml]\s*(-?[\d.]+)[\s,]+(-?[\d.]+)/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(d)) !== null) {
    const x = Number.parseFloat(m[1]);
    const y = Number.parseFloat(m[2]);
    if (Number.isFinite(x) && Number.isFinite(y)) pts.push({ x, y });
  }
  return pts;
}

function serializeSvg(w: number, h: number, strokes: Stroke[]): string {
  const body = strokes
    .map(
      (s) =>
        `<path d="${pathD(s.points)}" stroke="${s.color}" stroke-width="${s.width}" stroke-linecap="round" stroke-linejoin="round" fill="none" />`,
    )
    .join("");
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}">${body}</svg>`;
}

// Kurven-/Ellipse-Mathematik lebt jetzt in ./sketch-geometry (pure, testbar).

async function composite(
  bg: ImageSource,
  w: number,
  h: number,
  strokes: Stroke[],
): Promise<ImageSource> {
  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("canvas 2d context unavailable");

  await new Promise<void>((resolve, reject) => {
    const img = new Image();
    img.onload = () => {
      ctx.drawImage(img, 0, 0, w, h);
      resolve();
    };
    img.onerror = () => reject(new Error("background load failed"));
    img.src = `data:${bg.media_type};base64,${bg.data}`;
  });

  for (const s of strokes) {
    if (s.points.length === 0) continue;
    ctx.strokeStyle = s.color;
    ctx.lineWidth = s.width;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.beginPath();
    ctx.moveTo(s.points[0].x, s.points[0].y);
    if (s.points.length === 1) {
      ctx.lineTo(s.points[0].x + 0.5, s.points[0].y + 0.5);
    } else {
      for (let i = 1; i < s.points.length; i++) {
        ctx.lineTo(s.points[i].x, s.points[i].y);
      }
    }
    ctx.stroke();
  }

  // Der Editor zeichnet auf dem hochaufgeloesten Backdrop (v.source). Das hier
  // gebackene Bild geht ans Modell + in die Chat-Kachel — daher erst JETZT auf
  // eine modell-freundliche Groesse herunterskalieren ("erst danach
  // verschlechtern"): scharfes Editieren, aber konstante Token-Kosten.
  const MODEL_CAP = 1200; // laengste Kante in px
  let outCanvas: HTMLCanvasElement = canvas;
  const longest = Math.max(w, h);
  if (longest > MODEL_CAP) {
    const scale = MODEL_CAP / longest;
    const sc = document.createElement("canvas");
    sc.width = Math.max(1, Math.round(w * scale));
    sc.height = Math.max(1, Math.round(h * scale));
    const sctx = sc.getContext("2d");
    if (sctx) {
      sctx.imageSmoothingEnabled = true;
      sctx.imageSmoothingQuality = "high";
      sctx.drawImage(canvas, 0, 0, sc.width, sc.height);
      outCanvas = sc;
    }
  }
  const dataUrl = outCanvas.toDataURL("image/png");
  const b64 = dataUrl.split(",")[1] ?? "";
  return { type: "base64", media_type: "image/png", data: b64 };
}
