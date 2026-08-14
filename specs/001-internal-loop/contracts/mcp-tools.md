# Local MCP Tool Contract

The server uses JSON-RPC over stdio and implements the MCP `initialize`,
`tools/list`, and `tools/call` requests. It returns JSON content and structured
content for every tool result. Starting a server with one role exposes only that
role's tools; role selection is a local capability boundary, not multi-user
authentication.

## Planner Role

| Tool | Required arguments | Result |
| --- | --- | --- |
| `ultracode_create_run` | `objective`, `idempotency_key`, optional `max_iterations` | New run snapshot. |
| `ultracode_read_run` | `run_id` | Replayed run snapshot. |
| `ultracode_submit_instruction` | `run_id`, instruction fields, `idempotency_key` | Ready run snapshot and instruction ID. |
| `ultracode_read_result` | `run_id` | Latest structured executor result. |
| `ultracode_complete_run` | `run_id`, `rationale`, `idempotency_key` | Completed run snapshot; requires result evidence. |
| `ultracode_request_human` | `run_id`, `reason`, `idempotency_key` | Human-required run snapshot. |

## Worker Role

| Tool | Required arguments | Result |
| --- | --- | --- |
| `ultracode_claim_instruction` | `run_id`, `worker_id`, `idempotency_key` | Instruction plus lease token. |
| `ultracode_submit_result` | `run_id`, `worker_id`, `lease_token`, result fields, `idempotency_key` | Complete result snapshot. |
| `ultracode_report_progress` | `run_id`, `worker_id`, `lease_token`, `message`, `idempotency_key` | Progress event acknowledgement. |
| `ultracode_report_blocker` | `run_id`, `worker_id`, `lease_token`, `reason`, `idempotency_key` | Human-required run snapshot. |

## Control Role

| Tool | Required arguments | Result |
| --- | --- | --- |
| `ultracode_pause` | `run_id`, `reason`, `idempotency_key` | Paused run snapshot. |
| `ultracode_resume` | `run_id`, `idempotency_key` | Resumed run snapshot. |
| `ultracode_stop` | `run_id`, `reason`, `idempotency_key` | Stopped run snapshot. |
| `ultracode_status` | `run_id` | Replayed run snapshot. |
| `ultracode_history` | `run_id` | Ordered redacted events. |

All mutating tools validate their role, state, payload schema, and idempotency key.
Unknown tools and role-inappropriate tools fail with a JSON-RPC error. Tool
arguments never execute shell commands.
