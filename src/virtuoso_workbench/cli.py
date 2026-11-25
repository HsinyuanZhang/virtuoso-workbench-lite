"""Non-interactive JSON command interface for people and local agents."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from threading import Lock
from typing import Any, Callable, NoReturn

from . import server as app
from .checks import check_preview, connectivity_checks
from .requests import AGENT, HUMAN, SCHEMA, RequestError, RequestStore, obj, os_user, text
from .symbols import SymbolPackageError, export_package, load_package, load_packages, summarize_package, write_package


def emit(value: dict[str, Any]) -> None:
    print(json.dumps(value, allow_nan=False))


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        emit({"ok": False, "error": {"code": "usage", "message": message}})
        raise SystemExit(2)

    def print_help(self, file: Any = None) -> None:
        emit({"ok": True, "help": self.format_help()})


def parser() -> Parser:
    common = Parser(add_help=False, argument_default=argparse.SUPPRESS)
    common.add_argument("--state-dir", type=Path)
    endpoint = common.add_mutually_exclusive_group()
    endpoint.add_argument("--bridge-host")
    endpoint.add_argument("--bridge-docker", metavar="CONTAINER")
    common.add_argument("--bridge-port", type=int)
    common.add_argument("--port", type=int)
    common.add_argument("--review", type=Path)
    common.add_argument("--agent")
    common.add_argument("--harness")
    common.add_argument("--token")
    common.add_argument("--lease", type=int)
    common.add_argument("--symbol-package", action="append", type=Path)
    root = Parser(description=__doc__, parents=[common])
    commands = root.add_subparsers(dest="command")
    commands.add_parser("serve", parents=[common])
    for action in ("snapshot", "check"):
        snapshot = commands.add_parser(action, parents=[common])
        snapshot.add_argument("--source", choices=("demo", "virtuoso"), default="demo")
        snapshot.add_argument("--library")
        snapshot.add_argument("--cell")
        if action == "check":
            snapshot.add_argument("--file")
    for entity in ("request", "agent"):
        group = commands.add_parser(entity, parents=[common])
        actions = group.add_subparsers(dest="action", required=True)
        for action in (
            ["list", "show", "create", "import-handoff", *HUMAN]
            if entity == "request"
            else ["list", "show", "claim", *AGENT, "guide"]
        ):
            command = actions.add_parser(action, parents=[common])
            if action == "claim":
                command.add_argument("id", nargs="?")
                command.add_argument("--next", action="store_true")
                command.add_argument("--wait", type=float, default=0)
            elif action == "import-handoff":
                command.add_argument("file")
            elif action not in ("list", "create", "guide"):
                command.add_argument("id")
            if action in ("create", "plan", "result"):
                command.add_argument("--file", required=True)
            if entity == "request" and action in HUMAN:
                command.add_argument("--comment", default="")
            if action == "fail":
                command.add_argument("--message", required=True)
    symbols = commands.add_parser("symbols", parents=[common])
    symbol_actions = symbols.add_subparsers(dest="symbol_action", required=True)
    export = symbol_actions.add_parser("export", parents=[common])
    export.add_argument("--library", required=True)
    export.add_argument("--cell", action="append", required=True)
    export.add_argument("--view", default="symbol")
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--source-version")
    export.add_argument("--force", action="store_true")
    inspect = symbol_actions.add_parser("inspect", parents=[common])
    inspect.add_argument("--file", type=Path, required=True)
    return root


def read_file(filename: str) -> Any:
    try:
        if filename == "-":
            return json.load(sys.stdin)
        return json.loads(Path(filename).read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise RequestError("Invalid JSON file.") from error
    except FileNotFoundError:
        raise RequestError("Input file not found.", "not-found", 3) from None


def read_master_terminals(change: dict[str, Any], host: str | None, port: int) -> list[str]:
    if not host:
        raise RequestError("Cannot validate an unknown master without a Bridge endpoint.")
    from virtuoso_bridge import VirtuosoClient, decode_skill_output

    library, cell, view = (app.validate_name(change[key]) for key in ("library", "cell", "view"))
    query = f'''let((cv result)
      cv=dbOpenCellViewByType("{library}" "{cell}" "{view}" nil "r")
      if(cv then unwindProtect(
        progn(result="TERMINALS\\n" foreach(term cv~>terminals result=strcat(result term~>name "\\n"))
          strcat(result "END")) dbClose(cv)) else "MISSING"))'''
    with VirtuosoClient.local(host=host, port=port, timeout=15) as client:
        result = client.execute_skill(query)
    if not result.ok:
        raise RuntimeError("Bridge could not read master terminals.")
    records = decode_skill_output(result.output).strip().splitlines()
    if records == ["MISSING"]:
        raise RequestError("The requested instance master view does not exist.")
    if not records or records[0] != "TERMINALS" or records[-1] != "END":
        raise RuntimeError("Bridge returned invalid master terminal data.")
    return records[1:-1]


def context(
    arguments: argparse.Namespace,
) -> tuple[dict[str, Any], Callable[[str, str], dict[str, Any]], RequestStore, tuple[bytes, ...]]:
    host = arguments.bridge_host
    if arguments.bridge_docker:
        inspected = subprocess.check_output(
            [
                "docker",
                "inspect",
                "--type",
                "container",
                "--format",
                "{{json .NetworkSettings.Networks}}",
                arguments.bridge_docker,
            ],
            text=True,
            timeout=10,
            stderr=subprocess.PIPE,
        )
        addresses = {network["IPAddress"] for network in json.loads(inspected).values() if network["IPAddress"]}
        if len(addresses) != 1:
            raise RequestError("The container must have one IPv4 address, or supply --bridge-host.")
        host = addresses.pop()
    demo = json.loads((app.ROOT / "demo.json").read_text(encoding="utf-8"))
    packages = tuple(
        json.dumps(package, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        for package in load_packages(arguments.symbol_package or [])
    )
    bridge_lock = Lock()

    def read_live(library: str, cell: str) -> dict[str, Any]:
        with bridge_lock:
            if packages:
                return app.read_snapshot(library, cell, host, arguments.bridge_port, include_symbol_metadata=True)
            return app.read_snapshot(library, cell, host, arguments.bridge_port)

    def snapshot(target: dict[str, Any]) -> dict[str, Any]:
        return demo if target["source"] == "demo" else read_live(target["library"], target["cell"])

    def master(change: dict[str, Any]) -> list[str]:
        with bridge_lock:
            return read_master_terminals(change, host, arguments.bridge_port)

    return demo, read_live, RequestStore(arguments.state_dir.resolve(), snapshot, master), packages


def execute_symbols(arguments: argparse.Namespace, parse: Parser) -> None:
    if arguments.symbol_action == "inspect":
        emit(summarize_package(load_package(arguments.file)))
        return
    host = arguments.bridge_host
    if arguments.bridge_docker:
        inspected = subprocess.check_output(
            [
                "docker",
                "inspect",
                "--type",
                "container",
                "--format",
                "{{json .NetworkSettings.Networks}}",
                arguments.bridge_docker,
            ],
            text=True,
            timeout=10,
            stderr=subprocess.PIPE,
        )
        addresses = {network["IPAddress"] for network in json.loads(inspected).values() if network["IPAddress"]}
        if len(addresses) != 1:
            raise RequestError("The container must have one IPv4 address, or supply --bridge-host.")
        host = addresses.pop()
    if not host:
        raise RequestError("Symbol export needs --bridge-host or --bridge-docker.")
    try:
        package = export_package(
            arguments.library,
            arguments.cell,
            host,
            arguments.bridge_port,
            arguments.view,
            arguments.source_version,
        )
        write_package(arguments.output, package, arguments.force)
    except SymbolPackageError as error:
        raise RequestError(str(error), "symbol-package", 5) from error
    except OSError as error:
        raise RequestError("Could not write symbol package.", "symbol-package", 5) from error
    emit({**summarize_package(package), "output": str(arguments.output)})


def import_handoff(payload: Any, store: RequestStore) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("schema") != app.PLAN_SCHEMA:
        raise RequestError("Unsupported handoff schema.")
    try:
        endpoints = [
            {key: endpoint[key] for key in ("instance", "terminal")} for endpoint in payload["operation"]["endpoints"]
        ]
        return store.create(
            {
                "target": payload["target"],
                "revision": payload["base_revision"],
                "title": "Imported wire request",
                "changes": [{"op": "connect", "endpoints": endpoints}],
                "note": payload.get("note", ""),
                "submit": False,
            }
        )
    except (KeyError, TypeError):
        raise RequestError("Invalid wire handoff.") from None


def execute(arguments: argparse.Namespace, parse: Parser) -> None:
    if arguments.command == "symbols":
        execute_symbols(arguments, parse)
        return
    demo, read_live, store, packages = context(arguments)
    if arguments.review:
        if arguments.command not in (None, "serve"):
            parse.error("--review cannot be combined with a subcommand.")
        plan = read_file(str(arguments.review))
        if not isinstance(plan, dict) or plan.get("schema") != app.PLAN_SCHEMA:
            raise RequestError("Unsupported handoff schema.")
        try:
            target = obj(plan["target"], ("source", "library", "cell", "view"))
            if target["source"] not in ("demo", "virtuoso"):
                raise RequestError("Unsupported schematic source.")
            snapshot = demo if target["source"] == "demo" else read_live(target["library"], target["cell"])
            if plan["base_revision"] != app.revision(snapshot):
                raise RequestError("The schematic changed.", "stale-revision", 6)
            operation = app.review_wire(snapshot, plan["operation"]["endpoints"])
            emit({"ok": True, "target": target, "operation": operation, "note": plan["note"]})
        except (KeyError, TypeError):
            raise RequestError("Invalid wire handoff.") from None
    elif arguments.command in (None, "serve"):
        with app.WorkbenchServer(("127.0.0.1", arguments.port), app.Handler) as server:
            server.demo, server.read_live, server.state_dir = demo, read_live, store.state_dir
            server.symbol_packages = packages
            if store.master_reader:
                server.read_master = store.master_reader
            emit({"ok": True, "url": f"http://127.0.0.1:{server.server_port}"})
            sys.stdout.flush()
            server.serve_forever()
    elif arguments.command in ("snapshot", "check"):
        if arguments.source == "virtuoso" and (not arguments.library or not arguments.cell):
            parse.error("Live snapshots need --library and --cell.")
        snapshot = demo if arguments.source == "demo" else read_live(arguments.library, arguments.cell)
        if arguments.command == "snapshot":
            emit(
                {
                    "ok": True,
                    "snapshot": {
                        **snapshot,
                        "revision": app.revision(snapshot),
                        "checks": connectivity_checks(snapshot),
                    },
                }
            )
        else:
            check_payload: dict[str, Any] = {
                "target": {key: snapshot[key] for key in ("source", "library", "cell", "view")},
                "revision": app.revision(snapshot),
                "changes": [],
            }
            if arguments.file:
                data = read_file(arguments.file)
                if isinstance(data, dict) and data.get("ok") is True and "request" in data:
                    data = data["request"]
                if not isinstance(data, dict):
                    raise RequestError("Check input must be a request object.")
                if "schema" in data and data["schema"] != SCHEMA:
                    raise RequestError("Unsupported request schema.")
                try:
                    check_payload = {
                        "target": data["target"],
                        "revision": data["base_revision"] if "base_revision" in data else data["revision"],
                        "changes": data["changes"],
                    }
                except KeyError:
                    raise RequestError("Check input needs target, revision, and changes.") from None
            emit(check_preview(check_payload, snapshot))
    elif arguments.action == "list":
        emit({"ok": True, "requests": store.list()})
    elif arguments.action == "show":
        emit({"ok": True, "request": store.show(arguments.id)})
    elif arguments.command == "request":
        if arguments.action == "create":
            request = store.create(read_file(arguments.file))
        elif arguments.action == "import-handoff":
            request = import_handoff(read_file(arguments.file), store)
        else:
            request = store.human(arguments.id, arguments.action, arguments.comment)
        emit({"ok": True, "request": request})
    elif arguments.action == "guide":
        emit({"ok": True, "guide": (app.ROOT / "agent-protocol.md").read_text(encoding="utf-8")})
    elif arguments.action == "claim":
        if bool(arguments.id) == bool(arguments.next):
            parse.error("Supply an ID or --next, but not both.")
        if not 0 <= arguments.wait <= 86400:
            raise RequestError("Wait must be 0 to 86400 seconds.")
        deadline = time.monotonic() + arguments.wait
        while True:
            try:
                request = store.claim(arguments.id, arguments.agent, arguments.harness, arguments.lease)
                break
            except RequestError as error:
                if error.exit_code not in (3, 4) or arguments.wait == 0:
                    raise
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RequestError("Claim wait timed out.", "timeout", 7) from None
                time.sleep(min(0.1, remaining))
        emit({"ok": True, "request": request})
    else:
        if not arguments.token:
            parse.error("This agent action needs --token.")
        payload = read_file(arguments.file) if arguments.action in ("plan", "result") else None
        if arguments.action == "fail":
            payload = arguments.message
        request = store.agent(
            arguments.id, arguments.action, arguments.agent, arguments.token, payload, arguments.lease
        )
        emit({"ok": True, "request": request})


def main() -> None:
    parse = parser()
    arguments = parse.parse_args()
    defaults = {
        "state_dir": Path(os.environ.get("VWB_STATE_DIR", str(Path.cwd() / ".workbench"))),
        "agent": os.environ.get("VWB_AGENT", os_user()),
        "harness": os.environ.get("VWB_HARNESS", "unknown"),
        "lease": 1800,
        "port": 8765,
        "bridge_port": 65432,
        "bridge_host": None,
        "bridge_docker": None,
        "symbol_package": [],
        "review": None,
        "token": None,
    }
    for key, value in defaults.items():
        if not hasattr(arguments, key):
            setattr(arguments, key, value)
    try:
        text(arguments.agent, "Agent", 128, 1)
        text(arguments.harness, "Harness", 128, 1)
        if arguments.bridge_host and arguments.bridge_docker:
            parse.error("Supply only one Bridge endpoint.")
        execute(arguments, parse)
    except RequestError as error:
        emit({"ok": False, "error": {"code": error.code, "message": str(error)}})
        raise SystemExit(error.exit_code) from None
    except (ValueError, TypeError, KeyError) as error:
        emit({"ok": False, "error": {"code": "invalid-input", "message": str(error)}})
        raise SystemExit(5) from None
    except Exception as error:
        emit({"ok": False, "error": {"code": "internal", "message": str(error)}})
        raise SystemExit(1) from None
