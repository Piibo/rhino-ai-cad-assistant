import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
  type ChangeEvent,
  type ClipboardEvent,
  type KeyboardEvent,
} from "react";
import {
  Crosshair,
  Paperclip,
  Pencil,
  SendHorizontal,
  SlidersHorizontal,
  Square,
  SquareDashedMousePointer,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { useCondition } from "@/hooks/useCondition";
import { api } from "@/lib/api";
import { useChatStore } from "@/store/chatStore";
import { cn } from "@/lib/utils";
import { buildSketchOverlayFromGroup } from "./sketch-overlay-builder";
import { AttachmentStrip } from "./AttachmentStrip";
import {
  blockKey,
  createCommandTokenElement,
  createReferenceTokenElement,
  findBackspaceReferenceTarget,
  parseReferenceBlock,
  placeCaretAfterNode,
  placeCaretInParent,
  rangeBelongsTo,
  restoreComposerRange,
  serializeComposerContent,
} from "./composer-dom";
import type {
  ContentBlock,
  ImageBlock,
  PointPickBlock,
  ReferenceBlock,
  SketchBlock,
  WsCommand,
} from "@/lib/types";

interface InputBarProps {
  onSend: (cmd: WsCommand) => boolean;
}

interface ReferenceComposerHandle {
  clear: () => void;
  focus: () => void;
  insertReference: (block: ReferenceBlock) => boolean;
  insertCommand: (
    commandText: string,
    label: string,
    nestedBlocks?: ReferenceBlock[],
  ) => boolean;
  addCommandTarget: (block: ReferenceBlock) => boolean;
  // Bereits im Feld liegende Objekt-/Komponenten-Referenzen (Auswahl + K/F/O
  // Kante/Flaeche) in den Parametrisieren-Befehls-Chip nisten — legt ihn an oder
  // mergt in einen bestehenden (Punkt-Picks/Text bleiben). true bei >=1 Ziel.
  nestRefsAsCommand: () => boolean;
  hasCommand: () => boolean;
  // object_ids der im Befehls-Chip genesteten Ziele (eindeutig). Beim Senden
  // einer Parametrisierung erfasst -> Quelle fuer den spaeteren Abort-Restore.
  commandTargetObjectIds: () => string[];
  removeReferences: () => void;
  serialize: () => ContentBlock[];
}

// Befehlstext hinter dem "Parametrisieren"-Chip — GRANULARITAETSABHAENGIG:
//   - ganzes Objekt als Ziel  -> komplett parametrisch nachbauen (alle Maße als
//     Slider) = die bisherige Bedeutung.
//   - markierte Kante/Flaeche -> NUR dieses Element wird ueber einen Slider
//     veraenderbar (Rest bleibt fix). WAS genau (Richtung/Maß/Art) bestimmt der
//     Nutzer per Freitext — der geht mit der Nachricht raus und hat Vorrang;
//     sonst waehlt die KI die naheliegendste einzelne Stellgroesse. Bewusst
//     NEUTRAL gehalten (kein vorgegebener Chamfer) -> der Nutzer steuert die Details.
// Beides bleibt moeglich; die Wahl trifft parametricInstruction() anhand der
// Ziel-Granularitaet. Frontend-Canned-Text (serialisiert in die User-Nachricht)
// -> hash-neutral.
const PARAMETRIC_INSTRUCTION_WHOLE =
  "Baue das ausgewählte Objekt als parametrisches Grasshopper-Objekt nach: Analysiere seine Geometrie, erstelle eine GH-Definition, die es möglichst genau reproduziert, und exponiere die für dieses Objekt sinnvollen Maße als Slider. Verschiebe das Rhino-Original auf den Archive-Layer, sobald die GH-Variante steht.";
const PARAMETRIC_INSTRUCTION_SELECTIVE =
  "Mache das/die markierte(n) Element(e) — die referenzierte(n) Kante(n)/Fläche(n) — über einen Grasshopper-Slider gezielt veränderbar, sodass sich die Geometrie an dieser Stelle anpassen lässt und der Rest des Objekts unverändert bleibt. Richte dich danach, was ich im Text dazu schreibe (z. B. welche Richtung, welches Maß oder welche Art der Änderung) — meine Anweisung hat Vorrang. Wenn ich nichts Konkretes vorgebe, wähle die naheliegendste einzelne Stellgröße, die die Lage oder Größe dieses Elements steuert; lege dich nicht von vornherein auf eine Fase oder Verrundung fest. Baue eine Grasshopper-Definition, die das Objekt reproduziert, aber exponiere als Slider AUSSCHLIESSLICH diese eine (bzw. diese wenigen) Stellgröße(n) — nicht alle Maße des Objekts; die übrige Geometrie bleibt fest. Verschiebe das Rhino-Original auf den Archive-Layer, sobald die GH-Variante steht.";

// Welcher Text? Sobald EIN Ziel eine Kante/Flaeche ist -> selektiv (nur dieses
// Maß); sonst (ganze Objekte) -> komplettes Nachbauen.
function parametricInstruction(blocks: ReferenceBlock[]): string {
  const hasFeatureTarget = blocks.some(
    (b) =>
      b.type === "component_pick" &&
      (b.component_type === "edge" || b.component_type === "face"),
  );
  return hasFeatureTarget
    ? PARAMETRIC_INSTRUCTION_SELECTIVE
    : PARAMETRIC_INSTRUCTION_WHOLE;
}

export function InputBar({ onSend }: InputBarProps) {
  const [uploadingImage, setUploadingImage] = useState(false);
  const [composerHasContent, setComposerHasContent] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const composerRef = useRef<ReferenceComposerHandle | null>(null);
  const connected = useChatStore((s) => s.connected);
  const activeSessionId = useChatStore((s) => s.activeSessionId);
  const pendingSend = useChatStore((s) => s.pendingSend);
  const activeStudySession = useChatStore((s) => s.activeStudySession);
  const activeConsent = useChatStore((s) => s.activeConsent);
  const setConsentDialogOpen = useChatStore((s) => s.setConsentDialogOpen);
  // Study condition: "basis" hides every viewport/sketch/pick
  // slot (Studienartefakt-Spec §1.1). Text + image upload + send stay
  // available in both conditions.
  const condition = useCondition();
  const showWerkzeugSlots = condition === "werkzeug";
  // Study-mode consent gate (Studienartefakt-Spec §2.7). When a study
  // run is active but consent isn't recorded, block the send and let
  // the user open the consent dialog from the hint.
  const consentRequired =
    activeStudySession !== null && activeConsent === null;
  const stagedAttachments = useChatStore((s) => s.stagedAttachments);
  const setSketchOverlay = useChatStore((s) => s.setSketchOverlay);
  const addStagedAttachment = useChatStore((s) => s.addStagedAttachment);
  const removeStagedAttachment = useChatStore((s) => s.removeStagedAttachment);
  const clearStagedAttachments = useChatStore((s) => s.clearStagedAttachments);
  const viewportPending = useChatStore((s) => s.viewportPending);
  const setViewportPending = useChatStore((s) => s.setViewportPending);
  const pickModifiers = useChatStore((s) => s.pickModifiers);
  const viewportError = useChatStore((s) => s.viewportError);
  const setViewportError = useChatStore((s) => s.setViewportError);
  const setPendingSend = useChatStore((s) => s.setPendingSend);
  const runActive = useChatStore((s) => s.runActive);
  const setRunActive = useChatStore((s) => s.setRunActive);
  const requestCollapseParameters = useChatStore(
    (s) => s.requestCollapseParameters,
  );
  const bakeNotice = useChatStore((s) => s.bakeNotice);
  const setBakeNotice = useChatStore((s) => s.setBakeNotice);
  const setGhSourceObjectIds = useChatStore((s) => s.setGhSourceObjectIds);
  const setPickCallback = useChatStore((s) => s.setPickCallback);
  const setReferenceTokenCallback = useChatStore(
    (s) => s.setReferenceTokenCallback,
  );
  const setCommandTargetCallback = useChatStore(
    (s) => s.setCommandTargetCallback,
  );
  const rhinoSelectionCount = useChatStore((s) => s.rhinoSelectionCount);
  const [ghPickPending, setGhPickPending] = useState(false);
  // Persistente kleine Punkt-Marker (Referenz-Ansicht) der inline gepickten
  // Punkte: pointMarkerKeyRef = zuletzt gesendete Punktmenge (Dedup);
  // markersFrozenRef haelt die Dots ueber das Senden hinweg bis der Run fertig
  // ist; prevRunActiveRef erkennt das Run-Ende.
  const pointMarkerKeyRef = useRef("[]");
  // Persistente DUENNE Markierung der inline gepickten K/F/O-Komponenten
  // (Kante/Flaeche/Objekt bleiben duenn blau, solange das Token im Composer steht).
  const componentRefsKeyRef = useRef("[]");
  const markersFrozenRef = useRef(false);
  const prevRunActiveRef = useRef(false);
  const prevViewportPendingRef = useRef(viewportPending);

  // Nach einem abgeschlossenen ODER abgebrochenen Viewport-Pick den Tastatur-
  // Fokus zurueck in den Composer holen, damit man sofort weitertippen kann (statt
  // erst ins Feld klicken zu muessen). Feuert NUR beim Uebergang non-null -> null,
  // nie beim Mount; deckt point/component/object/pick/sketch einheitlich ab. Die
  // einzelnen Pick-Callbacks rufen .focus() zwar selbst, aber ohne garantierte
  // Reihenfolge ggue. dem Re-Enable des Composers -> dieser Effekt ist die
  // verlaessliche, modusuebergreifende Fokus-Rueckgabe.
  useEffect(() => {
    const prev = prevViewportPendingRef.current;
    prevViewportPendingRef.current = viewportPending;
    if (prev !== null && viewportPending === null) {
      composerRef.current?.focus();
    }
  }, [viewportPending]);

  // Sicherheits-Timeout fuer haengende Picks. Ein modaler Rhino-GetPoint/
  // GetObject blockiert den UI-Thread; friert Rhino dabei ein (Modal-Dialog
  // einer anderen App im Vordergrund o.ae.), kehrt der Pick nie zurueck und
  // viewportPending bliebe gesetzt -> Composer + Senden dauerhaft gesperrt, die
  // Person sitzt fest. Nach 120 s den Pick clientseitig aufgeben + Hinweis
  // zeigen, damit die Eingabe wieder frei ist. (Ein evtl. spaeter doch noch
  // eintreffendes Pick-Ergebnis wird vom bestehenden ghPickPending-/Inline-
  // Pfad ohnehin sauber behandelt.) 'sketch' ist ausgenommen: das ist ein
  // reines Frontend-Overlay ohne blockierenden Rhino-Call.
  useEffect(() => {
    if (!viewportPending || viewportPending === "sketch") return;
    const timer = window.setTimeout(() => {
      setViewportPending(null);
      setViewportError(
        "Pick abgebrochen — Rhino hat nicht reagiert. Bitte erneut versuchen.",
      );
    }, 120000);
    return () => window.clearTimeout(timer);
  }, [viewportPending, setViewportPending, setViewportError]);

  // Auto-dismiss the bake-success notice after 6 s.
  useEffect(() => {
    if (!bakeNotice) return;
    const t = setTimeout(() => setBakeNotice(null), 6000);
    return () => clearTimeout(t);
  }, [bakeNotice, setBakeNotice]);

  useEffect(() => {
    if (!showWerkzeugSlots) {
      setReferenceTokenCallback(null);
      setCommandTargetCallback(null);
      composerRef.current?.removeReferences();
      return;
    }
    setReferenceTokenCallback(
      (block) => composerRef.current?.insertReference(block) ?? false,
    );
    setCommandTargetCallback(
      (block) => composerRef.current?.addCommandTarget(block) ?? false,
    );
    return () => {
      setReferenceTokenCallback(null);
      setCommandTargetCallback(null);
    };
  }, [setCommandTargetCallback, setReferenceTokenCallback, showWerkzeugSlots]);

  useEffect(() => {
    // Beim Sitzungswechsel entsperren; clear() loest via onChange ein
    // syncPointMarkers aus, das die Dots der alten Sitzung ausblendet.
    markersFrozenRef.current = false;
    composerRef.current?.clear();
    setComposerHasContent(false);
  }, [activeSessionId]);

  const canSend =
    connected &&
    !pendingSend &&
    !viewportPending &&
    !uploadingImage &&
    !!activeSessionId &&
    !consentRequired &&
    (composerHasContent || stagedAttachments.length > 0);

  // Punkt-Referenz-Marker im Viewport mit den inline gepickten Punkten
  // synchronisieren (nur werkzeug). Waehrend eines laufenden Runs eingefroren
  // -> die gesendeten Dots bleiben bis zum Run-Ende sichtbar.
  const syncPointMarkers = useCallback(() => {
    if (!showWerkzeugSlots || markersFrozenRef.current) return;
    const blocks = composerRef.current?.serialize() ?? [];
    const points = blocks
      .filter((b): b is PointPickBlock => b.type === "point_pick")
      .map((b) => b.point);
    const key = JSON.stringify(points);
    if (key === pointMarkerKeyRef.current) return;
    const ok = onSend({ type: "viewport.point_markers", payload: { points } });
    if (ok) pointMarkerKeyRef.current = key;
  }, [showWerkzeugSlots, onSend]);

  // Persistente DUENNE Komponenten-Markierung: die inline gepickten K/F/O-
  // Komponenten bleiben duenn blau im Viewport, solange ihre Tokens im Composer
  // stehen. Minimale Referenz je Block (ohne Snapshot) -> leichtgewichtig.
  const syncComponentHighlights = useCallback(() => {
    if (!showWerkzeugSlots || markersFrozenRef.current) return;
    const blocks = composerRef.current?.serialize() ?? [];
    // Persistente duenne Markierung fuer ALLE Objekt-/Komponenten-Referenzen im
    // Composer — freistehender K/F/O-Chip, Auswahl-Chip ODER als Ziel im
    // Parametrisieren-Chip verschachtelt (serialize() emittiert die nested Ziele
    // mit). Auswahl-Bloecke (ganze Objekte) werden je object_id zu einer
    // "object"-Referenz expandiert, damit alle Refs dieselbe Form haben.
    const refs = blocks.flatMap((b) => {
      if (b.type === "component_pick") {
        return [
          {
            type: "component_pick" as const,
            object_id: b.object_id,
            component_type: b.component_type,
            component_index: b.component_index,
          },
        ];
      }
      if (b.type === "selection") {
        return b.object_ids.map((oid) => ({
          type: "component_pick" as const,
          object_id: oid,
          component_type: "object" as const,
          component_index: undefined,
        }));
      }
      return [];
    });
    const key = JSON.stringify(refs);
    if (key === componentRefsKeyRef.current) return;
    const ok = onSend({
      type: "viewport.persistent_refs",
      payload: { blocks: refs },
    });
    if (ok) componentRefsKeyRef.current = key;
  }, [showWerkzeugSlots, onSend]);

  // Run nach dem Senden fertig -> die Punkt-Referenz-Dots wieder ausblenden
  // (waren nur Referenz fuer diese Nachricht) und entsperren.
  useEffect(() => {
    if (prevRunActiveRef.current && !runActive) {
      markersFrozenRef.current = false;
      if (pointMarkerKeyRef.current !== "[]") {
        onSend({ type: "viewport.point_markers", payload: { points: [] } });
        pointMarkerKeyRef.current = "[]";
      }
      if (componentRefsKeyRef.current !== "[]") {
        onSend({ type: "viewport.persistent_refs", payload: { blocks: [] } });
        componentRefsKeyRef.current = "[]";
      }
    }
    prevRunActiveRef.current = runActive;
  }, [runActive, onSend]);

  const triggerImageUpload = useCallback(() => {
    if (!connected || pendingSend || uploadingImage) return;
    fileInputRef.current?.click();
  }, [connected, pendingSend, uploadingImage]);

  const onImageFilesSelected = useCallback(
    async (event: ChangeEvent<HTMLInputElement>) => {
      const files = Array.from(event.target.files ?? []);
      event.target.value = "";
      if (files.length === 0) return;

      setViewportError(null);
      setUploadingImage(true);
      try {
        for (const file of files) {
          if (!file.type.startsWith("image/")) {
            throw new Error(`'${file.name}' ist keine Bilddatei.`);
          }
          const source = await api.uploadImage(file);
          const block: ImageBlock = { type: "image", source };
          addStagedAttachment(block);
        }
      } catch (err) {
        const message =
          err instanceof Error ? err.message : "Bild-Upload fehlgeschlagen.";
        setViewportError(message);
      } finally {
        setUploadingImage(false);
      }
    },
    [addStagedAttachment, setViewportError],
  );

  const requestPoint = useCallback(() => {
    if (!connected || viewportPending) return;
    setViewportError(null);
    setViewportPending("point");
    onSend({
      type: "viewport.request_point",
      payload: {},
    });
  }, [connected, onSend, setViewportError, setViewportPending, viewportPending]);

  const requestComponent = useCallback(() => {
    if (!connected || viewportPending) return;
    setViewportError(null);
    setViewportPending("component");
    onSend({
      type: "viewport.request_component",
      payload: {
        component_type: "auto",
        session_id: activeSessionId,
      },
    });
  }, [
    activeSessionId,
    connected,
    onSend,
    setViewportError,
    setViewportPending,
    viewportPending,
  ]);

  const requestSketch = useCallback(() => {
    if (!connected || viewportPending) return;
    // Gibt es eine bereits gestagte (noch NICHT gesendete) Skizze, diese wieder
    // oeffnen statt neu zu erfassen -> der Nutzer verliert seine Striche nicht.
    // Neueste Gruppe gewinnt; bereits GESENDETE Skizzen liegen nicht in
    // stagedAttachments und kommen daher (gewollt) nicht zurueck. Frischen
    // Hintergrund holt man dann ueber "Hintergrund neu laden" im Editor.
    const stagedSketches = stagedAttachments.filter(
      (b): b is SketchBlock => b.type === "sketch",
    );
    const latestGroup = stagedSketches[stagedSketches.length - 1]?.sketch_group;
    if (latestGroup) {
      const members = stagedAttachments.filter(
        (b): b is SketchBlock =>
          b.type === "sketch" && b.sketch_group === latestGroup,
      );
      const overlay = buildSketchOverlayFromGroup(members, latestGroup);
      if (overlay) {
        setViewportError(null);
        setSketchOverlay(overlay);
        return;
      }
    }
    setViewportError(null);
    setViewportPending("sketch");
    onSend({
      type: "viewport.request_sketch",
      payload: { max_size: 1600 },
    });
  }, [
    connected,
    onSend,
    setViewportError,
    setViewportPending,
    viewportPending,
    stagedAttachments,
    setSketchOverlay,
  ]);

  // GH-Parametrik-Button: captures the current Rhino selection via the
  // existing pick flow (one-shot pickCallback) and sends a prefixed GH
  // instruction message — same path as a normal pick+send, no duplication.
  const requestGhParametric = useCallback(() => {
    if (!connected || viewportPending || ghPickPending) return;
    setViewportError(null);
    // EIN einziger "Parametrisieren"-Befehls-Chip deckt ALLE Ziele ab; es wird nie
    // ein zweiter Befehl angelegt. Jeder Pfad nistet Ziele ueber addCommandTarget,
    // das den Chip beim ersten Ziel ANLEGT und bei jedem weiteren an den bestehenden
    // ANHAENGT. Dadurch ist ein erneuter Parametrisieren-Klick mit frisch gepickten
    // Refs ein MERGE in den vorhandenen Chip (frueher: No-Op, sobald ein Befehl da
    // war). Reihenfolge der Faelle:
    //
    // S1: ist in Rhino ein Objekt frisch MARKIERT (Viewport-Vorauswahl) -> das hat
    // VORRANG und wird als ZIEL genistet, AUCH wenn schon andere Referenzen (z.B.
    // K/F/O-Kanten) oder ein Befehls-Chip im Feld liegen. "multi" greift die
    // Vorauswahl via GetObjects(preselect=True); das Ergebnis kommt als EIN
    // Auswahl-Block ueber den one-shot pickCallback und nistet (Anlage oder Merge).
    if (rhinoSelectionCount > 0) {
      setGhPickPending(true);
      setPickCallback((block) => {
        setGhPickPending(false);
        // abgebrochen / nichts getroffen -> STILL (bewusster Abbruch, kein Fehler).
        if (!block || block.object_ids.length === 0) return;
        composerRef.current?.addCommandTarget(block);
        composerRef.current?.focus();
      });
      setViewportPending("pick");
      onSend({
        type: "viewport.request_pick",
        payload: { mode: "multi", filter: "any", session_id: activeSessionId },
      });
      return;
    }
    // S2: keine frische Vorauswahl, aber schon Objekt-/Komponenten-Referenzen im
    // Feld (Auswahl- oder K/F/O-Kante/Fläche) -> in den Parametrisieren-Chip NISTEN
    // (legt ihn an ODER mergt in einen bestehenden; mehrere -> mehrere Ziel-Chips,
    // jeder mit ×; Punkt-Picks/Text bleiben). Granularitaet (ganzes Objekt vs.
    // markierte Kante/Flaeche) bestimmt den Befehlstext via parametricInstruction
    // (in addCommandTarget). false, wenn keine passende Referenz da ist -> faellt
    // auf S3 (frischer Pick).
    if (composerRef.current?.nestRefsAsCommand()) {
      composerRef.current?.focus();
      return;
    }
    // S3: kein vormarkiertes Objekt -> Objekt(e) per Hover-Picker im Objekt-Modus
    // picken (force_object: blaues Live-Hover wie K/F/O, kein gelbes rs.GetObject).
    // nest_target="command" laesst JEDEN Pick LIVE in den Befehls-Chip nisten:
    // der Chip erscheint sofort beim ersten Pick und waechst bei jedem weiteren
    // (Shift-Mehrfachauswahl). Kein one-shot pickCallback -> das Streaming laeuft
    // ueber den commandTargetCallback; viewportPending gibt jedes component_result
    // (auch das leere bei Esc) wieder frei. "object" (nicht "pick"): eigener
    // Banner-Zustand fuer diesen force_object-Hover-Pick (Shift = mehrere, Strg
    // no-op) statt des fuer die rs.GetObjects-Route gedachten "pick"-Banners.
    setViewportPending("object");
    onSend({
      type: "viewport.request_component",
      payload: {
        component_type: "auto",
        force_object: true,
        nest_target: "command",
        session_id: activeSessionId,
      },
    });
  }, [
    activeSessionId,
    connected,
    ghPickPending,
    onSend,
    rhinoSelectionCount,
    setPickCallback,
    setViewportError,
    setViewportPending,
    viewportPending,
  ]);

  // ghPickPending is only meaningful while a pick is in flight (S1 sets it
  // together with viewportPending="pick"). If the pick result is lost — a WS
  // reconnect or session switch drops the one-shot pickCallback, so its
  // setGhPickPending(false) never runs — clear the latch here so the
  // Parametrisieren button can't stay disabled (and pulsing) forever. Auch bei
  // Verbindungsabbruch (connected=false) sofort loesen: sonst haengt der Latch,
  // bis die Verbindung wieder steht (onopen nullt viewportPending erst dann).
  useEffect(() => {
    if ((viewportPending !== "pick" || !connected) && ghPickPending) {
      setGhPickPending(false);
    }
  }, [viewportPending, ghPickPending, connected]);

  const submit = useCallback(() => {
    if (!canSend) return;
    // Expand any ImageBlock with a caption into [TextBlock(caption),
    // ImageBlock] so paired snapshot + scene metadata reach the model as
    // two adjacent content blocks. The caption field is frontend-only
    // (it's not part of the backend schema), so strip it from the image
    // block before sending.
    const content: ContentBlock[] = [];
    for (const att of stagedAttachments) {
      if (att.type === "image" && att.caption) {
        content.push({ type: "text", text: att.caption });
        const { caption: _caption, ...imageOnly } = att;
        content.push(imageOnly);
      } else if (att.type === "sketch") {
        // Frontend-lokale Felder vor dem Senden entfernen (das Backend wuerde
        // sie ohnehin via extra="ignore" verwerfen): all_views (alle Backdrops
        // fuers Wieder-Oeffnen), composite (Overview-Grid, geht nie ans Modell)
        // und sketch_group. Spart WS-Payload + macht den Frontend-only-Vertrag
        // explizit statt von Backend-Nachsicht abzuhaengen.
        const {
          all_views: _av,
          composite: _comp,
          sketch_group: _grp,
          ...rest
        } = att;
        content.push(rest);
      } else {
        content.push(att);
      }
    }
    content.push(...(composerRef.current?.serialize() ?? []));
    if (content.length === 0) return;
    const ok = onSend({
      type: "chat.send",
      payload: { session_id: activeSessionId, content },
    });
    if (ok) {
      // Enthaelt die Nachricht einen Parametrisieren-Befehl, die gepickten Ziel-
      // object_ids festhalten, BEVOR der Composer geleert wird -> der spaetere
      // Verwerfen/Abort stellt genau diese Originale wieder her.
      if (composerRef.current?.hasCommand()) {
        setGhSourceObjectIds(composerRef.current.commandTargetObjectIds());
      }
      // Die gerade gesendeten Punkt-Referenz-Dots eingefroren halten, bis der
      // Run fertig ist (der run-complete-Effekt blendet sie dann aus).
      markersFrozenRef.current = true;
      composerRef.current?.clear();
      setComposerHasContent(false);
      clearStagedAttachments();
      setPendingSend(true);
      setRunActive(true);
      // Collapse the parameter/slider panel: the user has moved on to a new
      // instruction, so the (tall) slider strip shouldn't keep dominating the
      // chat. It stays mounted and re-expandable.
      requestCollapseParameters();
    }
  }, [
    activeSessionId,
    canSend,
    clearStagedAttachments,
    onSend,
    requestCollapseParameters,
    setGhSourceObjectIds,
    setPendingSend,
    setRunActive,
    stagedAttachments,
  ]);

  // Hält der Nutzer im K/F/O-/Objekt-Pick gerade einen wirksamen Modifier? Dann
  // zeigt das Pick-Banner NUR den aktiven Modus (statt der vollen Optionen). Strg
  // ("object") wirkt nur im K/F/O-Pick; im "object"-Pick ist das Objekt ohnehin
  // erzwungen, daher dort nur Shift relevant.
  const pickModeActive =
    !!pickModifiers &&
    (pickModifiers.shift ||
      (pickModifiers.object && viewportPending === "component"));

  return (
    <div className="px-3 pb-3 pt-1 sm:px-4">
      <div className="mx-auto flex max-w-3xl flex-col gap-2">
        {viewportPending && (
          <div className="rounded-xl bg-brand-blue-soft px-3 py-2 text-xs font-medium text-brand-blue-deep animate-in fade-in-0 slide-in-from-bottom-1 duration-200 motion-reduce:animate-none">
            {viewportPending === "pick" && (
              <span>
                Objekt auswählen (oder vorab markieren) ·{" "}
                <kbd className="rounded border border-brand-blue/30 bg-card px-1">Esc</kbd>
                {" "}= abbrechen
              </span>
            )}
            {viewportPending === "point" && (
              <span>
                Punkt auswählen · rastet auf Ecken/Kanten ·{" "}
                <kbd className="rounded border border-brand-blue/30 bg-card px-1">Esc</kbd>
                {" "}= abbrechen
              </span>
            )}
            {(viewportPending === "component" || viewportPending === "object") &&
              (pickModeActive ? (
                <span className="flex items-center gap-2 font-semibold text-brand-blue-deep">
                  <span
                    aria-hidden="true"
                    className="inline-block h-2 w-2 shrink-0 animate-pulse rounded-full bg-brand-blue"
                  />
                  <span>
                    {[
                      pickModifiers?.shift
                        ? "Mehrfachauswahl von " +
                          (pickModifiers?.object ? "Objekten" : "Kanten/Flächen")
                        : null,
                      pickModifiers?.object &&
                      viewportPending === "component" &&
                      !pickModifiers?.shift
                        ? "Ganzes Objekt auswählen"
                        : null,
                    ]
                      .filter(Boolean)
                      .join("   ·   ")}
                  </span>
                </span>
              ) : viewportPending === "component" ? (
                <span>
                  Kante/Fläche auswählen ·{" "}
                  <kbd className="rounded border border-brand-blue/30 bg-card px-1">Strg</kbd>
                  {" "}= ganzes Objekt ·{" "}
                  <kbd className="rounded border border-brand-blue/30 bg-card px-1">Shift</kbd>
                  {" "}= mehrere ·{" "}
                  <kbd className="rounded border border-brand-blue/30 bg-card px-1">Esc</kbd>
                  {" "}= abbrechen
                </span>
              ) : (
                <span>
                  Objekt auswählen ·{" "}
                  <kbd className="rounded border border-brand-blue/30 bg-card px-1">Shift</kbd>
                  {" "}= mehrere ·{" "}
                  <kbd className="rounded border border-brand-blue/30 bg-card px-1">Esc</kbd>
                  {" "}= abbrechen
                </span>
              ))}
            {viewportPending === "sketch" && <span>Skizze wird vorbereitet …</span>}
          </div>
        )}
        {viewportError && (
          <div className="flex items-start justify-between gap-2 rounded-xl border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive animate-in fade-in-0 slide-in-from-bottom-1 duration-200 motion-reduce:animate-none">
            <span className="break-words">{viewportError}</span>
            <button
              type="button"
              className="shrink-0 cursor-pointer opacity-70 transition-colors duration-200 hover:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
              onClick={() => setViewportError(null)}
              title="Hinweis schließen"
              aria-label="Schließen"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        )}
        {bakeNotice && (
          <div className="flex items-start justify-between gap-2 rounded-xl border border-success/30 bg-success-soft px-3 py-2 text-xs text-success-deep animate-in fade-in-0 slide-in-from-bottom-1 duration-200 motion-reduce:animate-none">
            <span className="break-words">{bakeNotice}</span>
            <button
              type="button"
              className="shrink-0 cursor-pointer opacity-70 transition-colors duration-200 hover:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
              onClick={() => setBakeNotice(null)}
              title="Hinweis schließen"
              aria-label="Schließen"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        )}
        {consentRequired && (
          <div className="flex items-center justify-between gap-2 rounded-xl bg-brand-orange-soft px-3 py-2 text-xs text-brand-orange-deep animate-in fade-in-0 slide-in-from-bottom-1 duration-200 motion-reduce:animate-none">
            <span>
              Einwilligung ausstehend — bitte den Einwilligungsdialog
              ausfüllen, bevor die erste Nachricht gesendet wird.
            </span>
            <button
              type="button"
              className="shrink-0 cursor-pointer rounded-full border border-brand-orange/30 bg-card px-2.5 py-0.5 font-medium transition-colors duration-200 hover:bg-brand-orange/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
              onClick={() => setConsentDialogOpen(true)}
            >
              Öffnen
            </button>
          </div>
        )}
        {stagedAttachments.length > 0 && (
          <AttachmentStrip
            blocks={stagedAttachments}
            onRemove={removeStagedAttachment}
          />
        )}
        <div className="flex flex-col gap-1 rounded-2xl bg-card p-2 shadow-panel transition-shadow duration-200 focus-within:shadow-panel-hover">
          <input
            ref={fileInputRef}
            type="file"
            accept="image/png,image/jpeg,image/webp,image/gif"
            multiple
            className="hidden"
            onChange={onImageFilesSelected}
          />
          <div
            className="relative grid grid-cols-[minmax(0,1fr)_auto] items-start gap-1"
          >
            <ReferenceComposer
              ref={composerRef}
              disabled={!connected || pendingSend}
              onChange={(has) => {
                setComposerHasContent(has);
                syncPointMarkers();
                syncComponentHighlights();
              }}
              onSubmit={submit}
              placeholder={
                connected
                  ? "Nachricht an Rhino… (Shift+Enter = Zeilenumbruch)"
                  : "Verbindung wird hergestellt…"
              }
              onHighlightReference={
                // Only wire up when werkzeug (tokens only exist there) and
                // connected; fall back to no-op in basis or disconnected state.
                showWerkzeugSlots && connected
                  ? (block) => {
                      if (block) {
                        onSend({
                          type: "viewport.highlight_reference",
                          payload: { block },
                        });
                      } else {
                        onSend({
                          type: "viewport.highlight_clear",
                          payload: {},
                        });
                      }
                    }
                  : undefined
              }
            />
            <Button
              type="button"
              size="icon"
              variant="ghost"
              onClick={triggerImageUpload}
              disabled={!connected || pendingSend || uploadingImage}
              aria-label="Bild anhängen"
              title="Bild anhängen — lokale Bilddatei (Referenz/Inspiration) an die Nachricht hängen"
              className={cn(
                "mt-1 h-8 w-8 rounded-full text-muted-foreground hover:text-foreground",
                uploadingImage && "animate-pulse",
              )}
            >
              <Paperclip className="h-4 w-4" />
            </Button>
          </div>
          <div
            className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-1 border-t border-border/60 pb-0.5 pl-1 pr-0 pt-1.5"
          >
            <div className="flex min-w-0 items-center gap-1">
              {showWerkzeugSlots && (
                <>
                  {/* Sketch button — opens the Multi-View sketch overlay.
                      One tool: pick a view, draw on it (Deixis) or just
                      attach the Multi-View snapshot (context). */}
                  <Button
                    type="button"
                    size="icon"
                    variant="ghost"
                    onClick={requestSketch}
                    disabled={!connected || viewportPending !== null}
                    aria-label="Viewport anhängen oder skizzieren"
                    title="Viewport anhängen oder skizzieren — aktuelle Ansicht als Bild an die Nachricht · optional mit Stift/Kurve markieren · mehrere Ansichten möglich"
                    className={cn(
                      "h-8 w-8 rounded-full",
                      viewportPending === "sketch" && "animate-pulse text-brand-blue",
                    )}
                  >
                    <Pencil className="h-4 w-4" />
                  </Button>
                  <Button
                    type="button"
                    size="icon"
                    variant="ghost"
                    onClick={requestPoint}
                    disabled={!connected || viewportPending !== null}
                    aria-label="Punkt im Viewport auswählen"
                    title="Punkt im Viewport auswählen — rastet auf Ecken/Kanten (Osnap); kommt als Referenz in den Chat"
                    className={cn("h-8 w-8 rounded-full", viewportPending === "point" && "animate-pulse text-brand-blue")}
                  >
                    <Crosshair className="h-4 w-4" />
                  </Button>
                  {(() => {
                    const hasPreselection = rhinoSelectionCount > 0;
                    const kfoLabel = hasPreselection
                      ? `${rhinoSelectionCount} markierte Objekt${rhinoSelectionCount > 1 ? "e" : ""} referenzieren`
                      : "Kante/Fläche im Viewport auswählen — Strg = ganzes Objekt, Shift = mehrere; kommt als Referenz in den Chat";
                    return (
                      <Button
                        type="button"
                        size="icon"
                        variant="ghost"
                        onClick={requestComponent}
                        disabled={!connected || viewportPending !== null}
                        aria-label={kfoLabel}
                        title={kfoLabel}
                        className={cn("relative h-8 w-8 rounded-full", viewportPending === "component" && "animate-pulse text-brand-blue")}
                      >
                        <SquareDashedMousePointer className="h-4 w-4" />
                        {hasPreselection && (
                          <span
                            aria-hidden="true"
                            className="pointer-events-none absolute -right-0.5 -top-0.5 flex h-3.5 min-w-[0.875rem] items-center justify-center rounded-full bg-brand-blue px-0.5 text-[9px] font-semibold leading-none text-white"
                          >
                            {rhinoSelectionCount}
                          </span>
                        )}
                      </Button>
                    );
                  })()}
                  <Button
                    type="button"
                    size="icon"
                    variant="ghost"
                    onClick={requestGhParametric}
                    disabled={!connected || viewportPending !== null || ghPickPending}
                    aria-label="Parametrisieren (Regler für die Maße)"
                    title="Parametrisieren (Regler für die Maße) — wirkt auf eine Referenz im Feld: eine K/F/O-Kante/Fläche wird gezielt einstellbar, ein ganzes Objekt komplett parametrisch. Ohne Referenz wählst du ein Objekt in Rhino aus (mehrere vorab markieren). Was genau einstellbar werden soll, kannst du dazuschreiben."
                    className={cn("h-8 w-8 rounded-full", ghPickPending && "animate-pulse text-brand-blue")}
                  >
                    <SlidersHorizontal className="h-4 w-4" />
                  </Button>
                </>
              )}
            </div>
            {runActive ? (
              <Button
                type="button"
                size="icon"
                onClick={() => {
                  if (!activeSessionId) return;
                  onSend({
                    type: "chat.cancel",
                    payload: { session_id: activeSessionId },
                  });
                }}
                title="Ausführung abbrechen"
                aria-label="Abbrechen"
                className="h-9 w-9 rounded-full bg-destructive/10 text-destructive hover:bg-destructive/20"
                variant="ghost"
              >
                <Square className="h-4 w-4" />
              </Button>
            ) : (
              <Button
                type="button"
                size="icon"
                onClick={submit}
                title="Nachricht senden"
                disabled={!canSend}
                aria-label="Senden"
                className="h-9 w-9 rounded-full"
              >
                <SendHorizontal className="h-4 w-4" />
              </Button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

interface ReferenceComposerProps {
  disabled: boolean;
  onChange: (hasContent: boolean) => void;
  onSubmit: () => void;
  placeholder: string;
  // Best-effort: fires when the user hovers a reference token so the
  // viewport can highlight the referenced geometry. May be undefined
  // (basis condition has no tokens, but guard is here for safety).
  onHighlightReference?: (block: ReferenceBlock | null) => void;
}

const REFERENCE_TOKEN_SELECTOR = "[data-reference-token='true']";

const ReferenceComposer = forwardRef<
  ReferenceComposerHandle,
  ReferenceComposerProps
>(function ReferenceComposer(
  { disabled, onChange, onSubmit, placeholder, onHighlightReference },
  ref,
) {
  const editorRef = useRef<HTMLDivElement | null>(null);
  const lastRangeRef = useRef<Range | null>(null);
  const [empty, setEmpty] = useState(true);

  // Hover-Highlight ueber einen Ref statt direkter Closure-Capture:
  // ``insertReference`` (und damit die im Token verdrahteten mouseenter/
  // mouseleave-Listener) wird einmal beim Mount erzeugt. ``onHighlightReference``
  // ist beim ersten Render aber noch ``undefined`` (WebSocket verbindet erst
  // async -> ``showWerkzeugSlots && connected`` ist false). Ohne diesen Ref
  // haetten alle eingefuegten Tokens dauerhaft KEINEN Highlight-Handler — der
  // Hover ueber den Chip wuerde die Geometrie im Viewport nie hervorheben.
  // ``emitHighlight`` ist stabil und liest IMMER den aktuellen Handler.
  const highlightRef = useRef(onHighlightReference);
  useEffect(() => {
    highlightRef.current = onHighlightReference;
  }, [onHighlightReference]);
  const emitHighlight = useCallback(
    (block: ReferenceBlock | null) => highlightRef.current?.(block),
    [],
  );

  const updateState = useCallback(() => {
    const root = editorRef.current;
    const hasContent = root ? serializeComposerContent(root).length > 0 : false;
    setEmpty(!hasContent);
    onChange(hasContent);
    // Chromium hinterlaesst beim Loeschen von Text DIREKT vor einer
    // nicht-editierbaren Pille manchmal ein fuehrendes <br> -> eine leere erste
    // Zeile ueber dem Inhalt (Pille rutscht eine Zeile runter). Genau dieses
    // Artefakt (fuehrendes <br>, gefolgt von einer Pille \u2014 ZWS-Anker dazwischen
    // ueberspringen) entfernen. Ein echtes <br> aus Shift+Enter (gefolgt von
    // Text) bleibt unangetastet.
    if (root && root.firstChild && root.firstChild.nodeName === "BR") {
      let after: ChildNode | null = root.firstChild.nextSibling;
      while (
        after &&
        after.nodeType === Node.TEXT_NODE &&
        /^\u200b*$/.test(after.textContent ?? "")
      ) {
        after = after.nextSibling;
      }
      if (
        after instanceof HTMLElement &&
        after.dataset.referenceToken === "true"
      ) {
        root.removeChild(root.firstChild);
      }
    }
  }, [onChange]);

  const rememberSelection = useCallback(() => {
    const root = editorRef.current;
    const selection = window.getSelection();
    if (!root || !selection || selection.rangeCount === 0) return;
    const range = selection.getRangeAt(0);
    if (rangeBelongsTo(root, range)) {
      lastRangeRef.current = range.cloneRange();
    }
  }, []);

  const removeToken = useCallback(
    (token: HTMLElement) => {
      // Loescht ein hover-aktives Highlight, BEVOR der Knoten verschwindet.
      // mouseleave feuert auf einem waehrend des Hoverns entfernten Knoten
      // nicht zuverlaessig (und der Listener stirbt mit dem Knoten) -> ohne
      // dies bliebe das orange Overlay im Viewport „eingebrannt", bis ein
      // anderes Highlight es zufaellig ersetzt. emitHighlight(null) ist ein
      // No-Op, wenn kein Handler aktiv ist (basis/getrennt).
      emitHighlight(null);
      const root = editorRef.current;
      if (!root || !root.contains(token)) return;
      const parent = token.parentNode;
      if (!parent) return;
      const offset = Array.prototype.indexOf.call(parent.childNodes, token);
      token.remove();
      root.normalize();
      placeCaretInParent(parent, Math.max(0, offset));
      rememberSelection();
      updateState();
    },
    [emitHighlight, rememberSelection, updateState],
  );

  const insertPlainText = useCallback(
    (value: string) => {
      const root = editorRef.current;
      if (!root || disabled || value.length === 0) return;
      const range = restoreComposerRange(root, lastRangeRef.current);
      range.deleteContents();
      const textNode = document.createTextNode(value);
      range.insertNode(textNode);
      placeCaretAfterNode(textNode);
      rememberSelection();
      updateState();
    },
    [disabled, rememberSelection, updateState],
  );

  const insertReference = useCallback(
    (block: ReferenceBlock) => {
      const root = editorRef.current;
      // NOTE: intentionally NOT gated on `disabled`. Inserting a reference
      // token only stages content for the NEXT message — it never sends.
      // While a run is in flight the composer is disabled for typing
      // (contentEditable=false), but a pick result must still land INLINE in
      // the field, not fall back to a chip above it. `range.insertNode` is a
      // DOM op and works regardless of contentEditable, so the token appears
      // in place and is ready once the run ends and the field re-enables.
      if (!root) return false;
      const range = restoreComposerRange(root, lastRangeRef.current);
      range.deleteContents();

      // KEINE automatischen Leerzeichen um die Pille — die optische Luft zu
      // Nachbartext kommt allein aus dem Pillen-Rand (mx-1.5), konsistent in
      // jeder Position. Hinter der Pille nur ein UNSICHTBARER Zero-Width-Space
      // als Caret-Anker: Chromium/WebView2 kann sonst hinter einem trailing
      // contentEditable=false-Element kaum den Caret setzen / weitertippen. Der
      // ZWS wird beim Serialisieren entfernt; eigene Leerzeichen des Nutzers
      // bleiben unangetastet.
      const fragment = document.createDocumentFragment();
      const token = createReferenceTokenElement(block, removeToken, emitHighlight);
      fragment.append(token);
      const caretAnchor = document.createTextNode("\u200b");
      fragment.append(caretAnchor);
      range.insertNode(fragment);
      placeCaretAfterNode(caretAnchor);
      rememberSelection();
      updateState();
      return true;
    },
    [emitHighlight, rememberSelection, removeToken, updateState],
  );

  // Einzelnes Ziel aus dem Befehls-Chip nehmen (Klick auf das × eines Ziel-Chips).
  // Das letzte Ziel entfernt den ganzen Befehls-Chip. Identitaet ueber blockKey
  // (robust gegen Index-Verschiebung, wenn mehrere nacheinander entfernt werden).
  const removeNestedTarget = useCallback(
    (token: HTMLElement, block: ReferenceBlock, chip: HTMLElement) => {
      emitHighlight(null);
      let nested: ReferenceBlock[] = [];
      try {
        nested = JSON.parse(token.dataset.nestedBlocks || "[]");
      } catch {
        nested = [];
      }
      const key = blockKey(block);
      const remaining = nested.filter((b) => blockKey(b) !== key);
      if (remaining.length === 0) {
        token.remove();
        updateState();
        return;
      }
      chip.remove();
      token.dataset.nestedBlocks = JSON.stringify(remaining);
      // Granularitaet kann sich geaendert haben (z.B. letzte Kante raus -> wieder
      // rein objektbasiert) -> Befehlstext neu bestimmen.
      token.dataset.commandText = parametricInstruction(remaining);
      updateState();
    },
    [emitHighlight, updateState],
  );

  // Befehls-Token einfuegen (z.B. "Parametrisieren") — spiegelt insertReference,
  // nur dass der Token beim Serialisieren zum fixen Befehlstext wird.
  const insertCommand = useCallback(
    (commandText: string, label: string, nestedBlocks?: ReferenceBlock[]) => {
      const root = editorRef.current;
      if (!root) return false;
      const range = restoreComposerRange(root, lastRangeRef.current);
      range.deleteContents();
      const fragment = document.createDocumentFragment();
      const token = createCommandTokenElement(
        commandText,
        label,
        removeToken,
        nestedBlocks,
        emitHighlight,
        removeNestedTarget,
      );
      fragment.append(token);
      const caretAnchor = document.createTextNode("​");
      fragment.append(caretAnchor);
      range.insertNode(fragment);
      placeCaretAfterNode(caretAnchor);
      rememberSelection();
      updateState();
      return true;
    },
    [emitHighlight, rememberSelection, removeNestedTarget, removeToken, updateState],
  );

  // Parametrisieren-Ziel live in den Befehls-Chip nisten. Existiert der Chip
  // noch nicht, wird er mit diesem ersten Ziel angelegt (-> erscheint sofort
  // beim ersten Pick). Existiert er, wird das Ziel an seine nestedBlocks
  // angehaengt und der Token mit der erweiterten Liste in-place neu gebaut
  // (-> waechst bei jedem weiteren Shift-Pick). Duplikate (gleiches Objekt)
  // werden uebersprungen.
  const addCommandTarget = useCallback(
    (block: ReferenceBlock) => {
      const root = editorRef.current;
      if (!root) return false;
      const existing = root.querySelector<HTMLElement>(
        "[data-command-token='true']",
      );
      if (!existing) {
        return insertCommand(
          parametricInstruction([block]),
          "Parametrisieren",
          [block],
        );
      }
      let nested: ReferenceBlock[] = [];
      try {
        nested = JSON.parse(existing.dataset.nestedBlocks || "[]");
      } catch {
        nested = [];
      }
      // Dedup per blockKey (object_id + component_type + component_index), NICHT
      // nur object_id: zwei verschiedene Kanten/Flaechen DESSELBEN Objekts haben
      // unterschiedliche Keys und bleiben beide erhalten; nur ein echtes Re-Pick
      // derselben Komponente wird verworfen. Symmetrisch zu removeNestedTarget.
      const newKey = blockKey(block);
      if (nested.some((b) => blockKey(b) === newKey)) {
        return true;
      }
      nested.push(block);
      // Text granularitaetsabhaengig aus dem GESAMTEN Ziel-Set neu bestimmen.
      const commandText = parametricInstruction(nested);
      const label = existing.dataset.commandLabel || "Parametrisieren";
      const fresh = createCommandTokenElement(
        commandText,
        label,
        removeToken,
        nested,
        emitHighlight,
        removeNestedTarget,
      );
      existing.replaceWith(fresh);
      updateState();
      return true;
    },
    [emitHighlight, insertCommand, removeNestedTarget, removeToken, updateState],
  );

  // Vorhandene Objekt-/Komponenten-Referenzen (Auswahl + K/F/O Kante/Flaeche) in den
  // Parametrisieren-Befehls-Chip nisten. Erst alle freistehenden Refs einsammeln
  // (DOM-Reihenfolge) + ihre Tokens entfernen, dann jede per addCommandTarget
  // einnisten. addCommandTarget legt den Chip beim ersten Ziel an und HAENGT bei
  // jedem weiteren an einen bestehenden an -> dadurch funktioniert das auch als
  // MERGE: existiert schon ein Befehls-Chip (2. Parametrisieren-Klick mit frisch
  // gepickten Refs), wandern die neuen Ziele in DENSELBEN Chip statt verworfen zu
  // werden. Dedup laeuft in addCommandTarget per blockKey. Punkt-Picks + getippter
  // Text bleiben unberuehrt. true, wenn mind. eine Ref genistet wurde.
  const nestRefsAsCommand = useCallback(() => {
    const root = editorRef.current;
    if (!root) return false;
    const blocks: ReferenceBlock[] = [];
    root
      .querySelectorAll<HTMLElement>(REFERENCE_TOKEN_SELECTOR)
      .forEach((token) => {
        if (token.dataset.commandToken === "true") return; // Befehls-Chip auslassen
        const block = parseReferenceBlock(token);
        if (
          block &&
          (block.type === "selection" || block.type === "component_pick")
        ) {
          blocks.push(block);
          token.remove();
        }
      });
    if (!blocks.length) return false;
    emitHighlight(null);
    blocks.forEach((block) => addCommandTarget(block));
    return true;
  }, [addCommandTarget, emitHighlight]);

  useImperativeHandle(
    ref,
    () => ({
      clear: () => {
        const root = editorRef.current;
        if (!root) return;
        // Hover-Highlight loeschen, bevor die Tokens (evtl. eines davon gerade
        // gehovert) beim Senden/Sitzungswechsel entfernt werden.
        emitHighlight(null);
        root.replaceChildren();
        lastRangeRef.current = null;
        updateState();
      },
      focus: () => editorRef.current?.focus(),
      insertReference,
      insertCommand,
      addCommandTarget,
      nestRefsAsCommand,
      hasCommand: () =>
        !!editorRef.current?.querySelector("[data-command-token='true']"),
      commandTargetObjectIds: () => {
        const cmd = editorRef.current?.querySelector<HTMLElement>(
          "[data-command-token='true']",
        );
        if (!cmd) return [];
        let nested: ReferenceBlock[] = [];
        try {
          nested = JSON.parse(cmd.dataset.nestedBlocks || "[]");
        } catch {
          nested = [];
        }
        const ids: string[] = [];
        for (const b of nested) {
          const oid = (b as { object_id?: string }).object_id;
          if (oid && !ids.includes(oid)) ids.push(oid);
        }
        return ids;
      },
      removeReferences: () => {
        const root = editorRef.current;
        if (!root) return;
        emitHighlight(null);
        root
          .querySelectorAll<HTMLElement>(REFERENCE_TOKEN_SELECTOR)
          .forEach((token) => token.remove());
        root.normalize();
        updateState();
      },
      serialize: () =>
        editorRef.current ? serializeComposerContent(editorRef.current) : [],
    }),
    [
      emitHighlight,
      addCommandTarget,
      nestRefsAsCommand,
      insertCommand,
      insertReference,
      updateState,
    ],
  );

  const handleKeyDown = useCallback(
    (event: KeyboardEvent<HTMLDivElement>) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        onSubmit();
        return;
      }
      if (event.key !== "Backspace") return;
      const root = editorRef.current;
      if (!root) return;
      const target = findBackspaceReferenceTarget(root);
      if (!target) return;
      event.preventDefault();
      if (target.whitespaceNode && target.whitespaceOffset > 0) {
        target.whitespaceNode.deleteData(0, target.whitespaceOffset);
      }
      removeToken(target.token);
    },
    [onSubmit, removeToken],
  );

  const handlePaste = useCallback(
    (event: ClipboardEvent<HTMLDivElement>) => {
      // Always take over the paste so the browser can't inject rich HTML / an
      // <img> into the contentEditable — serializeComposerContent ignores such
      // nodes, leaving content that's invisible to the model but visually stuck
      // in the field. Images go through the paperclip → uploadImage path, not
      // an inline paste.
      event.preventDefault();
      const value = event.clipboardData.getData("text/plain");
      if (!value) return;
      insertPlainText(value);
    },
    [insertPlainText],
  );

  return (
    <div className="relative min-w-0">
      <div
        ref={editorRef}
        role="textbox"
        aria-label="Nachricht"
        aria-multiline="true"
        contentEditable={!disabled}
        suppressContentEditableWarning
        onBlur={rememberSelection}
        onInput={() => {
          rememberSelection();
          updateState();
        }}
        onKeyDown={handleKeyDown}
        onKeyUp={rememberSelection}
        onMouseUp={rememberSelection}
        onPaste={handlePaste}
        className={cn(
          // leading-6 (statt -5): die Inline-Referenz-Pillen sind hoeher als
          // eine Textzeile — mehr Zeilenhoehe gibt ihnen Luft, sodass sich
          // umgebrochene Pillen nicht beruehren.
          "scrollbar-none min-h-10 max-h-36 w-full overflow-y-auto whitespace-pre-wrap break-words bg-transparent py-2 pl-3 pr-1 text-sm leading-6 outline-none",
          "empty:before:content-none focus-visible:outline-none",
          disabled && "cursor-not-allowed opacity-70",
        )}
      />
      {empty && (
        <span className="pointer-events-none absolute left-3 top-2 text-sm leading-6 text-muted-foreground">
          {placeholder}
        </span>
      )}
    </div>
  );
});

