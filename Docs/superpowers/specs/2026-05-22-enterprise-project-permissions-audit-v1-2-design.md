# Legal-MCP v1.2 Design: Enterprise Project Permissions and Audit Console

## Status

Draft approved for design direction on 2026-05-22.

## Goal

Legal-MCP v1.2 turns the v1.1 intranet MCP service into an enterprise-aware MCP gateway. The deployment assumption is that the AI model and MCP server run inside the enterprise environment, so v1.2 does not focus on preventing data disclosure to an external AI provider. Instead, it focuses on internal compliance:

- Each MCP token maps to a concrete Legal-MCP user.
- Users can only query projects they are allowed to access.
- Legal team users can keep the current workflow by defaulting to all-project visibility.
- Business users see only explicitly granted projects.
- Admins can maintain users, API keys, and project grants in a Web UI.
- Admins and auditors can inspect who queried which projects, through which AI client, and for what stated rationale.

## Current State

v1.1 provides shared team deployment:

```text
AI client -> local stdio proxy -> HTTP MCP server -> MCP router -> tools.py -> SQLite
```

It has one shared bearer token, Origin validation, the existing four MCP tools, required `rationale`, and local JSONL audit logging. This is enough for a trusted intranet pilot, but it cannot answer enterprise compliance questions such as "which employee accessed this project through AI?" or "why did this user see that project?"

`TODOS.md` already lists "Enterprise permissions and audit console" as a follow-up item. v1.2 makes that item concrete.

## Non-Goals

v1.2 will not implement:

- Field-level permissions or redaction.
- SSO/OIDC/Feishu/DingTalk/WeCom login.
- OCR, PDF parsing, or historical contract extraction.
- OA, Feishu, CLM, or file storage connectors.
- Editing project, contract, license, or risk data in the Web UI.
- Approval workflows for access requests.
- Complex audit analytics or retention policy automation.

## Recommended Approach

Use a server-side Policy Gateway around the existing MCP tool execution. The current tools should remain available to avoid breaking clients, but every tool call should execute under an authenticated user and an authorization context.

```text
AI client
  -> local stdio proxy
  -> HTTP MCP server
  -> Identity Layer
  -> MCP Router
  -> Project Policy Gateway
  -> Tool Execution
  -> Disclosure Audit
  -> MCP response
```

This keeps v1.2 centered on project-level compliance without expanding into full enterprise IAM.

## Identity Model

v1.2 introduces local Legal-MCP users and hashed API keys.

### users

Suggested fields:

- `id`
- `email`
- `display_name`
- `role`
- `status`
- `external_subject`
- `created_at`
- `updated_at`

`external_subject` is reserved for future enterprise identity integration. It can remain null in v1.2.

### api_keys

Suggested fields:

- `id`
- `user_id`
- `key_prefix`
- `key_hash`
- `label`
- `status`
- `last_used_at`
- `created_at`
- `revoked_at`

Only the hash is stored. The plaintext key is shown once when created.

## Roles

v1.2 uses a small fixed role set:

- `admin`: can manage users, API keys, project grants, and audit views.
- `legal`: can query all projects by default.
- `business`: can query only explicitly granted projects.
- `auditor`: can view audit records but cannot query project content through MCP.

The role model intentionally avoids custom role builders in v1.2.

## Project Authorization

Project-level grants are stored in `project_access`.

Suggested fields:

- `id`
- `user_id`
- `project_id`
- `granted_by_user_id`
- `created_at`

Authorization behavior:

- `admin` can administer all users and grants.
- `legal` users can query all projects without per-project grants.
- `business` users can query only projects present in `project_access`.
- `auditor` users cannot query project content through MCP.

Upgrade default:

- Existing shared v1.1 token mode remains available as a legacy pilot path.
- New v1.2 team usage should create one user token per person or AI client.
- Legal users default to all-project visibility.
- Business users default to no project visibility until granted.

## MCP Tool Behavior

v1.2 keeps the existing tool names:

