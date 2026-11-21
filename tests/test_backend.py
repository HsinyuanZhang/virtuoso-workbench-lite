import copy
import json
import sys
from types import SimpleNamespace

import pytest

from virtuoso_workbench import server as app


@pytest.mark.parametrize("name", ["examples", "amp_1", "A.$+!@-9", "a" * 128])
def test_valid_names(name):
    assert app.validate_name(name) == name


@pytest.mark.parametrize("name", [None, "", "a" * 129, "a/b", 'a")', "a\n", 123])
def test_invalid_names(name):
    with pytest.raises(app.WorkbenchError):
        app.validate_name(name)


def test_revision_is_canonical_and_detects_change(demo):
    assert app.revision(demo) == app.revision(dict(reversed(list(demo.items()))))
    assert app.revision(demo) != app.revision({**demo, "cell": "different"})


def test_snapshot_parser_normal_and_empty():
    raw = "SNAPSHOT\nI|M1|generic|nmos|R0|0:0|((-1 -1) (1 1))\nT|G|in|0:1\nP|IN|input|in|0:2\nW|in|((0 1) (0 2))\nEND"
    snapshot = app.parse_snapshot(raw, "examples", "amp")
    assert snapshot["instances"][0]["terminals"][0]["net"] == "in"
    assert snapshot["wires"][0]["points"] == [[0, 1], [0, 2]]
    assert app.parse_snapshot("SNAPSHOT\nEND", "examples", "empty")["instances"] == []
    assert app.point("nil") is None


@pytest.mark.parametrize("raw", ["", "SNAPSHOT", "SNAPSHOT\nX|bad\nEND", "SNAPSHOT\nW|net|0:0\nEND"])
def test_snapshot_parser_errors(raw):
    with pytest.raises(ValueError):
        app.parse_snapshot(raw, "examples", "amp")


def test_missing_snapshot_and_nonfinite_coordinates():
    with pytest.raises(app.WorkbenchError) as exc:
        app.parse_snapshot("MISSING", "examples", "amp")
    assert exc.value.status == 404
    with pytest.raises(ValueError):
        app.points("1e999:0")


def test_fake_bridge_success_and_error(monkeypatch):
    result = SimpleNamespace(ok=True, output="SNAPSHOT\nEND", errors=[])
    calls = []

    class FakeClient:
        @staticmethod
        def local(**kwargs):
            calls.append(kwargs)
            return FakeClient()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute_skill(self, query):
            assert '"examples" "amp" "schematic" "schematic" "r"' in query
            assert "@LIB@" not in query
            return result

    monkeypatch.setitem(
        sys.modules,
        "virtuoso_bridge",
        SimpleNamespace(
            VirtuosoClient=FakeClient,
            decode_skill_output=lambda value: value,
        ),
    )
    assert app.read_snapshot("examples", "amp", "127.0.0.1", 65432)["source"] == "virtuoso"
    assert calls == [{"host": "127.0.0.1", "port": 65432, "timeout": 15}]
    result.ok, result.errors = False, ["offline"]
    with pytest.raises(RuntimeError, match="offline"):
        app.read_snapshot("examples", "amp", "127.0.0.1", 65432)
    with pytest.raises(app.WorkbenchError) as exc:
        app.read_snapshot("examples", "amp", None, 65432)
    assert exc.value.status == 503


@pytest.mark.parametrize(
    "nets,outcome",
    [
        (("a", "a"), "already-connected"),
        (("a", "b"), "merge-existing-nets"),
        (("a", None), "attach-to-existing-net"),
        ((None, None), "create-net"),
    ],
)
def test_wire_outcomes(demo, endpoints, nets, outcome):
    snapshot = copy.deepcopy(demo)
    for instance, net in zip(snapshot["instances"][:2], nets):
        next(t for t in instance["terminals"] if t["name"] == "D")["net"] = net
    assert app.review_wire(snapshot, endpoints)["outcome"] == outcome


@pytest.mark.parametrize("endpoints", [None, [], [{}, {}], [None, None], [{"instance": "MP1", "terminal": "D"}] * 2])
def test_invalid_endpoints(demo, endpoints):
    with pytest.raises(app.WorkbenchError):
        app.review_wire(demo, endpoints)


def test_atomic_handoff_write(demo, payload, tmp_path, monkeypatch):
    state = tmp_path / "handoffs"
    calls = []
    replace, fsync = app.os.replace, app.os.fsync

    def sync(fd):
        calls.append("fsync")
        fsync(fd)

    def move(source, target):
        assert source.parent == target.parent == state
        assert json.loads(source.read_text())["schema"] == app.PLAN_SCHEMA
        calls.append("replace")
        replace(source, target)

    monkeypatch.setattr(app.os, "fsync", sync)
    monkeypatch.setattr(app.os, "replace", move)
    receipt = app.save_plan(payload, demo, None, state)
    assert calls == ["fsync", "replace"]
    assert list(state.iterdir()) == [state / (receipt["id"] + ".json")]
    assert state.stat().st_mode & 0o777 == 0o700
    assert receipt["plan"]["operation"]["outcome"] == "merge-existing-nets"


def test_failed_atomic_write_removes_temporary_file(demo, payload, tmp_path, monkeypatch):
    def fail(*args):
        raise OSError("disk error")

    monkeypatch.setattr(app.os, "replace", fail)
    with pytest.raises(OSError, match="disk error"):
        app.save_plan(payload, demo, None, tmp_path)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "update", [{"revision": "stale"}, {"note": "x" * 501}, {"note": None}, {"target": {}}, {"endpoints": []}]
)
def test_invalid_handoffs_do_not_write(demo, payload, tmp_path, update):
    with pytest.raises(app.WorkbenchError):
        app.save_plan({**payload, **update}, demo, None, tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_empty_handoff_is_rejected(demo, tmp_path):
    with pytest.raises(app.WorkbenchError):
        app.save_plan({}, demo, None, tmp_path)


def test_live_handoff_rereads_and_rejects_stale_revision(demo, payload, tmp_path):
    payload["target"] = {"source": "virtuoso", "library": "examples", "cell": "amp"}
    calls = []

    def read_live(library, cell):
        calls.append((library, cell))
        return {**demo, "source": "virtuoso"}

    with pytest.raises(app.WorkbenchError) as exc:
        app.save_plan(payload, demo, read_live, tmp_path)
    assert exc.value.status == 409
    assert calls == [("examples", "amp")]
    assert list(tmp_path.iterdir()) == []
