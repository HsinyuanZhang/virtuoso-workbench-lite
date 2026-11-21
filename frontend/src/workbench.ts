import "./workbench.css";
import { buildScene, collectNets, countLabel, segmentDistance } from "./logic";
import type { Point, Port, Snapshot, State, Hit } from "./types";
import { palette } from "./palette";
import { appendChange, buildDraft, parseRequest, parseResponse, sameTarget, setParameters } from "./requests";
import type { DraftChange } from "./requests";
import { RequestPanel, requestApi } from "./request-panel";
import { parseFindings, previewChecks } from "./checks";
import type { Finding } from "./checks";
import { flipPreview, toggleFlip } from "./orientation";

interface Elements {
  "flip-horizontal": HTMLButtonElement;
  schematic: HTMLCanvasElement;
  "net-search": HTMLInputElement;
  library: HTMLInputElement;
  cell: HTMLInputElement;
  instances: HTMLSelectElement;
  note: HTMLTextAreaElement;
  title: HTMLInputElement;
  simulation: HTMLTextAreaElement;
  acceptance: HTMLTextAreaElement;
  "open-form": HTMLFormElement;
  save: HTMLButtonElement;
  clear: HTMLButtonElement;
  "add-connect": HTMLButtonElement;
  "submit-draft": HTMLButtonElement;
  "open-cell": HTMLButtonElement;
  "open-demo": HTMLButtonElement;
}
function element<K extends keyof Elements>(id: K): Elements[K];
function element(id: string): HTMLElement;
function element(id: string): HTMLElement {
  return document.getElementById(id)!;
}
const canvas = element("schematic");
const context = canvas.getContext("2d")!;
const state: State = {
  snapshot: null,
  scene: null,
  nets: new Map(),
  instance: null,
  net: null,
  endpoints: [],
  view: { x: 0, y: 0, scale: 1 },
  hits: [],
  drag: null,
  request: 0,
};
const colors = { wire: palette.blue, active: palette.white, dim: palette.blue, selected: palette.white, text: palette.white, muted: palette.white };
let draft: DraftChange[] = [];
let saving = false;
const requests = new RequestPanel(() => draw(), message);
let snapshotFindings: Finding[] = [];
let findings: Finding[] = [];
let activeFinding: Finding | null = null;
let lastCheckKey = "";
const preview = previewChecks(async (payload: unknown, signal) => {
  const response = await fetch("/api/check", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload), signal });
  return parseFindings(parseResponse(response.status, await response.json()).checks);
}, (results) => {
  findings = results;
  renderFindings("Snapshot + draft preview");
}, (error) => {
  renderFindings(`Checks unavailable: ${error}`, true);
});
window.addEventListener("pagehide", () => preview.cancel());

function renderFindings(label: string, error = false): void {
  element("checks-status").textContent = `${label}${error ? "" : ` · ${findings.length} finding${findings.length === 1 ? "" : "s"}`}`;
  element("checks-status").classList.toggle("error", error);
  element("check-findings").replaceChildren(...findings.map((finding) => {
    const entry = item("li", ""), button = item("button", `${finding.severity} · ${finding.code}\n${finding.message}`, finding.severity);
    button.type = "button";
    button.setAttribute("aria-pressed", String(activeFinding === finding));
    button.addEventListener("click", () => {
      activeFinding = finding;
      state.net = finding.net ?? null;
      if (finding.instance) state.instance = finding.instance;
      renderInspector();
      renderNets();
      renderTopology();
      renderFindings(draft.length ? "Snapshot + draft preview" : "Current snapshot");
      draw();
    });
    entry.append(button);
    return entry;
  }));
}

function refreshChecks(): void {
  if (!state.snapshot) return;
  const { source, library, cell, view, revision } = state.snapshot;
  const payload = { target: { source, library, cell, view }, revision, changes: draft };
  const key = JSON.stringify(payload);
  if (key === lastCheckKey) return;
  lastCheckKey = key;
  activeFinding = null;
  if (!draft.length) {
    preview.cancel();
    findings = snapshotFindings;
    renderFindings("Current snapshot");
  } else {
    findings = [];
    renderFindings("Checking snapshot + draft…");
    preview.schedule(payload);
  }
  draw();
}

