/**
 * Aufnahme-Indikator, der als Knopf ein kleines Popover oeffnet, um die
 * laufende Studien-Session mitten im Chat abzubrechen (der Stop-Button fuehrt
 * dagegen regulaer in Fragebogen + Export). Zweistufiger Abbruchschutz: erst
 * "abbrechen ...", dann explizit "Ja, abbrechen".
 *
 * Der Abbruch nutzt den bestehenden Endpoint (POST /api/study/sessions/{id}/
 * abort, Logging als study_session_aborted). Das visuelle Pulsieren entspricht
 * dem RecordingBadge.
 */
import { useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";
import type { StudySession } from "@/lib/types";

interface Props {
  session: StudySession;
  onAborted: () => void;
}

export function StudyAbortMenu({ session, onAborted }: Props) {
  const [open, setOpen] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [aborting, setAborting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  const close = () => {
    setOpen(false);
    setConfirming(false);
    setError(null);
  };

  // Klick ausserhalb + Escape schliessen das Popover (und setzen die
  // Bestaetigungsstufe zurueck), damit kein versehentlicher Abbruch offen bleibt.
  useEffect(() => {
    if (!open) return;
    const onDocClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) close();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const doAbort = async () => {
    if (aborting) return;
    setAborting(true);
    setError(null);
    try {
      await api.abortStudySession(session.id, {
        stage: "mid_run",
        reason: "researcher_cancelled_mid_run",
      });
      close();
      onAborted();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setAborting(false);
    }
  };

  return (
    <div ref={ref} className="relative shrink-0">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-label="Studienaufzeichnung — Optionen (Abbruch)"
        title="Studienaufzeichnung läuft — klicken für Abbruch"
        className="inline-flex cursor-pointer items-center rounded-full bg-brand-orange-soft px-2.5 py-1.5 transition-colors duration-200 hover:bg-brand-orange-soft/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
      >
        {/* In-place Opacity-Puls statt animate-ping: der scale(2)-Ring des Pings
            wurde in der Rhino-WebView als angeschnittener Kreis gerendert. Ein
            reiner Opacity-Puls hat keinen Transform, kann also nicht ueberstehen. */}
        <span className="inline-flex h-2.5 w-2.5 animate-pulse rounded-full bg-brand-orange" />
      </button>

      {open && (
        <div className="absolute left-0 top-full z-40 mt-2 w-64 rounded-xl border border-border/60 bg-card p-3 shadow-panel">
          <div className="mb-0.5 text-xs font-semibold text-foreground">
            Studien-Session läuft
          </div>
          <div className="mb-3 text-xs leading-relaxed text-muted-foreground">
            {session.participant_code} · {session.condition} · Aufgabe{" "}
            {session.task_variant}
            {session.is_pilot ? " · Pilot" : ""}
          </div>

          {!confirming ? (
            <button
              type="button"
              onClick={() => setConfirming(true)}
              className="w-full cursor-pointer rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs font-medium text-red-700 transition-colors duration-200 hover:bg-red-100"
            >
              Studien-Session abbrechen …
            </button>
          ) : (
            <div className="space-y-2">
              <div className="text-xs leading-relaxed text-muted-foreground">
                Wirklich verwerfen? Die Sitzung wird als{" "}
                <strong className="text-foreground">abgebrochen</strong> markiert
                und nicht ausgewertet.
              </div>
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={doAbort}
                  disabled={aborting}
                  className="flex-1 cursor-pointer rounded-lg bg-red-600 px-3 py-2 text-xs font-semibold text-white transition-colors duration-200 hover:bg-red-700 disabled:opacity-50"
                >
                  {aborting ? "Bricht ab …" : "Ja, abbrechen"}
                </button>
                <button
                  type="button"
                  onClick={() => setConfirming(false)}
                  disabled={aborting}
                  className="flex-1 cursor-pointer rounded-lg border border-border/60 bg-card px-3 py-2 text-xs font-medium text-foreground transition-colors duration-200 hover:bg-muted/40 disabled:opacity-50"
                >
                  Zurück
                </button>
              </div>
            </div>
          )}

          {error && (
            <div className="mt-2 text-xs text-red-600">
              Abbruch fehlgeschlagen: {error}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
