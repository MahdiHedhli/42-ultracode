# D8 Supervised Delivery Contract

Status: pre-observation freeze

This contract fixes the F017 D8 implementation and qualification boundary before
campaign results or external reviews are observed.

## Authority

- Policy ID: `f017-m2-d8-supervised-chat-delivery-transport-v1`
- Policy SHA-256: `db58a1e73a934719f4df7b9e07a4217a289cb8f4b3b748ce16a0e537df8036b6`
- Accepted stages remain exactly D0 through D7.
- D8 is implementation readiness only. It is not an accepted stage.

## Transport profile

- Installed client: `codex-cli 0.146.0`
- Process argv: `codex app-server --listen stdio://`
- Shell execution: prohibited
- Transport: local stdio JSONL only
- Experimental API: prohibited
- Configuration, model, sandbox, approval, cwd, personality, effort, and service-tier overrides: prohibited
- Client requests: `initialize`, `thread/read`, `thread/resume`, exactly one `turn/start`
- Client notification: `initialized`
- Server notifications: `turn/started`, `turn/completed`, `error`
- Server requests, approval requests, user-input requests, unknown methods, duplicate response IDs, and malformed JSON: fail closed

This pre-observation freeze is historical authority for the initial profile. The Sequence 26/27 successor contract adds stable `thread/list` and exact `thread/status/changed` handling; it supersedes this method list without changing the no-live or D0-D7 acceptance boundary.

Selected installed-schema SHA-256 fingerprints:

| Schema | SHA-256 |
| --- | --- |
| `v1/InitializeParams.json` | `4f576f99e285beb28f71f48a72b887c1f517dada86fee348fe2af0a35511de23` |
| `v1/InitializeResponse.json` | `86dcd236d0576a82c85b933586dc45731260eab1b6edb3447b03f790277322b1` |
| `v2/ThreadReadParams.json` | `db97080f82facc3259dbb9404e9f0df81e360619f4cd73983a9d99d25f5089ee` |
| `v2/ThreadResumeParams.json` | `1dc47d294d0de32f334e0829893d743ec64393ebcf00d7212c9c55b03c34ed23` |
| `v2/TurnStartParams.json` | `48a0ee95b669b47f5557c68b99a4d459b50577ccce8ebc5976532f50e3c6d059` |
| `v2/TurnStartedNotification.json` | `e268134e79cae246e39f110e67bd2efbb49ce9a572520a85a96a7325eaf31e03` |
| `v2/TurnCompletedNotification.json` | `5b5f2ca515658ea6fcce7e961d1c3feddb3f48c0dcc813260c7ccf77a2d016af` |
| `v2/ErrorNotification.json` | `1ec871b02771300a26a34e41a7cfaf7484330a8c37c197d1ac133e753b083a09` |

## Human authority

The foreground command renders the exact UTF-8 payload and symbolic target alias,
then creates a fresh random challenge. Both input and output must be TTYs. The
operator must re-enter the challenge exactly before a short-lived, process-local,
single-use capability is minted. The capability is immutable, non-serializable,
thread-safe, bound to the preview hash and alias, and consumed before route alias
resolution. Tests may exercise synthetic capabilities; this qualification graph
must not create a capability for a real target.

## Durability and retries

The journal is append-only canonical JSONL with a SHA-256 hash chain, parent
directory synchronization on creation, and full-storage synchronization on Darwin
after every record. `ATTEMPT_STARTED` is durable before the `turn/start` line is
written. Once that write is attempted, any missing or ambiguous receipt becomes
terminal `UNCERTAIN`; automatic retry is prohibited. A partial line, invalid hash
chain, unknown event, identity mismatch, or recovered in-flight attempt fails
closed. A completed delivery and an uncertain delivery are both terminal. A
verified pre-write failure may be retried only after a fresh human confirmation.

## Frozen claims and counters

Qualification uses a deterministic fake local peer only. It must produce exactly
one request for each allowed request method, one `initialized` notification, no
other method, and zero real app-server launches, real alias resolutions, real task
reads/resumes, real turns, browser operations, MCP operations, automatic loops,
and posts. Twenty clean reconstructions must be byte-identical. Mutation and crash
campaigns must have zero unexpected passes.

## Sequence 38 durable route discovery

Sequence 38 adds a discovery-only protocol profile whose outbound methods are exactly `initialize` and `thread/list`. Active and archived pages share the same bounded listing engine and parser used by target-bound delivery. Discovery never calls `thread/read`, `thread/resume`, or `turn/start`.

The foreground operator selects an eligible task by explicit number or aborts with `q`, EOF, timeout, or exhausted corrections. No task identity is accepted from a command-line argument, environment variable, model response, or stored historical route. A valid selection is published into a fresh owner-only route root, registry, and fixed-basename locator using exclusive atomic writes and directory synchronization. The registry remains compatible with the production target-bound resolver.

Inbound responses, client notifications, server notifications, and server requests are counted separately. Only schema-verified, state-free notifications may be ignored before delivery writes; every inbound server request, malformed notification, pagination-integrity defect, or target-relevant inconsistency fails closed. No non-target task metadata is emitted through normal stdout, stderr, exceptions, public evidence, or the sanitized report.

The selected route and exact message are rendered together on the controlling foreground TTY. The subsequent single-use delivery challenge is entered personally by the operator. Discovery cannot infer, answer, copy, or reuse that challenge. Delivery retains its existing exact-target preparation, one-turn ceiling, durable journal, uncertainty boundary, and post-terminal route-digest checks.

## Sequence 40 discovery transport equivalence

Sequence 40 uses one source-defined production discovery path for both initialize-only liveness qualification and foreground task discovery. That path owns executable validation, the fixed stdio app-server process, process-group cleanup, the JSONL session, `initialize`, and the `initialized` notification. No alternate direct-process liveness path is accepted.

The run-local Codex protocol accepts exactly the current response/notification envelope and the explicit legacy JSON-RPC 2.0 envelope. Omitting `jsonrpc` is accepted only when every remaining field exactly matches the closed envelope; an incorrect version or any extra field fails closed. Requests remain fixed JSON-RPC 2.0 messages.

Before any initialize response, the shared path permits at most five fresh attempts within ninety seconds. Only spawn, bounded write, timeout, early EOF, or nonzero early-exit classes may be retried; each failed process tree is reaped before deterministic backoff. Executable, process-group, sensitive-stderr, response, notification, and cleanup failures are nonretryable. No retry is possible after an initialize response is observed. Diagnostics contain only a closed class and phase, bounded elapsed/read/write buckets, exit-status class, and retryability.

The Sequence 40 locator basename is separately allowlisted and creates a fresh Sequence 40 route namespace. Foreground discovery prints a preparation notice before process launch and a fixed no-send failure notice if the numbered list cannot be prepared. Route publication remains impossible before explicit numbered selection.