function item<K extends keyof HTMLElementTagNameMap>(tag: K, text: string, className?: string): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  node.textContent = text;
  if (className) node.className = className;
  return node;
}

function listButton(name: string, detail: string, active: boolean, click: () => void): HTMLLIElement {
  const entry = item("li", "");
  const button = item("button", "");
  button.type = "button";
  button.setAttribute("aria-pressed", String(active));
  button.append(item("span", name), item("span", detail));
  button.addEventListener("click", click);
  entry.append(button);
  return entry;
}

function message(text: string, error = false): void {
  const status = element("status");
  status.hidden = !text;
  status.textContent = text;
  status.classList.toggle("error", error);
}

async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(path, options);
  return parseResponse(response.status, await response.json()) as T;
}

function fit() {
  if (!state.scene) return;
  const { left, right, top, bottom } = state.scene.area;
  const width = canvas.clientWidth;
  const height = canvas.clientHeight;
  const scale = Math.max(0.01, Math.min(
    Math.max(1, width - 108) / Math.max(right - left, 0.1),
    Math.max(1, height - 90) / Math.max(top - bottom, 0.1),
  ));
  state.view = { x: width / 2 - (left + right) * scale / 2, y: height / 2 + (top + bottom) * scale / 2, scale };
  draw();
}

function screen([x, y]: Point): Point {
  return [state.view.x + x * state.view.scale, state.view.y - y * state.view.scale];
}

function zoom(multiplier: number, x = canvas.clientWidth / 2, y = canvas.clientHeight / 2) {
  if (!state.scene) return;
  const before = state.view.scale;
  const after = Math.max(0.001, Math.min(100000, before * multiplier));
  state.view = { x: x - (x - state.view.x) * after / before, y: y - (y - state.view.y) * after / before, scale: after };
  draw();
}

function line(path: Point[], color: string, width = 1, dash: number[] = []) {
  context.strokeStyle = color;
  context.lineWidth = width;
  context.setLineDash(dash);
  context.beginPath();
  path.forEach(([x, y], index) => index ? context.lineTo(x, y) : context.moveTo(x, y));
  context.stroke();
  context.setLineDash([]);
}

function circle(x: number, y: number, radius: number, fill: string, stroke: string | null = null) {
  context.beginPath();
  context.arc(x, y, radius, 0, 2 * Math.PI);
  context.fillStyle = fill;
  context.fill();
  if (stroke) {
    context.lineWidth = 1.3;
    context.strokeStyle = stroke;
    context.stroke();
  }
}

function text(textValue: string, x: number, y: number, color: string, align: CanvasTextAlign = "left", pixels = 11, background = false) {
  context.font = `${pixels}px monospace`;
  context.textAlign = align;
  context.textBaseline = "middle";
  if (background) {
    const width = context.measureText(textValue).width;
    const left = align === "center" ? x - width / 2 : align === "right" ? x - width : x;
    context.fillStyle = palette.black;
    context.fillRect(left - 1, y - pixels / 2, width + 2, pixels);
  }
  context.fillStyle = color;
  context.fillText(textValue, x, y);
}

function isEndpoint(port: Port) {
  return state.endpoints.some((end) => end.instance === port.instance && end.name === port.name);
}

function requestChanges() {
  return requests.selected && sameTarget(requests.selected.target, state.snapshot) ? requests.selected.changes : [];
}

function highlightedPort(port: Port): boolean {
  return isEndpoint(port) || (activeFinding?.instance === port.instance && activeFinding.terminal === port.name) || requestChanges().some((change) => change.op === "connect" && change.endpoints.some((end) => end.instance === port.instance && end.terminal === port.name));
}

