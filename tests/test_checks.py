import copy
import json
import sys
from collections import Counter

import pytest
from test_http_cli import request
from test_request_interfaces import command

from virtuoso_workbench import cli
from virtuoso_workbench import server as app
from virtuoso_workbench.checks import check_preview, connectivity_checks, geometry_tolerance
from virtuoso_workbench.requests import RequestError, RequestStore, apply_changes


def schematic(terminals=(), pins=(), wires=()):
    return {
        "source": "demo",
        "library": "examples",
        "cell": "test",
        "view": "schematic",
        "instances": [{"name": "M1", "terminals": list(terminals)}],
        "pins": list(pins),
        "wires": list(wires),
    }


def terminal(name, net, xy):
    return {"name": name, "net": net, "xy": xy}


def wire(net, *points):
    return {"net": net, "points": list(points)}


def codes(snapshot, **options):
    return [finding["code"] for finding in connectivity_checks(snapshot, **options)]


@pytest.fixture
def problem_snapshot():
    return schematic(
        [terminal("OPEN", None, [0, 0]), terminal("LONE", "SOLO", [1, 0]), terminal("D", "N", [2, 0])],
        [terminal("NO_NET", None, [3, 0]), terminal("UNUSED", "EMPTY", [4, 0]), terminal("CONNECTED", "N", [2, 0])],
        [wire("N", [2, 0], [2, 1]), wire("N", [10, 0], [11, 0])],
    )


def test_synthetic_problem_fixture_and_input_immutability(problem_snapshot):
    before = copy.deepcopy(problem_snapshot)
    findings = connectivity_checks(problem_snapshot)
    assert Counter(entry["code"] for entry in findings) == {
        "floating-terminal": 1,
        "single-connection-net": 2,
        "pin-without-net": 1,
        "pin-net-unused": 1,
        "dangling-wire-end": 3,
        "net-not-joined": 1,
    }
    assert problem_snapshot == before
    for entry in findings:
        assert entry["message"] and entry["severity"] in ("error", "warning")
    groups = next(entry for entry in findings if entry["code"] == "net-not-joined")
    assert groups["severity"] == "warning" and "2 geometric wire groups" in groups["message"]


def test_demo_has_only_the_existing_unconnected_body_terminal(demo):
    assert connectivity_checks(demo) == [
        {
            "code": "floating-terminal",
            "severity": "error",
            "message": "MN3.B has no net.",
            "instance": "MN3",
            "terminal": "B",
            "points": [[1.7, -6]],
        }
    ]


def test_empty_and_clean_snapshots():
    assert connectivity_checks(schematic()) == []
    clean = schematic(
        [terminal("A", "N", [0, 0]), terminal("B", "N", [2, 0])], wires=[wire("N", [0, 0], [1, 0], [2, 0])]
    )
    assert connectivity_checks(clean) == []


@pytest.mark.parametrize("net", [None, ""])
def test_floating_terminal_without_geometry(net):
    assert connectivity_checks(schematic([terminal("A", net, None)])) == [
        {
            "code": "floating-terminal",
            "severity": "error",
            "message": "M1.A has no net.",
            "instance": "M1",
            "terminal": "A",
        }
    ]


def test_logical_membership_counts_terminals_and_pins_not_wires():
    instance = terminal("A", "N", [0, 0])
    assert codes(schematic([instance])) == ["single-connection-net"]
    assert connectivity_checks(schematic([instance], [terminal("P", "N", [0, 0])])) == []
    assert "single-connection-net" not in codes(schematic(wires=[wire("N", [0, 0], [1, 0])]))


def test_pin_checks_and_missing_locations():
    assert codes(schematic(pins=[terminal("P", None, None)])) == ["pin-without-net"]
    pins = [terminal("P1", "N", None), terminal("P2", "N", None)]
    assert codes(schematic(pins=pins)) == ["pin-net-unused", "pin-net-unused"]
    assert connectivity_checks(schematic([terminal("A", "N", None)], pins)) == []


@pytest.mark.parametrize(
    "branch", [wire("N", [1, 0], [1, 1]), wire("N", [0.5, 0], [1.5, 0]), wire("N", [1, -1], [1, 1])]
)
def test_shared_endpoints_interior_junctions_overlaps_and_crossings(branch):
    wires = [wire("N", [0, 0], [2, 0]), branch]
    terminals = [terminal(str(index), "N", point) for index, point in enumerate([[0, 0], [2, 0], *branch["points"]])]
    assert connectivity_checks(schematic(terminals, wires=wires)) == []


