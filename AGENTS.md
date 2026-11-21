# Coding agent instructions

Read README.md and CONTRIBUTING.md. Create `.venv`, install `.[dev]`, and use
Node 22 with `npm --prefix frontend ci`. Build assets before backend or browser
tests. Run every check listed in CONTRIBUTING.md.

- Work only in this repository. Do not change live EDA workspaces or stop remote
  services without explicit authorization.
- Keep Python compatible with 3.9. Use the standard library for the backend and
  lazy optional imports for the `bridge` extra.
- Keep TypeScript strict. Do not suppress errors with `any`, disabled checks,
  or ignored diagnostics. Extract pure logic for unit tests.
- Preserve CLI flags, localhost binding, Host/origin validation, request limits,
  revision checks, atomic request writes, and design-request schema compatibility.
- Test with synthetic data and fake Bridge clients. Never require Cadence,
  production snapshots, credentials, license configuration, or foundry files.
- Do not commit build output, runtime state, environments, or secrets. Run the
  tracked-file private-name check after staging, and run gitleaks.
- Build the frontend before the wheel; verify installed-wheel operation without Node.
- Make focused commits. Do not change Git identity or push unless authorized.

The tool tracks design requests, claims, approval, and results. Agents perform
OA edits through the Bridge outside this tool. Do not add automatic OA editing
or simulation execution to the request service. Read docs/agent-protocol.md.
Keep its packaged copy identical. Do not change the frontend during Part A.
