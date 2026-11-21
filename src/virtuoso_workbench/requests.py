"""Validated design requests and POSIX, process-safe lifecycle storage."""

from __future__ import annotations

import copy
import fcntl
import hmac
import json
import os
import pwd
import re
import secrets
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

from .server import NAME, review_wire, revision

SCHEMA = "virtuoso-workbench/design-request-v1"
IDENTIFIER = re.compile(r"req-[0-9a-f]{12}")
HUMAN: dict[str, dict[str, str]] = {
    "submit": {"draft": "submitted"},
    "cancel": dict.fromkeys(("draft", "submitted", "claimed", "planned", "approved"), "cancelled"),
    "approve": {"planned": "approved"},
    "revise": {"planned": "claimed", "result-pending": "claimed"},
    "reject": {"planned": "rejected"},
    "accept": {"result-pending": "completed"},
}
AGENT: dict[str, dict[str, Optional[str]]] = {
    "release": {"claimed": "submitted"},
    "plan": {"claimed": "planned"},
    "start": {"approved": "executing"},
    "result": {"executing": "result-pending"},
    "fail": dict.fromkeys(("claimed", "approved", "executing"), "failed"),
    "heartbeat": dict.fromkeys(("claimed", "planned", "approved", "executing")),
}


class RequestError(ValueError):
    def __init__(self, message: str, code: str = "invalid-input", exit_code: int = 5) -> None:
        super().__init__(message)
        self.code = code
        self.exit_code = exit_code


def conflict(message: str) -> RequestError:
    return RequestError(message, "conflict", 4)


def os_user() -> str:
    return pwd.getpwuid(os.geteuid()).pw_name


def text(value: Any, field: str, maximum: int, minimum: int = 0) -> str:
    if not isinstance(value, str) or not minimum <= len(value) <= maximum or "\x00" in value:
        raise RequestError(f"{field} must be text of {minimum} to {maximum} characters.")
    return value


def name(value: Any) -> str:
    value = text(value, "Name", 128, 1)
    if not NAME.fullmatch(value):
        raise RequestError("Invalid schematic name.")
    return value


def obj(value: Any, required: tuple[str, ...], optional: tuple[str, ...] = ()) -> dict[str, Any]:
    if not isinstance(value, dict) or set(required) - value.keys() or value.keys() - set(required + optional):
        raise RequestError("Missing or unsupported object fields.")
    return value


def items(value: Any, maximum: int, minimum: int = 0) -> list[Any]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise RequestError(f"Expected a list of {minimum} to {maximum} items.")
    return value


def parameters(value: Any) -> dict[str, str]:
    if not isinstance(value, dict) or len(value) > 64:
        raise RequestError("Parameters must be an object with at most 64 entries.")
    return {name(key): text(item, "Parameter value", 64, 1) for key, item in value.items()}


def timestamp(now: float) -> str:
    return datetime.fromtimestamp(now, timezone.utc).isoformat().replace("+00:00", "Z")


def expired(request: dict[str, Any], now: float) -> bool:
    claim = request["claim"]
    return bool(claim and datetime.fromisoformat(claim["lease_expires_at"].replace("Z", "+00:00")).timestamp() <= now)


def public(request: dict[str, Any], now: float, reveal_token: bool = False) -> dict[str, Any]:
    result = copy.deepcopy(request)
    result["lease_expired"] = expired(result, now)
    if result["claim"] and not reveal_token:
        result["claim"].pop("token", None)
    return result


