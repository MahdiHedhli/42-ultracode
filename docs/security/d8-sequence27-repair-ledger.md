# D8 Sequence 27 repair ledger

Sequence 27 starts from unaccepted candidate `0bba6f3`.

## Repair loop 1

Opened findings:

- Darwin unreaped-zombie `EPERM` prevented process-group absence proof.
- Successful cleanup relied on idealized process fakes.
- Direct-child fallback lacked executable coverage.
- Schema digest literals were overstated as external-byte verification.
- Target-absent and stderr-bound mutations were missing.
- Normal exact post-write `active` status notifications were treated as ambiguity.

Bounded repair:

- Poll/reap the direct child during group-absence waits and discard only probe ambiguity resolved by exact absence.
- Reserve cleanup tail time for reap, stderr-helper join, stream close, and census.
- Add harmless real-child and live-descendant Darwin regressions while retaining fakes for error taxonomy.
- Add direct-child fallback, target-absent, stderr-bound, and notification-ordering falsifiers.
- Relabel schema literals as change-detector pins and synchronize qualification claims.

Status: implemented and independently exercised on ColPanicM2; detached adversarial review remains pending.

Qualification evidence:

- focused formatter, lint, typing, lifecycle, and security validation passed;
- 73 focused supervised-delivery tests passed;
- 523 complete repository tests passed in 46.03 seconds;
- a direct Darwin child was reaped with no cleanup issue in 0.0152 seconds;
- a `SIGTERM`-resistant descendant required group `SIGKILL` and reached exact group absence with no cleanup issue in 1.0149 seconds;
- the scripted dogfood run completed two bounded iterations, reconstructed after restart, and required zero manual prompt copies; and
- no real Codex app-server, task, thread, turn, delivery transport, checkpoint, inference, PulsarMLX, R001, Event 05/06, P1, or Mac Studio operation occurred.

The first focused invocation stopped before tests because Ruff required one deterministic line wrap. The next test invocation exposed a test-only readiness race: cleanup could begin before the synthetic descendant installed its `SIGTERM` handler. An explicit pipe handshake removed that race without changing production behavior. These were corrections within repair loop 1, not consecutive no-progress cycles.

## Repair loop 2

Claude rejected candidate `842ebd5` because probe and signal ambiguity recorded before the wait loop survived a later exact absence proof, and because the older Sequence 26 adjudication still made an unconditional status-change claim.

Bounded repair:

- keep process-group probe and signal failures provisional until the bounded cleanup either proves exact group absence or exhausts containment;
- add a Darwin regression that enters cleanup with an already-exited, unreaped direct child and requires exact absence without a cleanup issue; and
- scope the Sequence 26 status-change claim and explicitly record the Sequence 27 supersession.

Status: implemented and qualified; detached adversarial re-review pending.

Loop 2 qualification evidence:

- 74 focused supervised-delivery lifecycle and security tests passed;
- 524 complete repository tests passed in 46.74 seconds;
- an independent supervisor observed Darwin `EPERM` for the zombie-only group, then cleanup reaped the child, proved exact group absence, and returned no issue in 0.00011 seconds;
- the live direct-child and `SIGTERM`-resistant descendant controls also returned exact absence with no issue;
- the refreshed dogfood completed two bounded iterations with restart reconstruction and zero manual prompt copies; and
- no real Codex operation or out-of-scope repository, checkpoint, inference, Event, P1, or Mac Studio access occurred.

## Repair loop 3

Claude verified both loop 2 findings closed, then rejected candidate `47b3cd7` because two post-spawn exceptions in `_CodexProcess.__enter__` could escape before bounded cleanup: deadline evaluation and stderr-thread startup.

Bounded repair:

- place every operation after successful `Popen` construction inside one cleanup-on-`BaseException` boundary;
- add falsifiers for post-spawn deadline expiry and stderr-thread startup failure, requiring child termination and reap;
- add a persistent probe-and-signal failure regression proving provisional ambiguity is retained when exact absence is not established; and
- mark the initial D8 transport profile as historical and explicitly point to the Sequence 26/27 successor contract.

Status: implemented and qualified; detached adversarial re-review pending.

Loop 3 qualification evidence:

- 77 focused supervised-delivery lifecycle and security tests passed;
- 527 complete repository tests passed in 49.07 seconds;
- post-spawn deadline expiry terminated and reaped the captured child with no cleanup issue;
- simulated stderr-thread startup failure terminated and reaped the captured child while categorically recording the unstarted-helper cleanup limitation;
- persistent probe and signal failures remained categorical when exact absence was not established;
- the refreshed dogfood completed two bounded iterations with restart reconstruction and zero manual prompt copies; and
- no real Codex operation or out-of-scope access occurred.
