import type { Point, Snapshot, Extent, Scene, Device, Port, Terminal, Net, LibrarySymbolPackage } from "./types";
import { resolveSymbol, symbolWorldPoints } from "./symbol-render";

export function countLabel(count: number, singular: string): string {
  return `${count} ${singular}${count === 1 ? "" : "s"}`;
}

export function bounds(coordinates: Point[]): Extent {
  const extent = { left: Infinity, right: -Infinity, top: -Infinity, bottom: Infinity };
  for (const [x, y] of coordinates) {
    extent.left = Math.min(extent.left, x);
    extent.right = Math.max(extent.right, x);
    extent.top = Math.max(extent.top, y);
    extent.bottom = Math.min(extent.bottom, y);
  }
  return extent;
}

export function buildScene(snapshot: Snapshot, symbolPackages: LibrarySymbolPackage[] = []): Scene {
  const devices: Device[] = [];
  const extentPoints = [
    ...snapshot.wires.flatMap((wire) => wire.points),
    ...snapshot.pins.map((pin) => pin.xy).filter((xy): xy is Point => xy !== null),
  ];
  const widths = snapshot.instances
    .filter((instance) => instance.bbox.length === 2)
    .map((instance) => instance.bbox[1][0] - instance.bbox[0][0])
    .filter((width) => width > 0).sort((a, b) => a - b);
  const unit = widths[Math.floor(widths.length / 2)] ?? 1;
  let fallbackCount = 0;
  for (const instance of snapshot.instances) {
    const resolved = resolveSymbol(instance, symbolPackages);
    const measured = instance.terminals.map((terminal) => terminal.xy).filter((xy): xy is Point => xy !== null);
    const area = bounds(measured.length ? measured : instance.bbox);
    const center = instance.bbox.length === 2 ? bounds(instance.bbox) : area;
    const hasBounds = Number.isFinite(area.left);
    const [x, y] = hasBounds
      ? [(center.left + center.right) / 2, (center.top + center.bottom) / 2]
      : (instance.xy ?? [fallbackCount++ * unit * 2, 0]);
    const nativeWidth = hasBounds ? area.right - area.left : 0;
    const nativeHeight = hasBounds ? area.top - area.bottom : 0;
    const halfWidth = Math.max(unit * 0.26, nativeWidth * 0.40);
    const halfHeight = Math.max(unit * 0.14, nativeHeight * 0.40, instance.terminals.length * unit * 0.035);
    const groups: Record<string, Terminal[]> = { left: [], right: [], top: [], bottom: [] };
    for (const terminal of instance.terminals) {
      let side = "right";
      if (terminal.xy) {
        const dx = terminal.xy[0] - x;
        const dy = terminal.xy[1] - y;
        if (Math.abs(dx) / halfWidth > Math.abs(dy) / halfHeight) {
          side = dx < 0 ? "left" : "right";
        } else {
          side = dy < 0 ? "bottom" : "top";
        }
      }
      groups[side].push(terminal);
    }
    const ports: Port[] = [];
    if (resolved.symbol) {
      for (const terminal of instance.terminals) {
        const anchors = resolved.pinAnchors.get(terminal.name) ?? [];
        anchors.forEach((position, anchorIndex) => ports.push({ ...terminal, instance: instance.name, side: "symbol", position, anchorIndex }));
      }
    } else {
      for (const [side, terminals] of Object.entries(groups)) {
        const vertical = side === "left" || side === "right";
        const axis = vertical ? 1 : 0;
        terminals.sort((a, b) => (a.xy?.[axis] ?? 0) - (b.xy?.[axis] ?? 0));
        terminals.forEach((terminal, index) => {
          const offset = ((2 * index + 1) / terminals.length - 1) * 0.88;
          const position: Point = vertical
            ? [x + halfWidth * (side === "left" ? -1 : 1), y + halfHeight * offset]
            : [x + halfWidth * offset, y + halfHeight * (side === "top" ? 1 : -1)];
          ports.push({ ...terminal, instance: instance.name, side, position, anchorIndex: 0 });
        });
      }
    }
    const symbolPoints = resolved.symbol ? symbolWorldPoints(resolved.symbol, instance) : [];
    const fallbackBounds = { left: x - halfWidth, right: x + halfWidth, top: y + halfHeight, bottom: y - halfHeight };
    const hitBounds = symbolPoints.length ? bounds(symbolPoints) : fallbackBounds;
    devices.push({ instance, x, y, halfWidth, halfHeight, ports, hitBounds });
    extentPoints.push([hitBounds.left, hitBounds.bottom], [hitBounds.right, hitBounds.top]);
  }
  return { devices, area: bounds(extentPoints.length ? extentPoints : [[-1, -1], [1, 1]]) };
}

export function collectNets(snapshot: Snapshot): Map<string, Net> {
  const nets = new Map<string, Net>();
  function add(name: string | null, group: "terminals" | "pins" | "segments", value: string | null): void {
    if (!name) return;
    if (!nets.has(name)) nets.set(name, { terminals: [], pins: [], segments: 0 });
    const entry = nets.get(name)!;
    if (group === "segments") entry.segments += 1;
    else entry[group].push(value!);
  }
  for (const instance of snapshot.instances) {
    for (const terminal of instance.terminals) add(terminal.net, "terminals", `${instance.name}.${terminal.name}`);
  }
  for (const pin of snapshot.pins) add(pin.net, "pins", pin.name);
  for (const wire of snapshot.wires) add(wire.net, "segments", null);
  return new Map([...nets].sort(([a], [b]) => a.localeCompare(b, undefined, { numeric: true })));
}

export function segmentDistance(x: number, y: number, [x1, y1]: Point, [x2, y2]: Point): number {
  const dx = x2 - x1;
  const dy = y2 - y1;
  const ratio = Math.max(0, Math.min(1, ((x - x1) * dx + (y - y1) * dy) / ((dx * dx + dy * dy) || 1)));
  return Math.hypot(x - x1 - ratio * dx, y - y1 - ratio * dy);
}
