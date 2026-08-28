# v0.1 Validation Plan

This document is the reproducible validation contract for the
`001-internal-loop` feature. It distinguishes what can be tested locally from
what needs a person to exercise a desktop surface. A passing unit suite does not
by itself prove automatic ChatGPT continuation.

## Baseline quality gate

Run these commands from the repository root on a clean checkout:

```sh
uv sync --all-groups --no-editable
uv run --no-editable ruff format --check .
uv run --no-editable ruff check .
uv run --no-editable mypy ultracode
uv run --no-editable pytest
```

All commands must exit successfully before a dogfood claim, commit, or release
candidate is accepted. Use `uv run --no-editable pytest` for the integrated suite rather than
describing selected tests as the full gate.

The D6 registration coverage binds the exact policy ID, canonical digest, and
all eleven envelope fields before lease acquisition. It rejects all cross-policy
substitutions, widening attempts, and reconstructed policy objects without
controller history, requests, or alias resolution, then proves durable identity
after restart. These tests register policy only and do not execute D6.

The `--no-editable` flag is intentional. In the validated `uv 0.11.17` /
CPython 3.13 environment, the editable console entry point did not resolve a
checkout whose path contains spaces; a regular wheel installation did. Verify
the command surface after setup with `uv run --no-editable ultracode --help`.

## Validation matrix

| Layer | Required proof | Expected mechanism |
| --- | --- | --- |
| Unit | schemas, transitions, policy, idempotency, redaction, replay | deterministic controller/protocol tests |
| Integration | controller ↔ SQLite, JSON-RPC MCP ↔ controller, role separation, concurrent claims | temporary databases and stdio subprocesses |
| Recovery | process restart, interrupted write/worker, expired or stale claim | reopen/replay a persisted temporary database |
| Subscription | Codex executes under an existing ChatGPT login and no API key | read-only Codex CLI probe |
| Desktop/plugin | planner and worker tools can be manually loaded/invoked on supported local surfaces | operator checklist below |
| Dogfood | at least two bounded turns, recorded evidence, restart check, no copied handoff contents | `ultracode dogfood` plus the real-repository scenario |
| Security | hostile/malformed payloads, path scope, secret redaction, capability boundaries | automated tests plus pre-publication review |

## Unit validation

The test suite must cover, at minimum:

- instruction and result schemas, including required handoff fields and
  unexpected/malformed payloads;
- legal and illegal lifecycle transitions, terminal-run behavior, and frozen
  policy/iteration limits;
- append-only event ordering, event hash-chain/replay validation, and safe
  idempotency handling;
- normal progression, pause/resume, failure, human escalation, and completion;
- one-active-worker claim behavior and duplicate claim/result delivery;
- ten sequential planner/worker iterations within a fixed ceiling, followed by a
  rejected eleventh delivery and a replay-equivalent snapshot;
- controller restart and deterministic reconstruction of state, iteration,
  pending instruction, and latest result;
- interrupted execution, expired/stale lease recovery, and paused-run refusal;
- credential redaction, unsafe changed-file paths, and controller refusal to
  execute worker-reported commands.

The replay assertion must compare derived state with the live snapshot. Do not
test only a cached `runs` row: events are the authoritative history.

## Integration validation

### Controller and SQLite

Use a fresh temporary database for every integration test. Verify that a
transactionally persisted instruction can be claimed once, submitted as a
structured result, read after a new controller instance is constructed, and
reviewed/completed only with sufficient result evidence.

Test concurrent claim protection with separate controller connections. The second
claim must fail without adding a second owner or corrupting the event order.

### Local MCP

Start role-scoped stdio servers against one database:

```sh
uv run --no-editable ultracode mcp --role planner --database .ultracode/demo.db
uv run --no-editable ultracode mcp --role worker --database .ultracode/demo.db
uv run --no-editable ultracode mcp --role control --database .ultracode/demo.db
```

The MCP integration test must perform `initialize`, `tools/list`, and
`tools/call`, validate JSON-RPC failures, and show that each server advertises
only its role's tools. Mutating calls require an idempotency key. Unknown tools,
role-inappropriate calls, and malformed arguments must fail without a state
change.

### Skills

Skills are instructions rather than a process-isolation boundary. Validate them
by loading the relevant role Skill in a fresh Codex task and verifying that it
uses only that role's MCP tools, emits the typed protocol fields, reports actual
evidence, and escalates rather than changing objective/policy. Record this as a
manual validation unless an environment can exercise the Skill/MCP pair in an
automated test.

### Repository-local Codex plugin

The repository contains a local marketplace entry and a plugin package under
`plugins/42-ultracode`. From the repository root, the installed CLI exposes this
loader flow:

```sh
codex plugin marketplace add "$PWD" --json
codex plugin marketplace list --json
codex plugin add 42-ultracode@personal --json
codex plugin list --json
```

On 2026-08-14, this flow successfully registered the local `personal`
marketplace and installed an enabled `42-ultracode@personal` plugin. The cached
manifest and empty `.mcp.json` matched the repository source. This is the
evidence for plugin packaging below.

