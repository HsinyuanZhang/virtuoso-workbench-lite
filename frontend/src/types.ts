export type Point = [number, number];
export interface Terminal { name: string; net: string | null; xy: Point | null; source_pin_anchors?: Point[] }
export interface Instance {
  name: string; library: string; master: string; orientation: string;
  xy: Point | null; bbox: Point[]; terminals: Terminal[];
  master_view?: string; library_identity?: string; symbol_metadata_error?: string;
}
export interface Pin extends Terminal { direction: string }
export interface Wire { net: string; points: Point[] }
export interface Snapshot {
  source: string; library: string; cell: string; view: string; revision: string;
  instances: Instance[]; pins: Pin[]; wires: Wire[];
}
export type SymbolPrimitive =
  | { type: "line" | "polygon"; points: Point[]; layer: string; purpose: string }
  | { type: "rect" | "ellipse"; bbox: Point[]; layer: string; purpose: string }
  | { type: "arc"; bbox: Point[]; start_angle: number; stop_angle: number; layer: string; purpose: string }
  | { type: "label"; text: string; xy: Point; layer: string; purpose: string }
  | { type: "instance-name"; xy: Point; layer: string; purpose: string }
  | { type: "unsupported"; source_type: string; layer?: string; purpose?: string };
export interface SymbolTerminal { name: string; direction: string; pin_figures: { bbox: Point[]; anchors: Point[] }[] }
export interface LibrarySymbol {
  cell: string; view: string; origin: Point; bbox: Point[]; primitives: SymbolPrimitive[];
  terminals: SymbolTerminal[]; complete: boolean; diagnostics: string[];
}
export interface LibrarySymbolPackage {
  schema: string; library: { name: string; identity: string }; digest: string;
  coordinate_system: { axes: string; units: string; scale?: number; grid?: number }; symbols: LibrarySymbol[];
}
export interface Port extends Terminal {
  instance: string; side: string; position: Point; anchorIndex: number;
}
export interface Device {
  instance: Instance; x: number; y: number; halfWidth: number; halfHeight: number; ports: Port[]; hitBounds: Extent;
}
export interface Extent { left: number; right: number; top: number; bottom: number }
export interface Scene { devices: Device[]; area: Extent }
export interface Net { terminals: string[]; pins: string[]; segments: number }
export type Hit =
  | { kind: "terminal"; port: Port; x: number; y: number }
  | { kind: "pin"; pin: Pin; x: number; y: number }
  | { kind: "wire"; wire: Wire; path: Point[] }
  | { kind: "device"; instance: string; left: number; right: number; top: number; bottom: number; symbolResolved: boolean };
export interface State {
  snapshot: Snapshot | null; scene: Scene | null; nets: Map<string, Net>;
  instance: string | null; net: string | null; endpoints: Port[];
  view: { x: number; y: number; scale: number }; hits: Hit[];
  drag: { x: number; y: number; viewX: number; viewY: number; moved: boolean } | null;
  request: number;
}