function draw() {
  const width = canvas.clientWidth;
  const height = canvas.clientHeight;
  const ratio = window.devicePixelRatio || 1;
  if (canvas.width !== Math.round(width * ratio) || canvas.height !== Math.round(height * ratio)) {
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
  }
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  context.clearRect(0, 0, width, height);
  context.fillStyle = palette.white;
  context.globalAlpha = 0.12;
  const step = 19;
  for (let x = (state.view.x % step + step) % step; x < width; x += step) {
    for (let y = (state.view.y % step + step) % step; y < height; y += step) context.fillRect(x, y, 1, 1);
  }
  context.globalAlpha = 1;
  if (!state.scene || !state.snapshot) return;
  state.hits = [];
  const wireHits: Hit[] = [];
  for (const wire of state.snapshot.wires) {
    const path = wire.points.map(screen);
    const highlighted = wire.net === state.net;
    line(path, highlighted ? colors.active : state.net ? colors.dim : colors.wire, highlighted ? 2.4 : 1.45);
    wireHits.push({ kind: "wire", wire, path });
  }
  for (const device of state.scene.devices) {
    const { instance, x, y, halfWidth, halfHeight, ports } = device;
    const [left, top] = screen([x - halfWidth, y + halfHeight]);
    const [right, bottom] = screen([x + halfWidth, y - halfHeight]);
    const chosen = state.instance === instance.name || activeFinding?.instance === instance.name || requestChanges().some((change) => (change.op === "set-parameters" || change.op === "flip-horizontal") && change.instance === instance.name);
    const connected = Boolean(state.net && ports.some((port) => port.net === state.net));
    context.fillStyle = palette.black;
    context.strokeStyle = chosen || connected ? palette.white : palette.green;
    context.lineWidth = 1.25;
    context.fillRect(left, top, right - left, bottom - top);
    context.strokeRect(left, top, right - left, bottom - top);
    context.font = "11px monospace";
    const displayWidth = context.measureText(instance.name).width + 4;
    if (right - left > displayWidth && bottom - top > 15) {
      text(instance.name, (left + right) / 2, (top + bottom) / 2, colors.text, "center", 11);
    } else {
      text(instance.name, left + 2, top - 9, colors.text, "left", 10, true);
    }
    state.hits.push({ kind: "device", instance: instance.name, left, right, top, bottom });
  }
  const renderedPorts = [];
  for (const device of state.scene.devices) {
    for (const port of device.ports) {
      const [x, y] = screen(port.position);
      const selected = highlightedPort(port);
      const active = Boolean(state.net && state.net === port.net);
      const color = selected || active ? palette.white : palette.red;
      if (port.xy) line([screen(port.xy), [x, y]], color, 1.15, [2, 2]);
      renderedPorts.push({ port, x, y, color, selected, active, device });
    }
  }
  for (const pin of state.snapshot.pins) {
    if (!pin.xy) continue;
    const [x, y] = screen(pin.xy);
    const color = (state.net && pin.net === state.net) || activeFinding?.pin === pin.name ? palette.white : palette.red;
    context.fillStyle = color;
    context.fillRect(x - 3, y - 3, 6, 6);
    text(pin.name, x + 6, y - 9, palette.white, "left", 11, true);
    if (pin.net) state.hits.push({ kind: "pin", pin, x, y });
  }
  for (const { port, x, y, color, selected, active, device } of renderedPorts) {
    const radius = selected ? 5.5 : 3.4;
    circle(x, y, radius, selected ? color : palette.black, color);
    const current = selected || active || device.instance.name === state.instance;
    if (current || device.halfHeight * state.view.scale > 12) {
      const deltaX = port.side === "left" ? -8 : port.side === "right" ? 8 : 0;
      const deltaY = port.side === "top" ? -9 : port.side === "bottom" ? 9 : 0;
      const align = deltaX < 0 ? "right" : deltaX > 0 ? "left" : "center";
      text(port.name, x + deltaX, y + deltaY, current ? colors.text : colors.muted, align, 10, true);
    }
    state.hits.push({ kind: "terminal", port, x, y });
  }
  if (state.endpoints.length === 2) {
    const [start, end] = state.endpoints.map((port) => screen(port.position));
    const midX = (start[0] + end[0]) / 2;
    line([start, [midX, start[1]], [midX, end[1]], end], colors.selected, 2, [6, 4]);
  }
  state.hits.push(...wireHits);
  for (const point of activeFinding?.points ?? []) {
    const [x, y] = screen(point);
    circle(x, y, 9, palette.black, palette.white);
  }
}

function renderNets() {
  const query = element("net-search").value.trim().toLowerCase();
  const entries = [...state.nets].filter(([name]) => name.toLowerCase().includes(query));
  element("net-list").replaceChildren(...entries.map(([name, net]) => listButton(
    name, String(net.terminals.length + net.pins.length), state.net === name,
    () => selectNet(state.net === name ? null : name),
  )));
  element("net-count").textContent = String(state.nets.size);
}