These commands change the user's Codex plugin configuration, so review the
local manifest before installing and remove the local marketplace/plugin when it
is no longer wanted. They prove marketplace/manifest ingestion, not controller
launch: installed plugins are copied to a cache and cannot safely derive the
checked-out repository path. The plugin intentionally has no active MCP server
registry until a supported project-root interpolation primitive exists.

For actual local transport, configure exactly one role-specific MCP entry with
an explicit checkout path using
[`.codex/config.toml.example`](../.codex/config.toml.example) or
[`plugins/42-ultracode/README.md`](../plugins/42-ultracode/README.md). The
authoritative project Skills are the files under `.agents/skills`; the plugin
intentionally does not copy them.

## Subscription validation

The core must remain usable without an OpenAI API key. Check the actual Codex
account mode before claiming subscription-backed execution:

```sh
codex login status
codex exec --ephemeral --ignore-user-config --skip-git-repo-check \
  --sandbox read-only --json -m gpt-5.5 \
  'Reply exactly: SUBSCRIPTION_EXECUTION_OK'
```

Success means:

1. `codex login status` identifies a ChatGPT login.
2. The probe reaches `turn.completed` and contains
   `SUBSCRIPTION_EXECUTION_OK`.
3. No API key was supplied to make the probe work.

The 2026-08-14 spike met those conditions with `gpt-5.5`. It also found that
the installed `codex-cli 0.141.0` could not use its configured
`gpt-5.6-terra` default because the server required a newer CLI. If that happens
again, select a known compatible model or update Codex; do **not** silently add
an API-key fallback. The optional executor adapter can be exercised explicitly:

```sh
uv run --no-editable ultracode worker-once \
  --database .ultracode/demo.db \
  --run-id RUN_ID \
  --worker-id local-codex \
  --model gpt-5.5 \
  --sandbox read-only
```

Use `workspace-write` only after a human approves the repository modification
scope. This probe proves a subscription-authenticated Codex execution path; it
does not prove automatic ChatGPT planner re-entry.

## Plugin / desktop validation

This is an operator procedure, not an automated claim. It establishes the
highest supported cross-surface level without GUI automation.

1. Run the baseline quality gate and choose a single absolute checkout path and
   a database path under its ignored `.ultracode/` directory.
2. For Codex, optionally install the repository-local plugin using the preceding
   loader flow; it validates packaging but deliberately does not start an MCP
   server from the plugin cache. Read
   [`.codex/config.toml.example`](../.codex/config.toml.example) or the
   [plugin setup note](../plugins/42-ultracode/README.md), replace the checkout
   placeholder, and configure **only one** role for each client: planner for
   the planning surface, worker for Codex execution, or control for human
   operations. Point all selected clients at the same database.
3. Validate the Codex-side registration with `codex mcp list`. Restart or open a
   new desktop task if its tool list does not refresh after configuration.
4. Open the checkout in Codex so its repository-local Skills are available.
   Start the matching planner, worker, or control task with the corresponding
   `ultracode-*` Skill; confirm that its exposed MCP tools match that role.
5. In the ChatGPT desktop surface, where local stdio MCP is available, make an
   explicit planner tool call to create a run and submit a complete instruction.
   Record the returned run ID. Do not copy the instruction body to Codex.
6. In a Codex task configured only with the worker role, claim the instruction,
   perform the bounded repository work, and submit the structured result. The
   optional `worker-once` command may perform this execution after an explicit
   launch. Do not paste a raw result into the planner surface.
7. Return to the planner surface and explicitly call `ultracode_read_result`.
   Issue another bounded instruction, request human review, or complete the run
   only from recorded evidence. Use a separately configured control surface to
   pause, resume, stop, and inspect history.
8. Record which tool calls required a human to trigger, whether any handoff text
   was copied, the run ID, and the final event count.

The expected classification is **Level C**: local shared state with explicit
planner and worker invocation. If a current desktop build cannot load the local
planner MCP server, record that limitation and exercise the supported local MCP
integration tests instead. Do not claim Level A or use screen automation to
bridge the gap. The architecture keeps the planner adapter separate so a future
supported native continuation primitive can improve this without changing state
or protocol.

## Dogfood validation

Run the scripted protocol harness against a fresh local database:

```sh
uv run --no-editable ultracode dogfood \
  --database .ultracode/dogfood.db \
  --evidence .ultracode/dogfood-evidence.json
```

The evidence file must contain, at minimum:

- run ID and configured iteration ceiling;
- number of planner/executor iterations;
- ordered event count and state transitions;
- commands/tests reported by the scenario;
- restart/recovery outcome;
- manual interventions and a `manual_prompt_copy_count`; and
- final structured result, remaining uncertainty, and completion state.

For the real repository dogfood task, follow
[`docs/DOGFOOD.md`](DOGFOOD.md). A valid Level C dogfood record may include
explicit planner tool calls, an explicit worker launch, and human review. It
must report those interventions. It must report **zero copied instruction or
result bodies** to claim that prompt shuffling was eliminated at the supported
boundary.

