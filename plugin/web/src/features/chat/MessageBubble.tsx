import { memo, useState, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Box, BoxSelect, Crosshair, Spline, Square } from "lucide-react";
import { cn } from "@/lib/utils";
import { useCondition } from "@/hooks/useCondition";
import type {
  ContentBlock,
  ImageSource,
  Message,
  ToolResultBlock,
  ToolUseBlock,
  WsCommand,
} from "@/lib/types";
import { componentPickLabel } from "@/lib/component-pick-label";
import { pointPickLabel } from "@/lib/point-pick-label";
import { selectionLabel } from "@/lib/selection-label";
import { getImageSources, redactLargeData } from "@/lib/tool-result";
import { ToolCallGroup, type ToolCall } from "./ToolCallGroup";
import { QuickReplyCard, isDialogToolUseName } from "./QuickReplyCard";

interface MessageBubbleProps {
  message: Message;
  // tool_use_id -> tool_result, built by ChatView across ALL messages.
  // The agent persists tool_use (assistant msg) and tool_result (a
  // separate user msg) apart, so a tool call can only be paired with
  // its result via this cross-message lookup.
  toolResults?: Map<string, ToolResultBlock>;
  latestDialogToolUseId?: string | null;
  onSend?: (cmd: WsCommand) => boolean;
}

// Stabile Referenz: ein Inline-Array wuerde ReactMarkdown bei jedem Render
// als "neue" Plugin-Liste erscheinen und Re-Parses beguenstigen.
const REMARK_PLUGINS = [remarkGfm];

function MessageBubbleImpl({
  message,
  toolResults,
  latestDialogToolUseId = null,
  onSend,
}: MessageBubbleProps) {
  const isUser = message.role === "user";
  // Tool-Call-Cards are a werkzeug-only UI surface (Studienartefakt-Spec
  // §1.1). In the basis condition the run of tool_use blocks is skipped
  // entirely so the model's actions stay invisible to the participant.
  // This makes MessageBubble a fourth conditional-render site beyond the
  // §1.3-listed three; documented as an intentional deviation in the
  // commit message.
  const condition = useCondition();
  const showToolCalls = condition === "werkzeug";
  // Gesendete Skizzen sind statisch (kein Reopen, kein Hover-Zoom) — das
  // Bearbeiten passiert vor dem Senden ueber die gestagte Kachel im Composer.
  return (
    <div
      className={cn(
        "flex gap-3 py-2 animate-in fade-in-0 duration-300 motion-reduce:animate-none",
        isUser ? "justify-end" : "justify-start",
      )}
    >
      <div
        className={cn(
          // space-y-2 gibt den Blöcken in der Bubble (Chips-Zeile, Text,
          // Tool-Karten, Bilder) einen einheitlichen vertikalen Rhythmus.
          "min-w-0 max-w-[80%] space-y-2 rounded-2xl text-sm",
          // User-Bubble: blau gefüllt (Akzent), keine Hairline — die Farbe
          // trennt sie schon vom Canvas. Assistant: kein Rahmen, keine
          // Füllung — nur Text auf dem weißen Canvas (Claude-Stil), daher
          // auch nur vertikales Padding statt Box-Innenabstand.
          // Markdown-Links/Code erben sonst Browser-Defaults (kein
          // typography-Plugin; prose-* ist wirkungslos).
          isUser
            ? "rounded-br-md bg-primary p-3.5 text-primary-foreground [&_a]:font-medium [&_a]:text-primary-foreground [&_a]:underline [&_code]:rounded [&_code]:bg-primary-foreground/15 [&_code]:px-1 [&_pre]:overflow-x-auto [&_pre]:rounded-lg [&_pre]:bg-primary-foreground/15 [&_pre]:p-2"
            : "py-1 text-foreground [&_a]:font-medium [&_a]:text-primary [&_a]:underline [&_code]:rounded [&_code]:bg-muted [&_code]:px-1 [&_pre]:overflow-x-auto [&_pre]:rounded-lg [&_pre]:bg-muted [&_pre]:p-2",
        )}
      >
        {renderBlocks(
          message.content,
          toolResults,
          showToolCalls,
          latestDialogToolUseId,
          onSend,
        )}
      </div>
    </div>
  );
}

