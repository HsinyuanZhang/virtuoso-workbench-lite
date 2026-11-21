"""Local browser workbench for reviewing Virtuoso schematic connectivity."""

import argparse
import hashlib
import json
import math
import os
import re
import secrets
import subprocess
import tempfile
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parent
NAME = re.compile(r"[A-Za-z0-9_.$+!@-]{1,128}")
NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
PAIR = re.compile(rf"({NUMBER})(?:\s*:\s*|\s+)({NUMBER})")
PLAN_SCHEMA = "circuit-workbench/wire-intent-v1"

# Uses the same OA attributes as the Bridge's schematic reader and wire helper.
SNAPSHOT_QUERY = r'''
let((cv result net point pin fig bb)
  cv = dbOpenCellViewByType("@LIB@" "@CELL@" "schematic" "schematic" "r")
  if(cv then
    unwindProtect(
      progn(
        result = "SNAPSHOT\n"
        foreach(inst cv~>instances
          unless(inst~>purpose == "pin"
            result = strcat(result sprintf(nil "I|%s|%s|%s|%s|%L|%L\n"
              inst~>name inst~>libName inst~>cellName inst~>orient inst~>xy inst~>bBox))
            foreach(term inst~>master~>terminals
              net = car(setof(it inst~>instTerms it~>name == term~>name))
              pin = car(term~>pins)
              fig = when(pin car(pin~>figs))
              bb = when(fig dbTransformBBox(fig~>bBox inst~>transform))
              point = when(bb
                list((xCoord(car(bb)) + xCoord(cadr(bb))) / 2.0
                     (yCoord(car(bb)) + yCoord(cadr(bb))) / 2.0))
              result = strcat(result sprintf(nil "T|%s|%s|%L\n" term~>name
                 if(net && net~>net net~>net~>name "") point)))))
        foreach(term cv~>terminals
          pin = car(term~>pins)
          fig = when(pin car(pin~>figs))
          bb = when(fig fig~>bBox)
          point = when(bb
            list((xCoord(car(bb)) + xCoord(cadr(bb))) / 2.0
                 (yCoord(car(bb)) + yCoord(cadr(bb))) / 2.0))
          result = strcat(result sprintf(nil "P|%s|%s|%s|%L\n" term~>name
            term~>direction if(term~>net term~>net~>name "") point)))
        foreach(shape cv~>shapes
          when(shape~>objType == "line" && shape~>net
            result = strcat(result sprintf(nil "W|%s|%L\n"
              shape~>net~>name shape~>points))))
        strcat(result "END"))
      dbClose(cv))
  else "MISSING"))
'''


class WorkbenchError(ValueError):
    def __init__(self, message, status=HTTPStatus.BAD_REQUEST):
        super().__init__(message)
        self.status = status


def points(raw):
    values = [[float(x), float(y)] for x, y in PAIR.findall(raw)]
    if any(not math.isfinite(value) for pair in values for value in pair):
        raise ValueError("A coordinate is not finite.")
    return values


def point(raw):
    return next(iter(points(raw)), None)


def revision(snapshot):
    data = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return "sha256:" + hashlib.sha256(data.encode()).hexdigest()


def parse_snapshot(raw, library, cell):
    if raw.strip() == "MISSING":
        raise WorkbenchError("Schematic cellview was not found.", HTTPStatus.NOT_FOUND)
    records = raw.strip().splitlines()
    if not records or records[0] != "SNAPSHOT" or records[-1] != "END":
        raise ValueError("Virtuoso returned an incomplete schematic snapshot.")
    instances, pins, wires = [], [], []
    for record in records[1:-1]:
        kind, *fields = record.split("|")
        if kind == "I":
            name, lib, master, orientation, xy, bbox = fields
            instances.append({
                "name": name, "library": lib, "master": master,
                "orientation": orientation, "xy": point(xy), "bbox": points(bbox),
                "terminals": [],
            })
        elif kind == "T":
            name, net, xy = fields
            instances[-1]["terminals"].append({"name": name, "net": net or None, "xy": point(xy)})
        elif kind == "P":
            name, direction, net, xy = fields
            pins.append({"name": name, "direction": direction, "net": net or None, "xy": point(xy)})
        elif kind == "W":
            net, coords = fields
            path = points(coords)
            if len(path) < 2:
                raise ValueError("Virtuoso returned an invalid wire path.")
            wires.append({"net": net, "points": path})
        else:
            raise ValueError("Virtuoso returned an unknown schematic record.")
    for instance in instances:
        instance["terminals"].sort(key=lambda terminal: terminal["name"])
    return {
        "library": library, "cell": cell, "view": "schematic", "source": "virtuoso",
        "instances": sorted(instances, key=lambda instance: instance["name"]),
        "pins": sorted(pins, key=lambda pin: pin["name"]),
        "wires": sorted(wires, key=lambda wire: (wire["net"], wire["points"])),
    }


def validate_name(value):
    if not isinstance(value, str) or not NAME.fullmatch(value):
        raise WorkbenchError("Enter a valid library and cell name.")
    return value


