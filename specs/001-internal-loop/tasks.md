---
description: "Actionable implementation tasks for 001-internal-loop"
---

# Tasks: Internal ChatGPT/Codex Loop

## D2 Security Repair

- [x] D2R-0 Bind all prompt authorization fields to trusted typed policy and enforce exact control-document schemas.
- [x] D2R-1 Bind prompt bytes, sidecar, path, commit, envelope, and ancestry before lease acquisition.
- [x] D2R-2 Verify complete expected-parent and live-frontier identities.
- [x] D2R-3 Make resolved aliases non-serializable and reject them recursively from durable payloads.
- [x] D2R-4 Compile privacy coverage fail-closed and scan staged publication diffs.
- [x] D2R-5 Project feature/state atomically at the Git boundary with expected-hash race checks.
- [x] D2R-6 Run adversarial regressions, full quality gate, dogfood, privacy review, and no-source-mutation round trip.

**Input**: Design documents in `specs/001-internal-loop/`

**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, and
`contracts/mcp-tools.md`

**Tests**: Required by the specification, Constitution, and validation plan.

**Organization**: Tasks are grouped by user story after shared infrastructure so
each story has an independent test checkpoint.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Establish a minimal reproducible Python package and repository hygiene.

- [x] T001 Create `.gitignore` and `pyproject.toml` with repository-managed quality tools.
- [x] T002 Create package metadata and public exports in `ultracode/__init__.py`.
- [x] T003 [P] Create initial developer validation configuration in `pyproject.toml`.
- [x] T004 [P] Create practical repository guidance in `AGENTS.md`.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Build the schema, state reduction, persistence, and security foundation
that every user story relies on.

- [x] T005 Create typed instruction/result/event schemas and input validation in `ultracode/protocol.py`.
- [x] T006 Create deterministic state reducer and transition validation in `ultracode/protocol.py`.
- [x] T007 Create secret redaction and workspace-relative path validation in `ultracode/protocol.py`.
- [x] T008 Create transactional SQLite schema, append-only event writes, and replay reads in `ultracode/controller.py`.
- [x] T009 Create protocol and replay unit tests in `tests/unit/test_protocol.py` and `tests/unit/test_replay.py`.
- [x] T010 Create security validation tests for untrusted payloads in `tests/security/test_security.py`.

**Checkpoint**: Foundation is independently testable and every state mutation can
be replayed without MCP or Codex.

---

## Phase 3: User Story 1 — Send a Bounded Engineering Instruction (Priority: P1) 🎯 MVP

**Goal**: Planner can create a bounded run and submit exactly one validated,
durable instruction without copying content into a worker prompt.

**Independent Test**: Create a run, submit a valid instruction, reject invalid or
over-limit submissions, and reconstruct the ready state from events.

- [x] T011 [P] [US1] Add create-run, read-run, and submit-instruction controller tests in `tests/integration/test_controller.py`.
- [x] T012 [US1] Implement run creation and immutable policy initialization in `ultracode/controller.py`.
- [x] T013 [US1] Implement planner instruction submission, iteration enforcement, and idempotency in `ultracode/controller.py`.
- [x] T014 [US1] Implement planner MCP tool definitions and handlers in `ultracode/mcp/server.py`.
- [x] T015 [US1] Add planner tool contract coverage in `tests/integration/test_mcp.py`.

**Checkpoint**: A valid planner handoff reaches `READY_FOR_CODEX`; a malformed,
duplicate, or policy-bypassing handoff does not corrupt history.

---

## Phase 4: User Story 2 — Execute and Return Evidence (Priority: P1)

**Goal**: Exactly one worker claims an instruction and returns an evidence-rich
result for planner review.

**Independent Test**: Concurrent claim attempts yield one owner; a valid result
becomes readable after controller restart; duplicate result delivery is safe.

- [x] T016 [P] [US2] Add claim, lease, duplicate-delivery, and result integration tests in `tests/integration/test_controller.py`.
- [x] T017 [US2] Implement turn claiming, lease expiry recovery, and progress events in `ultracode/controller.py`.
- [x] T018 [US2] Implement result submission, review entry, and worker blocker handling in `ultracode/controller.py`.
- [x] T019 [US2] Implement worker MCP tool definitions and handlers in `ultracode/mcp/server.py`.
- [x] T020 [US2] Implement subscription-backed Codex CLI preflight and one-turn adapter in `ultracode/executor.py`.
- [x] T021 [US2] Add worker MCP and executor parsing tests in `tests/integration/test_mcp.py` and `tests/integration/test_executor.py`.

**Checkpoint**: A worker result is structured, persisted, redacted where needed,
and available for a planner decision without a raw transcript shuttle.

---

## Phase 5: User Story 3 — Control, Recover, and Escalate a Run (Priority: P2)

**Goal**: A human can pause, resume, stop, inspect, recover, or escalate a run.

