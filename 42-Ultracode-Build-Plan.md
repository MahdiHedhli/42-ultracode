# 42 Ultracode

**Build Plan and Product Roadmap**

**Target:** v0.1 dogfood of an internal ChatGPT ↔ Codex loop, followed
by v0.2 external adversarial specialists.

## Product Vision

42 Ultracode is a subscription-native orchestration layer for iterative
software engineering. ChatGPT acts as planner, critic, and judge; Codex
acts as repository-aware executor; 42 Ultracode owns state, handoffs,
evidence, lifecycle, limits, and policy.

The longer-term goal is a disciplined engineering harness. Reusable
Skills should impose methodologies such as GitHub Spec Kit,
Karpathy-style loops, Ponytail discipline, security-first engineering,
testing, performance work, and adversarial review without coupling those
methodologies to the orchestration engine.

## Constitution

1.  **Subscription first.** v0.1 must use existing ChatGPT/Codex
    subscription-backed sessions and require no OpenAI API key for its
    core loop.
2.  **Supported interfaces first.** Prefer plugins, Skills, MCP, hooks,
    Codex-supported interfaces, and local IPC before UI automation.
3.  **Ultracode owns state.** Agents cannot authoritatively alter
    lifecycle, limits, policy, approvals, or history.
4.  **Typed handoffs.** Exchange objective, task, context, constraints,
    completion criteria, results, evidence, blockers, and questions
    instead of whole transcripts.
5.  **Evidence over assertion.** Results should capture tests, commands,
    diffs, commits, benchmarks, errors, and uncertainty.
6.  **Bounded autonomy.** Agents cannot raise their own limits, rewrite
    policy, bypass approvals, or rewrite history.
7.  **Human interruptibility.** Status, pause, resume, stop, history,
    and escalation are mandatory.
8.  **Dogfood before generalization.** Solve the real ChatGPT ↔ Codex
    workflow before building a general agent framework.
9.  **Skills carry discipline.** Methodology belongs in composable
    Skills, not the transport layer.
10. **Reconstructability.** Persisted events must reconstruct current
    run state.

## Spec Kit Workflow

Use GitHub Spec Kit as the primary development discipline:

``` text
Constitution → Specify → Clarify → Plan → Tasks → Analyze → Implement → Converge
```

Initial specs:

-   `001-internal-loop`
-   `002-loop-recovery`
-   `003-discipline-skills`
-   `004-adversarial-specialists`

Keep v0.2 external agents out of `001-internal-loop`.

## Proposed Repository

``` text
42-ultracode/
├── .codex-plugin/plugin.json
├── .agents/plugins/marketplace.json
├── skills/
│   ├── ultracode-planner/
│   ├── ultracode-worker/
│   ├── ultracode-control/
│   └── disciplines/
│       ├── speckit/
│       ├── karpathy-loop/
│       ├── ponytail/
│       └── security/
├── ultracode/
│   ├── controller/
│   ├── protocol/
│   ├── state/
│   ├── transport/
│   ├── mcp/
│   └── policy/
├── hooks/
├── specs/
├── tests/{protocol,state,integration,dogfood}/
├── AGENTS.md
├── pyproject.toml
└── README.md
```

## v0.1 Internal Loop

A user gives ChatGPT an engineering objective. ChatGPT produces a
bounded instruction. 42 Ultracode transfers it to Codex. Codex executes
and submits a structured result. The planning side evaluates evidence
and chooses CONTINUE, HUMAN_REQUIRED, FAILED, or COMPLETE.

``` text
NEW → PLANNING → READY_FOR_CODEX → CODEX_RUNNING → CODEX_COMPLETE → REVIEWING
                                                               ├→ READY_FOR_CODEX
                                                               ├→ HUMAN_REQUIRED
                                                               ├→ FAILED
                                                               └→ COMPLETE
```

### Instruction Contract

Every instruction contains:

-   Goal
-   Context
-   Constraints
-   Done When

Optional fields include relevant files, required tests, prohibited
changes, evidence requirements, and selected discipline Skills.

### Result Contract

``` json
{
  "status": "completed",
  "summary": "...",
  "evidence": [],
  "changed_files": [],
  "tests": [],
  "commands": [],
  "commit": null,
  "blockers": [],
  "questions": [],
  "remaining_uncertainty": [],
  "recommended_next_action": "..."
}
```

Every state-changing action also creates an immutable ordered event.

## Phase 0: Feasibility Spikes

### Spike 001: Cross-Surface Handoff

