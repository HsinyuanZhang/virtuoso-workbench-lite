import copy
import json
import multiprocessing
import os
import pwd
from pathlib import Path

import jsonschema
import pytest

from virtuoso_workbench import server as app
from virtuoso_workbench.requests import AGENT, HUMAN, RequestError, RequestStore, validate_changes

STATUSES = (
    "draft",
    "submitted",
    "claimed",
    "planned",
    "approved",
    "executing",
    "result-pending",
    "completed",
    "rejected",
    "cancelled",
    "failed",
)
HUMAN_EDGES = {
    ("submit", "draft"),
    *(("cancel", state) for state in STATUSES[:5]),
    ("approve", "planned"),
    ("revise", "planned"),
    ("revise", "result-pending"),
    ("reject", "planned"),
    ("accept", "result-pending"),
}
AGENT_EDGES = {
    ("release", "claimed"),
    ("plan", "claimed"),
    ("start", "approved"),
    ("result", "executing"),
    ("fail", "claimed"),
    ("fail", "approved"),
    ("fail", "executing"),
    *(("heartbeat", state) for state in ("claimed", "planned", "approved", "executing")),
}
PLAN = {"summary": "Use a scratch copy.", "steps": ["Copy the cell.", "Run the checks."], "risks": "Bias can change."}
RESULT = {
    "outcome": "met",
    "summary": "Checks passed.",
    "metrics": [{"name": "gain", "value": "42", "unit": "dB", "target": "40", "met": True}],
    "artifacts": ["results/report.txt", "/scratch/log.txt"],
    "final_revision": "sha256:example",
}


@pytest.fixture
def clock():
    return [1700000000.0]


@pytest.fixture
def store(tmp_path, demo, clock):
    return RequestStore(tmp_path / "state", lambda target: copy.deepcopy(demo), clock=lambda: clock[0])


def claimed(store, payload):
    request = store.create({**payload, "submit": True})
    request = store.claim(request["id"], "worker", "pytest")
    return request["id"], request["claim"]["token"]


def set_state(store, identifier, state):
    with store.lock(identifier):
        request = store.read(identifier)
        request["status"] = state
        store.write(request)


@pytest.mark.parametrize("action,state", [(a, s) for a in HUMAN for s in STATUSES])
def test_every_human_transition(store, request_payload, action, state):
    identifier, token = claimed(store, request_payload)
    set_state(store, identifier, state)
    before = store.read(identifier)
    if (action, state) not in HUMAN_EDGES:
        with pytest.raises(RequestError) as error:
            store.human(identifier, action)
        assert error.value.exit_code == 4
        assert store.read(identifier) == before
    else:
        result = store.human(identifier, action, "Human review")
        assert (
            result["status"]
            == {
                "submit": "submitted",
                "cancel": "cancelled",
                "approve": "approved",
                "revise": "claimed",
                "reject": "rejected",
                "accept": "completed",
            }[action]
        )
        assert len(result["history"]) == len(before["history"]) + 1
        assert result["history"][-1]["from"] == state
        assert result["history"][-1]["to"] == result["status"]
        if action == "revise":
            assert store.read(identifier)["claim"]["token"] == token
            assert result["plan"] is None and result["result"] is None


@pytest.mark.parametrize("action,state", [(a, s) for a in AGENT for s in STATUSES])
def test_every_agent_transition(store, request_payload, action, state):
    identifier, token = claimed(store, request_payload)
    set_state(store, identifier, state)
    before = store.read(identifier)
    payload = {"plan": PLAN, "result": RESULT, "fail": "Tool failed."}.get(action)
    if (action, state) not in AGENT_EDGES:
        with pytest.raises(RequestError) as error:
            store.agent(identifier, action, "worker", token, payload)
        assert error.value.exit_code == 4
        assert store.read(identifier) == before
    else:
        result = store.agent(identifier, action, "worker", token, payload)
        assert (
            result["status"]
            == {
                "release": "submitted",
                "plan": "planned",
                "start": "executing",
                "result": "result-pending",
                "fail": "failed",
                "heartbeat": state,
            }[action]
        )
        assert len(result["history"]) == len(before["history"]) + 1
        assert result["history"][-1]["event"] == action
        assert not result["claim"] or "token" not in result["claim"]


