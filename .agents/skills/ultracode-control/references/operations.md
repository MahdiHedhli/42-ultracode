# Control Operation Reference

## Permitted tools

- `ultracode_pause`
- `ultracode_resume`
- `ultracode_stop`
- `ultracode_status`
- `ultracode_history`

## State guidance

`PAUSED` preserves a controller-recorded safe resume target. `STOPPED`,
`HUMAN_REQUIRED`, `FAILED`, and `COMPLETE` are terminal in v0.1; they do not
silently restart. Use history to explain the last actor, event, evidence, and
pending decision. History is redacted and append-only; do not edit the database
to repair it.

## Human-control checklist

Before a mutating control call, record the run ID, reason, current state, and
idempotency key. After it, re-read status/history and report the actual outcome.
Escalate suspected payload tampering, lost evidence, or dangerous requested work
instead of attempting to repair controller state manually.
