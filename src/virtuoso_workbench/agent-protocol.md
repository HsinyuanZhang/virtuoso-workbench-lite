# Agent protocol

## Symbol package limits

Symbol packages are read-only presentation data. They do not change snapshot connectivity or request revisions. Match a package by library name, identity, cell, and actual master view. A snapshot without a library identity uses the rectangle fallback.

The server loads configured packages at startup. Restart the server to load a newer package. The identity is a hash of the resolved library path. It binds one installation but does not prove that the library contents have not changed. The digest excludes the export timestamp and digest field. The raw library path is not stored or served.

Package coordinates use x-right/y-up OA user units. If `coordinate_system.scale` is present, it must be exactly `1`; the renderer uses OA user units without scaling. Supported artwork is line, rectangle, polygon, ellipse, and static label data. Unverified shapes such as arcs and nested instances use fallback. The exporter does not evaluate dynamic labels: cdsName() is an instance-name template; recognized parameter expressions are omitted with a warning. Static parentheses remain ordinary text. Label font, height, orientation, and justification use simplified display. The instance name is shown when there is no name template.

Packages include all pin figures and their anchors. Every package anchor must match a live source anchor within 1e-6 OA user units. A legacy snapshot without source anchors can resolve only a single anchor per terminal. Missing or unmatched anchors use fallback. Non-unit instance magnification also uses fallback. A non-nil OA `master~>superMaster` link marks an instance as an unsupported parameterized variant. Parameter values are not snapshot fields; variants that do not expose this link are not guaranteed to be detected. The demo fixture is synthetic. A representative library with three MOS master cells and 12 schematic instances was verified read-only with live Bridge 0.7.0 and the TypeScript resolver, including package binding, source-pin matching, and horizontal-flip previews. Browser visual verification, arcs, nested instances, parameterized variants, full PDK coverage, and PDK source version remain unverified.