Inspect a completed run with:

```sh
uv run --no-editable ultracode status --database .ultracode/dogfood.db RUN_ID
```

Do not commit a raw local database or unsanitized evidence. Commit a sanitized
evidence summary only when it was produced by an actual run and can be safely
made public.

The first executed record is
[`docs/dogfood/2026-08-14-v01-evidence.json`](dogfood/2026-08-14-v01-evidence.json).
It records both the two-turn scripted harness and the two-turn read-only
subscription-backed Codex adapter run, including explicit Level C interventions.

## Failure and recovery validation

Exercise these failures with isolated test fixtures or disposable databases:

| Failure | Required outcome |
| --- | --- |
| Controller stopped after a committed event | New controller replays the same valid state. |
| Worker crash after claim | Lease expiry/recovery leaves one safely claimable instruction; stale lease cannot submit. |
| Malformed or unexpected result | Validation fails with no partial state/event mutation. |
| Duplicate submission | Returns the original idempotent outcome or a safe rejection; no duplicate transition. |
| Illegal transition or terminal restart | Rejected; terminal state/history remain unchanged. |
| Iteration ceiling reached | Controller refuses a new planner instruction; an agent cannot raise the ceiling. |
| Paused run | Claim and submission paths refuse work until a control resume. |
| Corrupted event payload/order/hash | Replay rejects the history rather than inventing a state. |
| Human escalation | Run visibly enters `HUMAN_REQUIRED` and cannot silently continue. |

Do not mutate a real dogfood database to simulate corruption. Build an invalid
fixture or use a disposable copy, then prove the replay validator fails safely.

## Security validation and publication review

Automated checks must cover payload schema strictness, relative-path enforcement,
secret redaction, role-capability separation, lease-token checks, and the fact
that reported commands are data rather than executable instructions.

Before publishing, a reviewer must also:

- inspect `git status --short` and the complete diff for generated junk,
  machine-specific paths, raw databases, and stale documentation;
- check for credentials in configuration, event examples, test fixtures, and
  command output; do not print token-bearing diagnostics into an issue or log;
- confirm `.ultracode/` and local evidence outputs are not accidentally staged;
- review every filesystem or command-execution boundary for a human approval
  requirement; and
- verify that no v0.2 external agent, cloud queue, distributed worker, or API
  key requirement entered the v0.1 runtime.

For a Feature Loop D0-D3 pilot, additionally run the focused Feature Loop tests
and require exact commit/path/hash, expected-parent, duplicate-frontier,
privacy-category, checkpoint-denial, no-source-mutation, deterministic
publication, and transport-only retry coverage. Real aliases and checkpoint
roots must not be used by automated tests.

For the D2 security repair, additionally require adversarial tests for exact
prompt/sidecar identity, complete parent identity, alias non-serialization,
compiled privacy-category coverage, complete staged-diff scanning, and paired
feature/state expected-hash races. Publication is not accepted until both
control documents are staged and committed together.

Sequence 2 additionally mutates each trusted authorization field independently,
recomputes the untrusted prompt identity, and requires rejection before any
lease, controller-history change, or tracked mutation. Prompt, feature, state,
and privacy schemas are exact-version gates, and a verified identity cannot be
rebound to a different authorization policy.

Sequence 3 additionally requires a closed reviewed-policy registry. Tests must
reject a matching widened prompt/profile attack, unknown policy IDs, arbitrary
profile injection, policy copying/serialization/callback substitution, and
policy digest mismatch before lease acquisition and during durable binding
reconstruction.

Sequence 4 registers one D4 checkpoint-free read-only policy. Validation must
derive its canonical digest through production code, prove cross-policy phase
and gate rejection, bind every field independently, reject registry mutation
and unsafe minting, and preserve the existing D2 policy for sequence execution.

Sequence 6 registers one D5 bounded checkpoint-free Repack-branch write policy
without executing D5. Validation must preserve the exact D2/D4 values and
digests, derive the D5 digest through production canonicalization, reject every
cross-policy substitution and field mutation, enforce policy-ID-specific source
mutation and human gates, keep checkpoint/inference/chat capabilities universally
prohibited, prove zero lease/history mutation for a matching widened prompt, and
show that source and checkpoint aliases were never requested.

Sequence 9 registers one D5R1 bounded duplicate-role repair policy without
executing D5R1. Validation must preserve the PASS-only parent guard, admit only
the exact planner-authored PASS recovery attestation through the normal guard,
preserve D2/D4/D5 digests, derive the D5R1 digest through production
canonicalization, reject all field and cross-policy substitutions before lease,
prove durable policy binding across restart, and demonstrate that no source or
checkpoint alias was requested.

## Evidence record

For each release candidate or dogfood run, record the date, commit, environment
versions, commands actually executed, pass/fail result, run IDs, automation
level, and remaining manual interventions. Link the record to the relevant ADR
or issue if the result changes a capability claim.
