import { useEffect, useRef, useCallback } from "react";
import { useChatStore } from "@/store/chatStore";
import { api } from "@/lib/api";
import type {
  ComponentPickBlock,
  EditableStructureContext,
  ExposedParameter,
  GateClearedPayload,
  GatePreviewPayload,
  ImageBlock,
  ImageSource,
  Message,
  PointPickBlock,
  ReferenceBlock,
  RunCancelledPayload,
  SelectionBlock,
  Session,
  Settings,
  SketchView,
  Variant,
  WsCommand,
  WsEvent,
} from "@/lib/types";

function resolveWsUrl(): string {
  const loc = window.location;
  const proto = loc.protocol === "https:" ? "wss:" : "ws:";
  // Default: same host FastAPI serves from. In `vite dev`, the proxy in
  // vite.config.ts forwards /ws to localhost:8765.
  return `${proto}//${loc.host}/ws`;
}

export function useWebSocket() {
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimer = useRef<number | null>(null);
  const setConnected = useChatStore((s) => s.setConnected);
  const appendMessage = useChatStore((s) => s.appendMessage);
  const setMessages = useChatStore((s) => s.setMessages);
  const upsertSession = useChatStore((s) => s.upsertSession);
  const setSettings = useChatStore((s) => s.setSettings);
  const addStagedAttachment = useChatStore((s) => s.addStagedAttachment);
  const setViewportPending = useChatStore((s) => s.setViewportPending);
  const setPickModifiers = useChatStore((s) => s.setPickModifiers);
  const setViewportError = useChatStore((s) => s.setViewportError);
  const setPendingSend = useChatStore((s) => s.setPendingSend);
  const setSketchOverlay = useChatStore((s) => s.setSketchOverlay);
  const setActiveStructureContext = useChatStore(
    (s) => s.setActiveStructureContext,
  );
  const setExposedParameters = useChatStore((s) => s.setExposedParameters);
  const applyLocalParameterChange = useChatStore(
    (s) => s.applyLocalParameterChange,
  );
  const markParameterPending = useChatStore((s) => s.markParameterPending);
  const clearExposedParameters = useChatStore((s) => s.clearExposedParameters);
  const clearDismissedStructures = useChatStore(
    (s) => s.clearDismissedStructures,
  );
  const setVariants = useChatStore((s) => s.setVariants);
  const upsertVariant = useChatStore((s) => s.upsertVariant);
  const removeVariant = useChatStore((s) => s.removeVariant);
  const setActiveVariant = useChatStore((s) => s.setActiveVariant);
  const clearVariants = useChatStore((s) => s.clearVariants);
  const setRhinoSelectionCount = useChatStore((s) => s.setRhinoSelectionCount);
  // activeSessionId is intentionally NOT subscribed here: the WS handlers read
  // it via useChatStore.getState() so connect() stays stable across session
  // switches (a single persistent socket instead of reconnect-per-switch).
  const setPendingGate = useChatStore((s) => s.setPendingGate);
  const clearPendingGate = useChatStore((s) => s.clearPendingGate);
  const setRunActive = useChatStore((s) => s.setRunActive);
  // pickCallback is read via useChatStore.getState() inside the WS handler
  // to avoid stale-closure issues — no subscription needed here.

  const refreshSessionParameters = useCallback(
    async (sessionId: string | null | undefined) => {
      if (!sessionId) return;
      try {
        const parameters = await api.listExposedParameters(sessionId);
        setExposedParameters(parameters);
      } catch (err) {
        console.error("Refreshing parameters failed", err);
      }
    },
    [setExposedParameters],
  );

  const refreshSessionStructureContext = useCallback(
    async (sessionId: string | null | undefined) => {
      if (!sessionId) return;
      try {
        const context = await api.getStructureContext(sessionId);
        setActiveStructureContext(context);
      } catch (err) {
        console.error("Refreshing structure context failed", err);
      }
    },
    [setActiveStructureContext],
  );

  const refreshSessionVariants = useCallback(
    async (sessionId: string | null | undefined) => {
      if (!sessionId) return;
      try {
        const synced = await api.syncVariants(sessionId);
        setVariants(synced.variants);
      } catch (err) {
        console.error("Refreshing variants failed", err);
      }
    },
    [setVariants],
  );

  // On a transient reconnect (network blip, backend restart) that keeps the
  // same session, the App's message loader (keyed on activeSessionId) does NOT
  // re-run, and the backend has no per-client backlog/replay — so any
  // assistant turn that completed during the dead window is broadcast to no one
  // and would silently vanish from the chat. Re-fetch the full message list on
  // every (re)connect; setMessages dedups by id so a full reload is safe.
  const refreshSessionMessages = useCallback(
    async (sessionId: string | null | undefined) => {
      if (!sessionId) return;
      try {
        const msgs = await api.listMessages(sessionId);
        setMessages(msgs);
      } catch (err) {
        console.error("Refreshing messages failed", err);
      }
    },
    [setMessages],
  );

  const stageReferenceBlock = useCallback(
    (block: ReferenceBlock) => {
      const cb = useChatStore.getState().referenceTokenCallback;
      if (cb?.(block)) return;
      addStagedAttachment(block);
    },
    [addStagedAttachment],
  );

  // Parametrisieren-Ziel: der Pick nistet LIVE in den Befehls-Chip (statt als
  // freistehender Token). Fallback auf den normalen Token-Pfad, falls der
  // Composer/Befehls-Chip nicht montierbar ist — der Pick geht nie verloren.
  const stageCommandTarget = useCallback(
    (block: ReferenceBlock) => {
      const cb = useChatStore.getState().commandTargetCallback;
      if (cb?.(block)) return;
      stageReferenceBlock(block);
    },
    [stageReferenceBlock],
  );

  const connect = useCallback(() => {
    if (wsRef.current && wsRef.current.readyState <= 1) return;
    const ws = new WebSocket(resolveWsUrl());
    wsRef.current = ws;

    ws.onopen = () => {
      if (wsRef.current !== ws) return; // a newer socket already owns the ref
      setConnected(true);
      // Read the active session fresh (not via closure) so connect() does NOT
      // need activeSessionId as a dependency — otherwise the whole socket would
      // be torn down and rebuilt on every session switch (status flicker, lost
      // pick callbacks, redundant refreshes).
      const activeSessionId = useChatStore.getState().activeSessionId;
      // On (re)connect any in-flight agent call / viewport pick we were
      // waiting on is lost to us — the backend answered into the previous
      // socket or never at all. Clear the pending flags so the UI doesn't
      // stay permanently locked with a stuck send button. Also drop any
      // open sketch overlay whose backdrop was scoped to the dead socket.
      setPendingSend(false);
      setRunActive(false);
      setPendingGate(null);
      setViewportPending(null);
      setSketchOverlay(null);
      // Reset selection count on reconnect — we can't know the current
      // state until the backend sends a fresh viewport.selection_changed.
      setRhinoSelectionCount(0);
      void refreshSessionMessages(activeSessionId);
      void refreshSessionStructureContext(activeSessionId);
      void refreshSessionParameters(activeSessionId);
      void refreshSessionVariants(activeSessionId);
    };
    ws.onclose = () => {
      // A stale socket's late close handshake must not flip the status or
      // schedule a reconnect once a newer socket already owns the ref.
      if (wsRef.current !== ws) return;
      setConnected(false);
      if (reconnectTimer.current) window.clearTimeout(reconnectTimer.current);
      reconnectTimer.current = window.setTimeout(connect, 1500);
    };
    ws.onerror = () => {
      /* onclose will handle reconnect */
    };
    ws.onmessage = (ev) => {
      if (wsRef.current !== ws) return;
      let event: WsEvent;
      try {
        event = JSON.parse(ev.data);
      } catch {
        return;
      }
      // Read the active session fresh on every message so session routing does
      // not close over a stale value (which would force a per-switch reconnect).
      const activeSessionId = useChatStore.getState().activeSessionId;
      switch (event.type) {
        case "message.new":
          appendMessage(event.payload as unknown as Message);
          break;
        case "message.complete": {
          // The final assistant message already arrived via message.new;
          // this event only closes the turn — clear any pending-send state.
          // Only the ACTIVE session's completion may clear the global pending
          // state; a background session completing must not unlock the UI for
          // the session the user is watching.
          const completePayload = event.payload as { session_id?: string };
          if (
            completePayload.session_id &&
            completePayload.session_id !== activeSessionId
          )
            break;
          setPendingSend(false);
          setRunActive(false);
          void refreshSessionStructureContext(activeSessionId);
          void refreshSessionParameters(activeSessionId);
          void refreshSessionVariants(activeSessionId);
          break;
        }
        case "message.error": {
          const errPayload = event.payload as {
            session_id?: string;
            message?: string;
          };
          const sid = errPayload.session_id ?? activeSessionId;
          // Ignore a background session's error: it must not clear the active
          // session's pending state nor inject an error bubble into the view.
          if (sid !== activeSessionId) break;
          setPendingSend(false);
          setRunActive(false);
          if (!sid) break;
          const errMsg: Message = {
            id: `err-${Date.now()}`,
            session_id: sid,
            role: "assistant",
            content: [
              {
                type: "text",
                text: `⚠️ ${errPayload.message ?? "Fehler"}`,
              },
            ],
            created_at: new Date().toISOString(),
          };
          appendMessage(errMsg);
          break;
        }
        case "session.created":
          upsertSession(event.payload as unknown as Session);
          break;
        case "settings.updated":
          setSettings(event.payload as unknown as Settings);
          break;
        case "viewport.snapshot": {
          const payload = event.payload as {
            source: ImageSource;
            scene_text?: string;
          };
          const block: ImageBlock = {
            type: "image",
            source: payload.source,
            // Kein Referenzbild: markiert den Block fürs W2-Survey-Gate
            // (fragebogen-spec §5.2.4 — Viewport-Schnappschüsse zählen
            // nicht als hochgeladenes Bild).
            origin: "viewport",
            ...(payload.scene_text ? { caption: payload.scene_text } : {}),
          };
          addStagedAttachment(block);
          setViewportPending(null);
          setViewportError(null);
          break;
        }
        case "viewport.pick_result": {
          const payload = event.payload as {
            object_ids: string[];
            names: string[];
            types?: string[];
            snapshot: ImageSource | null;
          };
          // Session-Wechsel-Guard (analog sketch_ready): setActiveSession nullt
          // viewportPending (+ pickCallback + stagedAttachments). War beim
          // Eintreffen nichts mehr pending, kam der Pick aus einer inzwischen
          // verlassenen Session (oder wurde per Reconnect verworfen) -> das
          // Ergebnis NICHT in die jetzt aktive Session stagen (Cross-Session-Leak).
          const wasPending = useChatStore.getState().viewportPending;
          setViewportPending(null);
          setViewportError(null);
          if (!wasPending) break;
          // Read pick callback from live store state to avoid stale closure.
          const cb = useChatStore.getState().pickCallback;
          if (cb) {
            // One-shot: clear before invoking so a re-triggered pick won't
            // re-use a stale callback if the component unmounts between picks.
            useChatStore.getState().setPickCallback(null);
            const block: SelectionBlock | null =
              payload.object_ids && payload.object_ids.length > 0
                ? {
                    type: "selection",
                    object_ids: payload.object_ids,
                    names: payload.names ?? [],
                    types: payload.types ?? [],
                    ...(payload.snapshot ? { snapshot: payload.snapshot } : {}),
                  }
                : null;
            cb(block);
          } else {
            // Normal flow: stage for next send.
            // Empty object_ids → user cancelled the pick; stage nothing.
            if (payload.object_ids && payload.object_ids.length > 0) {
              const block: SelectionBlock = {
                type: "selection",
                object_ids: payload.object_ids,
                names: payload.names ?? [],
                types: payload.types ?? [],
                ...(payload.snapshot ? { snapshot: payload.snapshot } : {}),
              };
              stageReferenceBlock(block);
            }
          }
          break;
        }
        case "viewport.point_result": {
          const payload = event.payload as {
            point: number[] | null;
            snapshot: ImageSource | null;
            object_id?: string | null;
            object_name?: string | null;
            object_type?: string | null;
            snap_type?: string | null;
          };
          // Session-Wechsel-Guard (analog sketch_ready) — s. pick_result.
          const wasPending = useChatStore.getState().viewportPending;
          setViewportPending(null);
          setViewportError(null);
          if (!wasPending) break;
          // null → user pressed Esc; stage nothing.
          if (payload.point && payload.point.length === 3) {
            const block: PointPickBlock = {
              type: "point_pick",
              point: payload.point,
              ...(payload.snapshot ? { snapshot: payload.snapshot } : {}),
              ...(payload.object_id ? { object_id: payload.object_id } : {}),
              ...(payload.object_name ? { object_name: payload.object_name } : {}),
              ...(payload.object_type ? { object_type: payload.object_type } : {}),
              ...(payload.snap_type ? { snap_type: payload.snap_type } : {}),
            };
            stageReferenceBlock(block);
          }
          break;
        }
        case "viewport.pick_modifiers": {
          // Live-Modifier-Status vom Hover-Picker ({shift, object}). Treibt die
          // Live-Modus-Zeile im Pick-Banner. Ephemer + ohne session_id (ein Pick
          // gilt immer der aktiven Session); wird bei Pick-Ende vom Store geleert.
          const payload = event.payload as {
            shift?: boolean;
            object?: boolean;
          };
          setPickModifiers({
            shift: !!payload.shift,
            object: !!payload.object,
          });
          break;
        }
        case "viewport.component_result": {
          const payload = event.payload as {
            point: number[] | null;
            pick_point?: number[] | null;
            component_type?: "edge" | "face" | "vertex" | "object" | null;
            component_index?: number | null;
            component_info?: Record<string, unknown> | null;
            snapshot: ImageSource | null;
            object_id?: string | null;
            object_name?: string | null;
            object_type_name?: string | null;
            object_class?: string | null;
            geometry_class?: string | null;
            allowed_operations?: string[] | null;
            nest_target?: string | null;
          };
          // Ein Shift-Mehrfach-Pick streamt PRO gewaehlter Komponente ein
          // eigenes component_result-Event. Der erste Event leert
          // viewportPending; ein frueheres `if (!wasPending) break` verwarf
          // dadurch ALLE folgenden Komponenten desselben Picks — nur der erste
          // Chip erschien, obwohl der Viewport alle markierte. Daher hier NICHT
          // mehr auf wasPending gaten; die Payload-Validitaetspruefung unten
          // (Punkt + gueltiger Komponententyp + Index) laesst ohnehin nur echte
          // Komponenten durch, sodass jede gestreamte Komponente ihren Chip
          // erhaelt.
          setViewportPending(null);
          setViewportError(null);
          if (
            payload.point &&
            payload.point.length === 3 &&
            (payload.component_type === "edge" ||
              payload.component_type === "face" ||
              payload.component_type === "vertex" ||
              payload.component_type === "object") &&
            (payload.component_type === "object" ||
              typeof payload.component_index === "number")
          ) {
            const block: ComponentPickBlock = {
              type: "component_pick",
              component_type: payload.component_type,
              point: payload.point,
              ...(payload.pick_point && payload.pick_point.length === 3
                ? { pick_point: payload.pick_point }
                : {}),
              ...(payload.component_info ? { component_info: payload.component_info } : {}),
              ...(typeof payload.component_index === "number"
                ? { component_index: payload.component_index }
                : {}),
              ...(payload.snapshot ? { snapshot: payload.snapshot } : {}),
              ...(payload.object_id ? { object_id: payload.object_id } : {}),
              ...(payload.object_name ? { object_name: payload.object_name } : {}),
              ...(payload.object_type_name
                ? { object_type_name: payload.object_type_name }
                : {}),
              ...(payload.object_class
                ? { object_class: payload.object_class }
                : {}),
              ...(payload.geometry_class
                ? { geometry_class: payload.geometry_class }
                : {}),
              ...(payload.allowed_operations
                ? { allowed_operations: payload.allowed_operations }
                : {}),
            };
            // nest_target="command" -> der Pick wandert live in den
            // Parametrisieren-Befehls-Chip; sonst freistehender Referenz-Token.
            if (payload.nest_target === "command") {
              stageCommandTarget(block);
            } else {
              stageReferenceBlock(block);
            }
          }
          break;
        }
        case "viewport.sketch_ready": {
          const payload = event.payload as {
            views: SketchView[];
            composite: { source: ImageSource; width: number; height: number };
          };
          // Only act if a sketch is still wanted. cancel() clears
          // viewportPending, so a late response to a "Hintergrund neu laden"
          // the user already dismissed must NOT resurrect the overlay.
          const wasPending =
            useChatStore.getState().viewportPending === "sketch";
          setViewportPending(null);
          setViewportError(null);
          if (!wasPending) break;
          // The finished sketch never round-trips through the server —
          // SketchOverlay composites it client-side and stages a SketchBlock
          // locally on "Fertig". When this answers a refresh of an already-open
          // overlay, PRESERVE its identity (esp. reopenGroupId) and only swap
          // the backdrops — otherwise "Fertig" would mint a new group id and
          // duplicate the original staged blocks instead of replacing them.
          const currentOverlay = useChatStore.getState().sketchOverlay;
          if (currentOverlay) {
            setSketchOverlay({
              ...currentOverlay,
              views: payload.views,
              composite: payload.composite,
            });
          } else {
            setSketchOverlay({
              views: payload.views,
              composite: payload.composite,
            });
          }
          break;
        }
        case "viewport.error": {
          const payload = event.payload as { message?: string };
          setViewportPending(null);
          setViewportError(payload.message ?? "Viewport-Fehler");
          break;
        }
        case "structure_context.updated": {
          const payload = event.payload as {
            session_id: string;
            context: EditableStructureContext;
          };
          if (payload.session_id !== activeSessionId) break;
          setActiveStructureContext(payload.context);
          break;
        }
        case "structure_context.cleared": {
          const payload = event.payload as { session_id: string };
          if (payload.session_id !== activeSessionId) break;
          setActiveStructureContext(null);
          break;
        }
        case "parameter.exposed": {
          const payload = event.payload as {
            session_id: string;
            parameters: ExposedParameter[];
          };
          if (payload.session_id !== activeSessionId) break;
          setExposedParameters(payload.parameters);
          break;
        }
        case "parameter.cleared": {
          const payload = event.payload as { session_id: string };
          if (payload.session_id !== activeSessionId) break;
          clearExposedParameters();
          break;
        }
        case "parameter.updated": {
          const payload = event.payload as {
            session_id: string;
            name: string;
            new_value: number;
            current_value?: number;
            ok: boolean;
            message?: string;
          };
          if (payload.session_id !== activeSessionId) break;
          if (typeof payload.current_value === "number") {
            applyLocalParameterChange(payload.name, payload.current_value);
          }
          markParameterPending(payload.name, false);
          if (!payload.ok && payload.message) {
            setViewportError(`Slider '${payload.name}': ${payload.message}`);
          }
          break;
        }
        case "variant.added": {
          const payload = event.payload as {
            session_id: string;
            variant: Variant;
          };
          if (payload.session_id !== activeSessionId) break;
          upsertVariant(payload.variant);
          break;
        }
        case "variant.selected": {
          const payload = event.payload as {
            session_id: string;
            variant_id: string | null;
          };
          if (payload.session_id !== activeSessionId) break;
          setActiveVariant(payload.variant_id ?? null);
          break;
        }
        case "variant.removed": {
          const payload = event.payload as {
            session_id: string;
            variant_id: string;
          };
          if (payload.session_id !== activeSessionId) break;
          removeVariant(payload.variant_id);
          break;
        }
        case "variant.cleared": {
          const payload = event.payload as { session_id: string };
          if (payload.session_id !== activeSessionId) break;
          clearVariants();
          break;
        }
        case "viewport.selection_changed": {
          const p = event.payload as { count: number };
          setRhinoSelectionCount(p.count ?? 0);
          break;
        }
        case "viewport.undo_redo": {
          // A Rhino undo/redo happened → clear per-object parameter-panel
          // dismissals so a panel the user closed with × can reappear (the
          // designer's "undo brings it back" expectation).
          clearDismissedStructures();
          break;
        }
        case "gate.preview": {
          const gatePayload = event.payload as unknown as GatePreviewPayload;
          // Only pop the gate card for the session the user is watching — a
          // background session reaching a gated op must not steal the preview.
          if (
            gatePayload.session_id &&
            gatePayload.session_id !== activeSessionId
          )
            break;
          setPendingGate(gatePayload);
          break;
        }
        case "gate.cleared": {
          const clearedPayload = event.payload as unknown as GateClearedPayload;
          if (
            clearedPayload.session_id &&
            clearedPayload.session_id !== activeSessionId
          )
            break;
          clearPendingGate(clearedPayload.tu_id);
          break;
        }
        case "run.cancelled": {
          const cancelledPayload = event.payload as unknown as RunCancelledPayload;
          if (cancelledPayload.session_id === activeSessionId) {
            setPendingSend(false);
            setRunActive(false);
            setPendingGate(null);
          }
          break;
        }
        case "variant.thumbnail_ready": {
          const payload = event.payload as {
            session_id: string;
            variant_id: string;
            name: string;
            thumbnail: ImageSource;
          };
          if (payload.session_id !== activeSessionId) break;
          const existing = useChatStore
            .getState()
            .variants.find((v) => v.id === payload.variant_id);
          if (existing) {
            upsertVariant({ ...existing, thumbnail: payload.thumbnail });
          } else {
            void refreshSessionVariants(activeSessionId);
          }
          break;
        }
        default:
          break;
      }
    };
  }, [
    addStagedAttachment,
    applyLocalParameterChange,
    appendMessage,
    clearDismissedStructures,
    clearExposedParameters,
    clearPendingGate,
    clearVariants,
    markParameterPending,
    refreshSessionMessages,
    refreshSessionStructureContext,
    refreshSessionParameters,
    refreshSessionVariants,
    removeVariant,
    setActiveVariant,
    setConnected,
    setPendingGate,
    setRhinoSelectionCount,
    setRunActive,
    setExposedParameters,
    setActiveStructureContext,
    setVariants,
    upsertVariant,
    setPendingSend,
    setSettings,
    setSketchOverlay,
    setViewportError,
    setViewportPending,
    setPickModifiers,
    stageReferenceBlock,
    stageCommandTarget,
    upsertSession,
  ]);

  useEffect(() => {
    connect();
    return () => {
      if (reconnectTimer.current) window.clearTimeout(reconnectTimer.current);
      const ws = wsRef.current;
      wsRef.current = null;
      if (ws) {
        // Detach onclose first so this deliberate teardown does not schedule a
        // reconnect after the component unmounts.
        ws.onclose = null;
        ws.close();
      }
    };
  }, [connect]);

  const send = useCallback((cmd: WsCommand) => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return false;
    ws.send(JSON.stringify(cmd));
    return true;
  }, []);

  return { send };
}
