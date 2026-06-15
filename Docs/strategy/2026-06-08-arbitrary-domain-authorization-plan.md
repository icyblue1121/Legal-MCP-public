# v0.4.5 plan — content-driven domain + row authorization, with admin data-source & permission visibility

> Sequencing decision (2026-06-08): **identity passthrough ships first as v0.4**;
> this work is **v0.4.5**. Reason: the `by_owner` row scope below ("you only see
> your own rows") depends on a passed-through user identity, so identity work must
> land first.
>
> North star unchanged: *小问题别来问我，问 AI；AI 只回答你有权知道的那一部分。*
> This plan makes "有权知道的那一部分" configurable for **any** connected data
> source — not just the built-in `project` / `contract` / `license` domains — with
> both **domain-level and row-level** authorization driven by the source's
> **actual table content** (its real columns and row-identity), and lets an
> operator see every connected data source and the full space of assignable
> permissions from the admin console instead of editing code.
>
> Source-agnostic: the worked example happens to be a Feishu Bitable, but nothing
> here is Feishu-specific — it applies to any connector (SQLite, future sources).

## Motivation

A real Feishu Bitable ("成员分工安排") cannot map onto the `project` domain: it has
no `project_code`, so the gateway has no row-scope identity for it, and the
per-domain structural metadata (identity fields, relationship filters) is
hard-coded for the three legacy domains. The operator also cannot *see* which
domains a deployment serves, nor grant a domain to a specific user, without
editing a hard-coded dropdown.

## What is already true (do not rebuild)

These are load-bearing and stay:

- **Domain list is connector-driven**, not hard-coded. `build_query_catalog_from_connector()`
  ([query_catalog.py](../../src/legal_mcp/query_catalog.py)) asks the connector
  for `catalog()`; the gateway never assumes which domains exist.
- **Field/domain authorization is already generic and DB-backed.** The live
  `agent_query` path runs `authorize_query_plan`
  ([query_authorization.py:35](../../src/legal_mcp/query_authorization.py)), which
  checks `permission_grants` (group → `operation`, `data_domain`, `field_name`,
  `project_id`, `allowed`) for every filter and return field — for **any** domain
  string — and then ANDs the optional git YAML policy on top (defense in depth).
  So "admin grants a domain/field" already gates real queries; the mechanism
  exists.
- **`permission_grants` already expresses domain- and field-level grants.** The
  schema supports `data_domain` + optional `field_name` + optional `project_id` +
  `allowed`. It is under-used by the UI, not missing.
- **Admin console** is plain `http.server` server-rendered HTML with direct SQL +
  `admin_operations.py`; it already has a permissions tab that writes
  `permission_grants`.

## The actual gaps (what v0.4 builds)

### A. Generalized record (row) scope — *the critical unblocker*

Today row visibility is hard-coded to project identity:
`_DEFAULT_SCOPE_FIELD = "project_code"` and the governance lookup
`select project_code from projects ...`
([connector_retrieval.py:46,129](../../src/legal_mcp/connector_retrieval.py)),
plus `visible_project_ids` on the SQLite path. A non-project table has no such
identity, so it cannot be authorized at the row level at all.

**Design.** A domain declares *how* its rows are scoped, in the connector config:

```yaml
domains:
  - name: staffing            # 成员分工安排
    table_id: tbl...
    record_scope:
      mode: none              # no row scoping; domain/field grant is the only gate
    fields: [...]
  - name: project
    record_scope:
      mode: by_governed_code  # today's behavior, made explicit
      field: project_code     # row field bound to governance projects.project_code
    fields: [...]
```

- `mode: none` → no row post-filter; visibility decided entirely by the
  domain/field grant gate. This is honest and shippable for tables with no
  governed row identity (likely "成员分工安排").
- `mode: by_governed_code` → exactly today's `project_code` behavior, now one
  declared option instead of a constant.
- Reserved (not v0.4): `mode: by_owner` (row field bound to the requesting
  user's identity) for "you only see your own rows".

The gateway resolves the scope per domain from this declaration instead of the
hard-coded constant. `mode: none` removes the project assumption that blocks
arbitrary tables.

### B. Per-domain structural metadata from the catalog, not code

`query_authorization.py` hard-codes, per known domain: identity fields
(`_identity_fields`, [lines 259–266](../../src/legal_mcp/query_authorization.py)),
relationship-filter fields ([line 67](../../src/legal_mcp/query_authorization.py)),
the license return-field cap, and the `cross_domain` participant list
([lines 141–146](../../src/legal_mcp/query_authorization.py)).

**Design.** Derive identity fields from the catalog's existing `is_identity`
flags (`ConnectorField.is_identity` is already declared per field). Relationship
filters and `cross_domain` participation become per-domain catalog attributes,
empty by default. A new domain then needs **no code change** to be authorized
correctly — it just declares its fields. The legacy three domains keep their
current behavior by declaring the same metadata.

### C. Admin console: see data sources & assignable permissions, grant per user

1. **Inject the connector catalog into the admin server.** `LegalMCPAdminServer`
   does not currently hold a `ConnectorSetup` reference; pass it in at
   construction so admin pages can enumerate live data sources and domains.
2. **New read-only "Data Sources" view** (requirement *"管理后台可以看到接入了
   哪些数据源"*). Lists every connected source from the composite routing — its
   type (SQLite / Feishu / future), and the domains it serves. Drilling into a
   domain shows its fields (marking identity + sensitive) and its `record_scope`
   mode. This is the operator's map of "what is plugged in and how it's scoped".
3. **"Assignable permissions" catalog** (requirement *"有哪些权限可以分配"*). A
   derived view of the full grantable space for this deployment: for each live
   domain × field × operation × (row-scope option), what an operator *can* grant.
   The permissions form is populated from this catalog, not from constants —
   replacing the hard-coded domain dropdown
   ([admin_manage.py:474–478](../../src/legal_mcp/admin_manage.py)). An operator
   can only grant domains/fields that actually exist in a connected source.
4. **Grant a domain (and rows) to a specific user.** `permission_grants` is
   group-keyed today. To satisfy *"将特定域权限配置给不同用户"* directly, add a
   nullable `user_id` to `permission_grants` (exactly one of `user_id` /
   `group_id` set), and resolve a user's effective grants as
   `their direct grants ∪ their groups' grants`. Group grants remain the scalable
   path; direct user grants are the ad-hoc path the requirement asks for. The
   grant also carries the row-scope selector (e.g. which `project_code`s, or
   "all rows" for a `mode: none` domain), so **both domain-level and row-level**
   authorization are assigned in one place. Add a per-user "effective
   permissions" view showing the combined result.

### D. Content-driven config scaffolding (reduce template toil)

"根据表格的实际内容" — so a new table becomes a declared domain by reading its
*actual* schema, without hand-typing every field, while preserving the "only
declared fields are queryable, config is a reviewed git artifact" security
posture. Source-agnostic, expressed on the connector interface (not Feishu):

1. Add an optional `describe_schema()` capability to the connector interface
   (`connectors/base.py`): list a source's tables and their columns. Each
   connector implements it for its backend (a SQLite connector via `PRAGMA`; a
   Bitable-style connector via its list-tables / list-fields endpoints). Read-only.
2. A CLI helper (`legal-mcp scaffold-connector`) calls `describe_schema()` and
   emits a **draft** connector YAML from the real columns: all fields, a guessed
   identity field, blank aliases, `record_scope.mode: none` by default.
3. The operator reviews, deletes sensitive columns, confirms identity + scope,
   and commits. Auto-generation never widens disclosure on its own — a field is
   queryable only after it survives human review into the committed config.

## Non-goals for v0.4 (block scope creep)

- No automatic exposure of an entire table without operator review (conflicts
  with the security posture; see §D).
- `by_owner` row scope is **enabled by** the v0.4 identity-passthrough work
  landing first; it consumes the passed-through user identity. It is in scope for
  v0.4.5 *because* identity ships before it.
- No write/CRUD to data sources — read-through only, as always.
- No alias *inference*; aliases stay human-declared.

## Acceptance criteria

- [ ] **A1.** A connector domain with `record_scope.mode: none` is queryable
      end-to-end with field/domain authorization enforced and **no** project
      assumption anywhere in the path; covered by a test using a non-project
      table fixture.
- [ ] **A2.** `record_scope.mode: by_governed_code` reproduces today's
      `project_code` behavior; the existing mixed-source crown-jewel test passes
      unchanged.
- [ ] **B1.** Adding a brand-new domain (declare fields only, no code edit)
      authorizes filter/return fields correctly; identity fields come from the
      catalog `is_identity` flags. Test asserts no `query_authorization.py` edit
      is needed.
- [ ] **C1.** The admin "Data Sources" view lists every connected source and the
      domains each serves, with record-scope mode; drilling in shows fields.
- [ ] **C2.** The permissions form's options come from the live assignable-
      permissions catalog, not a constant; granting a non-existent domain/field
      is impossible.
- [ ] **C3.** An operator can grant a domain (optionally a single field) to a
      specific **user**; a query by that user is then allowed, and a query by a
      peer without the grant is denied — asserted by an integration test.
- [ ] **C4.** A per-user "effective permissions" view shows direct ∪ group grants.
- [ ] **D1.** `connectors/base.py` gains `describe_schema()`; `legal-mcp
      scaffold-connector` against any connector emits a valid draft YAML from the
      real columns that, after a no-op review, loads and serves the table.
- [ ] **Security gate.** The disclosure-leakage red-team test
      (`tests/test_disclosure_leakage.py`) is extended to cover an arbitrary
      (non-project) domain and still passes: an unauthorized field's value never
      enters the model context for the new domain either.

## Suggested sequencing

A → B unblock everything (arbitrary tables become authorizable). C makes it
operable from the console. D removes the typing toil. Each is independently
shippable behind the existing `--connector` / `--policy` switches.

## Roadmap note

Decided (2026-06-08): **identity passthrough is v0.4 and ships first; this is
v0.4.5.** `by_owner` row scope (§A) consumes the passed-through user identity, so
identity must land before it. The rest of this plan (A non-owner modes, B, C, D)
does not depend on identity and can begin in parallel once the identity interface
is stable.
