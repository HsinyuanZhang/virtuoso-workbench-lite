import shutil
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "text,expected",
    [
        ("synthetic example", 0),
        ("TS" + "MC", 1),
        ("t" + "28", 1),
        ("/da" + "ta/private", 1),
        ("/ho" + "me/private", 1),
        ("xinyuan_" + "ams_mc2", 1),
        ("", 0),
        ("binary\0TS" + "MC", 1),
    ],
)
def test_tracked_file_scan(tmp_path, text, expected):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    source = Path(__file__).resolve().parents[1] / "scripts" / "check_private_names.py"
    shutil.copyfile(source, scripts / source.name)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "fixture.txt").write_text(text)
    subprocess.run(["git", "add", "fixture.txt"], cwd=tmp_path, check=True)
    result = subprocess.run([sys.executable, str(scripts / source.name)], capture_output=True, text=True)
    assert result.returncode == expected
    if expected:
        assert "fixture.txt:1: forbidden identifier or path" in result.stderr
        assert text not in result.stderr
    else:
        assert "PASS private-name scan" in result.stdout
