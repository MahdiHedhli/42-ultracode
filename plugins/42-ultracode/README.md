# 42 Ultracode local plugin

This is a repository-local Codex plugin scaffold for the 42 Ultracode v0.1
controller. It is not a standalone packaged service and it does not provide
automatic ChatGPT-thread continuation.

The installed Codex plugin is copied into the local plugin cache. Therefore a
relative `uv run --directory ../..` command in this plugin would target the
cache, rather than the checked-out repository. `.mcp.json` intentionally has
no active servers until Codex offers a supported project-root interpolation
primitive.

For a local checkout, add exactly one matching entry to an actor-specific MCP
configuration after replacing `/absolute/path/to/42-ultracode` with the actual
checkout path:

```json
{
  "mcpServers": {
    "ultracode-planner": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "/absolute/path/to/42-ultracode",
        "--no-editable",
        "python",
        "-m",
        "ultracode.mcp.server",
        "--role",
        "planner",
        "--database",
        "/absolute/path/to/42-ultracode/.ultracode/ultracode.db"
      ]
    }
  }
}
```

Use the same command with `--role worker` or `--role control` and a corresponding
server name for those actors. Each actor should load only its matching entry:

- `ultracode-planner` for planner capabilities;
- `ultracode-worker` for executor capabilities; or
- `ultracode-control` for human-control capabilities.

Do not expose all three entries to one untrusted actor. Use the exact same
absolute database path for every selected actor; relying on each client's
working directory can split planner and worker state. The module launch form
runs the checkout source rather than a potentially stale non-editable console
script after local source changes. The database remains in the ignored
`.ultracode/` directory and out of the plugin manifest.

The project’s durable engineering Skills live under `.agents/skills/`.  This
plugin intentionally ships no duplicate skill wrappers: its responsibility is
the local MCP launch boundary.
