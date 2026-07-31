import { useEffect, useMemo, useRef, type ReactNode } from "react";
import { MessageSquareText } from "lucide-react";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useCondition } from "@/hooks/useCondition";
import { useChatStore } from "@/store/chatStore";
import type { Message, ToolResultBlock, ToolUseBlock, WsCommand } from "@/lib/types";
import { GatePreviewCard } from "./GatePreviewCard";
import { MessageBubble } from "./MessageBubble";
import { QuickReplyCard, isDialogToolUseName } from "./QuickReplyCard";
import { TypingIndicatorPresence } from "./TypingIndicator";
import { ToolCallGroup, type ToolCall } from "./ToolCallGroup";

interface ChatViewProps {
  onSend: (cmd: WsCommand) => boolean;
}

export function ChatView({ onSend }: ChatViewProps) {
  const messages = useChatStore((s) => s.messages);
  const connected = useChatStore((s) => s.connected);
  const pendingSend = useChatStore((s) => s.pendingSend);
  const pendingGate = useChatStore((s) => s.pendingGate);
  const showWerkzeugSlots = useCondition() === "werkzeug";
  // Wenn Anhänge gestaged werden, wächst die InputBar nach oben und der
  // Chat-Bereich (flex-1) schrumpft von unten — ohne Nachscrollen würde
  // die letzte Nachricht hinter den Chips verschwinden. Die Anzahl
  // fließt deshalb in die Auto-Scroll-Abhängigkeiten ein.
  const stagedCount = useChatStore((s) => s.stagedAttachments.length);
  const bottomRef = useRef<HTMLDivElement>(null);

  // Typing is tied to pendingSend only — the agent clears that on
  // message.complete/message.error and on WS reconnect. Basing it on
  // "last message is a user turn" (e.g. tool_result) falsely lights up
  // after a page reload: the DB's last row is the tool_result from a
  // previous, already-finished (or crashed) run, not live activity.
  const showTyping = connected && pendingSend;

  // Pair tool calls with their results across messages — the agent
  // persists tool_use (assistant msg) and tool_result (a separate user
  // msg) apart, so MessageBubble can only resolve a call's result via
  // this document-wide lookup.
  const toolResults = useMemo(() => {
    const map = new Map<string, ToolResultBlock>();
    for (const msg of messages) {
      for (const block of msg.content) {
        if (
          block.type === "tool_result" &&
          typeof block.tool_use_id === "string"
        ) {
          map.set(block.tool_use_id, block);
        }
      }
    }
    return map;
  }, [messages]);
  const pendingDialogToolUseId = useMemo(
    () => findPendingDialogToolUseId(messages),
    [messages],
  );

  // Element-Reuse: die Nachrichten-Elemente werden nur neu gebaut, wenn sich
  // ihre Eingaben aendern — Store-Aenderungen wie pendingSend/stagedAttachments
  // re-rendern dann NICHT die gesamte History (React bailt bei identischen
  // Element-Referenzen aus dem Subtree aus).
  const messageNodes = useMemo(
    () =>
      renderMessageItems(
        messages,
        toolResults,
        pendingDialogToolUseId,
        onSend,
        showWerkzeugSlots,
      ),
    [messages, toolResults, pendingDialogToolUseId, onSend, showWerkzeugSlots],
  );

  useEffect(() => {
    // Ein kurzes Delay stellt sicher, dass React (und Markdown/Bilder)
    // das Layout fertig berechnet hat, bevor gescrollt wird.
    const timer = setTimeout(() => {
      bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
    }, 100);
    return () => clearTimeout(timer);
  }, [messages.length, showTyping, stagedCount, pendingGate]);

  // Horizontales Padding gehört auf den scrollenden Inhalt (das innere
  // div), NICHT auf die ScrollArea-Root — sonst klebt der Inhalt an der
  // Viewport-Kante und die 1px-Hairline-Rahmen (box-shadow) der Karten
  // werden links/rechts vom overflow abgeschnitten.
  return (
    <ScrollArea className="flex-1">
      <div className="mx-auto max-w-3xl px-3 py-4 pb-6 sm:px-4">
        {messages.length === 0 ? (
          <EmptyState showWerkzeugSlots={showWerkzeugSlots} />
        ) : (
          messageNodes
        )}
        {/* Part C — Gate-Karte: nur in werkzeug-Bedingung (gate.preview kommt
            ohnehin nur dort vom Backend, aber zur Sicherheit gated). */}
        {showWerkzeugSlots && pendingGate && (
          <div className="flex justify-start gap-3 py-2">
            <div className="min-w-0 w-full max-w-[85%]">
              <GatePreviewCard gate={pendingGate} onSend={onSend} />
            </div>
          </div>
        )}
        <TypingIndicatorPresence show={showTyping} />
        <div ref={bottomRef} />
      </div>
    </ScrollArea>
  );
}

