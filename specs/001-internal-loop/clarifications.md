# Clarification Record: Internal ChatGPT/Codex Loop

**Feature**: [spec.md](spec.md)
**Date**: 2026-08-14
**Workflow**: `speckit-clarify`

## Result

No critical ambiguities required a new user question. The authoritative build
plan and mission prompt explicitly resolve the decisions that materially affect
scope, security, and validation.

| Area | Status | Resolution source |
| --- | --- | --- |
| Core workflow | Clear | ChatGPT plans; Ultracode owns state/transport; Codex executes. |
| Scope boundary | Clear | v0.1 excludes external adversarial agents and generic orchestration. |
| Persistence | Clear | Local SQLite is preferred unless evidence disproves it. |
| Automation fallback | Clear | Use the strongest supported subscription-native path; never claim Level A without proof. |
| Authority | Clear | Models cannot alter lifecycle, limits, policy, approval, or immutable history. |
| Security posture | Clear | Single-user local trust boundary; local privilege and payload risks must be documented and tested. |
| Quality gates | Clear | Replay, recovery, capability separation, and dogfood are mandatory. |

## Deferred to Research, Not User Clarification

- Exact Codex app-server and desktop cross-surface capabilities are environment
  facts, so they are resolved by the Phase 0 spikes in [research.md](research.md).
- The compatible Codex model is an operational compatibility choice, documented in
  the architecture decision rather than baked into the user-facing specification.

## Coverage Summary

All high-impact categories were clear from the supplied brief. Implementation
details, performance measurements, and configuration syntax are intentionally
deferred to planning and validation.
