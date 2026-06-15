# Legal-MCP — Permission-Aware MCP Gateway

English | [简体中文](README.zh-CN.md)

**Legal-MCP is an open-source, self-hosted, permission-aware MCP gateway.** It
sits between an AI client and your existing data sources, answers
natural-language questions, discloses only the fields the asking user is
authorized to see, and audits every disclosure. It does not re-host your
business data.

**North-star:** 小问题别来问我，问 AI；AI 只回答你有权知道的那一部分。

Current release: **v0.4.9**.

## How it works

Every question goes through the same pipeline, entirely server-side:

1. **Identify** the end user (per-user API key, trusted proxy header, or local
   operator).
2. **Plan** — a server-configured LLM turns the natural-language question into
   one constrained JSON `QueryPlan` (domain, filters, return fields, limit),
   bound to a catalog of registered fields. No SQL, no free-form retrieval.
3. **Authorize** the plan by user identity, data domain, record scope, and
   field-level grants. Default-deny: an ungranted field is refused, not
   redacted.
4. **Retrieve** through a read-through connector from your *existing* source
   (a Feishu Bitable, the bundled SQLite demo, …). Filters are pushed down to
   the source; the gateway never holds rows longer than one authorized query.
5. **Disclose** only the authorized return fields to the AI client.
6. **Audit** every allow, deny, and disclosure (who asked what, which plan ran,
   which fields and records were returned, from which source).

The AI client sees a single MCP tool — `agent_query` — and never receives
database handles, SQL, model tools, or executable plans.

### Non-goals

- Owning your business facts (projects, contracts, licenses…) as a canonical
  database, or providing data-entry / CRUD UI.
- Entity MDM, master-data, or alias governance.
- BI / reporting, or a document knowledge base.
- Official connectors for every possible source.

The legal domain is a **reference demo** (the flagship example), not the
product boundary. The reusable core is permission grants + connector + audit.

> **Data boundary:** the gateway promises your *raw data stays in its source*
> and *only authorized fields are disclosed*. It does **not** promise the
> answer never leaves your network — with an external AI client, answers enter
> that model's context. For a fully on-prem exchange, run a local model (see
> [examples/legal-demo/LOCAL-MODEL-DEMO.md](examples/legal-demo/LOCAL-MODEL-DEMO.md);
> `legal-mcp doctor --probe-ai` verifies the endpoint).

## Install

```sh
uv tool install --upgrade legal-mcp && legal-mcp setup
```

or use the bundled installer (same thing, with client passthrough):

```sh
./install.sh --client cursor
# From this checkout, for local development:
LEGAL_MCP_PACKAGE=. ./install.sh --client cursor
```

`legal-mcp setup --client CLIENT` writes a local stdio MCP config. Supported
clients: `claude` (Claude Desktop), `claude-code`, `cursor`, `windsurf`,
`vscode`, `codex`, `generic`. Re-run setup any time to repair the config.

## Quick start (single machine)

```sh
# 1. Import your ledger into the local governance/demo DB
legal-mcp import path/to/project-ledger.xlsx

# 2. Configure the server-side planner model (any OpenAI-compatible endpoint,
#    including a local one such as Ollama)
export LEGAL_MCP_AI_PROVIDER=openai-compatible
export LEGAL_MCP_AI_MODEL=qwen2.5:14b
export LEGAL_MCP_AI_BASE_URL=http://127.0.0.1:11434/v1
export LEGAL_MCP_AI_API_KEY=unused-for-local

# 3. Health check, then run the stdio MCP server
legal-mcp doctor
legal-mcp serve
```

Ask through any MCP client: `MOON的法务BP是谁`, `指间山海的官网`, `我能访问哪些项目`.

## CLI overview

| Command | Purpose |
| --- | --- |
| `legal-mcp serve` | stdio MCP server (local, single user, full operator access) |
| `legal-mcp serve-http` | shared HTTP MCP server for a team |
| `legal-mcp serve-admin` | admin web UI (users, groups, grants, API keys, data sources) |
| `legal-mcp admin` | admin bootstrap (e.g. `admin create-user`) |
| `legal-mcp proxy` | local stdio ⇄ remote HTTP bridge for team members |
| `legal-mcp setup` | write an MCP client config (local or `--remote-url`) |
| `legal-mcp import` | import CSV/XLSX into the local DB |
| `legal-mcp doctor` | install/schema/client-config/AI-endpoint health checks |
| `legal-mcp scaffold-connector` | draft a connector YAML from a Feishu Bitable's real columns |
| `legal-mcp recall-terms` | generate/review field recall terms (synonyms) for a source |

## The MCP tool surface

In production (`--agent-public-only` or `LEGAL_MCP_AGENT_PUBLIC_ONLY=true`),
`tools/list` exposes only:

