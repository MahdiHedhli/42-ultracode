# F017 D7 Sequence 21 security review request R7

## Exact repair boundary

- Remote source base: `d06b03d9b61f0176d8c4f8ec2883eaf0331d83c2`
- R6 reviewed commit: `cb9082f599ea38e99ed8e7e3d04e51ba9eed368b`
- R6 reviewed tree: `08c406c4a02e738acff0e3130d801ec1b8cfc938`
- R7 repair commit: `efa141483b96565ec56fa64f1af9cb4a608f8834`
- R7 repair tree: `ef01cf4241d4fdf68d39f2ebb60b5d969ba35045`
- Base-to-repair patch SHA-256: `1b0b9b16bb91a1f46a121a1966a573aff0becedffe0cdd63bdca21103b84894e`
- Normalized R6 rejection ledger SHA-256: `2ce15a6487030127a52371c795acac512af4c749bbbf41296bef83fdf5bea702`
- D7 policy ID: `f017-m2-d7-supervised-chat-handoff-readiness-v1`
- D7 policy SHA-256: `b982416cdb59df9eb814ecabce60d1d65d5ea708fd3535591957554274911cc2`

## Governing R6 findings

Claude and AGY agreed that all five frozen R5 findings were closed. Claude
returned the governing `REJECT` after reproducing three manifestations of the
same remaining direct-input defect:

1. An equality-lying `bytes` subclass bypassed canonical-form comparison.
2. An equality-raising `bytes` subclass escaped as `RuntimeError`.
3. A non-bytes object escaped JSON error normalization as `TypeError`.

## R7 repair claim

- `_load` rejects every value whose exact type is not built-in `bytes` before
  decoding, comparison, or caller-defined methods can execute.
- Verified response bytes use the same exact built-in `bytes` rule.
- Regressions cover equality-lying and equality-raising subclasses,
  `bytearray`, and unrelated objects at both public JSON parsers.
- The original 30 ordered cross-type tests, six-type deletion matrix, hostile
  `str` tests, and all prior D7 tests remain intact.
- No runtime capability, policy, controller, dependency, transport, browser,
  chat, checkpoint, inference, or publication code changed.

## Local qualification

- Ruff format and lint: pass.
- Mypy: pass, 11 source files.
- Focused D7 and security tests: pass, 130 tests.
- Complete pytest suite: pass, 426 tests.
- Controller dogfood: `COMPLETE`, 426 tests, zero manual prompt copies.
- Privacy scan and Git diff check: pass.

## Adversarial review requirements

Verify the exact commit/tree and attempt to falsify:

1. Canonical JSON using hostile `bytes` subclasses and mutable byte buffers.
2. Exception normalization for every non-exact `raw` input class.
3. Verified-response digest handling with hostile byte-like inputs.
4. All 30 cross-type class-reassignment pairs.
5. Direct and `object.__delattr__` deletion across all six sealed types.
6. Exact `str` validation and equality-method non-execution.
7. Registry type, identity, digest, weak-lifetime, and forged-instance checks.
8. Public-boundary fail-closed behavior and deterministic replay.
9. Zero live capability and unchanged D7 readiness-only authority.
10. Whether any R6 advisory becomes actionable under the repaired boundary.

Return exactly one terminal verdict: `ACCEPT`, `REJECT`, or `BLOCKED`.
`ACCEPT` requires all R5 and R6 findings closed with zero new actionable
finding. `REJECT` requires a reproducible actionable finding with file/line
references.
