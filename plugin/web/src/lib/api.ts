// Thin REST wrapper. The WebSocket handles reactive state; this file
// is only for one-shot fetches (initial session load, settings boot,
// file uploads).

import type {
  AgencyItemsResponse,
  AgencySurveyRecord,
  AgencySurveySubmission,
  Consent,
  ConsentSubmission,
  ConsentTextResponse,
  DemographicsConfigResponse,
  DemographicsRecord,
  DemographicsSubmission,
  EditableStructureContext,
  ExportResponse,
  ExposedParameter,
  FinalSurveyConfigResponse,
  FinalSurveyRecord,
  FinalSurveySubmission,
  ImageSource,
  Message,
  Session,
  SessionDebugDump,
  Settings,
  StudyContextEvent,
  StudyContextEventType,
  StudyParticipantStatus,
  StudySession,
  StudySessionCreate,
  SurveyConfigResponse,
  Variant,
} from "./types";

const BASE = ""; // same-origin; FastAPI serves us

async function errorFromResponse(res: Response): Promise<Error> {
  // Surface FastAPI's structured detail (e.g. {detail: "participant_code already
  // has 2 runs"}) instead of a bare status — the operator needs the real reason,
  // and statusText is empty under HTTP/2.
  let detail = "";
  try {
    const body = (await res.json()) as { detail?: unknown };
    const d = body?.detail;
    detail = typeof d === "string" ? d : d ? JSON.stringify(d) : "";
  } catch {
    try {
      detail = await res.text();
    } catch {
      /* ignore */
    }
  }
  return new Error(
    detail ? `${res.status} ${detail}` : `${res.status} ${res.statusText}`,
  );
}

async function http<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(BASE + path, {
    cache: "no-store",
    headers: {
      "Content-Type": "application/json",
      "Cache-Control": "no-cache",
      Pragma: "no-cache",
      ...(init?.headers ?? {}),
    },
    ...init,
  });
  if (!res.ok) throw await errorFromResponse(res);
  return res.json();
}

export interface BackendVersion {
  backend_sha: string;
  backend_started: string;
  backend_mode: string;
  use_mode: string;
}

