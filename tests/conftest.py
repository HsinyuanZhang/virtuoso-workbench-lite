import json
from pathlib import Path
from threading import Thread

import pytest

from virtuoso_workbench import server as app


@pytest.fixture
def demo():
    return json.loads((Path(app.__file__).parent / "demo.json").read_text())


@pytest.fixture
def endpoints():
    return [{"instance": "MP1", "terminal": "D"}, {"instance": "MP2", "terminal": "D"}]


@pytest.fixture
def payload(demo, endpoints):
    return {
        "target": {"source": "demo"},
        "revision": app.revision(demo),
        "endpoints": endpoints,
        "note": "Review merge",
    }


@pytest.fixture
def request_payload(demo, endpoints):
    return {
        "target": {key: demo[key] for key in ("source", "library", "cell", "view")},
        "revision": app.revision(demo),
        "title": "Review the merge",
        "changes": [{"op": "connect", "endpoints": endpoints}],
    }


@pytest.fixture
def http_server(demo, tmp_path):
    with app.WorkbenchServer(("127.0.0.1", 0), app.Handler) as server:
        server.demo = demo
        server.read_live = lambda library, cell: {**demo, "source": "virtuoso", "library": library, "cell": cell}
        server.state_dir = tmp_path / "handoffs"
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server
        finally:
            server.shutdown()
            thread.join()
