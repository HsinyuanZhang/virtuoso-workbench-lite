import { expect, it } from "vitest";
import demoData from "../../src/virtuoso_workbench/demo.json";
import { buildScene } from "./logic";
import { compose, flippedOrientation, flipPreview, orientations, toggleFlip } from "./orientation";
import { parseChange, setParameters } from "./requests";
import type { Snapshot } from "./types";

const snapshot = { ...demoData, revision: "synthetic" } as unknown as Snapshot;
const mapping = { R0: "MY", R90: "MXR90", R180: "MX", R270: "MYR90", MY: "R0", MYR90: "R270", MX: "R180", MXR90: "R90" };
it.each(Object.entries(mapping))("mirrors %s to %s with world matrix composition", (from, to) => {
  expect(flippedOrientation(from)).toBe(to);
  expect(flippedOrientation(to)).toBe(from);
  expect(compose(orientations.MY, orientations[from])).toEqual(orientations[to]);
  expect(compose(orientations.MY, orientations[to])).toEqual(orientations[from]);
});
it("toggles only the selected flip, retains other edits, and parses the operation", () => {
  const parameters = setParameters([], "MN1", [["w", "2u"]]);
  const changes = toggleFlip(parameters, snapshot, "MN1");
  expect(parseChange(changes[1])).toEqual({ op: "flip-horizontal", instance: "MN1", from_orientation: "R0", to_orientation: "MY" });
  expect(toggleFlip(changes, snapshot, "MN1")).toEqual(parameters);
  expect(toggleFlip(toggleFlip(changes, snapshot, "MN2"), snapshot, "MN1")).toHaveLength(2);
  expect(parameters).toHaveLength(1);
  expect(() => toggleFlip([], snapshot, "missing")).toThrow();
  expect(() => flippedOrientation("unknown")).toThrow();
  expect(() => flippedOrientation("constructor")).toThrow();
});
it("mirrors terminal geometry without moving wires or the bbox center and does not mutate", () => {
  const before = structuredClone(snapshot);
  const changes = toggleFlip([], snapshot, "MN1");
  const preview = flipPreview(snapshot, changes);
  const original = snapshot.instances.find((instance) => instance.name === "MN1")!;
  const flipped = preview.instances.find((instance) => instance.name === "MN1")!;
  const sum = original.bbox[0][0] + original.bbox[1][0];
  expect(flipped.bbox).toEqual(original.bbox);
  expect(flipped.orientation).toBe("MY");
  flipped.terminals.forEach((terminal, index) => expect(terminal.xy![0]).toBeCloseTo(sum - original.terminals[index].xy![0]));
  expect(preview.wires).toEqual(snapshot.wires);
  expect(snapshot).toEqual(before);
  expect(flipPreview(snapshot, toggleFlip(changes, snapshot, "MN1"))).toEqual(snapshot);
  const asymmetric = { ...snapshot, instances: [{ ...original, terminals: [{ ...original.terminals[0], xy: [original.bbox[0][0], 0] as [number, number] }] }] };
  expect(buildScene(flipPreview(asymmetric, changes)).devices[0].x).toBe(buildScene(asymmetric).devices[0].x);
});
it("handles empty and unknown geometry and rejects inconsistent ordered flips", () => {
  expect(flipPreview({ ...snapshot, instances: [] }, []).instances).toEqual([]);
  const unknown = { ...snapshot, instances: [{ ...snapshot.instances[0], bbox: [], xy: null, terminals: [{ name: "G", net: null, xy: null }] }] };
  const before = structuredClone(unknown);
  const changes = toggleFlip([], unknown, unknown.instances[0].name);
  const preview = flipPreview(unknown, changes);
  expect(preview.instances[0]).toEqual({ ...unknown.instances[0], orientation: "MY" });
  expect(unknown).toEqual(before);
  expect(() => flipPreview(unknown, [...changes, ...changes])).toThrow("current orientation");
  expect(unknown).toEqual(before);
});