- **`agent_query`** — natural-language read questions. The server-side
  LangGraph workflow plans, validates, authorizes, executes, and shapes the
  answer. External AI clients cannot directly access database tools —
project, contract, license, and cross-domain retrieval run inside the
server, and both filter fields and return fields are permission-checked
before results are shaped for the client.

Without that flag the catalog also includes `describe_my_access` (your visible
projects and readable fields), `structured_query` (a trusted client submits an
already-constrained plan; it still runs the same validation/authorization
path), and `agent_write` (proposal-only; it never mutates data).

### Agent behavior worth knowing

- **Turn isolation.** Each `agent_query` call is its own turn: it replans from
  its own question and can never replay an earlier turn's plan, even on the
  same conversation `thread_id`. Prior turns feed the planner only as narrow,
  safe conversation context (entity identities and field names already
  disclosed to the requester), so a follow-up like `它的官网呢` resolves the
  prior entity without inheriting state.
- **Identity resolution.** A bare project token (`MOON`, `月之子`, `nova`,
  `山海`) is matched against *all* of a domain's identity fields at once
  (code-or-name, full-or-fragment) with precision ranking; genuinely ambiguous
  tokens return a ranked candidate list (`identity_disambiguation`) instead of
  a guess.
- **Operator pushdown.** `eq` / `contains` / `in` filters are translated into
  the source's native query (Feishu filter API, SQLite `LIKE`), with
  case-insensitive fuzzy search. Richer operators (`is_empty`, `date_*`) are
  reported as unsupported rather than silently dropped.
- **Multi-source fallback (v0.4.9).** A domain may be served by several
  configured sources in priority order. The primary answers when it has rows
  (tagged `data_source` in the result); an empty primary falls back
  source-by-source. If *several* sources have rows, the gateway returns a
  `source_disambiguation` (source names + record counts, no rows) so the agent
  asks the user which source to trust; the follow-up turn pins the choice via
  the plan's optional `data_source` field. An unknown source name fails closed.
- **Diagnosable empties.** An authorized-but-empty result is tagged `no_rows`
  in audit, distinct from a denial or a planner failure. Per-turn planning
  attempts are recorded in `agent_steps` (keyed by `turn_id`).
- **Bounded plan repair.** The planner retries only constrained-JSON plans
  after catalog validation errors; it never retries authorization denials.
- **Catalog-gated fields.** A new column in a source is not queryable just
  because it exists. It must be declared in the connector config (or the
  field catalog), with aliases as needed, and covered by grants.

## Connecting your data (connectors)

A reviewable, committable YAML file declares which domains come from which
sources. Secrets never live in the file — sources name *environment variables*
for credentials. A bad or incomplete config fails closed: the server refuses to
start. See the annotated example at
[examples/connectors/feishu-bitable.connector.yaml](examples/connectors/feishu-bitable.connector.yaml).

```yaml
version: 1
sources:
  - type: feishu_bitable            # the `project` domain, live from Feishu
    app_token: bascnYourAppToken    # non-secret resource id
    app_id_env: FEISHU_APP_ID       # secrets come from the environment
    app_secret_env: FEISHU_APP_SECRET
    domains:
      - name: project
        table_id: tblYourTableId
        fields:                     # ONLY declared fields are ever queryable
          - {name: project_code, is_identity: true, aliases: ["项目代号"]}
          - {name: name, is_identity: true, aliases: ["项目名称"]}
          - {name: legal_bp, aliases: ["法务BP"]}
  - type: sqlite_demo               # local DB as a named fallback for project
    name: local-db
    domains: [project]
```

Pass it to either server: `legal-mcp serve-http … --connector config.yaml`.

- **Source types:** `feishu_bitable` (live Feishu/Lark Bitable; use
  `base_url: https://open.larksuite.com` for international tenants),
  `tencent_docs` (Tencent Docs smart table, online), `local_file` (CSV / XLSX /
  JSON / JSONL / Markdown frontmatter — `pip install 'legal-mcp[local-file]'` for
  XLSX/Markdown), and `sqlite_demo` (the local governance DB).
- **Onboard from the console, no restart:** the admin Data Sources view has a
  three-step **Add data source** wizard (choose type → introspect columns →
  review & enable) that writes a runtime registry row, takes effect on the next
  query, and is default-deny until you grant its fields. Sources can be enabled,
  disabled, or deleted from the same view.
- **Writing your own connector:** [Docs/connector-authoring.md](Docs/connector-authoring.md).
- **Multiple sources per domain:** declaration order is priority; sources
  serving the same domain must carry distinct `name`s. Domains not claimed by
  any source are served by the local SQLite demo.
- **Scaffolding:** `legal-mcp scaffold-connector --app-token … --table
  project:tblXXXX` introspects real columns (names only, never values) and
  emits a draft config for human review.
- **Record scope per domain:** `by_governed_code` (rows visible by governed
  project code — the default), `by_owner` (a user sees only rows whose owner
  column matches their own identity; pushed down to the source), or `none`
  (field gate only).