**Independent Test**: Pause/resume, process restart, expired worker lease, human
escalation, terminal state protection, and legal completion all pass.

- [x] T022 [P] [US3] Add pause/resume/stop/escalation/restart tests in `tests/integration/test_controller.py`.
- [x] T023 [P] [US3] Add illegal-transition and interrupted-execution replay tests in `tests/unit/test_replay.py`.
- [x] T024 [US3] Implement pause, resume, stop, request-human, and evidence-gated completion in `ultracode/controller.py`.
- [x] T025 [US3] Implement control MCP tool definitions and role capability checks in `ultracode/mcp/server.py`.
- [x] T026 [US3] Add control MCP subprocess coverage in `tests/integration/test_mcp.py`.

**Checkpoint**: Control actions are durable, terminal runs do not restart, and a
fresh controller reconstructs the active state solely from persisted events.

---

## Phase 6: User Story 4 — Understand Available Automation (Priority: P3)

**Goal**: Operators can reproduce the maximum supported local workflow and see
the exact automation boundary.

**Independent Test**: A local MCP exchange and multi-iteration dogfood scenario
run without manually copying any instruction or result contents.

- [x] T027 [P] [US4] Create dogfood scenario and evidence writer in `ultracode/dogfood.py`.
- [x] T028 [US4] Create human CLI and MCP server entrypoints in `ultracode/cli.py` and `ultracode/mcp/server.py`.
- [x] T029 [US4] Add dogfood harness tests in `tests/integration/test_dogfood.py`.
- [x] T030 [US4] Add a ten-sequential-iteration limit and replay regression in `tests/integration/test_dogfood.py`.
- [x] T031 [US4] Create planner, worker, and control Skills in `.agents/skills/ultracode-planner/SKILL.md`, `.agents/skills/ultracode-worker/SKILL.md`, and `.agents/skills/ultracode-control/SKILL.md`.
- [x] T032 [US4] Record desktop/plugin setup, architecture capability level, and replay validation in `docs/VALIDATION.md`.

**Checkpoint**: The project has a reproducible Level C dogfood path, a recorded
subscription execution proof, and no unsupported Level A claim.

---

## Phase 7: Polish and Cross-Cutting Concerns

**Purpose**: Publish an accurate, secure, reviewable v0.1 dogfood baseline.

- [x] T033 [P] Write public project overview, architecture, safety, and roadmap in `README.md`.
- [x] T034 [P] Write dogfood evidence interpretation and manual intervention record in `docs/DOGFOOD.md`.
- [x] T035 [P] Create local MCP configuration example in `.codex/config.toml.example`.
- [x] T036 Run formatter, linter, type checker, full tests, and dogfood command from `specs/001-internal-loop/quickstart.md`.
- [x] T037 Review tracked files for secrets, machine-specific data, dead code, stale specs, and v0.2 runtime leakage using `git diff --check`, `rg`, and `git status`.
- [x] T038 Commit meaningful milestones and publish verified `main` to the public GitHub repository.

## Dependencies and Execution Order

```text
Setup → Foundation → US1 (planner handoff) → US2 (worker result)
      → US3 (control/recovery) → US4 (dogfood/capabilities) → Polish
```

- US1 depends on Foundation.
- US2 depends on US1 because a worker must claim a planner instruction.
- US3 depends on Foundation and can be partly implemented alongside US2, but its
  end-to-end validation needs a worker claim.
- US4 depends on US1–US3.
- Polish depends on the desired user stories and their validation evidence.

## Parallel Opportunities

- T003/T004 can proceed after T001.
- T009/T010 can proceed after the matching Foundation implementation changes.
- The focused controller and MCP tests in T011/T016/T022/T023 can be authored in
  parallel with their separate implementation files.
- Documentation/Skills tasks T030–T034 can proceed once the interface is stable.

## Implementation Strategy

1. First make replay and policy enforcement correct without any model or MCP
   dependency.
2. Add the planner handoff and prove it independently.
3. Add a single-worker claim/result loop, then the control/recovery surface.
4. Exercise the same public interfaces with a multi-iteration self-dogfood run.
5. Only then package documentation and publish; do not add v0.2 adapters.

## Phase 8: F017 Feature Loop D0-D3 adapter

- [x] T039 Specify the thin Feature Loop adapter without changing core v0.1 lifecycle semantics.
- [x] T040 Add strict feature, state, privacy-policy, and prompt-envelope parsers.
- [x] T041 Add exact Git commit/path/hash, parent, sequence, and duplicate guards before lease claim.
- [x] T042 Add alias-safe durable binding, privacy scanning, notification evidence, and state projection.
- [x] T043 Add deterministic response/checksum/handoff/state publication with transport-only retry semantics.
- [x] T044 Add disposable frontier, replay, publication, privacy, path, checkpoint-denial, and no-source-mutation tests.
- [ ] T045 Revalidate the complete baseline, subscription transport, live F017 dry publication, and remote parity.
