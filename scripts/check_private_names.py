"""Reject private identifiers and machine-specific paths in Git-tracked files."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

FORBIDDEN = re.compile(
    "|".join(
        re.escape(value)
        for value in (
            "TS" + "MC",
            "T" + "28",
            "xinyuan_" + "ams_mc2",
            "/da" + "ta/",
            "/ho" + "me/",
        )
    ),
    re.IGNORECASE,
)


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    paths = subprocess.check_output(["git", "ls-files", "-z"], cwd=root).decode().split("\0")
    failures = []
    for name in filter(None, paths):
        path = root / name
        if FORBIDDEN.search(name):
            failures.append(f"{name}: forbidden path name")
        if not path.is_file():
            continue
        data = path.read_bytes()
        for number, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), 1):
            if FORBIDDEN.search(line):
                failures.append(f"{name}:{number}: forbidden identifier or path")
    if failures:
        raise SystemExit("\n".join(failures))
    print(f"PASS private-name scan ({len(list(filter(None, paths)))} tracked files)")


if __name__ == "__main__":
    main()
