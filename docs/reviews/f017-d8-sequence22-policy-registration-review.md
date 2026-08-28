# F017 D8 Sequence 22 Policy-Registration Review

## Reviewed identity

- Commit: `274f65c1fbbf578f6f1ad674dc5d72e7b114ac06`
- Tree: `3bcd36bf2b6345f6947e80a4dcabc9128048af72`
- Accepted base: `71953adaf931c535b0d05d0d8d0da364d16207af`
- Accepted base tree: `a12b84945625ab0cf1ac403c45d00ab0f8f43ed4`
- D8 policy: `f017-m2-d8-supervised-chat-delivery-transport-v1`
- D8 digest: `db58a1e73a934719f4df7b9e07a4217a289cb8f4b3b748ce16a0e537df8036b6`

Both reviewers inspected fresh detached checkouts of the same commit and tree.
They were instructed to make no repository change, access no network or
delivery surface, and inspect no PulsarMLX, R001, checkpoint, or Mac Studio
runtime/source state.

## Review loop 0

Claude returned `BLOCKED`, not `REJECT`: no code defect was found, but the
review harness denied local digest/test commands and supplied only the freeze
table digest rather than its bytes. AGY produced no verdict because its
headless command permission was denied. These were review-harness failures.

- Claude envelope SHA-256: `8752d8d64e60e2298cf7a5d065c90bbada407e1f2b9c3b07c963efaa0077455a`
- AGY envelope SHA-256: `e2b799d0ad54318003cc05e74d77ff306ab8afd0675cf66c311d85d65df0ed08`

No source repair was made because neither result identified an implementation
defect. The next review supplied the already-verified transition-table bytes
and permitted bounded local inspection and test execution.

## Review loop 1

### Claude

- CLI: Claude Code `2.1.250`
- Requested model: `opus`, maximum effort
- Provider-reported model: `claude-opus-5`
- Session: `d7de78b6-4a73-4d6f-b098-b8cb8d32d4f3`
- Duration: `719598 ms`
- Envelope SHA-256: `7b38560203eb8594a41dc412aea917872ab8945c63ccc11c239200188770bead`
- Terminal verdict: `ACCEPT`

Claude independently reproduced the review commit/tree, implementation diff
digest, freeze-table digest, and D8 policy digest. It reran `157` focused and
`450` complete tests, Ruff format/lint, and strict mypy. It exercised fifteen
additional encoding attacks, the 8-by-8 policy matrix, immutability, unsafe
minting, cross-policy isolation, and the zero-capability census. It reported
zero blocking, required, actionable-advisory, unresolved, or rejected claims.
A denied attempt to write an optional scratch probe did not prevent the
reviewer from completing the required checks or reaching terminal acceptance.

### AGY

- CLI: `agy` `1.1.22`
- Requested model: authenticated default, high effort
- Provider-reported model: not exposed by the CLI envelope
- Conversation: `c7df742d-1cc4-47f8-b838-2bf0a9a3e8f3`
- Duration: `202.134756 s`
- Envelope SHA-256: `bf0b8b16d0499de72b2d8e681b22fa6b5ea96fe560c860c2db95b04a31c44bff`
- Terminal verdict: `ACCEPT`

AGY independently reproduced all source identities and digests, reran `157`
focused and `450` complete tests plus Ruff and mypy, and falsified registry
closure, encoding/mutation hardening, 64 ordered policy checks with 56
cross-policy rejections, universal capability prohibition, and the absence of
live delivery code. Its claims census was zero blocking, required,
actionable-advisory, unresolved, and rejected claims.

## Disposition

`ACCEPT`

The exact D8 policy registration is accepted for strict-fast-forward
publication. This does not accept D8 as an executable stage and grants no live
delivery, alias resolution, browser/chat access, posting, receipt, or automatic
loop authority.
