/**
 * Gemeinsame Bausteine der Fragebogen-Dialoge (Lean-Fragebogen,
 * fragebogen-spec §2.4: ein Likert-Item pro Bildschirm, Anweisungs-
 * Karte vor jedem Block, Freitext mit Zeichen-Hinweis).
 *
 * Reine Präsentations-Komponenten — die Dialoge halten den State.
 */

import { useEffect, type ReactNode } from "react";
import { ArrowLeft, ArrowRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

/** Schmale Fortschrittsleiste oben im Wizard. */
export function WizardProgress({
  current,
  total,
}: {
  current: number;
  total: number;
}) {
  const pct = total > 0 ? Math.min(100, Math.round((current / total) * 100)) : 0;
  return (
    <div className="flex items-center gap-3">
      <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
        <div
          className="h-full rounded-full bg-primary transition-all duration-300"
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="shrink-0 text-xs font-medium tabular-nums text-muted-foreground">
        {current}/{total}
      </span>
    </div>
  );
}

/** Konsistente Schritt-Hülle: Inhalt + Zurück/Weiter-Footer. */
export function StepShell({
  children,
  onBack,
  onNext,
  nextLabel = "Weiter",
  nextDisabled = false,
  backDisabled = false,
}: {
  children: ReactNode;
  onBack?: () => void;
  onNext: () => void;
  nextLabel?: string;
  nextDisabled?: boolean;
  backDisabled?: boolean;
}) {
  // Enter (ohne Shift, nicht im Freitext) = Weiter, sofern erlaubt — bringt das
  // ueberall sonst geltende Enter=weiter-Modell auch in die Survey-Wizards. Window-
  // Capture wie bei den Quick-Reply-/Gate-Karten; greift nur solange ein Survey-
  // Dialog mit StepShell gemountet ist. Auf einem frischen Likert-Screen ist
  // nextDisabled=true -> kein versehentliches Ueberspringen.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key !== "Enter" || e.shiftKey || e.isComposing) return;
      const target = e.target as HTMLElement | null;
      if (target && target.closest("textarea")) return; // Freitext nicht abfangen
      if (nextDisabled) return;
      e.preventDefault();
      onNext();
    };
    window.addEventListener("keydown", handler, { capture: true });
    return () =>
      window.removeEventListener("keydown", handler, { capture: true });
  }, [onNext, nextDisabled]);

  return (
    <div className="flex min-h-[300px] flex-col">
      <div className="flex flex-1 flex-col justify-center py-2">{children}</div>
      <div className="mt-4 flex items-center justify-between gap-2">
        {onBack ? (
          <Button
            variant="ghost"
            onClick={onBack}
            disabled={backDisabled}
            className="shrink-0 gap-1.5"
          >
            <ArrowLeft className="h-4 w-4 shrink-0" />
            Zurück
          </Button>
        ) : (
          <span />
        )}
        {/* min-w-0 + whitespace-normal: ein langes Label (z. B. „Abschließen +
            Export") darf den Dialog nicht sprengen — es schrumpft/umbricht statt
            ueberzulaufen; der Pfeil bleibt via shrink-0 sichtbar. */}
        <Button
          onClick={onNext}
          disabled={nextDisabled}
          className="min-w-0 gap-1.5 whitespace-normal text-center leading-snug"
        >
          <span className="min-w-0">{nextLabel}</span>
          <ArrowRight className="h-4 w-4 shrink-0" />
        </Button>
      </div>
    </div>
  );
}

/** Anweisungs-Karte vor einem Block (Spec §2.4). */
export function BlockIntro({
  title,
  text,
}: {
  title: string;
  text: string;
}) {
  return (
    <div className="space-y-3 text-center">
      <h3 className="font-display text-lg font-bold tracking-tight">{title}</h3>
      <p className="mx-auto max-w-md text-sm leading-relaxed text-muted-foreground">
        {text}
      </p>
    </div>
  );
}

/**
 * Ein Likert-Item pro Bildschirm: Dimension-Chip, Item-Text, Skala als
 * Pill-Buttons mit Ankern links/rechts.
 */
