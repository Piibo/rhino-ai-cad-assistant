/**
 * Pre-Session-Dialog (Studienartefakt-Spec §2.1).
 *
 * Studienleiter-facing modal that creates one study run plus its
 * backing plugin session. Opens when the "+" button is clicked while
 * study mode is active. The participant never sees this dialog — it's
 * meant to be filled in by the researcher before handing the laptop
 * over.
 *
 * order_index is derived server-side from the participant_code count;
 * the dialog only collects the human-chosen fields.
 */

import { useEffect, useRef, useState } from "react";
import { AlertTriangle } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Field } from "@/components/ui/field";
import { api } from "@/lib/api";
import { useChatStore } from "@/store/chatStore";
import type { StudyParticipantStatus, StudySessionCreate } from "@/lib/types";

interface Props {
  open: boolean;
  onOpenChange: (v: boolean) => void;
}

const EMPTY: StudySessionCreate = {
  participant_code: "",
  condition: "werkzeug",
  task_variant: "A",
  setting: "privat",
  is_pilot: false,
};

interface CycleSuggestion {
  condition: "basis" | "werkzeug";
  task_variant: "A" | "B";
}

// Counterbalancing-Viererzyklus fuer den ERSTEN Lauf eines Kuerzels
// (Studienartefakt-Spec §2.1, Entscheidung 18.06.2026):
//   P01, P05, … -> basis + A      P02, P06, … -> werkzeug + A
//   P03, P07, … -> basis + B      P04, P08, … -> werkzeug + B
// Der zweite Lauf ist ueber den Participant-Status ohnehin auf die
// Komplement-Bedingung/-Aufgabe gezwungen (disabled Buttons + Auto-Switch).
// Kuerzel ohne Zahl am Ende liefern keinen Vorschlag (frei waehlbar).
function cycleSuggestion(code: string): CycleSuggestion | null {
  const m = /(\d+)\s*$/.exec(code.trim());
  if (!m) return null;
  const idx = parseInt(m[1], 10);
  if (!Number.isFinite(idx) || idx < 1) return null;
  const pos = (idx - 1) % 4;
  return {
    condition: pos % 2 === 0 ? "basis" : "werkzeug",
    task_variant: pos < 2 ? "A" : "B",
  };
}

