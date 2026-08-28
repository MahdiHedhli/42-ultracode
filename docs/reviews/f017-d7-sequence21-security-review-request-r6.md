# F017 D7 Sequence 21 security review request R6

## Exact repair boundary

- Remote source base: `d06b03d9b61f0176d8c4f8ec2883eaf0331d83c2`
- Banked R5 rejection head: `4086734ae45b164f43a3ede6584c2e6df36680f8`
- Repair implementation commit: `1c19630392ae7781859aa7f512433175a1675ac1`
- Repair implementation tree: `bc2b7fecd6260d4eebc5c21fa0569d71eecf406d`
- Base-to-repair patch SHA-256: `dc8cd1ad9695392adbb5fdc83215ec92f73d669f5379c065edda989060d9c849`
- Prior normalized R5 ledger SHA-256: `80bc10770c707c082133b1ac293814cbd21863d4109e22981e47173ed6cfd50f`
- Pre-fix executable reproduction SHA-256: `127599d7d07758a9a7466d7ac8731da597bfc38a2c89d69eb3d11a587a42eb0f`
- D7 policy ID: `f017-m2-d7-supervised-chat-handoff-readiness-v1`
- D7 policy SHA-256: `b982416cdb59df9eb814ecabce60d1d65d5ea708fd3535591957554274911cc2`

The raw provider envelopes remain local because they contain provider session
metadata and machine-local paths. Their banked SHA-256 identities are:

- Claude R5: `98d9c8c96eefc1f391617fa270c1a46ec5184c3cf2650ef2a66f7174e7ef114b`
- AGY R5: `1b1b2d4e3806777b4a55bb1d569a0832d4aef88b8a4879fd470a10793b7f311e`

## Frozen findings to verify

1. `R5-CLAUDE-B1`: registry entries did not bind the original concrete sealed
   type, so cross-type `__class__` reassignment reached the wrong consumer.
2. `R5-CLAUDE-R1`: `_text` accepted equality-confusing `str` subclasses.
3. `R5-CLAUDE-R2`: equality-raising `str` subclasses escaped the contract as
   non-`ReadinessError` exceptions.
4. `R5-CLAUDE-A1`: ordinary and `object.__delattr__` deletion were unguarded.
5. `R5-CLAUDE-A2`: the backing-map detection documentation was too broad.

No other defect authorizes scope expansion.

## Repair claim

- Sealed values live only in a private weak registry; the registry stores the
  original concrete class, immutable mapping proxy, and canonical digest.
- Every value lookup requires current exact type, registered exact type,
  object identity, and digest agreement.
- No `_values` instance slot exists. Direct deletion is rejected by
  `__delattr__`; `object.__delattr__` cannot delete the read-only descriptor.
- All direct text inputs require exact built-in `str`, not subclasses.
- All 30 ordered pairs among the six exported sealed record classes are tested
  for rejection or fail-closed detection at the target public consumer.
- Equality-confusing and equality-raising `str` subclasses are regression
  tested.
- The data model now scopes backing-map detection to validated boundary-
  function entry before use.

## Local qualification

- Ruff format: pass, 70 files.
- Ruff lint: pass.
- Mypy: pass, 11 source files.
- Focused D7 and security tests: pass, 119 tests.
- Complete pytest suite: pass, 415 tests.
- Controller dogfood: `COMPLETE`, 415 tests, zero manual prompt copies.
- Privacy scan: pass.
- Git diff check: pass.

## Adversarial review requirements

Independently inspect and, where useful, execute falsifying cases for:

1. Every ordered cross-type `__class__` reassignment pair.
2. Forged, uninitialized, copied, replaced, and deleted sealed records.
3. Direct and `object.__delattr__` deletion across all six record classes.
4. Equality-confusing and equality-raising `str` subclasses at every direct
   text boundary.
5. Registry identity, exact-type, mapping identity, and digest checks.
6. Weak-reference lifetime and accidental retention behavior.
7. Public-boundary exception normalization and fail-closed behavior.
8. Serialization, copying, subclassing, and metaclass bypass attempts.
9. Zero browser, chat, transport, checkpoint, inference, or posting capability.
10. Exact documentation scope and unchanged D7 readiness-only authority.

Return exactly one terminal verdict: `ACCEPT`, `REJECT`, or `BLOCKED`. A
`REJECT` must include reproducible actionable findings. An `ACCEPT` must state
that all five frozen R5 findings are closed and no new actionable finding was
found in the exact reviewed commit/tree.