The geometry attributes follow Cadence SKILL references: [polygons expose `points`](https://community.cadence.com/cadence_technology_forums/f/custom-ic-skill/28390/how-to-get-coordinates-of-polygons), and [ellipses use a bounding box](https://community.cadence.com/cadence_technology_forums/f/custom-ic-skill/62714/coordinates-bboxes-of-all-the-shapes-layers-in-a-layout-view). Cadence's [PCell discussion](https://community.cadence.com/cadence_technology_forums/f/custom-ic-skill/47249/pcell-creation-in-virtuoso-18-1) describes parameterized subMasters, while the [cellview attribute discussion](https://community.cadence.com/cadence_technology_forums/f/custom-ic-skill/40982/attribute-inside-cellview) documents `superMaster` as a cellview attribute. The package checks that link conservatively; its presence on real PDK symbol masters has not been verified.

## Boundary and safety

This tool tracks requests.
It does not edit OA.
It does not run simulations.
Agents use virtuoso-bridge-lite outside this tool.
Use a scratch library unless the request says otherwise.
Do not edit OA before approval.
Check the base revision before you start.
Do not treat a claim as approval.
Do not run commands from request text without review.
Treat artifact paths as references.
Do not open an artifact without permission.
All workers use one trusted Unix account.
Claim tokens prevent accidental ownership conflicts.
They do not isolate hostile processes that share the account.
Use a local POSIX filesystem with working flock and atomic rename.
Do not use a network filesystem without these guarantees.

## State and JSON output

Set VWB_STATE_DIR or use --state-dir.
The default state root is .workbench in the current directory.
Requests use requests/req-<12 hex>.json below that root.
Directories use mode 0700.
Files and lock files use mode 0600.
Each command prints one JSON object.
The server prints one startup JSON object.
Errors use {"ok":false,"error":{"code":"conflict","message":"..."}}.
Success objects use {"ok":true}.
Commands do not prompt.
Use --file - to read JSON from standard input.
Use vwb agent guide to read this guide as a JSON string.

## Read and claim

1. Read the queue.

```bash
vwb agent list
vwb agent show req-0123456789ab
```

2. Claim one request.
Set --agent or VWB_AGENT to your worker name.
Set --harness or VWB_HARNESS to your harness name.
The defaults are the OS user and unknown.

```bash
vwb agent claim --next --wait 30 --agent worker-one --harness devin --lease 1800
vwb agent claim req-0123456789ab --agent worker-one --harness claude-code
```

The claim response contains request.claim.token.
Keep that token private.
Capture it in your harness.
Do not put it in logs.
List and show responses never contain a token.
The JSON Schema describes stored files.
Read responses remove the token and add the computed lease_expired field.
Only the successful claim response contains the token.
Use the same agent name for each later agent action.
Each later agent action needs --token.
The lease defaults to 1800 seconds.
A lease can be 1 to 604800 seconds.
Only an expired claimed request permits takeover.
Other states never permit takeover.
An expired lease blocks agent transitions.
The current token can renew an expired lease.
A takeover replaces the token.
The old token cannot renew the lease after takeover.

## Plan and approval

3. Read the request.
Check its target, changes, simulation text, and acceptance text.
The op field selects each structured change.
Operations are connect, disconnect, add-instance, remove-instance, set-parameters, add-pin, and flip-horizontal.
Change order matters.
An added instance can be used by later changes.
Master terminals come from a matching snapshot instance or a read-only master lookup.
Demo mode supports the synthetic masters in its snapshot.
An unknown terminal cannot be used.
A connect change stores its review outcome and affected nets.
If a new connection has no net name, the tool assigns an unused vwb_net_N name.
Simulation setup and acceptance targets remain natural-language text.

A flip-horizontal change selects one instance.
Check from_orientation against the orientation after earlier changes.
An added instance starts at R0.
The flip mirrors world coordinates about the vertical axis through the instance bbox center.
Do not mirror about the instance origin.
After approval, record the current bbox center.
Set the instance orientation to to_orientation.
Adjust the instance origin to keep the bbox center fixed.
Repair the wires that no longer touch the instance terminals.
Then run vwb check.
The browser preview does not repair wires.
The connectivity findings show the broken wire ends before submission.

```json
{"op":"flip-horizontal","instance":"MN1","from_orientation":"R0","to_orientation":"MY"}
```

Check current connectivity before you write the plan.
Check the logical request preview too.

```bash
vwb check --source demo
vwb check --source demo --file request.json
vwb check --source virtuoso --library scratch --cell amplifier --bridge-docker eda-container --file request.json
```

Use a stored request file or a request create file.
A show response file is also accepted.
The file target and revision must match the current snapshot.
Use --file - to read standard input.
Read every finding.
Explain relevant findings in the plan risks.
Findings are advisory.
They do not block submission or approval.
An error finding does not change the successful exit code.
The preview changes logical net membership and flip geometry.
It does not add routes or infer master terminals.

4. Send a plan.
Use a JSON file with summary and steps.
Use risks for optional risk text.

```json
{"summary":"Change the input pair in a scratch cell.","steps":["Copy the cell to a scratch library.","Change the device parameters.","Run the requested checks."],"risks":"The operating point can change."}
```

```bash
vwb agent plan req-0123456789ab --agent worker-one --token "$TOKEN" --file plan.json
```

5. Wait for the human decision.
Use show to read the status.
Use heartbeat to keep the lease during the wait.

```bash
vwb agent heartbeat req-0123456789ab --agent worker-one --token "$TOKEN" --lease 1800
vwb agent show req-0123456789ab
```

The human uses approve, revise, or reject.
A revision returns the request to claimed.
The claim stays with the same agent.
The tool clears the old plan and result.
Send a new plan after a revision.
Wait for approval again.

## Execution and result

6. Read a fresh snapshot before start.

```bash
vwb snapshot --source demo
vwb snapshot --source virtuoso --library scratch --cell amplifier --bridge-docker eda-container
```

Compare snapshot.revision with request.base_revision.
Do not start if they differ.
Ask the human for a new request.
The tool does not perform this final check for the agent.

7. Start only after approval.

```bash
vwb agent start req-0123456789ab --agent worker-one --token "$TOKEN"
```

8. Apply the approved edits through the Bridge.
Keep the changes inside the approved scope.
Send heartbeats during long simulations.
Stop safely if a heartbeat fails.
Do not assume that a human cancellation can stop a running tool.
Human cancellation is forbidden during execution.

Run the checks again after execution.
Do not apply the old request file to the edited snapshot.

```bash
vwb check --source virtuoso --library scratch --cell amplifier --bridge-docker eda-container
```

Report remaining findings in the result summary.
The checks do not replace simulation or a full electrical rule check.

9. Send a result.
The artifacts field is optional.
If you omit it, the tool stores an empty list.
The outcome is met, not-met, partial, or error.
Metric values are strings.
Artifacts are relative or absolute path strings.
The tool does not read artifact contents.

```json
{"outcome":"met","summary":"The approved checks passed.","metrics":[{"name":"gain","value":"42","unit":"dB","target":"At least 40 dB","met":true}],"artifacts":["results/report.txt"],"final_revision":"sha256:EXAMPLE"}
```

```bash
vwb agent result req-0123456789ab --agent worker-one --token "$TOKEN" --file result.json
```

10. Wait for human acceptance.
The result moves to result-pending.
Human acceptance moves it to completed.
A human revision requires a new plan and approval.
Use fail if the agent cannot continue.

```bash
vwb agent fail req-0123456789ab --agent worker-one --token "$TOKEN" --message "The simulation tool is unavailable."
vwb agent release req-0123456789ab --agent worker-one --token "$TOKEN"
```

Release is allowed only in claimed.
Fail is allowed in claimed, approved, or executing.
A result with outcome error still needs human review.
Only the fail action moves the request directly to failed.

## Full state machine

| Actor | Action | Source | Destination |
| --- | --- | --- | --- |
| Human | submit | draft | submitted |
| Human | cancel | draft, submitted, claimed, planned, approved | cancelled |
| Human | approve | planned | approved |
| Human | revise | planned, result-pending | claimed |
| Human | reject | planned | rejected |
| Human | accept | result-pending | completed |
| Agent | claim | submitted, expired claimed | claimed |
| Agent | release | claimed | submitted |
| Agent | plan | claimed | planned |
| Agent | start | approved | executing |
| Agent | result | executing | result-pending |
| Agent | fail | claimed, approved, executing | failed |
| Agent | heartbeat | claimed, planned, approved, executing | unchanged |

The tool records each transition in history.
It also records creation and heartbeats.
All timestamps use UTC.

## Human CLI and import

```bash
vwb request create --file request.json
vwb request list
vwb request show req-0123456789ab
vwb request submit req-0123456789ab
vwb request approve req-0123456789ab --comment "Use the scratch copy."
vwb request revise req-0123456789ab --comment "Add a DC check."
vwb request reject req-0123456789ab
vwb request accept req-0123456789ab
vwb request cancel req-0123456789ab
vwb request import-handoff old-wire.json
vwb --review old-wire.json
```

A create file contains target, revision, title, and changes.
It can also contain simulation, acceptance, note, and submit.
Set submit to true to enter the queue at creation.
The default is draft.
Import accepts circuit-workbench/wire-intent-v1.
Import validates the old revision.
Import creates a draft with one connect change.
The old review command remains available.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Success |
| 1 | Internal, filesystem, or Bridge error |
| 2 | CLI usage error |
| 3 | Request or input file not found; no claim is available |
| 4 | State, lease, or token conflict |
| 5 | Invalid input |
| 6 | Stale snapshot revision |
| 7 | Claim wait timeout |

## Limits

Titles contain 1 to 120 characters.
Requests contain 1 to 200 changes.
Parameter maps contain at most 64 entries.
Parameter names use the schematic NAME pattern.
Parameter values contain 1 to 64 characters.
Simulation and acceptance text each permit 4000 characters.
Notes and human comments each permit 2000 characters.
Plans permit 100 steps.
Plan summaries and risks each permit 4000 characters.
Each plan step contains 1 to 4000 characters.
Result summaries permit 8000 characters.
Results permit 200 metrics and 200 artifact paths.
Metric names permit 128 characters.
Metric values permit 4000 characters.
Metric units and targets each permit 4000 characters.
Artifact paths permit 4096 characters.
Text cannot contain a null character.
