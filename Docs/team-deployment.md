# Team Deployment

Legal-MCP team deployment lets one operator maintain the canonical legal database while team members query the same shared context from Codex, Cursor, Claude Desktop, or another MCP client.

## Architecture

```text
Maintainer import
      |
      v
/data/legal.db
      |
      v
legal-mcp serve-http
      |
      v
legal-mcp proxy on each desktop
      |
      v
AI desktop client
```

## Operator setup

1. Choose an intranet host reachable by team members.

2. Import the current legal project ledger:

```sh
legal-mcp import project-ledger.xlsx --db /data/legal.db
```

3. Start the HTTP MCP server with only `agent_query` exposed to clients:

```sh
legal-mcp serve-http \
  --host 0.0.0.0 \
  --port 8765 \
  --db /data/legal.db \
  --audit-log /data/audit.jsonl \
  --agent-public-only
```

4. Check health:

```sh
legal-mcp doctor --remote-url http://legal-mcp.internal:8765/mcp
```

Expected output includes:

```text
Legal-MCP doctor: healthy
ok: remote HTTP server is healthy: http://legal-mcp.internal:8765/mcp
```

## Docker Compose

```sh
mkdir -p data
legal-mcp import project-ledger.xlsx --db data/legal.db
docker compose up --build
```

The server listens on:

```text
http://localhost:8765/mcp
```

Fast-path and retry improvements are server-side only. Team members do not need
to change MCP client configuration: production clients still call `agent_query`.
The server may answer common questions through 查询快路径 without an AI call, or
fall back to the AI planner with 有界重试 for repairable catalog-plan errors.
These paths 不硬编码具体数据库条目 and still enforce project visibility, field
authorization, disclosure audit, and the same registered field catalog. 新增数据库字段
must be registered and authorized server-side before they become queryable.

## v1.2 Enterprise Permissions

The v1.1 shared-token deployment remains available for small trusted pilots.
For v1.2 enterprise permissions, create named local users with roles:
`admin`, `legal`, `business`, and `auditor`.

Bootstrap the first admin:

```sh
legal-mcp admin create-user \
  --email admin@example.com \
  --display-name "Admin User" \
  --role admin \
  --password "replace-with-a-long-random-password" \
  --db /data/legal.db
```

Run the lightweight Admin Web UI:

```sh
legal-mcp serve-admin \
  --host 127.0.0.1 \
  --port 8766 \
  --db /data/legal.db
```

Keep Admin Web bound to `127.0.0.1` and reach it through an SSH tunnel, or put
it behind a TLS reverse proxy before binding it to a network interface. Admin
Web handles passwords, session cookies, and one-time API key display, so do not
serve it as plain HTTP on an intranet.

Use the Admin Web UI to create legal, business, and auditor users, issue
per-user API keys, and grant project access. Legal and admin users can see all
projects. Business users start with no project visibility and need project
access grants before their API keys can query project data. Auditor users are
for audit review and cannot query project content.

### Admin UI layout (v1.5.1)

The Admin Web UI is organised into top-level pages:

- **Users** — the landing page has just two actions: **New user** and
  **Manage users**.
  - *New user* is a single guided panel: identity, optional one-time API key,
    group memberships, and project access — all saved as one transaction.
  - *Manage users* holds everything else, split into tabs: **Users**,
    **Groups & Members**, **Permissions**, and **API Keys**. Each tab has its
    own search box, sortable column headers, and pagination (10 rows per page).
    Lists, tabs, page, and scroll position are preserved across every create /
    edit / delete so you never lose your place.
  - From the Users tab you can **edit** a user (display name, role, groups,
    project access), **set/reset a password**, and **disable/enable** the
    account. Disabling a user immediately stops all of their API keys from
    authenticating. (Only `admin` users actually use a password — for admin
    login. Passwords stored for other roles are not yet used for auth.)
  - From the API Keys tab you can **revoke** or **relabel** a key.
- **Database** — a summary of imported data (project, contract, license, risk,
  user, and key counts), an aggregated read-only **Entities** view (derived from
  contract/license counterparty and rights-holder fields), **Project aliases**
  management, and a drag-and-drop **Import** zone for `.xlsx` / `.csv` files.
  (In v1.5.1 the import endpoint validates the upload but does not yet load it
  into the database — wiring lands in 1.5.2.)
- **Audit** and **Agent Settings** are unchanged.

In **local mode** (`serve-admin --mode local` on a loopback host) the root URL
lands on the **Database** page, since data maintenance is the main single-user
workflow.

## Team member setup

Each team member installs Legal-MCP locally and configures their AI client to run a local proxy.

Codex:

```sh
export LEGAL_MCP_API_KEY="lmcp_replace_with_the_user_api_key"

legal-mcp setup \
  --client codex \
  --remote-url http://legal-mcp.internal:8765/mcp \
  --api-key "$LEGAL_MCP_API_KEY"
```

Equivalent one-line form:

```sh
legal-mcp setup --client codex --remote-url http://legal-mcp.internal:8765/mcp --api-key "$LEGAL_MCP_API_KEY"
```

Cursor:

