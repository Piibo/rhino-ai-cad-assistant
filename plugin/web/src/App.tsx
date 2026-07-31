import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  Download,
  History,
  Plus,
  Redo2,
  Settings as SettingsIcon,
  StopCircle,
  Undo2,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { ChatView } from "@/features/chat/ChatView";
import { InputBar } from "@/features/chat/InputBar";
import { ParameterPanel } from "@/features/chat/ParameterPanel";
import { VariantGallery } from "@/features/chat/VariantGallery";
import { SketchOverlay } from "@/features/chat/SketchOverlay";
import { SessionHistoryDialog } from "@/features/chat/SessionHistoryDialog";
import { NewSessionDialog } from "@/features/chat/NewSessionDialog";
import { SystemStatusIndicator } from "@/features/grasshopper/SystemStatusIndicator";
import { SettingsDialog } from "@/features/settings/SettingsDialog";
import { AgencySurveyDialog } from "@/features/study/AgencySurveyDialog";
import { ConsentDialog } from "@/features/study/ConsentDialog";
import { DemographicsDialog } from "@/features/study/DemographicsDialog";
import { FinalSurveyDialog } from "@/features/study/FinalSurveyDialog";
import { PreSessionDialog } from "@/features/study/PreSessionDialog";
import { StudyAbortMenu } from "@/features/study/StudyAbortMenu";
import { useCondition } from "@/hooks/useCondition";
import { useGrasshopperStatus } from "@/hooks/useGrasshopperStatus";
import { useWebSocket } from "@/hooks/useWebSocket";
import { api } from "@/lib/api";
import { useChatStore } from "@/store/chatStore";
import type { ExportResponse, Session } from "@/lib/types";

