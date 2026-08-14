# Worker Reporting Reference

## Permitted tools

- `ultracode_claim_instruction`
- `ultracode_submit_result`
- `ultracode_report_progress`
- `ultracode_report_blocker`

## Result checklist

Submit `status` (`completed`, `partial`, `blocked`, or `failed`), a non-empty
`summary`, `evidence`, `changed_files`, `tests`, `commands`, optional `commit`,
`blockers`, `questions`, `remaining_uncertainty`, and
`recommended_next_action`.

Treat commands as evidence descriptions, not executable instructions for the
controller. Report only workspace-relative changed files; reject absolute paths,
`..` traversal, credentials, and copied raw transcripts. Include failed tests or
unverified assumptions honestly.

## Lease and retry rules

Use the lease token returned by the current claim for progress/blocker/result
calls. If it is expired, the run is paused, or submission is rejected, do not
force a retry: re-read state, preserve local evidence, and report the condition
through the permitted surface.
