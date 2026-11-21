const element = (id) => document.getElementById(id);
const canvas = element("schematic");
const context = canvas.getContext("2d");
const state = {
  snapshot: null,
  scene: null,
  nets: new Map(),
  instance: null,
  net: null,
  endpoints: [],
  receipt: null,
  view: { x: 0, y: 0, scale: 1 },
  hits: [],
  drag: null,
  request: 0,
};
const colors = {
  wire: "#719f87",
  active: "#c0f6a4",
  dim: "#384d43",
  selected: "#f4c170",
  text: "#e6eee9",
  muted: "#a1b7aa",
};

function item(tag, text, className) {
  const node = document.createElement(tag);
  node.textContent = text;
  if (className) node.className = className;
  return node;
}

function countLabel(count, singular) {
  return `${count} ${singular}${count === 1 ? "" : "s"}`;
}

function listButton(name, detail, active, click) {
  const entry = item("li", "");
  const button = item("button", "");
  button.type = "button";
  button.setAttribute("aria-pressed", String(active));
  button.append(item("span", name), item("span", detail));
  button.addEventListener("click", click);
  entry.append(button);
  return entry;
}

function message(text, error = false) {
  const status = element("status");
  status.hidden = !text;
  status.textContent = text;
  status.classList.toggle("error", error);
}

async function api(path, options) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error);
  return data;
}

function bounds(coordinates) {
  const extent = { left: Infinity, right: -Infinity, top: -Infinity, bottom: Infinity };
  for (const [x, y] of coordinates) {
    extent.left = Math.min(extent.left, x);
    extent.right = Math.max(extent.right, x);
    extent.top = Math.max(extent.top, y);
    extent.bottom = Math.min(extent.bottom, y);
  }
  return extent;
}

function buildScene(snapshot) {
  const devices = [];
  const extentPoints = [
    ...snapshot.wires.flatMap((wire) => wire.points),
    ...snapshot.pins.map((pin) => pin.xy).filter(Boolean),
  ];
  const widths = snapshot.instances
    .filter((instance) => instance.bbox.length === 2)
    .map((instance) => instance.bbox[1][0] - instance.bbox[0][0])
    .filter((width) => width > 0).sort((a, b) => a - b);
  const unit = widths[Math.floor(widths.length / 2)] ?? 1;
  let fallbackCount = 0;
  for (const instance of snapshot.instances) {
    const measured = instance.terminals.map((terminal) => terminal.xy).filter(Boolean);
    const area = bounds(measured.length ? measured : instance.bbox);
    const hasBounds = Number.isFinite(area.left);
    const [x, y] = hasBounds
      ? [(area.left + area.right) / 2, (area.top + area.bottom) / 2]
      : (instance.xy ?? [fallbackCount++ * unit * 2, 0]);
    const nativeWidth = hasBounds ? area.right - area.left : 0;
    const nativeHeight = hasBounds ? area.top - area.bottom : 0;
    const halfWidth = Math.max(unit * 0.26, nativeWidth * 0.40);
    const halfHeight = Math.max(unit * 0.14, nativeHeight * 0.40, instance.terminals.length * unit * 0.035);
    const groups = { left: [], right: [], top: [], bottom: [] };
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
    const ports = [];
    for (const [side, terminals] of Object.entries(groups)) {
      const vertical = side === "left" || side === "right";
      const axis = vertical ? 1 : 0;
      terminals.sort((a, b) => (a.xy?.[axis] ?? 0) - (b.xy?.[axis] ?? 0));
      terminals.forEach((terminal, index) => {
        const offset = ((2 * index + 1) / terminals.length - 1) * 0.88;
        const position = vertical
          ? [x + halfWidth * (side === "left" ? -1 : 1), y + halfHeight * offset]
          : [x + halfWidth * offset, y + halfHeight * (side === "top" ? 1 : -1)];
        ports.push({ ...terminal, instance: instance.name, side, position });
      });
    }
    devices.push({ instance, x, y, halfWidth, halfHeight, ports });
    extentPoints.push([x - halfWidth, y - halfHeight], [x + halfWidth, y + halfHeight]);
  }
  return { devices, area: bounds(extentPoints.length ? extentPoints : [[-1, -1], [1, 1]]) };
}