// Memo mit massgeschneidertem Vergleich: Message-Objekte sind im Store
// append-only (stabile Referenzen), aber die toolResults-Map bekommt bei
// jedem Append eine neue Identitaet. Verglichen werden deshalb nur die
// Result-Eintraege der EIGENEN tool_use-Bloecke — historische Bubbles
// re-rendern (und re-parsen ihr Markdown) damit nicht bei jedem neuen Turn.
function sameOwnToolResults(
  prev: MessageBubbleProps,
  next: MessageBubbleProps,
): boolean {
  for (const block of next.message.content) {
    if (block.type !== "tool_use") continue;
    const id = (block as ToolUseBlock).id;
    if (!id) continue;
    if (prev.toolResults?.get(id) !== next.toolResults?.get(id)) return false;
  }
  return true;
}

export const MessageBubble = memo(
  MessageBubbleImpl,
  (prev, next) =>
    prev.message === next.message &&
    prev.latestDialogToolUseId === next.latestDialogToolUseId &&
    prev.onSend === next.onSend &&
    sameOwnToolResults(prev, next),
);
MessageBubble.displayName = "MessageBubble";

// Pick-/Auswahl-Chips sind Inline-Pills; aufeinanderfolgende Chips werden
// zu einer flex-wrap-Zeile gruppiert, damit sie sauberen Abstand
// zueinander (gap) und zum Folgetext (space-y der Bubble) bekommen.
const CHIP_BLOCK_TYPES = new Set(["selection", "point_pick", "component_pick"]);

// Bilder + Skizzen einer Nachricht werden zu EINER Galerie-Zeile gruppiert
// (statt jedes als eigener, voll breiter Block gestapelt -> wirkte wie zwei
// separate Nachrichten). Zwei gemalte Ansichten erscheinen so nebeneinander
// in derselben Bubble. Jedes Bild vergroessert sich beim Hovern (HoverZoomImage).
const MEDIA_BLOCK_TYPES = new Set(["image", "sketch"]);

function renderBlocks(
  blocks: ContentBlock[],
  toolResults: Map<string, ToolResultBlock> | undefined,
  showToolCalls: boolean,
  latestDialogToolUseId: string | null,
  onSend: ((cmd: WsCommand) => boolean) | undefined,
): ReactNode[] {
  const nodes: ReactNode[] = [];
  let i = 0;

  while (i < blocks.length) {
    const block = blocks[i];

    if (CHIP_BLOCK_TYPES.has(block.type)) {
      const chips: ReactNode[] = [];
      while (i < blocks.length && CHIP_BLOCK_TYPES.has(blocks[i].type)) {
        chips.push(<BlockRenderer key={i} block={blocks[i]} />);
        i += 1;
      }
      nodes.push(
        // Chips fluchten links mit dem Nachrichtentext (Rahmen am
        // Bubble-Innenrand). Ein vorangestelltes Icon macht sie klar als
        // Anhang lesbar, sodass die Pillen-Innenpolsterung nicht als
        // verrutschter Text wirkt.
        <div key={`chips-${i}`} className="flex flex-wrap gap-1.5">
          {chips}
        </div>,
      );
      continue;
    }

    // Run of consecutive image/sketch blocks -> one side-by-side gallery, so
    // mehrere gemalte Ansichten in EINER Nachricht zusammen erscheinen.
    if (MEDIA_BLOCK_TYPES.has(block.type)) {
      const media: ReactNode[] = [];
      const start = i;
      while (i < blocks.length && MEDIA_BLOCK_TYPES.has(blocks[i].type)) {
        media.push(
          <MediaBlock key={i} block={blocks[i]} />,
        );
        i += 1;
      }
      nodes.push(
        <div key={`media-${start}`} className="flex flex-wrap gap-2">
          {media}
        </div>,
      );
      continue;
    }

    // Run of consecutive tool_use blocks -> one collapsed ToolCallGroup,
    // or skipped entirely in the basis condition.
    if (block.type === "tool_use") {
      if (!showToolCalls) {
        while (i < blocks.length && blocks[i].type === "tool_use") {
          i += 1;
        }
        continue;
      }

      if (isDialogToolUseName(block.name) && onSend) {
        const use = block as ToolUseBlock;
        nodes.push(
          <QuickReplyCard
            key={`qr-${use.id ?? i}`}
            toolUse={use}
            active={use.id === latestDialogToolUseId}
            onSend={onSend}
          />,
        );
        i += 1;
        continue;
      }

      const calls: ToolCall[] = [];
      while (i < blocks.length && blocks[i].type === "tool_use") {
        const use = blocks[i] as ToolUseBlock;
        const result =
          (use.id ? toolResults?.get(use.id) : undefined) ??
          findSameMessageResult(blocks, use.id);
        calls.push({ use, result });
        i += 1;
      }
      nodes.push(<ToolCallGroup key={`tg-${i}`} calls={calls} />);
      continue;
    }

    // tool_result blocks are surfaced inside their ToolCallGroup (paired
    // via the cross-message map). A bare tool_result here would just be
    // a duplicate raw JSON dump — skip it.
    if (block.type === "tool_result") {
      i += 1;
      continue;
    }

    nodes.push(<BlockRenderer key={i} block={block} />);
    i += 1;
  }

  return nodes;
}