def test_same_net_required_for_terminal_and_wire_attachment():
    snapshot = schematic(
        [terminal("A", "OTHER", [0, 0]), terminal("B", "N", [1, 0])],
        wires=[wire("N", [0, 0], [1, 0]), wire("OTHER", [0, 0], [0, 1])],
    )
    dangling = [entry for entry in connectivity_checks(snapshot) if entry["code"] == "dangling-wire-end"]
    assert any(entry["net"] == "N" and entry["points"] == [[0, 0]] for entry in dangling)


def test_disconnected_labelled_net_is_a_warning_even_when_all_ends_have_terminals():
    points = [[0, 0], [1, 0], [3, 0], [4, 0]]
    snapshot = schematic(
        [terminal(str(index), "N", point) for index, point in enumerate(points)],
        wires=[wire("N", *points[:2]), wire("N", *points[2:])],
    )
    findings = connectivity_checks(snapshot)
    assert len(findings) == 1 and findings[0]["code"] == "net-not-joined"
    assert findings[0]["severity"] == "warning" and "2 geometric wire groups" in findings[0]["message"]


@pytest.mark.parametrize(
    "gap,expected", [(0.5e-6, []), (2e-6, ["dangling-wire-end", "dangling-wire-end", "net-not-joined"])]
)
def test_tolerance_for_wire_groups_and_dangling_ends(gap, expected):
    snapshot = schematic(
        [terminal("A", "N", [0, 0]), terminal("B", "N", [2, 0])],
        wires=[wire("N", [0, 0], [1, 0]), wire("N", [1 + gap, 0], [2, 0])],
    )
    assert codes(snapshot) == expected


@pytest.mark.parametrize("offset,expected", [(0.5e-6, False), (2e-6, True)])
def test_tolerance_for_pin_location_and_diagonal_segments(offset, expected):
    snapshot = schematic(
        [terminal("A", "N", [0, 0])], [terminal("P", "N", [1 + offset, 1])], [wire("N", [0, 0], [1, 1])]
    )
    assert ("dangling-wire-end" in codes(snapshot)) == expected


def test_fine_geometry_scales_tolerance_and_degenerate_paths_are_safe():
    parsed = app.parse_snapshot("SNAPSHOT\nW|N|((0.0625 0.0) (0.125 0.0))\nEND", "examples", "grid")
    assert parsed["wires"][0]["points"] == [[0.0625, 0.0], [0.125, 0.0]]
    fine = schematic(wires=[wire("N", [0, 0], [0.001, 0])])
    assert geometry_tolerance(fine) == pytest.approx(1e-7)
    assert geometry_tolerance(schematic()) == 1e-6
    snapshot = schematic(
        [terminal("A", "N", [0, 0]), terminal("B", "N", [0, 0])], wires=[wire("N", [0, 0], [0, 0]), wire("N")]
    )
    assert connectivity_checks(snapshot) == []


@pytest.mark.parametrize("tolerance", [0, -1, float("nan"), float("inf")])
def test_invalid_tolerance(tolerance):
    with pytest.raises(ValueError):
        connectivity_checks(schematic(), tolerance)


def test_preview_operations_merge_names_without_routing_or_mutating(demo, request_payload):
    before = copy.deepcopy(demo)
    changes = [
        {"op": "connect", "endpoints": [{"instance": "MN3", "terminal": "B"}, {"instance": "MN3", "terminal": "S"}]},
        {"op": "set-parameters", "instance": "MN3", "parameters": {"w": "2u"}},
    ]
    assert (
        check_preview({key: request_payload[key] for key in ("target", "revision")} | {"changes": changes}, demo)[
            "checks"
        ]
        == []
    )
    assert demo == before
    _, preview = apply_changes(request_payload["changes"], demo, preview=True)
    assert len(preview["wires"]) == len(demo["wires"])
    nets = {entry["net"] for instance in preview["instances"] for entry in instance["terminals"]}
    assert not {"NREF", "VOUT"}.issubset(nets)
    assert len({entry["net"] for entry in preview["pins"] if entry["name"] == "VOUT"}) == 1


def test_preview_disconnect_remove_add_pin_and_unknown_instance(demo):
    added = {"op": "add-instance", "name": "MNEW", "library": "generic", "cell": "nmos", "parameters": {}}
    _, preview = apply_changes(
        [added], demo, lambda change: pytest.fail("Preview must not query masters."), preview=True
    )
    assert preview["instances"][-1]["terminals"] == []
    _, preview = apply_changes(
        [
            added,
            {
                "op": "connect",
                "endpoints": [{"instance": "MNEW", "terminal": "G"}, {"instance": "MN3", "terminal": "B"}],
            },
        ],
        demo,
        preview=True,
    )
    assert connectivity_checks(preview) == []
    _, preview = apply_changes([{"op": "disconnect", "instance": "MP1", "terminal": "D"}], demo, preview=True)
    assert codes(preview).count("floating-terminal") == 2
    _, preview = apply_changes(
        [
            {"op": "remove-instance", "name": "MN3"},
            {"op": "add-pin", "name": "NEW", "direction": "output", "net": "NEW_NET"},
        ],
        demo,
        preview=True,
    )
    assert all(instance["name"] != "MN3" for instance in preview["instances"])
    assert {"pin-net-unused", "single-connection-net", "dangling-wire-end"} <= set(codes(preview))
    with pytest.raises(RequestError):
        apply_changes(
            [
                {
                    "op": "connect",
                    "endpoints": [{"instance": "MISSING", "terminal": "G"}, {"instance": "MN3", "terminal": "B"}],
                }
            ],
            demo,
            preview=True,
        )