export function LikertScreen({
  dimension,
  label,
  min,
  max,
  anchorMin,
  anchorMax,
  anchorMid,
  instruction,
  value,
  onChange,
  notUsedLabel,
  notUsed,
  onToggleNotUsed,
}: {
  dimension: string;
  label: string;
  min: number;
  max: number;
  anchorMin: string;
  anchorMax: string;
  /** Optional: Mittel-Anker (z.B. „etwa gleich“ bei −2…+2-Vergleichen). */
  anchorMid?: string;
  /** Optionale Instruktionszeile über dem Item (z.B. V2-Blockanweisung). */
  instruction?: string;
  value: number | null;
  onChange: (v: number) => void;
  /** Optional: „habe ich nicht genutzt“-Ausweichoption (Werkzeug-Items). */
  notUsedLabel?: string;
  notUsed?: boolean;
  onToggleNotUsed?: () => void;
}) {
  const steps: number[] = [];
  for (let i = min; i <= max; i++) steps.push(i);
  // Signierte Skala (min < 0, z. B. −2…+2) = bipolarer Vergleich → zahllos +
  // Intensitäts-Gradient (Mitte neutral/klein, Enden stark/groß); die drei
  // Anker tragen die Bedeutung. Positive 1–7-Skalen behalten ihre Zahlen.
  // Interne Kodierung (−2…+2) unverändert → Daten & Auswertung bleiben gleich.
  const isBipolar = min < 0;
  const center = (min + max) / 2;
  return (
    <div className="space-y-5">
      <div className="space-y-2 text-center">
        {instruction && (
          <p className="mx-auto max-w-md text-xs leading-relaxed text-muted-foreground">
            {instruction}
          </p>
        )}
        <span className="inline-flex rounded-full bg-primary/10 px-2.5 py-0.5 text-xs font-medium text-primary">
          {dimension}
        </span>
        <p className="mx-auto max-w-md text-base font-medium leading-relaxed">
          {label}
        </p>
      </div>
      <div className="space-y-2">
        <div
          className={cn(
            "relative flex items-center justify-center gap-2 sm:gap-3",
            !isBipolar && "gap-1 sm:gap-2",
            notUsed && "opacity-40",
          )}
          role="radiogroup"
          aria-label={label}
        >
          {/* Bipolar: verbindende Spur hinter den Punkten, damit die Reihe als
              Spektrum liest statt als lose Knöpfe. */}
          {isBipolar && (
            <div
              aria-hidden
              className="pointer-events-none absolute left-8 right-8 top-1/2 h-px -translate-y-1/2 bg-border"
            />
          )}
          {steps.map((n) => {
            // Bipolar: keine Zahl (Vorzeichen verwirren), stattdessen ein
            // Intensitäts-Gradient — Mitte (neutral) klein, Enden (stark) groß.
            const dist = Math.abs(n - center); // 0 = Mitte … 2 = Ende
            const sizeCls = isBipolar
              ? ["h-8 w-8", "h-10 w-10", "h-11 w-11"][Math.min(dist, 2)]
              : "h-9 w-9 sm:h-10 sm:w-10";
            return (
              <button
                key={n}
                type="button"
                role="radio"
                aria-checked={value === n}
                aria-label={
                  isBipolar
                    ? `Stufe ${steps.indexOf(n) + 1} von ${steps.length} (${anchorMin} ↔ ${anchorMax})`
                    : `${n} von ${max}`
                }
                onClick={() => onChange(n)}
                className={cn(
                  // h-9 unterhalb sm bei 1–7: sieben 40px-Pills + Gaps sprengen
                  // sonst das ~400px-Rhino-Panel (horizontaler Overflow).
                  "relative z-10 shrink-0 cursor-pointer rounded-full border transition-colors duration-200",
                  sizeCls,
                  !isBipolar && "text-sm font-medium tabular-nums",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60 focus-visible:ring-offset-2",
                  value === n && !notUsed
                    ? "border-primary bg-primary text-primary-foreground shadow-sm"
                    : "border-border bg-card text-foreground hover:border-primary/50 hover:bg-accent",
                )}
              >
                {isBipolar ? null : n}
              </button>
            );
          })}
        </div>
        <div className="flex items-start justify-between gap-2 px-1 text-xs text-muted-foreground">
          <span className="max-w-[40%]">{anchorMin}</span>
          {anchorMid && (
            <span className="max-w-[30%] text-center">{anchorMid}</span>
          )}
          <span className="max-w-[40%] text-right">{anchorMax}</span>
        </div>
      </div>
      {notUsedLabel && onToggleNotUsed && (
        <div className="flex justify-center">
          <button
            type="button"
            onClick={onToggleNotUsed}
            aria-pressed={notUsed}
            className={cn(
              "cursor-pointer rounded-full border px-3.5 py-1.5 text-xs font-medium transition-colors duration-200",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60",
              notUsed
                ? "border-brand-orange/40 bg-brand-orange-soft text-brand-orange-deep"
                : "border-border bg-card text-muted-foreground hover:bg-accent hover:text-foreground",
            )}
          >
            {notUsedLabel}
          </button>
        </div>
      )}
    </div>
  );
}

/** Freitext-Schritt mit Zeichen-Hinweis (Spec §2.4). */
export function FreeTextScreen({
  label,
  value,
  onChange,
  maxChars,
  placeholder,
  optionalHint = true,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  maxChars: number;
  placeholder?: string;
  optionalHint?: boolean;
}) {
  return (
    <div className="space-y-4">
      <p className="mx-auto max-w-md text-center text-base font-medium leading-relaxed">
        {label}
      </p>
      <div className="space-y-1.5">
        <Textarea
          rows={4}
          value={value}
          maxLength={maxChars}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
        />
        <div className="flex items-center justify-between text-xs text-muted-foreground">
          <span>{optionalHint ? "Optional" : ""}</span>
          <span className="tabular-nums">
            {value.length}/{maxChars}
          </span>
        </div>
      </div>
    </div>
  );
}

/** Auswahl-Pille für Single-/Multi-Select-Schritte. */
export function ChoicePill({
  label,
  selected,
  onClick,
  badge,
}: {
  label: string;
  selected: boolean;
  onClick: () => void;
  /** Optionaler Rang-Badge (V3-Ranking: 1/2/3). */
  badge?: number;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={selected}
      className={cn(
        "inline-flex cursor-pointer items-center gap-2 rounded-full border px-4 py-2 text-sm font-medium transition-colors duration-200",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60",
        selected
          ? "border-primary bg-primary/10 text-primary"
          : "border-border bg-card text-foreground hover:bg-accent",
      )}
    >
      {badge != null && (
        <span className="inline-flex h-5 w-5 items-center justify-center rounded-full bg-primary text-[11px] font-bold text-primary-foreground">
          {badge}
        </span>
      )}
      {label}
    </button>
  );
}
