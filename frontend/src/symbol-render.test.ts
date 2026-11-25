import { expect, it } from "vitest";
import demoData from "../../src/virtuoso_workbench/demo.json";
import packageData from "../../fixtures/demo-symbols.json";
import { buildScene } from "./logic";
import { flipPreview, toggleFlip } from "./orientation";
import { PIN_TOLERANCE, resolveSymbol, symbolWorldPoints, transformSymbolPoint } from "./symbol-render";
import type { Instance, LibrarySymbol, LibrarySymbolPackage, Point, Snapshot } from "./types";

const snapshot = demoData as unknown as Snapshot;
const symbols = packageData as unknown as LibrarySymbolPackage;

function required<T>(value: T | undefined, label: string): T {
  if (value === undefined) throw new Error(`Missing ${label} fixture.`);
  return value;
}

function withAnchors(instance: Instance, symbol: LibrarySymbol): Instance {
  return {
    ...instance,
    terminals: instance.terminals.map((terminal) => {
      const packaged = symbol.terminals.find((entry) => entry.name === terminal.name);
      if (!packaged) return terminal;
      const anchors = packaged.pin_figures.flatMap((figure) => figure.anchors.map((point) => transformSymbolPoint(point, symbol, instance)));
      return { ...terminal, xy: anchors[0] ?? terminal.xy, source_pin_anchors: anchors };
    }),
  };
}

it("binds demo symbols only by identity, cell, and actual view", () => {
  const instance = required(snapshot.instances.find((item) => item.name === "MP1"), "PMOS instance");
  expect(resolveSymbol(instance, [symbols]).reason).toBe("resolved");
  expect(resolveSymbol({ ...instance, library_identity: "sha256:" + "0".repeat(64) }, [symbols]).symbol).toBeNull();
  expect(resolveSymbol({ ...instance, master_view: "schematic" }, [symbols]).symbol).toBeNull();
  expect(resolveSymbol({ ...instance, library_identity: undefined }, [symbols]).reason).toContain("identity unavailable");
  expect(resolveSymbol({ ...instance, terminals: instance.terminals.slice(1) }, [symbols]).reason).toBe("terminal set mismatch");
  expect(resolveSymbol({ ...instance, orientation: "constructor" }, [symbols]).reason).toBe("unsupported orientation");
  expect(resolveSymbol({ ...instance, symbol_metadata_error: "unsupported-magnification" }, [symbols]).reason).toContain("magnification");
  expect(resolveSymbol({ ...instance, symbol_metadata_error: "unsupported-parameterized-variant" }, [symbols]).reason)
    .toBe("parameterized master variant is unsupported");
  expect(resolveSymbol({ ...instance, symbol_metadata_error: "metadata-invalid-variant-status" }, [symbols]).reason)
    .toBe("live master variant metadata invalid");
});

it("uses independent world-coordinate matrices for points, boxes, and every pin", () => {
  const matrices: Record<string, [[number, number], [number, number]]> = {
    R0: [[1, 0], [0, 1]], R90: [[0, -1], [1, 0]], R180: [[-1, 0], [0, -1]], R270: [[0, 1], [-1, 0]],
    MY: [[-1, 0], [0, 1]], MYR90: [[0, -1], [-1, 0]], MX: [[1, 0], [0, -1]], MXR90: [[0, 1], [1, 0]],
  };
  const base = required(snapshot.instances.find((item) => item.name === "MP1"), "PMOS instance");
  const symbol = { ...required(symbols.symbols.find((entry) => entry.cell === "pmos"), "PMOS symbol"), origin: [2, -3] as Point, bbox: [[-1, -2], [4, 3]] as Point[] };
  const instance: Instance = { ...base, xy: [7, 11] };
  const localPoint: Point = [4, 1];
  const boxCorners: Point[] = [[-1, -2], [-1, 3], [4, -2], [4, 3]];
  const pinPoints: Point[] = [[0, 1.7], [-1.7, 0], [1.7, 0], [0, -1.7]];
  for (const [orientation, matrix] of Object.entries(matrices)) {
    const oriented = { ...instance, orientation };
    const vector = (point: Point): Point => [point[0] - symbol.origin[0], point[1] - symbol.origin[1]];
    const expected = (point: Point): Point => {
      const [x, y] = vector(point);
      return [7 + matrix[0][0] * x + matrix[0][1] * y, 11 + matrix[1][0] * x + matrix[1][1] * y];
    };
    expect(transformSymbolPoint(localPoint, symbol, oriented)).toEqual(expected(localPoint));
    expect(boxCorners.map((point) => transformSymbolPoint(point, symbol, oriented))).toEqual(boxCorners.map(expected));
    expect(pinPoints.map((point) => transformSymbolPoint(point, symbol, oriented))).toEqual(pinPoints.map(expected));
  }
});

