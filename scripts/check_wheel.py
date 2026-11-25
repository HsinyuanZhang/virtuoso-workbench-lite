"""Verify an installed wheel serves the UI without Node or an editable source tree."""

from __future__ import annotations

import json
import os
import select
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.request import urlopen
from zipfile import ZipFile


def main() -> None:
    wheel = Path(sys.argv[1]).resolve()
    with ZipFile(wheel) as archive:
        for name in (
            "demo.json",
            "static/index.html",
            "static/workbench.js",
            "static/workbench.css",
            "schemas/design-request-v1.json",
            "agent-protocol.md",
        ):
            assert archive.read("virtuoso_workbench/" + name), name
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        subprocess.run([sys.executable, "-m", "venv", str(root / "env")], check=True)
        python = root / "env" / "bin" / "python"
        subprocess.run(
            [str(python), "-m", "pip", "install", "--no-deps", str(wheel)], check=True, stdout=subprocess.DEVNULL
        )
        env = {**os.environ, "PATH": str(root / "env" / "bin"), "PYTHONPATH": ""}
        process = subprocess.Popen(
            [str(root / "env" / "bin" / "vwb"), "--port", "0"],
            cwd=root,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            assert process.stdout
            assert select.select([process.stdout], [], [], 10)[0], "Server startup timed out"
            url = json.loads(process.stdout.readline())["url"]
            assert url.startswith("http://127.0.0.1:")
            for path in ("/", "/workbench.js", "/workbench.css"):
                with urlopen(url + path, timeout=10) as response:
                    assert response.status == 200 and response.read()
            with urlopen(url + "/api/demo", timeout=10) as response:
                assert json.load(response)["source"] == "demo"
            guide = subprocess.run(
                [str(root / "env" / "bin" / "vwb"), "agent", "guide"],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )
            assert "Do not edit OA before approval." in json.loads(guide.stdout)["guide"]
        finally:
            process.terminate()
            process.communicate(timeout=10)
    print("PASS installed wheel: static assets and demo served without Node")


if __name__ == "__main__":
    main()
