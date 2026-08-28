# F017 D7 Sequence 21 security review request R8

## Exact repair boundary

- Remote source base: `d06b03d9b61f0176d8c4f8ec2883eaf0331d83c2`
- R7 reviewed commit/tree: `ad20f389c47e95d33b4e5e50a7ff662f8b531da9` / `f17cdc150c6683d9c0126c35742ba7f111c6027b`
- R8 repair commit/tree: `90cf725fe620d804231242dc1ecf4e121a8630cd` / `bb880bf4caa642ccae9d0da691801bb8c4c4bf1f`
- Base-to-repair patch SHA-256: `8c33dcdd85744056e30e41b38e4cb467cc56a6393d9fa755bc8c4ce0b57d56a6`
- Normalized R7 rejection ledger SHA-256: `eec50dd2743b0ec5d310a68cc0ca01e2e5ea0c668ff3447f147b3132f4327cf2`
- D7 policy: `f017-m2-d7-supervised-chat-handoff-readiness-v1` / `b982416cdb59df9eb814ecabce60d1d65d5ea708fd3535591957554274911cc2`

## Governing R7 finding

Both reviewers verified every R5 finding and every R6 exact-byte manifestation
closed. Claude returned the governing `REJECT` after exact built-in bytes with
a 5,000-digit integer and extreme nesting escaped the public parser contract as
bare `ValueError` and `RecursionError`.

## R8 repair claim

- Deliberate `ReadinessError` from the non-finite constant hook is preserved.
- Decoder `AttributeError`, `RecursionError`, and `ValueError` are normalized
  to the boundary's non-payload-bearing `ReadinessError`.
- Both public JSON parsers test oversized integer and deep-nesting bytes.
- All R5/R6 regressions remain unchanged and passing.
- No policy, controller, capability, transport, dependency, checkpoint,
  inference, browser, chat, posting, or publication code changed.

## Local qualification

- Ruff format/lint and Mypy: pass.
- Focused D7/security tests: 134 pass.
- Complete pytest suite: 430 pass.
- Controller dogfood: `COMPLETE`, 430 tests, zero manual prompt copies.
- Privacy scan and Git diff check: pass.

## Required adversarial review

Verify the exact commit/tree and attempt to falsify all prior R5/R6 claims plus:

1. Exact-`bytes` JSON decoder failures and exception normalization.
2. Oversized integer, extreme nesting, malformed UTF-8, duplicate keys,
   non-finite constants, trailing bytes, and hostile byte-like subclasses.
3. Whether any expected parser rejection escapes as a non-`ReadinessError`.
4. All 30 cross-type swaps and six-type deletion matrix.
5. Registry identity/type/digest/lifetime and forged-instance handling.
6. Deterministic replay, terminal behavior, and zero live capability.
7. Whether either prior advisory becomes an actionable public-boundary defect.

Return exactly one terminal verdict: `ACCEPT`, `REJECT`, or `BLOCKED`.
`ACCEPT` requires zero remaining actionable findings. `REJECT` requires a
reproducible actionable finding with file/line references.
