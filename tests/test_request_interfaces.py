import copy
import io
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest
from test_http_cli import request

from virtuoso_workbench import cli
from virtuoso_workbench import server as app


def command(tmp_path, *arguments, stdin=None):
    result = subprocess.run(
        [sys.executable, "-m", "virtuoso_workbench", "--state-dir", str(tmp_path / "state"), *arguments],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert len(result.stdout.splitlines()) == 1, result
    return result.returncode, json.loads(result.stdout)


def test_cli_end_to_end_and_stdin(tmp_path, request_payload):
    code, response = command(
        tmp_path, "request", "create", "--file", "-", stdin=json.dumps({**request_payload, "submit": True})
    )
    assert code == 0 and response["ok"]
    identifier = response["request"]["id"]
    code, response = command(
        tmp_path, "agent", "claim", "--next", "--agent", "worker", "--harness", "pi", "--lease", "60"
    )
    assert code == 0 and response["request"]["claim"]["harness"] == "pi"
    token = response["request"]["claim"]["token"]
    assert command(tmp_path, "agent", "heartbeat", identifier, "--agent", "worker", "--token", "wrong")[0] == 4
    for entity in ("request", "agent"):
        for action in ("list", "show"):
            code, response = command(tmp_path, entity, action, *([identifier] if action == "show" else []))
            assert code == 0
            assert token not in json.dumps(response)
    for action, extra in [
        ("heartbeat", []),
        ("plan", ["--file", "-"]),
    ]:
        code, response = command(
            tmp_path,
            "agent",
            action,
            identifier,
            "--agent",
            "worker",
            "--token",
            token,
            *extra,
            stdin='{"summary":"Use scratch.","steps":["Copy."]}',
        )
        assert code == 0 and token not in json.dumps(response)
    assert command(tmp_path, "request", "approve", identifier)[0] == 0
    assert command(tmp_path, "agent", "start", identifier, "--agent", "worker", "--token", token)[0] == 0
    code, response = command(
        tmp_path,
        "agent",
        "result",
        identifier,
        "--agent",
        "worker",
        "--token",
        token,
        "--file",
        "-",
        stdin='{"outcome":"partial","summary":"One check remains.","metrics":[]}',
    )
    assert code == 0 and response["request"]["status"] == "result-pending"
    assert response["request"]["result"]["artifacts"] == []
    code, response = command(tmp_path, "request", "accept", identifier)
    assert code == 0 and response["request"]["status"] == "completed"


@pytest.mark.parametrize(
    "arguments,stdin,expected",
    [
        (["agent", "claim"], None, 2),
        (["agent", "claim", "--next", "req-0123456789ab"], None, 2),
        (["agent", "claim", "--next"], None, 3),
        (["agent", "claim", "--next", "--wait", "0.1"], None, 7),
        (["agent", "claim", "--next", "--wait", "nan"], None, 5),
        (["request", "show", "req-0123456789ab"], None, 3),
        (["request", "show", "../outside"], None, 5),
        (["request", "create", "--file", "missing.json"], None, 3),
        (["request", "create", "--file", "-"], "{bad", 5),
        (["request", "create", "--file", "-"], "{}", 5),
        (["request", "create", "--file", "-"], "null", 5),
        (["agent", "start", "req-0123456789ab"], None, 2),
        (["snapshot", "--source", "virtuoso"], None, 2),
        (["nonsense"], None, 2),
    ],
)
def test_cli_json_error_codes(tmp_path, arguments, stdin, expected):
    code, response = command(tmp_path, *arguments, stdin=stdin)
    assert code == expected
    assert response["ok"] is False
    assert set(response["error"]) == {"code", "message"}


def test_cli_conflict_stale_and_internal(tmp_path, request_payload, monkeypatch, capsys):
    code, response = command(tmp_path, "request", "create", "--file", "-", stdin=json.dumps(request_payload))
    identifier = response["request"]["id"]
    assert command(tmp_path, "request", "approve", identifier)[0] == 4
    assert (
        command(
            tmp_path, "request", "create", "--file", "-", stdin=json.dumps({**request_payload, "revision": "stale"})
        )[0]
        == 6
    )
    monkeypatch.setattr(sys, "argv", ["vwb", "agent", "guide"])

    def fail(*args):
        raise OSError("disk unavailable")

    monkeypatch.setattr(cli, "context", fail)
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "internal"


def test_cli_environment_defaults_and_option_positions(tmp_path, request_payload, monkeypatch, capsys):
    monkeypatch.setenv("VWB_STATE_DIR", str(tmp_path / "env-state"))
    monkeypatch.setenv("VWB_AGENT", "env-worker")
    monkeypatch.setenv("VWB_HARNESS", "env-harness")
    monkeypatch.setattr(sys, "argv", ["vwb", "request", "create", "--file", "-"])
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({**request_payload, "submit": True})))
    cli.main()
    capsys.readouterr()
    monkeypatch.setattr(sys, "argv", ["vwb", "agent", "--agent", "middle-worker", "claim", "--next"])
    cli.main()
    response = json.loads(capsys.readouterr().out)
    assert response["request"]["claim"]["agent"] == "middle-worker"
    assert response["request"]["claim"]["harness"] == "env-harness"
    assert len(list((tmp_path / "env-state/requests").glob("req-*.json"))) == 1