export function App() {
  const { send } = useWebSocket();
  const connected = useChatStore((s) => s.connected);
  const sessions = useChatStore((s) => s.sessions);
  const activeSessionId = useChatStore((s) => s.activeSessionId);
  const setSessions = useChatStore((s) => s.setSessions);
  const setActiveSession = useChatStore((s) => s.setActiveSession);
  const setMessages = useChatStore((s) => s.setMessages);
  const settings = useChatStore((s) => s.settings);
  const setSettings = useChatStore((s) => s.setSettings);
  const setActiveStructureContext = useChatStore(
    (s) => s.setActiveStructureContext,
  );
  const setExposedParameters = useChatStore((s) => s.setExposedParameters);
  const setVariants = useChatStore((s) => s.setVariants);
  const activeStudySession = useChatStore((s) => s.activeStudySession);
  const setActiveStudySession = useChatStore((s) => s.setActiveStudySession);
  const activeConsent = useChatStore((s) => s.activeConsent);
  const setActiveConsent = useChatStore((s) => s.setActiveConsent);
  const preSessionDialogOpen = useChatStore((s) => s.preSessionDialogOpen);
  const setPreSessionDialogOpen = useChatStore((s) => s.setPreSessionDialogOpen);
  const consentDialogOpen = useChatStore((s) => s.consentDialogOpen);
  const setConsentDialogOpen = useChatStore((s) => s.setConsentDialogOpen);
  const agencySurveyOpen = useChatStore((s) => s.agencySurveyOpen);
  const setAgencySurveyOpen = useChatStore((s) => s.setAgencySurveyOpen);
  const demographicsDialogOpen = useChatStore((s) => s.demographicsDialogOpen);
  const setDemographicsDialogOpen = useChatStore(
    (s) => s.setDemographicsDialogOpen,
  );
  const finalSurveyOpen = useChatStore((s) => s.finalSurveyOpen);
  const setFinalSurveyOpen = useChatStore((s) => s.setFinalSurveyOpen);
  const [exportToast, setExportToast] = useState<ExportResponse | null>(null);
  const [pauseToast, setPauseToast] = useState<string | null>(null);
  // Sichtbare Fehler-Rueckmeldung (destruktiv gefaerbt), u. a. wenn das Anlegen
  // einer neuen Sitzung fehlschlaegt — sonst bliebe der Fehler nur in der
  // Dev-Konsole und der/die Versuchsleiter:in staende vor leerem Zustand.
  const [errorToast, setErrorToast] = useState<string | null>(null);
  // Reentrancy-Guard: verhindert, dass ein Doppelklick / ueberlappende
  // Abbruch+Neu-Aufrufe zwei parallele createSession-Calls (zwei Sitzungen)
  // ausloesen. Ref statt State, damit der Guard synchron innerhalb desselben
  // Ticks greift, ohne ein Re-Render abzuwarten.
  const creatingSessionRef = useRef(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [sessionsOpen, setSessionsOpen] = useState(false);
  const [newSessionOpen, setNewSessionOpen] = useState(false);
  const [historyPending, setHistoryPending] = useState<"undo" | "redo" | null>(null);
  // Study condition gates the werkzeug-only UI slots (Studienartefakt-Spec §1.1).
  // Header buttons (undo/redo/new session/history/settings) stay
  // visible in both conditions — they're global session controls, not
  // werkzeug slots in the spec sense.
  const condition = useCondition();
  const showWerkzeugSlots = condition === "werkzeug";
  // Single GH polling loop for the whole app — passed as props to both
  // SystemStatusIndicator and the Discoverability-Chip so no second poll
  // runs in parallel (Spec §1.3: conditional render only at Toolbar-Stelle).
  const { ghState, connectGrasshopper } = useGrasshopperStatus();
  // Study mode (Studienartefakt-Spec §2). When on, header gets the
  // recording badge and the "+"-button routes through the pre-session
  // dialog instead of creating a dev session directly.
  const studyMode = settings?.use_mode === "study";

  // Boot: load settings + sessions; create a session if none exist.
  // The panel can load before the in-Rhino backend is listening (Rhino still
  // starting, or a hard reload mid-run). Retry with capped backoff instead of
  // giving up after one failed attempt and leaving the app stuck on null
  // settings (which would also run condition gating on the fallback default).
  useEffect(() => {
    let cancelled = false;
    (async () => {
      for (let attempt = 0; !cancelled; attempt++) {
        try {
          const [loadedSettings, existing] = await Promise.all([
            api.getSettings(),
            api.listSessions(),
          ]);
          if (cancelled) return;
          setSettings(loadedSettings);
          setSessions(existing);
          const active = existing[0] ?? (await api.createSession({}));
          if (cancelled) return;
          if (!existing.includes(active)) setSessions([active, ...existing]);
          setActiveSession(active.id);
          return;
        } catch (err) {
          console.error(`Boot attempt ${attempt + 1} failed`, err);
          const delay = Math.min(1000 * 2 ** attempt, 8000);
          await new Promise((resolve) => setTimeout(resolve, delay));
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [setActiveSession, setMessages, setSessions, setSettings]);

  // Load messages + active sliders + variants when the active session changes.
  useEffect(() => {
    if (!activeSessionId) return;
    (async () => {
      try {
        const [msgs, params, structureContext, syncedVariants] = await Promise.all([
          api.listMessages(activeSessionId),
          api.listExposedParameters(activeSessionId),
          api.getStructureContext(activeSessionId),
          api.syncVariants(activeSessionId),
        ]);
        setMessages(msgs);
        setExposedParameters(params);
        setActiveStructureContext(structureContext);
        setVariants(syncedVariants.variants);
      } catch (err) {
        console.error("Loading session state failed", err);
      }
    })();
  }, [
    activeSessionId,
    setActiveStructureContext,
    setExposedParameters,
    setMessages,
    setVariants,
  ]);

  // Study-session + consent loader. Runs alongside the message loader
  // on session switch so the consent gate has fresh state. Lookup is
  // 200-null-bodied for dev sessions so we can distinguish "no study"
  // (activeStudySession === null) from "study, no consent yet".
  useEffect(() => {
    if (!activeSessionId) {
      setActiveStudySession(null);
      setActiveConsent(null);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const study = await api.getStudySessionBySession(activeSessionId);
        if (cancelled) return;
        if (study && study.status !== "active") {
          setActiveStudySession(null);
          setActiveConsent(null);
          setConsentDialogOpen(false);
          return;
        }
        setActiveStudySession(study);
        if (study) {
          const consent = await api.getLatestConsent(study.id);
          if (cancelled) return;
          setActiveConsent(consent);
          // Auto-open the consent dialog if a study run exists but the
          // participant hasn't agreed yet — mostly relevant on plugin
          // restart while the previous run was still pending.
          if (!consent) setConsentDialogOpen(true);
        } else {
          setActiveConsent(null);
        }
      } catch (err) {
        console.error("Loading study session failed", err);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [
    activeSessionId,
    setActiveConsent,
    setActiveStudySession,
    setConsentDialogOpen,
  ]);

  const activeSession = sessions.find((s) => s.id === activeSessionId);
  const headerTitle = getHeaderTitle(activeSession, activeStudySession);

  // Spawn a fresh session and switch to it. Used for "+ Neue Sitzung"
  // so the user can escape a context-poisoned conversation (e.g. one
  // where the LLM kept copy-pasting the same wrong tool call from
  // earlier turns).
  //
  // In study mode the same button instead opens the pre-session dialog
  // (Studienartefakt-Spec §2.1) so every run goes through the
  // participant_code / condition / variant capture.
  const newSession = useCallback(() => {
    // Study mode keeps its own lifecycle: the "+" routes through the
    // pre-session dialog (participant_code / condition / variant capture,
    // Studienartefakt-Spec §2.1) — the viewport-clear confirm is a dev/
    // RtD affordance and must not short-circuit that flow.
    if (studyMode) {
      setPreSessionDialogOpen(true);
      return;
    }
    setNewSessionOpen(true);
  }, [setPreSessionDialogOpen, studyMode]);

  // Confirmed from NewSessionDialog. When clearViewport is set, the
  // current geometry is first hidden (moved to a hidden, timestamped
  // "Sitzung <…>" layer — never deleted) so the new session starts on an
  // empty scene. The session row is then created either way.
  const confirmNewSession = useCallback(
    async (clearViewport: boolean) => {
      // Reentrancy-Guard: ein zweiter (paralleler) Aufruf — z. B. Doppelklick
      // oder Abbruch-Handler + Dialog-Confirm kurz hintereinander — wuerde sonst
      // eine zweite Sitzung anlegen. Erster Aufruf gewinnt, weitere no-op.
      if (creatingSessionRef.current) return;
      creatingSessionRef.current = true;
      try {
        if (clearViewport) {
          try {
            await api.clearViewport();
          } catch (err) {
            // A failed clear must not block the new session — log + carry on.
            console.error("Clearing viewport failed", err);
          }
        }
        const created = await api.createSession({});
        setSessions([created, ...sessions]);
        setActiveSession(created.id);
        setMessages([]);
      } catch (err) {
        console.error("Creating session failed", err);
        const msg = err instanceof Error ? err.message : String(err);
        setErrorToast(`Neue Sitzung konnte nicht erstellt werden: ${msg}`);
      } finally {
        creatingSessionRef.current = false;
        setNewSessionOpen(false);
      }
    },
    [sessions, setActiveSession, setMessages, setSessions],
  );

  const undo = useCallback(async () => {
    if (historyPending) return;
    setHistoryPending("undo");
    try {
      await api.undoRhino(activeSessionId ?? undefined);
    } catch (err) {
      console.error("Undo failed", err);
    } finally {
      setHistoryPending(null);
    }
  }, [activeSessionId, historyPending]);

  const redo = useCallback(async () => {
    if (historyPending) return;
    setHistoryPending("redo");
    try {
      await api.redoRhino(activeSessionId ?? undefined);
    } catch (err) {
      console.error("Redo failed", err);
    } finally {
      setHistoryPending(null);
    }
  }, [activeSessionId, historyPending]);

  // Demografie-Gate (fragebogen-spec §4.1): der Block D1–D11 wird
  // einmal pro Teilnehmenden-Lane erhoben, direkt nach dem Consent des
  // ersten Laufs. Pilot-Läufe haben eine eigene Lane.
  useEffect(() => {
    if (!studyMode || !activeStudySession || !activeConsent) return;
    let cancelled = false;
    (async () => {
      try {
        const existing = await api.getParticipantDemographics(
          activeStudySession.participant_code,
          activeStudySession.is_pilot,
        );
        if (cancelled) return;
        if (existing === null) setDemographicsDialogOpen(true);
      } catch (err) {
        console.error("Loading demographics state failed", err);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [studyMode, activeStudySession, activeConsent, setDemographicsDialogOpen]);

  // Pause-marker hotkey (Studienartefakt-Spec §2.3 — researcher key
  // command). Ctrl+Shift+P logs a pause_marker event against the
  // active study session. A short toast confirms the marker so the
  // researcher doesn't have to peek at the dev console.
  useEffect(() => {
    if (!studyMode) return;
    const handler = (e: KeyboardEvent) => {
      const key = e.key.toLowerCase();
      if (!e.ctrlKey || !e.shiftKey || key !== "p") return;
      if (!activeStudySession) return;
      e.preventDefault();
      const stamp = new Date();
      api
        .logStudyContextEvent(activeStudySession.id, "pause_marker", {
          source: "hotkey",
          captured_at_iso: stamp.toISOString(),
        })
        .then(() => {
          setPauseToast(`Pause-Marker gesetzt (${stamp.toLocaleTimeString()})`);
        })
        .catch((err) => {
          console.error("pause_marker failed", err);
          setPauseToast(`Pause-Marker fehlgeschlagen: ${err}`);
        });
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [activeStudySession, studyMode]);

  // Pause-toast auto-dismiss
  useEffect(() => {
    if (!pauseToast) return;
    const t = setTimeout(() => setPauseToast(null), 2500);
    return () => clearTimeout(t);
  }, [pauseToast]);

  // Error-toast auto-dismiss (laenger sichtbar als der Pause-Toast).
  useEffect(() => {
    if (!errorToast) return;
    const t = setTimeout(() => setErrorToast(null), 6000);
    return () => clearTimeout(t);
  }, [errorToast]);

  const sessionCanEnd =
    studyMode &&
    activeStudySession !== null &&
    activeConsent !== null;
  const recordingActive = sessionCanEnd;

  return (
    <div className="flex h-screen flex-col bg-background">
      {/* z-30: unter Dialog-Overlays und SketchOverlay (beide z-50), damit
          die Header-Karte bei Modals mit abgedunkelt wird und während des
          Skizzierens nicht klickbar über dem Overlay schwebt. */}
      <header className="relative z-30 m-2 flex items-center justify-between gap-2 rounded-2xl bg-card px-3 py-2 shadow-panel">
        <div className="flex min-w-0 items-center gap-2">
          <SystemStatusIndicator
            backendConnected={connected}
            ghState={ghState}
            connectGrasshopper={connectGrasshopper}
          />
          {headerTitle ? (
            <div className="truncate font-display text-sm font-semibold tracking-tight">
              {headerTitle}
            </div>
          ) : null}
          {recordingActive && activeStudySession ? (
            <StudyAbortMenu
              session={activeStudySession}
              onAborted={() => {
                setActiveStudySession(null);
                setActiveConsent(null);
                // Auf einen sauberen, leeren Zustand zuruecksetzen: sonst bliebe
                // der Chat-Session-Titel der abgebrochenen Studien-Session ("P.. –
                // Pilot (werkzeug)") oben stehen. Frische Session ohne Viewport-
                // Clear + ohne Pre-Session-Dialog -> Titel + Chat sind leer, der
                // Forscher startet den naechsten Lauf ueber "+".
                void confirmNewSession(false);
              }}
            />
          ) : null}
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <Button
            variant="ghost"
            size="icon"
            className="rounded-full"
            onClick={undo}
            aria-label="Rückgängig"
            title="Letzten Plugin-Schritt rückgängig machen"
            disabled={historyPending !== null}
          >
            <Undo2 className="h-4 w-4" />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            className="rounded-full"
            onClick={redo}
            aria-label="Wiederholen"
            title="Zuletzt rückgängig gemachten Plugin-Schritt wiederholen"
            disabled={historyPending !== null}
          >
            <Redo2 className="h-4 w-4" />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            className="rounded-full"
            onClick={newSession}
            aria-label="Neue Sitzung"
            title={
              studyMode
                ? "Neue Studien-Session starten (Pre-Session-Dialog)"
                : "Neue Sitzung starten (frischer Kontext)"
            }
          >
            <Plus className="h-4 w-4" />
          </Button>
          {sessionCanEnd && (
            <Button
              variant="ghost"
              size="icon"
              className="rounded-full"
              onClick={async () => {
                // Lauf-Modell (.3dm) JETZT sichern — VOR dem Fragebogen,
                // solange die Geometrie garantiert in der Szene liegt. Der
                // Export nutzt sie dann Reset-unabhaengig. Ein Fehler darf den
                // Lauf-Abschluss nie blockieren -> Fragebogen oeffnet immer.
                if (activeStudySession) {
                  try {
                    await api.saveRunModel(activeStudySession.id);
                  } catch (err) {
                    console.error("Lauf-Modell sichern fehlgeschlagen", err);
                  }
                }
                setAgencySurveyOpen(true);
              }}
              aria-label="Studien-Session beenden"
              title="Studien-Session beenden: Fragebogen + Export"
            >
              <StopCircle className="h-4 w-4" />
            </Button>
          )}
          <Button
            variant="ghost"
            size="icon"
            className="rounded-full"
            onClick={() => setSessionsOpen(true)}
            aria-label="Sitzungsverlauf"
            title="Frühere Sitzungen öffnen"
          >
            <History className="h-4 w-4" />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            className="rounded-full"
            onClick={() => setSettingsOpen(true)}
            aria-label="Einstellungen"
            title="Einstellungen öffnen"
          >
            <SettingsIcon className="h-4 w-4" />
          </Button>
        </div>
      </header>
      {showWerkzeugSlots && <VariantGallery onSend={send} />}
      <ChatView onSend={send} />
      {/* key per session: the panel keeps local collapse state and never
          unmounts within a session, so remounting on session switch is what
          resets it (paired with the per-session dismiss-latch reset). */}
      {showWerkzeugSlots && (
        <ParameterPanel key={activeSessionId ?? "no-session"} onSend={send} />
      )}
      <InputBar onSend={send} />
      {showWerkzeugSlots && <SketchOverlay onSend={send} />}
      <SessionHistoryDialog open={sessionsOpen} onOpenChange={setSessionsOpen} />
      <NewSessionDialog
        open={newSessionOpen}
        onOpenChange={setNewSessionOpen}
        onConfirm={confirmNewSession}
      />
      <SettingsDialog open={settingsOpen} onOpenChange={setSettingsOpen} />
      <PreSessionDialog
        open={preSessionDialogOpen}
        onOpenChange={setPreSessionDialogOpen}
      />
      {/* ConsentDialog is hard-blocking when open. We only mount it
          when there's an active study session without consent. The
          dialog itself ignores Esc/outside-click. */}
      <ConsentDialog
        open={
          consentDialogOpen &&
          activeStudySession !== null &&
          activeConsent === null
        }
      />
      <DemographicsDialog
        open={demographicsDialogOpen && activeStudySession !== null}
        onClose={() => setDemographicsDialogOpen(false)}
        onAbort={() => {
          // Wie beim Badge-Abbruch: Studien-Session raus, Consent raus, frische
          // leere Session (sonst bliebe der Studien-Titel oben stehen).
          setDemographicsDialogOpen(false);
          setActiveStudySession(null);
          setActiveConsent(null);
          void confirmNewSession(false);
        }}
      />
      <AgencySurveyDialog
        open={agencySurveyOpen && activeStudySession !== null}
        onClose={(exported, finished, openFinalSurvey) => {
          setAgencySurveyOpen(false);
          // Zweiter Lauf: nach dem Per-Bedingungs-Block folgt der
          // Vergleichsblock — Ende + Export passieren erst dort.
          if (openFinalSurvey) {
            setFinalSurveyOpen(true);
            return;
          }
          if (finished) {
            setActiveStudySession(null);
            setActiveConsent(null);
          }
          if (exported) setExportToast(exported);
        }}
      />
      <FinalSurveyDialog
        open={finalSurveyOpen && activeStudySession !== null}
        onClose={(exported, finished) => {
          setFinalSurveyOpen(false);
          if (finished) {
            setActiveStudySession(null);
            setActiveConsent(null);
          }
          if (exported) setExportToast(exported);
        }}
      />
      {pauseToast && (
        <div className="fixed bottom-4 left-4 z-50 flex max-w-[calc(100vw-2rem)] items-center gap-3 rounded-2xl bg-card p-3 pr-4 shadow-pop">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-brand-orange-soft">
            <span className="relative flex h-2 w-2">
              <span className="absolute inset-0 animate-ping rounded-full bg-brand-orange/60" />
              <span className="relative inline-flex h-2 w-2 rounded-full bg-brand-orange" />
            </span>
          </span>
          <span className="break-words text-xs font-medium text-foreground">
            {pauseToast}
          </span>
        </div>
      )}
      {errorToast && (
        <div
          role="alert"
          className="fixed bottom-4 left-4 z-50 flex max-w-[calc(100vw-2rem)] items-start gap-3 rounded-2xl border border-destructive/40 bg-card p-3 pr-4 shadow-pop"
        >
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-destructive/10">
            <AlertTriangle className="h-4 w-4 text-destructive" />
          </span>
          <span className="break-words text-xs font-medium text-foreground">
            {errorToast}
          </span>
        </div>
      )}
      {exportToast && (
        <div className="fixed right-4 top-16 z-50 flex max-w-[calc(100vw-2rem)] items-start gap-3 rounded-2xl bg-card p-4 shadow-pop sm:max-w-md">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary/10">
            <Download className="h-4 w-4 text-primary" />
          </span>
          <div className="min-w-0 flex-1">
            <div className="text-sm font-medium text-foreground">
              Bundle exportiert
            </div>
            <div className="mt-1 break-all text-xs text-muted-foreground">
              {exportToast.bundle_path}
            </div>
            <div className="mt-1 text-xs tabular-nums text-muted-foreground">
              {(exportToast.bytes / 1024).toFixed(1)} kB,{" "}
              {exportToast.files_included.length} Dateien
            </div>
          </div>
          <button
            type="button"
            onClick={() => setExportToast(null)}
            className="flex h-7 w-7 shrink-0 cursor-pointer items-center justify-center rounded-full text-muted-foreground transition-colors duration-200 hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
            aria-label="Schließen"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      )}
    </div>
  );
}

// Default dev sessions don't need a visible title; study sessions get a
// neutral label while full titles remain in history/export/debug.
function getHeaderTitle(
  session: Session | undefined,
  activeStudySession: { order_index: number; is_pilot: boolean } | null,
): string {
  if (!session) return "";
  if (activeStudySession) {
    return activeStudySession.is_pilot
      ? "Pilotlauf"
      : `Lauf ${activeStudySession.order_index}`;
  }
  const title = session.title?.trim();
  if (!title || title === "Neue Sitzung") return "";
  return title;
}
