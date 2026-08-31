# ADR 0002: Feature Loop External-Interface Admission Preamble

**Status**: Proposed
**Date**: 2026-08-31

## Status and authority

This note records a prospective Feature Loop design rule. It is not an accepted
controller policy, does not change any registered Feature Loop policy, and does
not authorize prompt issuance, transport, checkpoint access, inference, or an
irreversible act. Implementation requires a separately specified, reviewed,
tested, and planner-approved change.

## Context

Recent F017 runs repeatedly terminated on defects at external interfaces rather
than defects in their internal graph logic. Representative failures included:

- a CLI flag consuming the wrong argument;
- a structured-output option receiving a pathname instead of inline JSON;
- a prompt sidecar carrying the wrong shape or digest length;
- incompatible field sets or sealed types at component boundaries;
- a reviewer attempting a prohibited tool or declining the requested audit;
- configuration written by one interface but rejected by another; and
- publication authority moving or disagreeing with projected state.

These defects were discoverable without repository mutation, checkpoint access,
one-shot authority, or numerical execution. Discovering them inside a numbered
sequence unnecessarily consumed sequence identifiers, planner turns, reviewer
availability, and sometimes repair budget.

The ordering lesson is consistent with the reviewed Colibri workflow at pinned
revision `8f512fc8c2f48ffa18cd624cd4a5bcaae4a4abfc`: validate deterministic,
reversible prerequisites and phase boundaries before expensive work, preserve
evidence at every boundary, and place the human decision immediately before the
irreversible action. Colibri's authority model is not adopted here; only those
ordering heuristics transfer.

## Decision proposed

Add a standing, source-free **External-Interface Admission Preamble** before a
numbered Feature Loop graph may be issued. The planner remains the sole prompt
issuer. A numbered graph is eligible for issuance only when every external
boundary it depends on has a fresh, machine-readable admission receipt.

The intended future operator surface is:

```text
feature-loop doctor --deep --emit-receipt
```

The command name is illustrative until specified and implemented. The preamble
must remain separate from the numbered run's lifecycle, lease, and repair-loop
accounting.

## Required ordering

### 1. Ceremony compilation

Compile and reject the prospective graph before any model, source checkout, or
transport is used. At minimum, verify:

- prompt bytes, basename-bearing sidecar, and digest shape;
- sequence number, target machine, phase, and filename agreement;
- expected parent path, digest, machine, status, and frontier projection;
- policy identity and all authorization fields;
- declared freeze transitions and terminal vocabulary;
- required terminal census fields with no duplicate or contradictory keys;
- explicit inheritance and amendment precedence; and
- absence of a directive that would launch or consume authority automatically.

### 2. Boundary-doctor checks

Exercise every external interface through the exact production construction
path while using only inert fixtures:

- **CLI invocation**: mechanically inspect argv tokenization, option arity,
  positional placement, and inline-schema delivery. A command that never
  started because argv was malformed is a harness defect, not a reviewer or
  capability verdict.
- **Reviewer admission**: pin CLI version and build, model identity, prompt
  digest, nonce echo, structured-output schema, and effective tool confinement.
  Require a rotating DAG-first canary before a real review. A reviewer decline,
  transport failure, canary failure, and completed security rejection are
  distinct outcomes.
- **Typed component composition**: derive tests from the authority DAG rather
  than hand-writing selected edges. Every producer output must construct and be
  accepted by its real consumer while preserving the governing identity digest.
  Uncovered and extraneous typed boundaries must both be zero.
- **Configuration behavior**: prove that the exact reader accepts the exact
  writer output in an isolated private home. Record effective behavior, not only
  configuration text. Do not mutate ambient configuration during admission.
- **Publication and notification**: validate prompt-control compare-and-swap,
  response/checksum/handoff ordering, state projection, notification alias
  nonserialization, and duplicate/terminal guards against inert repositories
  and sinks.

### 3. Admission receipt

Emit canonical, privacy-safe evidence binding at least:

- receipt schema version and admission result;
- graph identity, target machine, and prospective sequence;
- prompt, sidecar, policy, schema, executable, and configuration digests;
- exact CLI versions/builds and normalized invocation shapes;
- admitted reviewer identities, models, confinement, nonce/digest echoes, and
  rotating-canary results;