def validate_changes(
    changes: Any,
    snapshot: dict[str, Any],
    master_reader: Optional[Callable[[dict[str, Any]], list[str]]] = None,
) -> list[dict[str, Any]]:
    working = copy.deepcopy(snapshot)
    validated = []
    for raw in items(changes, 200, 1):
        if not isinstance(raw, dict):
            raise RequestError("Each change must be an object.")
        change = copy.deepcopy(raw)
        operation = change.get("op")
        instances = {instance["name"]: instance for instance in working["instances"]}

        def instance(key: str) -> dict[str, Any]:
            selected = instances.get(name(change[key]))
            if selected is None:
                raise RequestError("The instance does not exist at this point in the request.")
            return selected

        def terminal(selected: dict[str, Any], terminal_name: Any) -> dict[str, Any]:
            terminal_name = name(terminal_name)
            for candidate in selected["terminals"]:
                if candidate["name"] == terminal_name:
                    return candidate
            raise RequestError("The terminal does not exist on the instance.")

        if operation == "connect":
            obj(change, ("op", "endpoints"), ("net",))
            endpoints = items(change["endpoints"], 2, 2)
            for endpoint in endpoints:
                obj(endpoint, ("instance", "terminal"))
                name(endpoint["instance"])
                name(endpoint["terminal"])
            if "net" in change:
                name(change["net"])
            reviewed = review_wire(working, endpoints)
            used = {pin["net"] for selected in working["instances"] for pin in selected["terminals"]}
            used.update(pin["net"] for pin in working["pins"])
            used.update(wire["net"] for wire in working["wires"])
            requested_net = change.get("net")
            if requested_net in used and requested_net and requested_net not in reviewed["affected_nets"]:
                reviewed["affected_nets"] = sorted([*reviewed["affected_nets"], requested_net])
                reviewed["outcome"] = (
                    "merge-existing-nets" if len(reviewed["affected_nets"]) > 1 else "attach-to-existing-net"
                )
                reviewed["affected_top_level_pins"] = [
                    pin["name"] for pin in working["pins"] if pin["net"] in reviewed["affected_nets"]
                ]
            change.update({key: reviewed[key] for key in ("outcome", "affected_nets", "affected_top_level_pins")})
            net = change.get("net") or next(iter(reviewed["affected_nets"]), None)
            if net is None:
                suffix = 1
                while f"vwb_net_{suffix}" in used:
                    suffix += 1
                net = f"vwb_net_{suffix}"
                change["net"] = net
            affected = set(reviewed["affected_nets"])
            if net:
                for selected in working["instances"]:
                    for pin in selected["terminals"]:
                        if pin["net"] in affected:
                            pin["net"] = net
                for pin in working["pins"]:
                    if pin["net"] in affected:
                        pin["net"] = net
                for wire in working["wires"]:
                    if wire["net"] in affected:
                        wire["net"] = net
            for endpoint in endpoints:
                terminal(instances[endpoint["instance"]], endpoint["terminal"])["net"] = net
        elif operation == "disconnect":
            obj(change, ("op", "instance", "terminal"))
            terminal(instance("instance"), change["terminal"])["net"] = None
        elif operation == "add-instance":
            obj(change, ("op", "name", "library", "cell", "parameters"), ("view",))
            for field in ("name", "library", "cell"):
                name(change[field])
            change["view"] = name(change.get("view", "symbol"))
            change["parameters"] = parameters(change["parameters"])
            if change["name"] in instances:
                raise RequestError("The added instance name already exists.")
            matching = next(
                (
                    entry
                    for entry in snapshot["instances"]
                    if entry["library"] == change["library"]
                    and entry["master"] == change["cell"]
                    and change["view"] == "symbol"
                ),
                None,
            )
            terminals = [entry["name"] for entry in matching["terminals"]] if matching else []
            if not matching and master_reader:
                terminals = master_reader(change)
            working["instances"].append(
                {
                    "name": change["name"],
                    "library": change["library"],
                    "master": change["cell"],
                    "terminals": [{"name": name(entry), "net": None, "xy": None} for entry in terminals],
                }
            )
        elif operation == "remove-instance":
            obj(change, ("op", "name"))
            working["instances"].remove(instance("name"))
        elif operation == "set-parameters":
            obj(change, ("op", "instance", "parameters"))
            instance("instance")
            change["parameters"] = parameters(change["parameters"])
        elif operation == "add-pin":
            obj(change, ("op", "name", "direction", "net"))
            name(change["name"])
            name(change["net"])
            if change["direction"] not in ("input", "output", "inputOutput"):
                raise RequestError("Invalid pin direction.")
            if any(pin["name"] == change["name"] for pin in working["pins"]):
                raise RequestError("The added pin name already exists.")
            working["pins"].append({key: change[key] for key in ("name", "direction", "net")})
        else:
            raise RequestError("Unknown change operation.")
        validated.append(change)
    return validated


def validate_plan(payload: Any, actor: str, now: float) -> dict[str, Any]:
    obj(payload, ("summary", "steps"), ("risks",))
    result = {
        "agent": actor,
        "submitted_at": timestamp(now),
        "summary": text(payload["summary"], "Summary", 4000, 1),
        "steps": [text(step, "Step", 4000, 1) for step in items(payload["steps"], 100)],
    }
    if "risks" in payload:
        result["risks"] = text(payload["risks"], "Risks", 4000)
    return result


