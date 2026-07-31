import { useEffect, useRef } from "react";
import { ShieldAlert } from "lucide-react";
import { cn } from "@/lib/utils";
import { useChatStore } from "@/store/chatStore";
import type { GatePreviewPayload, WsCommand } from "@/lib/types";

type SendCommand = (cmd: WsCommand) => boolean;

interface GatePreviewCardProps {
  gate: GatePreviewPayload;
  onSend: SendCommand;
}

/**
 * Warum-Text pro Operationstyp. Erklaert, WESHALB die (destruktive) Operation
 * noetig ist — damit der Designer das delete-then-rebuild-Muster versteht und
 * die Karte nicht als "meine Arbeit wird vernichtet" liest. Die Karte kennt die
 * konkrete Absicht des Modells nicht (kein reason-Feld im Gate-Payload), aber
 * der Operationstyp traegt die typische Begruendung; rein aus gate.tool_name
 * abgeleitet. Bewusst knapp (ein Satz) und gehedged ("meist"), weil eine
 * Loeschung im Einzelfall auch endgueltig gemeint sein kann.
 */
function whyExplanation(toolName?: string): string {
  const t = toolName ?? "";
  if (t === "delete_object")
    return "Wird entfernt — meist, um es gleich neu aufzubauen.";
  if (t.startsWith("boolean_"))
    return "Die Objekte werden zu einer neuen Form verrechnet.";
  if (t.startsWith("subd_"))
    return "Ändert die Netzstruktur; die alte Version bleibt gesichert.";
  return "Ändert die bestehende Geometrie.";
}

/**
 * Part C — selektives Vorschau-Gate.
 *
 * Wird gezeigt, wenn das Backend eine destruktive Operation anhalten und dem
 * Designer zur Freigabe vorlegen will. Der Assistent wartet auf `gate.resolve`.
 *
 * Enter = Übernehmen (solange kein Eingabefeld fokussiert ist), analog zur
 * ConfirmationCard in QuickReplyCard.tsx.
 */
