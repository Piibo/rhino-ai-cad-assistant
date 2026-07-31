import type { ImageSource, SketchBlock, SketchView } from "@/lib/types";
import type { SketchOverlayState } from "@/store/chatStore";

/**
 * Eine gestagte Skizzen-Gruppe als Editor-Overlay rekonstruieren. Nutzt
 * ``all_views`` (alle erfassten Ansichts-Backdrops), damit JEDE Ansicht zum
 * Bemalen angeboten wird — nicht nur die bereits bemalten. Strich-Wiederher-
 * stellung: das bemalte ``svg`` wird ueber den ``view_name`` (= Label) der
 * passenden all_views-Ansicht zugeordnet. Fallback (aeltere Bloecke ohne
 * all_views): wie bisher je Member eine Ansicht. Null = nichts rekonstruierbar.
 *
 * Pure (kein React/Store) -> isoliert testbar (sketch-overlay-builder.test.ts).
 */
export function buildSketchOverlayFromGroup(
  members: SketchBlock[],
  group: string | undefined,
): SketchOverlayState | null {
  if (members.length === 0) return null;
  const allViews = members.find((b) => b.all_views && b.all_views.length > 0)
    ?.all_views;
  const initialStrokesByView: Record<string, string> = {};
  let composite: ImageSource | undefined;
  let views: SketchView[];
  if (allViews && allViews.length > 0) {
    views = allViews;
    for (const b of members) {
      // Bemalte Ansicht ueber ihr Label (view_name) der echten Ansicht zuordnen.
      if (b.background && b.svg && b.view_name) {
        const av = allViews.find(
          (v) => v.label === b.view_name || v.name === b.view_name,
        );
        if (av) initialStrokesByView[av.name] = b.svg;
      }
      if (!composite && b.composite) composite = b.composite;
    }
  } else {
    views = [];
    for (const b of members) {
      const src = b.background ?? b.rendered_png ?? b.composite;
      if (!src) continue;
      const name = b.view_name || "Skizze";
      views.push({ name, label: name, source: src, width: b.width, height: b.height });
      // Striche nur zurueckladen, wenn ein SAUBERER Hintergrund existiert
      // (sonst waere der Backdrop das bemalte rendered_png -> Doppelung).
      if (b.background && b.svg) initialStrokesByView[name] = b.svg;
      if (!composite && b.composite) composite = b.composite;
    }
    if (views.length === 0) return null;
  }
  // Use the composite block's TRUE grid dims, not members[0].width/height
  // (which for a painted block are the single cell's dims) — otherwise a
  // reopened painted group carries cell-sized dims with the grid image.
  const compBlock = members.find((b) => b.composite);
  const comp = compBlock?.composite ?? members[0].composite ?? views[0].source;
  const compW = compBlock?.composite_width ?? members[0].width;
  const compH = compBlock?.composite_height ?? members[0].height;
  return {
    views,
    composite: { source: comp, width: compW, height: compH },
    initialStrokesByView:
      Object.keys(initialStrokesByView).length > 0 ? initialStrokesByView : undefined,
    reopenGroupId: group,
  };
}
