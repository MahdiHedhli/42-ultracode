# Dogfood Procedure: 42 Ultracode on 42 Ultracode

## Purpose

The first useful dogfood is not a generic agent demonstration. It is a bounded,
multi-turn engineering task on this repository that exercises the exact loop
v0.1 is meant to support:

```text
planner instruction → worker claim → repository work → structured result
                  → planner review → next bounded instruction or completion
```

The automated harness tests transport, persistence, replay, and recovery. The
real-repository procedure below tests the human-visible planning/execution loop.
They are complementary; neither should be described as proof of the other.

Sequence 11 runs under the accepted D4 policy and registers only the exact D6
checkpoint-free synthetic repack round-trip envelope. Qualification requires a
clean baseline, one leased worker, no aliases or checkpoint access, no D6
execution, and a final PASS handoff. Public-safe evidence is recorded in
`docs/dogfood/2026-08-28-d6-policy-registration-evidence.json`.

## Current automation boundary

This procedure is designed for **Level C**. A person explicitly invokes the
planner tool, explicitly starts or invokes the worker, and explicitly invokes
the planner's result-reading tool. The instruction/result *contents* move
through typed MCP calls and SQLite; they must not be copied through a chat
prompt.

Do not claim automatic ChatGPT thread continuation, Level A, or a fully
automatic Level B loop from this procedure. Any desktop-surface limitation must
be recorded as a manual intervention rather than worked around with GUI
automation.

## Scenario

Use this task on a current 42 Ultracode checkout:

> Inspect the event-replay and recovery implementation for correctness gaps.
> Implement the smallest justified fixes, add regression tests, review the
> resulting diff, and continue until the complete validation suite passes.

The task must take at least two planner/executor iterations. Keep a narrow scope:
it is a v0.1 controller/replay task, not permission to introduce external
adversarial agents, cloud infrastructure, a general workflow graph, or an API
key dependency.

## Setup

1. Start from a clean, reviewed branch. Install the project and run the baseline
   commands in [`docs/VALIDATION.md`](VALIDATION.md).
2. Create a fresh local database and configure separate planner, worker, and
   control MCP clients using [`.codex/config.toml.example`](../.codex/config.toml.example).
   Give each actor only its own role.
3. Create a run with a human-approved iteration ceiling high enough for the
   planned two turns. Let the controller set its default if no explicit human
   ceiling is needed.
4. Open a record for the run using the evidence template below. Store the raw
   database and evidence under ignored `.ultracode/`; sanitize any evidence
   intended for the repository.

## Turn 1 — investigate and make a bounded fix

The planner submits an instruction that includes all required fields:

- **Goal:** inspect replay/recovery correctness and address one demonstrated
  gap.
- **Context:** the active specification, data model, relevant implementation
  files, and prior validation output.
- **Constraints:** preserve append-only history and controller-owned policy;
  do not broaden v0.1 scope; do not bypass tests; do not paste credentials or
  raw transcripts into events.
- **Done When:** a concrete issue is fixed or a justified no-change finding is
  reported with focused tests and evidence.

The worker claims the instruction, inspects the workspace, makes only the
justified change, runs focused validation, and submits a result with changed
files, commands, tests, evidence, blockers, questions, uncertainty, and a
recommended next action. A partial or uncertain result is still useful evidence;
it is not completion.

The planner reads the structured result from the controller, separates observed
evidence from assertion, and decides whether a second turn is needed. Record the
tool invocation, not a pasted copy of the instruction/result.

## Turn 2 — regression review and full validation

The planner submits a second bounded instruction. It should review the first
diff and evidence, close any identified regression gap, run the full validation
suite, and verify the stated completion criteria. The worker again claims and
reports through the controller.

The planner may complete only if the result evidence supports every `Done When`
condition. Otherwise it must submit another bounded instruction or request
human review. A failed command, ambiguity, dangerous change, or insufficient
evidence is a reason to escalate, not silently reinterpret the objective.

## Scripted harness

Run the local protocol harness separately from the real task:

```sh
uv run --no-editable ultracode dogfood \
  --database .ultracode/dogfood.db \
  --evidence .ultracode/dogfood-evidence.json
```

The harness is expected to exercise multiple typed turns and a restart/replay
check. It is not evidence that a ChatGPT desktop task automatically continued
or that an arbitrary repository change was autonomously made. Attach its
sanitized output to the evidence record only after confirming it actually ran.

## Evidence template

Record this information for every real dogfood attempt:

```text
Date / commit:
Run ID:
Automation level: C
Iteration ceiling / iterations completed:
Event count / final state:
Planner tool calls explicitly triggered:
Worker launches or tool calls explicitly triggered:
Control actions:
Manual prompt copies (instruction bodies / result bodies): 0 / 0
Manual interventions still required:
Changed files:
Commands and tests actually run:
Restart/recovery result:
Evidence supporting completion:
Blockers / remaining uncertainty:
```

Count a person deciding to call a tool or approve a dangerous action as a manual
intervention. Do **not** count it as prompt shuffling unless they copied an
instruction/result body between the planning and execution surfaces. If a copy
occurred, record it honestly and do not claim the corresponding handoff was
eliminated.

## Completion criteria

A dogfood attempt is valid only when all of these are true:

- it has at least two planner/executor iterations;
- every handoff appears in the controller history and replays to the observed
  state;
- results contain evidence instead of a raw transcript assertion;
- the complete validation suite was run and its actual outcome recorded;
- restart/recovery behavior was exercised by the harness or task; and
- every remaining manual intervention is listed.

The maximum currently supported claim is a successful Level C run with zero
copied handoff contents. Higher claims need independently recorded supported
platform evidence.

## Executed evidence

The sanitized 2026-08-14 record is at
[`docs/dogfood/2026-08-14-v01-evidence.json`](dogfood/2026-08-14-v01-evidence.json).
It records a two-turn harness run and a two-turn read-only Codex CLI adapter
run. Both reached `COMPLETE`; the adapter record explicitly retains the planner
and worker launches that were still manual Level C actions.

The sanitized Sequence 9 D5R1 policy-registration recovery record is at
[docs/dogfood/2026-08-27-d5r1-policy-registration-recovery-evidence.json](dogfood/2026-08-27-d5r1-policy-registration-recovery-evidence.json).
It records registry qualification only; D5R1, repack, checkpoint access, and
inference were not executed.

The sanitized Sequence 13 D6R1 path-safety policy-registration recovery record
is at
[docs/dogfood/2026-08-28-d6r1-policy-registration-recovery-evidence.json](dogfood/2026-08-28-d6r1-policy-registration-recovery-evidence.json).
It records closed-registry qualification through the ordinary PASS recovery
frontier only. D6R1, D6, repack, checkpoint access, inference, Event 06, D4,
and D5 were not executed.
