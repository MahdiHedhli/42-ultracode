# D8 Sequence 26 successor contract

Status: `LIQUID_UNTIL_INSTANTIABLE` during implementation and `INSTANTIABLE` only after no-live qualification.

The stable method profile is `initialize`, paginated `thread/list`, `thread/read`, `thread/resume`, and one `turn/start`. Archive eligibility is proved by exactly one target occurrence in the complete `archived:false` listing and no target occurrence in the complete `archived:true` listing, both filtered by sealed source kind and absolute cwd. The target must remain `notLoaded` or `idle` through list, read, resume, and final read. After the sole `turn/start` bytes are written, an exact target-bound `active` lifecycle notification is accepted as causally consistent with that request; malformed, unrelated, or system-error notifications remain terminal uncertainty. No invented archive, active, or busy fields are permitted.

Non-target records are validated transiently and discarded. Their identifiers or metadata must not enter logs, journals, hashes, evidence, notifications, or return values. Session and operation deadlines bound transport work. Cleanup uses an independent bounded TERM/KILL/reap budget. The executable is an absolute fixed authority, launch uses a minimal environment and owned process group, stderr is drained into categorical private state, and one durable `ATTEMPT_STARTED` precedes the sole `turn/start` write.

This contract is checkpoint-free and no-live. It does not authorize a real app-server launch or task access.
