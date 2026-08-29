# F017 D8 Sequence 28 Codex 0.149 exact-tree review request

Review the checked-out commit and tree exactly. Return `ACCEPT` or `REJECT`;
an acceptance must contain zero blocking, required, unresolved, or rejected
claims.

## Authorized scope

This candidate changes only the 42 Ultracode D8 supervised-delivery transport,
tests, and security documentation. It does not authorize app-server launch,
alias resolution, a target operation, Event 05/06, D4/D5 work, checkpoint
access, inference, privacy-configuration changes, or unrelated-project access.

## Production identity

- Executable: fixed Codex Desktop resource path.
- CLI: `codex-cli 0.149.0-alpha.4.3`.
- App version/build: `26.818.61809` / `7019`.
- Team ID: `2DC432GLL2`.
- Executable SHA-256:
  `dd304ffe232fa9e782ed3e5358776d270e394c2fb85cab846f989823f0843313`.

Before the human challenge, the adapter seals and revalidates the executable
and checks CLI, signer, team, and app identity. The canonical root-owned,
non-world-writable `/Applications` directory is the only group-writable
parent exception.

## Raw schema pins

The exact 0.149 generator produced fourteen raw files. An exact-map check passes
14/14. Parsed comparison with the accepted 0.146 baseline classified four
schemas semantically unchanged and ten changed:

- unchanged: thread-status notification, initialize response, thread-read
  params, and turn-start params;
- changed: thread-list params/response, thread-read response, thread-resume
  params/response, turn-start response, turn-started/completed notifications,
  initialize params, and error notification.

The current consumed thread projection requires nullable `projectId`, accepts
only the bounded optional `section` and `sectionEnteredAt` shapes, and
rejects the removed `isPinned`.

## Reviewer questions

1. Is the Desktop executable/signer binding fail-closed and ordered before the
   human challenge?
2. Do the raw pins and semantic classification support the consumed 0.149
   projection without a version range or hash waiver?
3. Can missing, renamed, widened, or ambiguous consumed fields pass?
4. Did the candidate preserve the frozen policy, exact-message handling,
   one-attempt/no-retry accounting, confirmation capability, lifecycle, privacy,
   and Darwin cleanup boundaries?
5. Did any change create a live transport operation or unrelated-project
   dependency?

## Required validation

The reviewer must reconstruct the candidate identity from Git, inspect the full
diff and relevant tests, and cite the exact commit and tree in the verdict.
Local qualification must include the official Ruff, mypy, and full pytest
gates, 20 deterministic fake-peer reconstructions, Darwin cleanup tests,
privacy scan, and owned-process census.