def test_check_http_snapshot_preview_errors_and_no_state_writes(http_server, demo, request_payload):
    headers = {"Host": "localhost", "Origin": "http://localhost", "Content-Type": "application/json"}
    payload = {key: request_payload[key] for key in ("target", "revision")}
    payload["changes"] = []
    for path in ("/api/demo", "/api/schematic?library=examples&cell=amplifier"):
        status, _, body = request(http_server, "GET", path)
        snapshot = json.loads(body)
        assert status == 200 and snapshot["checks"] == connectivity_checks(demo)
        assert snapshot["revision"] == app.revision(
            {key: value for key, value in snapshot.items() if key not in ("revision", "checks")}
        )
    status, _, body = request(http_server, "POST", "/api/check", json.dumps(payload), headers)
    assert status == 200 and json.loads(body)["checks"] == connectivity_checks(demo)
    assert json.loads(body)["revision"] == app.revision(demo)
    assert request(http_server, "POST", "/api/check", json.dumps({**payload, "revision": "stale"}), headers)[0] == 409
    assert (
        request(http_server, "POST", "/api/check", json.dumps(payload), {**headers, "Origin": "http://evil.example"})[0]
        == 403
    )
    for invalid in (
        {},
        {**payload, "changes": [{}]},
        {**payload, "target": {**payload["target"], "source": "other"}},
        {**payload, "target": {**payload["target"], "cell": "different"}},
    ):
        assert request(http_server, "POST", "/api/check", json.dumps(invalid), headers)[0] == 400
    for size in ("0", "65537", "invalid"):
        assert request(http_server, "POST", "/api/check", "", {**headers, "Content-Length": size})[0] == 400
    body = json.dumps(payload)
    status, _, _ = request(http_server, "POST", "/api/check", body + " " * (65536 - len(body)), headers)
    assert status == 200
    assert not http_server.state_dir.exists()


def test_check_cli_json_request_files_and_errors(tmp_path, demo, request_payload):
    code, response = command(tmp_path, "check", "--source", "demo")
    assert code == 0 and response["checks"] == connectivity_checks(demo)
    store = RequestStore(tmp_path / "requests", lambda target: demo)
    stored = store.create(request_payload)
    for payload in (request_payload, stored, {"ok": True, "request": stored}):
        code, response = command(tmp_path, "check", "--file", "-", stdin=json.dumps(payload))
        assert code == 0 and response["ok"]
    path = tmp_path / "request.json"
    path.write_text(json.dumps(stored))
    assert command(tmp_path, "check", "--file", str(path))[0] == 0
    assert command(tmp_path, "check", "--file", "-", stdin=json.dumps({**request_payload, "revision": "old"}))[0] == 6
    assert command(tmp_path, "check", "--file", "-", stdin="{}")[0] == 5
    assert command(tmp_path, "check", "--file", "-", stdin="[]")[0] == 5
    assert command(tmp_path, "check", "--file", "-", stdin=json.dumps({**stored, "schema": "other"}))[0] == 5
    assert (
        command(tmp_path, "check", "--file", "-", stdin=json.dumps({"target": stored["target"], "changes": []}))[0] == 5
    )
    assert command(tmp_path, "check", "--source", "virtuoso")[0] == 2
    assert command(tmp_path, "check", "--file", str(tmp_path / "missing.json"))[0] == 3


def test_check_cli_uses_fake_live_reader(monkeypatch, tmp_path, demo, capsys):
    monkeypatch.setattr(
        app,
        "read_snapshot",
        lambda library, cell, host, port: {**demo, "source": "virtuoso", "library": library, "cell": cell},
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "vwb",
            "check",
            "--source",
            "virtuoso",
            "--library",
            "scratch",
            "--cell",
            "amp",
            "--state-dir",
            str(tmp_path),
            "--bridge-host",
            "127.0.0.1",
        ],
    )
    cli.main()
    assert json.loads(capsys.readouterr().out)["checks"] == connectivity_checks(demo)
