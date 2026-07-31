import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { X } from "lucide-react";
import type { ToolUseBlock, WsCommand } from "@/lib/types";
import { cn } from "@/lib/utils";
import { useChatStore } from "@/store/chatStore";

type SendCommand = (cmd: WsCommand) => boolean;
type CardTone = "default" | "repair";

const DIALOG_TOOL_NAMES = new Set([
  "request_confirmation",
  "request_parameter",
  "request_parameters",
  "request_choice",
  "request_reference_pick",
]);

export function isDialogToolUseName(name: string | undefined): boolean {
  return !!name && DIALOG_TOOL_NAMES.has(name);
}

interface QuickReplyCardProps {
  toolUse: ToolUseBlock;
  active: boolean;
  onSend: SendCommand;
}

export function QuickReplyCard({
  toolUse,
  active,
  onSend,
}: QuickReplyCardProps) {
  const input = toolUse.input ?? {};
  const activeSessionId = useChatStore((s) => s.activeSessionId);
  const connected = useChatStore((s) => s.connected);
  const pendingSend = useChatStore((s) => s.pendingSend);
  const setPendingSend = useChatStore((s) => s.setPendingSend);
  const disabled = !active || !connected || pendingSend || !activeSessionId;
  const dismissedDialogIds = useChatStore((s) => s.dismissedDialogIds);
  const dismissDialog = useChatStore((s) => s.dismissDialog);
  const dismissed = !!toolUse.id && dismissedDialogIds.includes(toolUse.id);

  // via="quick_reply" markiert die Antwort als ueber eine Dialog-/Quick-Reply-
  // Karte abgeschickt (backend-seitig sonst nicht von getipptem Text
  // unterscheidbar). Backend loggt daraus ein quick_reply_used-Event.
  // Ausnahme: Freitext aus der Auswahl-Karte wird mit via=null gesendet -> als
  // ganz normaler getippter Text geloggt, NICHT als Quick-Reply. Sonst wuerde
  // ein bewusst frei formulierter Gegenvorschlag die FF1/FF2-Affordanz-Zaehlung
  // verfaelschen (er ist ja gerade KEINE Nutzung der vorgegebenen Optionen).
  const sendReply = (text: string, opts?: { via?: string | null }) => {
    const trimmed = text.trim();
    if (!trimmed || disabled || !activeSessionId) return;
    const via = opts && "via" in opts ? opts.via : "quick_reply";
    const payload: Record<string, unknown> = {
      session_id: activeSessionId,
      content: [{ type: "text", text: trimmed }],
    };
    if (via) payload.via = via;
    const ok = onSend({ type: "chat.send", payload });
    if (ok) setPendingSend(true);
  };

  // Mit dem × verworfen → auf eine gedämpfte "verworfen"-Spur einklappen
  // (spiegelt den "bereits beantwortet"-Endzustand) statt als aktive Frage zu
  // bleiben. Dialog-Tools sind nicht-blockierend (das awaiting_user-tool_result
  // ist bereits gespeichert) → das Ausblenden haengt den Run nie auf.
  if (dismissed) {
    return (
      <CardShell active={false} kind={dialogKind(toolUse.name)} settled="verworfen">
        <p className="text-sm leading-snug text-muted-foreground">
          {asString(input.prompt) || "Frage übersprungen."}
        </p>
      </CardShell>
    );
  }

  let card: ReactNode;
  if (toolUse.name === "request_parameter") {
    card = (
      <ParameterCard
        input={input}
        disabled={disabled}
        active={active}
        onReply={sendReply}
      />
    );
  } else if (toolUse.name === "request_parameters") {
    card = (
      <MultiParameterCard
        input={input}
        disabled={disabled}
        active={active}
        onReply={sendReply}
      />
    );
  } else if (toolUse.name === "request_choice") {
    card = (
      <ChoiceCard
        input={input}
        disabled={disabled}
        active={active}
        onReply={sendReply}
        onFreeText={(t) => sendReply(t, { via: null })}
        onSend={onSend}
      />
    );
  } else if (toolUse.name === "request_reference_pick") {
    card = (
      <ReferencePickCard
        input={input}
        disabled={disabled}
        active={active}
        onSend={onSend}
        onReply={sendReply}
      />
    );
  } else {
    card = (
      <ConfirmationCard
        input={input}
        disabled={disabled}
        active={active}
        onReply={sendReply}
      />
    );
  }

  // Nur die aktive Karte bekommt das × (aeltere sind ohnehin settled). Sonst die
  // Karte unveraendert, ohne den relative-Wrapper.
  if (!active || !toolUse.id) return card;
  const dismissId = toolUse.id;
  return (
    <div className="relative">
      {card}
      <button
        type="button"
        onClick={() => dismissDialog(dismissId)}
        aria-label="Karte verwerfen"
        title="Diese Frage überspringen"
        className="absolute right-3 top-3 flex h-6 w-6 items-center justify-center rounded-full text-muted-foreground opacity-70 transition-colors duration-200 hover:bg-muted hover:text-foreground hover:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
      >
        <X className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}

function ConfirmationCard({
  input,
  disabled,
  active,
  onReply,
}: {
  input: Record<string, unknown>;
  disabled: boolean;
  active: boolean;
  onReply: (text: string) => void;
}) {
  const prompt = asString(input.prompt) || "Soll ich so weitermachen?";
  const details = asString(input.details);
  const confirmLabel = asString(input.confirm_label) || "Weiter";
  const confirmResponse = asString(input.confirm_response) || "Ja, weiter.";
  const cancelLabel = asString(input.cancel_label) || "Abbrechen";
  const cancelResponse = asString(input.cancel_response) || "Nein, abbrechen.";
  const isRepair = isRepairConfirmation(prompt, details, confirmLabel);
  // 0 = Bestaetigen, 1 = Stattdessen (Korrektur tippen), 2 = Abbrechen. Mit den
  // Pfeiltasten umschaltbar, Enter loest die aktuelle Auswahl aus.
  const [selected, setSelected] = useState<0 | 1 | 2>(0);
  const [correcting, setCorrecting] = useState(false);
  const [correction, setCorrection] = useState("");
  const correctionRef = useRef<HTMLInputElement | null>(null);

  const openCorrection = () => {
    setSelected(1);
    setCorrecting(true);
    window.requestAnimationFrame(() => correctionRef.current?.focus());
  };
  const sendCorrection = () => {
    const text = correction.trim();
    if (!text || disabled) return;
    onReply(text);
  };

  useEffect(() => {
    if (!active || disabled) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      // Nur ECHTE Textfelder (Input/Textarea — das Korrektur-Feld, die
      // ParameterCard) behalten Pfeil-/Enter-Cursor-Navigation. Der contenteditable
      // Chat-Composer ist beim Bestaetigen meist leer und soll die Auswahl gewinnen
      // (wie schon bei Enter) -> dort abfangen (capture + stopPropagation).
      const inTextField =
        !!target &&
        (target.tagName === "INPUT" || target.tagName === "TEXTAREA");
      if (!inTextField && !event.metaKey && !event.ctrlKey && !event.altKey) {
        if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
          event.preventDefault();
          event.stopPropagation();
          setSelected((s) => (s > 0 ? ((s - 1) as 0 | 1 | 2) : s));
          return;
        }
        if (event.key === "ArrowRight" || event.key === "ArrowDown") {
          event.preventDefault();
          event.stopPropagation();
          setSelected((s) => (s < 2 ? ((s + 1) as 0 | 1 | 2) : s));
          return;
        }
      }
      if (event.key !== "Enter" || event.shiftKey || event.metaKey || event.ctrlKey) {
        return;
      }
      // Ein echtes Eingabefeld darf Enter behalten; der contenteditable Composer
      // (faellt NICHT unter diese Selektoren) soll die aktive Karte gewinnen.
      if (target?.closest("input, textarea, select, button")) return;
      event.preventDefault();
      event.stopPropagation();
      if (selected === 0) onReply(confirmResponse);
      else if (selected === 2) onReply(cancelResponse);
      else openCorrection(); // selected === 1 -> Korrektur-Feld oeffnen + fokussieren
    };
    window.addEventListener("keydown", handleKeyDown, { capture: true });
    return () => window.removeEventListener("keydown", handleKeyDown, { capture: true });
  }, [active, confirmResponse, cancelResponse, selected, disabled, onReply]);

  return (
    <CardShell
      active={active}
      kind={isRepair ? "Reparatur" : "Bestaetigung"}
      tone={isRepair ? "repair" : "default"}
    >
      <p className="text-sm font-medium leading-snug">{prompt}</p>
      {details && <p className="mt-1 text-xs text-muted-foreground">{details}</p>}
      <div className="mt-3 flex flex-wrap gap-2">
        <button
          type="button"
          disabled={disabled}
          onClick={() => onReply(confirmResponse)}
          onMouseEnter={() => setSelected(0)}
          className={cn(
            "cursor-pointer rounded-full px-3.5 py-1.5 text-xs font-medium shadow-sm transition-colors duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60 disabled:cursor-not-allowed disabled:opacity-40",
            isRepair
              ? "bg-brand-orange text-white hover:bg-brand-orange/90"
              : "bg-primary text-primary-foreground hover:bg-primary/90",
            selected === 0 &&
              (isRepair
                ? "ring-2 ring-brand-orange ring-offset-2 ring-offset-card"
                : "ring-2 ring-primary ring-offset-2 ring-offset-card"),
          )}
        >
          {confirmLabel}
        </button>
        <button
          type="button"
          disabled={disabled}
          onClick={openCorrection}
          onMouseEnter={() => setSelected(1)}
          className={cn(
            "cursor-pointer rounded-full border border-dashed border-border bg-card px-3.5 py-1.5 text-xs font-medium text-muted-foreground transition-colors duration-200 hover:border-primary/40 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60 disabled:cursor-not-allowed disabled:opacity-40",
            (selected === 1 || correcting) &&
              "ring-2 ring-primary ring-offset-2 ring-offset-card",
          )}
        >
          Stattdessen…
        </button>
        <button
          type="button"
          disabled={disabled}
          onClick={() => onReply(cancelResponse)}
          onMouseEnter={() => setSelected(2)}
          className={cn(
            "cursor-pointer rounded-full border border-border bg-card px-3.5 py-1.5 text-xs font-medium transition-colors duration-200 hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60 disabled:cursor-not-allowed disabled:opacity-40",
            selected === 2 && "ring-2 ring-primary ring-offset-2 ring-offset-card",
          )}
        >
          {cancelLabel}
        </button>
        {active && !correcting && (
          <span className="self-center text-[11px] text-muted-foreground">
            <kbd className="rounded border border-border/60 px-1">← →</kbd>{" "}
            wählen ·{" "}
            <kbd className="rounded border border-border/60 px-1">Enter</kbd> ={" "}
            {selected === 0
              ? confirmLabel
              : selected === 1
                ? "Stattdessen…"
                : cancelLabel}
          </span>
        )}
      </div>
      {correcting && (
        <div className="mt-3 animate-in fade-in-0 slide-in-from-top-1 duration-200 motion-reduce:animate-none">
          <p className="mb-1.5 text-xs font-medium text-foreground">
            Mach stattdessen:
          </p>
          <div className="flex items-center gap-2">
            <div className="flex min-w-0 flex-1 items-center rounded-xl border border-input bg-card px-2.5 py-1.5 transition-colors duration-200 focus-within:border-primary focus-within:ring-2 focus-within:ring-ring/50">
              <input
                ref={correctionRef}
                value={correction}
                disabled={disabled}
                placeholder="Was soll ich stattdessen machen?"
                onChange={(event) => setCorrection(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") {
                    event.preventDefault();
                    sendCorrection();
                  } else if (event.key === "Escape") {
                    event.preventDefault();
                    setCorrecting(false);
                  }
                }}
                className="min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground disabled:opacity-40"
              />
            </div>
            <button
              type="button"
              disabled={disabled || !correction.trim()}
              onClick={sendCorrection}
              className="cursor-pointer rounded-full bg-primary px-3.5 py-1.5 text-xs font-medium text-primary-foreground shadow-sm transition-colors duration-200 hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60 disabled:cursor-not-allowed disabled:opacity-40"
            >
              Senden
            </button>
          </div>
        </div>
      )}
    </CardShell>
  );
}

