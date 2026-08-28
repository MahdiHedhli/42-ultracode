# F017 D7 Sequence 20 Independent Security Re-review Request R3

## Exact review identity

Re-run the complete ten-criterion adversarial review defined by the original
Sequence 20 request and R2 request. The criteria and acceptance rule remain
frozen and unchanged.

- remote base: `d06b03d9b61f0176d8c4f8ec2883eaf0331d83c2`
- original candidate: `74481254f131aa4d44a7424a22ddd84effa46d86`
- repair-loop 1 commit: `a417ec2b98b7d4b3a2cf83b42c9bfae08c33ef9b`
- repair-loop 2 commit: `183013a7a2cd7bf5754fd16579da6ad1b8da9e82`
- repair-loop 2 tree: `5e5e298179279174f66f2bcfaaa214728223433d`
- repaired chain patch SHA-256:
  `6c7f310666196c87f32cdcd6cedd054c105214d566fcc45f0c39ec66e24e32f9`

Inspect the full base-to-head diff, implementation, tests, specification, and
all three review requests. Do not limit review to the latest repaired lines.

## Rejected cycle R2

Claude Opus 5 rejected review commit
`bdc2009224bd10aab1fec5e7e63f3e7358081484`, tree
`a52ac389f0cb265928fc248d5de666e29f8a228e`, with:

1. A blocker showing `__new__` plus `object.__setattr__` could forge an object
   that passed the mapping-proxy-only seal check.
2. Required fail-closed corrections for non-string regex inputs and unset
   sealed slots that leaked `TypeError` or `AttributeError`.
3. A required correction because the subclass gate used rebindable module
   state.

The normalized Claude R2 ledger SHA-256 is
`11a6302f29c25b96b8f9220657dd6be8943a20c3d57302ec2800c6dd8fea97ac`.
AGY R2 was nonterminal because its headless read command was denied before any
review response; that record SHA-256 is
`0dad3f19dab8ee75f5fc848077711473329eb846a300224e99aa625ceeec04ff`.

## Repair-loop 2 disposition

- Every boundary-created sealed instance now carries a private identity proof
  checked by `_require_sealed`; uninitialized or mapping-proxy-only `__new__`
  forgeries fail closed.
- Every sealed subtype has empty slots and cannot gain an instance dictionary.
- A closure-backed metaclass consumes exactly the fixed internal type set and
  permanently rejects later subclasses without rebindable module state.
- Regex-bearing public inputs are type-checked before matching.
- Missing proof/value slots and all tested malformed public inputs raise
  `ReadinessError`.
- Regressions cover forged and uninitialized objects, spoofed subclasses,
  non-string seal fields, owner IDs, idempotency keys, and replay owners.

Qualification passed:

- Ruff formatting and lint.
- Strict mypy.
- 70 focused D7/security tests.
- 366 complete repository tests.
- Two dogfood iterations, 11 transitions, restart reconstruction, final
  `COMPLETE`, and zero manual prompt copies.
- Diff/privacy checks with no private path, host, topic, credential, or token
  finding.

## Required assessment

Reassess all ten original criteria and specifically attempt:

- `__new__`, `object.__setattr__`, copied mapping proxies, and missing or fake
  proof slots across every sealed type;
- mutation or rebinding of module globals and metaclass state;
- subclass creation using spoofed module, name, qualname, keywords, or direct
  metaclass invocation;
- malformed values of every public parameter and every event field;
- cross-request composition, replay, serialization, terminal-state, URL,
  Unicode, and privacy bypasses;
- any bypass introduced by either repair.

Return exactly the closed JSON object required by the original request, with no
markdown or external prose. `ACCEPT` requires the exact detached review
commit/tree supplied by the invocation, all ten criteria assessed, and empty
`findings` and `rejected_security_claims`. Any uncertainty or actionable claim
requires `REJECT`.
