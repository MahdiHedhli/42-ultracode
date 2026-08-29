# D8 Sequence 26 adjudication

The Sequence 24 synthetic `archived`, `active`, and `busy` fields are rejected. Sequence 26 derives archive membership from the stable `thread/list` request parameter and derives concurrency from the stable `Thread.status` object. Source and cwd are read from the actual stable `Thread` schema and matched against machine-local sealed route authority.

The six pinned stable schema hashes are part of the protocol profile. Unknown fields, malformed pagination, duplicate identities, cursor cycles, unsafe statuses, source/cwd drift, and status changes fail closed. The successful no-live semantic result remains `PASS_PENDING_PLANNER_REVIEW`; it does not accept D8 or authorize live delivery.