// Fallback for the rare case a tool_use and its tool_result sit in the
// same message (older transcripts / defensive).
function findSameMessageResult(
  blocks: ContentBlock[],
  toolUseId: string | undefined,
): ToolResultBlock | undefined {
  if (!toolUseId) return undefined;
  for (const b of blocks) {
    if (b.type === "tool_result" && b.tool_use_id === toolUseId) return b;
  }
  return undefined;
}

function BlockRenderer({ block }: { block: ContentBlock }) {
  switch (block.type) {
    case "text":
      return (
        <div
          className={cn(
            "prose prose-sm max-w-none min-w-0 break-words overflow-x-auto leading-relaxed",
            "prose-p:my-1 prose-pre:my-2 prose-pre:bg-background/50 prose-pre:max-w-full",
          )}
        >
          <ReactMarkdown remarkPlugins={REMARK_PLUGINS}>{block.text}</ReactMarkdown>
        </div>
      );
    case "image":
      return <MediaBlock block={block} />;
    case "tool_use":
      // handled by renderBlocks pairing, but render defensively
      return (
        <div className="mt-1.5 rounded-lg bg-muted/70 px-2.5 py-1.5 font-mono text-xs text-foreground">
          <span className="opacity-60">-&gt; {block.name}</span>
        </div>
      );
    case "tool_result": {
      // Orphan result: no matching tool_use in the same message.
      const imageSources = getImageSources(block.content);
      if (imageSources.length > 0) {
        return (
          <div className="mt-1.5 flex flex-wrap gap-2 rounded-xl bg-muted/50 p-2">
            {imageSources.map((source, i) => (
              <HoverZoomImage
                key={i}
                source={source}
                alt="Tool result"
                thumbClassName="max-h-40 rounded-lg"
              />
            ))}
          </div>
        );
      }

      return (
        <div
          className={cn(
            "mt-1.5 whitespace-pre-wrap break-all overflow-x-auto max-w-full rounded-lg bg-muted/70 px-2.5 py-1.5 font-mono text-xs text-foreground",
            block.is_error && "bg-destructive/10 text-destructive",
          )}
        >
          {typeof block.content === "string"
            ? block.content
            : JSON.stringify(redactLargeData(block.content), null, 2)}
        </div>
      );
    }
    case "sketch":
      return <MediaBlock block={block} />;
    case "selection":
      return (
        <div className="inline-flex max-w-full items-center gap-1.5 rounded-[7px] border border-border/60 bg-card px-2 py-1 text-xs font-medium text-foreground shadow-sm">
          <BoxSelect className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          <span className="min-w-0 truncate">Auswahl: {selectionLabel(block)}</span>
        </div>
      );
    case "point_pick":
      return (
        <div className="inline-flex max-w-full items-center gap-1.5 rounded-[7px] border border-border/60 bg-card px-2 py-1 text-xs font-medium text-foreground tabular-nums shadow-sm">
          <Crosshair className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          <span className="min-w-0 truncate">{pointPickLabel(block)}</span>
        </div>
      );
    case "component_pick": {
      // Icon nach Komponententyp: Kante = Spline, Flaeche = Quadrat, Objekt =
      // Wuerfel. (Vertex gibt's ueber K/F/O nicht mehr; Crosshair bleibt dem
      // Punkt-Pick.) Muss mit componentTypeIcon in InputBar uebereinstimmen.
      const CompIcon =
        block.component_type === "face"
          ? Square
          : block.component_type === "object"
            ? Box
            : Spline;
      return (
        <div className="inline-flex max-w-full items-center gap-1.5 rounded-[7px] border border-border/60 bg-card px-2 py-1 text-xs font-medium text-foreground shadow-sm">
          <CompIcon className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          <span className="min-w-0 truncate">{componentPickLabel(block)}</span>
        </div>
      );
    }
    default:
      return null;
  }
}

