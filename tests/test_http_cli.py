import http.client
import json
import sys

import pytest

from virtuoso_workbench import cli
from virtuoso_workbench import server as app


def request(server, method, path, body=None, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    try:
        connection.request(method, path, body, headers or {})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def test_demo_and_static(http_server):
    status, headers, body = request(http_server, "GET", "/api/demo")
    assert status == 200
    assert json.loads(body)["revision"] == app.revision(http_server.demo)
    assert headers["Cache-Control"] == "no-store"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    for path, content_type in [
        ("/", "text/html"),
        ("/workbench.js", "text/javascript"),
        ("/workbench.css", "text/css"),
    ]:
        status, headers, body = request(http_server, "GET", path)
        assert status == 200 and body
        assert headers["Content-Type"].startswith(content_type)
    assert request(http_server, "GET", "/../demo.json")[0] == 404


def test_live_http_uses_fake_reader(http_server):
    status, _, body = request(http_server, "GET", "/api/schematic?library=examples&cell=amp")
    assert status == 200
    assert json.loads(body)["source"] == "virtuoso"


@pytest.mark.parametrize(
    "host,origin,status",
    [
        ("remote.example", None, 403),
        ("127.0.0.1.evil", None, 403),
        ("localhost", "https://localhost", 403),
        ("localhost", "http://evil.example", 403),
        ("localhost", None, 403),
        ("localhost:9999", "http://localhost:9999", 201),
        ("127.0.0.1", "http://127.0.0.1", 201),
    ],
)
def test_origin_and_host_checks(http_server, request_payload, host, origin, status):
    headers = {"Host": host, "Content-Type": "application/json"}
    if origin:
        headers["Origin"] = origin
    assert request(http_server, "POST", "/api/requests", json.dumps(request_payload), headers)[0] == status
    if status != 201:
        assert not http_server.state_dir.exists()


@pytest.mark.parametrize("length", ["0", "-1", "65537", "invalid"])
def test_request_size_limits(http_server, length):
    headers = {
        "Host": "localhost",
        "Origin": "http://localhost",
        "Content-Type": "application/json",
        "Content-Length": length,
    }
    assert request(http_server, "POST", "/api/requests", b"", headers)[0] == 400
    assert not http_server.state_dir.exists()


def test_post_json_and_revision_errors(http_server, request_payload):
    headers = {"Host": "localhost", "Origin": "http://localhost", "Content-Type": "application/json"}
    assert request(http_server, "POST", "/api/requests", "{bad", headers)[0] == 400
    request_payload["revision"] = "stale"
    assert request(http_server, "POST", "/api/requests", json.dumps(request_payload), headers)[0] == 409
    headers["Content-Type"] = "text/plain"
    assert request(http_server, "POST", "/api/requests", "{}", headers)[0] == 400
    assert not http_server.state_dir.exists()


@pytest.mark.parametrize(
    "options,expected_host",
    [([], None), (["--bridge-host", "127.0.0.1"], "127.0.0.1"), (["--bridge-docker", "eda-container"], "172.18.0.2")],
)
def test_cli_preserves_bind_flags_and_bridge_options(monkeypatch, options, expected_host, tmp_path):
    instances = []
    calls = []

    class FakeServer:
        server_port = 8765

        def __init__(self, address, handler):
            assert address == ("127.0.0.1", 8765)
            instances.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def serve_forever(self):
            self.read_live("examples", "amp")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(app, "WorkbenchServer", FakeServer)
    monkeypatch.setattr(
        cli.subprocess, "check_output", lambda *args, **kwargs: '{"network":{"IPAddress":"172.18.0.2"}}'
    )
    monkeypatch.setattr(app, "read_snapshot", lambda *args: calls.append(args))
    monkeypatch.setattr(sys, "argv", ["vwb", *options])
    app.main()
    assert calls == [("examples", "amp", expected_host, 65432)]
    assert instances[0].state_dir == tmp_path / ".workbench"


def test_cli_review_demo_and_stale_revision(monkeypatch, demo, payload, tmp_path, capsys):
    receipt = app.save_plan(payload, demo, None, tmp_path)
    monkeypatch.setattr(sys, "argv", ["vwb", "--review", receipt["path"]])
    app.main()
    assert json.loads(capsys.readouterr().out)["operation"]["outcome"] == "merge-existing-nets"
    path = tmp_path / (receipt["id"] + ".json")
    path.write_text(json.dumps({**receipt["plan"], "base_revision": "stale"}))
    with pytest.raises(SystemExit) as exc:
        app.main()
    assert exc.value.code == 6
