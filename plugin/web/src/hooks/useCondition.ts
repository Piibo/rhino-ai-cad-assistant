import { useChatStore } from "@/store/chatStore";
import type { Session } from "@/lib/types";

/**
 * Single source of truth for the active session's study condition
 * (Studienartefakt-Spec §1.2).
 *
 * Reads the condition off the currently active session. UI components
 * that gate themselves on basis vs. werkzeug consume this hook and
 * conditionally render — they never write the value. Writes happen only
 * at session creation (Settings default in dev mode; pre-session dialog
 * in study mode).
 *
 * Fail SAFE for study validity: when no session is active or the session's
 * condition has not resolved yet, default to "basis" (hide werkzeug-only
 * affordances) rather than "werkzeug". Exposing the full toolset to a basis
 * participant would invalidate the study; a brief missing-tool flash during
 * boot is the harmless direction. Loaded sessions always carry a condition
 * (DB column is NOT NULL DEFAULT 'werkzeug'), so this fallback only covers the
 * pre-resolve window (boot / reconnect before the session row lands).
 */
export function useCondition(): "basis" | "werkzeug" {
  const activeSessionId = useChatStore((s) => s.activeSessionId);
  const sessions = useChatStore((s) => s.sessions);
  if (!activeSessionId) return "basis";
  const active: Session | undefined = sessions.find(
    (s) => s.id === activeSessionId,
  );
  return active?.condition ?? "basis";
}
