import type { ComponentPickBlock } from "./types";

function hostLabel(block: ComponentPickBlock): string | null {
  const name = block.object_name?.trim();
  if (name) return name;
  if (block.object_type_name?.trim()) return block.object_type_name.trim();
  if (block.object_id?.trim()) return block.object_id.trim();
  return null;
}

export function componentPickLabel(block: ComponentPickBlock): string {
  const kind =
    block.component_type === "face"
      ? "Fläche"
      : block.component_type === "vertex"
        ? "Vertex"
        : block.component_type === "object"
          ? "Objekt"
          : "Kante";
  const prefix =
    block.geometry_class === "subd"
      ? "SubD-"
      : block.geometry_class === "mesh"
        ? "Mesh-"
        : "";
  const label = `${prefix}${kind}`;
  const parts: string[] = [];
  if (typeof block.component_index === "number") {
    parts.push(`${label} #${block.component_index}`);
  } else {
    parts.push(label);
  }
  const host = hostLabel(block);
  if (host) parts.push(`auf ${host}`);
  return parts.join(" ");
}

// Kurzform fuer den Inline-Chip: OHNE das Typ-Wort (Kante/Flaeche/…) — das zeigt
// im Chip ein Typ-Icon. Nur Index + Host, z.B. "#9 · Box" (Objekt-Pick: nur der
// Host). Das volle Label (mit Wort) bleibt fuer aria-label/Titel.
export function componentPickShortLabel(block: ComponentPickBlock): string {
  const host = hostLabel(block);
  // "#0 auf Box" liest sich als "(Flaeche) #0 auf Box" — das "auf" macht die
  // Komponente-auf-Host-Beziehung klar (statt eines neutralen "·"). Objekt-Pick
  // (kein Index): nur der Host.
  if (typeof block.component_index === "number") {
    return host
      ? `#${block.component_index} auf ${host}`
      : `#${block.component_index}`;
  }
  return host ?? "Komponente";
}
