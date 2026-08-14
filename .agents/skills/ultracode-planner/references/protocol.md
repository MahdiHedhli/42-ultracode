# Planner Protocol Reference

## Permitted tools

- `ultracode_create_run`
- `ultracode_read_run`
- `ultracode_submit_instruction`
- `ultracode_read_result`
- `ultracode_complete_run`
- `ultracode_request_human`

## Instruction checklist

Provide non-empty, bounded `goal`, `context`, `constraints`, and `done_when`.
When useful, add `relevant_files`, `required_tests`, `prohibited_changes`,
`evidence_requirements`, and `selected_discipline_skills`. Keep paths relative
to the workspace and avoid raw transcripts, credentials, and unrelated context.

## Review decision

| Evidence state | Planner action |
| --- | --- |
| Completion criteria are evidenced | Complete with a concise rationale. |
| More bounded repository work is justified | Submit one next instruction. |
| Judgment, approval, or missing information is required | Request human review. |
| Worker reports failure/blocker | Read it, preserve it as evidence, then escalate or issue a safe follow-up. |

Completion is a controller state change, not a conversational flourish. Preserve
the run's immutable history and use the MCP result rather than a copied report.
