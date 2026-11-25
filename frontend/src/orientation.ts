import type { Point, Snapshot } from "./types";
import type { DraftChange, Flip } from "./requests";

type Matrix = [[number, number], [number, number]];
export function compose(left: Matrix, right: Matrix): Matrix {
  const cell = (i: number, j: number): number => {
    const value = left[i][0] * right[0][j] + left[i][1] * right[1][j];
    return value === 0 ? 0 : value;
  };
  return [[cell(0, 0), cell(0, 1)], [cell(1, 0), cell(1, 1)]];
}
export const orientations: Record<string, Matrix> = {
  R0: [[1, 0], [0, 1]], R90: [[0, -1], [1, 0]], R180: [[-1, 0], [0, -1]], R270: [[0, 1], [-1, 0]],
  MY: [[-1, 0], [0, 1]], MX: [[1, 0], [0, -1]],
};
orientations.MYR90 = compose(orientations.R90, orientations.MY);
orientations.MXR90 = compose(orientations.R90, orientations.MX);

export function flippedOrientation(orientation: string): string {
  const matrix = Object.hasOwn(orientations, orientation) ? orientations[orientation] : undefined;
  if (!matrix) throw new Error("Unsupported instance orientation.");
  const result = compose(orientations.MY, matrix);
  return Object.entries(orientations).find(([, candidate]) => candidate.every((row, i) => row.every((value, j) => value === result[i][j])))![0];
}

export function toggleFlip(changes: DraftChange[], snapshot: Snapshot, instance: string): DraftChange[] {
  const previous = changes.find((entry) => entry.op === "flip-horizontal" && entry.instance === instance);
  if (previous) return changes.filter((entry) => entry !== previous);
  const selected = snapshot.instances.find((entry) => entry.name === instance);
  if (!selected) throw new Error("Select an instance to flip.");
  if (changes.length >= 200) throw new Error("A request can contain at most 200 changes.");
  const flip: Flip = { op: "flip-horizontal", instance, from_orientation: selected.orientation, to_orientation: flippedOrientation(selected.orientation) };
  return [...changes, flip];
}

export function flipPreview(snapshot: Snapshot, changes: DraftChange[]): Snapshot {
  return { ...snapshot, instances: snapshot.instances.map((original) => {
    let instance = original;
    for (const change of changes) {
      if (change.op !== "flip-horizontal" || change.instance !== instance.name) continue;
      if (change.from_orientation !== instance.orientation || flippedOrientation(instance.orientation) !== change.to_orientation) throw new Error("The flip does not match the current orientation.");
      const bbox = instance.bbox;
      const mirror = (point: Point): Point => [bbox[0][0] + bbox[1][0] - point[0], point[1]];
      instance = { ...instance, orientation: change.to_orientation };
      if (bbox.length !== 2) continue;
      const points = bbox.map(mirror);
      instance = { ...instance, xy: instance.xy ? mirror(instance.xy) : null,
        bbox: [[Math.min(...points.map(([x]) => x)), Math.min(...points.map(([, y]) => y))], [Math.max(...points.map(([x]) => x)), Math.max(...points.map(([, y]) => y))]],
        terminals: instance.terminals.map((terminal) => ({ ...terminal, xy: terminal.xy ? mirror(terminal.xy) : null })),
      };
    }
    return instance;
  }) };
}
