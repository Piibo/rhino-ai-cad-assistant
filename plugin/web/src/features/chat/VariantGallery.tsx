import { useCallback, useEffect, useRef, useState } from "react";
import { Box, ChevronDown, Images, Trash2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useChatStore } from "@/store/chatStore";
import { cn } from "@/lib/utils";
import type { Variant, WsCommand } from "@/lib/types";

interface VariantGalleryProps {
  onSend: (cmd: WsCommand) => boolean;
}

export function VariantGallery({ onSend }: VariantGalleryProps) {
  const variants = useChatStore((s) => s.variants);
  const activeVariantId = useChatStore((s) => s.activeVariantId);
  const activeSessionId = useChatStore((s) => s.activeSessionId);
  // Local collapse state. The component is mounted unconditionally and returns
  // null when the list is empty (it does NOT unmount), so `collapsed` would
  // otherwise persist across a clear -> new-batch cycle and hide the fresh
  // batch behind a collapsed header. Reset it on the empty -> non-empty edge.
  const [collapsed, setCollapsed] = useState(false);
  const prevCountRef = useRef(0);
  useEffect(() => {
    if (prevCountRef.current === 0 && variants.length > 0) {
      setCollapsed(false);
    }
    prevCountRef.current = variants.length;
  }, [variants.length]);

  const onSelect = useCallback(
    (name: string) => {
      if (!activeSessionId) return;
      onSend({
        type: "variant.select",
        payload: { session_id: activeSessionId, name },
      });
    },
    [activeSessionId, onSend],
  );

  const onDelete = useCallback(
    (name: string) => {
      if (!activeSessionId) return;
      onSend({
        type: "variant.delete",
        payload: { session_id: activeSessionId, name },
      });
    },
    [activeSessionId, onSend],
  );

  const onClearAll = useCallback(() => {
    if (!activeSessionId) return;
    onSend({
      type: "variants.clear",
      payload: { session_id: activeSessionId },
    });
  }, [activeSessionId, onSend]);

  // Commit the selected variant as THE main model (promote to Active) and close
  // the gallery — distinct from variants.clear (discard all).
  const onCommit = useCallback(() => {
    if (!activeSessionId) return;
    onSend({
      type: "variant.commit",
      payload: { session_id: activeSessionId },
    });
  }, [activeSessionId, onSend]);

  // Header close (×): with a variant selected, keep it as the main model;
  // showing the Original (no selection) means "discard all variants".
  const hasSelection = activeVariantId !== null;
  const onCloseGallery = useCallback(() => {
    if (hasSelection) onCommit();
    else onClearAll();
  }, [hasSelection, onCommit, onClearAll]);

  const onShowOriginal = useCallback(() => {
    if (!activeSessionId) return;
    onSend({
      type: "variant.show_original",
      payload: { session_id: activeSessionId },
    });
  }, [activeSessionId, onSend]);

  if (variants.length === 0) return null;

  return (
    <div className="px-3 py-2">
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-2 rounded-2xl bg-card p-3 shadow-panel">
        <div className="flex items-center justify-between">
          <span className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            <Images className="h-3.5 w-3.5 text-primary" />
            <span>Varianten ({variants.length})</span>
          </span>
          <div className="flex items-center gap-0.5">
            <Button
              variant="ghost"
              size="icon"
              className="h-7 w-7 rounded-full"
              onClick={() => setCollapsed((c) => !c)}
              aria-label={
                collapsed ? "Varianten-Panel ausklappen" : "Varianten-Panel einklappen"
              }
              title={collapsed ? "Ausklappen" : "Einklappen"}
            >
              {/* Panel sits at the top of the chat area: content folds
                  upward, so the chevron points up to collapse (expanded
                  state) and down to expand (collapsed state). Mirror
                  image of ParameterPanel, which is anchored at the
                  bottom — the opposite rotation there is intentional. */}
              <ChevronDown
                className={cn(
                  "h-3.5 w-3.5 transition-transform",
                  !collapsed && "rotate-180",
                )}
              />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className="h-7 w-7 rounded-full"
              onClick={onCloseGallery}
              aria-label={
                hasSelection
                  ? "Auswahl übernehmen und Galerie schließen"
                  : "Alle Varianten verwerfen"
              }
              title={
                hasSelection
                  ? "Auswahl übernehmen & schließen"
                  : "Alle Varianten verwerfen"
              }
            >
              <X className="h-3.5 w-3.5" />
            </Button>
          </div>
        </div>
        {/* Accordion ohne feste Höhe: grid-template-rows 0fr <-> 1fr, das
            overflow-hidden-Kind clippt die Thumbnails waehrend des Uebergangs.
            Inhalt bleibt gemountet (nur visuell kollabiert) — kein Effekt/
            Autofokus im Inneren. Die scrollbare Thumbnail-Reihe (max-h-56
            overflow-y-auto) bleibt als eigene Ebene erhalten. */}
        <div
          className={cn(
            "grid transition-[grid-template-rows] duration-200 ease-out motion-reduce:transition-none",
            collapsed ? "[grid-template-rows:0fr]" : "[grid-template-rows:1fr]",
          )}
        >
          <div
            className="overflow-hidden min-h-0"
            // inert im kollabierten Zustand: die Variant-Kacheln (Buttons)
            // duerfen eingeklappt keine unsichtbaren Tab-Stops sein.
            ref={(el) => {
              if (!el) return;
              if (collapsed) el.setAttribute("inert", "");
              else el.removeAttribute("inert");
            }}
          >
            <div className="-mx-1 flex flex-wrap gap-3 px-1 py-1 max-h-56 overflow-y-auto">
              <OriginalTile
                active={activeVariantId === null}
                onClick={onShowOriginal}
              />
              {variants.map((variant) => (
                <VariantThumb
                  key={variant.id}
                  variant={variant}
                  active={variant.id === activeVariantId}
                  onSelect={() => onSelect(variant.name)}
                  onDelete={() => onDelete(variant.name)}
                />
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

interface VariantThumbProps {
  variant: Variant;
  active: boolean;
  onSelect: () => void;
  onDelete: () => void;
}

interface OriginalTileProps {
  active: boolean;
  onClick: () => void;
}

function OriginalTile({ active, onClick }: OriginalTileProps) {
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onClick}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onClick();
        }
      }}
      className={cn(
        "group relative flex shrink-0 cursor-pointer flex-col gap-1.5 rounded-xl bg-muted/50 p-1.5 text-xs transition-shadow duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60",
        active ? "ring-2 ring-primary" : "hover:ring-2 hover:ring-primary/40",
      )}
      title="Originalzustand anzeigen"
    >
      <div className="flex h-20 w-28 items-center justify-center rounded-lg bg-muted text-muted-foreground">
        <Box className="h-7 w-7 opacity-60" />
      </div>
      <div className="flex items-center px-0.5">
        <span className="min-w-0 truncate rounded-full bg-card px-2 py-0.5 text-[11px] font-medium shadow-sm">
          Original
        </span>
      </div>
    </div>
  );
}

