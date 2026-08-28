# F017 D7 Sequence 20 security review R4

## Reviewed implementation boundary

- Commit: `b4edff6d9fb1107a65e4f75020667ede16304888`
- Tree: `17e4b32d45c65377c09f2744b3e82d4e812c0bfd`
- Review-request commit: `2eb7867766cff22c5c407cc8b94626d45758ae7b`
- Review-request tree: `b9dec4250e0bd4d4cf271ef6fdc1c2dfd52ad8e7`

## Terminal disposition

`REJECT`

Claude Opus 5 returned `REJECT`. Authenticated AGY returned `ACCEPT`. The
stronger falsifying disposition governs, so the reviewed boundary was not
published.

Claude verified the declared patch and tree, executed 372 tests, and found:

1. A blocker: `gc.get_referents` could reach and mutate the dictionary behind
   the registered mapping proxy without changing proxy identity. Sealed
   requests, prepared records, events, and snapshots were therefore mutable.
2. A required repair: frontier identity compared values before enforcing exact
   string/integer types, allowing equality confusion and non-`ReadinessError`
   exceptions.
3. An advisory: a metaclass subclass calling `type.__new__` could bypass the
   metaclass's structural subclass rule.
4. An advisory: hostile same-process private introspection can recover Python
   module internals, so the closed-boundary claim requires explicit public-API
   scope.

AGY assessed all ten domains and returned no findings. That result remains
banked but does not override Claude's demonstrated mutations.

## Local raw evidence integrity

Raw envelopes remain local because they include provider session metadata and
machine-local paths.

- Claude envelope SHA-256:
  `75cf7aee0d25b2a17df3a3394c2eb877b5fb74112376f149d876852446637cc9`
- AGY envelope SHA-256:
  `833e6f370346fa8a754db6c0e986d8587c1ba3a9cc9da3354f3ee56dd5a35a31`

## Repair-loop 4 disposition

The bounded repair:

- binds a canonical content digest beside mapping-proxy identity and verifies
  both before every use;
- rejects backing-dictionary mutation, copied proxies, and replaced proxies;
- validates frontier strings and exact integers before equality comparison;
- adds an `_Sealed.__init_subclass__` guard that remains active when a derived
  metaclass bypasses the original metaclass constructor;
- scopes the seal to public-API integrity and explicitly excludes hostile
  same-process private introspection from the isolation claim.

Validation passed:

- Ruff format and lint;
- mypy over `ultracode`;
- 82 focused D7/security tests;
- 378 complete repository tests;
- two dogfood iterations, eleven transitions, restart reconstruction, zero
  manual prompt copies, and final state `COMPLETE`.

- Dogfood evidence SHA-256:
  `71e96553ffd55c5bcbe1024f51016c7caafc472325efd50a4b0edaccc996f495`

This document is not acceptance. A fresh same-commit dual-provider review is
required.
