# Contributing

Use Python 3.9+ and Node 22. CI checks Python 3.9 and 3.11.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
npm --prefix frontend ci
cd frontend && npx playwright install chromium && cd ..
npm --prefix frontend run typecheck
npm --prefix frontend run lint
npm --prefix frontend test
npm --prefix frontend run build
ruff check .
ruff format --check .
mypy
pytest
npm --prefix frontend run test:e2e
python -m build
python scripts/check_wheel.py dist/virtuoso_workbench_lite-0.1.0-py3-none-any.whl
python scripts/check_private_names.py
gitleaks git --redact --no-banner
```

Linux browser tests may need system libraries; CI uses Playwright's
`install --with-deps chromium`. Build assets before backend static-serving or
browser tests. The smoke test starts a demo server on port 8877 and writes
under ignored artifact directories. The wheel check creates an isolated
temporary environment and serves the installed package without Node on PATH.

Commit source, lockfiles, documentation, and synthetic fixtures only. Run the
private-name check after staging new files: it checks tracked paths, including
staged additions. It reports filenames and line numbers, not values.
Review the staged diff before committing; do not commit generated assets.

Keep changes small and test normal, empty, and error cases. Use fake Bridge
responses, never private designs or licensed EDA tools. UI changes need browser
tests and visual review. Request storage tests require POSIX flock. The dev-only
jsonschema dependency checks persisted examples; runtime validation uses the
standard library. Keep docs/agent-protocol.md and its packaged copy identical.

The Playwright demo tests use the real local CLI to claim requests, send plans,
and send synthetic results. They do not edit OA. Each run uses a new state root
under `.artifacts`. Set `VWB_SCREENSHOT_DIR` to save the draft and approval
screenshots in another existing directory. The palette test scans all source CSS
and TypeScript. Keep color literals in the CSS custom properties and `palette.ts`.

Connectivity checks run only in Python. The browser sends logical preview requests
to the backend. Keep snapshot annotations out of revision hashes. Use the synthetic
`problem_snapshot` fixture and geometry tests for new rules. Document tolerance
changes in `docs/connectivity-checks.md`. Findings must not disable Submit.

This project uses MIT licensing. Submit only code and fixtures you may publish,
and retain required third-party notices. Report suspected secrets privately
to the maintainer, not in a public issue.