function VariantThumb({
  variant,
  active,
  onSelect,
  onDelete,
}: VariantThumbProps) {
  const thumbnail = variant.thumbnail;
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onSelect}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onSelect();
        }
      }}
      className={cn(
        "group relative flex shrink-0 cursor-pointer flex-col gap-1.5 rounded-xl bg-muted/50 p-1.5 text-xs transition-shadow duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60",
        active ? "ring-2 ring-primary" : "hover:ring-2 hover:ring-primary/40",
      )}
      title={variant.description || variant.name}
    >
      {thumbnail ? (
        <img
          src={`data:${thumbnail.media_type};base64,${thumbnail.data}`}
          alt={variant.name}
          className="h-20 w-28 rounded-lg object-cover animate-in fade-in-0 duration-300"
        />
      ) : (
        <div className="flex h-20 w-28 items-center justify-center rounded-lg bg-muted text-[10px] text-muted-foreground">
          (keine Vorschau)
        </div>
      )}
      <div className="flex items-center justify-between gap-1 px-0.5">
        <span className="min-w-0 truncate rounded-full bg-card px-2 py-0.5 text-[11px] font-medium shadow-sm">
          {variant.name}
        </span>
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onDelete();
          }}
          aria-label={`Variante ${variant.name} löschen`}
          title="Variante löschen"
          className="shrink-0 cursor-pointer rounded-full p-0.5 text-muted-foreground opacity-0 transition-opacity duration-200 hover:text-destructive group-hover:opacity-100 focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
        >
          <Trash2 className="h-3 w-3" />
        </button>
      </div>
    </div>
  );
}
