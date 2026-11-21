# World horizontal flips

`flip-horizontal` mirrors one instance about the vertical axis through its
world-coordinate bounding-box center. It does not mirror about the instance origin.
The request has `instance`, `from_orientation`, and `to_orientation`.
Ordered validation checks the current orientation after every earlier change.
New instances start at `R0`. A second flip must use the first flip's output.
The browser instead toggles its one draft flip off on the second click.

For column vectors, `new = MY × current`, with
`MY = [[-1, 0], [0, 1]]`. Rotation is counterclockwise.
Virtuoso compound names apply the mirror first and then the rotation:
`MYR90 = R90 × MY`, and `MXR90 = R90 × MX`.

| From | To |
| --- | --- |
| R0 | MY |
| R90 | MXR90 |
| R180 | MX |
| R270 | MYR90 |
| MY | R0 |
| MYR90 | R270 |
| MX | R180 |
| MXR90 | R90 |

Convention evidence: Cadence's Andrew Beckett explains the left-to-right mirror,
then rotation order and gives numerical `dbTransformPoint` results in
[this Cadence Community answer](https://community.cadence.com/cadence_technology_forums/f/custom-ic-skill/41859/bug-in-dbtransformpoint).
For `(2.15, 2.05)`, `MYR90` produces `(-2.05, -2.15)` and `MXR90` produces
`(2.05, 2.15)`. The Python tests include those results. No Cadence code is copied.
All eight cases have matrix composition and flip-twice tests in Python and TS.

For center `(cx, cy)`, each world point changes to `(2cx - x, y)`. The preview
reflects terminal positions, the instance origin, and bbox corners. It normalizes
the bbox corners again; the bounds and center therefore stay fixed. Unknown
coordinates stay unknown. An added instance can change orientation before its
geometry is known. Wires stay where they were. Python checks report the newly
unattached wire ends. The canvas uses the same reflection formula for its preview;
connectivity checks remain Python-only.

The agent must apply `to_orientation` to the same OA instance after approval.
It must preserve the recorded bbox center, not the old origin. It must repair
wiring and then run `vwb check`. The server never edits OA. The canvas remains a
symbolic device view, not a full OA symbol renderer. It does not model geometry
changes caused by parameter edits.
