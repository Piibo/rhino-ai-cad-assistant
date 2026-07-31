import type { SelectionBlock } from "./types";

/**
 * Build the human-readable label for a selection chip.
 *
 * Prefers user-assigned names; falls back to Rhino type labels grouped by
 * count ("2× polysurface, 1× curve") when all names are blank; falls back
 * to a bare count as last resort. Shared between the in-chat MessageBubble
 * rendering and the pre-send InputBar attachment chip so both stay in
 * sync — previously `names.join(", ")` returned truthy ", " on ["", ""],
 * so the `|| count` fallback silently never fired and we rendered
 * "Auswahl: ,".
 */
export function selectionLabel(block: SelectionBlock): string {
  const nonEmpty = block.names.filter((n) => n && n.trim() !== "");
  if (nonEmpty.length > 0) return nonEmpty.join(", ");
  const types = block.types ?? [];
  if (types.length > 0) {
    const counts = new Map<string, number>();
    for (const t of types) counts.set(t, (counts.get(t) ?? 0) + 1);
    return Array.from(counts.entries())
      .map(([t, c]) => `${c}× ${t}`)
      .join(", ");
  }
  return `${block.object_ids.length} Objekte`;
}

// Kurzform fuer den Inline-Chip: Mehrfachauswahl -> "N Objekte", Einzel -> Name
// (ohne "Objekt:"-Praefix), damit Auswahl-Chips wie die Objekt-/K/F/O-Chips
// aussehen (Box-Icon + sauberer Name).
export function selectionShortLabel(block: SelectionBlock): string {
  if (block.object_ids.length > 1) return `${block.object_ids.length} Objekte`;
  return selectionLabel(block);
}
