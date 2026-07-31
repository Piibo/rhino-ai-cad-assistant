// Composer-DOM-Helfer: reine DOM-Konstruktion + Serialisierung der Referenz- und
// Befehls-Tokens des ReferenceComposer (kein React). Aus InputBar.tsx ausgelagert
// (Refactor 26.06.2026, Verhalten identisch).
import {
  componentPickLabel,
  componentPickShortLabel,
} from "@/lib/component-pick-label";
import { pointPickLabel, pointPickShortLabel } from "@/lib/point-pick-label";
import { selectionLabel, selectionShortLabel } from "@/lib/selection-label";
import type {
  ContentBlock,
  ComponentPickBlock,
  ReferenceBlock,
  TextBlock,
} from "@/lib/types";

// Feste Breite aller Referenz-Chips -> das ×-Rund steht IMMER an derselben
// Stelle, sodass man mehrere Chips hintereinander wegklicken kann, ohne die
// Maus zu bewegen (der nachrueckende Chip landet exakt an der Position des
// geloeschten). Laengere Labels werden gekuerzt (Hover zeigt das volle Label).
const REFERENCE_CHIP_WIDTH = "8rem";

export function createReferenceTokenElement(
  block: ReferenceBlock,
  onRemove: (token: HTMLElement) => void,
  onHighlightReference?: (block: ReferenceBlock | null) => void,
): HTMLElement {
  const fullLabel = referenceTokenLabel(block);
  const token = document.createElement("span");
  token.dataset.referenceToken = "true";
  token.dataset.referenceBlock = JSON.stringify(block);
  token.setAttribute("contenteditable", "false");
  token.setAttribute("aria-label", referenceTokenTitle(block));
  token.className =
    // Schrift exakt wie der getippte Composer-Text: text-sm (14px) + KEIN
    // font-medium (Gewicht 400 wie der Text); Familie (Inter) via Vererbung.
    // Nur die Farbe bleibt blau als Token-Kennzeichnung (= die im Viewport blau
    // markierte Geometrie). Kein Rahmen im
    // Ruhezustand; on hover ein 1px Inset-Ring (box-shadow -> KEIN Layout-Shift,
    // Pillenhoehe + Umbruch-Abstand bleiben). pr-0.5 setzt das ×-Rund in die
    // rechte Kappe. align-baseline (statt -middle): im Browser gemessen liegt
    // damit die vertikale Pillen-Mitte exakt auf der Textmitte/dem Cursor
    // (align-middle saass ~2px zu tief, der Cursor wirkte hoeher).
    "mx-1 inline-flex cursor-default items-center gap-1 rounded-full bg-brand-blue-soft pl-2 pr-0.5 py-0.5 align-baseline text-sm leading-4 text-brand-blue-deep shadow-sm ring-1 ring-inset ring-transparent transition-shadow duration-200 hover:ring-brand-blue/50";
  // Feste Breite (siehe REFERENCE_CHIP_WIDTH) -> alle Chips gleich gross, ×
  // immer an derselben Stelle. Tooltip immer, da bei fester Breite auch mittlere
  // Labels gekuerzt sein koennen (zeigt das volle Label).
  token.style.width = REFERENCE_CHIP_WIDTH;
  token.style.maxWidth = REFERENCE_CHIP_WIDTH;

  // Viewport highlight on hover — best-effort, never blocks tooltip/composer.
  if (onHighlightReference) {
    token.addEventListener("mouseenter", () => onHighlightReference(block));
    token.addEventListener("mouseleave", () => onHighlightReference(null));
  }

  // Alle Chip-Typen einheitlich: Typ-Icon + Kurztext in `·`-Tokens (z.B.
  // "#9 · Box"). Komponente -> Geometrie-Glyph, Punkt -> Crosshair, Auswahl ->
  // Box (Wuerfel). Das volle Label bleibt ueberall in der aria-Bezeichnung.
  if (block.type === "component_pick") {
    token.append(componentTypeIcon(block));
  } else if (block.type === "point_pick") {
    token.append(pointIcon());
  } else if (block.type === "selection") {
    token.append(boxIcon());
  }
  const label = document.createElement("span");
  label.dataset.refLabel = "1"; // -> Truncation-Check / Marquee
  label.className = "min-w-0 flex-1 truncate";
  label.textContent =
    block.type === "component_pick"
      ? componentPickShortLabel(block)
      : block.type === "point_pick"
        ? pointPickShortLabel(block) // gleiche `·`-Token-Struktur wie K/F/O-Chips
        : block.type === "selection"
          ? selectionShortLabel(block)
          : fullLabel;
  token.append(label);
  // Marquee ERST JETZT verdrahten — vorher existiert das [data-ref-label] noch
  // nicht im Token (das war der Grund, warum lange Labels nicht durchliefen).
  attachReferenceTokenMarquee(token);

  const remove = document.createElement("button");
  remove.type = "button";
  remove.setAttribute("aria-label", `Referenz entfernen: ${fullLabel}`);
  remove.className =
    "ml-0.5 inline-flex h-4 w-4 cursor-pointer items-center justify-center rounded-full text-[12px] leading-none text-brand-blue-deep/70 transition-colors duration-200 hover:bg-brand-blue/15 hover:text-brand-blue-deep focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60";
  remove.textContent = "×";
  remove.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    onRemove(token);
  });
  token.append(remove);

  return token;
}

