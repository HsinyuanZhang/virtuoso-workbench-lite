# Advisory connectivity checks

The implementation is Python standard-library code in `checks.py`. It is original
MIT code in this repository. The browser calls the backend for a draft preview.
There is no second connectivity implementation in TypeScript.

## Findings

| Code | Severity | Condition |
| --- | --- | --- |
| `floating-terminal` | error | An instance terminal has no logical net. |
| `single-connection-net` | warning | A net has exactly one instance terminal or top-level pin in total. Wires do not count. |
| `pin-without-net` | error | A top-level pin has no logical net. |
| `pin-net-unused` | warning | A top-level pin net has no instance terminal. |
| `dangling-wire-end` | error | A polyline end does not touch a same-net terminal, pin, or another same-net segment. |
| `net-not-joined` | warning | Same-net segments form more than one geometric group. The message gives the group count. |

Each finding has `code`, `severity`, and `message`. It can have `instance`,
`terminal`, `net`, `pin`, and `points`. Missing coordinates are not guessed.
Click a finding to highlight its known locations on the loaded canvas.
Errors are red. Warnings and selection are white. Neither severity blocks Submit.

Geometric connections include endpoints, endpoint-on-segment T junctions,
collinear overlaps, and same-net interior crossings. Different nets never join
geometrically. Duplicate dangling points on the same net are reported once.
Internal polyline bends are not dangling ends.

## Units and tolerance

The reader exports SKILL `shape~>points`, instance coordinates, and transformed
pin bounding-box centers directly. These are Virtuoso user coordinates, not
integer database-unit coordinates. No scaling is applied by the parser.
The snapshot does not include the editor grid pitch. The checker does not claim
to recover that grid from geometry.

The tolerance is `min(1e-6, shortest_nonzero_segment_length * 1e-4)` in snapshot
user units, with a lower bound of `1e-15`. With no nonzero segments, it is `1e-6`.
The shortest run is a conservative geometric scale proxy, not the actual grid.
The cap handles normal schematic grids. The scale factor reduces tolerance for
fine geometry so it cannot bridge a gap as large as the shortest run. The lower
bound is a numerical floor; sub-femtounit geometry is outside this check's scope.
`POST /api/check` and `vwb check` return the tolerance used.

The demo uses a tolerance of `1e-6`. Its only finding is
`floating-terminal`: `MN3.B has no net.` This existing synthetic issue is retained.
Its bends, T junctions, and shared wire interiors produce no geometric findings.
`tests/test_checks.py` also supplies a synthetic `problem_snapshot` fixture with
all six finding codes.

## Interfaces

Snapshot HTTP responses and `vwb snapshot` include a `checks` array. The revision
still hashes the raw snapshot, without response annotations.

```text
POST /api/check
{"target":{"source":"demo","library":"examples","cell":"Differential pair","view":"schematic"},"revision":"<current revision>","changes":[]}
```

The endpoint requires the same Host/Origin checks as other POST routes. Its body
limit is 65536 bytes. A stale revision returns HTTP 409. It does not write state,
read master cells, edit OA, or run simulation. The browser debounces draft edits
for 300 ms. It aborts obsolete requests and ignores late responses.

```bash
vwb check --source demo
vwb check --source demo --file request.json
vwb check --source virtuoso --library scratch --cell amplifier --bridge-docker eda-container
```

The file can contain a create payload, a stored design request, or a show response.
`--file -` reads standard input. Its target and revision must match the selected
snapshot. Stored connect review metadata is recomputed, not trusted. Findings do
not change exit code 0. Invalid input and stale revisions use the normal CLI codes.

## Preview and limits

The preview reuses ordered request validation and its working snapshot. A connect
merges logical nets, including pin and wire net names, but does not draw a route.
A disconnect clears one terminal's net. A removal deletes the instance terminals.
Parameter edits do not change connectivity. New pins have no known coordinates.
New instances have no inferred terminals. If later changes explicitly name one
of their terminals, the preview uses a logical stub without coordinates. It cannot
verify that terminal against its master. Full request creation still validates
master terminals through the existing validation path.

This is not ERC, LVS, or a routing engine. Pin figures are represented by centers,
not contact areas. Net labels are absent. Labels can legally connect separate
wire groups, so `net-not-joined` is always a warning. The checker does not infer
shorts between differently named nets or test device operating conditions. The
canvas previews horizontal flips, including terminal positions. Wires do not move.
Other draft checks describe logical changes only. A horizontal flip mirrors
terminal positions about the instance bbox center. The agent must repair the
broken wiring after approval. See [orientation conventions](orientation.md).
