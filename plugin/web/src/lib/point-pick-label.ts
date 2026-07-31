import type { PointPickBlock } from "./types";

function formatCoord(value: number | undefined): string {
  return typeof value === "number" ? value.toFixed(1) : "?";
}

function hostLabel(block: PointPickBlock): string | null {
  const name = block.object_name?.trim();
  if (name) return name;
  if (block.object_type?.trim()) return block.object_type.trim();
  if (block.object_id?.trim()) return block.object_id.trim();
  return null;
}

// Englischer Osnap-Enum-Name -> deutsche Kurzbezeichnung (Spiegel von
// _SNAP_LABELS_DE im Backend, pick.py). Nur fuer die Anzeige; das Backend
// schickt den Rohwert (z.B. "End") auch an das Modell.
const SNAP_LABELS_DE: Record<string, string> = {
  end: "Endpunkt",
  near: "Nahe",
  point: "Punkt",
  midpoint: "Mittelpunkt",
  center: "Zentrum",
  intersection: "Schnittpunkt",
  perpendicular: "Senkrecht",
  tangent: "Tangente",
  quadrant: "Quadrant",
  knot: "Knoten",
  vertex: "Vertex",
  focus: "Brennpunkt",
};

function snapLabelDe(snap: string | undefined | null): string {
  const key = snap?.trim().toLowerCase();
  if (!key) return "";
  for (const token in SNAP_LABELS_DE) {
    if (key.includes(token)) return SNAP_LABELS_DE[token];
  }
  return snap!.trim();
}

export function pointPickLabel(block: PointPickBlock): string {
  const [x, y, z] = block.point;
  const parts = [
    `Punkt: (${formatCoord(x)}, ${formatCoord(y)}, ${formatCoord(z)})`,
  ];
  const host = hostLabel(block);
  if (host) parts.push(`auf ${host}`);
  const snap = snapLabelDe(block.snap_type);
  if (snap) parts.push(`Snap: ${snap}`);
  return parts.join(" · ");
}

// Kurzform fuer den Inline-Chip: GLEICHE Token-Struktur wie componentPickShortLabel
// ("`·`"-getrennt, KEINE Prosa wie "Punkt:"/"auf"/"Snap:"), damit Punkt- und
// K/F/O-Chips einheitlich aussehen. z.B. "(10, 10, 0) · Box · Endpunkt".
export function pointPickShortLabel(block: PointPickBlock): string {
  const [x, y, z] = block.point;
  const parts = [`(${formatCoord(x)}, ${formatCoord(y)}, ${formatCoord(z)})`];
  const host = hostLabel(block);
  if (host) parts.push(host);
  const snap = snapLabelDe(block.snap_type);
  if (snap) parts.push(snap);
  return parts.join(" · ");
}