def read_snapshot(library, cell, bridge_host, bridge_port):
    library, cell = validate_name(library), validate_name(cell)
    if not bridge_host:
        raise WorkbenchError("Start the workbench with a Bridge endpoint to read live cells.", HTTPStatus.SERVICE_UNAVAILABLE)
    from virtuoso_bridge import VirtuosoClient, decode_skill_output

    values = {"@LIB@": library, "@CELL@": cell}
    query = re.sub(r"@(?:LIB|CELL)@", lambda marker: values[marker[0]], SNAPSHOT_QUERY)
    with VirtuosoClient.local(host=bridge_host, port=bridge_port, timeout=15) as client:
        result = client.execute_skill(query)
    if not result.ok:
        detail = result.errors[0] if result.errors else "No response from CIW."
        raise RuntimeError(f"Bridge could not read the schematic: {detail}")
    return parse_snapshot(decode_skill_output(result.output), library, cell)


def terminal_index(snapshot):
    return {
        (instance["name"], terminal["name"]): terminal
        for instance in snapshot["instances"]
        for terminal in instance["terminals"]
    }


def review_wire(snapshot, endpoints):
    if not isinstance(endpoints, list) or len(endpoints) != 2:
        raise WorkbenchError("Select two instance terminals.")
    terminals = terminal_index(snapshot)
    keys = []
    for endpoint in endpoints:
        if not isinstance(endpoint, dict):
            raise WorkbenchError("Each endpoint must be an instance terminal.")
        instance, terminal = endpoint.get("instance"), endpoint.get("terminal")
        if not isinstance(instance, str) or not isinstance(terminal, str) or (instance, terminal) not in terminals:
            raise WorkbenchError("A selected terminal no longer exists.")
        keys.append((instance, terminal))
    if keys[0] == keys[1]:
        raise WorkbenchError("Select two distinct terminals.")
    before = [
        {
            "instance": instance,
            "terminal": terminal,
            "net": terminals[(instance, terminal)]["net"],
            "has_geometry": terminals[(instance, terminal)]["xy"] is not None,
        }
        for instance, terminal in keys
    ]
    left, right = (endpoint["net"] for endpoint in before)
    if left and left == right:
        outcome = "already-connected"
    elif left and right:
        outcome = "merge-existing-nets"
    elif left or right:
        outcome = "attach-to-existing-net"
    else:
        outcome = "create-net"
    affected_nets = sorted({net for net in (left, right) if net})
    affected_pins = [pin["name"] for pin in snapshot["pins"] if pin["net"] in affected_nets]
    return {
        "type": "connect-instance-terminals",
        "endpoints": before,
        "outcome": outcome,
        "affected_nets": affected_nets,
        "affected_top_level_pins": affected_pins,
    }


