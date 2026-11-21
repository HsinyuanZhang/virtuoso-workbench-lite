# virtuoso-workbench-lite

A localhost browser interface for communication between a remote human and an
agent on a headless EDA server. The human inspects topology and creates a design
request. The agent claims it, sends a plan, waits for approval, and uses
[virtuoso-bridge-lite](https://github.com/Arcadia-1/virtuoso-bridge-lite) for
separately authorized OA edits, parameter tuning, and simulation.

The backend tracks design requests, approval, and results. It does **not** apply
edits or launch simulation. Part A leaves the viewer unchanged. Its old Save
handoff button does not work until Part B. Use the CLI or HTTP API for requests.

## Architecture

- Python 3.9+ standard-library HTTP server, packaged as `virtuoso_workbench`.
- Strict TypeScript and Canvas frontend built with Vite. Wheels contain the
  static build; deployed servers do not need Node.
- Synthetic demo included. Live snapshots use the optional `virtuoso-bridge`
  package and a working daemon attached to Virtuoso.
- Browser → localhost API → read-only OA snapshot. Human → design request →
  agent plan → human approval → external agent work → result → human acceptance.
- The schema is `virtuoso-workbench/design-request-v1`. Its JSON Schema ships
  inside the package under `schemas/design-request-v1.json`.

## Quick start: demo from source

Use Python 3.9+ and Node 22 to build the frontend once:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
npm --prefix frontend ci
npm --prefix frontend run build
vwb
```

Open <http://127.0.0.1:8765>. The demo needs no EDA tools, PDK, or Bridge.
After installation, `python workbench.py` and `python -m virtuoso_workbench`
are equivalent launchers. `vwb --help` lists all flags.

## Wheel deployment (no Node on the server)

After building the frontend:

```bash
python -m build
python scripts/check_wheel.py dist/virtuoso_workbench_lite-0.1.0-py3-none-any.whl
```

Copy the wheel to the target machine, install it with
`python -m pip install path/to/wheel.whl`, and run `vwb`.
Build the frontend before building a wheel. Generated static files are not committed.

## Bridge mode and SSH access

The optional `bridge` extra supports the Bridge 0.7 series. That distribution is
not currently available on PyPI. First install a trusted upstream Bridge checkout
into the same environment, then enable the extra:

```bash
python -m pip install ../virtuoso-bridge-lite
python -m pip install -e '.[bridge]'
# For wheel deployment instead: python -m pip install 'path/to/wheel.whl[bridge]'
```

Start its daemon separately; this application does not start or manage it.

```bash
vwb --bridge-docker eda-container
# Or use a directly reachable daemon:
vwb --bridge-host 127.0.0.1 --bridge-port 65432
```

The Docker option inspects an existing container and requires one IPv4 address.
HTTP defaults to port 8765; use `--port` to change it.

On the human's machine, keep this SSH tunnel open:

```bash
ssh -NT -o ExitOnForwardFailure=yes -L 8765:127.0.0.1:8765 user@eda-server
```

Open <http://127.0.0.1:8765>, enter a library/cell, and click **Open cell**.

## Design requests

Topology and device parameters are structured changes. Simulation setup and
acceptance targets are natural-language text. Requests contain 1–200 ordered
changes. Each change uses an `op` field. Supported operations are `connect`,
`disconnect`, `add-instance`, `remove-instance`, `set-parameters`, and `add-pin`.

First read the snapshot and its revision:

```bash
vwb snapshot --source demo
```

Save a create payload as `request.json`. Replace `REVISION` with the returned
snapshot revision:

```json
{
  "target": {"source":"demo","library":"examples","cell":"Differential pair","view":"schematic"},
  "revision": "REVISION",
  "title": "Review the input pair width",
  "changes": [{"op":"set-parameters","instance":"MN1","parameters":{"w":"4u"}}],
  "simulation": "Run a DC operating point check in a scratch copy.",
  "acceptance": "Keep both input devices in saturation.",
  "note": "Do not change the original cell.",
  "submit": true
}
```

```bash
vwb request create --file request.json
vwb request list
vwb request show req-0123456789ab
vwb request approve req-0123456789ab --comment "Use a scratch copy."
vwb request accept req-0123456789ab
```

Use approve only after the agent sends a plan. Use accept only after it sends a
result. Use revise to ask for a new plan. Cancel is not allowed during execution.
An agent can report failure instead. See the complete workflow in
[docs/agent-protocol.md](docs/agent-protocol.md), or run `vwb agent guide`.
The [skill file](skills/virtuoso-workbench/SKILL.md) supports Devin, Claude Code,
pi, and other local harnesses. No interactive prompts are required.

The state root is `.workbench` in the launch directory. Override it with
`--state-dir` or `VWB_STATE_DIR`. Requests live in `requests/<id>.json` below it.
This changes the old meaning of `--state-dir`, which selected the handoff folder.
Storage requires POSIX `fcntl.flock` and atomic rename. Use a local filesystem.
Files use mode 0600; state and request directories use mode 0700.

Import old handoffs as draft requests, or review them without importing:

```bash
vwb request import-handoff old-wire.json
vwb --bridge-docker eda-container --review old-wire.json
```

OA reads remain read-only. Any actual route requires separate authorization,
backup, Bridge operations, and verification outside this application.

### Human HTTP API

Snapshot GET endpoints are unchanged. New routes are:

- `GET /api/requests`: token-free summaries.
- `GET /api/requests/<id>`: token-free request with `lease_expired`.
- `POST /api/requests`: the create payload above; maximum 65536 bytes.
- `POST /api/requests/<id>/<action>`: submit, cancel, approve, revise, reject,
  or accept; optional JSON `comment`, maximum 2000 characters and 8192 bytes.

Send `Content-Type: application/json` and the same-origin `Origin` header for
JSON POST requests. A stale revision returns 409. State conflicts also return
409. The old `POST /api/handoffs` now returns 404. Agent changes use the local
CLI, not HTTP. CLI commands return one JSON object and documented exit codes.
The server writes one JSON startup object with its loopback URL.

## Security model

The listener binds only to `127.0.0.1`. Use localhost plus an authenticated SSH
tunnel. There is no application login or multi-user isolation. Do not expose
the listener or forward the Bridge daemon's port to the browser.

Host/origin checks, request limits, revision checks, and atomic request writes
are safety measures, not an authenticated deployment boundary. Only trusted
users should have local access. Requests, logs, and snapshots can contain private
design data. Never commit them, credentials, license configuration, foundry
material, or production examples. CI needs no Cadence installation.

## Scope and development

The viewer preserves OA wire coordinates. Rectangles and terminal leaders are
display aids. It uses each terminal's first pin figure and checks one schematic
level. Warnings do not replace ERC, LVS, or simulation. The browser still shows
the Step 1 viewer until Part B. Direct OA writeback and simulation execution
remain outside this tool.

See [CONTRIBUTING.md](CONTRIBUTING.md) for checks and [CHANGELOG.md](CHANGELOG.md)
for changes. License: [MIT](LICENSE).