@pytest.mark.parametrize(
    "change",
    [
        {
            "op": "connect",
            "endpoints": [{"instance": "MN1", "terminal": "G"}, {"instance": "MN2", "terminal": "G"}],
            "net": "NEW",
        },
        {"op": "disconnect", "instance": "MN1", "terminal": "G"},
        {"op": "add-instance", "name": "MN4", "library": "generic", "cell": "nmos", "parameters": {"w": "4u"}},
        {"op": "remove-instance", "name": "MN1"},
        {"op": "set-parameters", "instance": "MN1", "parameters": {}},
        {"op": "add-pin", "name": "OUT2", "direction": "output", "net": "NEW"},
    ],
)
def test_each_operation_valid(demo, change):
    before = copy.deepcopy(demo)
    result = validate_changes([change], demo)
    assert result[0]["op"] == change["op"]
    assert demo == before
    schema = json.loads((app.ROOT / "schemas/design-request-v1.json").read_text())
    jsonschema.Draft202012Validator({"$ref": "#/$defs/change", "$defs": schema["$defs"]}).validate(result[0])


@pytest.mark.parametrize(
    "change",
    [
        None,
        {},
        {"op": "unknown"},
        {"op": "connect", "endpoints": []},
        {"op": "connect", "endpoints": [{"instance": "MN1", "terminal": "G"}] * 2},
        {
            "op": "connect",
            "endpoints": [{"instance": "missing", "terminal": "G"}, {"instance": "MN1", "terminal": "G"}],
        },
        {
            "op": "connect",
            "endpoints": [{"instance": "MN2", "terminal": "missing"}, {"instance": "MN1", "terminal": "G"}],
        },
        {"op": "disconnect", "instance": "missing", "terminal": "G"},
        {"op": "disconnect", "instance": "MN1", "terminal": "missing"},
        {"op": "add-instance", "name": "MN1", "library": "generic", "cell": "nmos", "parameters": {}},
        {"op": "add-instance", "name": "MN4", "library": "bad/name", "cell": "nmos", "parameters": {}},
        {"op": "remove-instance", "name": "missing"},
        {"op": "set-parameters", "instance": "missing", "parameters": {}},
        {"op": "set-parameters", "instance": "MN1", "parameters": {"bad/name": "4u"}},
        {"op": "set-parameters", "instance": "MN1", "parameters": {"w": 4}},
        {"op": "set-parameters", "instance": "MN1", "parameters": {"w": ""}},
        {"op": "set-parameters", "instance": "MN1", "parameters": {"w": "x" * 65}},
        {"op": "set-parameters", "instance": "MN1", "parameters": {str(i): "1" for i in range(65)}},
        {"op": "add-pin", "name": "VDD", "direction": "input", "net": "VDD"},
        {"op": "add-pin", "name": "OUT2", "direction": "wrong", "net": "N"},
        {"op": "add-pin", "name": "OUT2", "direction": "output", "net": "bad/net"},
    ],
)
def test_each_operation_invalid(demo, change):
    with pytest.raises(ValueError):
        validate_changes([change], demo)


def test_ordered_changes_and_added_master_terminals(demo, endpoints):
    changes = [
        {"op": "add-instance", "name": "MN4", "library": "generic", "cell": "nmos", "parameters": {}},
        {"op": "set-parameters", "instance": "MN4", "parameters": {"w": "2u"}},
        {"op": "connect", "endpoints": [{"instance": "MN4", "terminal": "G"}, {"instance": "MN1", "terminal": "G"}]},
        {"op": "disconnect", "instance": "MN4", "terminal": "G"},
        {"op": "remove-instance", "name": "MN4"},
    ]
    assert validate_changes(changes, demo)[2]["outcome"] == "attach-to-existing-net"
    with pytest.raises(ValueError):
        validate_changes(changes + [changes[1]], demo)
    added = {**changes[0], "cell": "newmaster", "view": "custom"}
    assert validate_changes([added, changes[2]], demo, lambda change: ["G"])[1]["outcome"] == "attach-to-existing-net"
    with pytest.raises(ValueError):
        validate_changes([added, changes[2]], demo, lambda change: ["D"])
    merge = {"op": "connect", "endpoints": endpoints}
    result = validate_changes([merge, merge], demo)
    assert result[0]["outcome"] == "merge-existing-nets"
    assert result[1]["outcome"] == "already-connected"


def test_new_net_and_rename_are_reflected_in_later_changes(demo, endpoints):
    disconnects = [{"op": "disconnect", **endpoint} for endpoint in endpoints]
    connect = {"op": "connect", "endpoints": endpoints}
    result = validate_changes([*disconnects, connect, connect], demo)
    assert result[2]["outcome"] == "create-net"
    assert result[2]["net"].startswith("vwb_net_")
    assert result[3]["outcome"] == "already-connected"
    assert validate_changes([{**connect, "net": "RENAMED"}, connect], demo)[1]["affected_nets"] == ["RENAMED"]
    result = validate_changes([*disconnects, {**connect, "net": "VDD"}], demo)[-1]
    assert result["outcome"] == "attach-to-existing-net"
    assert result["affected_nets"] == ["VDD"]
    assert "VDD" in result["affected_top_level_pins"]


