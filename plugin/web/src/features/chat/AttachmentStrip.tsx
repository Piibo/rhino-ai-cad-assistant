// Anhang-Streifen unter dem Composer: gestagte Bloecke (Bild/Auswahl/Punkt/
// Komponente/Skizze) als entfernbare Kacheln/Chips. Aus InputBar.tsx ausgelagert
// (Refactor 26.06.2026, Verhalten identisch).
import { useEffect, useRef, useState } from "react";
import { X } from "lucide-react";
import { useChatStore } from "@/store/chatStore";
import { componentPickLabel } from "@/lib/component-pick-label";
import { pointPickLabel } from "@/lib/point-pick-label";
import { selectionLabel } from "@/lib/selection-label";
import { buildSketchOverlayFromGroup } from "./sketch-overlay-builder";
import type { ContentBlock, ImageSource, SketchBlock } from "@/lib/types";

export function AttachmentStrip({
  blocks,
  onRemove,
}: {
  blocks: ContentBlock[];
  onRemove: (index: number) => void;
}) {
  const setSketchOverlay = useChatStore((s) => s.setSketchOverlay);
  // Exit-Animation: der zu entfernende Chip faded erst aus (140 ms), dann
  // fliegt er aus dem Store. Waehrend eines laufenden Exits sind weitere
  // Entfernungen gesperrt — die Chips sind index-gekeyt, ein zweites Remove
  // im selben Fenster wuerde sonst nach dem Index-Shift den falschen treffen.
  const [leaving, setLeaving] = useState<number | null>(null);
  // Timer im Ref + Unmount-Cleanup: sendet der Nutzer WAEHREND der Exit-
  // Animation (clearStagedAttachments -> Strip unmountet), darf der Timer
  // nicht mehr feuern — sonst entfernt onRemove(index) 140 ms spaeter per
  // stale Index einen falschen, frisch gestageten Anhang (Review-Finding).
  const exitTimerRef = useRef<number | null>(null);
  useEffect(() => {
    return () => {
      if (exitTimerRef.current !== null) {
        window.clearTimeout(exitTimerRef.current);
      }
    };
  }, []);
  const handleRemove = (index: number) => {
    if (leaving !== null) return;
    setLeaving(index);
    exitTimerRef.current = window.setTimeout(() => {
      exitTimerRef.current = null;
      setLeaving(null);
      onRemove(index);
    }, 140);
  };

  // Eine gestagte Skizze wieder zum Bearbeiten oeffnen: ALLE Bloecke ihrer
  // Gruppe (Mehransicht) einsammeln und als Editor-Overlay rekonstruieren.
  // "Fertig" ersetzt danach die Gruppe (reopenGroupId).
  const reopenSketch = (clicked: SketchBlock) => {
    const group = clicked.sketch_group;
    const members = group
      ? blocks.filter(
          (b): b is SketchBlock => b.type === "sketch" && b.sketch_group === group,
        )
      : [clicked];
    const overlay = buildSketchOverlayFromGroup(members, group);
    if (overlay) setSketchOverlay(overlay);
  };

  return (
    <div className="flex flex-wrap gap-2">
      {blocks.map((block, i) => (
        <div
          key={i}
          className={
            leaving === i
              ? "pointer-events-none animate-out fade-out-0 zoom-out-95 fill-mode-forwards duration-150 motion-reduce:animate-none"
              : "animate-in fade-in-0 zoom-in-95 duration-200 motion-reduce:animate-none"
          }
        >
          <AttachmentChip
            block={block}
            onRemove={() => handleRemove(i)}
            onReopen={
              block.type === "sketch" ? () => reopenSketch(block) : undefined
            }
          />
        </div>
      ))}
    </div>
  );
}