function renderInspector() {
  if (!state.scene) return;
  const device = state.scene.devices.find((entry) => entry.instance.name === state.instance);
  const terminalList = element("terminal-list");
  element("master").textContent = device ? `${device.instance.library}/${device.instance.master} · ${device.instance.orientation}` : "";
  if (device) element("instances").value = device.instance.name;
  terminalList.replaceChildren(...(device?.ports ?? []).map((port) => listButton(
    port.name, port.net ?? "Open", isEndpoint(port), () => chooseTerminal(port),
  )));
  renderParameters();
}

function renderTopology() {
  if (!state.snapshot) return;
  const net = state.nets.get(state.net ?? "");
  const details = element("net-details");
  details.replaceChildren();
  element("topology-overview").textContent = state.net && net
    ? `${state.net} · ${countLabel(net.terminals.length, "instance terminal")} · ${countLabel(net.segments, "wire segment")}`
    : "Select a net to inspect its snapshot membership.";
  if (net) {
    if (net.terminals.length) details.append(item("p", net.terminals.join(" · ")));
    if (net.pins.length) details.append(item("p", `I/O: ${net.pins.join(", ")}`));
  }
}

function renderDraft() {
  if (state.snapshot) {
    state.scene = buildScene(flipPreview(state.snapshot, draft));
    state.endpoints = state.endpoints.flatMap((endpoint) => state.scene?.devices.find((device) => device.instance.name === endpoint.instance)?.ports.filter((port) => port.name === endpoint.name) ?? []);
    renderInspector();
  }
  element("flip-horizontal").disabled = !state.instance || saving;
  const endpoints = state.endpoints;
  element("wire-status").textContent = endpoints.length === 0 ? "Select two instance terminals to draft a wire."
    : endpoints.length === 1 ? "Select the other endpoint." : "Review this proposed connection.";
  element("endpoints").replaceChildren(...endpoints.map((port) => item("li", `${port.instance}.${port.name} · ${port.net ?? "Open"}`)));
  const effect = element("wire-effect");
  effect.textContent = "";
  effect.classList.remove("warning");
  if (endpoints.length === 2) {
    const [left, right] = endpoints.map((port) => port.net);
    if (left && right && left !== right) {
      effect.textContent = `Merges distinct nets ${left} and ${right}. Review both nets before routing.`;
      effect.classList.add("warning");
    } else if (left && left === right) {
      effect.textContent = `Both endpoints already share ${left}.`;
    } else {
      effect.textContent = left || right ? `Attaches the open terminal to ${left || right}.` : "Creates a new net.";
    }
  }
  element("add-connect").disabled = endpoints.length !== 2 || saving;
  element("save").disabled = !draft.length || saving;
  element("submit-draft").disabled = !draft.length || saving;
  for (const field of ["title", "simulation", "acceptance", "note"] as const) element(field).disabled = saving;
  element("clear").disabled = endpoints.length === 0;
  element("draft-changes").replaceChildren(...draft.map((change, index) => {
    const entry = item("li", change.op === "flip-horizontal" ? `${change.instance}: ${change.from_orientation} → ${change.to_orientation}` : JSON.stringify(change));
    const remove = item("button", "Remove");
    remove.type = "button";
    remove.disabled = saving;
    remove.setAttribute("aria-label", `Remove change ${index + 1}`);
    remove.addEventListener("click", () => { draft = draft.filter((_, position) => position !== index); renderDraft(); });
    entry.append(remove);
    return entry;
  }));
  refreshChecks();
  draw();
}

element("flip-horizontal").addEventListener("click", () => {
  if (!state.snapshot || !state.instance || saving) return;
  try {
    draft = toggleFlip(draft, state.snapshot, state.instance);
    renderDraft();
  } catch (error) { message(error instanceof Error ? error.message : String(error), true); }
});

function selectNet(name: string | null) {
  state.net = name;
  renderNets();
  renderTopology();
  draw();
}

function chooseTerminal(port: Port) {
  if (isEndpoint(port)) state.endpoints = state.endpoints.filter((end) => end.instance !== port.instance || end.name !== port.name);
  else if (state.endpoints.length === 2) state.endpoints = [port];
  else state.endpoints.push(port);
  state.instance = port.instance;
  renderInspector();
  renderDraft();
  draw();
  message("");
}

