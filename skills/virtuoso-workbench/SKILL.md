---
name: virtuoso-workbench
description: Read and claim local design requests. Send plans and results with the vwb JSON CLI.
---

# Virtuoso Workbench

Read `docs/agent-protocol.md` before you work on a request.
Use `vwb agent guide` if the repository is not available.
Use the same workflow in Devin, Claude Code, pi, and other harnesses.
Set a distinct agent name for each worker.
Claim a request before you send a plan.
Keep the claim token private.
Wait for human approval.
Do not edit OA before approval.
Use a scratch library unless the request says otherwise.
Check the base revision with `vwb snapshot` before start.
Use the Bridge outside this tool for approved OA edits.
Send heartbeats during long simulations.
Send a result for human acceptance.