// Befehls-Token (Aktion) fuer den Composer, z.B. "Parametrisieren". Liegt als
// eigene loeschbare Einheit im Eingabefeld (×/Backspace) und liest sich als
// Feature statt als getippter Text. Teilt die Token-Mechanik (data-reference-
// token) fuer Entfernen/Clear; serialisiert aber NICHT als Referenz, sondern
// ueber data-command-text zum fixen Befehlstext (siehe serializeComposerContent).
// Optik: helle Pille wie die Referenz-Chips, aber auto-breit + sichtbarer
// Akzent-Ring + Slider-Icon -> "Aktion" klar von "Referenz" unterschieden.
// Stabiler Identitaetsschluessel eines Referenz-Blocks — zum Entfernen eines
// einzelnen Ziels aus dem Befehls-Chip (robust gegen Index-Verschiebung).
export function blockKey(block: ReferenceBlock): string {
  if (block.type === "selection") return "sel:" + block.object_ids.join(",");
  if (block.type === "component_pick")
    return [
      "cmp",
      block.object_id ?? "",
      block.component_type ?? "",
      block.component_index ?? "",
    ].join(":");
  if (block.type === "point_pick") return "pt:" + (block.point ?? []).join(",");
  return JSON.stringify(block);
}

// Kompakter Referenz-Chip, der INNERHALB des Befehls-Chips sitzt (das "Ziel" der
// Aktion): Typ-Icon + Kurzlabel, hellerer Fond auf der Befehls-Pille.
function buildNestedRefChip(
  block: ReferenceBlock,
  onHighlight?: (block: ReferenceBlock | null) => void,
): HTMLElement {
  const chip = document.createElement("span");
  chip.className =
    "ml-1 inline-flex min-w-0 max-w-[12rem] cursor-default items-center gap-1 rounded-full bg-card px-1.5 py-0.5 text-[11px] leading-none text-brand-blue-deep ring-1 ring-inset ring-brand-blue/30";
  // Hover ueber den verschachtelten Chip -> referenziertes Objekt im Viewport
  // hervorheben (gleicher Pfad wie die freistehenden Referenz-Chips).
  if (onHighlight) {
    chip.addEventListener("mouseenter", () => onHighlight(block));
    chip.addEventListener("mouseleave", () => onHighlight(null));
  }
  if (block.type === "component_pick") {
    chip.append(componentTypeIcon(block));
  } else if (block.type === "point_pick") {
    chip.append(pointIcon());
  } else {
    chip.append(boxIcon());
  }
  const text = document.createElement("span");
  // truncate (+ max-w am Chip): sehr lange Objektnamen enden mit Ellipse statt den
  // Chip ueber die Composer-Breite zu treiben. title -> voller Name beim Hover.
  text.className = "truncate";
  text.title =
    block.type === "component_pick"
      ? componentPickShortLabel(block)
      : block.type === "point_pick"
        ? pointPickShortLabel(block)
        : selectionShortLabel(block);
  text.textContent =
    block.type === "component_pick"
      ? componentPickShortLabel(block)
      : block.type === "point_pick"
        ? pointPickShortLabel(block)
        : selectionShortLabel(block);
  chip.append(text);
  return chip;
}

