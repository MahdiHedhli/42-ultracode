# F017 D7 Sequence 21 security review request R9

## Exact repair boundary

- Remote base: `d06b03d9b61f0176d8c4f8ec2883eaf0331d83c2`
- R8 reviewed commit/tree: `f01d584c1e69ca247256e5778c43c41a7f203376` / `55aa9b3f7c2a2ded887e985c04f1063a16a86204`
- R9 repair commit/tree: `96f5af17164d9c0c94d604f1b8b598ffe85716c8` / `fa78a73aff3c355e47ed8051d4221cae6030ded1`
- Base-to-repair patch SHA-256: `970bddc094adb2d122b5e7d3c79624767f7fcc58f6ef63757fb1fc58216eb480`
- Normalized R8 rejection ledger SHA-256: `76660ae5d0ca0f40d83b90125bed2bc82c3b2a05d272ac0dec0b884b38c7fadb`
- D7 policy: `f017-m2-d7-supervised-chat-handoff-readiness-v1` / `b982416cdb59df9eb814ecabce60d1d65d5ea708fd3535591957554274911cc2`

## Governing R8 finding

Both reviewers verified all earlier findings closed. Claude returned the
governing `REJECT` after exact-field nested JSON in a measured depth window was
decoded successfully but raised bare `RecursionError` during canonical
re-encoding. The previous wrong-field regression short-circuited before that
code and therefore did not prove the intended invariant.

## R9 repair claim

- Canonical re-encoding `RecursionError` and `ValueError` are normalized to
  `ReadinessError` before canonical byte comparison.
- The regression uses each parser's exact closed field set.
- Both parsers sweep depths 100,000 through 120,000 in 5,000-level steps,
  spanning parse-side rejection, successful parse/re-encode failure, and
  canonical-contract rejection without permitting any non-`ReadinessError`.
- All prior R5/R6/R7 regressions remain intact and passing.
- No capability, policy, controller, transport, dependency, browser, chat,
  checkpoint, inference, posting, or publication code changed.

## Local qualification

- Ruff format/lint and Mypy: pass.
- Focused D7/security tests: 134 pass.
- Complete pytest suite: 430 pass.
- Controller dogfood: `COMPLETE`, 430 tests, zero manual prompt copies.
- Privacy scan and Git diff check: pass.

## Required adversarial review

Verify the exact commit/tree, repeat all prior falsification domains, and focus
on exact-field nesting around the measured parser/re-encoder transition.
Determine whether any caller-controlled valid `bytes` rejection can still
escape the public `ReadinessError` contract. Confirm zero live capability and
unchanged readiness-only authority.

Return exactly one terminal verdict: `ACCEPT`, `REJECT`, or `BLOCKED`.
`ACCEPT` requires zero remaining actionable findings. `REJECT` requires a
reproducible actionable finding with file/line references.