async function open(path: string) {
  const request = ++state.request;
  element("open-cell").disabled = true;
  element("open-demo").disabled = true;
  message("Opening schematic…");
  try {
    const snapshot = await api<Snapshot & { checks: unknown }>(path);
    if (request !== state.request) return;
    snapshotFindings = parseFindings(snapshot.checks);
    lastCheckKey = "";
    state.snapshot = snapshot;
    state.scene = buildScene(snapshot);
    state.nets = collectNets(snapshot);
    state.instance = snapshot.instances[0]?.name ?? null;
    state.net = null;
    state.endpoints = [];
    draft = [];
    for (const field of ["title", "simulation", "acceptance", "note"] as const) element(field).value = "";
    element("net-search").value = "";
    element("cell-title").textContent = snapshot.cell;
    element("cell-path").textContent = `${snapshot.library} / ${snapshot.view}`;
    element("source").replaceChildren(document.createElement("i"), document.createTextNode(
      snapshot.source === "demo" ? "Demo" : "Virtuoso · Read only",
    ));
    element("statistics").textContent = [
      countLabel(snapshot.instances.length, "instance"),
      countLabel(snapshot.pins.length, "I/O pin"),
      countLabel(snapshot.wires.length, "wire segment"),
    ].join(" · ");
    element("instances").replaceChildren(...snapshot.instances.map((instance) => item("option", instance.name)));
    element("instances").disabled = !snapshot.instances.length;
    renderNets();
    renderInspector();
    renderTopology();
    renderDraft();
    fit();
    message("");
  } catch (error) {
    if (request === state.request) message(error instanceof Error ? error.message : String(error), true);
  } finally {
    if (request === state.request) {
      element("open-cell").disabled = false;
      element("open-demo").disabled = false;
    }
  }
}

element("open-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const fields = new FormData(element("open-form"));
  open(`/api/schematic?${new URLSearchParams([...fields].map(([key, value]) => [key, String(value)]))}`);
});
element("open-demo").addEventListener("click", () => open("/api/demo"));
element("net-search").addEventListener("input", renderNets);
element("instances").addEventListener("change", () => {
  state.instance = element("instances").value;
  renderInspector();
  draw();
});
element("clear").addEventListener("click", () => {
  state.endpoints = [];
  renderInspector();
  renderDraft();
  draw();
});
element("add-connect").addEventListener("click", () => {
  if (state.endpoints.length !== 2 || saving) return;
  try {
    const [left, right] = state.endpoints.map((port) => ({ instance: port.instance, terminal: port.name }));
    draft = appendChange(draft, { op: "connect", endpoints: [left, right] });
    state.endpoints = [];
    renderInspector();
    renderDraft();
    draw();
    message("Connection added to the local draft.");
  } catch (error) { message(error instanceof Error ? error.message : String(error), true); }
});

function parameterRow(name = "", value = ""): void {
  const row = item("div", "", "parameter-row");
  const key = item("input", ""), input = item("input", ""), remove = item("button", "Remove");
  key.setAttribute("aria-label", "Parameter name");
  key.maxLength = 128;
  key.value = name;
  input.setAttribute("aria-label", "Parameter value");
  input.maxLength = 64;
  input.value = value;
  remove.type = "button";
  remove.setAttribute("aria-label", "Remove parameter row");
  remove.addEventListener("click", () => row.remove());
  row.append(key, input, remove);
  element("parameter-rows").append(row);
}

let parameterInstance: string | null | undefined;
function renderParameters(force = false): void {
  if (!force && parameterInstance === state.instance) return;
  parameterInstance = state.instance;
  element("parameter-rows").replaceChildren();
  const change = draft.find((entry) => entry.op === "set-parameters" && entry.instance === state.instance);
  if (change?.op === "set-parameters") Object.entries(change.parameters).forEach(([key, value]) => parameterRow(key, value));
  else parameterRow();
}
element("parameter-row").addEventListener("click", () => parameterRow());
element("parameter-apply").addEventListener("click", () => {
  if (!state.instance || saving) return;
  try {
    const rows = [...element("parameter-rows").querySelectorAll(".parameter-row")].map((row): [string, string] => {
      const inputs = row.querySelectorAll("input");
      return [inputs[0].value, inputs[1].value];
    });
    draft = setParameters(draft, state.instance, rows);
    renderParameters(true);
    renderDraft();
    message(`Parameters added for ${state.instance}.`);
  } catch (error) { message(error instanceof Error ? error.message : String(error), true); }
});

