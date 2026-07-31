import { useState, type ReactNode } from "react";
import { Check, ChevronRight, Wrench, X } from "lucide-react";
import { cn } from "@/lib/utils";
import type { ToolResultBlock, ToolUseBlock } from "@/lib/types";
import { getImageSources, redactLargeData } from "@/lib/tool-result";

interface ToolCallCardProps {
  use: ToolUseBlock;
  result?: ToolResultBlock;
}

export function ToolCallCard({ use, result }: ToolCallCardProps) {
  const [expanded, setExpanded] = useState(false);

  const hasResult = result !== undefined;
  const isError = result?.is_error === true;
  const imageResults = result ? getImageSources(result.content) : [];
  const hasImageResults = imageResults.length > 0;
  const resultText = result ? resultTextForDisplay(result.content) : "";

  const preview = result
    ? hasImageResults
      ? `${imageResults.length} Bild${imageResults.length === 1 ? "" : "er"}`
      : resultPreview(resultText)
    : "";

  const inputKeys = Object.keys(use.input ?? {});
  const hasInput = inputKeys.length > 0;

  return (
    <div
      className={cn(
        // Transparente Karte mit Hairline-Border statt weißer Füllung:
        // vermeidet das grau-weiß-grau-Schichten in der Bubble. Grau
        // kommt nur noch bei den Code-Blöcken (den eigentlichen Daten).
        "overflow-hidden rounded-xl border border-border/60 text-xs",
        "animate-in fade-in-0 slide-in-from-bottom-1 duration-200 motion-reduce:animate-none",
        isError && "border-destructive/40",
      )}
    >
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full cursor-pointer items-center gap-1.5 px-2.5 py-2 text-left transition-colors duration-200 hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
      >
        <ChevronRight
          className={cn(
            "h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform duration-200",
            expanded && "rotate-90",
          )}
        />
        <Wrench className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        <span className="font-mono text-xs font-medium text-foreground">{use.name}</span>
        {hasResult ? (
          isError ? (
            <span className="inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-destructive/10">
              <X className="h-3 w-3 text-destructive" />
            </span>
          ) : (
            <span className="inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-success-soft">
              <Check className="h-3 w-3 text-success" />
            </span>
          )
        ) : (
          <span className="flex shrink-0 items-center gap-1.5 italic text-muted-foreground">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-primary" />
            läuft…
          </span>
        )}
        {!expanded && preview && (
          <span className="min-w-0 flex-1 truncate font-mono text-muted-foreground">
            {preview}
          </span>
        )}
      </button>

      {/* Accordion ohne feste Höhe: der Grid-Wrapper faehrt grid-template-rows
          von 0fr -> 1fr, das overflow-hidden-Kind clippt den Inhalt waehrenddessen.
          Inhalt bleibt gemountet (nur visuell kollabiert) — keine Effekte/Autofokus
          im Detail-Block, daher unproblematisch. */}
      <div
        className={cn(
          "grid transition-[grid-template-rows] duration-200 ease-out motion-reduce:transition-none",
          expanded ? "[grid-template-rows:1fr]" : "[grid-template-rows:0fr]",
        )}
      >
        <div
          className="overflow-hidden min-h-0"
          // inert im eingeklappten Zustand: kein unsichtbarer Fokus/Klick in
          // den dauergemounteten Details.
          ref={(el) => {
            if (!el) return;
            if (expanded) el.removeAttribute("inert");
            else el.setAttribute("inert", "");
          }}
        >
          <div className="space-y-2.5 border-t border-border/60 px-2.5 py-2.5">
            {hasInput && (
              <Section label="Input">
                <CollapsiblePre className="overflow-x-auto whitespace-pre-wrap rounded-lg bg-muted/60 px-2.5 py-1.5 font-mono text-[11px]">
                  {JSON.stringify(use.input, null, 2)}
                </CollapsiblePre>
              </Section>
            )}
            {hasResult && hasImageResults && (
              <Section label="Bild">
                <div className="flex flex-wrap gap-2">
                  {imageResults.map((source, i) => (
                    <img
                      key={i}
                      src={`data:${source.media_type};base64,${source.data}`}
                      alt="Viewport"
                      className="max-h-40 rounded-lg object-contain shadow-sm animate-in fade-in-0 duration-300"
                    />
                  ))}
                </div>
              </Section>
            )}
            {hasResult && !hasImageResults && resultText && (
              <Section label={isError ? "Fehler" : "Ergebnis"}>
                <CollapsiblePre
                  className={cn(
                    "overflow-x-auto whitespace-pre-wrap rounded-lg bg-muted/60 px-2.5 py-1.5 font-mono text-[11px]",
                    isError && "bg-destructive/10 text-destructive",
                  )}
                >
                  {resultText}
                </CollapsiblePre>
              </Section>
            )}
            {hasResult && !hasImageResults && !resultText && (
              <p className="text-[11px] italic text-muted-foreground">
                (leere Antwort)
              </p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

const COLLAPSE_MAX_LINES = 15;
const COLLAPSE_MAX_CHARS = 1200;

/**
 * <pre>-Block, der lange Inhalte kappt: mehr als 15 Zeilen ODER mehr als
 * 1200 Zeichen -> nur die ersten 15 Zeilen bzw. 1200 Zeichen (an Zeilengrenze
 * geschnitten) mit Fade-Overlay und Toggle. Kurze Inhalte rendern exakt wie
 * ein einfaches <pre> (kein Button, kein Overlay).
 */
function CollapsiblePre({
  className,
  children,
}: {
  className?: string;
  children: string;
}) {
  const [showAll, setShowAll] = useState(false);

  const lines = children.split("\n");
  const isLong =
    lines.length > COLLAPSE_MAX_LINES || children.length > COLLAPSE_MAX_CHARS;

  if (!isLong) {
    return <pre className={className}>{children}</pre>;
  }

  // An Zeilengrenze schneiden: erst auf 15 Zeilen begrenzen, das Ergebnis
  // dann zusaetzlich auf 1200 Zeichen (bis zur letzten Zeilengrenze) kappen.
  let clipped = lines.slice(0, COLLAPSE_MAX_LINES).join("\n");
  if (clipped.length > COLLAPSE_MAX_CHARS) {
    const head = clipped.slice(0, COLLAPSE_MAX_CHARS);
    const lastBreak = head.lastIndexOf("\n");
    clipped = lastBreak > 0 ? head.slice(0, lastBreak) : head;
  }

  const hiddenLines = lines.length - clipped.split("\n").length;

  return (
    <div>
      <div className="relative">
        <pre className={className}>{showAll ? children : clipped}</pre>
        {!showAll && (
          <div className="pointer-events-none absolute inset-x-0 bottom-0 h-8 rounded-b-lg bg-gradient-to-t from-muted/80 to-transparent" />
        )}
      </div>
      <button
        type="button"
        onClick={() => setShowAll((v) => !v)}
        className="mt-1 cursor-pointer text-xs text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
      >
        {showAll
          ? "Weniger anzeigen"
          : `Mehr anzeigen (+${hiddenLines} Zeile${hiddenLines === 1 ? "" : "n"})`}
      </button>
    </div>
  );
}

function resultPreview(text: string): string {
  // Kurz halten: die nowrap-Vorschau traegt sonst auch nach dem
  // ScrollArea-Fix unnoetig viel Min-Content in schmale Panels.
  const firstLine = text.split("\n")[0] ?? "";
  if (firstLine.length <= 60 && !text.includes("\n")) return firstLine;
  return `${firstLine.slice(0, 60)}...`;
}

function resultTextForDisplay(content: ToolResultBlock["content"]): string {
  if (typeof content === "string") return content;
  return JSON.stringify(redactLargeData(content), null, 2);
}

function Section({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <div>
      <div className="mb-0.5 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
        {label}
      </div>
      {children}
    </div>
  );
}
