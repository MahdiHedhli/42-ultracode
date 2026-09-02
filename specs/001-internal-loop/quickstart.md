# Quickstart: Internal Loop

This is a validation guide for the implemented local v0.1 loop. Refer to
[mcp-tools.md](contracts/mcp-tools.md) for exact tool contracts and
[data-model.md](data-model.md) for the lifecycle.

## Prerequisites

- Python 3.11 or newer and `uv`.
- A ChatGPT-authenticated Codex CLI for optional execution validation; no OpenAI
  API key is required.
- A compatible Codex model. The local 2026-08-14 spike used `gpt-5.5`; the
  installed default model required a newer CLI.

## Setup and Automated Validation

```sh
uv sync --all-groups --no-editable
uv run --no-editable ruff format --check .
uv run --no-editable ruff check .
uv run --no-editable mypy ultracode
uv run --no-editable pytest
```

Expected result: formatter, linter, type checker, and all unit/integration/security
tests pass.

## Local MCP Exercise

Start separate role-scoped processes against the same local state file. For
cross-client use, make the database path absolute and identical in every
client's configuration:

```sh
uv run --no-editable python -m ultracode.mcp.server --role planner --database .ultracode/demo.db
uv run --no-editable python -m ultracode.mcp.server --role worker --database .ultracode/demo.db
uv run --no-editable python -m ultracode.mcp.server --role control --database .ultracode/demo.db
```

Use the corresponding tool contracts to create a run, submit an instruction,
claim it, submit a result, inspect history, and complete it. The automated MCP
integration test performs this exchange without a GUI. The module form keeps a
checkout source change from being masked by a stale non-editable console wheel.

## Subscription Validation

```sh
codex login status
codex exec --ephemeral --ignore-user-config --skip-git-repo-check \
  --sandbox read-only --json -m gpt-5.5 'Reply exactly: SUBSCRIPTION_EXECUTION_OK'
```

Expected result: login reports ChatGPT and the execution output contains a
completed turn and the exact marker. If the configured default model fails with a
version-compatibility error, use the documented compatible model or update Codex;
do not substitute an API key.

## Dogfood Scenario

```sh
uv run --no-editable ultracode dogfood --database .ultracode/dogfood.db \
  --evidence .ultracode/dogfood-evidence.json
```

Expected result: two planner/worker iterations complete, controller reconstruction
is checked after a simulated restart, the complete test command is captured as
evidence, and the evidence JSON records the run ID, events, and manual
interventions.
