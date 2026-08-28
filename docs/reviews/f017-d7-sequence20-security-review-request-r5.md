# F017 D7 Sequence 20 independent security review request R5

## Immutable review boundary

- Original remote base: `d06b03d9b61f0176d8c4f8ec2883eaf0331d83c2`
- Sequence 20 review commit: `4712ee4007c610b5623a438626d8bf07ed1455ad`
- Sequence 20 review tree: `e766ce6c8c23e766f34213f5d69ca1864ec2809a`
- Complete base-to-review patch SHA-256:
  `c65abfab8420a60ce54364aee8582add479ab5e1315b46c948fbb4ced502c118`
- Prior R4 rejection ledger SHA-256:
  `8a8000d059a9472928427b85d5393e9345e7d696ea4bff1f6b17d967bba74dc9`

Review the complete base-to-review boundary. Operate read-only and do not edit
the detached snapshot.

## R4 findings that must be falsified

1. Mapping-proxy backing dictionaries remained mutable without invalidating a
   seal.
2. Frontier value comparison preceded exact type validation.
3. A metaclass subclass calling `type.__new__` bypassed the subclass gate.
4. The boundary claim did not distinguish public-API integrity from hostile
   same-process private introspection.

The repair binds both mapping-proxy identity and a canonical content digest,
validates the digest before every use, validates exact frontier types before
equality, adds an `_Sealed.__init_subclass__` guard, and documents the trusted
same-process boundary explicitly.

## Required adversarial attacks

- Mutate every sealed type through `gc.get_referents`, direct proxy referents,
  copied or replaced mappings, nested mutable values, and content changes that
  preserve serialized length.
- Test malformed or unserializable backing-map mutations and require only
  `ReadinessError` from public consumers.
- Attempt digest reuse across objects, mapping aliases, garbage collection,
  object-id reuse, weak-key equality/hash behavior, and stale registry entries.
- Attempt direct construction, `__new__`, `object.__setattr__`, descriptors,
  `__class__` reassignment, copy, deepcopy, pickle, and weak proxies.
- Subclass every exported sealed type through `type`, the original metaclass,
  a derived metaclass calling `type.__new__`, spoofed class metadata, and the
  private base reachable through MRO. Distinguish a harmless private-root class
  from an object accepted as an exported exact type.
- Supply equality-confusing, equality-raising, float, boolean, decimal-like,
  fraction-like, non-string, non-ASCII, and malformed values to every frontier
  field and public parser.
- Mutate every event field before replay, including unhashable kinds and
  malformed receipts; require strict shared validation and `ReadinessError`.
- Compose request, handoff, and observation objects across authorities.
- Mutate commit, path, digest, URL, route, nonce, posture, capability,
  idempotency key, lifecycle order, receipt, and terminal state.
- Find any callback, environment, dynamic import, browser, network, transport,
  posting, or real-delivery capability.
- Verify that documentation claims public-API integrity only. Arbitrary code
  already authorized to inspect private module globals is explicitly trusted
  and is not claimed to be sandboxed.

## Frozen ten-domain acceptance criteria

1. Canonical handoff authority and commit-pinned response URL.
2. Symbolic unresolved route with zero resolver calls.
3. Sanitized `MOCK_LOCAL_ONLY` observation with no raw page fields.
4. Deterministic sealed preparation and one-shot identity binding.
5. Inert lifecycle, restart, retry, receipt, duplicate, and terminal behavior.
6. No callback, environment, import, browser, network, or transport surface.
7. Closed public API and static import/call prohibitions.
8. Fail-closed rejection of identity, mutation, and lifecycle attacks.
9. Privacy-safe exception text and canonical duplicate-free JSON.
10. No real-delivery claim or capability from `MOCK_DELIVERED`.

## Banked validation evidence

- Ruff format and lint: pass.
- Mypy over `ultracode`: pass.
- Focused D7/security suite: 82 passed.
- Complete pytest suite: 378 passed.
- Dogfood: two iterations, eleven transitions, restart reconstruction, zero
  manual prompt copies, final state `COMPLETE`.
- Worktree at the review commit: clean.

## Required response

Return exactly one terminal verdict: `ACCEPT`, `REJECT`, or `BLOCKED`.

`ACCEPT` requires all ten domains to pass with no blocker, required,
actionable, advisory, unresolved, or rejected-claim finding. For any other
result, provide minimal evidence and the smallest safe correction.