def validate_result(payload: Any, actor: str, now: float) -> dict[str, Any]:
    obj(payload, ("outcome", "summary", "metrics", "artifacts"), ("final_revision",))
    if payload["outcome"] not in ("met", "not-met", "partial", "error"):
        raise RequestError("Invalid result outcome.")
    result = copy.deepcopy(payload)
    text(result["summary"], "Summary", 8000, 1)
    for metric in items(result["metrics"], 200):
        obj(metric, ("name", "value"), ("unit", "target", "met"))
        text(metric["name"], "Metric name", 128, 1)
        text(metric["value"], "Metric value", 4000, 1)
        for field in ("unit", "target"):
            if field in metric:
                text(metric[field], field, 4000)
        if "met" in metric and not isinstance(metric["met"], bool):
            raise RequestError("Metric met must be a boolean.")
    for artifact in items(result["artifacts"], 200):
        text(artifact, "Artifact path", 4096, 1)
    if "final_revision" in result:
        text(result["final_revision"], "Final revision", 128, 1)
    result.update(agent=actor, submitted_at=timestamp(now))
    return result


class RequestStore:
    def __init__(
        self,
        state_dir: Path,
        snapshot_reader: Callable[[dict[str, Any]], dict[str, Any]],
        master_reader: Optional[Callable[[dict[str, Any]], list[str]]] = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.state_dir = state_dir
        self.directory = state_dir / "requests"
        self.snapshot_reader = snapshot_reader
        self.master_reader = master_reader
        self.clock = clock

    def identifier(self, identifier: str) -> str:
        if not IDENTIFIER.fullmatch(identifier):
            raise RequestError("Invalid request ID.")
        return identifier

    @contextmanager
    def lock(self, identifier: str) -> Iterator[None]:
        self.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.directory.mkdir(mode=0o700, exist_ok=True)
        self.state_dir.chmod(0o700)
        self.directory.chmod(0o700)
        descriptor = os.open(self.directory / (identifier + ".lock"), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            os.close(descriptor)

    def read(self, identifier: str) -> dict[str, Any]:
        path = self.directory / (self.identifier(identifier) + ".json")
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            raise RequestError("Request not found.", "not-found", 3) from None
        with os.fdopen(descriptor, encoding="utf-8") as handle:
            result: dict[str, Any] = json.load(handle)
        return result

    def write(self, request: dict[str, Any]) -> None:
        path = self.directory / (self.identifier(request["id"]) + ".json")
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.directory, delete=False) as handle:
            temporary = Path(handle.name)
            try:
                os.fchmod(handle.fileno(), 0o600)
                json.dump(request, handle, indent=2, allow_nan=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
                os.replace(temporary, path)
                descriptor = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            finally:
                temporary.unlink(missing_ok=True)

    def record(
        self,
        request: dict[str, Any],
        actor: str,
        event: str,
        destination: str,
        message: str,
        now: float,
    ) -> None:
        request["history"].append(
            {
                "at": timestamp(now),
                "actor": actor,
                "event": event,
                "from": request["status"],
                "to": destination,
                "message": message,
            }
        )
        request.update(status=destination, updated_at=timestamp(now))

    def create(self, payload: Any) -> dict[str, Any]:
        obj(payload, ("target", "revision", "title", "changes"), ("simulation", "acceptance", "note", "submit"))
        target = obj(payload["target"], ("source", "library", "cell", "view"))
        if target["source"] not in ("demo", "virtuoso"):
            raise RequestError("Unknown schematic source.")
        for field in ("library", "cell", "view"):
            text(target[field], field, 128, 1)
            if target["source"] == "virtuoso":
                name(target[field])
        snapshot = self.snapshot_reader(target)
        if any(target[key] != snapshot[key] for key in ("source", "library", "cell", "view")):
            raise RequestError("The target does not match the snapshot.")
        if payload["revision"] != revision(snapshot):
            raise RequestError("The schematic changed. Read a new snapshot.", "stale-revision", 6)
        if not isinstance(payload.get("submit", False), bool):
            raise RequestError("Submit must be a boolean.")
        now = self.clock()
        actor = os_user()
        request: dict[str, Any] = {
            "schema": SCHEMA,
            "id": "",
            "title": text(payload["title"], "Title", 120, 1),
            "status": "draft",
            "created_at": timestamp(now),
            "updated_at": timestamp(now),
            "created_by": actor,
            "target": copy.deepcopy(target),
            "base_revision": revision(snapshot),
            "changes": validate_changes(payload["changes"], snapshot, self.master_reader),
            "simulation": text(payload.get("simulation", ""), "Simulation", 4000),
            "acceptance": text(payload.get("acceptance", ""), "Acceptance", 4000),
            "note": text(payload.get("note", ""), "Note", 2000),
            "claim": None,
            "plan": None,
            "decision": None,
            "result": None,
            "history": [],
        }
        self.record(request, actor, "create", "draft", "", now)
        if payload.get("submit"):
            self.record(request, actor, "submit", "submitted", "", now)
        with self.lock(".queue"):
            while True:
                request["id"] = "req-" + secrets.token_hex(6)
                if not (self.directory / (request["id"] + ".json")).exists():
                    break
            self.write(request)
        return public(request, now)

    def show(self, identifier: str) -> dict[str, Any]:
        return public(self.read(identifier), self.clock())

    def list(self) -> list[dict[str, Any]]:
        if not self.directory.exists():
            return []
        summaries = []
        for path in sorted(self.directory.glob("req-*.json")):
            request = self.show(path.stem)
            summaries.append(
                {
                    key: request[key]
                    for key in ("id", "title", "status", "target", "created_at", "updated_at", "claim", "lease_expired")
                }
            )
        return sorted(summaries, key=lambda entry: (entry["created_at"], entry["id"]))

    def human(self, identifier: str, action: str, comment: str = "") -> dict[str, Any]:
        text(comment, "Comment", 2000)
        self.identifier(identifier)
        with self.lock(identifier):
            request = self.read(identifier)
            destination = HUMAN.get(action, {}).get(request["status"])
            if not destination:
                raise conflict("This human action is not allowed in the current state.")
            now = self.clock()
            if action in ("approve", "revise", "reject"):
                request["decision"] = {
                    "action": action,
                    "by": os_user(),
                    "at": timestamp(now),
                    "comment": comment,
                }
            if action == "revise":
                request["plan"] = None
                request["result"] = None
            self.record(request, os_user(), action, destination, comment, now)
            self.write(request)
        return public(request, now)

    def claim(
        self,
        identifier: Optional[str],
        actor: str,
        harness: str,
        lease: int = 1800,
    ) -> dict[str, Any]:
        text(actor, "Agent", 128, 1)
        text(harness, "Harness", 128, 1)
        self.validate_lease(lease)
        if identifier:
            self.identifier(identifier)
            return self.claim_one(identifier, actor, harness, lease)
        with self.lock(".queue"):
            for summary in self.list():
                if summary["status"] == "submitted" or (summary["status"] == "claimed" and summary["lease_expired"]):
                    try:
                        return self.claim_one(summary["id"], actor, harness, lease)
                    except RequestError as error:
                        if error.exit_code != 4:
                            raise
        raise RequestError("No request is available.", "not-found", 3)

    def claim_one(self, identifier: str, actor: str, harness: str, lease: int) -> dict[str, Any]:
        with self.lock(identifier):
            request = self.read(identifier)
            now = self.clock()
            if request["status"] != "submitted" and not (request["status"] == "claimed" and expired(request, now)):
                raise conflict("The request is not available for claim.")
            request["claim"] = {
                "agent": actor,
                "harness": harness,
                "claimed_at": timestamp(now),
                "lease_expires_at": timestamp(now + lease),
                "token": secrets.token_hex(24),
            }
            self.record(request, actor, "claim", "claimed", "", now)
            self.write(request)
        return public(request, now, reveal_token=True)

    def validate_lease(self, lease: int) -> None:
        if not isinstance(lease, int) or isinstance(lease, bool) or not 1 <= lease <= 604800:
            raise RequestError("Lease must be 1 to 604800 seconds.")

    def agent(
        self,
        identifier: str,
        action: str,
        actor: str,
        token: str,
        payload: Any = None,
        lease: int = 1800,
    ) -> dict[str, Any]:
        self.identifier(identifier)
        with self.lock(identifier):
            request = self.read(identifier)
            now = self.clock()
            claim = request["claim"]
            if (
                not claim
                or claim["agent"] != actor
                or not isinstance(token, str)
                or not re.fullmatch(r"[0-9a-f]{48}", token)
                or not hmac.compare_digest(claim["token"], token)
            ):
                raise conflict("The claim token or agent does not match.")
            if action not in AGENT or request["status"] not in AGENT[action]:
                raise conflict("This agent action is not allowed in the current state.")
            if action != "heartbeat" and expired(request, now):
                raise conflict("The lease expired. Renew it before this action.")
            message = ""
            if action == "plan":
                request["plan"] = validate_plan(payload, actor, now)
            elif action == "result":
                request["result"] = validate_result(payload, actor, now)
            elif action == "fail":
                message = text(payload, "Failure message", 2000, 1)
            elif action == "heartbeat":
                self.validate_lease(lease)
                claim["lease_expires_at"] = timestamp(now + lease)
            elif action == "release":
                request["claim"] = None
                request["plan"] = None
            destination = AGENT[action][request["status"]] or request["status"]
            self.record(request, actor, action, destination, message, now)
            self.write(request)
        return public(request, now)
