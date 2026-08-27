# Feature Specification: Internal ChatGPT/Codex Loop

**Feature Branch**: `001-internal-loop`

## D2 Security Repair Addendum

Sequence 1 and later MUST acquire a worker lease only from a sealed prompt
identity that binds exact Git commit, repository-relative path, calculated
SHA-256, adjacent basename sidecar, parsed envelope, and control-base ancestry.
The live state MUST bind the complete parent response identity and agree with
the feature manifest on protocol status and machine sequence.

Resolved machine-local aliases are transport capabilities, not data records.
They MUST reject default serialization, copying, and pickling, and recursive
durable-payload validation MUST reject them at any depth. Privacy policy
construction MUST fail when a prohibited category lacks either a reviewed
structural detector or a non-serializable machine-local marker provider.

Feature and state projection MUST compare both expected live hashes before
writing and MUST be committed together as the atomic Git boundary. Complete
staged publication diffs remain subject to fail-closed automated scanning and
manual review.

**Created**: 2026-08-14

**Status**: Implemented and dogfood-validated

**Input**: User description: "Build the first usable subscription-native internal
ChatGPT and Codex orchestration loop. It must replace manual typed handoff with
bounded structured instructions and results, preserve human control and immutable
workflow history, work without an OpenAI API key in a ChatGPT-authenticated Codex
environment, and explicitly exclude external adversarial agents."

## User Scenarios & Testing *(mandatory)*

<!--
  IMPORTANT: User stories should be PRIORITIZED as user journeys ordered by importance.
  Each user story/journey must be INDEPENDENTLY TESTABLE - meaning if you implement just ONE of them,
  you should still have a viable MVP (Minimum Viable Product) that delivers value.

  Assign priorities (P1, P2, P3, etc.) to each story, where P1 is the most critical.
  Think of each story as a standalone slice of functionality that can be:
  - Developed independently
  - Tested independently
  - Deployed independently
  - Demonstrated to users independently
-->

### User Story 1 - Send a Bounded Engineering Instruction (Priority: P1)

As a planner, I can create a run and submit a bounded instruction with a goal,
context, constraints, and completion criteria so that an executor receives the
same task without either person copying a prompt between surfaces.

**Why this priority**: This is the smallest useful replacement for the manual
planner-to-executor handoff and establishes the workflow contract.

**Independent Test**: A planner submits one valid instruction, an executor can
retrieve exactly that instruction, and the persisted run shows a ready-to-execute
state without relying on a transcript copy.

**Acceptance Scenarios**:

1. **Given** a newly created run, **When** a planner submits a complete bounded
   instruction, **Then** the instruction is durably available for one executor
   turn and the run records the state change.
2. **Given** a planner submits an incomplete instruction, **When** the handoff is
   requested, **Then** it is rejected with an actionable validation error and the
   existing workflow history remains unchanged.

---

### User Story 2 - Execute and Return Evidence (Priority: P1)

As an executor, I can claim the current instruction and submit a structured
evidence-rich result so that the planner can assess progress without receiving a
giant raw transcript.

**Why this priority**: The return handoff closes the essential internal loop and
lets the planner make a grounded next-step decision.

**Independent Test**: One executor claims an available instruction, submits a
valid result, and a planner reads the exact result and evidence after a process
restart.

**Acceptance Scenarios**:

1. **Given** an instruction is ready, **When** one executor claims it, **Then** a
   second executor cannot simultaneously own that turn.
2. **Given** an executor has a valid claim, **When** it submits a structured
   result, **Then** the planner can read the result, including validation evidence,
   changed files, blockers, questions, and uncertainty.
3. **Given** an executor repeats the same delivery, **When** the system receives
   it again, **Then** the result is either safely recognized as the original or
   rejected without creating a second state change.

---

### User Story 3 - Control, Recover, and Escalate a Run (Priority: P2)

As a human operator, I can inspect a run, pause it, resume it, stop it, or
require human review so that automation remains bounded and a process failure
does not lose control of active work.

**Why this priority**: Durable human control is a safety requirement and turns a
handoff demo into a dogfoodable workflow.

**Independent Test**: A run is paused and resumed, another run survives a
controller restart, and an escalated run cannot continue until a human explicitly
acts.

**Acceptance Scenarios**:

1. **Given** a non-terminal run, **When** an authorized human pauses it, **Then**
   no executor can claim work until it is explicitly resumed.
2. **Given** a process stops after persisting events, **When** the controller is
   restarted, **Then** it reconstructs the same active state from history.
3. **Given** evidence is insufficient or a blocker requires judgment, **When** the
   planner requests human involvement, **Then** the run enters a visible human
   review state rather than silently continuing.

### User Story 4 - Understand Available Automation (Priority: P3)

As a dogfood operator, I can see which ChatGPT/Codex handoff level has actually
been demonstrated and follow reproducible setup instructions so that I do not
mistake explicit continuation for automatic cross-surface execution.

**Why this priority**: Accurate capability reporting prevents unsafe or misleading
automation claims while allowing the strongest supported workflow to be used.

**Independent Test**: The validation record identifies the demonstrated handoff
level, links the reproducible evidence, and calls out any manual continuation that
remains.

**Acceptance Scenarios**:

1. **Given** an environment lacks automatic ChatGPT thread continuation, **When**
   a run is used, **Then** the documentation presents the supported fallback and
   does not claim automatic continuation.

### Edge Cases

- A second executor attempts to claim an instruction while a valid turn owner
  exists.
- A result is malformed, includes unexpected fields, or includes a path outside
  the repository scope.
- The controller stops during an attempted state update or after a claim but
  before a result.
- A run reaches its externally configured iteration limit.
- A paused, stopped, failed, human-required, completed, or otherwise terminal
  run receives an illegal transition request.