def save_plan(payload, demo, read_live, state_dir):
    if not isinstance(payload, dict) or not isinstance(payload.get("target"), dict):
        raise WorkbenchError("Expected a target schematic.")
    target = payload["target"]
    if target.get("source") == "demo":
        snapshot = demo
    elif target.get("source") == "virtuoso":
        snapshot = read_live(target.get("library"), target.get("cell"))
    else:
        raise WorkbenchError("Unknown schematic source.")
    if payload.get("revision") != revision(snapshot):
        raise WorkbenchError("The schematic changed. Reload the cell and review the wire again.", HTTPStatus.CONFLICT)
    note = payload.get("note", "")
    if not isinstance(note, str) or len(note) > 500:
        raise WorkbenchError("The agent note must be text of at most 500 characters.")
    operation = review_wire(snapshot, payload.get("endpoints"))
    instructions = {
        "already-connected": "The terminals already share a net. Review whether a physical route is needed before editing.",
        "merge-existing-nets": "Review every affected net and port with the designer before applying this merge.",
        "attach-to-existing-net": "Review the existing net and confirm the intended added terminal.",
        "create-net": "Confirm the new wire and its intended net name before editing.",
    }
    plan = {
        "schema": PLAN_SCHEMA,
        "target": {key: snapshot[key] for key in ("source", "library", "cell", "view")},
        "base_revision": revision(snapshot), "operation": operation,
        "review": instructions[operation["outcome"]],
        "note": note.strip(),
    }
    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = state_dir / f"wire-{secrets.token_hex(6)}.json"
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=state_dir, delete=False) as handle:
        temp_path = Path(handle.name)
        try:
            json.dump(plan, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            os.replace(temp_path, path)
        finally:
            temp_path.unlink(missing_ok=True)
    return {"id": path.stem, "path": str(path), "plan": plan}


class Handler(BaseHTTPRequestHandler):
    STATIC = {
        "/": ("index.html", "text/html"),
        "/workbench.css": ("workbench.css", "text/css"),
        "/workbench.js": ("workbench.js", "text/javascript"),
    }

    def setup(self):
        self.request.settimeout(30)
        super().setup()

    def check_origin(self):
        host = self.headers.get("Host", "")
        # Accept a different local port for SSH forwards ending at this server.
        if not re.fullmatch(r"(localhost|127\.0\.0\.1)(:\d+)?", host):
            raise WorkbenchError("Connect through a loopback URL.", HTTPStatus.FORBIDDEN)
        origin = self.headers.get("Origin")
        if self.command == "POST" and origin != f"http://{host}":
            raise WorkbenchError("A same-origin browser request is required.", HTTPStatus.FORBIDDEN)

    def send(self, status, content, content_type):
        self.send_response(status)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; object-src 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(content)

    def send_json(self, data, status=HTTPStatus.OK):
        self.send(status, json.dumps(data, allow_nan=False).encode(), "application/json")

    def route(self):
        self.check_origin()
        target = urlsplit(self.path)
        if self.command == "GET":
            if target.path in self.STATIC:
                filename, content_type = self.STATIC[target.path]
                return self.send(HTTPStatus.OK, (ROOT / "web" / filename).read_bytes(), content_type)
            if target.path == "/api/demo":
                snapshot = self.server.demo
            elif target.path == "/api/schematic":
                query = parse_qs(target.query, max_num_fields=4)
                snapshot = self.server.read_live(query.get("library", [""])[0], query.get("cell", [""])[0])
            else:
                raise WorkbenchError("Page not found.", HTTPStatus.NOT_FOUND)
            return self.send_json({**snapshot, "revision": revision(snapshot)})
        if target.path != "/api/handoffs":
            raise WorkbenchError("Page not found.", HTTPStatus.NOT_FOUND)
        if self.headers.get_content_type() != "application/json":
            raise WorkbenchError("Send a JSON request.")
        length = int(self.headers.get("Content-Length", "0"))
        if not 0 < length <= 8192:
            raise WorkbenchError("Request size is outside the supported range.")
        payload = json.loads(self.rfile.read(length))
        saved = save_plan(payload, self.server.demo, self.server.read_live, self.server.state_dir)
        return self.send_json(saved, HTTPStatus.CREATED)

    def dispatch(self):
        try:
            self.route()
        except WorkbenchError as error:
            self.send_json({"error": str(error)}, error.status)
        except (json.JSONDecodeError, UnicodeDecodeError):
            self.send_json({"error": "Invalid JSON request."}, HTTPStatus.BAD_REQUEST)
        except (ImportError, OSError, RuntimeError, ValueError) as error:
            self.log_error("%s", error)
            self.send_json({"error": str(error)}, HTTPStatus.BAD_GATEWAY)

    do_GET = dispatch
    do_POST = dispatch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    endpoint = parser.add_mutually_exclusive_group()
    endpoint.add_argument("--bridge-host")
    endpoint.add_argument("--bridge-docker", metavar="CONTAINER")
    parser.add_argument("--bridge-port", type=int, default=65432)
    parser.add_argument("--state-dir", type=Path, default=ROOT / ".workbench" / "handoffs")
    parser.add_argument("--review", type=Path, metavar="HANDOFF", help="Review a saved handoff against the current snapshot")
    arguments = parser.parse_args()
    host = arguments.bridge_host
    if arguments.bridge_docker:
        inspected = subprocess.check_output(
            ["docker", "inspect", "--type", "container",
             "--format", "{{json .NetworkSettings.Networks}}", arguments.bridge_docker],
            text=True, timeout=10,
        )
        addresses = sorted({network["IPAddress"] for network in json.loads(inspected).values() if network["IPAddress"]})
        if len(addresses) != 1:
            parser.error("The container must have one IPv4 address, or supply --bridge-host.")
        host = addresses[0]
    demo = json.loads((ROOT / "demo.json").read_text(encoding="utf-8"))
    bridge_lock = Lock()

    def read_live(library, cell):
        with bridge_lock:
            return read_snapshot(library, cell, host, arguments.bridge_port)

    if arguments.review:
        try:
            plan = json.loads(arguments.review.read_text(encoding="utf-8"))
            if plan["schema"] != PLAN_SCHEMA:
                raise WorkbenchError("Unsupported handoff schema.")
            target = plan["target"]
            if target["source"] not in ("demo", "virtuoso"):
                raise WorkbenchError("Unsupported schematic source.")
            snapshot = demo if target["source"] == "demo" else read_live(target["library"], target["cell"])
            if plan["base_revision"] != revision(snapshot):
                raise WorkbenchError("The schematic changed. Make a new handoff from the latest cell.")
            operation = review_wire(snapshot, plan["operation"]["endpoints"])
            print(json.dumps({"target": target, "operation": operation, "note": plan["note"]}, indent=2))
        except (KeyError, TypeError, OSError, RuntimeError, ValueError) as error:
            parser.exit(1, f"Could not review handoff: {error}\n")
        return

    with ThreadingHTTPServer(("127.0.0.1", arguments.port), Handler) as server:
        server.demo = demo
        server.read_live = read_live
        server.state_dir = arguments.state_dir.resolve()
        print(f"Circuit Workbench at http://127.0.0.1:{server.server_port}", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