async function saveDraft(submit: boolean): Promise<void> {
  if (!state.snapshot || saving) return;
  const generation = state.request;
  try {
    const payload = buildDraft(state.snapshot, draft, {
      title: element("title").value, simulation: element("simulation").value,
      acceptance: element("acceptance").value, note: element("note").value,
    }, submit);
    saving = true;
    renderDraft();
    const request = parseRequest((await requestApi("/api/requests", payload)).request);
    if (generation === state.request) {
      draft = [];
      for (const field of ["title", "simulation", "acceptance", "note"] as const) element(field).value = "";
      renderParameters();
    }
    await requests.select(request.id);
    await requests.refresh();
    message(submit ? "Request submitted. An agent can claim it." : "Draft saved on the server.");
  } catch (error) { message(error instanceof Error ? error.message : String(error), true); }
  finally { saving = false; renderDraft(); }
}
element("save").addEventListener("click", () => { void saveDraft(false); });
element("submit-draft").addEventListener("click", () => { void saveDraft(true); });

function hitTest(x: number, y: number): Hit | undefined {
  let nearest: Hit | undefined;
  let distance = 12;
  for (const hit of state.hits) {
    if (hit.kind !== "terminal" && hit.kind !== "pin") continue;
    const candidate = Math.hypot(x - hit.x, y - hit.y);
    if (candidate < distance) {
      nearest = hit;
      distance = candidate;
    }
  }
  if (nearest) return nearest;
  return state.hits.find((hit) => {
    if (hit.kind === "device") return x >= hit.left && x <= hit.right && y >= hit.top && y <= hit.bottom;
    if (hit.kind === "wire") return hit.path.some((point, index) => index && segmentDistance(x, y, hit.path[index - 1], point) < 5);
    return false;
  });
}

canvas.addEventListener("pointerdown", (event) => {
  if (event.button !== 0) return;
  state.drag = { x: event.clientX, y: event.clientY, viewX: state.view.x, viewY: state.view.y, moved: false };
  canvas.setPointerCapture(event.pointerId);
});
canvas.addEventListener("pointermove", (event) => {
  const x = event.clientX - canvas.getBoundingClientRect().left;
  const y = event.clientY - canvas.getBoundingClientRect().top;
  if (!state.drag) {
    canvas.classList.toggle("port", hitTest(x, y)?.kind === "terminal");
    return;
  }
  const dx = event.clientX - state.drag.x;
  const dy = event.clientY - state.drag.y;
  if (Math.hypot(dx, dy) < 4 && !state.drag.moved) return;
  state.drag.moved = true;
  canvas.classList.add("down");
  state.view.x = state.drag.viewX + dx;
  state.view.y = state.drag.viewY + dy;
  draw();
});
canvas.addEventListener("pointerup", (event) => {
  const drag = state.drag;
  state.drag = null;
  canvas.classList.remove("down");
  if (!drag || drag.moved) return;
  const rect = canvas.getBoundingClientRect();
  const hit = hitTest(event.clientX - rect.left, event.clientY - rect.top);
  if (hit?.kind === "terminal") chooseTerminal(hit.port);
  else if (hit?.kind === "pin") selectNet(hit.pin.net);
  else if (hit?.kind === "wire") selectNet(hit.wire.net);
  else if (hit?.kind === "device") {
    state.instance = hit.instance;
    renderInspector();
    draw();
  }
});
canvas.addEventListener("lostpointercapture", () => {
  state.drag = null;
  canvas.classList.remove("down");
});
canvas.addEventListener("wheel", (event) => {
  event.preventDefault();
  const rect = canvas.getBoundingClientRect();
  zoom(Math.exp(-Math.max(-180, Math.min(180, event.deltaY)) * 0.002), event.clientX - rect.left, event.clientY - rect.top);
}, { passive: false });
element("fit").addEventListener("click", fit);
element("zoom-in").addEventListener("click", () => zoom(1.25));
element("zoom-out").addEventListener("click", () => zoom(0.8));
new ResizeObserver(() => state.scene ? fit() : draw()).observe(element("canvas-shell"));
open("/api/demo");
