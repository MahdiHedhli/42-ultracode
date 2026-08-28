# F017 D7 Sequence 20 Independent Security Review Request

## Review boundary

Review the F017 D7 supervised chat-handoff readiness implementation as a pure,
transport-free security boundary. This is readiness-only code. It must not
resolve a route alias, access a browser or chat surface, deliver a message, or
claim that a mock lifecycle event proves real delivery.

The implementation candidate is fixed at:

- base commit: `d06b03d9b61f0176d8c4f8ec2883eaf0331d83c2`
- candidate commit: `74481254f131aa4d44a7424a22ddd84effa46d86`
- candidate tree: `c3c4d3d505cae6236fc4ff68f6351734eb61d1e5`
- candidate patch SHA-256:
  `221632a2f66b882bf880e71814095c888d5e4f914a5682f159196a9fb042a3a5`

Inspect the complete diff from the base through the candidate, plus this review
request. Do not review a substituted commit, working-tree mutation, generated
summary, or source excerpt in place of the repository objects.

## Governing authority

- D7 policy: `f017-m2-d7-supervised-chat-handoff-readiness-v1`
- D7 policy SHA-256:
  `b982416cdb59df9eb814ecabce60d1d65d5ea708fd3535591957554274911cc2`
- Sequence 20 prompt commit:
  `fcb4f4d7b36ef5ea300ad10c137976a84a2c6a2a`
- Sequence 20 prompt SHA-256:
  `37110864d30aba152d0348d197d41e6c8bcc094c7efb7704a5fe4a9286331247`
- Recovery parent commit:
  `24d037b6118b6e312b00c89521f9291b5e78e066`
- Recovery parent SHA-256:
  `7202d387f409fb3c59f914ebae3f11abc206be8a09ebb77498467cfa2e3ccd8f`
- Freeze Transition Table SHA-256:
  `4ce3d27514215c9f4356d64a5a929e037fdd317569079fa92e25a56e043d26a3`
- Frozen criteria receipt SHA-256:
  `8e580426b5ac19f46ee10cb8bdd353e102fb4e3c23b241c86a261430ee915ca0`

The policy permits only bounded 42 Ultracode D7 readiness work. Original
checkpoint access, full-model inference, and automatic chat posting are all
prohibited. D7 stage acceptance and operational ratification are outside this
review.

## Frozen acceptance criteria

Attempt to falsify every domain below without changing the criteria:

1. Canonical handoff authority and commit-pinned GitHub URL parsing.
2. Symbolic unresolved route aliases and zero resolver calls.
3. Sanitized `MOCK_LOCAL_ONLY` observations with no raw page or browser fields.
4. Deterministic sealed dry preparation and one-shot identity.
5. Inert lifecycle, restart, duplicate, uncertain-delivery, and retry behavior.
6. No callback, mutable mapping, environment authority, dynamic import,
   plugin, connector, shell, browser, accessibility, clipboard, network, Codex,
   publication, posting, or transport surface.
7. Closed public API and load-bearing AST, import, and call prohibitions.
8. Rejection of policy, prompt, parent, response, feature, machine, sequence,
   URL, digest, observation, route, and lifecycle mutations.
9. Privacy and exception-text nonleakage.
10. No real-delivery claim from `MOCK_DELIVERED`.

Explicitly test for URL and Unicode ambiguity, caller-forged sealed objects,
subclassing and serialization bypasses, identity swaps, replay or ordinal
confusion, duplicate receipts, terminal-state replay, mutable internal state,
and source-level import or capability aliases.

## Qualification evidence

The exact candidate passed:

- Ruff formatting and lint checks.
- Strict mypy over `ultracode`.
- 59 focused D7/security tests.
- 355 complete repository tests after final seal hardening.
- 20 deterministic identity reconstructions with one unique identity.
- 49 mutation/negative test instances.
- 7 static-prohibition test instances.
- 10 privacy test instances.
- Two dogfood iterations, 11 transitions, restart reconstruction, final
  `COMPLETE`, and zero manual prompt copies.
- Zero resolver, browser, chat, network, shell, connector, Codex, source-work,
  publication, or posting calls.
- Diff/privacy checks with no private path, host, notification topic,
  credential, or token finding.

Passing tests are evidence, not a substitute for adversarial source review.

## Required terminal response

Return one JSON object and no prose outside it. Use this closed shape:

```json
{
  "verdict": "ACCEPT or REJECT",
  "reviewed_commit": "40 lowercase hex characters",
  "reviewed_tree": "40 lowercase hex characters",
  "provider_model": "provider-reported model identity or UNKNOWN",
  "findings": [
    {
      "severity": "BLOCKER or REQUIRED or ACTIONABLE_ADVISORY or UNRESOLVED",
      "title": "short title",
      "evidence": "exact file and line or concrete counterexample",
      "remediation": "smallest required correction"
    }
  ],
  "rejected_security_claims": [],
  "criteria_assessed": 10,
  "summary": "evidence-backed terminal conclusion"
}
```

`ACCEPT` is valid only when the exact reviewed commit and tree match the
detached checkout, all ten criteria were assessed, and `findings` plus
`rejected_security_claims` are empty. Any uncertainty, required change, or
actionable advisory requires `REJECT`.
