# Circuit Workbench

A local browser interface for reading Cadence Virtuoso schematics through
[virtuoso-bridge-lite](https://github.com/Arcadia-1/virtuoso-bridge-lite).
Open a schematic without a VNC session. The browser displays devices as
rectangles with labeled terminals and draws wire geometry read from OA.

## Run the demo

On the server, from this directory, run:

```bash
python3 workbench.py
```

Python 3.9 or newer is enough for the synthetic demo. It needs no Cadence or
PDK installation and no frontend build step.

## Connect to Virtuoso

Start with a working Bridge daemon attached to the existing Virtuoso process.
Use a Python environment with `virtuoso-bridge` installed.

On the server, when the daemon is in Docker:

```bash
python workbench.py --bridge-docker eda-container
```

For a daemon reachable at a known address, pass `--bridge-host` instead.
The default Bridge port is 65432. Override it with `--bridge-port` when needed.

On the Mac, keep this SSH command running and replace the login target:

```bash
ssh -NT -o ExitOnForwardFailure=yes -L 8765:127.0.0.1:8765 user@eda-server
```

Open [http://127.0.0.1:8765](http://127.0.0.1:8765) in the Mac browser.
Enter the OA library and cell and click **Open cell**. The workbench uses a
loopback-only HTTP listener. Do not forward the Bridge daemon's own port to
the browser.

## Review a wire

1. Click a net to inspect its terminals, I/O pins, and physical wires. The
   topology panel reports open terminals and devices whose D and G share
   a net. Physical wires and logical net membership are both useful when
   a schematic uses labels instead of continuous wire geometry.
2. Click two labeled device terminals on the canvas or in the instance
   inspector. The dashed line shows the proposed connection. The inspector
   explains whether it creates a net, adds a terminal to a net, merges two
   nets, or joins terminals already on the same net.
3. Optionally describe the intended circuit behavior. **Save handoff** stores
   a JSON record on the server. **Copy agent request** copies a request
   containing the server-side path. Send that request to the agent that can
   access the same server.

The agent can re-read the current OA revision and review the operation with:

```bash
python workbench.py --bridge-docker eda-container --review .workbench/handoffs/wire-ID.json
```

Handoffs include the selected endpoints, current net membership, affected
top-level pins, and the base revision. The browser and review command read
OA in read mode. Handoff storage is local and git-ignored. For a separately
requested Virtuoso edit, the agent can back up the cell, apply the route
through Bridge, and read back the topology.

## Prototype scope

The viewer preserves OA wire coordinates. Rectangle geometry and the dashed
leaders to their terminals are display aids. The display uses the first pin
figure for each terminal. Topology checks cover a single schematic level and
do not replace ERC, LVS, or simulation. Drafts describe one wire between two
existing instance terminals. Placement, interactive routing, and direct OA
writeback are outside this version.

Source lives in its own directory so it can be published separately from the
EDA workspace. The included example is synthetic. Production cell snapshots
and saved handoffs are generated at runtime.
