import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { useChatStore } from "@/store/chatStore";

export type GhState = "unknown" | "disconnected" | "connecting" | "connected";

const POLL_INTERVAL_MS = 3000;
const CONNECT_GRACE_MS = 8000;

export interface GrasshopperStatus {
  ghState: GhState;
  connectGrasshopper: () => Promise<void>;
}

/**
 * Single polling loop for Grasshopper connection state.
 *
 * Extracted from SystemStatusIndicator so App.tsx can instantiate it once
 * and pass both ghState and connectGrasshopper down to the two consumers
 * (SystemStatusIndicator + the Discoverability-Chip in the toolbar).
 * This prevents a second poll loop from running in parallel.
 */
export function useGrasshopperStatus(): GrasshopperStatus {
  const [ghState, setGhState] = useState<GhState>("unknown");
  const graceUntilRef = useRef<number>(0);
  const setViewportError = useChatStore((s) => s.setViewportError);

  useEffect(() => {
    let cancelled = false;

    const poll = async () => {
      try {
        const { connected } = await api.getGrasshopperStatus();
        if (cancelled) return;
        if (connected) {
          setGhState("connected");
        } else if (Date.now() >= graceUntilRef.current) {
          setGhState((prev) => (prev === "connecting" ? prev : "disconnected"));
        }
      } catch {
        if (cancelled || Date.now() < graceUntilRef.current) return;
        setGhState("disconnected");
      }
    };

    void poll();
    const intervalId = window.setInterval(poll, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
    };
  }, []);

  const connectGrasshopper = useCallback(async () => {
    if (ghState === "connecting" || ghState === "connected") return;
    setGhState("connecting");
    graceUntilRef.current = Date.now() + CONNECT_GRACE_MS;
    setViewportError(null);
    try {
      const resp = await api.connectGrasshopper();
      console.info("[GH connect] rhino_response:", resp.rhino_response);
      if (
        resp.rhino_response &&
        !resp.rhino_response.includes("loaded") &&
        !resp.rhino_response.includes("already_loaded")
      ) {
        setViewportError(
          `GH-Connect: ${resp.rhino_response.split(" | ")[0]}`,
        );
      }
      const { connected } = await api.getGrasshopperStatus();
      if (connected) setGhState("connected");
    } catch (err) {
      console.error("Grasshopper-Connect fehlgeschlagen", err);
      setViewportError(
        `GH-Connect fehlgeschlagen: ${err instanceof Error ? err.message : String(err)}`,
      );
      graceUntilRef.current = 0;
      setGhState("disconnected");
    }
  }, [ghState, setViewportError]);

  return { ghState, connectGrasshopper };
}