def test_snapshot_guide_and_import(tmp_path, demo, payload):
    code, response = command(tmp_path, "snapshot", "--source", "demo")
    assert code == 0 and response["snapshot"]["revision"] == app.revision(demo)
    code, response = command(tmp_path, "agent", "guide")
    assert code == 0 and "Do not edit OA before approval." in response["guide"]
    receipt = app.save_plan(payload, demo, None, tmp_path / "old")
    code, response = command(tmp_path, "request", "import-handoff", receipt["path"])
    assert code == 0 and response["request"]["status"] == "draft"
    assert response["request"]["changes"][0]["op"] == "connect"
    assert response["request"]["changes"][0]["outcome"] == "merge-existing-nets"
    assert command(tmp_path, "request", "import-handoff", "-", stdin="{}")[0] == 5
    stale = {**receipt["plan"], "base_revision": "stale"}
    assert command(tmp_path, "request", "import-handoff", "-", stdin=json.dumps(stale))[0] == 6


def headers():
    return {"Host": "localhost", "Origin": "http://localhost", "Content-Type": "application/json"}


def test_http_create_read_action_and_removed_route(http_server, request_payload):
    assert request(http_server, "GET", "/api/requests")[0] == 200
    assert request(http_server, "POST", "/api/handoffs", "{}", headers())[0] == 404
    status, _, body = request(http_server, "POST", "/api/requests", json.dumps(request_payload), headers())
    assert status == 201
    identifier = json.loads(body)["request"]["id"]
    assert request(http_server, "GET", "/api/requests/" + identifier)[0] == 200
    assert request(http_server, "GET", "/api/requests/req-0123456789ab")[0] == 404
    path = "/api/requests/" + identifier
    assert request(http_server, "POST", path + "/approve", "{}", headers())[0] == 409
    assert request(http_server, "POST", path + "/submit", "{}", headers())[0] == 200
    claim = http_server.request_store().claim(identifier, "worker", "pytest")
    token = claim["claim"]["token"]
    for url in ["/api/requests", path]:
        _, _, body = request(http_server, "GET", url)
        assert token.encode() not in body
    assert request(http_server, "POST", path + "/cancel", "{}", headers())[0] == 200


@pytest.mark.parametrize("action", ["submit", "cancel", "approve", "revise", "reject", "accept"])
def test_http_all_actions_keep_origin_checks(http_server, request_payload, action):
    created = http_server.request_store().create(request_payload)
    path = "/api/requests/" + created["id"] + "/" + action
    bad = {**headers(), "Origin": "http://evil.example"}
    assert request(http_server, "POST", path, "{}", bad)[0] == 403
    assert request(http_server, "POST", path, json.dumps({"comment": "x" * 2001}), headers())[0] == 400
    assert request(http_server, "POST", path, json.dumps({"unexpected": "x"}), headers())[0] == 400
    assert request(http_server, "POST", path, b"", {**headers(), "Content-Length": "8193"})[0] == 400