function collectNets(snapshot) {
  const nets = new Map();
  function add(name, group, value) {
    if (!name) return;
    if (!nets.has(name)) nets.set(name, { terminals: [], pins: [], segments: 0 });
    const entry = nets.get(name);
    if (group === "segments") entry.segments += 1;
    else entry[group].push(value);
  }
  for (const instance of snapshot.instances) {
    for (const terminal of instance.terminals) add(terminal.net, "terminals", `${instance.name}.${terminal.name}`);
  }
  for (const pin of snapshot.pins) add(pin.net, "pins", pin.name);
  for (const wire of snapshot.wires) add(wire.net, "segments", null);
  return new Map([...nets].sort(([a], [b]) => a.localeCompare(b, undefined, { numeric: true })));
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

function screen([x, y]) {
  return [state.view.x + x * state.view.scale, state.view.y - y * state.view.scale];
}

function zoom(multiplier, x = canvas.clientWidth / 2, y = canvas.clientHeight / 2) {
  if (!state.scene) return;
  const before = state.view.scale;
  const after = Math.max(0.001, Math.min(100000, before * multiplier));
  state.view = { x: x - (x - state.view.x) * after / before, y: y - (y - state.view.y) * after / before, scale: after };
  draw();
}

function line(path, color, width = 1, dash = []) {
  context.strokeStyle = color;
  context.lineWidth = width;
  context.setLineDash(dash);
  context.beginPath();
  path.forEach(([x, y], index) => index ? context.lineTo(x, y) : context.moveTo(x, y));
  context.stroke();
  context.setLineDash([]);
}

function circle(x, y, radius, fill, stroke = null) {
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

function text(textValue, x, y, color, align = "left", pixels = 11, background = false) {
  context.font = `${pixels}px monospace`;
  context.textAlign = align;
  context.textBaseline = "middle";
  if (background) {
    const width = context.measureText(textValue).width;
    const left = align === "center" ? x - width / 2 : align === "right" ? x - width : x;
    context.fillStyle = "#111916";
    context.fillRect(left - 1, y - pixels / 2, width + 2, pixels);
  }
  context.fillStyle = color;
  context.fillText(textValue, x, y);
}

function isEndpoint(port) {
  return state.endpoints.some((end) => end.instance === port.instance && end.name === port.name);
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
  context.fillStyle = "#2a3a31";
  const step = 19;
  for (let x = (state.view.x % step + step) % step; x < width; x += step) {
    for (let y = (state.view.y % step + step) % step; y < height; y += step) context.fillRect(x, y, 1, 1);
  }
  if (!state.scene) return;
  state.hits = [];
  const wireHits = [];
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
    const chosen = state.instance === instance.name;
    const connected = Boolean(state.net && ports.some((port) => port.net === state.net));
    context.fillStyle = chosen ? "#283d30" : "#1a2721";
    context.strokeStyle = chosen || connected ? "#8eb490" : "#56705f";
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
      const selected = isEndpoint(port);
      const active = Boolean(state.net && state.net === port.net);
      const color = selected ? colors.selected : active ? colors.active : port.net ? colors.wire : colors.selected;
      if (port.xy) line([screen(port.xy), [x, y]], color, 1.15, [2, 2]);
      renderedPorts.push({ port, x, y, color, selected, active, device });
    }
  }
  for (const pin of state.snapshot.pins) {
    if (!pin.xy) continue;
    const [x, y] = screen(pin.xy);
    const color = pin.net === state.net ? colors.active : colors.muted;
    context.fillStyle = color;
    context.fillRect(x - 3, y - 3, 6, 6);
    text(pin.name, x + 6, y - 9, color, "left", 11, true);
    if (pin.net) state.hits.push({ kind: "pin", pin, x, y });
  }
  for (const { port, x, y, color, selected, active, device } of renderedPorts) {
    const radius = selected ? 5.5 : 3.4;
    circle(x, y, radius, selected ? color : "#111916", color);
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
}

function renderNets() {
  const query = element("net-search").value.trim().toLowerCase();
  const entries = [...state.nets].filter(([name]) => name.toLowerCase().includes(query));
  element("net-list").replaceChildren(...entries.map(([name, net]) => listButton(
    name, String(net.terminals.length + net.pins.length), state.net === name,
    () => selectNet(state.net === name ? null : name),
  )));
  element("net-count").textContent = state.nets.size;
}

function renderInspector() {
  const device = state.scene.devices.find((entry) => entry.instance.name === state.instance);
  const terminalList = element("terminal-list");
  element("master").textContent = device ? `${device.instance.library}/${device.instance.master} · ${device.instance.orientation}` : "";
  if (device) element("instances").value = device.instance.name;
  terminalList.replaceChildren(...(device?.ports ?? []).map((port) => listButton(
    port.name, port.net ?? "Open", isEndpoint(port), () => chooseTerminal(port),
  )));
}

function renderTopology() {
  const open = [];
  const sameNet = [];
  for (const instance of state.snapshot.instances) {
    const byName = new Map(instance.terminals.map((terminal) => [terminal.name.toUpperCase(), terminal]));
    const drain = byName.get("D");
    if (byName.has("S") && drain?.net && drain.net === byName.get("G")?.net) {
      sameNet.push(`${instance.name} D = G`);
    }
    for (const terminal of instance.terminals) {
      if (!terminal.net) open.push(`${instance.name}.${terminal.name}`);
    }
  }
  const net = state.nets.get(state.net);
  const details = element("net-details");
  details.replaceChildren();
  element("topology-overview").textContent = state.net
    ? `${state.net} · ${countLabel(net.terminals.length, "instance terminal")} · ${countLabel(net.segments, "wire segment")}`
    : countLabel(open.length, "open instance terminal");
  if (net) {
    if (net.terminals.length) details.append(item("p", net.terminals.join(" · ")));
    if (net.pins.length) details.append(item("p", `I/O: ${net.pins.join(", ")}`));
  }
  element("checks").replaceChildren(
    ...open.slice(0, 6).map((name) => item("li", `Open: ${name}`, "warning")),
    ...sameNet.slice(0, 6).map((name) => item("li", name)),
    ...(open.length > 6 || sameNet.length > 6 ? [item("li", "Use the instance inspector to review more terminals.")] : []),
  );
}

function renderHandoff() {
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
  element("save").disabled = endpoints.length !== 2 || Boolean(state.receipt) || element("save").dataset.busy === "true";
  element("clear").disabled = endpoints.length === 0;
  element("receipt").hidden = !state.receipt;
  if (state.receipt) element("receipt-path").textContent = state.receipt.path;
}

function selectNet(name) {
  state.net = name;
  renderNets();
  renderTopology();
  draw();
}

function chooseTerminal(port) {
  state.receipt = null;
  if (isEndpoint(port)) state.endpoints = state.endpoints.filter((end) => end.instance !== port.instance || end.name !== port.name);
  else if (state.endpoints.length === 2) state.endpoints = [port];
  else state.endpoints.push(port);
  state.instance = port.instance;
  renderInspector();
  renderHandoff();
  draw();
  message("");
}

async function open(path) {
  const request = ++state.request;
  element("open-cell").disabled = true;
  element("open-demo").disabled = true;
  message("Opening schematic…");
  try {
    const snapshot = await api(path);
    if (request !== state.request) return;
    state.snapshot = snapshot;
    state.scene = buildScene(snapshot);
    state.nets = collectNets(snapshot);
    state.instance = snapshot.instances[0]?.name ?? null;
    state.net = null;
    state.endpoints = [];
    state.receipt = null;
    element("note").value = "";
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
    renderHandoff();
    fit();
    message("");
  } catch (error) {
    if (request === state.request) message(error.message, true);
  } finally {
    if (request === state.request) {
      element("open-cell").disabled = false;
      element("open-demo").disabled = false;
    }
  }
}

element("open-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const fields = new FormData(event.currentTarget);
  open(`/api/schematic?${new URLSearchParams(fields)}`);
});
element("open-demo").addEventListener("click", () => open("/api/demo"));
element("net-search").addEventListener("input", renderNets);
element("instances").addEventListener("change", (event) => {
  state.instance = event.target.value;
  renderInspector();
  draw();
});
element("clear").addEventListener("click", () => {
  state.endpoints = [];
  state.receipt = null;
  renderInspector();
  renderHandoff();
  draw();
});
element("note").addEventListener("input", () => {
  if (state.receipt) {
    state.receipt = null;
    renderHandoff();
  }
});
element("save").addEventListener("click", async () => {
  const request = state.request;
  const endpoints = [...state.endpoints];
  const note = element("note").value;
  element("save").dataset.busy = "true";
  renderHandoff();
  try {
    const { source, library, cell, revision } = state.snapshot;
    const receipt = await api("/api/handoffs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        target: { source, library, cell }, revision, note,
        endpoints: endpoints.map((port) => ({ instance: port.instance, terminal: port.name })),
      }),
    });
    if (request === state.request && endpoints.every((port, index) => state.endpoints[index] === port)
      && note === element("note").value) {
      state.receipt = receipt;
      message("Handoff saved on the server.");
    }
  } catch (error) {
    if (request === state.request) message(error.message, true);
  } finally {
    delete element("save").dataset.busy;
    renderHandoff();
  }
});
element("copy").addEventListener("click", async () => {
  if (!state.receipt) return;
  const prompt = `Review this Circuit Workbench wire handoff: ${state.receipt.path}. `
    + "Verify the current revision and explain the affected nets and the proposed Virtuoso route.";
  try {
    await navigator.clipboard.writeText(prompt);
    message("Agent request copied.");
  } catch {
    message("Clipboard unavailable. Copy the handoff path from the inspector.");
  }
});

function segmentDistance(x, y, [x1, y1], [x2, y2]) {
  const dx = x2 - x1;
  const dy = y2 - y1;
  const ratio = Math.max(0, Math.min(1, ((x - x1) * dx + (y - y1) * dy) / ((dx * dx + dy * dy) || 1)));
  return Math.hypot(x - x1 - ratio * dx, y - y1 - ratio * dy);
}

function hitTest(x, y) {
  let nearest = null;
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
