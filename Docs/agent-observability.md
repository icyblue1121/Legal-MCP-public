# Agent Observability

Legal-MCP v1.4 routes `agent_query` through a server-side LangGraph workflow.
Legal-MCP v1.4.1 makes that graph the retrieval boundary: production MCP
clients see only graph entry tools, not raw database-backed tools. The normal
catalog exposes `agent_query`, `agent_write`, `describe_my_access`, and
`structured_query`.

The graph classifies and normalizes each query, builds a constrained plan,
authorizes filter fields and return fields, executes internal project,
contract, license, or cross-domain search, writes Legal-MCP audit records, and
stores run metadata in SQLite. External AI clients cannot directly access
database tools or receive a database handle.

## Optional Agent Dependencies

For local development from this checkout:

```sh
uv pip install -e ".[agent]"
```

The repository lockfile also supports:

```sh
uv sync --extra agent
```

## Model Configuration

Set an OpenAI-compatible model endpoint for the agent runtime:

```sh
export OPENAI_API_KEY="replace-with-agent-key"
export OPENAI_BASE_URL="http://localhost:4000/v1"
export LEGAL_MCP_AGENT_MODEL="gpt-4.1-mini"
```

`OPENAI_BASE_URL` is optional when using the default OpenAI API endpoint.
`LEGAL_MCP_AGENT_MODEL` defaults to `gpt-4.1-mini`.

For the v1.4.1 provider adapter, prefer the explicit server-side AI variables:

```sh
export LEGAL_MCP_AI_PROVIDER="openai_compatible"
export LEGAL_MCP_AI_MODEL="qwen-local"
export LEGAL_MCP_AI_BASE_URL="http://127.0.0.1:11434/v1"
export LEGAL_MCP_AI_API_KEY="replace-with-server-key"
```

These settings can point at OpenAI-compatible intranet or future local model
endpoints without changing graph nodes. The provider returns an intent or plan
candidate only; it is not given callable database tools.

Use `LEGAL_MCP_AGENT_PUBLIC_ONLY=true` or `--agent-public-only` on
`legal-mcp serve` / `legal-mcp serve-http` when MCP clients should list only
`agent_query`.

Use `structured_query` for trusted structured payloads that still need the same
graph validation and authorization path. Use `agent_write` only to create
review proposals; v1.4.1 does not directly write SQLite from that entry.

## Self-Hosted Langfuse

Langfuse tracing is optional and disabled unless all required environment
variables are present:

```sh
export LANGFUSE_PUBLIC_KEY="pk-lf-local"
export LANGFUSE_SECRET_KEY="sk-lf-local"
export LANGFUSE_BASE_URL=http://127.0.0.1:3000
```

When Legal-MCP and Langfuse run in one private Docker network, use the private
service name instead:

```sh
export LANGFUSE_BASE_URL=http://langfuse-web:3000
```

Langfuse Cloud is not the production default. Production observability must use
self-hosted Langfuse on localhost, a private Docker network, or an intranet
host. Do not point production `LANGFUSE_BASE_URL` at
`https://cloud.langfuse.com`.

Trace metadata is sanitized: it may include `thread_id`, selected tool name,
status, and error code, but not raw project, contract, license, or risk result
payloads.

## Checkpoints And Runs

Agent checkpoints default to a SQLite file named
`legal-mcp-agent-checkpoints.sqlite` next to the Legal-MCP database. Each
completed `agent_query` inserts a row into `agent_runs`:

```sql
select thread_id, status, selected_tool, error_code, created_at
from agent_runs
order by id desc
limit 20;
```

Use `agent_runs` and the normal Legal-MCP disclosure audit tables as the source
of truth for what the agent selected and what data was disclosed or denied.

## Agent Planning Steps

`agent_steps` records each server-side planning attempt for a thread. It stores
whether the attempt came from the deterministic 查询快路径, the normal AI planner,
or an AI 有界重试, plus the selected or rejected constrained plan and validation
error metadata:

```sql
-- v0.4.6: steps are turn-keyed. thread_id is the conversation; one turn is one
-- agent_query invocation. Each turn restarts step_index at 1, so order by turn.
select turn_id, step_index, planner_source, status, error_code, reason
from agent_steps
where thread_id = ?
order by turn_id, step_index;
```

This table is operator and auditor telemetry. MCP clients still receive only the
client-safe `agent_query` response and never receive executable internal plans.
As of v0.4.6, only the schema-independent access-scope fast path is deterministic;
every field/domain question goes to the catalog-driven AI planner — there is no
hard-coded project-code or field grammar. 新增数据库字段 must be registered in the
field catalog and authorization model before the planner can query them. An
authorized but empty result is tagged `no_rows` in audit, distinct from a denial.

## Langflow

Langflow is prototype-only. Use it only with development or mock data, and do
not connect it to production Legal-MCP data without network/auth isolation.
Production routing is owned by the checked-in LangGraph workflow, capability
registry, field authorization, and audit layer.