function AttachmentChip({
  block,
  onRemove,
  onReopen,
}: {
  block: ContentBlock;
  onRemove: () => void;
  onReopen?: () => void;
}) {
  if (block.type === "image") {
    return (
      <div className="relative">
        <PreviewImage
          source={block.source}
          alt="Anhang"
          className="h-16 w-16 rounded-xl border border-border/60 object-cover"
        />
        <button
          type="button"
          onClick={onRemove}
          aria-label="Entfernen"
          className="absolute -right-1 -top-1 flex h-4 w-4 cursor-pointer items-center justify-center rounded-full bg-card text-foreground shadow transition-colors duration-200 hover:bg-muted"
        >
          <X className="h-3 w-3" />
        </button>
      </div>
    );
  }
  if (block.type === "selection") {
    return (
      <div className="flex items-center gap-1.5 rounded-full border border-border/60 bg-card px-2.5 py-1 text-xs font-medium shadow-sm">
        <span>Auswahl: {selectionLabel(block)}</span>
        <button type="button" onClick={onRemove} aria-label="Entfernen" className="cursor-pointer">
          <X className="h-3 w-3 opacity-60 transition-opacity duration-200 hover:opacity-100" />
        </button>
      </div>
    );
  }
  if (block.type === "point_pick") {
    return (
      <div className="flex items-center gap-1.5 rounded-full border border-border/60 bg-card px-2.5 py-1 text-xs font-medium tabular-nums shadow-sm">
        <span>{pointPickLabel(block)}</span>
        <button type="button" onClick={onRemove} aria-label="Entfernen" className="cursor-pointer">
          <X className="h-3 w-3 opacity-60 transition-opacity duration-200 hover:opacity-100" />
        </button>
      </div>
    );
  }
  if (block.type === "component_pick") {
    return (
      <div className="flex items-center gap-1.5 rounded-full border border-border/60 bg-card px-2.5 py-1 text-xs font-medium shadow-sm">
        <span>{componentPickLabel(block)}</span>
        <button type="button" onClick={onRemove} aria-label="Entfernen" className="cursor-pointer">
          <X className="h-3 w-3 opacity-60 transition-opacity duration-200 hover:opacity-100" />
        </button>
      </div>
    );
  }
  if (block.type === "sketch") {
    // Zeige die BEMALTE Einzelansicht (rendered_png) auf der Kachel — das ist,
    // was der Designer gezeichnet hat. Das Composite (nur am ersten Block als
    // Multi-View-Kontext mitgegeben) wuerde UNBEMALT erscheinen, deshalb nur als
    // Fallback; bei einem strichlosen Snapshot IST rendered_png ohnehin das
    // Composite. background = Fallback fuer alte Zeilen ohne rendered_png.
    const preview = block.rendered_png ?? block.composite ?? block.background;
    return (
      <div className="relative">
        {preview ? (
          <PreviewImage
            source={preview}
            alt="Skizze"
            className="h-16 w-16 rounded-xl border border-border/60 object-cover"
            onActivate={onReopen}
            activateTitle="Zum Bearbeiten öffnen"
          />
        ) : (
          <div className="flex h-16 w-16 items-center justify-center rounded-xl border border-border/60 bg-muted/50 text-xs text-muted-foreground">
            Skizze
          </div>
        )}
        <button
          type="button"
          onClick={onRemove}
          aria-label="Entfernen"
          className="absolute -right-1 -top-1 flex h-4 w-4 cursor-pointer items-center justify-center rounded-full bg-card text-foreground shadow transition-colors duration-200 hover:bg-muted"
        >
          <X className="h-3 w-3" />
        </button>
      </div>
    );
  }
  return null;
}

function PreviewImage({
  source,
  alt,
  className,
  onActivate,
  activateTitle,
}: {
  source: ImageSource;
  alt: string;
  className?: string;
  // Wenn gesetzt: Klick loest diese Aktion aus (z.B. Skizze wieder oeffnen)
  // statt das Zoom-Overlay umzuschalten. Hover zeigt weiterhin die Vorschau.
  onActivate?: () => void;
  activateTitle?: string;
}) {
  const [expanded, setExpanded] = useState(false);
  const src = `data:${source.media_type};base64,${source.data}`;

  return (
    <div
      className="relative inline-block"
      onMouseEnter={() => setExpanded(true)}
      onMouseLeave={() => setExpanded(false)}
    >
      <button
        type="button"
        title={onActivate ? activateTitle : undefined}
        className={`block overflow-hidden rounded-xl focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/60 ${
          onActivate ? "cursor-pointer" : "cursor-zoom-in"
        }`}
        onClick={(event) => {
          event.stopPropagation();
          if (onActivate) {
            setExpanded(false);
            onActivate();
            return;
          }
          setExpanded((value) => !value);
        }}
      >
        <img src={src} alt={alt} className={className} />
      </button>
      {expanded && (
        <button
          type="button"
          className="absolute bottom-full left-0 z-50 mb-2 block cursor-zoom-out overflow-hidden rounded-xl bg-card shadow-pop animate-in fade-in-0 zoom-in-95"
          onClick={(event) => {
            event.stopPropagation();
            setExpanded(false);
          }}
        >
          <img
            src={src}
            alt={alt}
            className="max-h-72 max-w-[min(22rem,80vw)] rounded-xl object-contain"
          />
        </button>
      )}
    </div>
  );
}