@pytest.mark.parametrize(
    "update",
    [
        {"title": ""},
        {"title": "x" * 121},
        {"changes": []},
        {"changes": [{}] * 201},
        {"simulation": "x" * 4001},
        {"acceptance": "x" * 4001},
        {"note": "x" * 2001},
        {"submit": 1},
        {"extra": True},
        {"title": "bad\x00title"},
        {"target": {"source": "unknown", "library": "a", "cell": "b", "view": "schematic"}},
        {"target": {"source": "demo", "library": "wrong", "cell": "wrong", "view": "schematic"}},
    ],
)
def test_invalid_create_does_not_write(store, request_payload, update):
    with pytest.raises(RequestError):
        store.create({**request_payload, **update})
    assert not store.state_dir.exists()


def test_stale_revision(store, request_payload):
    with pytest.raises(RequestError) as error:
        store.create({**request_payload, "revision": "stale"})
    assert error.value.exit_code == 6


def test_human_identity_uses_process_user_not_environment(store, request_payload, monkeypatch):
    monkeypatch.setenv("LOGNAME", "not-the-process-user")
    monkeypatch.setenv("USER", "not-the-process-user")
    request = store.create(request_payload)
    actual = pwd.getpwuid(os.geteuid()).pw_name
    assert request["created_by"] == actual
    result = store.human(request["id"], "submit")
    assert result["history"][-1]["actor"] == actual


@pytest.mark.parametrize("state", STATUSES)
def test_claim_allowed_only_submitted_or_expired_claimed(store, request_payload, clock, state):
    identifier, token = claimed(store, request_payload)
    set_state(store, identifier, state)
    if state != "submitted":
        with pytest.raises(RequestError):
            store.claim(identifier, "other", "pytest")
    clock[0] += 1800
    if state in ("submitted", "claimed"):
        result = store.claim(identifier, "other", "pytest")
        assert result["claim"]["token"] != token
    else:
        with pytest.raises(RequestError):
            store.claim(identifier, "other", "pytest")


def test_tokens_expiry_and_renewal(store, request_payload, clock):
    identifier, token = claimed(store, request_payload)
    for actor, supplied in [("worker", "wrong"), ("other", token), ("worker", None), ("worker", "incorrect-令牌")]:
        with pytest.raises(RequestError) as error:
            store.agent(identifier, "plan", actor, supplied, PLAN)
        assert error.value.exit_code == 4
    assert "token" not in store.show(identifier)["claim"]
    assert "token" not in store.list()[0]["claim"]
    clock[0] += 1800
    assert store.show(identifier)["lease_expired"] is True
    with pytest.raises(RequestError):
        store.agent(identifier, "plan", "worker", token, PLAN)
    assert store.agent(identifier, "heartbeat", "worker", token)["lease_expired"] is False
    clock[0] += 1800
    store.claim(identifier, "other", "pytest")
    with pytest.raises(RequestError):
        store.agent(identifier, "heartbeat", "worker", token)


@pytest.mark.parametrize("lease", [0, -1, 604801, True, "30"])
def test_invalid_leases(store, request_payload, lease):
    request = store.create({**request_payload, "submit": True})
    with pytest.raises(RequestError):
        store.claim(request["id"], "worker", "pytest", lease)


@pytest.mark.parametrize(
    "action,payload",
    [
        ("plan", {}),
        ("plan", {**PLAN, "summary": "x" * 4001}),
        ("plan", {**PLAN, "steps": ["step"] * 101}),
        ("plan", {**PLAN, "steps": [3]}),
        ("plan", {**PLAN, "risks": 3}),
        ("result", {**RESULT, "outcome": "unknown"}),
        ("result", {**RESULT, "summary": "x" * 8001}),
        ("result", {**RESULT, "metrics": [{"name": "gain", "value": 42}]}),
        ("result", {**RESULT, "metrics": [{"name": "gain", "value": "42", "met": "yes"}]}),
        ("result", {**RESULT, "artifacts": [3]}),
        ("result", {**RESULT, "final_revision": ""}),
        ("fail", ""),
    ],
)
def test_invalid_agent_payload_does_not_write(store, request_payload, action, payload):
    identifier, token = claimed(store, request_payload)
    if action == "result":
        set_state(store, identifier, "executing")
    before = store.read(identifier)
    with pytest.raises(RequestError):
        store.agent(identifier, action, "worker", token, payload)
    assert store.read(identifier) == before


@pytest.mark.parametrize("artifacts", [None, [], ["results/report.txt"]])
def test_result_artifacts_default_and_schema(store, request_payload, artifacts):
    identifier, token = claimed(store, request_payload)
    set_state(store, identifier, "executing")
    payload = {key: value for key, value in RESULT.items() if key != "artifacts"}
    if artifacts is not None:
        payload["artifacts"] = artifacts
    before = copy.deepcopy(payload)
    result = store.agent(identifier, "result", "worker", token, payload)["result"]
    assert payload == before
    assert result["artifacts"] == (artifacts or [])
    assert store.read(identifier)["result"] == result
    schema = json.loads((app.ROOT / "schemas/design-request-v1.json").read_text())
    validator = jsonschema.Draft202012Validator({"$ref": "#/$defs/result", "$defs": schema["$defs"]})
    validator.validate(result)
    validator.validate({key: value for key, value in result.items() if key != "artifacts"})


