# F017 D7 Sequence 20 Independent Security Re-review Request R2

## Exact review identity

Re-run the complete adversarial review defined by
`docs/reviews/f017-d7-sequence20-security-review-request.md`. The frozen ten
criteria and acceptance rule are unchanged.

The repaired implementation identity is:

- remote base: `d06b03d9b61f0176d8c4f8ec2883eaf0331d83c2`
- original candidate: `74481254f131aa4d44a7424a22ddd84effa46d86`
- first review commit: `321894218319ec38a036b8408fc25f1e32aa6c74`
- repair commit: `a417ec2b98b7d4b3a2cf83b42c9bfae08c33ef9b`
- repair tree: `c532c7a0db01c2ea5d325742790b4b71deb37365`
- repaired chain patch SHA-256:
  `2e44d3d4f0454d7c7ba176df62549594b55ad9636df72901cb12a6e679b88f5b`

Inspect the entire base-to-repair diff and both review-request documents. Do
not limit review to the repaired lines.

## Rejected cycle R1

Claude Opus 5 rejected review commit
`321894218319ec38a036b8408fc25f1e32aa6c74`, tree
`3b30d04eceda516fbf8697097340d5f916abad8b`, with two required findings and
one actionable advisory:

1. `prepare_dry_run` did not cross-bind the observation's response commit,
   path, digest, and prior sequence to the request.
2. Non-receipt events accepted malformed receipt values, including a path that
   leaked `TypeError` instead of `ReadinessError`.
3. The subclass prohibition trusted caller-controlled `__module__`.

The normalized rejected-cycle ledger SHA-256 is
`f6d0955c37419c8e4fc2a6aaeb717361422dc4b48bcefc9d75c740fa731e9b54`.

## Repair-loop 1 disposition

- Cross-request observation fields are now compared with the request before
  preparation.
- Receipt events require a string containing exactly 64 lowercase hexadecimal
  characters; every non-receipt event requires `None`.
- Sealed subclass creation closes after the module's fixed internal type set is
  defined and no longer trusts subclass `__module__`.
- New regressions cover cross-request composition, malformed string and
  non-string receipts on non-receipt events, and spoofed-module subclasses.

Repair-loop qualification passed:

- Ruff formatting and lint.
- Strict mypy.
- 62 focused D7/security tests.
- 358 complete repository tests.
- Two dogfood iterations, 11 transitions, restart reconstruction, final
  `COMPLETE`, and zero manual prompt copies.
- Diff/privacy checks with no private path, host, topic, credential, or token
  finding.

## Required assessment

Attempt to falsify all ten original criteria again. In particular, test:

- source observation A combined with request/handoff B;
- malformed, non-string, or unnecessary receipts for every event kind;
- subclass creation with spoofed module/name/qualname and after module import;
- direct caller composition, replay, serialization, and identity mutation;
- any new bypass introduced by the repairs.

Return exactly the closed JSON shape required by the original review request,
with no markdown or prose outside the JSON object. `ACCEPT` requires the exact
detached review commit/tree supplied by the invocation, ten criteria assessed,
and empty `findings` and `rejected_security_claims`. Any uncertainty or
actionable statement requires `REJECT`.
