# F017 D7 Sequence 20 security review R3

## Reviewed boundary

- Commit: `31c86481707d9040a24e6c17138984427c94167f`
- Tree: `824c367a3defb22564328476150a2daabb57e354`
- Claude provider: Claude Opus 5, high effort, read-only tools
- AGY provider: authenticated AGY CLI, high effort, plan mode, sandboxed

## Terminal disposition

`REJECT`

Claude returned `REJECT`. AGY returned `BLOCKED` because its headless sandbox
could not read the detached snapshot; AGY did not produce a substantive
security disposition. The rejected boundary was not published.

Claude identified:

1. A blocker: every boundary-created object exposed the construction token in
   its `_proof` slot, allowing direct construction of arbitrary sealed types.
2. A required repair: replay trusted already-sealed event fields instead of
   independently revalidating the complete event contract.
3. An advisory: the subclass gate's closure counter remained writable through
   Python closure-cell introspection, so the permanent-gate claim was too broad.

## Local raw evidence integrity

Raw provider envelopes remain local and are not committed because they contain
provider session metadata and machine-local paths.

- Claude envelope SHA-256:
  `473d5b50dd245d17dfb7dfc987cd7e704af2bef1256f12980cdd130f894e7fb0`
- AGY terminal envelope SHA-256:
  `418451afe0aec7899734a118080d8bb3c773230a4330d13bfd37be41392a23c8`

## Repair-loop 3 disposition

The bounded repair:

- replaced the exposed proof token with a weak identity registry bound to the
  original immutable values mapping;
- rejects uninitialized objects, copied mappings, and replaced mappings;
- revalidates every mock-event field during replay through the same strict
  validator used at event creation;
- replaced the mutable closure counter with a structural direct-subclass rule;
- added malformed event and sealed-object forgery regressions.

Validation passed for formatting, lint, static typing, focused D7/security
tests, the complete test suite, and a two-iteration/eleven-transition dogfood
run with restart reconstruction and final state `COMPLETE`.

- Dogfood evidence SHA-256:
  `20bbf5e977474a0c8794b24f5ed79daef56f5a07fc97f7324d0422810cfbcb6b`

This document does not constitute acceptance. A fresh same-commit dual-provider
review is required.
