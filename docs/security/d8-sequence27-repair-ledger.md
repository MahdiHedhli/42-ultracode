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