export const api = {
  // Code-Stand des laufenden Backend-Prozesses (Deploy-Gap sichtbar machen).
  getVersion: () => http<BackendVersion>("/api/version"),
  listSessions: () => http<Session[]>("/api/sessions"),
  createSession: (session: Partial<Session>) =>
    http<Session>("/api/sessions", {
      method: "POST",
      body: JSON.stringify({
        title: "Neue Sitzung",
        use_mode: "normal",
        ...session,
      }),
    }),
  listMessages: (sessionId: string) =>
    http<Message[]>(`/api/sessions/${sessionId}/messages`),
  getSessionDebugDump: (sessionId: string) =>
    http<SessionDebugDump>(`/api/sessions/${sessionId}/debug-dump`),
  listExposedParameters: (sessionId: string) =>
    http<ExposedParameter[]>(`/api/sessions/${sessionId}/parameters`),
  getStructureContext: (sessionId: string) =>
    http<EditableStructureContext | null>(
      `/api/sessions/${sessionId}/structure-context`,
    ),
  listVariants: (sessionId: string) =>
    http<Variant[]>(`/api/sessions/${sessionId}/variants`),
  syncVariants: (sessionId: string) =>
    http<{ status: string; variants: Variant[]; active_variant_id: string | null }>(
      `/api/sessions/${sessionId}/variants/sync`,
      { method: "POST" },
    ),
  getSettings: () => http<Settings>("/api/settings"),
  updateSettings: (patch: Partial<Settings>) =>
    http<Settings>("/api/settings", {
      method: "PATCH",
      body: JSON.stringify(patch),
    }),
  uploadImage: async (file: File): Promise<ImageSource> => {
    const form = new FormData();
    form.append("file", file);
    const res = await fetch(`${BASE}/api/upload/image`, {
      method: "POST",
      body: form,
    });
    if (!res.ok) throw await errorFromResponse(res);
    const data = await res.json();
    return {
      type: "base64",
      media_type: data.media_type,
      data: data.data,
    };
  },
  // Grasshopper "Click-to-Connect" header button. The status endpoint
  // pings the GH HTTP server (lives inside grasshopper_mcp_client.gh).
  // The connect endpoint opens GH and loads that file via Rhino's
  // Grasshopper .NET API on the UI thread.
  getGrasshopperStatus: () =>
    http<{ connected: boolean }>("/api/grasshopper/status"),
  connectGrasshopper: () =>
    http<{ status: string; rhino_response?: string }>(
      "/api/grasshopper/connect",
      { method: "POST" },
    ),
  // HITL commit: bake the live GH geometry into real Rhino objects so the
  // designer can keep working with them. User-triggered (the "Backen"
  // button), never agent-triggered.
  bakeGrasshopper: (sessionId: string) =>
    http<{ status: string; rhino_response?: string; baked_count?: number }>(
      "/api/grasshopper/bake",
      { method: "POST", body: JSON.stringify({ session_id: sessionId }) },
    ),
  // Verwerfen-Gegenstueck zu Bake: bricht die aktive GH-Parametrisierung ab
  // (GH-Chain weg, Slider/Kontext clear) und holt die beim Parametrisieren
  // gepickten Original-Objekte zurueck (sourceObjectIds -> Restore aus
  // Active/Archive). User-getriggert (der "Verwerfen"-Button).
  abortGrasshopper: (sessionId: string, sourceObjectIds: string[]) =>
    http<{ status: string; restored?: string }>("/api/grasshopper/abort", {
      method: "POST",
      body: JSON.stringify({
        session_id: sessionId,
        source_object_ids: sourceObjectIds,
      }),
    }),
  undoRhino: (sessionId?: string) =>
    http<{ status: string; result: string }>("/api/rhino/undo", {
      method: "POST",
      body: JSON.stringify({ session_id: sessionId ?? null }),
    }),
  redoRhino: (sessionId?: string) =>
    http<{ status: string; result: string }>("/api/rhino/redo", {
      method: "POST",
      body: JSON.stringify({ session_id: sessionId ?? null }),
    }),
  // Hide (not delete) all visible geometry so a new session starts on a
  // clean viewport. Backend moves everything onto a hidden, timestamped
  // "Sitzung <…>" layer — recoverable via Rhino's Layer-Panel or Ctrl+Z.
  clearViewport: () =>
    http<{ status: string; moved: number; layer: string; rhino_response?: string }>(
      "/api/rhino/clear-viewport",
      { method: "POST" },
    ),
  // ---- Studienartefakt-Spec §2 (Schritt 2) ----
  getConsentText: () =>
    http<ConsentTextResponse>("/api/study/consent-text"),
  createStudySession: (payload: StudySessionCreate) =>
    http<StudySession>("/api/study/sessions", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  getStudySessionBySession: (sessionId: string) =>
    http<StudySession | null>(
      `/api/study/sessions/by-session/${sessionId}`,
    ),
  getStudyParticipantStatus: (participantCode: string, isPilot = false) =>
    http<StudyParticipantStatus>(
      `/api/study/participants/${encodeURIComponent(
        participantCode,
      )}/status?is_pilot=${isPilot ? "true" : "false"}`,
    ),
  submitConsent: (studySessionId: string, payload: ConsentSubmission) =>
    http<Consent>(`/api/study/sessions/${studySessionId}/consent`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  getLatestConsent: (studySessionId: string) =>
    http<Consent | null>(`/api/study/sessions/${studySessionId}/consent`),
  logStudyContextEvent: (
    studySessionId: string,
    eventType: StudyContextEventType,
    payload: Record<string, unknown> = {},
  ) =>
    http<StudyContextEvent>(
      `/api/study/sessions/${studySessionId}/events`,
      {
        method: "POST",
        body: JSON.stringify({
          study_session_id: studySessionId,
          event_type: eventType,
          payload,
        }),
      },
    ),
  getAgencyItems: () =>
    http<AgencyItemsResponse>("/api/study/agency-items"),
  // ---- Lean-Fragebogen (fragebogen-spec v1.2.7) ----
  getSurveyConfig: (studySessionId: string) =>
    http<SurveyConfigResponse>(
      `/api/study/sessions/${studySessionId}/survey-config`,
    ),
  getLatestAgencySurvey: (studySessionId: string) =>
    http<AgencySurveyRecord | null>(
      `/api/study/sessions/${studySessionId}/survey`,
    ),
  submitAgencySurvey: (
    studySessionId: string,
    payload: AgencySurveySubmission,
  ) =>
    http<AgencySurveyRecord>(
      `/api/study/sessions/${studySessionId}/survey`,
      {
        method: "POST",
        body: JSON.stringify(payload),
      },
    ),
  getDemographicsConfig: () =>
    http<DemographicsConfigResponse>("/api/study/demographics-config"),
  getParticipantDemographics: (participantCode: string, isPilot = false) =>
    http<DemographicsRecord | null>(
      `/api/study/participants/${encodeURIComponent(
        participantCode,
      )}/demographics?is_pilot=${isPilot ? "true" : "false"}`,
    ),
  submitDemographics: (
    studySessionId: string,
    payload: DemographicsSubmission,
  ) =>
    http<DemographicsRecord>(
      `/api/study/sessions/${studySessionId}/demographics`,
      {
        method: "POST",
        body: JSON.stringify(payload),
      },
    ),
  getFinalSurveyConfig: (studySessionId: string) =>
    http<FinalSurveyConfigResponse>(
      `/api/study/sessions/${studySessionId}/final-survey-config`,
    ),
  getLatestFinalSurvey: (studySessionId: string) =>
    http<FinalSurveyRecord | null>(
      `/api/study/sessions/${studySessionId}/final-survey`,
    ),
  submitFinalSurvey: (
    studySessionId: string,
    payload: FinalSurveySubmission,
  ) =>
    http<FinalSurveyRecord>(
      `/api/study/sessions/${studySessionId}/final-survey`,
      {
        method: "POST",
        body: JSON.stringify(payload),
      },
    ),
  endStudySession: (studySessionId: string) =>
    http<StudyContextEvent>(
      `/api/study/sessions/${studySessionId}/end`,
      { method: "POST" },
    ),
  // Sichert das aktuelle Lauf-Modell (.3dm) fuer diese Session — beim
  // Lauf-Ende (Stop-Button), bevor der Fragebogen kommt und die Szene
  // spaeter neu geladen wird. Der Export bevorzugt diese Datei.
  saveRunModel: (studySessionId: string) =>
    http<{ saved: boolean }>(
      `/api/study/sessions/${studySessionId}/save-run-model`,
      { method: "POST" },
    ),
  abortStudySession: (
    studySessionId: string,
    payload: Record<string, unknown> = {},
  ) =>
    http<StudyContextEvent>(
      `/api/study/sessions/${studySessionId}/abort`,
      {
        method: "POST",
        body: JSON.stringify(payload),
      },
    ),
  exportStudySession: (studySessionId: string) =>
    http<ExportResponse>(
      `/api/study/sessions/${studySessionId}/export`,
      { method: "POST" },
    ),
};
