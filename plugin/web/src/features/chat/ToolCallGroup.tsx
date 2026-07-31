import { memo, useState } from "react";
import { ChevronRight, Wrench, X } from "lucide-react";
import { cn } from "@/lib/utils";
import type { ImageSource, ToolResultBlock, ToolUseBlock } from "@/lib/types";
import { ToolCallCard } from "./ToolCallCard";

export interface ToolCall {
  use: ToolUseBlock;
  result?: ToolResultBlock;
}

interface ToolCallGroupProps {
  calls: ToolCall[];
}

/**
 * Collapses one assistant turn's tool calls into a single subtle,
 * foldable row.
 *
 * A Grasshopper build fires 10-30 tool calls; rendered individually
 * (and worse, with their raw ``execute_gh_code`` input and
 * ``expire_and_get_info`` JSON dumps) they bury the actual conversation
 * — the designer's prompt and the assistant's answer. Designers don't
 * act on that plumbing, so the group is collapsed by default. The
 * detail (per-call ToolCallCards, themselves expandable) stays one
 * click away for anyone who wants to audit what the agent did.
 *
 * Exception: image results (viewport screenshots) ARE a useful visual
 * result, so they surface even while the group is collapsed.
 */
function ToolCallGroupImpl({ calls }: ToolCallGroupProps) {
  const [expanded, setExpanded] = useState(false);

  if (calls.length === 0) return null;

  const errorCount = calls.filter((c) => c.result?.is_error === true).length;
  const images = calls.flatMap((c) => imageSourcesOf(c.result));
  const suffix = calls.length === 1 ? "Werkzeug-Schritt" : "Werkzeug-Schritte";

  return (
    // Werkzeug-Schritte bekommen einen Rahmen (KI-Text-Antworten bewusst
    // nicht). Liegt IN der Group, damit inline und standalone
    // (ToolRunBubble) identisch aussehen. w-fit = content-breit, damit
    // der eingeklappte Toggle nicht je nach Textbreite variiert. border
    // statt shadow-panel: robuster im Scroll (wird nicht abgeschnitten).
    <div className="mt-1.5 w-fit max-w-full rounded-2xl border border-border/60 p-1.5 first:mt-0">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full cursor-pointer items-center gap-1.5 rounded-xl px-2 py-1.5 text-xs text-muted-foreground transition-colors duration-200 hover:bg-muted/60 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
        aria-label={
          expanded ? "Werkzeug-Schritte einklappen" : "Werkzeug-Schritte ausklappen"
        }
        title={expanded ? "Werkzeug-Schritte einklappen" : "Werkzeug-Schritte ausklappen"}
      >
        <ChevronRight
          className={cn(
            "h-3.5 w-3.5 shrink-0 transition-transform duration-200",
            expanded && "rotate-90",
          )}
        />
        <Wrench className="h-3.5 w-3.5 shrink-0" />
        <span className="flex items-baseline gap-1 font-medium">
          <RollingNumber value={calls.length} />
          <span>{suffix}</span>
        </span>
        {errorCount > 0 && (
          <span className="inline-flex items-center gap-0.5 rounded-full bg-destructive/10 px-2 py-0.5 text-[11px] font-medium text-destructive">
            <X className="h-3 w-3" />
            {errorCount}
          </span>
        )}
      </button>

      {/* Viewport screenshots stay visible even collapsed. */}
      {images.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-2 px-1 pb-1">
          {images.map((src, i) => (
            <img
              key={i}
              src={`data:${src.media_type};base64,${src.data}`}
              alt="Viewport"
              className="max-h-48 rounded-xl object-contain shadow-sm"
            />
          ))}
        </div>
      )}

      {expanded && (
        <div className="mt-1.5 space-y-1.5">
          {calls.map((c, i) => (
            <ToolCallCard key={c.use.id ?? i} use={c.use} result={c.result} />
          ))}
        </div>
      )}
    </div>
  );
}

// Memo mit Inhalts-Vergleich: das calls-Array wird von den Aufrufern bei
// jedem Render neu gebaut, seine Eintraege (use/result-Bloecke) sind aber
// referenzstabil (append-only Store). Historische Gruppen re-rendern so
// nicht bei jedem neuen Turn.
export const ToolCallGroup = memo(
  ToolCallGroupImpl,
  (prev, next) =>
    prev.calls.length === next.calls.length &&
    prev.calls.every(
      (c, i) => c.use === next.calls[i].use && c.result === next.calls[i].result,
    ),
);
ToolCallGroup.displayName = "ToolCallGroup";

function RollingNumber({ value }: { value: number }) {
  return (
    <span className="inline-flex h-[1.05em] min-w-[1ch] overflow-hidden tabular-nums">
      <span key={value} className="animate-count-roll leading-none">
        {value}
      </span>
    </span>
  );
}

function imageSourcesOf(result: ToolResultBlock | undefined): ImageSource[] {
  if (!result || !Array.isArray(result.content)) return [];
  return result.content.flatMap((item) => {
    if (item.type !== "image") return [];
    const source = item.source;
    if (!source || typeof source !== "object") return [];
    const src = source as Partial<ImageSource>;
    if (
      src.type === "base64" &&
      typeof src.media_type === "string" &&
      typeof src.data === "string"
    ) {
      return [source as ImageSource];
    }
    return [];
  });
}