export function GatePreviewCard({ gate, onSend }: GatePreviewCardProps) {
  const connected = useChatStore((s) => s.connected);
  const activeSessionId = useChatStore((s) => s.activeSessionId);
  const clearPendingGate = useChatStore((s) => s.clearPendingGate);
  // NICHT von pendingSend abhaengig: das Gate erscheint WAEHREND eines aktiven
  // Runs (pendingSend=true). Wuerde pendingSend hier deaktivieren, waeren die
  // Buttons genau dann tot, wenn der Designer entscheiden muss -> Backend
  // wartet ewig -> Hang. Das Gate ist gerade dann klickbar.
  const disabled = !connected || !activeSessionId;
  const acceptButtonRef = useRef<HTMLButtonElement | null>(null);

  const resolve = (decision: "accept" | "revert") => {
    if (disabled) return;
    onSend({
      type: "gate.resolve",
      payload: { tu_id: gate.tu_id, decision },
    });
    // Optimistisch wegblenden: sofortiges Feedback + verhindert Doppelklick auf
    // ein bereits aufgeloestes Gate (Backend ist idempotent; gate.cleared kommt
    // ohnehin noch).
    clearPendingGate(gate.tu_id);
  };

  // Enter = Übernehmen — mirrors ConfirmationCard keyboard shortcut.
  useEffect(() => {
    if (disabled) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (
        event.key !== "Enter" ||
        event.shiftKey ||
        event.metaKey ||
        event.ctrlKey
      ) {
        return;
      }
      const target = event.target as HTMLElement | null;
      if (target?.closest("input, textarea, select, button")) return;
      // Capture + stopPropagation: gewinnt gegen den Enter-Handler des
      // fokussierten Composers (contenteditable DIV), der sonst onSubmit feuert.
      event.preventDefault();
      event.stopPropagation();
      resolve("accept");
    };
    window.addEventListener("keydown", handleKeyDown, { capture: true });
    return () => window.removeEventListener("keydown", handleKeyDown, { capture: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [disabled, gate.tu_id]);

  // Auto-Fokus auf "Übernehmen", sobald die Karte erscheint. Das ist der
  // EIGENTLICHE Enter-Pfad: das Plugin laeuft in einem in Rhino eingebetteten
  // Webview; wenn dort kein fokussiertes Element existiert, liefert der Host
  // ein blankes Enter oft nicht an das document (der window-Keydown-Listener
  // oben feuert dann nie). Ein fokussierter Default-Button faengt Enter nativ
  // ab (loest onClick aus) UND gibt dem Webview ein konkretes Tastatur-Ziel.
  // Zeigt zugleich den Default visuell (Fokusring). preventScroll, damit der
  // Chat nicht ruckt. Best-effort: .focus() kann den OS-Fokus nicht erzwingen,
  // wenn er beim Rhino-Viewport liegt (dann bleibt Klicken der Weg).
  useEffect(() => {
    if (disabled) return;
    const btn = acceptButtonRef.current;
    if (!btn) return;
    const id = window.requestAnimationFrame(() => {
      try {
        btn.focus({ preventScroll: true });
      } catch {
        btn.focus();
      }
    });
    return () => window.cancelAnimationFrame(id);
  }, [disabled, gate.tu_id]);

  return (
    <div
      className={cn(
        "rounded-2xl border border-brand-orange/30 bg-card p-4 shadow-panel ring-2 ring-brand-orange/25",
        "animate-in fade-in-0 slide-in-from-bottom-1 duration-200 motion-reduce:animate-none",
      )}
    >
      {/* Header */}
      <div className="mb-2.5 flex items-center gap-2">
        <ShieldAlert className="h-3.5 w-3.5 shrink-0 text-brand-orange" aria-hidden="true" />
        <span className="text-[11px] font-semibold uppercase tracking-[0.14em] text-brand-orange">
          Vorschau — Änderung bestätigen
        </span>
      </div>

      {/* Summary */}
      <p className="text-sm font-medium leading-snug">{gate.summary}</p>

      {/* Warum-Hinweis (knapp) — reframed das delete-then-rebuild-Muster: die
          Karte feuert auf dem Loesch-/Boolean-Schritt, bestaetigt aber den
          GANZEN Umbau (eine Karte pro Run). Ein kurzer Satz genuegt, damit die
          Karte nicht als "meine Arbeit wird geloescht" gelesen wird. Tailored
          via gate.tool_name. */}
      <p className="mt-1 text-xs leading-snug text-muted-foreground">
        {whyExplanation(gate.tool_name)}
      </p>

      {/* Tool name badge — dezent */}
      {gate.tool_name && (
        <p className="mt-1 text-xs text-muted-foreground">
          Werkzeug:{" "}
          <code className="rounded bg-muted px-1 py-0.5 font-mono text-[11px]">
            {gate.tool_name}
          </code>
        </p>
      )}

      {/* Viewport-hint — nur wenn es ueberhaupt betroffene Objekte gibt.
          Ohne Ziel-GUIDs (z.B. eine Operation, deren Tool-Input keine
          Objekt-Referenz traegt) wird im Viewport nichts hervorgehoben; die
          Karte darf das dann auch nicht behaupten (sonst sucht der Designer
          eine Markierung, die es nicht gibt). */}
      {gate.object_ids && gate.object_ids.length > 0 && (
        <p className="mt-1.5 text-xs text-muted-foreground">
          Die betroffene Geometrie ist im Viewport hervorgehoben.
        </p>
      )}

      {/* Action buttons */}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button
          ref={acceptButtonRef}
          type="button"
          disabled={disabled}
          onClick={() => resolve("accept")}
          className="cursor-pointer rounded-full bg-primary px-3.5 py-1.5 text-xs font-medium text-primary-foreground shadow-sm transition-colors duration-200 hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60 disabled:cursor-not-allowed disabled:opacity-40"
        >
          Übernehmen
        </button>
        <button
          type="button"
          disabled={disabled}
          onClick={() => resolve("revert")}
          className="cursor-pointer rounded-full border border-border bg-card px-3.5 py-1.5 text-xs font-medium transition-colors duration-200 hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60 disabled:cursor-not-allowed disabled:opacity-40"
        >
          Verwerfen
        </button>
        <span className="self-center text-[11px] text-muted-foreground">
          Enter = Übernehmen
        </span>
      </div>
    </div>
  );
}
