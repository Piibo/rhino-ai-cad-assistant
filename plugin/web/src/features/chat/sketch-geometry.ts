// Pure 2D-Geometrie fuers Sketch-Tool (Kurven-Glaettung + Ellipse). Bewusst
// frei von React/DOM, damit die Mathematik isoliert testbar ist
// (sketch-geometry.test.ts). Punkte sind in Backdrop-/viewBox-Pixeln.

export type Pt = { x: number; y: number };

/** Senkrechter Abstand von p zur Geraden a-b (fuer RDP). */
export function perpDist(p: Pt, a: Pt, b: Pt): number {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const len2 = dx * dx + dy * dy;
  if (len2 === 0) return Math.hypot(p.x - a.x, p.y - a.y);
  const t = ((p.x - a.x) * dx + (p.y - a.y) * dy) / len2;
  return Math.hypot(p.x - (a.x + t * dx), p.y - (a.y + t * dy));
}

/**
 * Ramer-Douglas-Peucker: reduziert eine dichte Freihand-Spur auf ihre
 * tragenden Stuetzpunkte (entfernt Hand-Jitter). Basis des Kurven-Werkzeugs.
 */
export function simplifyRDP(points: Pt[], epsilon: number): Pt[] {
  if (points.length < 3) return points.slice();
  let maxD = 0;
  let idx = 0;
  const a = points[0];
  const b = points[points.length - 1];
  for (let i = 1; i < points.length - 1; i++) {
    const d = perpDist(points[i], a, b);
    if (d > maxD) {
      maxD = d;
      idx = i;
    }
  }
  if (maxD > epsilon) {
    const left = simplifyRDP(points.slice(0, idx + 1), epsilon);
    const right = simplifyRDP(points.slice(idx), epsilon);
    return left.slice(0, -1).concat(right);
  }
  return [a, b];
}

/** Catmull-Rom-Spline durch die Stuetzpunkte -> dichte, flowing Polylinie. */
export function catmullRom(pts: Pt[], samples = 16): Pt[] {
  if (pts.length < 3) return pts.slice();
  const out: Pt[] = [pts[0]];
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[i - 1] ?? pts[i];
    const p1 = pts[i];
    const p2 = pts[i + 1];
    const p3 = pts[i + 2] ?? pts[i + 1];
    for (let j = 1; j <= samples; j++) {
      const t = j / samples;
      const t2 = t * t;
      const t3 = t2 * t;
      out.push({
        x:
          0.5 *
          (2 * p1.x +
            (-p0.x + p2.x) * t +
            (2 * p0.x - 5 * p1.x + 4 * p2.x - p3.x) * t2 +
            (-p0.x + 3 * p1.x - 3 * p2.x + p3.x) * t3),
        y:
          0.5 *
          (2 * p1.y +
            (-p0.y + p2.y) * t +
            (2 * p0.y - 5 * p1.y + 4 * p2.y - p3.y) * t2 +
            (-p0.y + 3 * p1.y - 3 * p2.y + p3.y) * t3),
      });
    }
  }
  return out;
}

/** Chaikin corner-cutting: rundet die Polylinie (Kurven-Nachglaetten). */
export function chaikin(pts: Pt[], iterations = 1): Pt[] {
  let cur = pts;
  for (let k = 0; k < iterations; k++) {
    if (cur.length < 3) return cur;
    const next: Pt[] = [cur[0]];
    for (let i = 0; i < cur.length - 1; i++) {
      const a = cur[i];
      const b = cur[i + 1];
      next.push(
        { x: 0.75 * a.x + 0.25 * b.x, y: 0.75 * a.y + 0.25 * b.y },
        { x: 0.25 * a.x + 0.75 * b.x, y: 0.25 * a.y + 0.75 * b.y },
      );
    }
    next.push(cur[cur.length - 1]);
    cur = next;
  }
  return cur;
}

/**
 * Kurven-Werkzeug: rohe Drag-Spur -> RDP-Simplify -> Catmull-Rom-Spline ->
 * 1x Chaikin-Nachglaetten. Ergibt eine klar geschwungene, flowing Kurve
 * (anders als das jittrige Freihand-Zeichnen). Epsilon adaptiv zur Gesten-
 * Groesse, damit es unabhaengig von der Backdrop-Aufloesung gleich wirkt.
 * Bei <3 Punkten unveraendert.
 */
export function smoothCurve(raw: Pt[]): Pt[] {
  if (raw.length < 3) return raw.slice();
  let minX = raw[0].x;
  let minY = raw[0].y;
  let maxX = raw[0].x;
  let maxY = raw[0].y;
  for (const p of raw) {
    if (p.x < minX) minX = p.x;
    if (p.x > maxX) maxX = p.x;
    if (p.y < minY) minY = p.y;
    if (p.y > maxY) maxY = p.y;
  }
  const diag = Math.hypot(maxX - minX, maxY - minY);
  const epsilon = Math.max(3, diag * 0.014);
  return chaikin(catmullRom(simplifyRDP(raw, epsilon), 16), 1);
}

/** Punkte einer Ellipse aus zwei Bounding-Box-Ecken (geschlossener Ring). */
export function ellipsePoints(a: Pt, b: Pt, steps = 48): Pt[] {
  const cx = (a.x + b.x) / 2;
  const cy = (a.y + b.y) / 2;
  const rx = Math.abs(b.x - a.x) / 2;
  const ry = Math.abs(b.y - a.y) / 2;
  const pts: Pt[] = [];
  for (let i = 0; i <= steps; i++) {
    const angle = (i / steps) * 2 * Math.PI;
    pts.push({ x: cx + rx * Math.cos(angle), y: cy + ry * Math.sin(angle) });
  }
  return pts;
}