@pytest.mark.parametrize("artifacts", [None, "", {}, [3], [""]])
def test_invalid_explicit_artifacts(store, request_payload, artifacts):
    identifier, token = claimed(store, request_payload)
    set_state(store, identifier, "executing")
    before = store.read(identifier)
    with pytest.raises(RequestError):
        store.agent(identifier, "result", "worker", token, {**RESULT, "artifacts": artifacts})
    assert store.read(identifier) == before


def test_atomic_write_modes_and_cleanup(store, request_payload, monkeypatch):
    calls = []
    fsync, replace = os.fsync, os.replace

    def sync(descriptor):
        calls.append("sync")
        return fsync(descriptor)

    def move(source, target):
        calls.append("replace")
        assert source.parent == target.parent == store.directory
        json.loads(source.read_text())
        return replace(source, target)

    monkeypatch.setattr(os, "fsync", sync)
    monkeypatch.setattr(os, "replace", move)
    request = store.create(request_payload)
    assert calls == ["sync", "replace", "sync"]
    assert store.state_dir.stat().st_mode & 0o777 == 0o700
    for path in store.directory.iterdir():
        assert path.stat().st_mode & 0o777 == 0o600
    before = store.read(request["id"])

    def fail(*args):
        raise OSError("disk error")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError):
        store.human(request["id"], "submit")
    assert store.read(request["id"]) == before
    assert not list(store.directory.glob("tmp*"))


def claim_worker(root, barrier, queue):
    demo = json.loads((app.ROOT / "demo.json").read_text())
    store = RequestStore(Path(root), lambda target: demo)
    barrier.wait(timeout=20)
    try:
        request = store.claim(None, "worker-" + str(os.getpid()), "pytest")
        queue.put((0, request["id"]))
    except RequestError as error:
        queue.put((error.exit_code, None))


def test_eight_processes_claim_next_exactly_three_winners(store, request_payload):
    identifiers = {store.create({**request_payload, "submit": True})["id"] for _ in range(3)}
    context = multiprocessing.get_context("spawn")
    barrier, queue = context.Barrier(8), context.Queue()
    processes = [context.Process(target=claim_worker, args=(str(store.state_dir), barrier, queue)) for _ in range(8)]
    try:
        for process in processes:
            process.start()
        results = [queue.get(timeout=30) for _ in processes]
        for process in processes:
            process.join(timeout=10)
            assert process.exitcode == 0
        winners = [identifier for code, identifier in results if code == 0]
        assert len(winners) == len(set(winners)) == 3
        assert set(winners) == identifiers
        assert sum(code == 3 for code, _ in results) == 5
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join()
        queue.close()


def test_schema_lifecycle_examples_and_packaged_guide(store, request_payload):
    schema = json.loads((app.ROOT / "schemas/design-request-v1.json").read_text())
    jsonschema.Draft202012Validator.check_schema(schema)
    validator = jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())
    request = store.create(request_payload)
    identifier = request["id"]
    validator.validate(store.read(identifier))
    store.human(identifier, "submit")
    validator.validate(store.read(identifier))
    claim = store.claim(identifier, "worker", "pytest")
    token = claim["claim"]["token"]
    for actor, action in [
        ("agent", "plan"),
        ("human", "revise"),
        ("agent", "plan"),
        ("human", "approve"),
        ("agent", "start"),
        ("agent", "result"),
        ("human", "revise"),
        ("agent", "plan"),
        ("human", "approve"),
        ("agent", "start"),
        ("agent", "result"),
        ("human", "accept"),
    ]:
        if actor == "agent":
            store.agent(identifier, action, "worker", token, {"plan": PLAN, "result": RESULT}.get(action))
        else:
            store.human(identifier, action)
        validator.validate(store.read(identifier))
    root = Path(__file__).resolve().parents[1]
    validator.validate(json.loads((root / "docs/examples/design-request-v1.json").read_text()))


def test_agent_protocol_copies_are_identical():
    root = Path(__file__).resolve().parents[1]
    assert (root / "docs/agent-protocol.md").read_bytes() == (app.ROOT / "agent-protocol.md").read_bytes()


@pytest.mark.parametrize("identifier", ["../outside", "req-0", "req-" + "A" * 12, ""])
def test_request_id_validation(store, identifier):
    with pytest.raises(RequestError):
        store.show(identifier)