function renderMessageItems(
  messages: Message[],
  toolResults: Map<string, ToolResultBlock>,
  pendingDialogToolUseId: string | null,
  onSend: (cmd: WsCommand) => boolean,
  showWerkzeugSlots: boolean,
) {
  const nodes: ReactNode[] = [];
  let i = 0;

  while (i < messages.length) {
    const message = messages[i];

    if (isToolResultCarrier(message)) {
      i += 1;
      continue;
    }

    if (isDialogToolOnlyAssistantMessage(message)) {
      if (!showWerkzeugSlots) {
        i += 1;
        continue;
      }
      const dialogBlocks = message.content.filter(
        (block): block is ToolUseBlock =>
          block.type === "tool_use" && isDialogToolUseName(block.name),
      );
      nodes.push(
        <DialogToolBubble
          key={`dialog-${message.id}`}
          toolUses={dialogBlocks}
          pendingDialogToolUseId={pendingDialogToolUseId}
          onSend={onSend}
        />,
      );
      i += 1;
      continue;
    }

    if (isToolOnlyAssistantMessage(message)) {
      const calls: ToolCall[] = [];
      const firstMessageId = message.id;

      while (i < messages.length) {
        const current = messages[i];
        if (isToolResultCarrier(current)) {
          i += 1;
          continue;
        }
        if (!isToolOnlyAssistantMessage(current)) break;
        calls.push(...toolCallsFromMessage(current, toolResults));
        i += 1;
      }

      if (showWerkzeugSlots) {
        nodes.push(
          <ToolRunBubble
            key={`tool-run-${firstMessageId}`}
            calls={calls}
          />,
        );
      }
      continue;
    }

    nodes.push(
      <MessageBubble
        key={message.id}
        message={message}
        toolResults={toolResults}
          latestDialogToolUseId={pendingDialogToolUseId}
        onSend={onSend}
      />,
    );
    i += 1;
  }

  return nodes;
}

function DialogToolBubble({
  toolUses,
  pendingDialogToolUseId,
  onSend,
}: {
  toolUses: ToolUseBlock[];
  pendingDialogToolUseId: string | null;
  onSend: (cmd: WsCommand) => boolean;
}) {
  return (
    <div className="flex justify-start gap-3 py-2 animate-in fade-in-0 duration-300 motion-reduce:animate-none">
      {/* Kein eigenes Card-Chrome: die QuickReplyCards bringen ihre
          weiße Card-Optik selbst mit (CardShell). */}
      <div className="min-w-0 w-full max-w-[85%]">
        <div className="grid gap-3">
          {toolUses.map((toolUse) => (
            <QuickReplyCard
              key={toolUse.id}
              toolUse={toolUse}
              active={toolUse.id === pendingDialogToolUseId}
              onSend={onSend}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

function ToolRunBubble({ calls }: { calls: ToolCall[] }) {
  // Kein eigenes Card-Chrome — eine eigenständige Tool-Nachricht soll
  // exakt wie ein inline (in einer Text-Antwort) gerenderter Werkzeug-
  // Schritt aussehen: rahmenlos, nur der ToolCallGroup-Toggle.
  return (
    <div className="flex justify-start py-2 text-sm animate-in fade-in-0 duration-300 motion-reduce:animate-none">
      <div className="min-w-0 max-w-[85%]">
        <ToolCallGroup calls={calls} />
      </div>
    </div>
  );
}

// A message whose content is nothing but tool_result blocks is a
// carrier the agent created to hand tool output back to the model. Its
// content is surfaced inside the matching ToolCallGroup, so it gets no
// bubble of its own — otherwise every turn would show a redundant
// JSON-dump bubble.
function isToolResultCarrier(msg: Message): boolean {
  return (
    msg.content.length > 0 &&
    msg.content.every((b) => b.type === "tool_result")
  );
}

function isToolOnlyAssistantMessage(msg: Message): boolean {
  return (
    msg.role === "assistant" &&
    msg.content.length > 0 &&
    msg.content.every((block) => block.type === "tool_use")
  );
}

function isDialogToolOnlyAssistantMessage(msg: Message): boolean {
  return (
    msg.role === "assistant" &&
    msg.content.length > 0 &&
    msg.content.every(
      (block) => block.type === "tool_use" && isDialogToolUseName(block.name),
    )
  );
}

function findPendingDialogToolUseId(messages: Message[]): string | null {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const message = messages[i];
    if (isToolResultCarrier(message)) continue;
    if (message.role === "user") return null;
    if (message.role !== "assistant") continue;
    for (let j = message.content.length - 1; j >= 0; j -= 1) {
      const block = message.content[j];
      if (block.type === "tool_use" && isDialogToolUseName(block.name)) {
        return block.id;
      }
    }
  }
  return null;
}

function toolCallsFromMessage(
  msg: Message,
  toolResults: Map<string, ToolResultBlock>,
): ToolCall[] {
  return msg.content.flatMap((block) => {
    if (block.type !== "tool_use") return [];
    const use = block as ToolUseBlock;
    return [
      {
        use,
        result: use.id ? toolResults.get(use.id) : undefined,
      },
    ];
  });
}

function EmptyState({ showWerkzeugSlots }: { showWerkzeugSlots: boolean }) {
  return (
    <div className="flex flex-col items-center justify-center gap-4 pt-16 text-center text-muted-foreground">
      <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-primary/10 text-primary">
        <MessageSquareText className="h-5 w-5" />
      </div>
      <p className="max-w-sm text-sm leading-relaxed">
        {showWerkzeugSlots
          ? "Beschreibe was du entwerfen möchtest, oder starte mit einer Skizze aus dem Viewport."
          : "Beschreibe was du entwerfen möchtest, oder hänge ein Referenzbild an."}
      </p>
    </div>
  );
}
