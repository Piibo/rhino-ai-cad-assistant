import { describe, it, expect } from "vitest";
import {
  perpDist,
  simplifyRDP,
  catmullRom,
  chaikin,
  smoothCurve,
  ellipsePoints,
  type Pt,
} from "./sketch-geometry";

const P = (x: number, y: number): Pt => ({ x, y });
const finite = (pts: Pt[]) =>
  pts.every((p) => Number.isFinite(p.x) && Number.isFinite(p.y));

describe("perpDist", () => {
  it("returns Euclidean distance for a zero-length segment", () => {
    expect(perpDist(P(3, 4), P(0, 0), P(0, 0))).toBeCloseTo(5);
  });
  it("measures perpendicular distance to the line", () => {
    // line along x-axis; point at y=2 -> distance 2
    expect(perpDist(P(5, 2), P(0, 0), P(10, 0))).toBeCloseTo(2);
  });
  it("measures to the INFINITE line, not the segment (RDP semantics)", () => {
    // point beyond the segment end, but on the line -> distance 0
    expect(perpDist(P(20, 0), P(0, 0), P(10, 0))).toBeCloseTo(0);
  });
});

describe("simplifyRDP", () => {
  it("returns a copy for <3 points", () => {
    const inp = [P(0, 0), P(1, 1)];
    const out = simplifyRDP(inp, 1);
    expect(out).toEqual(inp);
    expect(out).not.toBe(inp);
  });
  it("collapses a near-collinear run to its 2 endpoints", () => {
    const line = Array.from({ length: 20 }, (_, i) => P(i, 0.01 * (i % 2)));
    const out = simplifyRDP(line, 1);
    expect(out).toHaveLength(2);
    expect(out[0]).toEqual(P(0, 0));
    expect(out[1]).toEqual(line[line.length - 1]);
  });
  it("keeps a clear corner point", () => {
    const out = simplifyRDP([P(0, 0), P(5, 50), P(10, 0)], 1);
    expect(out).toHaveLength(3);
    expect(out[1]).toEqual(P(5, 50));
  });
  it("does not throw on all-identical points", () => {
    const out = simplifyRDP([P(2, 2), P(2, 2), P(2, 2), P(2, 2)], 1);
    expect(finite(out)).toBe(true);
  });
  it("terminates on a long dense input", () => {
    const dense = Array.from({ length: 600 }, (_, i) =>
      P(i, Math.sin(i / 20) * 30),
    );
    const out = simplifyRDP(dense, 2);
    expect(out.length).toBeGreaterThan(2);
    expect(out.length).toBeLessThan(dense.length);
  });
});

describe("catmullRom", () => {
  it("returns a copy for <3 points", () => {
    const inp = [P(0, 0), P(1, 1)];
    expect(catmullRom(inp)).toEqual(inp);
  });
  it("starts exactly at the first control point", () => {
    const pts = [P(0, 0), P(10, 10), P(20, 0)];
    expect(catmullRom(pts, 8)[0]).toEqual(P(0, 0));
  });
  it("passes through interior control points at segment boundaries", () => {
    const pts = [P(0, 0), P(10, 10), P(20, 0), P(30, 10)];
    const samples = 10;
    const out = catmullRom(pts, samples);
    // out[0] = pts[0]; out[i*samples] = pts[i]
    expect(out[samples].x).toBeCloseTo(pts[1].x);
    expect(out[samples].y).toBeCloseTo(pts[1].y);
    expect(out[2 * samples].x).toBeCloseTo(pts[2].x);
    expect(out[2 * samples].y).toBeCloseTo(pts[2].y);
  });
  it("does not NaN at the ends (endpoint duplication)", () => {
    expect(finite(catmullRom([P(0, 0), P(5, 5), P(10, 0)], 6))).toBe(true);
  });
});

describe("chaikin", () => {
  it("returns input unchanged for <3 points", () => {
    const inp = [P(0, 0), P(1, 1)];
    expect(chaikin(inp, 1)).toBe(inp);
  });
  it("0 iterations is a no-op", () => {
    const inp = [P(0, 0), P(1, 1), P(2, 0)];
    expect(chaikin(inp, 0)).toBe(inp);
  });
  it("preserves first/last endpoints and grows interior points", () => {
    const inp = [P(0, 0), P(10, 10), P(20, 0)];
    const out = chaikin(inp, 1);
    expect(out[0]).toEqual(P(0, 0));
    expect(out[out.length - 1]).toEqual(P(20, 0));
    expect(out.length).toBeGreaterThan(inp.length);
  });
});

describe("smoothCurve", () => {
  it("returns a copy for <3 points", () => {
    const inp = [P(0, 0), P(1, 1)];
    const out = smoothCurve(inp);
    expect(out).toEqual(inp);
    expect(out).not.toBe(inp);
  });
  it("degenerate bbox (all identical points) yields no NaN", () => {
    const out = smoothCurve([P(5, 5), P(5, 5), P(5, 5), P(5, 5)]);
    expect(finite(out)).toBe(true);
  });
  it("produces a dense, finite polyline for an S-curve", () => {
    const s = Array.from({ length: 60 }, (_, i) =>
      P(i * 5, Math.sin(i / 8) * 40 + (i % 3)),
    );
    const out = smoothCurve(s);
    expect(out.length).toBeGreaterThan(10);
    expect(finite(out)).toBe(true);
  });
  it("is scale-equivariant above the epsilon floor (uniform scale -> equal count)", () => {
    // Both gestures must clear the epsilon floor (max(3, diag*0.014)); a large
    // base ensures epsilon = diag*0.014, which scales linearly. RDP is then
    // scale-equivariant, so a uniformly scaled gesture keeps the same support
    // points -> identical output length. (A tiny gesture would hit the floor and
    // break this — that floor is intentional, not tested here.)
    const base = Array.from({ length: 40 }, (_, i) =>
      P(i * 10, Math.sin(i / 6) * 100),
    );
    const scaled = base.map((p) => P(p.x * 5, p.y * 5));
    expect(smoothCurve(base).length).toBe(smoothCurve(scaled).length);
  });
});

describe("ellipsePoints", () => {
  it("returns steps+1 points and is a closed loop", () => {
    const out = ellipsePoints(P(0, 0), P(100, 50), 24);
    expect(out).toHaveLength(25);
    expect(out[0].x).toBeCloseTo(out[24].x);
    expect(out[0].y).toBeCloseTo(out[24].y);
  });
  it("zero-size bbox collapses to the center without NaN", () => {
    const out = ellipsePoints(P(7, 7), P(7, 7), 8);
    expect(finite(out)).toBe(true);
    expect(out.every((p) => p.x === 7 && p.y === 7)).toBe(true);
  });
  it("derives center and radii from the two corners", () => {
    const out = ellipsePoints(P(0, 0), P(100, 40), 4);
    // i=0 -> angle 0 -> (cx+rx, cy) = (100, 20)
    expect(out[0].x).toBeCloseTo(100);
    expect(out[0].y).toBeCloseTo(20);
  });
});
