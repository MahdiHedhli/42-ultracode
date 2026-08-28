# D8 Supervised Delivery Qualification

Status: candidate for independent adversarial review

## Identity

- Implementation commit: `113ce3b532956db39ca548a3025547194fc3b2fc`
- Implementation tree: `d5f0b67691645cb6753f48f318a76b6b8833ce60`
- Contract freeze commit: `4ff451bb367c92eb6617c2dacc47614bbcc89379`
- Policy ID: `f017-m2-d8-supervised-chat-delivery-transport-v1`
- Policy SHA-256: `db58a1e73a934719f4df7b9e07a4217a289cb8f4b3b748ce16a0e537df8036b6`
- Codex client: `codex-cli 0.146.0`
- Qualification date: 2026-08-28 UTC
- Repair loops consumed: 6 of 10

Accepted stages remain exactly D0 through D7. This evidence does not accept D8,
exercise a real Codex task, or authorize automatic delivery.

## Qualification results

| Gate | Result |
| --- | --- |
| Ruff format | PASS |
| Ruff lint | PASS |
| mypy strict | PASS |
| Full pytest corpus | PASS, 471 collected tests |
| Existing bounded dogfood | PASS |
| `git diff --check` | PASS |
| Source worktree | clean |

Local detailed evidence is retained outside Git. Its redacted integrity bindings
are:

- Qualification result table SHA-256: `020010eac214abfeff75ce656ec18d687438b9f52d1b84d4789dc78a43336d70`
- Bounded dogfood evidence SHA-256: `a264e8f3926373f72abe15f45403d55750d3ce554a001d6978c4a82d54757b7d`
- Fake-peer summary SHA-256: `2315a033fd8539ce677f9ba6bd7acfcf2d324e2d3f0b4558e1165c377527fbfc`

## Fake-peer result

Twenty clean, isolated reconstructions produced one result:

- Outcome: `DELIVERED`
- Requests: `initialize`, `thread/read`, `thread/resume`, `turn/start`
- Client notifications: `initialized`
- Transcript SHA-256: `03fb76b8e81200a9f2c749b948388014b152f0cbdfb894f790de06f5d4ebbb1b`
- Journal SHA-256: `a5322c44b87420569a49ea0ec53d6b636569d54b4af79aa7cdfc2e5a2e4623a3`
- Real app-server launches: 0
- Real alias resolutions: 0
- Real task reads: 0
- Real task resumes: 0
- Real turns: 0
- Posts: 0
- Static exclusions: browser operations, MCP operations, automatic loops

The exact process guard found no process whose command was
`codex app-server --listen stdio://`.

## Falsifying coverage

The committed tests cover:

- Wrong JSON-RPC response ID.
- Server request in place of a response.
- Unknown notification.
- Partial transport line.
- Crash reconstruction after durable `ATTEMPT_STARTED`.
- Partial journal terminal record.
- Invalid journal schema/hash chain.
- Symlinked route registry.
- Unsafe route-registry mode.
- Non-TTY confirmation.
- Exact TTY challenge re-entry and one-use consumption.
- Terminal-control escaping in the exact payload representation.
- One and only one `turn/start`, with no optional override fields.
- Static fixed-argv and `shell=False` enforcement.
- Static network/browser/automatic-loop exclusion.
- Monkeypatched live process constructor during fake qualification.
- Exact production-entry rejection before any process launch on non-TTY input.
- Terminal `DELIVERED`/`UNCERTAIN` replay refusal through `_perform`.
- Fresh-confirmed retry only after a verified pre-write failure.
- Durable journal visibility at the exact `turn/start` write boundary.
- Exclusive fake-fixture creation that cannot follow or overwrite a symlink.
- True hash-chain corruption after an otherwise valid journal write.

All expected rejections occurred; unexpected passes: 0.

## Frozen safety interpretation

The implementation is a foreground, operator-confirmed readiness boundary. The
symbolic route is resolved only after a short-lived sealed capability is consumed.
The journal fsyncs `ATTEMPT_STARTED` before the user-message write. Any ambiguity
after that point becomes terminal `UNCERTAIN`, and no automatic retry exists.
Server requests and approvals are never answered automatically.

No real task alias, Codex task, PulsarMLX checkout, checkpoint, Event 05/06, D4/D5
runtime work, browser, or live delivery transport was accessed during
qualification.

## Preliminary review disposition

Claude Opus independently reproduced 467 tests and the 20-run evidence on the
prior detached candidate, returned `ACCEPT`, and identified bounded hardening
findings. Those findings are closed in `113ce3b532956db39ca548a3025547194fc3b2fc`:

- Fake qualification now uses exclusive, no-follow, owner-only fixture creation.
- Parent path traversal, owner-only input modes, and empty-journal adoption fail closed.
- Real transport counters are incremented at the operations they represent.
- The frozen app-server version is checked from the initialize response.
- New journals sync the parent directory and Darwin records use `F_FULLFSYNC`.
- Regression tests bind full-sync ordering, terminal refusal, pre-write retry, and the production entry point.

AGY timed out on the prior candidate and produced no verdict. It is not counted as
an acceptance. Both reviewers must return terminal `ACCEPT` on the same new
detached review commit and tree before publication.
