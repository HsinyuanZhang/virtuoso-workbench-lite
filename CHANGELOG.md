# Changelog

## 0.1.0 — Unreleased

### Step 2, Part A

- Replace the HTTP wire handoff with design-request-v1 and a packaged JSON Schema.
- Add POSIX locked request storage, claim leases, human approval, and result review.
- Add non-interactive JSON request and agent CLI commands and an installed agent guide.
- Change `--state-dir` from the handoff folder to the state root. Its default is
  now `.workbench`, with requests under `.workbench/requests`. Add `VWB_STATE_DIR`.
- Keep legacy `--review` and add wire handoff import to draft requests.
- Remove `POST /api/handoffs`. The unchanged frontend Save handoff operation and
  `frontend/tests/demo.spec.ts` smoke test need Part B and currently fail.
- Emit one JSON startup object when the server starts.

### Step 1

- Preserve the viewer, read-only Bridge snapshots, topology warnings, and JSON
  wire-intent handoff workflow.
- Package the standard-library backend with a `vwb` command and optional Bridge
  dependency. Default runtime state is relative to the launch directory.
- Port the frontend to strict TypeScript and Vite; include built assets in wheels.
- Add safety, frontend, browser, wheel, lint, type, and public-source CI checks.
- Add MIT licensing and contributor/agent guidance.