it("mirrors origin and source anchors around an asymmetric bbox center", () => {
  const base = required(snapshot.instances.find((item) => item.name === "MP1"), "PMOS instance");
  const asymmetric: Instance = {
    ...base, xy: [3, 5], bbox: [[1, 2], [6, 8]],
    terminals: base.terminals.map((terminal) => ({ ...terminal, source_pin_anchors: [[terminal.xy?.[0] ?? 0, terminal.xy?.[1] ?? 0], [4.25, 6.5]] })),
  };
  const local = required(symbols.symbols.find((entry) => entry.cell === "pmos"), "PMOS symbol");
  const sample: Point = [0.4, 1.2];
  const before = transformSymbolPoint(sample, local, asymmetric);
  const change = toggleFlip([], { ...snapshot, instances: [asymmetric] }, asymmetric.name);
  const flipped = flipPreview({ ...snapshot, instances: [asymmetric] }, change).instances[0];
  const after = transformSymbolPoint(sample, local, flipped);
  expect(after).toEqual([7 - before[0], before[1]]);
  expect(flipped.terminals[0].source_pin_anchors).toEqual(asymmetric.terminals[0].source_pin_anchors?.map(([x, y]) => [7 - x, y]));
  const restored = flipPreview({ ...snapshot, instances: [asymmetric] }, toggleFlip(change, { ...snapshot, instances: [asymmetric] }, asymmetric.name)).instances[0];
  expect(restored).toEqual(asymmetric);
});

it("keeps a non-binary bbox exact across flip previews and UI toggle cancellation", () => {
  const base = required(snapshot.instances.find((item) => item.name === "MP1"), "PMOS instance");
  const asymmetric: Instance = { ...base, xy: [0.17, 0.5], bbox: [[0.1, 0.2], [0.3, 0.7]] };
  const source = { ...snapshot, instances: [asymmetric] };
  const firstChange = toggleFlip([], source, asymmetric.name);
  const once = flipPreview(source, firstChange);
  expect(once.instances[0].bbox).toEqual(asymmetric.bbox);

  const secondChange = toggleFlip([], once, asymmetric.name);
  const twice = flipPreview(once, secondChange);
  expect(twice.instances[0].bbox).toEqual(asymmetric.bbox);

  const cancelledChange = toggleFlip(firstChange, source, asymmetric.name);
  expect(cancelledChange).toEqual([]);
  expect(flipPreview(source, cancelledChange).instances[0]).toEqual(asymmetric);
});

it("matches two distinct source figures, creates two hit ports, and keeps them aligned after flip", () => {
  const base = required(symbols.symbols.find((entry) => entry.cell === "nmos"), "NMOS symbol");
  const instance = required(snapshot.instances.find((item) => item.name === "MN1"), "NMOS instance");
  const multi: LibrarySymbol = {
    ...base,
    terminals: base.terminals.map((terminal) => terminal.name === "D"
      ? { ...terminal, pin_figures: [...terminal.pin_figures, { bbox: [[0.15, 1.6], [0.35, 1.8]], anchors: [[0.25, 1.7]] }] }
      : terminal),
  };
  const pack: LibrarySymbolPackage = { ...symbols, symbols: [multi] };
  const live = withAnchors(instance, multi);
  const resolvedSnapshot = { ...snapshot, instances: snapshot.instances.map((entry) => entry.name === live.name ? live : entry) };
  const result = resolveSymbol(live, [pack]);
  expect(result.symbol).toEqual(multi);
  expect(result.pinAnchors.get("D")).toEqual([[-4, 0.7], [-3.75, 0.7]]);
  const device = required(buildScene(resolvedSnapshot, [pack]).devices.find((entry) => entry.instance.name === "MN1"), "resolved device");
  expect(device.ports.filter((port) => port.name === "D").map((port) => port.position)).toEqual([[-4, 0.7], [-3.75, 0.7]]);

  const change = toggleFlip([], resolvedSnapshot, live.name);
  const flipped = flipPreview(resolvedSnapshot, change);
  const flippedInstance = required(flipped.instances.find((entry) => entry.name === live.name), "flipped NMOS");
  expect(resolveSymbol(flippedInstance, [pack]).symbol).toEqual(multi);
  const flippedPorts = required(buildScene(flipped, [pack]).devices.find((entry) => entry.instance.name === live.name), "flipped resolved device");
  expect(flippedPorts.ports.filter((port) => port.name === "D").map((port) => port.position)).toEqual([[-4, 0.7], [-4.25, 0.7]]);
  expect(flippedInstance.terminals.find((terminal) => terminal.name === "D")?.source_pin_anchors).toEqual([[-4, 0.7], [-4.25, 0.7]]);
});