export function PreSessionDialog({ open, onOpenChange }: Props) {
  const settings = useChatStore((s) => s.settings);
  const setSessions = useChatStore((s) => s.setSessions);
  const setActiveSession = useChatStore((s) => s.setActiveSession);
  const setMessages = useChatStore((s) => s.setMessages);
  const setActiveStudySession = useChatStore((s) => s.setActiveStudySession);
  const setActiveConsent = useChatStore((s) => s.setActiveConsent);
  const setConsentDialogOpen = useChatStore((s) => s.setConsentDialogOpen);

  const [draft, setDraft] = useState<StudySessionCreate>(EMPTY);
  const [submitting, setSubmitting] = useState(false);
  const [participantStatus, setParticipantStatus] =
    useState<StudyParticipantStatus | null>(null);
  const [checkingParticipant, setCheckingParticipant] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Aktiver Counterbalancing-Vorschlag (nur echter Lauf 1). Wird pro
  // Kuerzel+Pilot-Kombination genau EINMAL in den Draft uebernommen
  // (appliedSuggestionKeyRef), damit ein bewusstes Umschalten durch die
  // Studienleitung nicht beim naechsten Status-Refresh zurueckgesetzt wird.
  const [suggested, setSuggested] = useState<CycleSuggestion | null>(null);
  const appliedSuggestionKeyRef = useRef("");

  // Spec §3.5 preflight — block the form when the backend wouldn't
  // accept the request anyway, so the researcher fixes Settings
  // *before* typing the participant code instead of hitting a 409
  // after.
  const apiPreflightReasons: string[] = [];
  if (settings) {
    if (settings.backend_mode !== "api") {
      apiPreflightReasons.push(
        "Backend-Modus muss 'Direkter API-Aufruf' sein (aktuell: " +
          settings.backend_mode +
          ").",
      );
    }
    if (settings.model === "local") {
      apiPreflightReasons.push(
        "Modell darf nicht 'local' sein — bitte ein Anthropic-Modell wählen.",
      );
    }
    if (!settings.api_key?.trim()) {
      apiPreflightReasons.push("Anthropic-API-Key fehlt in den Einstellungen.");
    }
  }
  const apiReady = apiPreflightReasons.length === 0;

  // Reset draft on each open so a previous attempt doesn't leak
  // participant data across runs.
  useEffect(() => {
    if (open) {
      setDraft(EMPTY);
      setParticipantStatus(null);
      setCheckingParticipant(false);
      setError(null);
      setSuggested(null);
      appliedSuggestionKeyRef.current = "";
    }
  }, [open]);

  const update = <K extends keyof StudySessionCreate>(
    key: K,
    value: StudySessionCreate[K],
  ) => setDraft((prev) => ({ ...prev, [key]: value }));

  useEffect(() => {
    if (!open) return;
    const participantCode = draft.participant_code.trim();
    if (!participantCode) {
      setParticipantStatus(null);
      setCheckingParticipant(false);
      return;
    }

    let cancelled = false;
    setCheckingParticipant(true);
    const timer = window.setTimeout(async () => {
      try {
        const status = await api.getStudyParticipantStatus(
          participantCode,
          draft.is_pilot,
        );
        if (cancelled) return;
        setParticipantStatus(status);
        // Counterbalancing-Automatik (Spec §2.1): fuer den ERSTEN echten Lauf
        // eines Kuerzels Bedingung + Aufgabe aus dem Viererzyklus vorschlagen
        // und einmalig setzen. Lauf 2 erzwingt das Komplement bereits ueber
        // available_conditions/-task_variants; Pilotlaeufe bleiben frei.
        if (!draft.is_pilot && status.used_conditions.length === 0) {
          const sug = cycleSuggestion(participantCode);
          setSuggested(sug);
          const key = `${participantCode}|pilot=${draft.is_pilot}`;
          if (sug && appliedSuggestionKeyRef.current !== key) {
            appliedSuggestionKeyRef.current = key;
            setDraft((prev) => ({
              ...prev,
              condition: sug.condition,
              task_variant: sug.task_variant,
            }));
          }
        } else {
          setSuggested(null);
        }
        if (
          !draft.is_pilot &&
          status.available_conditions.length > 0 &&
          !status.available_conditions.includes(draft.condition)
        ) {
          setDraft((prev) => ({
            ...prev,
            condition: status.available_conditions[0] ?? prev.condition,
          }));
        }
        if (
          !draft.is_pilot &&
          status.available_task_variants.length > 0 &&
          !status.available_task_variants.includes(draft.task_variant)
        ) {
          setDraft((prev) => ({
            ...prev,
            task_variant:
              status.available_task_variants[0] ?? prev.task_variant,
          }));
        }
      } catch (err) {
        if (cancelled) return;
        console.error("Participant status check failed", err);
        setParticipantStatus(null);
      } finally {
        if (!cancelled) setCheckingParticipant(false);
      }
    }, 200);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [draft.condition, draft.task_variant, draft.is_pilot, draft.participant_code, open]);

  const participantBlockedReason =
    !draft.is_pilot && participantStatus?.active_run_open
      ? "Für dieses Kürzel ist noch ein Studienlauf aktiv. Bitte erst beenden oder abbrechen."
      : !draft.is_pilot &&
          participantStatus !== null &&
          participantStatus.available_conditions.length === 0
        ? "Für dieses Kürzel sind bereits beide Bedingungen angelegt."
        : null;

  const canSubmit =
    apiReady &&
    draft.participant_code.trim().length > 0 &&
    !checkingParticipant &&
    participantBlockedReason === null &&
    !submitting;

  const submit = async () => {
    if (!canSubmit) return;
    setSubmitting(true);
    setError(null);
    try {
      const study = await api.createStudySession({
        ...draft,
        participant_code: draft.participant_code.trim(),
      });
      // Counterbalancing-Audit-Trail (Spec §2.1): bewusste Abweichung vom
      // Zyklusvorschlag als Studien-Ereignis festhalten. Fire-and-forget —
      // ein Logging-Fehler darf den Lauf-Start nicht blockieren.
      if (suggestionDeviation && suggested) {
        api
          .logStudyContextEvent(study.id, "counterbalancing_override", {
            participant_code: draft.participant_code.trim(),
            suggested_condition: suggested.condition,
            suggested_task_variant: suggested.task_variant,
            chosen_condition: draft.condition,
            chosen_task_variant: draft.task_variant,
          })
          .catch((err) =>
            console.error("Counterbalancing-Override-Logging fehlgeschlagen", err),
          );
      }
      // Frischer Start pro Lauf: die Geometrie des vorigen Laufs wird auf einen
      // versteckten, timestamped „Sitzung <…>"-Layer archiviert (NIE gelöscht),
      // damit jeder Teilnehmer/Lauf auf leerer Szene beginnt. Ohne dies blieb das
      // Objekt des vorigen Laufs im Viewport stehen. Ein Fehlschlag darf den Lauf
      // nicht blockieren → loggen + weiter.
      try {
        await api.clearViewport();
      } catch (err) {
        console.error("Clearing viewport for new study session failed", err);
      }
      // Pull fresh session list so the new plugin session appears in
      // the sidebar with its study title intact.
      const updatedSessions = await api.listSessions();
      setSessions(updatedSessions);
      setActiveSession(study.session_id);
      setMessages([]);
      setActiveStudySession(study);
      const consent = await api.getLatestConsent(study.id);
      setActiveConsent(consent);
      onOpenChange(false);
      // ConsentDialog opens immediately — the participant must agree
      // before the first chat send unless the backend reused consent.
      setConsentDialogOpen(consent === null);
    } catch (err) {
      console.error("Pre-Session-Dialog submit failed", err);
      const msg = err instanceof Error ? err.message : String(err);
      setError(`Anlegen fehlgeschlagen: ${msg}`);
    } finally {
      setSubmitting(false);
    }
  };

  const conditionLabel = (condition: "basis" | "werkzeug") =>
    condition === "basis" ? "Basis" : "Werkzeug";

  const usedConditionText =
    participantStatus && participantStatus.used_conditions.length > 0
      ? participantStatus.used_conditions.map(conditionLabel).join(" + ")
      : "";

  const participantDescription =
    draft.participant_code.trim().length === 0
      ? "Frei wählbar, z.B. P01, P02. Für Tests am besten Pilot-Sitzung aktivieren."
      : checkingParticipant
        ? "Prüfe vorhandene Studienläufe …"
        : draft.is_pilot
          ? "Pilot-Sitzung: wiederholbar, blockiert keine echte Teilnehmer-ID."
          : participantBlockedReason
            ? participantBlockedReason
            : usedConditionText
              ? `Bereits angelegt: ${usedConditionText}. Die andere Bedingung bleibt wählbar.`
              : "Noch keine echte Studienbedingung für dieses Kürzel angelegt.";

  // Weicht die aktuelle Wahl vom Lauf-1-Zyklusvorschlag ab? (Nur echte Läufe;
  // im zweiten Lauf gibt es keinen Vorschlag, dort zwingt der Status ohnehin.)
  const suggestionDeviation =
    !draft.is_pilot &&
    suggested !== null &&
    (draft.condition !== suggested.condition ||
      draft.task_variant !== suggested.task_variant);

  const suggestionLabel = suggested
    ? `${conditionLabel(suggested.condition)} + Aufgabe ${suggested.task_variant}`
    : "";

  const conditionDescription = draft.is_pilot
    ? "Pilot-Sitzungen können beliebig oft wiederholt werden."
    : suggested && !suggestionDeviation
      ? `Automatisch nach Counterbalancing gesetzt (${suggestionLabel}). Abweichen ist möglich und wird protokolliert.`
      : suggestionDeviation
        ? `Weicht vom Counterbalancing-Vorschlag (${suggestionLabel}) ab — wird als Studien-Ereignis protokolliert.`
        : participantStatus?.has_reusable_consent
          ? "Einwilligung liegt für den aktuellen Text schon vor; der nächste Lauf startet ohne zweite Abfrage."
          : "Echte Studienläufe sind pro Kürzel und Bedingung einmalig.";

  const conditionDisabled = (condition: "basis" | "werkzeug") =>
    !draft.is_pilot &&
    participantStatus !== null &&
    !participantStatus.available_conditions.includes(condition);

  // Counterbalancing: im zweiten Lauf ist die in Lauf 1 genutzte Aufgabe gesperrt
  // (analog zur Bedingung), damit nicht versehentlich dieselbe Aufgabe zweimal laeuft.
  const taskVariantDisabled = (task: "A" | "B") =>
    !draft.is_pilot &&
    participantStatus !== null &&
    !participantStatus.available_task_variants.includes(task);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Neue Studien-Session</DialogTitle>
          <DialogDescription className="leading-relaxed">
            Studienleiter-Vorbereitung. Diese Felder werden mit der Session
            verknüpft und bestimmen u.a., welche Werkzeug-Konfiguration die
            Teilnehmenden bekommen.
          </DialogDescription>
        </DialogHeader>

        {!apiReady && (
          <div className="my-2 flex items-start gap-3 rounded-xl bg-brand-orange-soft px-3.5 py-3 text-xs leading-relaxed text-brand-orange-deep">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            <div className="min-w-0">
              <div className="font-semibold">
                Voraussetzungen nicht erfüllt (Studienartefakt-Spec §3.5):
              </div>
              <ul className="mt-1 list-inside list-disc space-y-0.5">
                {apiPreflightReasons.map((r) => (
                  <li key={r} className="break-words">
                    {r}
                  </li>
                ))}
              </ul>
              <div className="mt-1">
                Bitte zuerst in den Einstellungen anpassen, dann hier erneut
                öffnen.
              </div>
            </div>
          </div>
        )}

        <div className="space-y-4 py-2">
          <Field
            label="Teilnehmer-Kürzel"
            description={participantDescription}
          >
            <Input
              value={draft.participant_code}
              onChange={(e) => update("participant_code", e.target.value)}
              placeholder="P01"
              autoFocus
            />
          </Field>

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 sm:gap-3">
            <Field label="Bedingung" description={conditionDescription}>
              <div className="grid grid-cols-2 gap-1 rounded-full bg-muted p-1">
                <button
                  type="button"
                  onClick={() => update("condition", "werkzeug")}
                  disabled={conditionDisabled("werkzeug")}
                  aria-pressed={draft.condition === "werkzeug"}
                  className={`cursor-pointer whitespace-nowrap rounded-full px-2 py-1.5 text-sm font-medium transition-colors duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60 disabled:cursor-not-allowed disabled:opacity-40 ${
                    draft.condition === "werkzeug"
                      ? "bg-card text-foreground shadow-sm"
                      : "text-muted-foreground hover:text-foreground"
                  }`}
                >
                  Werkzeug
                </button>
                <button
                  type="button"
                  onClick={() => update("condition", "basis")}
                  disabled={conditionDisabled("basis")}
                  aria-pressed={draft.condition === "basis"}
                  className={`cursor-pointer whitespace-nowrap rounded-full px-2 py-1.5 text-sm font-medium transition-colors duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60 disabled:cursor-not-allowed disabled:opacity-40 ${
                    draft.condition === "basis"
                      ? "bg-card text-foreground shadow-sm"
                      : "text-muted-foreground hover:text-foreground"
                  }`}
                >
                  Basis
                </button>
              </div>
            </Field>
            <Field label="Aufgabenvariante">
              <div className="grid grid-cols-2 gap-1 rounded-full bg-muted p-1">
                <button
                  type="button"
                  onClick={() => update("task_variant", "A")}
                  disabled={taskVariantDisabled("A")}
                  aria-pressed={draft.task_variant === "A"}
                  className={`cursor-pointer whitespace-nowrap rounded-full px-2 py-1.5 text-sm font-medium transition-colors duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60 disabled:cursor-not-allowed disabled:opacity-40 ${
                    draft.task_variant === "A"
                      ? "bg-card text-foreground shadow-sm"
                      : "text-muted-foreground hover:text-foreground"
                  }`}
                >
                  A
                </button>
                <button
                  type="button"
                  onClick={() => update("task_variant", "B")}
                  disabled={taskVariantDisabled("B")}
                  aria-pressed={draft.task_variant === "B"}
                  className={`cursor-pointer whitespace-nowrap rounded-full px-2 py-1.5 text-sm font-medium transition-colors duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60 disabled:cursor-not-allowed disabled:opacity-40 ${
                    draft.task_variant === "B"
                      ? "bg-card text-foreground shadow-sm"
                      : "text-muted-foreground hover:text-foreground"
                  }`}
                >
                  B
                </button>
              </div>
            </Field>
          </div>

          <Field label="Setting" description="Informativ, kein UI-Effekt.">
            <select
              className="h-10 w-full cursor-pointer rounded-xl border border-input bg-card px-3 text-sm transition-colors duration-200 hover:border-muted-foreground/40 focus-visible:border-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
              value={draft.setting}
              onChange={(e) =>
                update(
                  "setting",
                  e.target.value as "privat" | "lab" | "remote",
                )
              }
            >
              <option value="privat">Privat (Einzelsitzung)</option>
              <option value="lab">Lab</option>
              <option value="remote">Remote</option>
            </select>
          </Field>

          <div className="flex items-center justify-between gap-3 rounded-xl bg-muted/40 px-4 py-3">
            <div>
              <Label htmlFor="pilot-toggle">Pilot-Sitzung</Label>
              <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                Wiederholbar und blockiert keine echte Teilnehmer-ID.
              </p>
            </div>
            <Switch
              id="pilot-toggle"
              checked={draft.is_pilot}
              onCheckedChange={(v) => update("is_pilot", v)}
            />
          </div>
        </div>

        {error && (
          <div className="rounded-xl border border-destructive/30 bg-destructive/10 px-3.5 py-2.5 text-xs text-destructive">
            {error}
          </div>
        )}

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Abbrechen
          </Button>
          <Button onClick={submit} disabled={!canSubmit}>
            {submitting
              ? "Lege Lauf an …"
              : participantStatus?.has_reusable_consent
                ? "Lauf anlegen + direkt starten"
                : "Lauf anlegen + Einwilligung"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
