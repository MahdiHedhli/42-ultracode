# F017 D7 Sequence 21 security review R6

## Reviewed boundary

- Commit: `cb9082f599ea38e99ed8e7e3d04e51ba9eed368b`
- Tree: `08c406c4a02e738acff0e3130d801ec1b8cfc938`
- Repair implementation: `1c19630392ae7781859aa7f512433175a1675ac1`

## Terminal disposition

`REJECT`

Claude returned `REJECT`; authenticated AGY returned `ACCEPT`. The stronger
falsifying disposition governs. Both reviewers verified that all five frozen
R5 findings were closed. Claude additionally reproduced three manifestations
of one unprotected input-type defect at `_load`:

1. An equality-lying `bytes` subclass bypassed the canonical-byte comparison.
2. An equality-raising `bytes` subclass escaped as `RuntimeError`.
3. A non-bytes object with an invalid `decode()` result escaped as `TypeError`.

The bounded repair is to require exact built-in `bytes` before `_load` performs
any operation. The same exact-type rule is applied to verified response bytes
for a consistent direct-input boundary. Regression tests must cover lying and
raising subclasses, `bytearray`, and unrelated objects.

Claude also noted that direct dynamic subclass construction can reach the
internal sealed root, but found no exploit: the private construction token and
exact registered-type checks remain load-bearing and reject every such object.
This is an advisory inside the documented trusted-process boundary, not an
actionable D7 capability defect.

## Local raw evidence integrity

Raw provider envelopes remain local because they contain provider session
metadata and machine-local paths.

- Claude envelope SHA-256:
  `1f6e44d1f2b67f9155cdb2644a99ba04fcc9ad7c4bd32594f97137d0b541df38`
- AGY envelope SHA-256:
  `5d7cc2795b5519dd12f4e5a58f6b6ec78b125a600ad8662c62bc129df83f1a92`

Final state: `REJECTED_FOR_BOUNDED_R7_REPAIR`.