- Authorization and audit always stay in the gateway, around the connector.
  The connector is a dumb read pipe; field gate and record scope apply to each
  source individually, including during fallback.

## Identity & authorization

- **Per-user API keys** (`lmcp_…`), issued from the admin UI; each request is
  authorized as that user. Keys can be reset; disabling a user immediately
  revokes access.
- **Trusted proxy header** — `serve-http --trusted-identity-header X-Auth-User
  --trusted-proxy 10.0.0.0/8` lets an SSO reverse proxy assert the end user
  (mapped via `users.external_subject`). Headers from untrusted peers, or
  conflicting identities, are rejected fail-closed. **SSO is done by a front
  OIDC proxy (oauth2-proxy / Authelia / nginx-oidc), not built in** — see
  [Docs/sso-reverse-proxy.md](Docs/sso-reverse-proxy.md).
- **Local operator** — `legal-mcp serve` (stdio) runs with full local access.
- Grants are DB-backed: users, groups, per-field permission grants, and
  project-level record scope, managed in the admin web UI (`serve-admin`).
  An unidentifiable subject gets zero rows, never "all".

Keep the admin UI on `127.0.0.1` (SSH tunnel) or behind a TLS reverse proxy.

## Team Deployment

One shared HTTP server on an intranet host; each member connects through a
local stdio proxy. Full guide: [Docs/team-deployment.md](Docs/team-deployment.md).

```sh
# Operator: bootstrap an admin, run the servers
legal-mcp admin create-user --email admin@example.com --role admin \
  --display-name "Admin" --password "…" --db /data/legal.db
legal-mcp serve-admin --host 127.0.0.1 --port 8766 --db /data/legal.db
legal-mcp serve-http --host 0.0.0.0 --port 8765 --db /data/legal.db \
  --audit-log /data/audit.jsonl --agent-public-only --connector /data/connector.yaml

# Team member: point a client at the shared server with a personal key
export LEGAL_MCP_API_KEY="lmcp_replace_with_the_user_api_key"
legal-mcp setup --client codex \
  --remote-url http://legal-mcp.internal:8765/mcp --api-key "$LEGAL_MCP_API_KEY"
```

Claude Code users pass `--client claude-code` instead of `--client codex`.
Keep deployment notes that contain hostnames, client paths, tokens, or real
data in local documents outside Git.

### Docker

`docker-compose.yml` runs the gateway (`:8765`) and admin UI (`:8766`) from the
`legal-mcp:v0.4.9` image with `./data` mounted at `/data` (DB, audit log,
connector YAML). `pull_policy: never` keeps startup offline-safe.

```sh
docker compose -f docker-compose.yml -f docker-compose.build.yml build legal-mcp
docker compose up -d
```

For weak-network hosts, build once and ship a tar:
`scripts/prepare-offline-images.sh` / `scripts/load-offline-images.sh`.

## Server-side model & observability

The planner model is configured on the server only — MCP callers never supply
model tools:

| Variable | Meaning |
| --- | --- |
| `LEGAL_MCP_AI_PROVIDER` | `openai` or `openai-compatible` |
| `LEGAL_MCP_AI_MODEL` | planner model name |
| `LEGAL_MCP_AI_BASE_URL` | endpoint (intranet / local Ollama supported) |
| `LEGAL_MCP_AI_API_KEY` | endpoint credential |
| `LEGAL_MCP_AI_JSON_MODE` | force JSON-object responses where supported |

Admins can also manage these at `/admin/agent-settings`; environment variables
override stored settings.

Tracing uses **self-hosted Langfuse only** (`docker-compose.langfuse.yml`; set
`LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`,
`LANGFUSE_BASE_URL=http://127.0.0.1:3000`).

Audit lives in the governance DB (`audit_events`, per-field
`audit_disclosures`, full request/response in `audit_event_details`) plus an
append-only JSONL log (`--audit-log`).

## Demos

- [examples/legal-demo](examples/legal-demo) — seeded end-to-end legal demo.
- [examples/legal-demo/FEISHU-MIXED-DEMO.md](examples/legal-demo/FEISHU-MIXED-DEMO.md)
  — `project` live from a real Feishu Bitable, the rest from SQLite.
- [examples/legal-demo/LOCAL-MODEL-DEMO.md](examples/legal-demo/LOCAL-MODEL-DEMO.md)
  — fully on-prem with Ollama.

## Development

```sh
uv sync
uv run pytest -q
```

Keep real client data, trial databases, exports, and deployment notes with
hostnames/tokens outside Git.
The repository intentionally ships with empty data directories only. See [CHANGELOG.md](CHANGELOG.md) for release history,
[SECURITY.md](SECURITY.md) for the security policy, and
[CONTRIBUTING.md](CONTRIBUTING.md) to get started.
