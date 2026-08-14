# Research: 001 Internal Loop

**Date**: 2026-08-14

## Spike 001 — Cross-Surface Handoff

### Decision

Use a local role-scoped stdio MCP server backed by the controller's local state.
Planner and worker surfaces explicitly invoke their permitted tools against the
same database. The transport does not attempt to insert messages into arbitrary
ChatGPT threads or automate desktop UI interaction.

### Evidence

- Spec Kit 0.15.2 initialized its Codex integration and installed Skills in
  `.agents/skills`.
- Codex CLI 0.141.0 exposes local MCP configuration and a stdio MCP server mode.
- The local desktop/Codex configuration contains MCP support. Direct local
  ChatGPT Chat tool invocation was not available to this build session, so that
  final desktop step remains a documented manual validation.

### Classification

**Level C — shared state requiring explicit invocation on both sides.** The
controller and its local protocol are proven by automated integration tests. The
Codex side is supportable through local MCP configuration. ChatGPT Chat automatic
continuation is not proven and is not claimed; a planner must explicitly invoke a
planner tool when the current desktop capability requires it.

### Alternatives Considered

- **Level A automatic Chat ↔ Codex continuation**: rejected because no supported
  arbitrary ChatGPT-thread continuation primitive was demonstrated.
- **GUI automation**: rejected by the Constitution as brittle and unsafe.
- **Transcript shuffling**: rejected because it loses typed handoffs and makes
  evidence/replay unreliable.

## Spike 002 — Subscription-Backed Codex Control

### Decision

Provide an optional Codex CLI executor adapter with a configurable compatible
model. It uses the existing CLI authentication and submits only its structured
result back through the controller. It is not the source of authoritative state.

### Evidence

- `codex login status` reported `Logged in using ChatGPT`.
- `codex doctor` reported a ChatGPT auth mode, stored ChatGPT tokens, no stored
  API key, and successful ChatGPT backend connectivity.
- A no-write, ephemeral command using `codex exec --ignore-user-config -m
  gpt-5.5` completed successfully and returned a `turn.completed` event. This
  proves a subscription-authenticated execution path without an API key.
- A two-turn read-only `ultracode worker-once` dogfood run completed with
  `gpt-5.5`; the controller persisted typed worker results and then accepted an
  explicit planner completion. Its sanitized evidence is retained in
  `docs/dogfood/2026-08-14-v01-evidence.json`.
- The installed default `gpt-5.6-terra` model failed before execution with a
  server message requiring a newer Codex version. This is a local CLI/model
  compatibility blocker, not an authentication failure.
- The installed CLI advertises `exec`, `resume`, `app-server`, and
  `remote-control`; app-server/remote-control are experimental and are not a
  required runtime dependency for v0.1.

### Implication

Level B-style automatic execution can be exercised by running the local worker
adapter after a planner submits an instruction, while planner re-entry remains
explicit. The default environment needs a Codex update or model override before
using its configured default model. The implementation records this as an
operational preflight instead of silently falling back to an API.

## Spike 003 — Persistence

### Decision

Use SQLite through the Python standard library. The event log is append-only,
ordered, transactionally written, and replayed to rebuild controller state.

### Evidence

- Python 3.14.6 and SQLite 3.53.4 are available locally.
- SQLite supports transactional writes, unique constraints, immutable-event
  triggers, and a single-file local database appropriate to the single-user v0.1
  trust boundary.

### Alternatives Considered

- **Flat JSON files**: rejected because leases, ordered writes, idempotency, and
  crash-safe mutation would need a new ad-hoc transaction layer.
- **Redis/PostgreSQL/cloud queue**: rejected as unnecessary infrastructure and a
  v0.1 scope violation.

## Chosen Architecture

```text
explicit planner tool call ─┐
                            v
                     role-scoped MCP
                            v
                   controller + SQLite
                    ├─ append-only events
                    ├─ deterministic replay
                    ├─ policy and leases
                    └─ structured artifacts
                            ^
                            │
           optional subscription-authenticated Codex CLI worker
```

The controller is the sole state authority. The planner, worker, transport, and
policy layers are separated at the interface boundary so that a future native
ChatGPT continuation adapter can replace the explicit planner invocation without
redesigning persistence or workflow rules.