Test plugin behavior from ChatGPT Chat, ChatGPT Work if relevant, Codex
desktop, and Codex CLI.

Minimum proof:

``` text
ChatGPT → ultracode.submit_instruction()
Codex   → ultracode.claim_instruction()
Codex   → ultracode.submit_result()
ChatGPT → ultracode.read_result()
```

Classify actual support:

-   **A:** automatic Chat ↔ Codex continuation
-   **B:** automatic Codex execution with explicit Chat continuation
-   **C:** shared state requiring explicit invocation on both sides

Do not assume undocumented thread-injection behavior.

### Spike 002: Subscription-Backed Codex Control

Validate current Codex interfaces for start, resume, read, turn
execution, steering, interruption, and account/rate-limit inspection
where available. Prove the execution path remains
subscription-authenticated.

### Spike 003: Persistence

Prefer SQLite in plugin-writable local storage. Avoid Redis and cloud
infrastructure.

### Exit Gate

Prove: subscription-backed session → controlled Codex work → persisted
structured result → planning surface consumes result.

If arbitrary ChatGPT threads cannot be automatically advanced, use a
subscription-backed planner/executor fallback rather than brittle GUI
automation. Keep planner identity abstract so native ChatGPT planning
can replace the fallback later.

## Phase 1: Protocol Foundation

-   **T001:** Run schema.
-   **T002:** Ordered immutable event schema.
-   **T003:** Instruction schema.
-   **T004:** Evidence-rich result schema.
-   **T005:** State-transition validator.
-   **T006:** Deterministic event replay.

**Gate:** protocol and replay tests pass without ChatGPT, Codex, or
plugin runtime.

## Phase 2: Core

Implement:

``` text
create_run()
get_run()
claim_turn()
submit_instruction()
submit_result()
request_human()
pause_run()
resume_run()
complete_run()
stop_run()
```

Initial SQLite tables: `runs`, `events`, `leases`, `artifacts`.

Invariants: one active actor per turn; iteration never decreases;
terminal runs do not silently restart; agents cannot raise limits or
modify policy; history is append-only; interrupted writes cannot corrupt
state.

## Phase 3: Local MCP

Planner capabilities:

``` text
ultracode_create_run
ultracode_read_run
ultracode_submit_instruction
ultracode_read_result
ultracode_complete_run
ultracode_request_human
```

Worker capabilities:

``` text
ultracode_claim_instruction
ultracode_submit_result
ultracode_report_progress
ultracode_report_blocker
```

Control capabilities:

``` text
ultracode_pause
ultracode_resume
ultracode_stop
ultracode_status
ultracode_history
```

Planner and worker capabilities should be separately permissioned.

## Phase 4: Skills

### `ultracode-planner`

Understand objective → read result → separate evidence from assertion →
evaluate completion → issue next bounded task or escalate/complete.

### `ultracode-worker`

Claim task → inspect workspace → execute within constraints → validate →
capture evidence → submit structured result. Never silently redefine the
objective.

### `ultracode-control`

Human workflows: status, pause, resume, stop, why, history.

## Discipline Skills

A discipline Skill changes **how engineering work is performed**, not
how messages are transported.

### Spec Kit

Require appropriate Spec Kit artifacts, preserve constitution
constraints, and prevent implementation from silently outrunning
specification.

### Karpathy Loop

Use an explicit iterative loop:

``` text
hypothesis → implementation → measurement → evaluation → retain/revert/revise → repeat
```

Specify the exact desired discipline before implementation rather than
relying on the label alone.

### Ponytail

Create an explicit Ponytail Skill specification defining concrete
behaviors, checkpoints, and verification requirements before
implementing it.

### Security

Baseline and optional enhanced controls should include trust-boundary
review, auth/authz impact, credential isolation, dependency review,
validation, least privilege, dangerous-command review, secret scanning,
security tests, and residual-risk reporting.

### Composition

Runs may eventually select:

``` yaml
disciplines:
  - speckit
  - security
  - karpathy-loop
```

Resolve Skills into a coherent execution contract. Do not concatenate
giant prompt files. Test precedence, conflicts, duplication, context
growth, and completion semantics.

## Phase 5: Plugin Packaging

Package the proven core using current OpenAI plugin conventions:

``` text
Plugin
├── Skills
├── MCP configuration
├── Hooks
├── Local controller/state integration
└── ChatGPT/Codex mappings as supported
```

Develop through a repository-local plugin/marketplace configuration
first. Public distribution is out of scope for v0.1.

## Phase 6: Autonomous Loop

Enable repetition only after persistence and recovery are proven.

