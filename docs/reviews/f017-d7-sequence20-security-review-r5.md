# F017 D7 Sequence 20 security review R5

## Reviewed implementation boundary

- Commit: `4712ee4007c610b5623a438626d8bf07ed1455ad`
- Tree: `e766ce6c8c23e766f34213f5d69ca1864ec2809a`
- Review-request commit: `bd55802d227280e654a1a16bc601db88485be00c`
- Review-request tree: `67cb790b53f16e934fa882ec7cb8a06552db0df1`

## Terminal disposition

`REJECT`

Claude Opus 5 returned `REJECT`. Authenticated AGY returned `ACCEPT`. The
stronger falsifying disposition governs. The reviewed 42 branch must not be
published.

Claude verified the declared patch, tree, and prior ledger; reproduced 82
focused tests and 378 complete tests; and found:

1. A blocker: `object.__setattr__(record, "__class__", OtherSealedType)`
   preserved the registry entry because the seal bound values and digest but
   not the original class. A legitimate event could be retyped as a prepared
   record and drive the lifecycle to `MOCK_DELIVERED` without preparation.
2. A required repair: `_text` accepted `str` subclasses, allowing equality-
   confusing frontier values.
3. A required repair: equality-raising `str` subclasses escaped as non-
   `ReadinessError` exceptions.
4. An advisory: `_Sealed` blocked assignment but not deletion.
5. An advisory: documentation must say backing-map mutation is detected on
   boundary-function entry, not on unchecked property reads.

AGY assessed all ten domains and returned no findings. That result remains
banked but cannot override Claude's executable public-API exploit.

## Local raw evidence integrity

Raw provider envelopes remain local because they contain provider session
metadata and machine-local paths.

- Claude envelope SHA-256:
  `98d9c8c96eefc1f391617fa270c1a46ec5184c3cf2650ef2a66f7174e7ef114b`
- AGY envelope SHA-256:
  `1b1b2d4e3806777b4a55bb1d569a0832d4aef88b8a4879fd470a10793b7f311e`

## Review-budget disposition

Sequence 20 consumed all five operator-authorized repair loops. No further
repair is authorized in this graph. The candidate remains local and unpushed.

The smallest next repair is evidence-bounded:

- bind `type(record)` in each registry entry and verify it before use;
- require `type(value) is str` in `_text`;
- reject `__delattr__` on sealed instances;
- narrow the backing-map detection sentence;
- add all-ordered-pair `__class__` reassignment regressions and equality-lying
  and equality-raising `str` subclass regressions.

Final state: `REVIEW_BUDGET_EXHAUSTED`.
