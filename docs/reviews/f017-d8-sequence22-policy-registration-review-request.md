# F017 D8 Sequence 22 Policy-Registration Security Review

## Review identity

- Accepted remote base: `71953adaf931c535b0d05d0d8d0da364d16207af`
- Accepted base tree: `a12b84945625ab0cf1ac403c45d00ab0f8f43ed4`
- Implementation commit: `153774ac7a211038a00016a576a5bd1e155d0e55`
- Implementation tree: `d7058b787a836e9dd0c3b2f30bef422017850df5`
- Implementation diff SHA-256: `ff9d8055891c70b5e87ad754cb2752a2f929438236823ded2756f4c0dee2056e`
- Review target: the detached checkout HEAD supplied by the review envelope
- Prompt SHA-256: `6d7edff9b42ca5adac6f40c6f87b8b0cde78d67c99ea31ff28d5d0bb36c80c33`
- Freeze-transition-table SHA-256: `c9f79d8ec10c5be0b06c37cc1922bc9e567f41437bcfee6bfb89c356f900f1dc`

Review the complete accepted-base-to-HEAD diff, not only the implementation
commit. The checkout must be detached, read-only in intent, clean, and have the
same commit and tree as the provider envelope.

## Frozen policy

- ID: `f017-m2-d8-supervised-chat-delivery-transport-v1`
- SHA-256: `db58a1e73a934719f4df7b9e07a4217a289cb8f4b3b748ce16a0e537df8036b6`
- Limitation: `SUPERVISED_ONLY_HUMAN_CONFIRMATION_REQUIRED_ONE_MESSAGE_ONE_TARGET_NO_BACKGROUND_LOOP`

The eleven authorization values are:

```text
schema=pulsarmlx.graph-prompt/1.0.0
feature_id=F017
machine_model=MacBook Pro M2 Max
machine_architecture=arm64
phase=Feature-Loop-D8-supervised-chat-delivery-transport
human_gate=PLANNER_ACCEPTED_D7_SUPERVISED_CHAT_HANDOFF_READINESS
source_repository=MahdiHedhli/42-ultracode
source_mutation=BOUNDED_42_ONLY_SUPERVISED_CHAT_DELIVERY_TRANSPORT
original_checkpoint_access=PROHIBITED
full_model_inference=PROHIBITED
automatic_chat_posting=PROHIBITED
```

The digest is canonical compact sorted JSON over
`{"authorization": <normalized fields>, "policy_id": <ID>}`.

## Scope and evidence

The change adds one enum member, one immutable boundary, one registry mint,
focused tests, and registration-only documentation. It adds no transport,
resolver, alias access, browser/chat/task client, receipt, posting logic,
dependency, callback, dynamic policy factory, or capability module.

- D7 parent and planner acceptance remain readiness-only with no live delivery authority.
- All eight policy digests match their frozen values.
- The ordered policy matrix has 64 successes/selections and 56 cross-policy rejections.
- All eleven D8 fields and eight noncanonical envelope classes fail before lease.
- Unsafe minting, mutation, copying, deep-copying, pickling, JSON serialization, aliases, and enum spoofing fail closed.
- `automatic_chat_posting`, checkpoint access, and full-model inference remain unconditionally prohibited.
- Focused tests: `157 passed`.
- Complete tests: `450 passed`.
- Ruff format/lint and strict mypy: pass.
- Dogfood: two iterations, eleven transitions, deterministic restart, zero copied prompts.
- Privacy and diff checks: pass.
- Mac Studio Sequence 10 is preserved as an independent control-plane frontier; its runtime and source were not accessed.
- Live delivery capability census: zero.

## Required falsification

Attempt to falsify:

1. Registry closure and singleton identity for all eight policies.
2. Independent digest derivation and exact D8 field binding.
3. Alternate encoding, coercion, normalization, duplicate, deletion, addition, and type-change rejection.
4. D7/D8 and all other cross-policy isolation.
5. Universal automatic-posting, checkpoint, and inference prohibition.
6. Immutability and absence of dynamic minting or caller-controlled profiles.
7. Zero lease/history/alias/transport effects on policy mismatch.
8. Absence of any live delivery implementation or capability.
9. Compliance with the declared Sequence 22 freeze transition table.
10. Preservation of the accepted D7 chain and no coupling to PulsarMLX, R001, or Mac Studio runtime/source.

Return one terminal verdict: `ACCEPT`, `REJECT`, or `BLOCKED`. `ACCEPT`
requires zero blocking, required, actionable-advisory, unresolved, or rejected
claims. For `REJECT`, provide reproducible, in-scope findings with exact file
and line references. Do not propose or implement D8 transport functionality.
