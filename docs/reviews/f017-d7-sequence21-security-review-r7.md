# F017 D7 Sequence 21 security review R7

## Reviewed boundary

- Commit: `ad20f389c47e95d33b4e5e50a7ff662f8b531da9`
- Tree: `f17cdc150c6683d9c0126c35742ba7f111c6027b`
- Repair implementation: `efa141483b96565ec56fa64f1af9cb4a608f8834`

## Terminal disposition

`REJECT`

Claude returned `REJECT`; authenticated AGY returned `ACCEPT`. The stronger
falsifying disposition governs. Both reviewers verified that every R5 finding
and all three R6 exact-byte manifestations were closed. Claude additionally
reproduced two decoder-resource exceptions from exact built-in `bytes` that
escaped the public `ReadinessError` contract:

1. A 5,000-digit JSON integer raised bare `ValueError`.
2. A deeply nested JSON value raised bare `RecursionError`.

The bounded repair preserves deliberate `ReadinessError` from the non-finite
constant hook and normalizes decoder `AttributeError`, `RecursionError`, and
`ValueError` failures into `ReadinessError`. Both public JSON parsers require
regressions for oversized integers and deep nesting.

No forged authority, digest bypass, illegal lifecycle transition, or delivery
capability was produced. The prior dynamic-subclass observation remains a
non-actionable trusted-process advisory because exact registered-type checks
reject those objects.

## Local raw evidence integrity

Raw provider envelopes remain local because they contain provider session
metadata and machine-local paths.

- Claude envelope SHA-256:
  `7874bc97f3de85812318026c0b430fa1c70eef94a7f7ae069b17872dd36750a9`
- AGY envelope SHA-256:
  `13baf297bf2d463270c3642291ec613713c77fcaecddac1a6564840137f69d1c`

Final state: `REJECTED_FOR_BOUNDED_R8_REPAIR`.