// Befehls-Token "Parametrisieren". Kann verschachtelte Referenz-Chips (die ZIELE
// der Aktion) enthalten; beim Serialisieren werden deren Bloecke + der fixe
// Befehlstext emittiert ("diese Objekte parametrisieren").
export function createCommandTokenElement(
  commandText: string,
  label: string,
  onRemove: (token: HTMLElement) => void,
  nestedBlocks?: ReferenceBlock[],
  onHighlight?: (block: ReferenceBlock | null) => void,
  onRemoveTarget?: (
    token: HTMLElement,
    block: ReferenceBlock,
    chip: HTMLElement,
  ) => void,
): HTMLElement {
  const token = document.createElement("span");
  token.dataset.referenceToken = "true";
  token.dataset.commandToken = "true";
  token.dataset.commandText = commandText;
  token.dataset.commandLabel = label;
  if (nestedBlocks && nestedBlocks.length) {
    token.dataset.nestedBlocks = JSON.stringify(nestedBlocks);
  }
  token.setAttribute("contenteditable", "false");
  token.setAttribute("aria-label", `Befehl: ${label}`);
  // Mit verschachteltem Ziel-Chip mehr vertikale Luft (py-1), damit der Box-Chip
  // nicht reingequetscht wirkt; ohne Ziel kompakt (py-0.5, wie die Referenz-Chips).
  const hasNested = !!(nestedBlocks && nestedBlocks.length);
  // max-w-full + flex-wrap: bei mehreren genesteten Ziel-Chips bricht der Chip
  // INNERHALB der Composer-Breite auf eine zweite Zeile um, statt rechts
  // abgeschnitten zu werden. gap-y-1 gibt den umgebrochenen Zeilen Luft.
  token.className =
    "mx-1 inline-flex max-w-full flex-wrap cursor-default items-center gap-1 rounded-full bg-brand-blue-soft pl-2 pr-0.5 align-baseline text-sm leading-4 text-brand-blue-deep shadow-sm ring-1 ring-inset ring-brand-blue/40 " +
    (hasNested ? "py-1 gap-y-1" : "py-0.5");

  // Reihenfolge liest sich als "Box parametrisieren": erst das/die ZIEL-Objekt(e),
  // dann Slider-Icon + Verb (klein, weil es dem Objekt folgt). Ohne Ziel (noch
  // ungebunden): nur Icon + "Parametrisieren".
  if (nestedBlocks && nestedBlocks.length) {
    nestedBlocks.forEach((b) => {
      const chip = buildNestedRefChip(b, onHighlight);
      // Pro Ziel ein kleines × -> einzelnes Objekt wieder aus dem Befehl nehmen
      // (das letzte entfernt den ganzen Befehls-Chip).
      if (onRemoveTarget) {
        const x = document.createElement("button");
        x.type = "button";
        x.setAttribute("aria-label", "Ziel entfernen");
        x.className =
          "ml-0.5 inline-flex h-3.5 w-3.5 cursor-pointer items-center justify-center rounded-full text-[11px] leading-none text-brand-blue-deep/55 transition-colors duration-200 hover:bg-brand-blue/15 hover:text-brand-blue-deep";
        x.textContent = "×";
        x.addEventListener("click", (event) => {
          event.preventDefault();
          event.stopPropagation();
          onRemoveTarget(token, b, chip);
        });
        chip.append(x);
      }
      token.append(chip);
    });
  }

  token.append(
    makeChipIcon([
      { tag: "line", attrs: { x1: "21", x2: "14", y1: "4", y2: "4" } },
      { tag: "line", attrs: { x1: "10", x2: "3", y1: "4", y2: "4" } },
      { tag: "line", attrs: { x1: "21", x2: "12", y1: "12", y2: "12" } },
      { tag: "line", attrs: { x1: "8", x2: "3", y1: "12", y2: "12" } },
      { tag: "line", attrs: { x1: "21", x2: "16", y1: "20", y2: "20" } },
      { tag: "line", attrs: { x1: "12", x2: "3", y1: "20", y2: "20" } },
      { tag: "line", attrs: { x1: "14", x2: "14", y1: "2", y2: "6" } },
      { tag: "line", attrs: { x1: "8", x2: "8", y1: "10", y2: "14" } },
      { tag: "line", attrs: { x1: "16", x2: "16", y1: "18", y2: "22" } },
    ]),
  );

  const labelEl = document.createElement("span");
  labelEl.className = "whitespace-nowrap";
  labelEl.textContent = hasNested ? label.toLowerCase() : label;
  token.append(labelEl);

  const remove = document.createElement("button");
  remove.type = "button";
  remove.setAttribute("aria-label", `Befehl entfernen: ${label}`);
  remove.className =
    "ml-0.5 inline-flex h-4 w-4 cursor-pointer items-center justify-center rounded-full text-[12px] leading-none text-brand-blue-deep/70 transition-colors duration-200 hover:bg-brand-blue/15 hover:text-brand-blue-deep focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60";
  remove.textContent = "×";
  remove.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    onRemove(token);
  });
  token.append(remove);

  return token;
}

