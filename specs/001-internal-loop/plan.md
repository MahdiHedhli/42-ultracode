# Implementation Plan: Internal ChatGPT/Codex Loop

**Branch**: `001-internal-loop` | **Date**: 2026-08-14 | **Spec**:
[spec.md](spec.md)

**Input**: Feature specification from `specs/001-internal-loop/spec.md`

## Summary

Deliver a local, single-user orchestration core that replaces manual prompt
copying with typed planner instructions and executor results. The controller owns
policy, an append-only event history, bounded state transitions, leases, and
replay. A role-scoped stdio MCP interface provides the planner, worker, and human
control surfaces. A small Codex CLI adapter preserves the subscription-backed
execution path where the installed CLI and selected model are compatible.

## Technical Context

**Language/Version**: Python 3.11+ (validated with local Python 3.14.6)

**Primary Dependencies**: Python standard library at runtime; `pytest`, `ruff`,
and `mypy` as repository-managed development tools through `uv`

**Storage**: Local SQLite database with append-only events, run snapshots, leases,
artifacts, and idempotency records

**Testing**: `pytest` unit, integration, replay/recovery, security, MCP subprocess,
and dogfood harness tests

**Target Platform**: Local macOS/Linux developer machine running a
subscription-authenticated Codex CLI; no hosted service in v0.1

**Project Type**: Python library with CLI and local stdio MCP server

**Performance Goals**: Complete 100 local state transitions in under five seconds
on a developer machine; retain deterministic replay for every tested run

**Constraints**: No OpenAI API key for the core loop; no network database, no GUI
automation, no model-controlled policy changes, no command execution from report
fields, no external adversarial agents

**Scale/Scope**: One trusted local operator, one active worker per run, durable
local state, and a bounded maximum iteration count configured outside model input

## Constitution Check

*GATE: Passed before Phase 0 research; re-checked after Phase 1 design.*

| Constitution gate | Design response | Status |
| --- | --- | --- |
| Subscription First | Codex CLI adapter uses existing ChatGPT login; the core does not accept or require an API key. A compatible model is configurable because the local default model is version-incompatible. | Pass |
| Supported Interfaces First | Local stdio MCP and Codex CLI are selected; no GUI automation or thread injection is designed. | Pass |
| Ultracode Owns State | Controller validates transitions, limits, leases, policy, and append-only events transactionally. | Pass |
| Typed, Evidence-Rich Handoffs | Explicit instruction/result schemas and input validation are defined in the contracts. | Pass |
| Bounded Autonomy | Roles are capability-scoped; policy mutation is absent from planner/worker contracts. | Pass |
| Reconstructability | Event replay is a core implementation and test target; run rows are a rebuildable cache. | Pass |
| Skills Carry Discipline | Repository-local planner, worker, and control Skills guide behavior without granting state authority. | Pass |
| Dogfood Before Generalization | The dogfood harness uses this repository and v0.2 integrations are documentation-only. | Pass |
| Security and Least Privilege | Local role separation, secret redaction, path validation, no report-command execution, and residual-risk documentation are in scope. | Pass |
| Testable Simplicity | Standard-library SQLite/JSON-RPC implementation avoids orchestration frameworks. | Pass |

## Project Structure

### Documentation (this feature)

```text
specs/001-internal-loop/
├── checklists/requirements.md
├── clarifications.md
├── contracts/mcp-tools.md
├── data-model.md
├── plan.md
├── quickstart.md
├── research.md
├── spec.md
└── tasks.md
```

### Source Code (repository root)
```text
ultracode/
├── __init__.py
├── cli.py                  # Human/developer command surface and dogfood entrypoint
├── controller.py           # Transactional operations, persistence, and policy enforcement
├── executor.py             # Optional subscription-backed Codex CLI worker adapter
├── protocol.py             # Schemas, validation, redaction, events, and replay
├── mcp/
│   └── server.py            # Role-scoped stdio JSON-RPC/MCP surface
└── dogfood.py               # Multi-iteration self-dogfood scenario

tests/
├── unit/
│   ├── test_protocol.py
│   ├── test_replay.py
│   └── test_cli.py
├── integration/
│   ├── test_controller.py
│   ├── test_mcp.py
│   ├── test_executor.py
│   └── test_dogfood.py
└── security/
    └── test_security.py

.agents/skills/
├── ultracode-planner/
├── ultracode-worker/
└── ultracode-control/

docs/
├── decisions/0001-v01-local-subscription-transport.md
├── DOGFOOD.md
└── VALIDATION.md
```

**Structure Decision**: A single Python package keeps policy, persistence, and
transport close enough to test transactionally. The MCP server is a thin adapter;
it must not contain a second state machine. Skills live with the repository so
Codex loads discipline alongside Spec Kit skills.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

No Constitution Check violations require a complexity exception.

## Feature Loop adapter plan

Implement the F017 D0-D3 pilot as one standard-library adapter module around
the existing controller. Git guards protect the artifact frontier; existing
controller leases protect worker ownership. A restricted YAML reader avoids a
new runtime dependency. Publication uses adjacent atomic files in deterministic
order, while Git commit/push and aliased notification remain explicit transport
steps. All tests use disposable repositories, temporary databases, synthetic
content, and a denied checkpoint alias.
