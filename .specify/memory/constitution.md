<!--
Sync Impact Report
- Version change: template → 1.0.0
- Modified principles: none; initial adoption
- Added sections: Core Principles, v0.1 Boundaries and Security, Development Workflow
- Removed sections: template placeholders
- Follow-up TODOs: select a public software license before a release.
-->
# 42 Ultracode Constitution

## Core Principles

### I. Subscription First

The v0.1 core MUST use an existing ChatGPT/Codex subscription-authenticated
environment and MUST NOT require an OpenAI API key. A fallback may require an
explicit continuation by a human, but it MUST preserve subscription-backed
execution and document the handoff boundary. This keeps the product focused on
the workflow its users actually have.

### II. Supported Interfaces First

The project MUST prefer documented plugins, Skills, MCP, hooks, Codex
interfaces, and local IPC/state. GUI automation, screen scraping, undocumented
thread injection, and brittle UI assumptions are prohibited in the v0.1 core.
This keeps integration reproducible and safe to evolve.

### III. Ultracode Owns Authoritative State

Only the controller may authoritatively change workflow lifecycle, iteration
limits, policy, approval boundaries, or immutable history. Planner and worker
messages are untrusted inputs to the controller. This separates model judgment
from enforceable system control.

### IV. Typed, Evidence-Rich Handoffs

Instructions and results MUST use explicit schemas rather than transcript
shuffling. Instructions require goal, context, constraints, and completion
criteria; results require status, evidence, affected files, validation, blockers,
questions, uncertainty, and a recommended next action. Assertions without
relevant evidence MUST NOT be treated as completion proof.

### V. Bounded Autonomy and Human Interruptibility

Models MUST NOT raise their own limits, bypass approval requirements, silently
rewrite objectives, or rewrite history. Every run MUST support status, pause,
resume, stop, history inspection, and human escalation. Terminal runs MUST NOT
restart silently. The controller MUST make those bounds durable across restarts.

### VI. Reconstructability and Durable Evidence

Every meaningful state change MUST append an ordered immutable event. The current
run state MUST be deterministically reconstructable from persisted events, and
execution reports MUST retain relevant commands, tests, diffs, errors, commits,
and uncertainty after redaction. This makes recovery and review possible without
trusting an in-memory agent narrative.

### VII. Skills Carry Engineering Discipline

The transport engine MUST remain methodology-neutral. Composable Skills define
how work is done, including Spec Kit, iterative experimentation, security review,
and test discipline. Skills MUST not grant authority to alter policy or state, and
Ponytail behavior MUST remain a placeholder until a concrete methodology is
defined.

### VIII. Dogfood Before Generalization

The internal ChatGPT/Codex loop MUST be proven on 42 Ultracode before generic
agent graphs, distributed queues, dashboards, or external specialist agents are
added. v0.2 adversarial integrations are explicitly outside the v0.1 runtime.
This preserves a small, testable first product.

### IX. Security and Least Privilege

The project MUST validate untrusted payloads, separate planner/worker/control
capabilities, avoid executing worker-reported commands, redact recognizable
secrets before persistence, restrict filesystem semantics, and document residual
risks. Dangerous operations require a human approval boundary; local transport
must not be mistaken for remote multi-user authentication.

### X. Testable Simplicity

The v0.1 implementation MUST prefer the standard library, SQLite, explicit
schemas, append-only events, and deterministic tests over framework-heavy
abstractions. Uncertain changes follow a hypothesis → smallest change →
measurement → evaluation → retain/revise loop. Complexity needs evidence of need.

## v0.1 Boundaries and Security

v0.1 is a single-user, local, subscription-native orchestration substrate. It
MUST not add Claude, Grok, Gemini, agy, local-model specialists, parallel workers,
distributed infrastructure, cloud queues, public marketplace distribution, or an
arbitrary workflow DSL. SQLite is the preferred persistence layer unless a tested
constraint proves it insufficient. Event history is append-only and tamper-evident
within its documented trust assumptions, not a substitute for operating-system
access control or an external audit log.

## Development Workflow

Spec Kit artifacts are the source of truth for feature intent: Constitution →
Specify → Clarify → Plan → Tasks → Analyze → Implement → Converge. Implementations
MUST update affected artifacts before materially exceeding their scope. Changes
MUST include proportionate unit, integration, replay/recovery, and security
validation; documentation MUST distinguish demonstrated capabilities from inferred
ones. Commits are meaningful milestones, review checks for credentials and
machine-specific data, and a clean validation run is required before publication.

## Governance

This Constitution supersedes informal development practices. Any contributor or
agent changing the controller, protocol, local MCP surface, Skills, or feature
scope MUST verify compliance during review and record exceptions with rationale in
the relevant specification or architecture decision. Amendments require a written
change to this file, an accompanying Sync Impact Report, and semantic versioning:
MAJOR for incompatible governance changes, MINOR for added or materially expanded
principles, PATCH for clarifications. Compliance is reviewed at each feature plan,
before release, and during dogfood validation. `AGENTS.md` provides practical
operating guidance but cannot override this Constitution.

**Version**: 1.0.0 | **Ratified**: 2026-08-14 | **Last Amended**: 2026-08-14