function referenceTokenLabel(block: ReferenceBlock): string {
  if (block.type === "selection") {
    const prefix =
      block.object_ids.length > 1
        ? `${block.object_ids.length} Objekte`
        : "Objekt";
    return `${prefix}: ${selectionLabel(block)}`;
  }
  if (block.type === "point_pick") return pointPickLabel(block);
  return componentPickLabel(block);
}

function referenceTokenTitle(block: ReferenceBlock): string {
  if (block.type === "selection") {
    const names = selectionLabel(block);
    return `Referenz: ${names}`;
  }
  if (block.type === "point_pick") return `Referenz: ${pointPickLabel(block)}`;
  return `Referenz: ${componentPickLabel(block)}`;
}

const SVG_NS = "http://www.w3.org/2000/svg";

function makeChipIcon(
  children: Array<{ tag: string; attrs: Record<string, string> }>,
): SVGElement {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "2");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("class", "h-3.5 w-3.5 shrink-0");
  svg.setAttribute("aria-hidden", "true");
  for (const { tag, attrs } of children) {
    const el = document.createElementNS(SVG_NS, tag);
    for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
    svg.append(el);
  }
  return svg;
}

// Typ-Icon fuer den Komponenten-Chip — lucide-Icons wie in MessageBubble
// (Chat-Verlauf), damit Inline-Chip und gesendete Nachricht konsistent aussehen:
// Kante = Spline (Kurve + 2 Kontrollpunkte), Flaeche = Square, Objekt = Box.
// (Vertex gibt's ueber K/F/O nicht mehr — Vertices laufen ueber den Crosshair.)
// Bei Aenderung in MessageBubble hier mitziehen.
function componentTypeIcon(block: ComponentPickBlock): SVGElement {
  switch (block.component_type) {
    case "face": // Square mit zarter Fuellung = angedeutete Flaeche (Umriss + Fill)
      return makeChipIcon([
        {
          tag: "rect",
          attrs: {
            width: "18",
            height: "18",
            x: "3",
            y: "3",
            rx: "2",
            fill: "currentColor",
            "fill-opacity": "0.18",
          },
        },
      ]);
    case "object": // lucide Box
      return boxIcon();
    default: // Kante -> lucide Spline
      return makeChipIcon([
        { tag: "circle", attrs: { cx: "19", cy: "5", r: "2" } },
        { tag: "circle", attrs: { cx: "5", cy: "19", r: "2" } },
        { tag: "path", attrs: { d: "M5 17A12 12 0 0 1 17 5" } },
      ]);
  }
}

// Objekt-/Auswahl-Chip-Icon = lucide Box (Wuerfel). Geteilt vom Objekt-Pick und
// den Auswahl-Chips, damit beide gleich aussehen.
function boxIcon(): SVGElement {
  return makeChipIcon([
    { tag: "path", attrs: { d: "M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z" } },
    { tag: "path", attrs: { d: "m3.3 7 8.7 5 8.7-5" } },
    { tag: "path", attrs: { d: "M12 22V12" } },
  ]);
}

// Punkt-Chip-Icon = lucide Crosshair (wie MessageBubble fuer den Punkt-Pick).
function pointIcon(): SVGElement {
  return makeChipIcon([
    { tag: "circle", attrs: { cx: "12", cy: "12", r: "10" } },
    { tag: "line", attrs: { x1: "22", x2: "18", y1: "12", y2: "12" } },
    { tag: "line", attrs: { x1: "6", x2: "2", y1: "12", y2: "12" } },
    { tag: "line", attrs: { x1: "12", x2: "12", y1: "6", y2: "2" } },
    { tag: "line", attrs: { x1: "12", x2: "12", y1: "22", y2: "18" } },
  ]);
}

