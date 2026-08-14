---
name: ultracode-planner
description: Plan and review bounded 42 Ultracode v0.1 runs through planner-role MCP tools. Use when converting an approved engineering objective or an executor result into a typed next instruction, human escalation, or evidence-backed completion.
---

# Ultracode Planner

Use the planner-role tools to move a run forward without copying prompt bodies
between ChatGPT and Codex. Read
[the protocol reference](references/protocol.md) before creating a run or
submitting an instruction.

1. Read the run and its latest structured result before deciding a next action.
2. Create a run only for a human-approved objective. Treat iteration limits as
   controller-owned: omit `max_iterations` unless a human has explicitly chosen
   one, and never try to raise it later.
3. Submit a bounded instruction with `goal`, `context`, `constraints`, and
   `done_when`; include only relevant files, tests, prohibitions, evidence
   requirements, and discipline Skills. Use a new idempotency key for a new
   delivery and reuse one only for a retry of the same delivery.
4. On a result, separate observed evidence from worker assertion. Compare the
   evidence to each completion condition and identify blockers and uncertainty.
5. Either issue the smallest useful next instruction, request human review, or
   complete only when the persisted result provides sufficient evidence.

Do not call worker or control tools, execute reported commands, change governing
policy, lower/raise limits, silently redefine the objective, or claim automatic
ChatGPT continuation. Level C requires an explicit planner tool call.