- A duplicate delivery arrives after a network or process retry.
- An untrusted instruction or result contains recognizable credential material.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST let a planner create a bounded run with an objective
  and an externally configured maximum number of iterations.
- **FR-002**: The system MUST accept planner instructions containing goal, context,
  constraints, and completion criteria, with optional relevant files, required
  tests, prohibited changes, evidence requirements, and discipline Skills.
- **FR-003**: The system MUST make each pending instruction claimable by only one
  executor at a time and MUST reject or safely handle duplicate deliveries.
- **FR-004**: The system MUST accept executor results containing status, summary,
  evidence, changed files, tests, commands, commit, blockers, questions, remaining
  uncertainty, and a recommended next action.
- **FR-005**: The system MUST record every meaningful lifecycle change as an ordered,
  immutable history item sufficient to reconstruct a run's current state.
- **FR-006**: The system MUST support the lifecycle states needed to plan, await an
  executor, execute, review, escalate to a human, fail, complete, pause, resume,
  and stop a run.
- **FR-007**: The system MUST enforce iteration limits and governing policy outside
  planner and executor control; neither actor may raise a limit, alter policy, or
  silently restart a terminal run.
- **FR-008**: The system MUST preserve a valid run when an interrupted write, worker
  crash, malformed result, duplicate submission, or illegal transition occurs.
- **FR-009**: The system MUST provide separate planner, worker, and human-control
  capability surfaces rather than granting every actor all operations by default.
- **FR-010**: The system MUST expose the core workflow through a supported local
  interface and document how to exercise it from the available ChatGPT/Codex
  environment.
- **FR-011**: The system MUST redact recognizable credentials from persisted event
  payloads and MUST NOT execute commands merely because an executor reports them.
- **FR-012**: The system MUST document the highest demonstrated cross-surface
  automation level and explicitly identify unsupported automatic continuation.
- **FR-013**: The v0.1 runtime MUST NOT include external adversarial agents,
  distributed workers, cloud queues, generic workflow graphs, or a requirement for
  an OpenAI API key.

### Key Entities *(include if feature involves data)*

- **Run**: A bounded engineering workflow with an objective, lifecycle state,
  externally governed limits, and ordered history.
- **Instruction**: A planner-authored, bounded unit of executor work linked to one
  run iteration.
- **Execution Result**: An executor-authored report of work, evidence, blockers,
  and remaining uncertainty linked to a claimed instruction.
- **Event**: An immutable ordered record of a meaningful change that can recreate
  the run's state.
- **Turn Claim**: Time-bounded ownership of one ready instruction by one executor.
- **Policy**: Controller-owned rules that constrain limits, approval boundaries,
  and legal lifecycle changes.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A planner can deliver a complete instruction and an executor can
  retrieve it from the supported local interface with zero manual copying of the
  instruction contents.
- **SC-002**: For every automated test scenario, replaying persisted history
  reconstructs the same lifecycle state, iteration count, pending instruction, and
  last result as the live run.
- **SC-003**: The validation suite covers normal progression, pause/resume, failure,
  human escalation, completion, duplicate delivery, illegal transitions, process
  interruption, and controller restart with a 100% pass rate.
- **SC-004**: A bounded test run successfully processes at least 10 sequential
  planner/executor iterations without lowering or exceeding its configured limit.
- **SC-005**: A real dogfood scenario on this repository completes at least two
  planner/executor iterations, retains its run evidence, and identifies every
  manual intervention that remains.
- **SC-006**: The published documentation correctly classifies the actually
  demonstrated ChatGPT/Codex automation level and gives a reproducible manual
  validation path for unproven surfaces.

## Assumptions

- The first dogfood audience is a single trusted developer using an existing
  ChatGPT/Codex subscription-authenticated environment on one machine.
- A local supported interface can be configured separately in planner and executor
  surfaces even when an automatic ChatGPT thread continuation primitive is absent.
- Persistence is local to the trusted development machine; v0.1 does not promise
  multi-user authorization, network durability, or protection from a user with
  direct host access.
- The product may use explicit continuation at the ChatGPT planning surface if that
  is the strongest supported capability, but content itself must not be manually
  copied between planner and executor.
- A public license has not been selected; this is recorded as a pre-release
  decision rather than inferred.

## Feature Loop Protocol v1 adapter extension

The v0.1 controller MAY be wrapped by a thin Git artifact adapter for a bounded
Feature Loop dogfood. The adapter MUST preserve controller-owned events, replay,
leases, idempotency, lifecycle, role separation, and iteration ceilings.

- **FR-014**: The adapter MUST parse the feature manifest, feature state,
  privacy policy, and prompt envelope strictly and fail closed on malformed or
  ambiguous input.
- **FR-015**: An exact prompt commit, path, SHA-256, expected parent, feature,
  machine, sequence, and duplicate-artifact frontier MUST pass before a worker
  lease can be claimed.
- **FR-016**: Resolved local aliases MUST remain process-local and MUST NOT be
  serializable into events, logs intended for publication, feature state,
  notifications, responses, or handoffs.
- **FR-017**: Response, checksum, allowlisted handoff, and feature state MUST be
  prepared in deterministic order with resumable transport-only failure
  semantics. A publication, notification, or handoff retry MUST NOT repeat a
  durable worker result.
- **FR-018**: A policy-driven privacy scan MUST reject every prohibited
  identifier category before publication.
- **FR-019**: The first F017 dogfood round trip MUST expose no PulsarMLX write
  capability and MUST deny resolution of `CHECKPOINT_ROOT`.

This extension does not add automatic ChatGPT posting, cloud queues, a generic
workflow engine, checkpoint access, source-repository mutation, or a new
controller state machine.