- source-derived DAG edge count, composition-test count, and uncovered or
  extraneous boundary counts;
- publication and notification fixture results;
- creation time, expiry time, and explicit invalidation inputs; and
- proof that source access, source mutation, checkpoint resolution/access,
  inference, live delivery, lease acquisition, and one-shot consumption were
  all zero.

The receipt is evidence, not authority. It cannot issue a graph, relax a policy,
mint a capability, or satisfy a human gate.

### 4. Privacy and authentication ceremony

Authentication and source-free invocation checks may occur during admission.
Any privacy ceremony involving retention, account state, or a foreground human
confirmation occurs after the harness itself is admitted and before source
content is supplied to a reviewer. Privacy evidence must be machine-local and
must not serialize credentials, aliases, route details, raw prompts, or private
source.

### 5. Numbered graph and human gate

Only after a fresh receipt passes may the planner commit and issue the numbered
graph. The graph rechecks the receipt and its invalidation inputs before lease
acquisition. The human GO remains last: after complete no-access composition,
qualification, and review, immediately before the irreversible one-shot action.

## Freshness and invalidation

Admission fails closed when any bound input changes. Invalidation includes, at
minimum:

- CLI version, build, executable, help/schema output, or effective tool set;
- model identity or reviewer confinement;
- ambient or isolated configuration digest;
- prompt, sidecar, policy, schema, DAG, or component bytes;
- target machine or route authority;
- expected parent or prompt-control frontier; or
- receipt expiry.

Deterministic reruns, CI waits, evidence normalization, publication
reconciliation, reviewer transport failures, reviewer declines, and malformed
invocations do not consume structural repair loops. They receive distinct
preflight outcomes and cannot be reported as implementation defects.

## Failure classes

The future doctor should use closed, non-overlapping classifications:

- `CEREMONY_SPEC_INVALID`
- `HARNESS_INVOCATION_INVALID`
- `BOUNDARY_CONTRACT_MISMATCH`
- `REVIEWER_NOT_ADMITTED`
- `REVIEWER_DECLINED`
- `REVIEW_TRANSPORT_UNAVAILABLE`
- `PUBLICATION_HARNESS_INVALID`

None of these outcomes implies that implementation code passed or failed a
security review. A completed reviewer rejection remains separate and governs
according to the registered review policy.

## Implementation backlog

Before this proposal can become controller policy:

1. Specify the receipt schema, invalidation matrix, and classification
   precedence in Spec Kit artifacts.
2. Implement the doctor as a pure/read-only surface with deterministic fixtures.
3. Generate typed-boundary composition checks from the source-derived authority
   DAG and fail closed on incomplete coverage.
4. Add exact invocation constructors and smoke tests for each admitted CLI.
5. Add isolated configuration round trips and inert publication/notification
   fixtures.
6. Add security tests proving the receipt cannot alter policy, state, leases,
   limits, history, human gates, or one-shot authority.
7. Dogfood the preamble on 42 Ultracode and bank evidence before requiring it
   for F017 prompt issuance.

## Acceptance criteria

This ADR may advance from `Proposed` only when independently reviewed evidence
shows:

- all required preamble checks run before numbered lifecycle creation;
- malformed argv and sidecars fail before a sequence or lease exists;
- every source-derived typed boundary has exactly one generated composition
  witness and there are no stale DAG edges;
- version or configuration drift invalidates the receipt;
- reviewer refusal, transport failure, canary failure, and completed rejection
  remain distinct;
- no preflight-only outcome consumes a structural repair loop;
- no admission check resolves a checkpoint root, accesses original checkpoint
  bytes, performs inference, launches live delivery, or creates one-shot
  authority; and
- the planner and human gates remain unchanged and nondelegable.

## Non-goals

This proposal does not import Colibri's runtime, authority model, or workflow.
It does not add external adversarial agents to the v0.1 core, automate arbitrary
ChatGPT thread continuation, weaken dual-review policy, bypass manual planner
review, or authorize Event 06. It only records the intended ordering discipline
for a future, separately authorized Feature Loop enhancement.