``` python
while run.status not in TERMINAL:
    enforce_limits(run)
    instruction = planner.next(run)
    validate_instruction(instruction)
    submit_instruction(instruction)

    result = executor.run(instruction)
    validate_result(result)
    submit_result(result)

    decision = planner.review(result)
    if decision == COMPLETE:
        complete_run()
    elif decision == HUMAN_REQUIRED:
        request_human()
    else:
        advance_iteration()
```

The controller, not the models, owns iteration, state, timeouts, stop
conditions, policy, and approvals.

## Phase 7: Dogfood

42 Ultracode must use 42 Ultracode to develop 42 Ultracode.

A dogfood task must exercise multiple planner/executor iterations
against the Ultracode repository with no manual prompt copying.

## v0.1 Acceptance Criteria

-   [ ] Subscription-backed ChatGPT/Codex authentication.
-   [ ] No OpenAI API key required for the core loop.
-   [ ] No manual planner → Codex prompt copying.
-   [ ] No manual Codex → planner result copying.
-   [ ] At least 10 sequential iterations survive.
-   [ ] Instructions are delivered exactly once or safely retried.
-   [ ] Every Codex result is persisted.
-   [ ] Complete event history is retained.
-   [ ] State reconstructs from events.
-   [ ] Pause, resume, stop, and human escalation work.
-   [ ] Limits remain outside model control.
-   [ ] Process interruption does not corrupt state.
-   [ ] Restart can resume a prior run.
-   [ ] A meaningful Ultracode repository task completes through the
    loop.

### v0.1 Non-Goals

No Claude/Grok/Gemini integration, parallel workers, distributed queue,
cloud controller, Redis, dashboard, mobile app, multi-user service,
general workflow DSL, arbitrary agent graph, public marketplace release,
or long-term semantic memory.

## v0.2: Adversarial Specialists

v0.2 adds external specialist agents without changing the core state
machine. Codex remains their supervisor.

``` text
Planner → 42 Ultracode → Codex
                         ├→ Claude specialist
                         ├→ Grok specialist
                         └→ other specialist
```

Where officially supported, external tools should use their existing
subscription-authenticated CLI/session.

External specialists may produce evidence and recommendations. They may
**not** advance the Ultracode iteration, change the objective, declare
completion, increase limits, alter policy, bypass approvals, or directly
control the planner.

Candidate roles:

-   independent review
-   red-team
-   architecture challenge
-   bug hunt
-   security review
-   performance review
-   alternative implementation
-   falsification

Prefer falsification over model voting. For example: planner proposes
hypothesis H; Codex investigates; independent specialists attempt to
falsify it or propose alternatives; Codex runs discriminating tests;
evidence determines the surviving explanation.

## Version Roadmap

  -----------------------------------------------------------------------
  Version                             Milestone
  ----------------------------------- -----------------------------------
  0.0.1                               Spec Kit bootstrap and constitution

  0.0.2                               Cross-surface feasibility proof

  0.0.3                               Protocol and deterministic replay

  0.0.4                               SQLite core

  0.0.5                               Local MCP server

  0.0.6                               Planner/worker/control Skills

  0.0.7                               Local ChatGPT/Codex plugin

  0.0.8                               Subscription-backed Codex execution

  0.0.9                               Bounded autonomous loop

  **0.1.0**                           **Dogfood: meaningful repo task
                                      completed without manual prompt
                                      shuffling**

  0.1.x                               Recovery, evidence, observability,
                                      UX hardening

  **0.2.0**                           **External adversarial specialist
                                      loop**
  -----------------------------------------------------------------------

## Immediate Next Steps

1.  Create the `42-ultracode` repository.
2.  Initialize GitHub Spec Kit with Codex/Skills integration.
3.  Write the constitution from this plan.
4.  Create `001-internal-loop`.
5.  Execute Spike 001 before committing to transport details.
6.  Execute subscription-control and persistence spikes.
7.  Freeze the v0.1 protocol.
8.  Implement deterministic core and replay.
9.  Add MCP and core Skills.
10. Package the local plugin.
11. Enable bounded looping.
12. Dogfood Ultracode against its own repository.
13. Harden 0.1 before beginning adversarial 0.2.

## Product Boundary

**42 Ultracode is the orchestration substrate. Skills are the
engineering discipline. Codex is the execution plane. ChatGPT is the
planning/judgment plane.**

Keeping those concerns separate is what allows the same loop to evolve
from a simple prompt shuttle into a rigorous, composable
software-engineering system without turning the core into a monolithic
agent framework.