it("falls back for missing or unmatched multi-figure source anchors and unsupported arcs", () => {
  const base = required(symbols.symbols.find((entry) => entry.cell === "nmos"), "NMOS symbol");
  const instance = required(snapshot.instances.find((item) => item.name === "MN1"), "NMOS instance");
  const multi: LibrarySymbol = {
    ...base,
    terminals: base.terminals.map((terminal) => terminal.name === "D"
      ? { ...terminal, pin_figures: [...terminal.pin_figures, { bbox: [[0.15, 1.6], [0.35, 1.8]], anchors: [[0.25, 1.7]] }] }
      : terminal),
  };
  const pack: LibrarySymbolPackage = { ...symbols, symbols: [multi] };
  expect(resolveSymbol(instance, [pack]).reason).toContain("source pin anchors unavailable");
  const mismatched = withAnchors(instance, multi);
  mismatched.terminals = mismatched.terminals.map((terminal) => terminal.name === "D"
    ? { ...terminal, source_pin_anchors: [[-4, 0.7], [-3.5, 0.7]] }
    : terminal);
  expect(resolveSymbol(mismatched, [pack]).reason).toContain("pin anchor mismatch");
  const arc = { ...base, complete: false, diagnostics: ["unsupported-shape:arc"] };
  expect(resolveSymbol(instance, [{ ...symbols, symbols: [arc] }]).symbol).toBeNull();
});

it("keeps dynamic-label warnings cosmetic and applies the OA tolerance", () => {
  const instance = required(snapshot.instances.find((item) => item.name === "MP1"), "PMOS instance");
  const base = required(symbols.symbols.find((entry) => entry.cell === "pmos"), "PMOS symbol");
  const warning = { ...base, complete: true, diagnostics: ["unsupported-dynamic-label"] };
  expect(resolveSymbol(instance, [{ ...symbols, symbols: [warning] }]).symbol).toEqual(warning);
  const shifted = (offset: number): LibrarySymbol => ({
    ...base,
    terminals: base.terminals.map((terminal) => ({
      ...terminal,
      pin_figures: terminal.pin_figures.map((figure) => ({
        ...figure,
        anchors: figure.anchors.map(([x, y]) => [x + offset, y]),
      })),
    })),
  });
  expect(resolveSymbol(instance, [{ ...symbols, symbols: [shifted(PIN_TOLERANCE / 2)] }]).symbol).not.toBeNull();
  expect(resolveSymbol(instance, [{ ...symbols, symbols: [shifted(PIN_TOLERANCE * 1.1)] }]).reason).toContain("pin anchor mismatch");
});

it("computes bounds from visible artwork without changing pin resolution", () => {
  const instance = required(snapshot.instances.find((item) => item.name === "MN1"), "NMOS instance");
  const symbol = required(symbols.symbols.find((entry) => entry.cell === "nmos"), "NMOS symbol");
  const points = symbolWorldPoints(symbol, instance);
  expect(Math.min(...points.map(([x]) => x))).toBe(-5.7);
  expect(Math.max(...points.map(([x]) => x))).toBe(-2.3);
  expect(Math.max(...points.map(([, y]) => y))).toBe(1);
  const withHiddenShapes: LibrarySymbol = {
    ...symbol,
    bbox: [[-99, -99], [99, 99]],
    primitives: [
      ...symbol.primitives,
      { type: "rect", layer: "instance", purpose: "custom", bbox: [[20, 20], [40, 40]] },
      { type: "rect", layer: "pin", purpose: "drawing", bbox: [[-99, -99], [-20, -20]] },
    ],
  };
  expect(symbolWorldPoints(withHiddenShapes, instance)).toEqual(points);
  const baseline = resolveSymbol(instance, [symbols]);
  const resolved = resolveSymbol(instance, [{ ...symbols, symbols: [withHiddenShapes] }]);
  expect(resolved.reason).toBe("resolved");
  expect(resolved.pinAnchors).toEqual(baseline.pinAnchors);
});