// Bild- ODER Skizzen-Block als zoombares Thumbnail. Bevorzugt das gebackene
// annotierte View (rendered_png), sonst das Multi-View-Composite; faellt fuer
// alte DB-Zeilen ohne gebackenes PNG auf das Hintergrund+SVG-Overlay zurueck.
function MediaBlock({ block }: { block: ContentBlock }) {
  if (block.type === "image") {
    return <HoverZoomImage source={block.source} alt="" />;
  }
  if (block.type === "sketch") {
    // Gesendete Skizze = statisch: kein Reopen (eine gesendete Skizze laesst
    // sich nicht mehr aendern) und kein Hover-Zoom (im schmalen Panel ~gleich
    // gross -> nutzlos). Bearbeitet wird vor dem Senden ueber die gestagte
    // Kachel im Composer.
    const baked = block.rendered_png ?? block.composite;
    if (baked) {
      return (
        <img
          src={`data:${baked.media_type};base64,${baked.data}`}
          alt="Skizze"
          className="max-h-48 rounded-xl object-contain animate-in fade-in-0 duration-300 motion-reduce:animate-none"
        />
      );
    }
    return (
      <div
        className="relative overflow-hidden rounded-xl border border-border/60 bg-muted/40"
        style={{ aspectRatio: `${block.width} / ${block.height}` }}
      >
        {block.background && (
          <img
            src={`data:${block.background.media_type};base64,${block.background.data}`}
            alt=""
            className="absolute inset-0 h-full w-full object-contain"
          />
        )}
        <div
          className="absolute inset-0"
          dangerouslySetInnerHTML={{ __html: block.svg }}
        />
      </div>
    );
  }
  return null;
}

// Thumbnail, das beim Hovern eine grosse, mittig schwebende Vorschau zeigt,
// damit man das Bild nochmal genau anschauen kann. Fixed + zentriert ->
// wird nie vom Chat-Scrollcontainer abgeschnitten; pointer-events-none, damit
// das mouseleave am Thumbnail zuverlaessig feuert.
function HoverZoomImage({
  source,
  alt,
  thumbClassName,
}: {
  source: ImageSource;
  alt?: string;
  thumbClassName?: string;
}) {
  const [zoom, setZoom] = useState(false);
  const src = `data:${source.media_type};base64,${source.data}`;
  return (
    <>
      <img
        src={src}
        alt={alt ?? ""}
        onMouseEnter={() => setZoom(true)}
        onMouseLeave={() => setZoom(false)}
        className={cn(
          "cursor-zoom-in rounded-xl object-contain animate-in fade-in-0 duration-300 motion-reduce:animate-none",
          thumbClassName ?? "max-h-48",
        )}
      />
      {zoom && (
        <div className="pointer-events-none fixed inset-0 z-[60] flex items-center justify-center p-6">
          <img
            src={src}
            alt={alt ?? ""}
            className="max-h-[85vh] max-w-[85vw] rounded-2xl bg-card object-contain shadow-pop ring-1 ring-border animate-in fade-in-0 zoom-in-95"
          />
        </div>
      )}
    </>
  );
}