```sh
legal-mcp setup \
  --client cursor \
  --remote-url http://legal-mcp.internal:8765/mcp \
  --api-key "$LEGAL_MCP_API_KEY"
```

Generic stdio config:

```sh
legal-mcp setup \
  --client generic \
  --remote-url http://legal-mcp.internal:8765/mcp \
  --api-key "$LEGAL_MCP_API_KEY"
```

The generated stdio entry runs:

```sh
legal-mcp proxy --url http://legal-mcp.internal:8765/mcp --api-key "$LEGAL_MCP_API_KEY"
```

For v1.1 shared-token pilots only, use `--legacy-token` on the server. In v1.2
and later, shared tokens bypass named-user attribution and project grants.

## v1.3 Deployment Notes

Before deploying v1.3, back up the SQLite database and run `legal-mcp doctor`.
The server runs startup checks for the local schema version and fails clearly
when the database is incompatible. Remote version checks can be configured for
update notices, but network failures do not block MCP startup.

v1.3 clients must use the tool catalog and fine-grained tools. Queries that
previously depended on `get_project_context` need to request explicit fields or
use the planner. This minimum disclosure model prevents default full project,
license, contract, and risk context responses.

## v1.4 Agent Entry

v1.4 adds `agent_query` as the preferred public AI entry point. The server uses
LangGraph to route natural-language questions through the same permissioned
internal tools, so field grants, disclosure audit rows, and minimum disclosure
still apply.

To expose only the agent entry point to team clients:

```sh
export LEGAL_MCP_AGENT_PUBLIC_ONLY=true

legal-mcp serve-http \
  --host 0.0.0.0 \
  --port 8765 \
  --db /data/legal.db \
  --audit-log /data/audit.jsonl \
  --agent-public-only
```

Configure model access with `OPENAI_API_KEY`, optional `OPENAI_BASE_URL`, and
optional `LEGAL_MCP_AGENT_MODEL`. Keep these values in operator-managed secrets,
not in Git.

Production observability must use self-hosted Langfuse on localhost, a private
Docker network, or an intranet host. For a host-local test deployment:

```sh
export LANGFUSE_PUBLIC_KEY="pk-lf-local"
export LANGFUSE_SECRET_KEY="sk-lf-local"
export LANGFUSE_BASE_URL=http://127.0.0.1:3000
```

Langfuse Cloud is not the production default. Langflow is prototype-only and
must not be connected to production Legal-MCP data without network/auth
isolation.

### Switching deployment mode from the admin UI

`--mode` (local/team) seeds the deployment mode on first startup; after that the
admin server stores it and you can switch from the UI (the mode label in the top
nav links to **Deployment Mode**). The persisted value wins across restarts.

- **Local → Team** requires setting and confirming an admin password in the same
  step; the server verifies it and logs you in before switching, so you cannot
  lock yourself out of the now-password-protected backend.
- **Team → Local** removes the login requirement, so it asks for explicit
  confirmation and is only allowed when the server is bound to a loopback host.

### Embedded observability in the admin server (passwordless)

The admin server can embed the self-hosted Langfuse dashboard so administrators
reach it directly from the **Audit** page — no separate Langfuse login. This is
safe because the admin backend is already authenticated.

How it works: the admin server runs a loopback reverse proxy (default port
`8767`) that bootstraps a Langfuse session from the init credentials and injects
it upstream, and strips `X-Frame-Options` / CSP `frame-ancestors` so the
dashboard can be embedded in an iframe. Access is gated by the admin session
(team mode) or open in local mode.

Enable it by giving the **admin** process these variables (the Langfuse compose
override `docker-compose.langfuse.yml` already wires them into `legal-mcp-admin`
and publishes the proxy port):

```sh
export LANGFUSE_BASE_URL=http://127.0.0.1:3000        # or http://langfuse-web:3000 in Docker
export LANGFUSE_INIT_USER_EMAIL=admin@legal-mcp.local
export LANGFUSE_INIT_USER_PASSWORD=change-me-admin-password
export LEGAL_MCP_OBSERVABILITY_PORT=8767              # optional, defaults to 8767
```

Then open the admin server and click **Open Observability (Langfuse)** on the
Audit page. When these variables are unset the entry is hidden and the rest of
the admin server is unaffected.

The proxy binds to the admin `--host`. Keep its port bound to loopback or a
trusted network only — it serves an authenticated Langfuse session, so do not
expose it publicly.

## Smoke test

Ask the AI client:

```text
查询 Acme 项目的发行对接人，用于合同沟通。
```

Expected answer:

```text
发行对接人是沪小胖。
```

## Audit log

Every MCP tool call writes to:

```text
/data/audit.jsonl
```

Each record includes timestamp, tool name, argument summary, rationale, source client, result status, and error code when applicable.

v1.2 also records DB-backed disclosure audit events for named-user access,
including the user, role, project, tool name, argument summary, rationale,
result status, and disclosure decision.

## Operational rules

- Revoke a user's API key and remove project grants when that user leaves.
- Keep `/data/legal.db` and `/data/audit.jsonl` on an encrypted disk or protected intranet server.
- The v1.1 HTTP server is intended for trusted intranet use.
- Use a reverse proxy with TLS before exposing the service beyond a trusted internal network.
