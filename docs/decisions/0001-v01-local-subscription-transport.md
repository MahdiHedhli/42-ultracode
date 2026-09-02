# ADR 0001: Local Subscription-Native Transport for v0.1

**Status**: Accepted
**Date**: 2026-08-14

## Context

42 Ultracode needs to remove content copying from the internal planner/executor
loop without claiming unsupported automatic ChatGPT thread continuation. The core
must use existing ChatGPT/Codex subscription authentication, keep state outside
model control, and remain restartable on a local developer machine.

## Decision

Use a Python controller with SQLite persistence and a role-scoped stdio MCP
server. Planner, worker, and control roles call typed tools explicitly. An optional
Codex CLI worker adapter claims one instruction, runs a compatible model under the
existing ChatGPT login, and submits a structured result. The controller—not the
adapter—owns limits, policy, events, and state transitions.

## Proven Capabilities

- Codex CLI 0.141.0 is logged in using ChatGPT; doctor output reports no stored
  API key and successful ChatGPT backend connectivity.
- A read-only `codex exec` request with `--ignore-user-config -m gpt-5.5`
  completed under that subscription.
- A two-turn read-only `ultracode worker-once` run completed through the Codex
  CLI adapter using `gpt-5.5`; both typed results were persisted and the planner
  explicitly completed the run. The sanitized record is in
  `docs/dogfood/2026-08-14-v01-evidence.json`.
- Python standard-library SQLite is locally available and adequate for the
  single-user append-only event design.
- Codex exposes local MCP capability; the project can validate a stdio MCP server
  through subprocess integration tests.
- In the validated `uv 0.11.17` / CPython 3.13 environment, an editable console
  entry point could not resolve this checkout path containing spaces. A regular
  wheel install via `uv sync --no-editable` and `uv run --no-editable` was
  verified, so those flags are part of the reproducible command path.

## Unavailable or Unproven Capabilities

- No automatic arbitrary ChatGPT Chat ↔ Codex thread continuation was demonstrated.
- Direct ChatGPT desktop tool loading is a manual environment validation, not an
  automated test in this workspace.
- The current default `gpt-5.6-terra` fails with Codex CLI 0.141.0 because the
  server requires a newer version. This prevents use of that default until Codex is
  updated or a compatible configured model is selected.
- `app-server` and `remote-control` are experimental. Their start/resume/read/
  steer/interrupt possibilities are noted for future adapters, not required by the
  v0.1 core.

## Validation Addendum (2026-09-02)

- `codex-cli 0.152.1` reported a ChatGPT login and two fresh `codex exec`
  worker sessions discovered the real worker-only MCP endpoint, claimed their
  turns, and submitted structured results to one shared local run.
- Separate role-scoped planner and control MCP processes read, continued,
  completed, and replayed that run to `COMPLETE` with eleven ordered events and
  two iterations. The public-safe record is
  `docs/dogfood/2026-09-02-live-mcp-acceptance.json`.
- Codex CLI 0.152.1 sent `{"_meta":{"progressToken":0}}` to `tools/list`.
  The server accepts the standard nullable `cursor` and object metadata
  envelope, while still rejecting a non-null cursor and unexpected top-level
  fields.
- The installed non-editable console script can lag checkout source after a
  local edit. Checkout MCP configurations therefore use
  `python -m ultracode.mcp.server` with an absolute checkout and database path;
  release/console testing requires an explicit package rebuild.
- Non-interactive `codex exec` needs `--approve-for-me` for expected local
  state-mutating MCP calls. With its default `never` approval policy, Codex
  discovered the worker tools but refused the claim safely.

This addendum proves the local MCP/Codex worker transport. It does not change
the Level C classification or prove automatic ChatGPT desktop planner loading.

## Consequences

The demonstrated product level is **Level C** shared state with explicit planner
and executor invocation. The Codex CLI adapter materially advances a **Level B**
execution path once a worker process is launched, but planner re-entry remains
explicit. A future native ChatGPT continuation adapter fits behind the planner
transport boundary without changing state or protocol.

The local database is not a multi-user security boundary. Filesystem permissions,
trusted local clients, and local database backups remain operator responsibilities.
Hash chaining detects ordinary accidental mutation but does not protect against a
host administrator who can replace the entire database.
