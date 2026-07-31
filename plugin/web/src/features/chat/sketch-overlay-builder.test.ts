import { describe, it, expect } from "vitest";
import { buildSketchOverlayFromGroup } from "./sketch-overlay-builder";
import type { ImageSource, SketchBlock, SketchView } from "@/lib/types";

const img = (data: string): ImageSource => ({
  type: "base64",
  media_type: "image/jpeg",
  data,
});
const view = (name: string, label: string, data: string): SketchView => ({
  name,
  label,
  source: img(data),
  width: 1600,
  height: 900,
});

const ALL_VIEWS: SketchView[] = [
  view("perspective", "Perspektive", "persp-bg"),
  view("top", "Top", "top-bg"),
  view("front", "Front", "front-bg"),
  view("right", "Right", "right-bg"),
];

// A painted block, as done() stages it: view_name = the LABEL, background = the
// clean view backdrop, svg = the strokes, all_views/composite on the first block.
const painted = (over: Partial<SketchBlock> = {}): SketchBlock => ({
  type: "sketch",
  svg: "<svg>strokes</svg>",
  rendered_png: img("painted-png"),
  background: img("persp-bg"),
  view_name: "Perspektive",
  has_strokes: true,
  width: 1600,
  height: 900,
  sketch_group: "g1",
  composite: img("grid"),
  all_views: ALL_VIEWS,
  ...over,
});

describe("buildSketchOverlayFromGroup", () => {
  it("returns null for no members", () => {
    expect(buildSketchOverlayFromGroup([], "g1")).toBeNull();
  });

  it("all_views branch: picker = all four views, strokes keyed by real name", () => {
    const out = buildSketchOverlayFromGroup([painted()], "g1");
    expect(out).not.toBeNull();
    expect(out!.views).toBe(ALL_VIEWS); // all 4 paintable, not only the painted one
    // "Perspektive" (the stored view_name=label) maps to view name "perspective"
    expect(out!.initialStrokesByView).toEqual({
      perspective: "<svg>strokes</svg>",
    });
    expect(out!.composite.source).toEqual(img("grid"));
    expect(out!.reopenGroupId).toBe("g1");
  });

  it("all_views branch: a stroke whose view_name matches no view is dropped (no crash)", () => {
    const out = buildSketchOverlayFromGroup(
      [painted({ view_name: "Schraegansicht" })],
      "g1",
    );
    expect(out!.views).toBe(ALL_VIEWS); // still all four
    expect(out!.initialStrokesByView).toBeUndefined(); // nothing matched -> empty -> undefined
  });

  it("all_views branch: strokeless group -> all views paintable, no initial strokes", () => {
    const snapshot: SketchBlock = {
      type: "sketch",
      svg: "",
      rendered_png: img("grid"),
      composite: img("grid"),
      has_strokes: false,
      width: 1600,
      height: 1200,
      sketch_group: "g1",
      all_views: ALL_VIEWS,
    };
    const out = buildSketchOverlayFromGroup([snapshot], "g1");
    expect(out!.views).toBe(ALL_VIEWS);
    expect(out!.initialStrokesByView).toBeUndefined();
  });

  it("fallback (no all_views): one view per member, strokes only with clean background", () => {
    const a: SketchBlock = {
      type: "sketch",
      svg: "<svg>A</svg>",
      background: img("a-bg"),
      view_name: "Top",
      has_strokes: true,
      width: 800,
      height: 600,
      sketch_group: "g2",
      composite: img("grid2"),
    };
    const b: SketchBlock = {
      type: "sketch",
      svg: "<svg>B</svg>",
      rendered_png: img("b-png"), // no clean background -> stroke must NOT reload
      view_name: "Front",
      has_strokes: true,
      width: 800,
      height: 600,
      sketch_group: "g2",
    };
    const out = buildSketchOverlayFromGroup([a, b], "g2");
    expect(out!.views.map((v) => v.name)).toEqual(["Top", "Front"]);
    expect(out!.initialStrokesByView).toEqual({ Top: "<svg>A</svg>" });
    expect(out!.composite.source).toEqual(img("grid2"));
  });

  it("fallback: returns null when no member yields a usable source", () => {
    const noSrc: SketchBlock = {
      type: "sketch",
      svg: "",
      has_strokes: false,
      width: 100,
      height: 100,
      sketch_group: "g3",
    };
    expect(buildSketchOverlayFromGroup([noSrc], "g3")).toBeNull();
  });

  it("composite falls back to the first view source when no composite exists", () => {
    const a: SketchBlock = {
      type: "sketch",
      svg: "<svg>A</svg>",
      background: img("a-bg"),
      view_name: "Top",
      has_strokes: true,
      width: 800,
      height: 600,
      sketch_group: "g4",
    };
    const out = buildSketchOverlayFromGroup([a], "g4");
    expect(out!.composite.source).toEqual(img("a-bg"));
  });
});
