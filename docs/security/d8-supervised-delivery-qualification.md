# D8 Supervised Delivery Qualification

Status: candidate for independent adversarial review

## Identity

- Implementation commit: `041863a5be7a801ca5bf3a6adfa11515d28dc776`
- Implementation tree: `2f91c7e206da0ca129e3c634f9b790bd5f84df72`
- Contract freeze commit: `4ff451bb367c92eb6617c2dacc47614bbcc89379`
- Policy ID: `f017-m2-d8-supervised-chat-delivery-transport-v1`
- Policy SHA-256: `db58a1e73a934719f4df7b9e07a4217a289cb8f4b3b748ce16a0e537df8036b6`
- Codex client: `codex-cli 0.146.0`
- Qualification date: 2026-08-28 UTC
- Repair loops consumed before review: 4 of 10

Accepted stages remain exactly D0 through D7. This evidence does not accept D8,
exercise a real Codex task, or authorize automatic delivery.

## Qualification results

| Gate | Result |
| --- | --- |
| Ruff format | PASS |
| Ruff lint | PASS |
| mypy strict | PASS |
| Full pytest corpus | PASS, 467 collected tests |
| Existing bounded dogfood | PASS |
| `git diff --check` | PASS |
| Source worktree | clean |

Local detailed evidence is retained outside Git. Its redacted integrity bindings
are:

- Qualification result table SHA-256: `b1df35433eb817ecad949608183483891ceeb3bf28d9d91e9f9648492261e473`
- Bounded dogfood evidence SHA-256: `457313ff3512dfbc99b97b1f61e590ec42d2dcb1a122209ae26077be8bc21a8b`
- Fake-peer summary SHA-256: `0ed3b264531adf4df0ad4679363b0e262b38ff4c5a58c796f055b243cd916fdd`

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
- Browser operations: 0
- MCP operations: 0
- Automatic loops: 0

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

