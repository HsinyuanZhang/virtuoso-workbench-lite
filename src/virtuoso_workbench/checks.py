"""Advisory connectivity checks for logical nets and snapshot wire geometry."""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

Point = list[float]
Segment = tuple[Point, Point]


def geometry_tolerance(snapshot: dict[str, Any]) -> float:
    lengths = [
        math.dist(start, end)
        for wire in snapshot["wires"]
        for start, end in zip(wire["points"], wire["points"][1:])
        if start != end
    ]
    return max(1e-15, min(1e-6, min(lengths) * 1e-4)) if lengths else 1e-6


def on_segment(point: Point, segment: Segment, tolerance: float) -> bool:
    start, end = segment
    if any(
        point[axis] < min(start[axis], end[axis]) - tolerance or point[axis] > max(start[axis], end[axis]) + tolerance
        for axis in (0, 1)
    ):
        return False
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = dx * dx + dy * dy
    if length == 0:
        return math.dist(point, start) <= tolerance
    fraction = max(0.0, min(1.0, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / length))
    return math.dist(point, [start[0] + fraction * dx, start[1] + fraction * dy]) <= tolerance


def joined(left: Segment, right: Segment, tolerance: float) -> bool:
    if any(
        max(left[0][axis], left[1][axis]) + tolerance < min(right[0][axis], right[1][axis])
        or max(right[0][axis], right[1][axis]) + tolerance < min(left[0][axis], left[1][axis])
        for axis in (0, 1)
    ):
        return False
    if any(
        on_segment(point, other, tolerance) for segment, other in ((left, right), (right, left)) for point in segment
    ):
        return True

    def side(segment: Segment, point: Point) -> float:
        start, end = segment
        return (end[0] - start[0]) * (point[1] - start[1]) - (end[1] - start[1]) * (point[0] - start[0])

    return (side(left, right[0]) > 0) != (side(left, right[1]) > 0) and (side(right, left[0]) > 0) != (
        side(right, left[1]) > 0
    )


def connectivity_checks(snapshot: dict[str, Any], tolerance: float | None = None) -> list[dict[str, Any]]:
    tolerance = geometry_tolerance(snapshot) if tolerance is None else tolerance
    if not math.isfinite(tolerance) or tolerance <= 0:
        raise ValueError("Geometry tolerance must be finite and positive.")
    findings: list[dict[str, Any]] = []
    members: dict[str, list[dict[str, Any]]] = defaultdict(list)
    locations: dict[str, list[Point]] = defaultdict(list)
    terminals: dict[str, int] = defaultdict(int)

    def report(code: str, severity: str, message: str, **fields: Any) -> None:
        findings.append({"code": code, "severity": severity, "message": message, **fields})

    def location(entry: dict[str, Any]) -> dict[str, Any]:
        return {"points": [list(entry["xy"])]} if entry.get("xy") is not None else {}

    for instance in snapshot["instances"]:
        for terminal in instance["terminals"]:
            fields = {"instance": instance["name"], "terminal": terminal["name"], **location(terminal)}
            net = terminal["net"]
            if not net:
                report("floating-terminal", "error", f"{instance['name']}.{terminal['name']} has no net.", **fields)
            else:
                members[net].append(fields)
                terminals[net] += 1
                if terminal.get("xy") is not None:
                    locations[net].append(terminal["xy"])
    for pin in snapshot["pins"]:
        fields = {"pin": pin["name"], **location(pin)}
        net = pin["net"]
        if not net:
            report("pin-without-net", "error", f"Pin {pin['name']} has no net.", **fields)
        else:
            members[net].append(fields)
            if pin.get("xy") is not None:
                locations[net].append(pin["xy"])
            if not terminals[net]:
                report(
                    "pin-net-unused",
                    "warning",
                    f"Pin {pin['name']} net {net} has no instance terminal.",
                    net=net,
                    **fields,
                )
    for net, entries in sorted(members.items()):
        if len(entries) == 1:
            report(
                "single-connection-net",
                "warning",
                f"Net {net} touches only one terminal or pin.",
                net=net,
                **entries[0],
            )

    by_net: dict[str, list[Segment]] = defaultdict(list)
    ends: dict[str, list[tuple[Point, int]]] = defaultdict(list)
    for wire in snapshot["wires"]:
        path = wire["points"]
        if len(path) < 2 or not wire["net"]:
            continue
        segments = by_net[wire["net"]]
        first = len(segments)
        segments.extend(zip(path, path[1:]))
        ends[wire["net"]].extend(((path[0], first), (path[-1], len(segments) - 1)))
    for net, segments in sorted(by_net.items()):
        dangling: list[Point] = []
        for point, own in ends[net]:
            if any(math.dist(point, other) <= tolerance for other in locations[net]):
                continue
            if any(index != own and on_segment(point, segment, tolerance) for index, segment in enumerate(segments)):
                continue
            if not any(math.dist(point, other) <= tolerance for other in dangling):
                dangling.append(point)
                report(
                    "dangling-wire-end",
                    "error",
                    f"Wire end on net {net} is not attached at {point}.",
                    net=net,
                    points=[list(point)],
                )

        parents = list(range(len(segments)))

        def root(index: int) -> int:
            while parents[index] != index:
                parents[index] = parents[parents[index]]
                index = parents[index]
            return index

        for index, segment in enumerate(segments):
            for previous in range(index):
                if root(index) != root(previous) and joined(segment, segments[previous], tolerance):
                    parents[root(index)] = root(previous)
        groups = {root(index) for index in range(len(segments))}
        if len(groups) > 1:
            report(
                "net-not-joined",
                "warning",
                f"Net {net} has {len(groups)} geometric wire groups. Net labels can join separate groups; labels are not in the snapshot.",
                net=net,
                points=[list(segments[index][0]) for index in sorted(groups)],
            )
    return findings


def check_preview(payload: Any, snapshot: dict[str, Any]) -> dict[str, Any]:
    from .requests import RequestError, apply_changes, obj, text
    from .server import revision

    obj(payload, ("target", "revision", "changes"))
    target = obj(payload["target"], ("source", "library", "cell", "view"))
    for key in target:
        text(target[key], key, 128, 1)
    if any(target[key] != snapshot[key] for key in target):
        raise RequestError("The target does not match the snapshot.")
    if payload["revision"] != revision(snapshot):
        raise RequestError("The schematic changed. Read a new snapshot.", "stale-revision", 6)
    _, preview = apply_changes(payload["changes"], snapshot, preview=True)
    return {
        "ok": True,
        "revision": revision(snapshot),
        "checks": connectivity_checks(preview),
        "tolerance": geometry_tolerance(preview),
    }
