/**
 * Vergleichsblock + Schlussfragen (fragebogen-spec §5.3/§5.4).
 *
 * Erscheint nach dem Per-Bedingungs-Survey des zweiten Laufs:
 * V1 Präferenz (+ Begründung) → V2 sechs Dimensions-Vergleiche
 * (5-Punkt, −2…+2) → V3 Werkzeug-Ranking (Top-3, Reihenfolge per
 * Klick) → V4 Hybrid-Frage → S1–S3 offene Schlussfragen.
 *
 * Nach dem Submit wird die Studien-Session beendet und das
 * Export-Bundle erzeugt (übernimmt die End-Schritte, die beim ersten
 * Lauf der AgencySurveyDialog macht).
 */

import { useEffect, useMemo, useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { useChatStore } from "@/store/chatStore";
import type {
  ExportResponse,
  FinalSurveyConfigResponse,
  FinalSurveySubmission,
} from "@/lib/types";
import {
  BlockIntro,
  ChoicePill,
  FreeTextScreen,
  LikertScreen,
  StepShell,
  WizardProgress,
} from "./survey-ui";

interface Props {
  open: boolean;
  onClose: (exported: ExportResponse | null, finished?: boolean) => void;
}

type Step =
  | { kind: "intro" }
  | { kind: "preference" }
  | { kind: "v2"; id: string; label: string }
  | { kind: "ranking" }
  | { kind: "freetext"; id: string; label: string; maxChars: number };

export function FinalSurveyDialog({ open, onClose }: Props) {
  const activeStudySession = useChatStore((s) => s.activeStudySession);

  const [config, setConfig] = useState<FinalSurveyConfigResponse | null>(null);
  const [stepIndex, setStepIndex] = useState(0);
  const [preference, setPreference] = useState<string | null>(null);
  const [preferenceWhy, setPreferenceWhy] = useState("");
  const [v2, setV2] = useState<Record<string, number | null>>({});
  const [ranking, setRanking] = useState<string[]>([]);
  const [freetexts, setFreetexts] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);
  const [aborting, setAborting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [finishingRun, setFinishingRun] = useState(false);

  useEffect(() => {
    if (!open || !activeStudySession) return;
    let cancelled = false;
    setError(null);
    setConfig(null);
    setStepIndex(0);
    setPreference(null);
    setPreferenceWhy("");
    setV2({});
    setRanking([]);
    setFreetexts({});
    setFinishingRun(false);
    (async () => {
      try {
        const [fetched, existing] = await Promise.all([
          api.getFinalSurveyConfig(activeStudySession.id),
          api.getLatestFinalSurvey(activeStudySession.id),
        ]);
        if (cancelled) return;
        // Re-Entry-Guard: Vergleichsblock schon abgeschickt, aber
        // Ende/Export sind seinerzeit gescheitert → Restschritte
        // nachholen statt erneut zu fragen.
        if (existing !== null) {
          setFinishingRun(true);
          await finishRun(() => cancelled);
          return;
        }
        setConfig(fetched);
      } catch (err) {
        if (cancelled) return;
        const msg = err instanceof Error ? err.message : String(err);
        setError(`Vergleichsblock konnte nicht geladen werden: ${msg}`);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open, activeStudySession]);

  const steps = useMemo<Step[]>(() => {
    if (!config) return [];
    const result: Step[] = [{ kind: "intro" }, { kind: "preference" }];
    for (const dim of config.v2_dimensions) {
      result.push({ kind: "v2", id: dim.id, label: dim.label });
    }
    if (config.ranking_options.length > 0) {
      result.push({ kind: "ranking" });
    }
    result.push({
      kind: "freetext",
      id: "hybrid_mode",
      label: config.hybrid_label,
      maxChars: config.hybrid_max_chars,
    });
    for (const q of config.open_questions) {
      result.push({
        kind: "freetext",
        id: q.id,
        label: q.label,
        maxChars: q.max_chars,
      });
    }
    return result;
  }, [config]);

  const step = steps[stepIndex];
  const isLast = stepIndex === steps.length - 1;

  const stepComplete = useMemo(() => {
    if (!step) return false;
    if (step.kind === "preference") return preference != null;
    if (step.kind === "v2") return v2[step.id] != null;
    return true; // Intro, Ranking (optional), Freitexte (optional)
  }, [step, preference, v2]);

  const toggleRank = (toolId: string) => {
    if (!config) return;
    setRanking((prev) => {
      if (prev.includes(toolId)) return prev.filter((id) => id !== toolId);
      if (prev.length >= config.ranking_max_rank) return prev;
      return [...prev, toolId];
    });
  };

  // Ende + Export — gemeinsamer Schlussschritt für Submit und den
  // Re-Entry-Fall. Function declaration (hoisted), damit der
  // Load-Effect sie aufrufen kann. Ein Doppel-Submit des Blocks ist
  // unkritisch: das Backend ersetzt die Zeile (eine pro Termin).
  async function finishRun(
    shouldAbort: () => boolean = () => false,
  ): Promise<void> {
    if (!activeStudySession) return;
    try {
      await api.endStudySession(activeStudySession.id);
    } catch (err) {
      console.error("End study session failed", err);
      const msg = err instanceof Error ? err.message : String(err);
      setError(`Session-Abschluss fehlgeschlagen: ${msg}`);
      setFinishingRun(false);
      return;
    }
    let exported: ExportResponse | null = null;
    try {
      exported = await api.exportStudySession(activeStudySession.id);
    } catch (err) {
      console.error("Export after final survey failed", err);
    }
    // Don't fire onClose if the dialog was torn down mid-await (see Agency).
    if (shouldAbort()) return;
    onClose(exported, true);
  }

  const submit = async () => {
    if (!config || !activeStudySession || submitting || aborting) return;
    setSubmitting(true);
    setError(null);
    try {
      const payload: FinalSurveySubmission = {
        fragebogen_version_hash: config.fragebogen_version_hash,
        preference_choice:
          (preference as FinalSurveySubmission["preference_choice"]) ?? null,
        preference_freetext: preferenceWhy,
        v2_control: v2["v2_control"] ?? null,
        v2_expressiveness: v2["v2_expressiveness"] ?? null,
        v2_exploration: v2["v2_exploration"] ?? null,
        v2_speed: v2["v2_speed"] ?? null,
        v2_trust: v2["v2_trust"] ?? null,
        v2_ownership: v2["v2_ownership"] ?? null,
        tool_importance_rank_1: ranking[0] ?? null,
        tool_importance_rank_2: ranking[1] ?? null,
        tool_importance_rank_3: ranking[2] ?? null,
        hybrid_mode_freetext: freetexts["hybrid_mode"] ?? "",
        surprise_freetext: freetexts["surprise"] ?? "",
        missing_freetext: freetexts["missing"] ?? "",
        wish_freetext: freetexts["wish"] ?? "",
      };
      await api.submitFinalSurvey(activeStudySession.id, payload);
      await finishRun();
    } catch (err) {
      console.error("Final survey submit failed", err);
      const msg = err instanceof Error ? err.message : String(err);
      setError(`Speichern fehlgeschlagen: ${msg}`);
    } finally {
      setSubmitting(false);
    }
  };

  // Rückzieher während des Vergleichsblocks: nach dem Per-Bedingungs-
  // Submit von Lauf 2 leitet der Re-Entry-Guard jeden Stop-Klick direkt
  // hierher — ohne eigenen Abort-Pfad wäre ein Abbruch per UI sonst
  // nicht mehr möglich.
  const abortStudyRun = async () => {
    if (!activeStudySession || submitting || aborting) return;
    setAborting(true);
    setError(null);
    try {
      await api.abortStudySession(activeStudySession.id, {
        stage: "final_survey",
        reason: "researcher_aborted_run",
      });
      onClose(null, true);
    } catch (err) {
      console.error("Study abort failed", err);
      const msg = err instanceof Error ? err.message : String(err);
      setError(`Abbruch fehlgeschlagen: ${msg}`);
    } finally {
      setAborting(false);
    }
  };

  const next = () => {
    if (isLast) {
      void submit();
      return;
    }
    setStepIndex((i) => Math.min(i + 1, steps.length - 1));
  };

  const renderStep = () => {
    if (!config || !step) return null;
    switch (step.kind) {
      case "intro":
        return <BlockIntro title="Bedingungsvergleich" text={config.intro} />;
      case "preference":
        return (
          <div className="space-y-4">
            <p className="mx-auto max-w-md text-center text-base font-medium leading-relaxed">
              {config.preference_label}
            </p>
            <div className="flex flex-wrap justify-center gap-2">
              {config.preference_options.map((opt) => (
                <ChoicePill
                  key={opt.id}
                  label={opt.label}
                  selected={preference === opt.id}
                  onClick={() => setPreference(opt.id)}
                />
              ))}
            </div>
            <FreeTextScreen
              label=""
              value={preferenceWhy}
              onChange={setPreferenceWhy}
              maxChars={config.preference_why_max_chars}
              placeholder={config.preference_why_label}
            />
          </div>
        );
      case "v2":
        return (
          <LikertScreen
            dimension="Vergleich"
            label={step.label}
            instruction={config.v2_label}
            min={config.v2_scale.min}
            max={config.v2_scale.max}
            anchorMin={config.v2_scale.anchor_min}
            anchorMid={config.v2_scale.anchor_mid}
            anchorMax={config.v2_scale.anchor_max}
            value={v2[step.id] ?? null}
            onChange={(v) => setV2((prev) => ({ ...prev, [step.id]: v }))}
          />
        );
      case "ranking":
        return (
          <div className="space-y-4">
            <p className="mx-auto max-w-md text-center text-base font-medium leading-relaxed">
              {config.ranking_label}
            </p>
            <div className="flex flex-wrap justify-center gap-2">
              {config.ranking_options.map((opt) => {
                const rank = ranking.indexOf(opt.id);
                return (
                  <ChoicePill
                    key={opt.id}
                    label={opt.dimension}
                    selected={rank >= 0}
                    badge={rank >= 0 ? rank + 1 : undefined}
                    onClick={() => toggleRank(opt.id)}
                  />
                );
              })}
            </div>
            <p className="text-center text-xs text-muted-foreground">
              Erneutes Anklicken entfernt ein Werkzeug aus der Reihung.
            </p>
          </div>
        );
      case "freetext":
        return (
          <FreeTextScreen
            label={step.label}
            value={freetexts[step.id] ?? ""}
            onChange={(v) =>
              setFreetexts((prev) => ({ ...prev, [step.id]: v }))
            }
            maxChars={step.maxChars}
          />
        );
    }
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(nextOpen) => {
        if (!nextOpen) onClose(null, false);
      }}
    >
      <DialogContent className="flex max-h-[90vh] max-w-xl flex-col gap-0 overflow-hidden p-0">
        <DialogHeader className="shrink-0 px-6 pb-4 pt-6">
          <DialogTitle>Abschluss-Fragebogen</DialogTitle>
          <DialogDescription>
            {activeStudySession ? (
              <>
                Vergleich beider Bedingungen, Teilnehmer{" "}
                {activeStudySession.participant_code}
              </>
            ) : (
              "Keine aktive Studien-Session."
            )}
          </DialogDescription>
          {steps.length > 0 && (
            <div className="pt-3">
              <WizardProgress current={stepIndex + 1} total={steps.length} />
            </div>
          )}
        </DialogHeader>

        <div className="flex-1 overflow-y-auto px-6 pb-2">
          {config === null && !error && (
            <p className="py-8 text-center text-sm text-muted-foreground">
              {finishingRun
                ? "Vergleichsblock bereits beantwortet — schließe Lauf ab (Export) …"
                : "Lade Fragebogen …"}
            </p>
          )}
          {config !== null && step && (
            <StepShell
              onBack={
                stepIndex > 0 ? () => setStepIndex((i) => i - 1) : undefined
              }
              onNext={next}
              nextLabel={isLast ? "Abschließen + Export" : "Weiter"}
              nextDisabled={!stepComplete || submitting || aborting}
              backDisabled={submitting || aborting}
            >
              {renderStep()}
            </StepShell>
          )}
        </div>

        <div className="shrink-0 border-t border-border/60 px-6 py-3">
          {error && (
            <div className="mb-3 rounded-xl border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">
              {error}
            </div>
          )}
          <div className="flex items-center justify-between gap-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => onClose(null, false)}
              disabled={submitting || aborting}
            >
              Dialog schließen
            </Button>
            <Button
              variant="ghost"
              size="sm"
              className="text-destructive hover:bg-destructive/10 hover:text-destructive"
              onClick={abortStudyRun}
              disabled={submitting || aborting || activeStudySession === null}
            >
              {aborting ? "Breche ab …" : "Studienlauf abbrechen"}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