function ParameterCard({
  input,
  disabled,
  active,
  onReply,
}: {
  input: Record<string, unknown>;
  disabled: boolean;
  active: boolean;
  onReply: (text: string) => void;
}) {
  const prompt = asString(input.prompt) || "Welchen Wert soll ich verwenden?";
  const parameterName = asString(input.parameter_name) || "Wert";
  const unit = asString(input.unit);
  const defaultValue = input.default_value;
  const [value, setValue] = useState(formatValue(defaultValue));
  const suggestions = useMemo(
    () => asArray(input.suggestions).map(formatValue).filter(Boolean),
    [input.suggestions],
  );
  const min = asNumber(input.min);
  const max = asNumber(input.max);
  const step = asNumber(input.step) ?? 1;
  const numericValue = asNumber(value);
  const canSlide = min != null && max != null && numericValue != null;

  const replyText = () => {
    const template = asString(input.response_template);
    if (template) {
      return template
        .replaceAll("{value}", value)
        .replaceAll("{unit}", unit ? ` ${unit}` : "");
    }
    return `${parameterName}: ${value}${unit ? ` ${unit}` : ""}`;
  };

  return (
    <CardShell active={active} kind="Parameter">
      <p className="text-sm font-medium leading-snug">{prompt}</p>
      <div className="mt-3 flex items-center gap-2">
        <div className="flex min-w-0 flex-1 items-center gap-2 rounded-xl border border-input bg-card px-2.5 py-1.5 transition-colors duration-200 focus-within:border-primary focus-within:ring-2 focus-within:ring-ring/50">
          <input
            value={value}
            disabled={disabled}
            onChange={(event) => setValue(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                onReply(replyText());
              }
            }}
            className="min-w-0 flex-1 bg-transparent text-sm tabular-nums outline-none disabled:opacity-40"
          />
          {unit && <span className="text-xs text-muted-foreground">{unit}</span>}
        </div>
        <button
          type="button"
          disabled={disabled || !value.trim()}
          onClick={() => onReply(replyText())}
          className="cursor-pointer rounded-full bg-primary px-3.5 py-1.5 text-xs font-medium text-primary-foreground shadow-sm transition-colors duration-200 hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60 disabled:cursor-not-allowed disabled:opacity-40"
        >
          Übernehmen
        </button>
      </div>
      {canSlide && (
        <input
          type="range"
          min={min}
          max={max}
          step={step}
          value={clamp(numericValue, min, max)}
          disabled={disabled}
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={(event) => {
            // Enter auch aus dem fokussierten Slider heraus = Übernehmen
            // (sonst fing nur das Zahlenfeld Enter ab).
            if (event.key === "Enter" && !disabled && value.trim()) {
              event.preventDefault();
              onReply(replyText());
            }
          }}
          // Keine fixe Höhe: h-1.5 hat den ~16px-Slider-Daumen abgeschnitten.
          className="mt-3 block w-full cursor-pointer accent-primary disabled:opacity-40"
        />
      )}
      {suggestions.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-2">
          {suggestions.map((suggestion) => (
            <button
              type="button"
              key={suggestion}
              disabled={disabled}
              onClick={() => {
                setValue(suggestion);
                onReply(
                  `${parameterName}: ${suggestion}${unit ? ` ${unit}` : ""}`,
                );
              }}
              className="cursor-pointer rounded-full border border-border bg-card px-2.5 py-1 text-xs tabular-nums transition-colors duration-200 hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {suggestion}
              {unit ? ` ${unit}` : ""}
            </button>
          ))}
        </div>
      )}
    </CardShell>
  );
}

