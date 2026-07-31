/**
 * Per-Bedingungs-Survey (Studienartefakt-Spec §2.5; fragebogen-spec
 * §5.2, Lean-Variante).
 *
 * Wizard im Trans-Format (Spec §2.4): ein Likert-Item pro Bildschirm,
 * Anweisungs-Karte vor jedem Block, Freitext mit Zeichen-Hinweis.
 * Blöcke: Mini-CSI (6) → Agency (4, Wortlaut aus agency_items.json) →
 * Werkzeug-Items (gegated, Spec §4.3) → W-OPEN1 → R1.
 *
 * Die Item-Liste kommt fertig gegated aus /api/study/sessions/{id}/
 * survey-config — das Frontend kennt keine Gating-Logik. Nach dem
 * Submit: beim ersten Lauf wie bisher Session beenden + Export; beim
 * zweiten Lauf (is_final_run) signalisiert onClose stattdessen, dass
 * der Vergleichsblock (FinalSurveyDialog) folgt — Ende + Export
 * passieren dann dort.
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
  AgencySurveySubmission,
  ExportResponse,
  SurveyConfigResponse,
  SurveyLikertItem,
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
  /**
   * exported: Bundle-Toast-Daten (nur wenn hier exportiert wurde).
   * finished: Studien-Session ist beendet (Recording-Badge aus etc.).
   * openFinalSurvey: zweiter Lauf — App soll den Vergleichsblock öffnen.
   */
  onClose: (
    exported: ExportResponse | null,
    finished?: boolean,
    openFinalSurvey?: boolean,
  ) => void;
}

type Step =
  | { kind: "intro"; title: string; text: string }
  | { kind: "likert"; item: SurveyLikertItem; isTool: boolean }
  | { kind: "unused" }
  | { kind: "reflection" };

