import { describe, expect, it } from "vitest";
import demoData from "../../src/virtuoso_workbench/demo.json";
import { bounds, buildScene, collectNets, countLabel, segmentDistance } from "./logic";
import type { Snapshot } from "./types";

const demo = { ...demoData, revision: "synthetic" } as unknown as Snapshot;

describe("scene geometry", () => {
  it("preserves wire extents and places every device and terminal", () => {
    const scene = buildScene(demo);
    expect(scene.devices).toHaveLength(5);
    expect(scene.devices.flatMap((d) => d.ports)).toHaveLength(20);
    expect(scene.area).toEqual({ left: -8, right: 8, top: 8, bottom: -9 });
    expect(scene.devices.flatMap((d) => d.ports).every((p) => p.position.every(Number.isFinite))).toBe(true);
    expect(demo.instances[0].terminals[0].xy).toEqual([-4, 6.7]);
  });
  it("gives an empty schematic finite default bounds", () => {
    const empty = { ...demo, instances: [], pins: [], wires: [] };
    expect(buildScene(empty)).toEqual({ devices: [], area: { left: -1, right: 1, top: 1, bottom: -1 } });
    expect(collectNets(empty).size).toBe(0);
  });
  it("places devices without geometry using the existing fallback", () => {
    const snapshot = { ...demo, pins: [], wires: [], instances: [{ ...demo.instances[0], xy: null, bbox: [], terminals: [{ name: "A", net: null, xy: null }] }] };
    expect(buildScene(snapshot).devices[0].ports[0].position.every(Number.isFinite)).toBe(true);
  });
  it("counts logical membership separately from wire segments", () => {
    const nets = collectNets(demo);
    expect(nets.get("VDD")?.pins).toEqual(["VDD"]);
    expect(nets.get("VDD")?.segments).toBe(4);
    expect(nets.get("NREF")?.terminals).toContain("MP1.D");
    expect(nets.has("")).toBe(false);
  });
  it("computes hit distances for segments and zero-length segments", () => {
    expect(segmentDistance(2, 3, [0, 0], [4, 0])).toBe(3);
    expect(segmentDistance(3, 4, [0, 0], [0, 0])).toBe(5);
    expect(segmentDistance(8, 0, [0, 0], [4, 0])).toBe(4);
    expect(bounds([[1, 2], [-1, -3]])).toEqual({ left: -1, right: 1, top: 2, bottom: -3 });
    expect(countLabel(1, "pin")).toBe("1 pin");
    expect(countLabel(0, "pin")).toBe("0 pins");
  });
});
