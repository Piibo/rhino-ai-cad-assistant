/**
 * Demografie- und Vorerfahrungs-Block D1–D11 (fragebogen-spec §5.1).
 *
 * Erscheint einmal pro Teilnehmenden-Lane nach dem Consent des ersten
 * Laufs (Spec §4.1: Demographics-Block einmalig zu Beginn, ca. 3 min).
 * Alle Angaben sind freiwillig — der Block ist als ein scrollbares
 * Formular gerendert (das Item-pro-Bildschirm-Format gilt für die
 * Likert-Blöcke, nicht für Demografie).
 *
 * Felddefinitionen kommen aus /api/study/demographics-config, damit
 * Wortlaut-Änderungen in fragebogen_items.json ohne Frontend-Build
 * sichtbar werden.
 */

import { useEffect, useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api } from "@/lib/api";
import { useChatStore } from "@/store/chatStore";
import { cn } from "@/lib/utils";
import type {
  DemographicsConfigResponse,
  DemographicsField,
  DemographicsSubmission,
} from "@/lib/types";

interface Props {
  open: boolean;
  onClose: (submitted: boolean) => void;
  onAbort: () => void;
}

const OTHER_PREFIX = "Sonstige: ";

export function DemographicsDialog({ open, onClose, onAbort }: Props) {
  const activeStudySession = useChatStore((s) => s.activeStudySession);

  const [config, setConfig] = useState<DemographicsConfigResponse | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [multiValues, setMultiValues] = useState<Record<string, string[]>>({});
  const [otherValues, setOtherValues] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);
  const [aborting, setAborting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setError(null);
    setConfig(null);
    setValues({});
    setMultiValues({});
    setOtherValues({});
    (async () => {
      try {
        const fetched = await api.getDemographicsConfig();
        if (cancelled) return;
        setConfig(fetched);
      } catch (err) {
        if (cancelled) return;
        const msg = err instanceof Error ? err.message : String(err);
        setError(`Demografie-Block konnte nicht geladen werden: ${msg}`);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open]);

  const toggleMulti = (fieldId: string, option: string) => {
    setMultiValues((prev) => {
      const current = prev[fieldId] ?? [];
      return {
        ...prev,
        [fieldId]: current.includes(option)
          ? current.filter((o) => o !== option)
          : [...current, option],
      };
    });
  };

  const submit = async () => {
    if (!config || !activeStudySession || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const multiWithOther = (fieldId: string): string[] => {
        const selected = [...(multiValues[fieldId] ?? [])];
        const other = (otherValues[fieldId] ?? "").trim();
        if (other) selected.push(`${OTHER_PREFIX}${other}`);
        return selected;
      };
      const payload: DemographicsSubmission = {
        fragebogen_version_hash: config.fragebogen_version_hash,
        age_range: values["age_range"] || null,
        gender: values["gender"] || null,
        field: values["field"] || null,
        design_experience_years: values["design_experience_years"] || null,
        cad_experience_years: values["cad_experience_years"] || null,
        rhino_self_assessment: values["rhino_self_assessment"]
          ? Number(values["rhino_self_assessment"])
          : null,
        grasshopper_self_assessment: values["grasshopper_self_assessment"]
          ? Number(values["grasshopper_self_assessment"])
          : null,
        other_cad_tools: multiWithOther("other_cad_tools"),
        genai_usage_frequency: values["genai_usage_frequency"] || null,
        genai_design_tools: multiWithOther("genai_design_tools"),
        furniture_design_experience:
          values["furniture_design_experience"] || null,
      };
      await api.submitDemographics(activeStudySession.id, payload);
      onClose(true);
    } catch (err) {
      console.error("Demographics submit failed", err);
      const msg = err instanceof Error ? err.message : String(err);
      setError(`Speichern fehlgeschlagen: ${msg}`);
    } finally {
      setSubmitting(false);
    }
  };

  // Bewusster Forscher-Abbruch aus dem Demografie-Gate (analog zum Consent-
  // Dialog). Esc/Overlay-Klick bleiben blockiert; nur dieser explizite Knopf
  // bricht die Studien-Session ab (Backend markiert 'aborted', Logging).
  const abortRun = async () => {
    if (!activeStudySession || submitting || aborting) return;
    setAborting(true);
    setError(null);
    try {
      await api.abortStudySession(activeStudySession.id, {
        stage: "demographics",
        reason: "researcher_cancelled_at_demographics",
      });
      onAbort();
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(`Abbruch fehlgeschlagen: ${msg}`);
    } finally {
      setAborting(false);
    }
  };

  const renderField = (field: DemographicsField) => {
    switch (field.kind) {
      case "text":
        return (
          <Input
            value={values[field.id] ?? ""}
            placeholder={field.placeholder || undefined}
            maxLength={100}
            onChange={(e) =>
              setValues((prev) => ({ ...prev, [field.id]: e.target.value }))
            }
          />
        );
      case "select":
        return (
          <div className="flex flex-wrap gap-1.5">
            {field.options.map((opt) => {
              const selected = values[field.id] === opt;
              return (
                <button
                  key={opt}
                  type="button"
                  aria-pressed={selected}
                  onClick={() =>
                    setValues((prev) => ({
                      ...prev,
                      [field.id]: selected ? "" : opt,
                    }))
                  }
                  className={cn(
                    "cursor-pointer rounded-full border px-3.5 py-2 text-sm font-medium transition-colors duration-200",
                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60",
                    selected
                      ? "border-primary bg-primary/10 text-primary"
                      : "border-border bg-card text-foreground hover:bg-accent",
                  )}
                >
                  {opt}
                </button>
              );
            })}
          </div>
        );
      case "multiselect":
        return (
          <div className="space-y-2">
            <div className="flex flex-wrap gap-1.5">
              {field.options.map((opt) => {
                const selected = (multiValues[field.id] ?? []).includes(opt);
                return (
                  <button
                    key={opt}
                    type="button"
                    aria-pressed={selected}
                    onClick={() => toggleMulti(field.id, opt)}
                    className={cn(
                      "cursor-pointer rounded-2xl border px-3.5 py-2 text-left text-sm font-medium leading-snug transition-colors duration-200",
                      "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60",
                      selected
                        ? "border-primary bg-primary/10 text-primary"
                        : "border-border bg-card text-foreground hover:bg-accent",
                    )}
                  >
                    {opt}
                  </button>
                );
              })}
            </div>
            {field.allow_other && (
              <Input
                value={otherValues[field.id] ?? ""}
                placeholder="Sonstige (optional)"
                maxLength={100}
                onChange={(e) =>
                  setOtherValues((prev) => ({
                    ...prev,
                    [field.id]: e.target.value,
                  }))
                }
              />
            )}
          </div>
        );
      case "scale": {
        const min = field.scale_min ?? 1;
        const max = field.scale_max ?? 5;
        const steps: number[] = [];
        for (let i = min; i <= max; i++) steps.push(i);
        const current = values[field.id] ? Number(values[field.id]) : null;
        return (
          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              {steps.map((n) => (
                <button
                  key={n}
                  type="button"
                  aria-pressed={current === n}
                  onClick={() =>
                    setValues((prev) => ({
                      ...prev,
                      [field.id]: current === n ? "" : String(n),
                    }))
                  }
                  className={cn(
                    "h-9 w-9 cursor-pointer rounded-full border text-sm font-medium tabular-nums transition-colors duration-200",
                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60",
                    current === n
                      ? "border-primary bg-primary text-primary-foreground shadow-sm"
                      : "border-border bg-card text-foreground hover:border-primary/50 hover:bg-accent",
                  )}
                >
                  {n}
                </button>
              ))}
            </div>
            <div className="flex items-center justify-between text-xs text-muted-foreground">
              <span>{field.anchor_min}</span>
              <span>{field.anchor_max}</span>
            </div>
          </div>
        );
      }
      case "dropdown":
        return (
          <select
            value={values[field.id] ?? ""}
            aria-label={field.label}
            onChange={(e) =>
              setValues((prev) => ({ ...prev, [field.id]: e.target.value }))
            }
            className="h-10 w-full cursor-pointer rounded-xl border border-input bg-card px-3 text-sm transition-colors duration-200 hover:border-muted-foreground/40 focus-visible:border-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
          >
            <option value="">Bitte wählen …</option>
            {field.options.map((opt) => (
              <option key={opt} value={opt}>
                {opt}
              </option>
            ))}
          </select>
        );
    }
  };

  return (
    <Dialog
      open={open}
      // Demographics (D1–D11) is required study data, collected once per
      // participant lane (fragebogen-spec §4.1). Like consent it must not be
      // dismissable: a stray Esc/overlay-click/X used to call onClose(false),
      // submitting nothing, and the gate effect never re-opened it — leaving the
      // lane's demographics permanently empty (the primary analysis dimension).
      // Ignore all close attempts; the only exit is a completed submit, which
      // calls onClose(true) directly.
      onOpenChange={(nextOpen) => {
        if (nextOpen) return;
      }}
    >
      <DialogContent
        hideClose
        className="flex max-h-[90vh] max-w-xl flex-col gap-0 overflow-hidden p-0"
        onEscapeKeyDown={(e) => e.preventDefault()}
        onPointerDownOutside={(e) => e.preventDefault()}
        onInteractOutside={(e) => e.preventDefault()}
      >
        <DialogHeader className="shrink-0 px-6 pb-4 pt-6">
          <DialogTitle>Zu Ihrer Person</DialogTitle>
          <DialogDescription>
            {config?.intro ??
              "Einige kurze Fragen zu Ihrem Hintergrund — alle Angaben sind freiwillig."}
          </DialogDescription>
        </DialogHeader>

        <div className="flex-1 space-y-5 overflow-y-auto px-6 pb-4">
          {config === null && !error && (
            <p className="py-8 text-center text-sm text-muted-foreground">
              Lade Fragen …
            </p>
          )}
          {config?.fields.map((field) => (
            <div key={field.id} className="space-y-2">
              <Label>{field.label}</Label>
              {renderField(field)}
            </div>
          ))}
        </div>

        <div className="shrink-0 border-t border-border/60 px-6 py-3">
          {error && (
            <div className="mb-3 rounded-xl border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">
              {error}
            </div>
          )}
          <div className="flex items-center justify-between">
            <button
              type="button"
              onClick={abortRun}
              disabled={aborting || submitting}
              className="cursor-pointer text-sm text-muted-foreground underline-offset-2 transition-colors duration-200 hover:text-foreground hover:underline disabled:opacity-50"
            >
              {aborting ? "Bricht ab …" : "Studienlauf abbrechen"}
            </button>
            <Button onClick={submit} disabled={submitting || config === null}>
              {submitting ? "Speichere …" : "Weiter"}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