export function AgencySurveyDialog({ open, onClose }: Props) {
  const activeStudySession = useChatStore((s) => s.activeStudySession);

  const [config, setConfig] = useState<SurveyConfigResponse | null>(null);
  const [stepIndex, setStepIndex] = useState(0);
  const [likert, setLikert] = useState<Record<string, number | null>>({});
  const [notUsed, setNotUsed] = useState<Record<string, boolean>>({});
  const [unusedSelection, setUnusedSelection] = useState<string[]>([]);
  const [unusedFreetext, setUnusedFreetext] = useState("");
  const [reflection, setReflection] = useState("");
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
    setLikert({});
    setNotUsed({});
    setUnusedSelection([]);
    setUnusedFreetext("");
    setReflection("");
    setFinishingRun(false);
    (async () => {
      try {
        const [fetched, existing] = await Promise.all([
          api.getSurveyConfig(activeStudySession.id),
          api.getLatestAgencySurvey(activeStudySession.id),
        ]);
        if (cancelled) return;
        // Re-Entry-Guard: Block schon beantwortet (z.B. Dialog nach
        // Teilfehler geschlossen, Stop erneut gedrückt) → KEINE
        // Duplikat-Erhebung. Lauf 2: direkt zum Vergleichsblock; Lauf 1:
        // nur die ausstehenden Schritte (Ende + Export) nachholen.
        if (existing !== null) {
          if (fetched.is_final_run) {
            onClose(null, false, true);
            return;
          }
          setFinishingRun(true);
          await finishRun(() => cancelled);
          return;
        }
        setConfig(fetched);
      } catch (err) {
        if (cancelled) return;
        const msg = err instanceof Error ? err.message : String(err);
        setError(`Fragebogen konnte nicht geladen werden: ${msg}`);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open, activeStudySession]);

  const steps = useMemo<Step[]>(() => {
    if (!config) return [];
    const result: Step[] = [
      {
        kind: "intro",
        title: "Kurzer Fragebogen",
        text: config.intro,
      },
    ];
    for (const item of config.csi_items) {
      result.push({ kind: "likert", item, isTool: false });
    }
    if (config.agency_items.length > 0) {
      result.push({
        kind: "intro",
        title: "Kontrolle & Urheberschaft",
        text: "Im nächsten Block geht es darum, wie Sie Steuerung, Freiheit und Urheberschaft in der eben absolvierten Aufgabe erlebt haben.",
      });
      for (const item of config.agency_items) {
        result.push({ kind: "likert", item, isTool: false });
      }
    }
    if (config.collaboration_items.length > 0) {
      result.push({
        kind: "intro",
        title: "Zusammenarbeit mit der KI",
        text: "Einige kurze Aussagen dazu, wie sich die Zusammenarbeit mit der KI angefühlt hat.",
      });
      for (const item of config.collaboration_items) {
        result.push({ kind: "likert", item, isTool: false });
      }
    }
    if (config.tool_items.length > 0) {
      result.push({
        kind: "intro",
        title: "Eingabewege & Werkzeuge",
        text: "Zum Abschluss einige Fragen zu den einzelnen Eingabemöglichkeiten. Falls Sie etwas nicht genutzt haben, wählen Sie die entsprechende Option.",
      });
      for (const item of config.tool_items) {
        result.push({ kind: "likert", item, isTool: true });
      }
    }
    if (config.unused_question_label) {
      result.push({ kind: "unused" });
    }
    if (config.reflection_label) {
      result.push({ kind: "reflection" });
    }
    // Z3 Systemkompetenz bewusst als ALLERLETZTER Schritt: ein explizites
    // Kompetenz-Item vor den Werkzeug-/Agency-Ratings würde diese framen
    // (Manipulation-Check-Reihenfolge-Literatur; Fix 02.07.2026).
    if ((config.competence_items ?? []).length > 0) {
      result.push({
        kind: "intro",
        title: "Zum Abschluss",
        text: "Eine letzte Einschätzung zum System selbst.",
      });
      for (const item of config.competence_items) {
        result.push({ kind: "likert", item, isTool: false });
      }
    }
    return result;
  }, [config]);

  const step = steps[stepIndex];
  const isLast = stepIndex === steps.length - 1;

  // Likert-Items sind Pflicht (Spec §2.4); Werkzeug-Items gelten als
  // beantwortet, wenn entweder ein Wert oder „nicht genutzt“ gesetzt ist.
  const stepComplete = useMemo(() => {
    if (!step) return false;
    if (step.kind !== "likert") return true;
    if (step.isTool && notUsed[step.item.id]) return true;
    return likert[step.item.id] != null;
  }, [step, likert, notUsed]);

  // Ende + Export eines Laufs — gemeinsamer Schlussschritt für den
  // normalen Submit (Lauf 1) und den Re-Entry-Fall (Block schon
  // beantwortet, nur end/export stehen noch aus). Function declaration
  // (hoisted), damit der Load-Effect sie aufrufen kann.
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
    // Export immediately so the researcher leaves the dialog with
    // the bundle already on disk. Failures here don't undo the
    // survey/end events — we surface the error and let the
    // researcher retry the export from elsewhere.
    let exported: ExportResponse | null = null;
    try {
      exported = await api.exportStudySession(activeStudySession.id);
    } catch (err) {
      console.error("Export after survey failed", err);
    }
    // The dialog may have been torn down (session switch / unmount) during the
    // end/export awaits — don't fire onClose against a stale dialog, which would
    // null activeStudySession and toast an export for a run already left behind.
    if (shouldAbort()) return;
    onClose(exported, true);
  }

  const submit = async () => {
    if (!config || !activeStudySession || submitting || aborting) return;
    setSubmitting(true);
    setError(null);
    try {
      const payload: AgencySurveySubmission = {
        fragebogen_version_hash: config.fragebogen_version_hash,
        tools_unused_selection: unusedSelection,
        tools_unused_freetext: unusedFreetext,
        reflection_freetext: reflection,
        notes: "",
      };
      const record = payload as Record<string, unknown>;
      for (const item of [
        ...config.csi_items,
        ...config.agency_items,
        ...config.collaboration_items,
        ...(config.competence_items ?? []),
      ]) {
        record[item.id] = likert[item.id] ?? null;
      }
      for (const item of config.tool_items) {
        const skipped = !!notUsed[item.id];
        record[item.id] = skipped ? null : (likert[item.id] ?? null);
        record[`${item.id}_unused`] = skipped;
      }
      await api.submitAgencySurvey(activeStudySession.id, payload);

      if (config.is_final_run) {
        // Zweiter Lauf: Vergleichsblock folgt — Ende + Export erst dort.
        onClose(null, false, true);
        return;
      }
      await finishRun();
    } catch (err) {
      console.error("Survey submit failed", err);
      const msg = err instanceof Error ? err.message : String(err);
      setError(`Speichern fehlgeschlagen: ${msg}`);
    } finally {
      setSubmitting(false);
    }
  };

  const abortStudyRun = async () => {
    if (!activeStudySession || submitting || aborting) return;
    setAborting(true);
    setError(null);
    try {
      await api.abortStudySession(activeStudySession.id, {
        stage: "agency_survey",
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
        return <BlockIntro title={step.title} text={step.text} />;
      case "likert":
        return (
          <LikertScreen
            dimension={step.item.dimension}
            label={step.item.label}
            min={config.scale.min}
            max={config.scale.max}
            anchorMin={config.scale.anchor_min}
            anchorMax={config.scale.anchor_max}
            value={likert[step.item.id] ?? null}
            onChange={(v) =>
              setLikert((prev) => ({ ...prev, [step.item.id]: v }))
            }
            notUsedLabel={step.isTool ? config.tool_not_used_label : undefined}
            notUsed={step.isTool ? !!notUsed[step.item.id] : undefined}
            onToggleNotUsed={
              step.isTool
                ? () =>
                    setNotUsed((prev) => ({
                      ...prev,
                      [step.item.id]: !prev[step.item.id],
                    }))
                : undefined
            }
          />
        );
      case "unused":
        return (
          <div className="space-y-4">
            <p className="mx-auto max-w-md text-center text-base font-medium leading-relaxed">
              {config.unused_question_label}
            </p>
            {config.unused_options.length > 0 && (
              <div className="flex flex-wrap justify-center gap-2">
                {config.unused_options.map((opt) => (
                  <ChoicePill
                    key={opt.id}
                    label={opt.dimension}
                    selected={unusedSelection.includes(opt.id)}
                    onClick={() =>
                      setUnusedSelection((prev) =>
                        prev.includes(opt.id)
                          ? prev.filter((id) => id !== opt.id)
                          : [...prev, opt.id],
                      )
                    }
                  />
                ))}
              </div>
            )}
            <FreeTextScreen
              label=""
              value={unusedFreetext}
              onChange={setUnusedFreetext}
              maxChars={500}
              placeholder="Kurze Begründung (optional)"
            />
          </div>
        );
      case "reflection":
        return (
          <FreeTextScreen
            label={config.reflection_label}
            value={reflection}
            onChange={setReflection}
            maxChars={config.reflection_max_chars}
          />
        );
    }
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(nextOpen) => {
        // Esc / outside-click bleibt erlaubt, damit die Studienleitung
        // notfalls aussteigen kann — Survey ist nicht hart-blockierend.
        if (!nextOpen) onClose(null, false);
      }}
    >
      <DialogContent className="flex max-h-[90vh] max-w-xl flex-col gap-0 overflow-hidden p-0">
        <DialogHeader className="shrink-0 px-6 pb-4 pt-6">
          <DialogTitle>Fragebogen zur Aufgabe</DialogTitle>
          <DialogDescription>
            {activeStudySession ? (
              <>
                Lauf {activeStudySession.order_index}, Teilnehmer{" "}
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
                ? "Fragebogen bereits beantwortet — schließe Lauf ab (Export) …"
                : "Lade Fragebogen …"}
            </p>
          )}
          {config !== null && step && (
            <StepShell
              onBack={stepIndex > 0 ? () => setStepIndex((i) => i - 1) : undefined}
              onNext={next}
              nextLabel={
                isLast
                  ? config.is_final_run
                    ? "Weiter zum Vergleich"
                    : "Abschicken"
                  : "Weiter"
              }
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