// Combined parameter card — asks for MULTIPLE related values in ONE card
// (backend tool ``request_parameters``). Mirrors ParameterCard per field, but
// collects all values and sends ONE combined reply on a single "Übernehmen"
// (so "Tiefe + Breite einer Nut" is one card + one answer, not two cards).
// Suggestion chips here only SET the field (they don't auto-submit like the
// single-value card), because submitting needs every field filled.
function MultiParameterCard({
  input,
  disabled,
  active,
  onReply,
}: {
  input: Record<string, unknown>;
  disabled: boolean;
  active: boolean;
  onReply: (text: string) => void;
}) {
  const title = asString(input.prompt);
  const fields = useMemo(
    () =>
      asArray(input.parameters)
        .map((item) => (isRecord(item) ? item : null))
        .filter((item): item is Record<string, unknown> => !!item)
        .slice(0, 6),
    [input.parameters],
  );
  // Lazy init from each field's default. The card is keyed per tool-use in
  // the chat list, so ``input`` is immutable for this instance — no resync.
  const [values, setValues] = useState<string[]>(() =>
    fields.map((f) => formatValue(f.default_value)),
  );
  const setAt = (index: number, next: string) =>
    setValues((prev) => {
      const out = prev.slice();
      out[index] = next;
      return out;
    });

  const fieldReply = (field: Record<string, unknown>, value: string): string => {
    const template = asString(field.response_template);
    const unit = asString(field.unit);
    if (template) {
      return template
        .replaceAll("{value}", value)
        .replaceAll("{unit}", unit ? ` ${unit}` : "");
    }
    const name = asString(field.parameter_name) || "Wert";
    return `${name}: ${value}${unit ? ` ${unit}` : ""}`;
  };

  const allFilled =
    fields.length > 0 &&
    values.length === fields.length &&
    values.every((v) => v.trim() !== "");

  const submit = () => {
    if (disabled || !allFilled) return;
    const text = fields
      .map((field, i) => fieldReply(field, values[i].trim()))
      .join(", ");
    onReply(text);
  };

  return (
    <CardShell active={active} kind="Parameter">
      {title && <p className="text-sm font-medium leading-snug">{title}</p>}
      <div className={cn("grid gap-4", title && "mt-3")}>
        {fields.map((field, i) => {
          const name = asString(field.parameter_name) || `Wert ${i + 1}`;
          const label = asString(field.label) || name;
          const unit = asString(field.unit);
          const min = asNumber(field.min);
          const max = asNumber(field.max);
          const step = asNumber(field.step) ?? 1;
          const value = values[i] ?? "";
          const numericValue = asNumber(value);
          const canSlide = min != null && max != null && numericValue != null;
          const suggestions = asArray(field.suggestions)
            .map(formatValue)
            .filter(Boolean);
          return (
            <div key={i}>
              <p className="text-xs font-medium text-foreground">{label}</p>
              <div className="mt-1.5 flex items-center gap-2 rounded-xl border border-input bg-card px-2.5 py-1.5 transition-colors duration-200 focus-within:border-primary focus-within:ring-2 focus-within:ring-ring/50">
                <input
                  value={value}
                  disabled={disabled}
                  onChange={(event) => setAt(i, event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") {
                      event.preventDefault();
                      submit();
                    }
                  }}
                  className="min-w-0 flex-1 bg-transparent text-sm tabular-nums outline-none disabled:opacity-40"
                />
                {unit && (
                  <span className="text-xs text-muted-foreground">{unit}</span>
                )}
              </div>
              {canSlide && (
                <input
                  type="range"
                  min={min}
                  max={max}
                  step={step}
                  value={clamp(numericValue, min, max)}
                  disabled={disabled}
                  onChange={(event) => setAt(i, event.target.value)}
                  onKeyDown={(event) => {
                    // Nach einem Slider-Drag liegt der Fokus auf dem Slider —
                    // ohne dies liefe Enter ins Leere (nur das Zahlenfeld fing
                    // es ab). Enter = Übernehmen, wie der Hinweis verspricht.
                    if (event.key === "Enter") {
                      event.preventDefault();
                      submit();
                    }
                  }}
                  className="mt-2 block w-full cursor-pointer accent-primary disabled:opacity-40"
                />
              )}
              {suggestions.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-2">
                  {suggestions.map((suggestion) => (
                    <button
                      type="button"
                      key={suggestion}
                      disabled={disabled}
                      onClick={() => setAt(i, suggestion)}
                      className={cn(
                        "cursor-pointer rounded-full border px-2.5 py-1 text-xs tabular-nums transition-colors duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60 disabled:cursor-not-allowed disabled:opacity-40",
                        value === suggestion
                          ? "border-primary/50 bg-primary/10 text-primary"
                          : "border-border bg-card hover:bg-accent",
                      )}
                    >
                      {suggestion}
                      {unit ? ` ${unit}` : ""}
                    </button>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>
      <div className="mt-4 flex items-center gap-2">
        <button
          type="button"
          disabled={disabled || !allFilled}
          onClick={submit}
          className="cursor-pointer rounded-full bg-primary px-3.5 py-1.5 text-xs font-medium text-primary-foreground shadow-sm transition-colors duration-200 hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60 disabled:cursor-not-allowed disabled:opacity-40"
        >
          Übernehmen
        </button>
        {active && (
          <span className="self-center text-[11px] text-muted-foreground">
            <kbd className="rounded border border-border/60 px-1">Enter</kbd> =
            Übernehmen
          </span>
        )}
      </div>
    </CardShell>
  );
}

function ChoiceCard({
  input,
  disabled,
  active,
  onReply,
  onFreeText,
  onSend,
}: {
  input: Record<string, unknown>;
  disabled: boolean;
  active: boolean;
  onReply: (text: string) => void;
  onFreeText: (text: string) => void;
  onSend: SendCommand;
}) {
  const connected = useChatStore((s) => s.connected);
  const prompt = asString(input.prompt) || "Welche Option soll ich nehmen?";
  const options = asArray(input.options)
    .map((item) => (isRecord(item) ? item : null))
    .filter((item): item is Record<string, unknown> => !!item)
    .slice(0, 4);
  // Tastatur-Paritaet zur ConfirmationCard: ← →/↑ ↓ waehlen die Option (mit
  // Viewport-Highlight), blosses Enter loest die aktive aus.
  const [selected, setSelected] = useState(0);
  // Freitext-Ausweg: eine eigene Idee statt der vorgegebenen Optionen.
  const [custom, setCustom] = useState("");

  // Hover/Focus ueber eine Option, die eine konkrete Geometrie meint, hebt
  // diese non-mutating im Rhino-Viewport hervor — derselbe DisplayConduit-
  // Pfad wie die Inline-Referenz-Tokens (keine Auswahl, kein Eingriff,
  // kein Logging-Seiteneffekt). Block wird EXPLIZIT als component_pick
  // gebaut (component_type default "object"), damit der Backend-Handler ihn
  // ohne Inference-Rateweg aufloest.
  const highlightFor = (
    option: Record<string, unknown>,
  ): Record<string, unknown> | null => {
    const reference = isRecord(option.reference) ? option.reference : null;
    const objectId = reference ? asString(reference.object_id) : "";
    if (!reference || !objectId) return null;
    const componentType = asString(reference.component_type) || "object";
    const rawIndex = reference.component_index;
    return {
      type: "component_pick",
      object_id: objectId,
      component_type: componentType,
      component_index: typeof rawIndex === "number" ? rawIndex : null,
    };
  };

  const showHighlight = (block: Record<string, unknown> | null) => {
    // Settled (already-answered) choice cards stay in the transcript; hovering
    // one must NOT drive the viewport (the referenced geometry may be gone).
    if (!block || !connected || disabled) return;
    onSend({ type: "viewport.highlight_reference", payload: { block } });
  };
  const clearHighlight = () => {
    if (!connected) return;
    onSend({ type: "viewport.highlight_clear", payload: {} });
  };
  // Clear any lingering hover highlight if this card unmounts mid-hover (no
  // mouseleave/blur fires on unmount) so it can't orphan the Rhino overlay.
  useEffect(() => {
    return () => {
      onSend({ type: "viewport.highlight_clear", payload: {} });
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Tastatursteuerung wie bei der ConfirmationCard: ← →/↑ ↓ waehlen (mit Viewport-
  // Highlight), blosses Enter loest die aktive Option aus. Ein fokussierter Options-
  // Button behaelt sein natives Enter (Guard auf input/textarea/select/button); der
  // contenteditable Composer faellt nicht darunter -> die aktive Karte gewinnt.
  useEffect(() => {
    if (!active || disabled || options.length === 0) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const inField =
        !!target &&
        (target.tagName === "INPUT" || target.tagName === "TEXTAREA");
      if (!inField && !event.metaKey && !event.ctrlKey && !event.altKey) {
        if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
          event.preventDefault();
          event.stopPropagation();
          const next = selected > 0 ? selected - 1 : selected;
          setSelected(next);
          showHighlight(highlightFor(options[next]));
          return;
        }
        if (event.key === "ArrowRight" || event.key === "ArrowDown") {
          event.preventDefault();
          event.stopPropagation();
          const next =
            selected < options.length - 1 ? selected + 1 : selected;
          setSelected(next);
          showHighlight(highlightFor(options[next]));
          return;
        }
      }
      if (
        event.key !== "Enter" ||
        event.shiftKey ||
        event.metaKey ||
        event.ctrlKey
      ) {
        return;
      }
      if (target?.closest("input, textarea, select, button")) return;
      const option = options[selected];
      if (!option) return;
      event.preventDefault();
      event.stopPropagation();
      clearHighlight();
      onReply(
        asString(option.response_text) ||
          asString(option.label) ||
          `Option ${selected + 1}`,
      );
    };
    window.addEventListener("keydown", handleKeyDown, { capture: true });
    return () =>
      window.removeEventListener("keydown", handleKeyDown, { capture: true });
    // showHighlight/clearHighlight/highlightFor sind render-lokale Closures (stabil
    // genug ueber die gelisteten Deps); bewusst nicht in den Deps, analog Composer.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, disabled, selected, options, onReply]);

  return (
    <CardShell active={active} kind="Auswahl">
      <p className="text-sm font-medium leading-snug">{prompt}</p>
      <div className="mt-3 grid gap-2">
        {options.map((option, index) => {
          const label = asString(option.label) || `Option ${index + 1}`;
          const description = asString(option.description);
          const response = asString(option.response_text) || label;
          const highlightBlock = highlightFor(option);
          return (
            <button
              type="button"
              key={`${label}-${index}`}
              disabled={disabled}
              onClick={() => {
                clearHighlight();
                onReply(response);
              }}
              onMouseEnter={() => {
                setSelected(index);
                showHighlight(highlightBlock);
              }}
              onMouseLeave={clearHighlight}
              onFocus={() => {
                setSelected(index);
                showHighlight(highlightBlock);
              }}
              onBlur={clearHighlight}
              className={cn(
                "cursor-pointer rounded-xl border border-border/60 bg-card px-3.5 py-2.5 text-left text-xs transition-colors duration-200 hover:border-primary/40 hover:bg-primary/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60 disabled:cursor-not-allowed disabled:opacity-40",
                active &&
                  index === selected &&
                  "border-primary/60 bg-primary/5 ring-1 ring-primary/30",
              )}
            >
              <span className="block font-medium">{label}</span>
              {description && (
                <span className="mt-0.5 block text-muted-foreground">
                  {description}
                </span>
              )}
            </button>
          );
        })}
      </div>
      {active && (
        <>
          {/* Freitext-Ausweg: nicht in die Vorschlaege zwingen — eine eigene
              Idee tippen und als Antwort senden. Laeuft ueber onFreeText OHNE
              quick_reply-Marker (s. sendReply), damit es als normaler Text und
              nicht als Quick-Reply-Nutzung gezaehlt wird. */}
          <div className="mt-2 flex items-center gap-2 rounded-xl border border-input bg-card px-2.5 py-1.5 transition-colors duration-200 focus-within:border-primary focus-within:ring-2 focus-within:ring-ring/50">
            <input
              value={custom}
              disabled={disabled}
              onChange={(event) => setCustom(event.target.value)}
              onFocus={clearHighlight}
              onKeyDown={(event) => {
                if (
                  event.key === "Enter" &&
                  !event.shiftKey &&
                  !disabled &&
                  custom.trim()
                ) {
                  event.preventDefault();
                  event.stopPropagation();
                  clearHighlight();
                  onFreeText(custom.trim());
                }
              }}
              placeholder="… oder eigene Idee eingeben"
              className="min-w-0 flex-1 bg-transparent text-xs outline-none placeholder:text-muted-foreground disabled:opacity-40"
            />
            <button
              type="button"
              disabled={disabled || !custom.trim()}
              onClick={() => {
                clearHighlight();
                onFreeText(custom.trim());
              }}
              className="shrink-0 cursor-pointer rounded-full bg-primary px-2.5 py-1 text-[11px] font-medium text-primary-foreground transition-colors duration-200 hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-40"
            >
              Senden
            </button>
          </div>
          {options.length > 0 && (
            <p className="mt-2 text-[11px] text-muted-foreground">
              <kbd className="rounded border border-border/60 px-1">← →</kbd>{" "}
              wählen ·{" "}
              <kbd className="rounded border border-border/60 px-1">Enter</kbd> ={" "}
              bestätigen · oder eigene Idee tippen
            </p>
          )}
        </>
      )}
    </CardShell>
  );
}

function ReferencePickCard({
  input,
  disabled,
  active,
  onSend,
  onReply,
}: {
  input: Record<string, unknown>;
  disabled: boolean;
  active: boolean;
  onSend: SendCommand;
  onReply: (text: string) => void;
}) {
  const prompt = asString(input.prompt) || "Wähle die gewünschte Referenz aus.";
  const hint = asString(input.hint);
  const targetType = (asString(input.target_type) || "face").toLowerCase();
  const activeSessionId = useChatStore((s) => s.activeSessionId);
  const viewportPending = useChatStore((s) => s.viewportPending);
  const setViewportPending = useChatStore((s) => s.setViewportPending);
  const setViewportError = useChatStore((s) => s.setViewportError);
  const triggerDisabled = disabled || !!viewportPending || !activeSessionId;

  // Target-type → WS command + label. Mirrors the per-icon pick triggers
  // in InputBar so the AI-initiated pick uses the exact same Rhino path.
  const TARGETS: Record<
    string,
    { label: string; trigger: () => void }
  > = {
    object: {
      label: "Objekt auswählen",
      trigger: () => {
        setViewportError(null);
        setViewportPending("pick");
        onSend({
          type: "viewport.request_pick",
          payload: {
            mode: "single",
            filter: "any",
            session_id: activeSessionId,
          },
        });
      },
    },
    face: {
      label: "Fläche auswählen",
      trigger: () => {
        setViewportError(null);
        setViewportPending("component");
        onSend({
          type: "viewport.request_component",
          payload: {
            component_type: "face",
            session_id: activeSessionId,
          },
        });
      },
    },
    edge: {
      label: "Kante auswählen",
      trigger: () => {
        setViewportError(null);
        setViewportPending("component");
        onSend({
          type: "viewport.request_component",
          payload: {
            component_type: "edge",
            session_id: activeSessionId,
          },
        });
      },
    },
    point: {
      label: "Punkt auswählen",
      trigger: () => {
        setViewportError(null);
        setViewportPending("point");
        onSend({
          type: "viewport.request_point",
          payload: {},
        });
      },
    },
  };
  const target = TARGETS[targetType] ?? TARGETS.face;

  return (
    <CardShell active={active} kind="Referenz auswählen">
      <p className="text-sm font-medium leading-snug">{prompt}</p>
      {hint && (
        <p className="mt-1 text-xs text-muted-foreground">{hint}</p>
      )}
      <div className="mt-3 flex flex-wrap gap-2">
        <button
          type="button"
          disabled={triggerDisabled}
          onClick={target.trigger}
          className="cursor-pointer rounded-full bg-primary px-3.5 py-1.5 text-xs font-medium text-primary-foreground shadow-sm transition-colors duration-200 hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {target.label}
        </button>
        <button
          type="button"
          disabled={disabled}
          onClick={() => onReply("Doch nicht, ich wähle nichts aus.")}
          className="cursor-pointer rounded-full border border-border bg-card px-3.5 py-1.5 text-xs font-medium transition-colors duration-200 hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60 disabled:cursor-not-allowed disabled:opacity-40"
        >
          Abbrechen
        </button>
        {active && (
          <span className="self-center text-[11px] text-muted-foreground">
            Nach der Auswahl: Senden
          </span>
        )}
      </div>
    </CardShell>
  );
}

function CardShell({
  active,
  kind,
  tone = "default",
  settled,
  children,
}: {
  active: boolean;
  kind: string;
  tone?: CardTone;
  settled?: string;
  children: ReactNode;
}) {
  return (
    <div
      className={cn(
        "rounded-2xl border border-border/60 bg-card p-4 shadow-panel",
        "animate-in fade-in-0 slide-in-from-bottom-1 duration-200 motion-reduce:animate-none",
        active &&
          (tone === "repair"
            ? "border-brand-orange/30 ring-2 ring-brand-orange/25"
            : "ring-2 ring-primary/20"),
        !active && "opacity-60",
      )}
    >
      <div className="mb-2.5 flex items-center justify-between gap-2">
        <span
          className={cn(
            "text-[11px] font-semibold uppercase tracking-[0.14em]",
            active && tone === "repair" && "text-brand-orange",
            active && tone !== "repair" && "text-primary",
            !active && "text-muted-foreground",
          )}
        >
          {kind}
        </span>
        {!active && (
          <span className="rounded-full bg-muted px-2.5 py-0.5 text-[11px] font-medium text-muted-foreground">
            {settled ?? "bereits beantwortet"}
          </span>
        )}
      </div>
      {children}
    </div>
  );
}

function asString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

// Kind-Label fuer die eingeklappte "verworfen"-Spur (gleiche Labels wie die
// jeweilige Karte oben verwendet). Hinweis: eine verworfene Reparatur-
// Bestaetigung kollabiert hier zu "Bestaetigung" — die Reparatur-Flavor
// (isRepairConfirmation, aus dem Prompt-Text) laesst sich aus dem Tool-Namen
// allein nicht rekonstruieren; bewusst akzeptiert (rein kosmetisch).
function dialogKind(name: string | undefined): string {
  switch (name) {
    case "request_choice":
      return "Auswahl";
    case "request_parameter":
    case "request_parameters":
      return "Parameter";
    case "request_reference_pick":
      return "Referenz auswählen";
    default:
      return "Bestaetigung";
  }
}

function isRepairConfirmation(
  prompt: string,
  details: string,
  confirmLabel: string,
): boolean {
  const text = `${prompt} ${details} ${confirmLabel}`.toLowerCase();
  return (
    text.includes("reparatur") ||
    text.includes("reparieren") ||
    text.includes("tool-fehler")
  );
}

function asNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim() !== "") {
    const parsed = Number(value.replace(",", "."));
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

function formatValue(value: unknown): string {
  if (typeof value === "number" && Number.isFinite(value)) {
    return Number.isInteger(value) ? String(value) : String(Number(value.toFixed(3)));
  }
  return typeof value === "string" ? value : "";
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}