- `list_projects`
- `get_project_context`
- `list_expiring_licenses`
- `list_open_risks`

Every tool call is evaluated under an `AccessContext` resolved from the bearer token.

Expected behavior:

- `list_projects` returns only projects visible to the current user.
- `get_project_context` returns context only when the target project is visible.
- `list_expiring_licenses` returns licenses only for visible projects.
- `list_open_risks` returns risks only for visible projects.
- `auditor` users receive an authorization error when calling content tools.

For project names or codes that exist but are not visible, the MCP response should avoid confirming hidden project existence to the AI. The preferred user-facing error is `not_found`; the audit record should store the internal decision as `denied`.

Field-level redaction is not part of v1.2. Once a project is visible, its current project, contract, license, and risk fields are visible.

## Admin Web UI

v1.2 adds a lightweight management UI. It is not a data editing interface.

Admin Web authentication uses the same local Legal-MCP user table. v1.2 does not integrate enterprise SSO for the Web UI, but the identity model keeps `external_subject` available for that future path.

Pages:

- Login.
- User list.
- Create, disable, and view users.
- Create and revoke API keys.
- Project authorization page for granting or revoking business user access.
- Audit page with filters for time range, user, project, tool, status, and client.

The UI can be server-rendered or minimal HTML. It should share the same database and identity layer as the MCP HTTP server.

## Audit Model

The existing JSONL audit log can remain for operational continuity, but v1.2 should add database-backed audit tables for the Web UI.

### audit_events

One row per MCP tool call.

Suggested fields:

- `id`
- `timestamp`
- `user_id`
- `api_key_id`
- `source_client`
- `tool_name`
- `rationale`
- `arguments_summary`
- `result_status`
- `error_code`
- `response_record_count`

### audit_disclosures

One or more rows per tool call, recording what project data was allowed or denied.

Suggested fields:

- `id`
- `audit_event_id`
- `project_id`
- `record_type`
- `record_id`
- `decision`
- `reason`

Supported `decision` values:

- `allowed`
- `denied`

The audit UI should answer:

- Which projects did this user query through AI?
- Which users queried this project?
- Which accesses were denied?
- Which MCP tools are used most often?
- What rationale did the AI client provide for a query?

## Request Flow

1. HTTP server receives a MCP request with `Authorization: Bearer <token>`.
2. Identity layer validates the token hash and loads user, role, and API key status.
3. MCP router parses `tools/list` or `tools/call`.
4. Policy Gateway creates an `AccessContext`.
5. Tool execution receives the context and applies visible-project filtering.
6. Audit event is written with tool, user, rationale, status, and arguments summary.
7. Disclosure rows are written for allowed or denied project-level decisions.
8. Response is returned to the MCP client.

## Error Handling

The service should distinguish internal audit reasons from AI-visible errors.

- Missing or invalid bearer token: HTTP `401`.
- Disabled user or revoked key: HTTP `401`.
- Authenticated user lacks permission for the tool class: MCP error `access_denied`.
- Existing but hidden project: MCP error `not_found`, audit decision `denied`.
- Database failure: MCP error `database_error`.

## Migration

v1.2 should preserve the v1.1 team deployment path while recommending named user tokens.

Suggested migration path:

1. Add the identity and audit schema.
2. Create an initial admin user during setup or via CLI.
3. Allow the admin to create legal and business users in the Web UI.
4. Generate per-user API keys for MCP clients.
5. Keep single shared token mode documented as legacy pilot mode.
6. Update team deployment docs to recommend per-user tokens.

## Success Criteria

v1.2 is complete when:

- Each MCP token maps to a user.
- `business` users see only granted projects.
- `legal` users can query all projects by default.
- `auditor` users can view audit records but cannot query project content through MCP.
- Admins can create users, revoke keys, and grant or revoke project access in the Web UI.
- All MCP tool calls write database-backed audit events.
- Allowed and denied project disclosures are visible in the audit UI.
- Existing v1.1 HTTP/proxy deployment still works or has a documented migration path.
