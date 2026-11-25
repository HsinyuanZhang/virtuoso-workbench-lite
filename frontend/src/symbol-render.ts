import { orientations } from "./orientation";
import type { Instance, LibrarySymbol, LibrarySymbolPackage, Point, SymbolPrimitive } from "./types";

export const PIN_TOLERANCE = 1e-6;

export interface SymbolResolution {
  symbol: LibrarySymbol | null;
  reason: string;
  pinAnchors: Map<string, Point[]>;
}

function matrixFor(instance: Instance) {
  return Object.hasOwn(orientations, instance.orientation) ? orientations[instance.orientation] : undefined;
}

export function transformSymbolPoint(point: Point, symbol: LibrarySymbol, instance: Instance): Point {
  const matrix = matrixFor(instance);
  if (!matrix || !instance.xy) throw new Error("Symbol transform needs a known orientation and instance origin.");
  const x = point[0] - symbol.origin[0];
  const y = point[1] - symbol.origin[1];
  return [instance.xy[0] + matrix[0][0] * x + matrix[0][1] * y, instance.xy[1] + matrix[1][0] * x + matrix[1][1] * y];
}

function fallback(reason: string): SymbolResolution {
  return { symbol: null, reason, pinAnchors: new Map() };
}

function close(left: Point, right: Point): boolean {
  return Math.hypot(left[0] - right[0], left[1] - right[1]) <= PIN_TOLERANCE;
}

function metadataReason(status: string): string {
  const reasons: Record<string, string> = {
    "metadata-binding-mismatch": "live master binding changed",
    "metadata-binding-unavailable": "live master binding unavailable",
    "metadata-invalid-magnification": "instance magnification unavailable",
    "unsupported-magnification": "instance magnification is unsupported",
    "metadata-terminal-mismatch": "live terminal metadata changed",
    "metadata-ambiguous": "live master metadata is ambiguous",
    "metadata-missing": "live master metadata unavailable",
    "metadata-invalid": "live master metadata invalid",
    "metadata-unavailable": "live master metadata unavailable",
    "unsupported-parameterized-variant": "parameterized master variant is unsupported",
    "metadata-invalid-variant-status": "live master variant metadata invalid",
  };
  return Object.hasOwn(reasons, status) ? reasons[status] : "live master metadata invalid";
}

export function resolveSymbol(instance: Instance, packages: LibrarySymbolPackage[]): SymbolResolution {
  if (instance.symbol_metadata_error) return fallback(metadataReason(instance.symbol_metadata_error));
  if (!matrixFor(instance)) return fallback("unsupported orientation");
  if (!instance.library_identity) return fallback("library identity unavailable");
  if (!instance.master_view) return fallback("master view unavailable");
  const library = packages.find((entry) => entry.library.name === instance.library && entry.library.identity === instance.library_identity);
  if (!library) return fallback("library package not bound");
  const symbol = library.symbols.find((entry) => entry.cell === instance.master && entry.view === instance.master_view);
  if (!symbol) return fallback("master symbol not packaged");
  if (!symbol.complete) return fallback(symbol.diagnostics.join(", ") || "symbol incomplete");
  if (!instance.xy) return fallback("instance origin unavailable");
  const packageNames = new Set(symbol.terminals.map((terminal) => terminal.name));
  if (packageNames.size !== instance.terminals.length || instance.terminals.some((terminal) => !packageNames.has(terminal.name))) {
    return fallback("terminal set mismatch");
  }
  const pinAnchors = new Map<string, Point[]>();
  for (const terminal of instance.terminals) {
    const packaged = symbol.terminals.find((entry) => entry.name === terminal.name);
    if (!packaged) return fallback(`pin geometry unavailable: ${terminal.name}`);
    const expected = packaged.pin_figures.flatMap((figure) => figure.anchors.map((anchor) => transformSymbolPoint(anchor, symbol, instance)));
    if (!expected.length) return fallback(`pin geometry unavailable: ${terminal.name}`);
    if (terminal.source_pin_anchors !== undefined) {
      const actual = terminal.source_pin_anchors;
      if (actual.length !== expected.length || actual.some((anchor) => !anchor.every(Number.isFinite))) {
        return fallback(`source pin anchor mismatch: ${terminal.name}`);
      }
      const used = new Set<number>();
      for (const anchor of expected) {
        const match = actual.findIndex((candidate, index) => !used.has(index) && close(anchor, candidate));
        if (match < 0) return fallback(`pin anchor mismatch: ${terminal.name}`);
        used.add(match);
      }
      const snapshotAnchor = terminal.xy;
      if (snapshotAnchor && !actual.some((anchor) => close(snapshotAnchor, anchor))) {
        return fallback(`snapshot pin anchor mismatch: ${terminal.name}`);
      }
      pinAnchors.set(terminal.name, actual.map(([x, y]) => [x, y]));
    } else {
      if (expected.length !== 1) return fallback(`source pin anchors unavailable: ${terminal.name}`);
      if (!terminal.xy || !close(expected[0], terminal.xy)) return fallback(`pin anchor mismatch: ${terminal.name}`);
      pinAnchors.set(terminal.name, [[terminal.xy[0], terminal.xy[1]]]);
    }
  }
  return { symbol, reason: "resolved", pinAnchors };
}

function boxCorners(box: Point[]): Point[] {
  const [[left, bottom], [right, top]] = box;
  return [[left, bottom], [left, top], [right, bottom], [right, top]];
}

function primitivePoints(primitive: SymbolPrimitive): Point[] {
  switch (primitive.type) {
    case "line":
    case "polygon":
      return primitive.points;
    case "rect":
    case "ellipse":
      return boxCorners(primitive.bbox);
    case "label":
    case "instance-name":
      return [primitive.xy];
    case "unsupported":
      return [];
  }
}

export function visibleSymbolPrimitives(symbol: LibrarySymbol): SymbolPrimitive[] {
  return symbol.primitives.filter((primitive) => primitive.layer !== "instance" && primitive.layer !== "pin");
}

export function symbolWorldPoints(symbol: LibrarySymbol, instance: Instance): Point[] {
  const primitives = visibleSymbolPrimitives(symbol);
  const local = primitives.flatMap(primitivePoints);
  if (!primitives.some((primitive) => primitive.type === "instance-name")) local.push(symbol.origin);
  return local.map((point) => transformSymbolPoint(point, symbol, instance));
}
