export type Point = [number, number];
export interface Terminal { name: string; net: string | null; xy: Point | null }
export interface Instance {
  name: string; library: string; master: string; orientation: string;
  xy: Point | null; bbox: Point[]; terminals: Terminal[];
}
export interface Pin extends Terminal { direction: string }
export interface Wire { net: string; points: Point[] }
export interface Snapshot {
  source: string; library: string; cell: string; view: string; revision: string;
  instances: Instance[]; pins: Pin[]; wires: Wire[];
}
export interface Port extends Terminal {
  instance: string; side: string; position: Point;
}
export interface Device {
  instance: Instance; x: number; y: number; halfWidth: number; halfHeight: number; ports: Port[];
}
export interface Extent { left: number; right: number; top: number; bottom: number }
export interface Scene { devices: Device[]; area: Extent }
export interface Net { terminals: string[]; pins: string[]; segments: number }
export type Hit =
  | { kind: "terminal"; port: Port; x: number; y: number }
  | { kind: "pin"; pin: Pin; x: number; y: number }
  | { kind: "wire"; wire: Wire; path: Point[] }
  | { kind: "device"; instance: string; left: number; right: number; top: number; bottom: number };
export interface State {
  snapshot: Snapshot | null; scene: Scene | null; nets: Map<string, Net>;
  instance: string | null; net: string | null; endpoints: Port[];
  view: { x: number; y: number; scale: number }; hits: Hit[];
  drag: { x: number; y: number; viewX: number; viewY: number; moved: boolean } | null;
  request: number;
}
