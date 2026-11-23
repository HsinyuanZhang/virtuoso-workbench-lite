"""Local browser workbench for reviewing Virtuoso schematic connectivity."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import secrets
import tempfile
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable
from urllib.parse import parse_qs, urlsplit

from .checks import check_preview, connectivity_checks
from .symbols import SymbolPackageError, library_identity, parse_skill_value

if TYPE_CHECKING:
    from .requests import RequestStore

ROOT = Path(__file__).resolve().parent
NAME = re.compile(r"[A-Za-z0-9_.$+!@-]{1,128}")
NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
PAIR = re.compile(rf"({NUMBER})(?:\s*:\s*|\s+)({NUMBER})")
PLAN_SCHEMA = "circuit-workbench/wire-intent-v1"

SYMBOL_METADATA_QUERY = r"""let((cv rows lib path inst transform mag parameterized termRows anchors pin fig bb)
  cv=dbOpenCellViewByType("@LIB@" "@CELL@" "schematic" "schematic" "r")
  if(cv then unwindProtect(
    progn(rows=nil
      foreach(inst cv~>instances unless(inst~>purpose == "pin"
        lib=ddGetObj(inst~>libName)
        path=when(lib lib~>readPath)
        transform=inst~>transform
        mag=when(transform caddr(transform))
        parameterized=when(inst~>master~>superMaster t)
        termRows=nil
        foreach(term inst~>master~>terminals
          anchors=nil
          foreach(pin term~>pins
            foreach(fig pin~>figs
              bb=when(fig~>bBox dbTransformBBox(fig~>bBox transform))
              when(bb anchors=cons(list((xCoord(car(bb)) + xCoord(cadr(bb))) / 2.0
                                        (yCoord(car(bb)) + yCoord(cadr(bb))) / 2.0) anchors)))
          termRows=cons(list(term~>name reverse(anchors)) termRows))
        rows=cons(list(inst~>name inst~>libName inst~>cellName inst~>viewName path mag parameterized reverse(termRows)) rows)))
      sprintf(nil "%L" reverse(rows))) dbClose(cv))
    else sprintf(nil "%L" list("MISSING"))))"""

SYMBOL_PRESENTATION_FIELDS = {"master_view", "library_identity", "symbol_metadata_error"}
MAX_SOURCE_PIN_ANCHORS = 100000

# Uses the same OA attributes as the Bridge's schematic reader and wire helper.
SNAPSHOT_QUERY = r"""
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
"""


class WorkbenchError(ValueError):
    def __init__(self, message: str, status: HTTPStatus = HTTPStatus.BAD_REQUEST) -> None:
        super().__init__(message)
        self.status = status


def points(raw: str) -> list[list[float]]:
    values = [[float(x), float(y)] for x, y in PAIR.findall(raw)]
    if any(not math.isfinite(value) for pair in values for value in pair):
        raise ValueError("A coordinate is not finite.")
    return values


def point(raw: str) -> list[float] | None:
    return next(iter(points(raw)), None)


def revision(snapshot: dict[str, Any]) -> str:
    stable = dict(snapshot)
    stable["instances"] = []
    for instance in snapshot["instances"]:
        stable_instance = {key: value for key, value in instance.items() if key not in SYMBOL_PRESENTATION_FIELDS}
        stable_instance["terminals"] = [
            {key: value for key, value in terminal.items() if key != "source_pin_anchors"}
            for terminal in instance["terminals"]
        ]
        stable["instances"].append(stable_instance)
    data = json.dumps(stable, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return "sha256:" + hashlib.sha256(data.encode()).hexdigest()


def parse_snapshot(raw: str, library: str, cell: str) -> dict[str, Any]:
    if raw.strip() == "MISSING":
        raise WorkbenchError("Schematic cellview was not found.", HTTPStatus.NOT_FOUND)
    records = raw.strip().splitlines()
    if not records or records[0] != "SNAPSHOT" or records[-1] != "END":
        raise ValueError("Virtuoso returned an incomplete schematic snapshot.")
    instances: list[dict[str, Any]] = []
    pins: list[dict[str, Any]] = []
    wires: list[dict[str, Any]] = []
    for record in records[1:-1]:
        kind, *fields = record.split("|")
        if kind == "I":
            name, lib, master, orientation, xy, bbox = fields
            instances.append(
                {
                    "name": name,
                    "library": lib,
                    "master": master,
                    "orientation": orientation,
                    "xy": point(xy),
                    "bbox": points(bbox),
                    "terminals": [],
                }
            )
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
        "library": library,
        "cell": cell,
        "view": "schematic",
        "source": "virtuoso",
        "instances": sorted(instances, key=lambda instance: instance["name"]),
        "pins": sorted(pins, key=lambda pin: pin["name"]),
        "wires": sorted(wires, key=lambda wire: (wire["net"], wire["points"])),
    }


def validate_name(value: Any) -> str:
    if not isinstance(value, str) or not NAME.fullmatch(value):
        raise WorkbenchError("Enter a valid library and cell name.")
    return value


def read_snapshot(
    library: str,
    cell: str,
    bridge_host: str | None,
    bridge_port: int,
    include_symbol_metadata: bool = False,
) -> dict[str, Any]:
    library, cell = validate_name(library), validate_name(cell)
    if not bridge_host:
        raise WorkbenchError(
            "Start the workbench with a Bridge endpoint to read live cells.", HTTPStatus.SERVICE_UNAVAILABLE
        )
    from virtuoso_bridge import VirtuosoClient, decode_skill_output

    values = {"@LIB@": library, "@CELL@": cell}
    query = re.sub(r"@(?:LIB|CELL)@", lambda marker: values[marker[0]], SNAPSHOT_QUERY)
    with VirtuosoClient.local(host=bridge_host, port=bridge_port, timeout=15) as client:
        result = client.execute_skill(query)
        metadata_result = None
        metadata_query_failed = False
        if result.ok and include_symbol_metadata:
            try:
                metadata_result = client.execute_skill(
                    SYMBOL_METADATA_QUERY.replace("@LIB@", library).replace("@CELL@", cell), timeout=15
                )
            except Exception:
                metadata_query_failed = True
    if not result.ok:
        detail = result.errors[0] if result.errors else "No response from CIW."
        raise RuntimeError(f"Bridge could not read the schematic: {detail}")
    snapshot = parse_snapshot(decode_skill_output(result.output), library, cell)
    if include_symbol_metadata and (metadata_query_failed or metadata_result is None or not metadata_result.ok):
        _mark_symbol_metadata_error(snapshot, "metadata-unavailable")
    elif metadata_result is not None:
        try:
            rows = parse_skill_value(decode_skill_output(metadata_result.output))
            _attach_symbol_metadata(snapshot, rows)
        except Exception:
            _mark_symbol_metadata_error(snapshot, "metadata-invalid")
    return snapshot


def _mark_symbol_metadata_error(snapshot: dict[str, Any], reason: str) -> None:
    for instance in snapshot["instances"]:
        instance["symbol_metadata_error"] = reason


def _metadata_anchor_rows(value: Any) -> dict[str, list[list[float]]]:
    if value is None:
        return {}
    if not isinstance(value, list):
        raise SymbolPackageError("Bridge returned invalid source pin anchors.")
    anchors_by_terminal: dict[str, list[list[float]]] = {}
    anchor_count = 0
    for row in value:
        if not isinstance(row, list) or len(row) != 2 or not isinstance(row[0], str) or not row[0]:
            raise SymbolPackageError("Bridge returned invalid source pin anchors.")
        name, anchors = row
        if name in anchors_by_terminal:
            raise SymbolPackageError("Bridge returned duplicate source terminal metadata.")
        if anchors is None:
            anchors = []
        if not isinstance(anchors, list):
            raise SymbolPackageError("Bridge returned invalid source pin anchors.")
        anchor_count += len(anchors)
        if anchor_count > MAX_SOURCE_PIN_ANCHORS:
            raise SymbolPackageError("Bridge returned too many source pin anchors.")
        normalized = []
        for anchor in anchors:
            if (
                not isinstance(anchor, list)
                or len(anchor) != 2
                or any(
                    isinstance(coordinate, bool) or not isinstance(coordinate, (int, float)) for coordinate in anchor
                )
                or any(not math.isfinite(float(coordinate)) for coordinate in anchor)
                or any(abs(float(coordinate)) > 1e12 for coordinate in anchor)
            ):
                raise SymbolPackageError("Bridge returned invalid source pin anchors.")
            normalized.append([float(anchor[0]), float(anchor[1])])
        anchors_by_terminal[name] = normalized
    return anchors_by_terminal


def _attach_symbol_metadata(snapshot: dict[str, Any], rows: Any) -> None:
    if rows is None:
        rows = []
    if not isinstance(rows, list):
        raise SymbolPackageError("Bridge returned invalid symbol metadata.")
    rows_by_name: dict[str, list[list[Any]]] = {}
    for row in rows:
        if not isinstance(row, list) or len(row) != 8 or not isinstance(row[0], str):
            raise SymbolPackageError("Bridge returned invalid symbol metadata.")
        rows_by_name.setdefault(row[0], []).append(row)
    for instance in snapshot["instances"]:
        matches = rows_by_name.get(instance["name"], [])
        if len(matches) != 1:
            instance["symbol_metadata_error"] = "metadata-missing" if not matches else "metadata-ambiguous"
            continue
        _, library, master, view, path, magnification, parameterized, terminal_rows = matches[0]
        if library != instance["library"] or master != instance["master"]:
            instance["symbol_metadata_error"] = "metadata-binding-mismatch"
            continue
        if parameterized is True:
            instance["symbol_metadata_error"] = "unsupported-parameterized-variant"
            continue
        if parameterized is not None:
            instance["symbol_metadata_error"] = "metadata-invalid-variant-status"
            continue
        if not isinstance(view, str) or not view or not isinstance(path, str):
            instance["symbol_metadata_error"] = "metadata-binding-unavailable"
            continue
        if (
            isinstance(magnification, bool)
            or not isinstance(magnification, (int, float))
            or not math.isfinite(float(magnification))
        ):
            instance["symbol_metadata_error"] = "metadata-invalid-magnification"
            continue
        if float(magnification) != 1.0:
            instance["symbol_metadata_error"] = "unsupported-magnification"
            continue
        anchors_by_terminal = _metadata_anchor_rows(terminal_rows)
        terminal_names = {terminal["name"] for terminal in instance["terminals"]}
        if set(anchors_by_terminal) != terminal_names:
            instance["symbol_metadata_error"] = "metadata-terminal-mismatch"
            continue
        instance["master_view"] = view
        instance["library_identity"] = library_identity(path)
        for terminal in instance["terminals"]:
            terminal["source_pin_anchors"] = anchors_by_terminal[terminal["name"]]


def terminal_index(snapshot: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    return {
        (instance["name"], terminal["name"]): terminal
        for instance in snapshot["instances"]
        for terminal in instance["terminals"]
    }


def review_wire(snapshot: dict[str, Any], endpoints: Any) -> dict[str, Any]:
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


def save_plan(
    payload: Any,
    demo: dict[str, Any],
    read_live: Callable[[str, str], dict[str, Any]],
    state_dir: Path,
) -> dict[str, Any]:
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
        "base_revision": revision(snapshot),
        "operation": operation,
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


class WorkbenchServer(ThreadingHTTPServer):
    symbol_packages: tuple[bytes, ...] = ()

    demo: dict[str, Any]
    read_live: Callable[[str, str], dict[str, Any]]
    state_dir: Path
    read_master: Callable[[dict[str, Any]], list[str]]

    def request_store(self) -> RequestStore:
        from .requests import RequestStore

        def snapshot(target: dict[str, Any]) -> dict[str, Any]:
            return self.demo if target["source"] == "demo" else self.read_live(target["library"], target["cell"])

        return RequestStore(self.state_dir, snapshot, getattr(self, "read_master", None))


class Handler(BaseHTTPRequestHandler):
    server: WorkbenchServer
    STATIC = {
        "/": ("index.html", "text/html"),
        "/workbench.css": ("workbench.css", "text/css"),
        "/workbench.js": ("workbench.js", "text/javascript"),
    }

    def setup(self) -> None:
        self.request.settimeout(30)
        super().setup()

    def check_origin(self) -> None:
        host = self.headers.get("Host", "")
        # Accept a different local port for SSH forwards ending at this server.
        if not re.fullmatch(r"(localhost|127\.0\.0\.1)(:\d+)?", host):
            raise WorkbenchError("Connect through a loopback URL.", HTTPStatus.FORBIDDEN)
        origin = self.headers.get("Origin")
        if self.command == "POST" and origin != f"http://{host}":
            raise WorkbenchError("A same-origin browser request is required.", HTTPStatus.FORBIDDEN)

    def send(self, status: HTTPStatus, content: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; object-src 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(content)

    def send_json(self, data: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send(status, json.dumps(data, allow_nan=False).encode(), "application/json")

    def route(self) -> None:
        self.check_origin()
        target = urlsplit(self.path)
        if self.command == "GET":
            if target.path == "/api/symbols":
                return self.send_json({"packages": [json.loads(package) for package in self.server.symbol_packages]})
            if target.path == "/api/requests":
                return self.send_json({"ok": True, "requests": self.server.request_store().list()})
            if re.fullmatch(r"/api/requests/[^/]+", target.path):
                return self.send_json(
                    {"ok": True, "request": self.server.request_store().show(target.path.rsplit("/", 1)[1])}
                )
            if target.path in self.STATIC:
                filename, content_type = self.STATIC[target.path]
                return self.send(HTTPStatus.OK, (ROOT / "static" / filename).read_bytes(), content_type)
            if target.path == "/api/demo":
                snapshot = self.server.demo
            elif target.path == "/api/schematic":
                query = parse_qs(target.query, max_num_fields=4)
                snapshot = self.server.read_live(query.get("library", [""])[0], query.get("cell", [""])[0])
            else:
                raise WorkbenchError("Page not found.", HTTPStatus.NOT_FOUND)
            return self.send_json({**snapshot, "revision": revision(snapshot), "checks": connectivity_checks(snapshot)})
        action = re.fullmatch(r"/api/requests/([^/]+)/(submit|cancel|approve|revise|reject|accept)", target.path)
        if target.path not in ("/api/requests", "/api/check") and not action:
            raise WorkbenchError("Page not found.", HTTPStatus.NOT_FOUND)
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise WorkbenchError("Invalid content length.") from None
        if not (0 if action else 1) <= length <= (8192 if action else 65536):
            raise WorkbenchError("Request size is outside the supported range.")
        if length and self.headers.get_content_type() != "application/json":
            raise WorkbenchError("Send a JSON request.")
        payload = json.loads(self.rfile.read(length)) if length else {}
        store = self.server.request_store()
        if target.path == "/api/check":
            from .requests import RequestError, name, obj

            obj(payload, ("target", "revision", "changes"))
            checked_target = obj(payload["target"], ("source", "library", "cell", "view"))
            if checked_target["source"] not in ("demo", "virtuoso"):
                raise RequestError("Unknown schematic source.")
            if checked_target["source"] == "virtuoso":
                for field in ("library", "cell", "view"):
                    name(checked_target[field])
            return self.send_json(check_preview(payload, store.snapshot_reader(checked_target)))
        if action:
            from .requests import obj

            obj(payload, (), ("comment",))
            return self.send_json(
                {"ok": True, "request": store.human(action[1], action[2], payload.get("comment", ""))}
            )
        return self.send_json({"ok": True, "request": store.create(payload)}, HTTPStatus.CREATED)

    def dispatch(self) -> None:
        from .requests import RequestError

        try:
            self.route()
        except RequestError as error:
            status = {3: HTTPStatus.NOT_FOUND, 4: HTTPStatus.CONFLICT, 6: HTTPStatus.CONFLICT}.get(
                error.exit_code, HTTPStatus.BAD_REQUEST
            )
            self.send_json({"ok": False, "error": {"code": error.code, "message": str(error)}}, status)
        except WorkbenchError as error:
            self.send_json({"error": str(error)}, error.status)
        except (json.JSONDecodeError, UnicodeDecodeError):
            self.send_json({"error": "Invalid JSON request."}, HTTPStatus.BAD_REQUEST)
        except (ImportError, OSError, RuntimeError, ValueError) as error:
            self.log_error("%s", error)
            self.send_json({"error": str(error)}, HTTPStatus.BAD_GATEWAY)

    do_GET = dispatch
    do_POST = dispatch


def main() -> None:
    from .cli import main as run

    run()


if __name__ == "__main__":
    main()