def test_http_create_body_can_exceed_old_limit(http_server, request_payload):
    large = {**request_payload, "simulation": "s" * 4000, "acceptance": "a" * 4000, "note": "n" * 2000}
    assert len(json.dumps(large)) > 8192
    assert request(http_server, "POST", "/api/requests", json.dumps(large), headers())[0] == 201


def test_http_plan_approval_and_result_acceptance(http_server, request_payload):
    store = http_server.request_store()
    created = store.create({**request_payload, "submit": True})
    identifier = created["id"]
    token = store.claim(identifier, "worker", "pytest")["claim"]["token"]
    store.agent(identifier, "plan", "worker", token, {"summary": "Use scratch.", "steps": []})
    path = "/api/requests/" + identifier
    assert request(http_server, "POST", path + "/approve", None, headers())[0] == 200
    store.agent(identifier, "start", "worker", token)
    assert request(http_server, "POST", path + "/cancel", "{}", headers())[0] == 409
    store.agent(
        identifier,
        "result",
        "worker",
        token,
        {"outcome": "error", "summary": "Tool unavailable.", "metrics": [], "artifacts": []},
    )
    assert request(http_server, "POST", path + "/accept", "{}", headers())[0] == 200


def test_fake_bridge_master_lookup_and_live_cli(monkeypatch, tmp_path, demo, capsys):
    result = SimpleNamespace(ok=True, output="TERMINALS\nD\nG\nS\nEND")

    class Client:
        @staticmethod
        def local(**kwargs):
            return Client()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute_skill(self, query):
            assert '"r"' in query and "dbClose" in query
            assert "dbCreate" not in query
            return result

    monkeypatch.setitem(
        sys.modules, "virtuoso_bridge", SimpleNamespace(VirtuosoClient=Client, decode_skill_output=lambda value: value)
    )
    change = {"library": "generic", "cell": "nmos", "view": "symbol"}
    assert cli.read_master_terminals(change, "127.0.0.1", 65432) == ["D", "G", "S"]
    result.output = "MISSING"
    with pytest.raises(ValueError):
        cli.read_master_terminals(change, "127.0.0.1", 65432)
    result.ok = False
    with pytest.raises(RuntimeError):
        cli.read_master_terminals(change, "127.0.0.1", 65432)
    live = {**copy.deepcopy(demo), "source": "virtuoso", "library": "examples", "cell": "amp"}
    monkeypatch.setattr(app, "read_snapshot", lambda *args: live)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "vwb",
            "snapshot",
            "--source",
            "virtuoso",
            "--library",
            "examples",
            "--cell",
            "amp",
            "--bridge-host",
            "127.0.0.1",
        ],
    )
    cli.main()
    assert json.loads(capsys.readouterr().out)["snapshot"]["revision"] == app.revision(live)


def test_eight_cli_claim_next_processes(tmp_path, request_payload):
    identifiers = set()
    for _ in range(3):
        code, response = command(
            tmp_path, "request", "create", "--file", "-", stdin=json.dumps({**request_payload, "submit": True})
        )
        assert code == 0
        identifiers.add(response["request"]["id"])
    processes = [
        subprocess.Popen(
            [
                sys.executable,
                "-m",
                "virtuoso_workbench",
                "--state-dir",
                str(tmp_path / "state"),
                "agent",
                "claim",
                "--next",
                "--agent",
                "worker-" + str(index),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for index in range(8)
    ]
    winners = []
    try:
        for process in processes:
            output, errors = process.communicate(timeout=15)
            assert not errors and len(output.splitlines()) == 1
            response = json.loads(output)
            assert process.returncode in (0, 3)
            if process.returncode == 0:
                winners.append(response["request"]["id"])
        assert len(winners) == len(set(winners)) == 3
        assert set(winners) == identifiers
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
                process.communicate(timeout=5)