// Hover-Marquee statt Popup: passt das Label nicht ganz in die feste Chip-Breite,
// laeuft es bei Hover/Fokus sanft IM Chip durch (hin und zurueck, mit kurzer Pause
// an den Enden). Per ``scrollLeft`` (overflow:hidden ist programmatisch
// scrollbar), waehrend des Laufs ohne … (``text-overflow: clip``). Responsive:
// der Ueberlauf wird in jedem Frame frisch gemessen. Self-cleaning: stoppt, sobald
// das Token aus dem DOM ist.
function attachReferenceTokenMarquee(token: HTMLElement): void {
  const label = token.querySelector<HTMLElement>("[data-ref-label]");
  if (!label) return;
  let rafId = 0;
  const stop = () => {
    if (rafId) cancelAnimationFrame(rafId);
    rafId = 0;
    label.scrollLeft = 0;
    label.style.textOverflow = "";
  };
  const start = () => {
    if (rafId) return; // laeuft schon
    // Bewegungsempfindlichkeit respektieren: Marquee gar nicht erst starten —
    // das Label bleibt mit … gekuerzt (Volltext weiterhin via title-Tooltip).
    if (
      typeof window !== "undefined" &&
      window.matchMedia &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches
    )
      return;
    if (label.scrollWidth - label.clientWidth <= 1) return; // passt rein
    label.style.textOverflow = "clip"; // … weg fuer sauberen Lauf
    const speed = 0.045; // px/ms
    const pause = 650; // ms an den Enden
    let phase: "startPause" | "toEnd" | "endPause" | "toStart" = "startPause";
    let t0 = performance.now();
    const tick = (now: number) => {
      if (!token.isConnected) {
        stop();
        return;
      }
      const dt = now - t0;
      const ov = label.scrollWidth - label.clientWidth; // responsiv neu messen
      if (ov <= 1) {
        label.scrollLeft = 0;
      } else if (phase === "startPause") {
        if (dt >= pause) {
          phase = "toEnd";
          t0 = now;
        }
      } else if (phase === "toEnd") {
        label.scrollLeft = Math.min(ov, dt * speed);
        if (label.scrollLeft >= ov) {
          phase = "endPause";
          t0 = now;
        }
      } else if (phase === "endPause") {
        if (dt >= pause) {
          phase = "toStart";
          t0 = now;
        }
      } else {
        label.scrollLeft = Math.max(0, ov - dt * speed);
        if (label.scrollLeft <= 0) {
          phase = "startPause";
          t0 = now;
        }
      }
      rafId = requestAnimationFrame(tick);
    };
    rafId = requestAnimationFrame(tick);
  };
  token.addEventListener("mouseenter", start);
  token.addEventListener("mouseleave", stop);
  token.addEventListener("focusin", start);
  token.addEventListener("focusout", stop);
}

export function serializeComposerContent(root: HTMLElement): ContentBlock[] {
  const content: ContentBlock[] = [];
  let textBuffer = "";

  const flushText = () => {
    // Zero-Width-Space (Caret-Anker hinter Pillen) entfernen, \u00a0 -> Space,
    // dann trimmen -> der ZWS + die optischen Pillen-Raender landen nie im Text.
    const text = textBuffer
      .replace(/\u200b/g, "")
      .replace(/\u00a0/g, " ")
      .trim();
    textBuffer = "";
    if (!text) return;
    const block: TextBlock = { type: "text", text };
    content.push(block);
  };

  const walk = (node: ChildNode) => {
    if (isCommandTokenElement(node)) {
      flushText();
      // Verschachtelte Referenzen = die ZIELE der Aktion -> ihre Bloecke ZUERST,
      // dann der fixe Befehlstext (das Modell liest "diese Objekte
      // parametrisieren"). Referenzen AUSSERHALB des Befehls-Chips bleiben
      // separate Bloecke = Kontext.
      const nestedRaw = node.dataset.nestedBlocks;
      if (nestedRaw) {
        try {
          for (const b of JSON.parse(nestedRaw) as ReferenceBlock[]) {
            content.push(b);
          }
        } catch {
          // malformed nested payload -> ignorieren
        }
      }
      const text = node.dataset.commandText;
      if (text) {
        const block: TextBlock = { type: "text", text };
        content.push(block);
      }
      return;
    }
    if (isReferenceTokenElement(node)) {
      flushText();
      const block = parseReferenceBlock(node);
      if (block) content.push(block);
      return;
    }
    if (node.nodeType === Node.TEXT_NODE) {
      textBuffer += node.textContent ?? "";
      return;
    }
    if (node instanceof HTMLBRElement) {
      textBuffer += "\n";
      return;
    }
    if (node instanceof HTMLElement) {
      const addsLineBreak = node.tagName === "DIV" || node.tagName === "P";
      node.childNodes.forEach(walk);
      if (addsLineBreak) textBuffer += "\n";
    }
  };

  root.childNodes.forEach(walk);
  flushText();
  return content;
}

