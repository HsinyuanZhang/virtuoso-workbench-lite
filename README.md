# virtuoso-workbench-lite

A localhost browser interface for communication between a remote human and an
agent on a headless EDA server. The human inspects topology and drafts a wire
request. The agent reviews it and uses
[virtuoso-bridge-lite](https://github.com/Arcadia-1/virtuoso-bridge-lite) for
separately authorized OA edits, parameter tuning, and simulation.

This version preserves the existing viewer and JSON handoff workflow. It does
**not** apply edits, launch simulation, or stream agent results back to the UI.

## Architecture

- Python 3.9+ standard-library HTTP server, packaged as `virtuoso_workbench`.
- Strict TypeScript and Canvas frontend built with Vite. Wheels contain the
  static build; deployed servers do not need Node.
- Synthetic demo included. Live snapshots use the optional `virtuoso-bridge`
  package and a working daemon attached to Virtuoso.
- Browser → localhost API → read-only OA snapshot. Browser → wire handoff →
  agent review. The schema stays `circuit-workbench/wire-intent-v1`.

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

## Wire review

Select a net to inspect it. Select two existing instance terminals on the canvas
or in the keyboard-accessible inspector. Review whether the proposal creates a
net, attaches a terminal, merges nets, or joins already-connected terminals.
Add a note, click **Save handoff**, and use **Copy agent request** to send its
server-side path to an agent with access to the same server.

Default handoffs are in `.workbench/handoffs` in the **launch working directory**,
not the installed package. Use `--state-dir` for a stable location.
Review against the latest snapshot:

```bash
vwb --bridge-docker eda-container --review .workbench/handoffs/wire-ID.json
```

OA reads remain read-only. Any actual route requires separate authorization,
backup, Bridge operations, and verification outside this application.

## Security model

The listener binds only to `127.0.0.1`. Use localhost plus an authenticated SSH
tunnel. There is no application login or multi-user isolation. Do not expose
the listener or forward the Bridge daemon's port to the browser.

Host/origin checks, request limits, revision checks, and atomic handoff writes
are safety measures, not an authenticated deployment boundary. Only trusted
users should have local access. Handoffs, logs, and snapshots can contain private
design data. Never commit them, credentials, license configuration, foundry
material, or production examples. CI needs no Cadence installation.

## Scope and development

The viewer preserves OA wire coordinates. Rectangles and terminal leaders are
display aids. It uses each terminal's first pin figure and checks one schematic
level. Warnings do not replace ERC, LVS, or simulation. Drafts connect two existing
instance terminals. Placement, interactive routing, and direct OA writeback
remain outside this version.

See [CONTRIBUTING.md](CONTRIBUTING.md) for checks and [CHANGELOG.md](CHANGELOG.md)
for changes. License: [MIT](LICENSE).
