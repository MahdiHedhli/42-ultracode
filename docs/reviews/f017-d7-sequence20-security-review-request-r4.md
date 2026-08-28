# F017 D7 Sequence 20 independent security review request R4

## Immutable review boundary

- Original remote base: `d06b03d9b61f0176d8c4f8ec2883eaf0331d83c2`
- Sequence 20 review commit: `b4edff6d9fb1107a65e4f75020667ede16304888`
- Sequence 20 review tree: `17e4b32d45c65377c09f2744b3e82d4e812c0bfd`
- Complete base-to-review patch SHA-256:
  `ddb95d662dd3f20f10e78b37affcfe25003c8f43ee2348eb00236864fa5ac604`
- Prior R3 rejection ledger SHA-256:
  `9058ba51f2777d6dd54843f7faa2c7193e9fca1f63f24378328fd9e49f3a6390`

Review the complete base-to-review boundary, not only the latest repair.
Operate read-only and do not edit the detached snapshot.

## R3 findings that must be falsified

1. A returned sealed object exposed the construction token through `_proof`.
2. Replay did not independently validate every event field.
3. The subclass gate depended on a writable closure counter.

The repair removes `_proof`, registers each legitimate instance together with
the identity of its original immutable values mapping in a weak registry,
reuses one strict event validator at creation and replay, and uses a structural
direct-subclass rule with no mutable counter.

## Required adversarial attacks

Attempt all of the following rather than trusting the regression tests:

- construct every exported sealed type directly;
- use `__new__`, `object.__setattr__`, a copied mapping proxy, the original
  mapping proxy, and a replaced mapping proxy;
- recover or manufacture any construction capability through public return
  values, object attributes, descriptors, weak references, or garbage
  collection behavior;
- exploit `WeakKeyDictionary` identity/equality/hash behavior or stale entries;
- invoke the metaclass directly and through fresh metaclasses, spoofed names,
  modules, bases, and class namespaces;
- subclass every exported sealed type;
- inject malformed, unhashable, non-string, boolean, null, non-ASCII, and
  non-canonical values into every public input and every replayed event field;
- bypass receipt presence/absence rules during replay;
- compose request, handoff, and observation objects from different authorities;
- mutate response commit, path, digest, URL, sequence, route alias, nonce,
  owner, idempotency key, ordinal, event kind, receipt, and lifecycle order;
- induce a non-`ReadinessError` exception on any fail-closed public path;
- find any callback, environment, dynamic import, network, browser, transport,
  posting, or real-delivery capability.

## Frozen ten-domain acceptance criteria

1. Canonical handoff authority and commit-pinned response URL.
2. Symbolic unresolved route with zero resolver calls.
3. Sanitized `MOCK_LOCAL_ONLY` observation with no raw page fields.
4. Deterministic sealed preparation and one-shot identity binding.
5. Inert lifecycle, restart, retry, receipt, duplicate, and terminal behavior.
6. No callback, environment, import, browser, network, or transport surface.
7. Closed public API and static import/call prohibitions.
8. Fail-closed rejection of all identity, URL, digest, route, and lifecycle
   mutations.
9. Privacy-safe exception text and canonical duplicate-free JSON.
10. No real-delivery claim or capability from `MOCK_DELIVERED`.

## Banked validation evidence

- Ruff format and lint: pass.
- Mypy: pass.
- Focused D7 and security suite: pass.
- Complete pytest suite: pass.
- Dogfood: two iterations, eleven transitions, restart reconstruction, final
  state `COMPLETE`.
- Worktree at the review commit: clean.

## Required response

Return exactly one terminal verdict: `ACCEPT`, `REJECT`, or `BLOCKED`.

`ACCEPT` requires all ten domains to pass with no blocker, required,
actionable, advisory, unresolved, or rejected-claim finding. For any other
result, provide minimal evidence and the smallest safe correction.