export function parseReferenceBlock(node: HTMLElement): ReferenceBlock | null {
  const raw = node.dataset.referenceBlock;
  if (!raw) return null;
  try {
    const block = JSON.parse(raw) as ReferenceBlock;
    return isReferenceBlock(block) ? block : null;
  } catch {
    return null;
  }
}

function isReferenceBlock(block: unknown): block is ReferenceBlock {
  if (!block || typeof block !== "object") return false;
  const type = (block as { type?: unknown }).type;
  return (
    type === "selection" ||
    type === "point_pick" ||
    type === "component_pick"
  );
}

function isReferenceTokenElement(node: ChildNode): node is HTMLElement {
  return (
    node instanceof HTMLElement &&
    node.dataset.referenceToken === "true"
  );
}

function isCommandTokenElement(node: ChildNode): node is HTMLElement {
  return node instanceof HTMLElement && node.dataset.commandToken === "true";
}

export function rangeBelongsTo(root: HTMLElement, range: Range): boolean {
  return root.contains(range.startContainer) && root.contains(range.endContainer);
}

export function restoreComposerRange(root: HTMLElement, saved: Range | null): Range {
  const selection = window.getSelection();
  const range = document.createRange();
  if (saved && rangeBelongsTo(root, saved)) {
    range.setStart(saved.startContainer, saved.startOffset);
    range.setEnd(saved.endContainer, saved.endOffset);
  } else {
    range.selectNodeContents(root);
    range.collapse(false);
  }
  selection?.removeAllRanges();
  selection?.addRange(range);
  root.focus();
  return range;
}

export function placeCaretAfterNode(node: ChildNode): void {
  const range = document.createRange();
  range.setStartAfter(node);
  range.collapse(true);
  const selection = window.getSelection();
  selection?.removeAllRanges();
  selection?.addRange(range);
}

export function placeCaretInParent(parent: Node, offset: number): void {
  const range = document.createRange();
  range.setStart(parent, Math.min(offset, parent.childNodes.length));
  range.collapse(true);
  const selection = window.getSelection();
  selection?.removeAllRanges();
  selection?.addRange(range);
}

interface BackspaceReferenceTarget {
  token: HTMLElement;
  whitespaceNode?: Text;
  whitespaceOffset: number;
}

export function findBackspaceReferenceTarget(
  root: HTMLElement,
): BackspaceReferenceTarget | null {
  const selection = window.getSelection();
  if (!selection || selection.rangeCount === 0 || !selection.isCollapsed) {
    return null;
  }
  const range = selection.getRangeAt(0);
  if (!rangeBelongsTo(root, range)) return null;

  const node = range.startContainer;
  const offset = range.startOffset;
  if (node.nodeType === Node.TEXT_NODE) {
    const textNode = node as Text;
    const before = textNode.data.slice(0, offset);
    if (before.length > 0 && /\S/.test(before)) return null;
    const token = previousReferenceToken(textNode);
    return token
      ? { token, whitespaceNode: textNode, whitespaceOffset: offset }
      : null;
  }
  const previous = node.childNodes.item(offset - 1);
  if (previous && isReferenceTokenElement(previous)) {
    return { token: previous, whitespaceOffset: 0 };
  }
  if (previous?.nodeType === Node.TEXT_NODE && !/\S/.test(previous.textContent ?? "")) {
    const token = previousReferenceToken(previous);
    return token
      ? {
          token,
          whitespaceNode: previous as Text,
          whitespaceOffset: (previous.textContent ?? "").length,
        }
      : null;
  }
  return null;
}

function previousReferenceToken(node: ChildNode): HTMLElement | null {
  let previous = node.previousSibling;
  while (previous && previous.nodeType === Node.TEXT_NODE) {
    if (/\S/.test(previous.textContent ?? "")) return null;
    previous = previous.previousSibling;
  }
  return previous && isReferenceTokenElement(previous) ? previous : null;
}
