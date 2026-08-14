---
name: ultracode-control
description: Operate and explain 42 Ultracode v0.1 run state through control-role MCP tools. Use when a human needs status, ordered history, pause, resume, or stop for a bounded run without gaining planner or worker authority.
---

# Ultracode Control

Use the control surface to keep a run interruptible and understandable. Read
[the operation reference](references/operations.md) before changing a run.

1. Read status and ordered history before pausing, resuming, or stopping a run.
2. Pause with a concise reason whenever work must stop temporarily; confirm the
   saved safe resume target.
3. Resume only after the human understands the reason for the pause and the
   current state permits it.
4. Stop with a reason for an intentional terminal shutdown. Preserve history;
   do not try to reopen a stopped or other terminal run through this role.
5. Explain state from persisted evidence and identify the next allowed actor or
   needed human decision.

Do not submit planner instructions/results, change policy or iteration limits,
edit event history, or treat role separation as multi-user authentication.
