# virtuoso-workbench-lite

A localhost browser interface for communication between a remote human and an
agent on a headless EDA server. The human inspects topology and creates a design
request. The agent claims it, sends a plan, waits for approval, and uses
[virtuoso-bridge-lite](https://github.com/Arcadia-1/virtuoso-bridge-lite) for
separately authorized OA edits, parameter tuning, and simulation.

The backend tracks design requests, approval, and results. It does **not** apply
edits or launch simulation. Use the browser to build requests, review agent plans,
and accept results. Agents use the local CLI for claims, plans, and results.

## Library-bound symbol packages

`vwb symbols export --library <alias> --cell <cell> --view symbol --output <path>` reads only explicit symbol cells through Bridge. Repeat `--cell` for more cells. The export is read-only and atomic; the output path is required, and `--force` is required to replace a file. `vwb symbols inspect --file <path>` validates a package without Bridge. The versioned JSON Schema is packaged at `schemas/library-symbols-v1.json`; runtime validation is also strict. Run the synthetic demo with `python -m virtuoso_workbench serve --symbol-package fixtures/demo-symbols.json`. Packages are separate from request state and can be reused with different `--state-dir` values.

Packages bind by library name, SHA-256 identity derived from the resolved path returned by `ddGetObj`, cell, and actual master view. The raw path is not stored or served. This identity binds an installation; it does not prove that library contents have not changed. The content digest excludes the timestamp and digest field. The server loads packages once at startup; restart it to load a newer export. No OA modification-time guarantee is made.

Geometry uses x-right/y-up coordinates in OA user units. The optional package scale must be exactly `1`; the renderer does not scale OA coordinates. Exported line points, rectangles, polygons, ellipses, arcs, and static labels use the mapped OA attributes; other unverified shapes remain explicit incomplete geometry. Arcs store the full ellipse box and OA start and stop angles in radians, counterclockwise in the symbol frame. Nested instances also cause fallback. The renderer leaves `instance` and `pin` layer shapes out of the drawn artwork and its bounds. Dynamic labels are not evaluated: `cdsName()` and the NLP token `[@instanceName]` become instance-name templates. Other NLP and expression labels are omitted with a visible warning. Static text such as `W=(typ)` is preserved. Re-export an older package if it displays literal NLP tokens. Label font, height, orientation, and justification are displayed in a simplified form, not faithfully reproduced. The instance name is overlaid when no name template exists.

All pin figures and their anchor points are packaged. A live symbol resolves only when every package anchor matches a source anchor within `1e-6` OA user units. Older snapshots without source-anchor metadata may resolve only when each terminal has one verifiable anchor; extra figures use the rectangle fallback. Non-unit instance magnification also uses fallback. A non-nil OA `master~>superMaster` link marks the instance as an unsupported parameterized variant and forces fallback. Parameter values are not snapshot fields; variants that do not expose this link are not guaranteed to be detected. The checked-in geometry is synthetic. A representative library with three MOS master cells and 12 schematic instances was verified read-only with live Bridge 0.7.0 and the TypeScript resolver, including package binding, source-pin matching, and horizontal-flip previews. Full browser visual verification, nested instances, parameterized variants, full PDK coverage, and PDK source version remain unverified.

The demo leaves `MN2` at `R0` even though its snapshot terminal coordinates are mirrored. It intentionally uses the rectangle fallback; the package does not rewrite demo orientation or connectivity.

## Browser workflow

Select two instance terminals and click **Add connect**. Add parameter name/value
rows in the instance inspector and click **Add parameters**. Repeated parameter
edits update that instance's draft change. Remove unwanted changes from the list.
Enter a title, simulation setup, acceptance targets, and an optional note. Click
**Save draft** or **Submit**. The builder uses the loaded snapshot revision.
A stale revision error keeps the local draft. Opening a schematic clears it.

Select a request in the Requests panel. The panel polls every five seconds without
overlapping polls. Review changes, plans, results, metrics, and history. Use the
available action buttons and add a review comment. Executing requests cannot be
cancelled by the human. Request highlights appear only on the matching schematic.

The UI uses a black background, neutral gray panels and inputs, green devices, blue wires, red pins, and white text
and selection. Errors are red. Success and met targets are green. Status uses text.

## Advisory connectivity checks

Use **Flip left-right** in the instance inspector to mirror one instance in the
draft. Click again to remove its flip. The canvas mirrors its terminals without
moving wires. Checks report broken wire ends before Submit. After approval, the
agent flips that same instance in OA, keeps its bbox center fixed, repairs wires,
and runs `vwb check`. See [orientation conventions](docs/orientation.md).

Use the **Checks** panel to inspect the current snapshot and the logical draft
preview. Click a finding to highlight it. Draft edits update checks after a short
debounce. Errors are red. Warnings are white. Findings never block Submit.

```bash
vwb check --source demo
vwb check --source demo --file request.json
```

Snapshot responses include `checks`. `POST /api/check` checks ordered logical
changes without writing state or editing OA. The demo retains one floating body
terminal, `MN3.B`. See [checks and tolerance](docs/connectivity-checks.md) for the
rules, geometry limits, API, and preview behavior. This is not a full ERC or LVS.

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

Use Python 3.9+ and Node 22.x (22.13.0 or later) to build the frontend once:

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
  For approve or accept, send `reviewed_event`: the zero-based index in the
  displayed request history of the latest `plan` or `result` event, respectively.
  If it no longer matches, the server returns 409 without recording a decision.

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
