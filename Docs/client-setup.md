# MCP Client Setup

Run `legal-mcp setup` for guided configuration, or pass a client explicitly:

```sh
legal-mcp setup --client cursor
```

Legal-MCP writes a stdio server entry that runs:

```sh
legal-mcp serve --db ~/.legal-mcp/legal.db --audit-log ~/.legal-mcp/audit.jsonl
```

## Supported Clients

| Client | Command | Default config path |
| --- | --- | --- |
| Claude Desktop | `legal-mcp setup --client claude` | `~/Library/Application Support/Claude/claude_desktop_config.json` on macOS |
| Cursor | `legal-mcp setup --client cursor` | `~/.cursor/mcp.json` |
| Windsurf | `legal-mcp setup --client windsurf` | `~/.codeium/windsurf/mcp_config.json` |
| VS Code / GitHub Copilot | `legal-mcp setup --client vscode` | `~/Library/Application Support/Code/User/mcp.json` on macOS |
| Codex | `legal-mcp setup --client codex` | `~/.codex/config.toml` |
| Generic stdio JSON | `legal-mcp setup --client generic` | `~/.legal-mcp/legal-mcp-stdio.json` |

Use `--config PATH` to write somewhere else, `--db PATH` to choose a database,
and `--audit-log PATH` to choose the audit log file.

After setup, run:

```sh
legal-mcp doctor
```

If the client supports manually adding MCP servers, use the generic stdio config
or point it at `legal-mcp serve`.

## v1.3 Tool Selection

Legal-MCP v1.3 exposes a machine-readable tool catalog through `tools/list`.
Clients should choose fine-grained tools such as `get_project_fields` and
`get_contract_fields`, pass explicit `fields`, or call `plan_query` to get a
minimum disclosure tool plan.

`get_project_context` is deprecated in v1.3 and does not return full project,
license, contract, and risk context. Update saved prompts and client workflows
that depended on full context responses.

## Remote proxy mode

For team deployments, each desktop client can keep using a local stdio MCP entry while forwarding requests to the shared intranet Legal-MCP server.

```sh
legal-mcp setup \
  --client codex \
  --remote-url http://legal-mcp.internal:8765/mcp \
  --api-key "$LEGAL_MCP_API_KEY"
```

The generated server command is:

```sh
legal-mcp proxy --url http://legal-mcp.internal:8765/mcp --api-key "$LEGAL_MCP_API_KEY"
```

Run a remote health check:

```sh
legal-mcp doctor --remote-url http://legal-mcp.internal:8765/mcp
```
