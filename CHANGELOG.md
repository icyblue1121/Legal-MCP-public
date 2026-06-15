# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> **Versioning note.** Releases use a `0.x` line. The earlier internal milestones
> `v1.3`–`v1.5.2` predate the open-source pivot, used a different non-public
> numbering, and were never PyPI releases. `0.2.0` is the first version under the
> permission-aware MCP gateway direction.

## [0.5.0] — v0.5 line

The v0.5 line makes data sources **self-serve and the planner harder to
false-empty**, on three pillars: a console one-click onboarding framework
(runtime registry + wizard + guardrails), connector reach (a dependency-free
local-file source and an online sample), and planner retrieval enhancements
(operator completion, field semantics + recall terms, the `eq` false-empty
rescue, explainable `no_rows`). OIDC stays out of the product — SSO is a front
OIDC reverse proxy feeding the existing trusted-header source. Schema v22 → **v25**
(`field_semantics`, `data_sources`). Tagged `v0.5`.

### Added — v0.5.10: documentation + `v0.5` tag
- **Connector author guide** ([Docs/connector-authoring.md](Docs/connector-authoring.md)):
  the `DataConnector` contract, the operator set to translate (reuse
  `sqlite_filter` for SQL-like sources), the security constraints (declared fields
  only, values-free introspection, no in-connector authorization, env-only
  credentials), registering the type, rich-text/pagination/rate-limit notes, and
  how recall terms are generated.
- **SSO reverse-proxy guide** ([Docs/sso-reverse-proxy.md](Docs/sso-reverse-proxy.md)):
  front an OIDC proxy (oauth2-proxy / Authelia / nginx-oidc) → trusted header →
  the existing `TrustedHeaderSource`, with a compose sketch and the gateway flags.
  States explicitly that a built-in OIDC flow is not on the roadmap.
- Updated `identity-model.md` (SSO reverse-proxy section), `threat-model.md` (the
  runtime-registry credential threat row, added in v0.5.8), and the README
  (new source types, the onboarding wizard, `recall-terms`, the SSO pointer).

### Added — v0.5.9: Tencent Docs smart-table online connector
- **A second online read-through source, the v0.5 online sample.** New
  `connectors/tencent_docs.py` reads a Tencent Docs smart table (腾讯文档智能表格) —
  chosen as the recommended online source for Chinese legal teams — through the same
  contract as Feishu: a config-driven catalog (only declared columns are queryable),
  an injectable `SmartSheetClient` seam so the translation logic is unit-tested with
  no network, and a thin credential-bound urllib client isolated and marked no-cover.
  - Operator push-down translates `eq` / `contains` / `is_empty` / `date_before` /
    `date_after` to flat conditions and `in` / `date_between` / the virtual
    `identity` `or_fields` token to one-level condition groups (OR for membership,
    AND for a closed range), pinned by structural unit tests. *Live caveat:* the
    exact smart-table value/filter envelope is the deployment's integration point;
    the operator translation is what these tests fix.
  - Registered as the `tencent_docs` source type in the connector config factory
    (YAML or the runtime registry), with the access token read from an env var
    (`access_token_env`, default `TENCENT_DOCS_TOKEN`) — never committed, fail-closed
    when missing.
  - The onboarding wizard is now multi-type: it can declare a `tencent_docs` source
    (online connect fields + introspection via `describe_schema` when credentials are
    present), so one-click onboarding covers online sources too. Other online sources
    are left to the connector-author guide (v0.5.10), honoring the "official = 1–2
    connectors" non-goal.

### Added — v0.5.8: data-source CRUD + credential model
- **The Data Sources view now manages the runtime registry, and the credential
  model is settled.** The console lists runtime-registered sources with their
  status and lets an admin enable / disable / delete them; the change takes effect
  on the next request (hot), no restart.
  - `db.list_data_sources` / `set_data_source_status` / `delete_data_source`, with
    a new `Runtime-registered sources` section on `/admin/database` and POST routes
    `/admin/data-sources/status` and `/admin/data-sources/delete`. Disabling takes a
    domain out of the live catalog without losing its reviewed declaration; deleting
    removes it entirely (the domain leaves the catalog, fail-closed). Both are
    admin-only and audited.
  - **Credential model: env reference by default.** A source's `secret_ref` holds an
    environment-variable *name*, never a secret value — a DB leak is not a credential
    leak. Encrypted-at-rest storage remains an explicit, not-yet-implemented opt-in
    (would require a deployment master key); the decision (per the v0.5 plan) is to
    keep env-reference the default and defer encryption. `threat-model.md` records
    the runtime-registry credential threat row.

### Added — v0.5.7: add-data-source wizard + security guardrails
- **An admin can now onboard a data source from the console in three steps, with
  the guardrails that make that safe shipping in the same version.** New
  `admin_data_sources.py` adds a server-rendered wizard:
  1. **Choose type + connect** (`/admin/data-sources/new`) — a registered connector
     type and its connection parameters (a local file path + format today; the
     online type lands with its connector in v0.5.9).
  2. **Introspect** (`/admin/data-sources/introspect`) — `describe_schema` lists the
     source's real *column names*, values-free, so the operator reviews a real schema.
  3. **Review + enable** (`/admin/data-sources/register`) — tick the columns to
     expose, mark identity fields, set the record scope, add aliases, then register
     into the v0.5.6 runtime registry (`status='active'`, hot — no restart).
  - **Guardrails (the reason wizard + guardrails are one version):**
    - **Admin only** — the routes are behind the existing `_current_admin` check; an
      unauthenticated POST is redirected to login and writes nothing.
    - **Default-deny by construction** — a new source defaults to
      `record_scope: none` and is registered with **zero permission grants**, so its
      fields are denied to everyone until an admin grants them; onboarding never
      auto-discloses.
    - **Only declared columns** — only ticked columns enter the source declaration;
      an un-ticked column is never queryable. No row value passes through the wizard,
      only column names and metadata.
    - **Validated + audited** — registration builds the connector first (a malformed
      declaration is rejected before persistence) and writes an audit event.
  - Added `connector_config.build_source_connector` (public per-type build/validate
    wrapper) so the wizard validates a declaration with the same factory the runtime
    uses. Leakage-tested: a wizard-onboarded source is default-deny to a non-admin
    for its non-identity fields.

### Added — v0.5.6: runtime data-source registry (`data_sources`, schema v25, hot-reload)
- **A data source can now be registered at runtime and take effect without a
  restart.** New `data_sources` table (schema **v25**) persists a source's reviewed
  declaration (`config_json` — the same shape as a YAML `sources[]` entry: type,
  domains, fields, identity, record_scope, aliases), with a `status` of
  `draft` / `active` / `disabled` and a `secret_ref` (env-var name; encrypted
  storage is a later opt-in). Only `active` rows join the live catalog.
  - **Union with the static config, hot.** `effective_connector_setup` merges the
    active DB sources into the YAML-declared setup (or a synthesized pure-SQLite base
    when there is no YAML config) at request time, reusing the same per-type
    `_build_source` factory. The per-request catalog rebuild
    (`_catalog_for_database`) and routing therefore pick up a newly-activated source
    on the very next query; disabling a row drops its domain (`unsupported_domain`).
  - **Cost-controlled.** The merged setup is cached on a cheap registry fingerprint
    (`count` + `max(updated_at)` of active rows), so connectors are rebuilt only when
    the registry actually changes, not every request.
  - **Authorization unchanged.** A DB-registered domain flows through the same field
    gate and record scope as any other; a source with no grants stays default-deny,
    so the registry adds a wiring path, not an authorization bypass.
  - Refactor: the connector source-type dispatch is extracted to a shared
    `_build_source`, and `CompositeConnector.routes()` exposes the routing table so
    DB sources merge into a fresh composite without mutating the static one.

### Added — v0.5.5: `local_file` read-through connector (CSV / XLSX / JSON / Markdown)
- **A first source with no external service: read a local structured or
  semi-structured table directly.** New `connectors/local_file.py` implements the
  `DataConnector` contract over CSV, XLSX, JSON / JSONL, and a directory of Markdown
  files with YAML frontmatter, and registers as the `local_file` source type in the
  connector config — so the v0.5 onboarding framework has a dependency-free source
  to drive end-to-end.
  - **Operator parity via an in-memory SQLite stage.** Rather than reimplement the
    operators, `query` loads the declared columns into an in-memory SQLite table and
    reuses the shared filter translation (new `connectors/sqlite_filter.py`,
    extracted from the demo connector), so `eq` / `contains` / `in` / `is_empty` /
    `date_*` / the virtual `identity` `or_fields` group all behave identically to the
    demo source. File columns are mapped to safe synthetic identifiers, so an oddly
    named column never reaches SQL.
  - **Declared catalog, reviewed boundary.** `describe_schema` discovers a file's
    real columns (values-free) for scaffolding, but only *declared* columns load or
    are queryable — an undeclared column is a hard "unknown filter field", never a
    silent leak. Markdown frontmatter keys become columns; the document **body is
    never read** (the v0.6 RAG question, explicitly out of scope).
  - **Zero-dep core preserved.** CSV and JSON/JSONL use the standard library; XLSX
    (openpyxl) and Markdown frontmatter (PyYAML) are lazy-imported behind a new
    optional `local-file` extra, so `dependencies` stays empty and those formats
    simply require the extra.
  - Shared `record_scope_from_dict` moved into `connectors/base.py` so the Feishu
    and local-file connectors parse the row-scope block identically.

### Added — v0.5.4: explainable `no_rows` + clarification
- **An authorized-but-empty result is now leak-free guidance, not a dead "找不到".**
  When a search matches the field gate and record scope but returns zero rows,
  `execute_plan` attaches a structured `clarification` (`reason: no_rows`) built
  *only* from catalog metadata (the domain's filterable + identity fields) and the
  user's own filter inputs — never from fetched rows, so it opens no new data path.
  It echoes the user's search terms, lists the fields they can filter on, and — when
  they pinned a specific identity field with `eq` — nudges the code-vs-name
  confusion that most often causes a real-entity false-empty. The rendered answer
  is human-readable guidance; the structured form is also exposed on the tool result
  so a client can drive its own clarification UX.
- **An ambiguous identity match renders a "did you mean" list.** When an identity
  token resolves to several candidates, the answer is now a readable candidate list
  (project_code / name) instead of raw JSON. The candidates are already
  record-scoped and field-gated and carry only identity ∪ granted return fields, so
  listing them discloses nothing new.
- Leakage verified: a non-matching project's values never appear in a no_rows
  answer; candidates are scoped identity values only.

### Added — v0.5.3: field recall-term generation + prompt governance
- **Recall terms (semantic synonyms) for each field are now generated at
  onboarding time and folded into the planner catalog, so a user's near-synonym
  resolves to the right canonical field — without any model call on the query
  path.** New `recall_terms` module and `legal-mcp recall-terms` CLI:
  - **Generation through the single model seam.** For each field (name + known
    aliases + domain) the deployment's `AIProvider.complete()` produces a batch of
    synonyms / colloquial phrasings / Chinese-English variants / jargon. The same
    seam the agent uses, so a **local/self-hosted model drives it with no new
    dependency** — honouring "the most sensitive path never leaves the network."
    Output is sanitized (semantic terms only, canonical name and existing aliases
    dropped, de-duped, capped) and written to `field_semantics` with
    `origin='generated'`.
  - **Governance, not hidden weights.** Terms are a reviewable, versionable,
    audited artifact: `legal-mcp recall-terms <source>` is a dry-run by default
    (emits a review JSON), `--write` persists and records an audit event,
    `--recompute` is required to overwrite existing generated rows, and a
    hand-authored (`origin='manual'`) row is **never** clobbered by generation.
  - **A name handle only — never an authorization change.** A recall term maps a
    near-synonym to a canonical field; the field gate still decides disclosure and
    no row value is ever carried. Generation happens at onboarding; the query path
    stays deterministic and low-latency.
  - **Fail-closed.** If the model is unavailable, a field degrades to *empty*
    recall terms (a feature loss), never a relaxed gate, an error that blocks the
    catalog, or a silently written empty row.
  - Per-domain source keying: terms are written under the sub-source that serves
    each domain (via the connector's `domain_sources()`), matching how the live
    catalog reads them back — fixing the v0.5.2 case where a multi-source
    `CompositeConnector` would not have found its own per-source semantics.

### Added — v0.5.2: catalog field semantics layer (`field_semantics`, schema v24)
- **Fields can now carry semantic metadata so an oddly-named column is reachable
  by natural language.** New `field_semantics` table (schema **v24**) holds, per
  `(source, domain, field)`, a `description`, example values, and synonyms, with an
  `origin` of `manual` or `generated` (the latter for the v0.5.3 recall-term
  generator). It applies uniformly to the built-in demo catalog and to
  connector-built catalogs.
  - **Synonyms fold into field resolution.** A synonym is merged into the domain's
    alias map (synonym → canonical field), so a near-synonym the user types now
    resolves to the right field — without ever overriding an existing alias or
    shadowing a real field, and stale rows for unknown fields are dropped.
  - **Metadata is injected into the planner prompt.** `catalog_context_for_prompt`
    emits a per-domain `field_semantics` block (description / examples / synonyms,
    omitting empty parts), and the planner system prompt tells the model to use it
    to map a phrase to a canonical field.
  - **Not an authorization change.** Synonyms are only a name handle: a field a
    synonym points to is still subject to the field gate, and no row value is ever
    stored or carried. This is the carrying layer; v0.5.3 populates it.
  - Additive, non-destructive migration: `create table if not exists` adds the
    table to an already-deployed DB; the loader tolerates an older DB without the
    table and malformed JSON, degrading to empty rather than failing.

### Added — v0.5.1: `is_empty` / `date_*` operator push-down on the connector path
- **The connector path now pushes down every `QueryPlan` operator the SQLite
  *direct* path already supported.** `is_empty`, `date_before`, `date_after`, and
  `date_between` were declared in the plan schema (and translated by
  `search_tools`) but the connector retrieval path only pushed `eq` / `contains` /
  `in` — so a connector-served domain (Feishu, and any future source) silently
  reported `unsupported_operator` for an emptiness check or a date range. They now
  translate to native source predicates:
  - **SQLite demo connector** mirrors the direct path: `is_empty` → `(col is null
    or col = '')`; `date_before` / `date_after` → `<` / `>`; `date_between` →
    `between ? and ?` over a `[start, end]` pair (ISO strings; the planner
    normalizes relative dates to absolute).
  - **Feishu Bitable connector** maps `is_empty` → the native unary `isEmpty`
    (empty value array), `date_before` / `date_after` → `isLess` / `isGreater`, and
    `date_between` → an `isGreaterEqual` … `isLessEqual` closed range inside a
    `children` AND-group (Feishu has no single between operator). *Live caveat:* for
    Bitable DateTime fields Feishu expects its own value envelope (`["ExactDate",
    "<ms>"]`); mapping the planner's absolute date to that envelope is a deployment
    concern — the translations here are pinned by structural unit tests.
  - Authorization is unchanged: the field gate and record scope still run around
    the connector; a richer operator no longer escapes them, it is simply served.

### Fixed — v0.5.0: planner lone identity-field `eq` false-empty rescue
- **A bare project token that the planner mapped to a single identity column with
  `eq` no longer returns a false-empty.** Since v0.4.8 the planner is told to emit
  the virtual `identity` + `contains` filter for a bare token, but it still
  sometimes guessed one identity field with `eq` — `name eq "MOON"` when MOON is a
  *code*, or a case-mismatched name — so an authorized query returned zero rows
  although the project existed. This was the residual the v0.4.9 cross-source
  fallback could only mask, never fix within a single source.
  - **Deterministic rewrite-and-retry at the execution layer.** When an authorized
    plan returns an empty result and its *only* filter is a lone identity-field
    `eq` (or a virtual `identity` `eq`), `execute_plan` rewrites that filter to
    `identity` + `contains` — which ORs across every identity field,
    case-insensitively, with the existing precision ranking / candidate
    disambiguation — and retries once. A non-identity equality (`stage eq "dead"`)
    and any multi-filter plan are left untouched, so a deliberate equality is never
    broadened.
  - **Recorded, not hidden.** The rescue does not overwrite the planner's original
    plan (kept for observability); it is tagged on the tool call and recorded in the
    audit run as `error_code = "rewrite:eq_to_identity"`, reusing the same column as
    the clarify / `no_rows` diagnostics. The marker carries no field value. A rewrite
    that does not help leaves a clean `no_rows`, unmarked.
  - Chosen as a deterministic execution-layer fix (not a planner retry loop) because
    it is a structural backstop for the v0.4.8 identity semantics, not model
    randomness. First version of the v0.5 line (self-serve onboarding + planner
    retrieval); see `Docs/strategy/2026-06-13-v0.5-version-plan.md`.

### Added — v0.4.9: multi-source fallback + source disambiguation
- **A domain can now be served by several data sources, and an empty answer on
  the primary source no longer ends the search.** A connector config may declare
  the same domain under multiple sources (declaration order = priority); sources
  serving the same domain must carry distinct `name`s. The new `sqlite_demo`
  source type lets the local governance DB be declared as a named fallback (or
  primary) alongside a remote source.
  - **Fallback on empty.** The primary source answers when it has rows (fallbacks
    are never queried, and its result is tagged `data_source`). An empty primary
    triggers the remaining sources in order; exactly one source with rows answers,
    tagged with its `data_source` name.
  - **Source disambiguation.** When *several* fallback sources have rows, no rows
    are returned — the result carries `source_disambiguation` (per-source name +
    record count) so the agent asks the user which source to use, mirroring the
    v0.4.8 `identity_disambiguation` pattern. The pending plan and source names
    are recorded as safe conversation context (`pending_source_choice`), and the
    follow-up turn pins the chosen source via a new optional `data_source` field
    on the query plan (an unknown name fails closed: `unknown_data_source`).
  - **Authorization unchanged per source.** Every per-source query runs through
    `execute_connector_plan` individually — field gate and record scope apply to
    each source exactly as if it were the only one, and a disabled source
    (`data_source_state`) is skipped during fallback.

### Added — v0.4.8: project identity resolver (code-or-name, full-or-fragment)
- **A user can now name a project by code or name, full form or fragment, and hit
  it reliably — no more planner single-field guessing.** v0.4.7 made `contains`
  search work over a connector, but the planner still had to *guess* which single
  identity field a bare token meant (`name = "MOON"` when MOON is the
  `project_code`; `name = "指间山海"` when the user typed only "山海"), so a code,
  a fragment, or a Chinese short name often missed. v0.4.8 adds a virtual
  `identity` filter that resolves a token against *all* of a domain's identity
  fields at once, with precision ranking and an ambiguity fallback:
  - **Virtual `identity` filter.** The planner emits a bare token as
    `{field:"identity", operator:"contains", value:<token>}`. `query_plan` /
    `query_catalog.validate_plan` accept `identity` as a legal virtual filter field
    on any domain that declares identity fields, and `query_authorization` exempts
    it from the field gate exactly as the identity fields it expands to are (it can
    only ever reach an identity column, so it never widens disclosure).
  - **OR pushdown to the source.** `ConnectorFilter` gains `or_fields`: one
    predicate ORed across several fields. The connector path expands `identity`
    into a single `project_code contains ? OR name contains ?` filter pushed
    *down* — Feishu as a one-level `children` OR-group (reusing the v0.4.7 children
    mechanism), the SQLite demo connector as a parenthesized `OR`. The SQLite
    *direct* path (`search_tools`) expands it the same way (`(project_code LIKE ?
    OR name LIKE ?)`, case-insensitive).
  - **Precision ranking + candidates.** A shared helper (`identity_match`) ranks
    the retrieved rows: if any identity field matches the token exactly
    (case-insensitive) only the exact hits are returned (so "MOON"/"nova" resolve
    to one project); otherwise the substring matches are returned as a ranked,
    capped candidate list carrying code+name so the agent can disambiguate (so
    "山海" lists `指间山海` and `山海经`, while a unique fragment like "月之子"
    answers directly). The candidate list stays behind record scope and the field
    gate — an out-of-scope row or an ungranted field never appears in it.
  - **Planner guidance.** The planner prompt now teaches: bare token →
    `identity`+`contains`; only an explicit 项目代号/项目名称 → lock the specific
    field with `eq`.
  - Verified end-to-end through the live agent path against a Feishu-served
    `project` domain: `MOON的法务BP是谁`, `月之子`, `nova` resolve to the right
    project; `山海` returns a code+name candidate list.

### Fixed — v0.4.7: connector retrieval lost fuzzy / multi-value / case-insensitive search
- **A connector-served source (Feishu) silently dropped every `contains` / `in`
  search to `unsupported_operator` and matched `eq` case-sensitively.** Once v0.4.6
  wired Feishu into the main stack, all queries route through the connector
  retrieval path — whose filter translation only honored `eq` (case-sensitive),
  even though the planner is told `contains`/`in` are supported, validation accepts
  them, and the SQLite *direct* path executes them (`LIKE`, `collate nocase`). So
  switching the live source to a connector forced the agent into exact-name
  guessing (`name = "nova"` → 0 rows, `name = "NOVA"` → 0 rows) and the symptom
  "查找搜寻时找不到信息". Operators now flow through the connector boundary via an
  operator-aware `ConnectorFilter`: the gateway pushes `eq` / `contains` / `in`
  down; Feishu uses its native `contains` and expresses `in` as a one-level
  `children` OR-group (Feishu has no native `IN`); the SQLite demo connector uses
  `LIKE` and case-insensitive `eq` (`collate nocase`). Richer operators
  (`is_empty` / `date_*`) are still reported, not silently dropped. The `by_owner`
  scope predicate stays an equality that overrides any client filter on the owner
  field. Verified end-to-end: `name contains "nova"` → resolves `NOVA / Project
  Nova 新星`; `project_code = "moon"` (lowercase) → resolves `MOON`.

### Fixed — v0.4.6: turn-safe agent queries (LangGraph query redesign)
- **Each `agent_query` turn is now an isolated query transaction.** A second
  question on the same conversation `thread_id` could previously execute the
  *first* question's plan: `run_agent_query` keyed the LangGraph checkpoint by the
  client conversation id, so a later turn hydrated the earlier turn's `query_plan`
  and `classify_question` skipped planning when a plan was already present. The
  checkpoint is now keyed by a fresh per-turn `turn_id`; the client `thread_id`
  remains the conversation id (echoed back, used for audit and as the Langfuse
  session). Natural-language classification is gated on an explicit
  `input_mode == "structured"` (not "a plan exists"), so a leaked plan can never
  short-circuit a natural-language turn (§B/§C).
- **Removed the global business fast path** that parsed project-code grammar and
  project/license field names out of natural language (`agent_fast_path` no longer
  plans fields). It assumed legal-demo phrasing — e.g. a verbose `请查询项目代码
  VT-0001 的法务 BP 是谁` became `name = "请查询项目代码 VT-0001"` — and silently
  searched zero rows. Field/domain planning is now always catalog-bound. Only the
  schema-independent **access-scope** fast path remains deterministic (§A).
- **Feishu read-through returned empty for every authorized row.** Feishu's
  `records/search` returns text cells as rich-text *segment lists*
  (`[{"text": "MOON", "type": "text"}]`), not bare strings, so the connector's rows
  carried list-valued scope fields. The `by_governed_code` post-filter compared
  `str(list)` against the governed `project_code` set and dropped *every* row —
  silent false-empty on the live path. The fake-client e2e test never caught it
  (it returned plain strings). `FeishuBitableConnector.query` now flattens
  rich-text cells to scalars; regression test feeds the real segment-list shape.

### Added — v0.4.6
- **Conversation context as input, not mutable graph state** (§D). A new
  `load_conversation_context` graph node reads a narrow, safe object — entity
  identities and field names already disclosed to the requester — and feeds it to
  the planner prompt so follow-ups like `它的官网呢` resolve the prior entity
  *without* replaying a prior plan. `record_turn_context` writes it only for a
  turn that succeeded with rows, so a failed or empty turn is never promoted as the
  next turn's entity. Stored in a new `agent_turn_context` table, keyed by
  `(conversation_id, turn_id)`; never stores raw rows or unauthorized values.
- **Per-turn planner audit** (§F). `agent_steps` gains a `turn_id` and its unique
  key is now `(thread_id, turn_id, step_index)` — planner attempts restart at
  `step_index = 1` every turn, so the old `(thread_id, step_index)` key collided
  across turns and the collision was silently swallowed exactly when per-turn plan
  audit was needed. Step-recording now re-raises integrity errors instead of
  swallowing them; `list_agent_steps` can narrow to one turn.
- **Legible empty results** (§G). An authorized zero-row search is tagged
  `diagnostic: {reason: no_rows, matched_authorization: true}` in server-side audit
  so an operator can tell a wrong filter from a permission denial; the tag carries
  no field values and is stripped from the client-safe response.
- Schema bumped to **v22** (non-destructive: `agent_steps` is rebuilt to add
  `turn_id`/relax its unique key, backfilling legacy rows under synthetic turn ids;
  `agent_turn_context` is created fresh).

### Added — v0.4.5 Phase 2: trusted reverse-proxy header identity
- **Resolve the request's user from a header a trusted reverse proxy injects**
  (`TrustedHeaderSource`, registered on the Phase 1 identity-resolver seam). The
  trust is in the proxy **boundary**, not the raw header: the source verifies the
  request's TCP peer is a configured trusted proxy (`--trusted-proxy IP_OR_CIDR`)
  before honoring `--trusted-identity-header NAME`. The header value maps to
  `users.external_subject`; `--trusted-header-email-fallback` optionally allows an
  `email` match. A header from an untrusted peer is rejected fail-closed, and a
  request presenting both a bearer token (incl. the legacy shared token) and the
  header is a conflict (401) — never silently resolved to one identity.
- **Audit records which identity source resolved each disclosure**
  (`audit_events.identity_source`: `bearer_token` / `legacy` / `trusted_header` /
  `local`), so a reviewer can tell an api-key disclosure from a trusted-proxy one.
  Stored as a source label, never a token. Schema bumped to v21 (non-destructive
  `ADD COLUMN` migration for existing databases).
- The HTTP server **refuses to start** when an identity header is configured with
  no trusted proxy (would otherwise silently deny every request).

### Added — v0.4.5 Phase 4: `record_scope: by_owner` row scope
- **A domain can scope rows to "your own" via `record_scope: {mode: by_owner,
  field: <owner column>, subject: external_subject|email|user_id}`** — closing the
  mode reserved (and rejected) in v0.4.0. The requester's subject is resolved by a
  dedicated own-scope resolver whose "no subject" result is the **empty set**, not
  the `None = unrestricted` sentinel the project-code path uses: anonymous, legacy,
  and unrestricted-but-unmapped contexts see **zero** rows, never everyone's.
- **The owner predicate is pushed down into the connector query** (overriding any
  client-supplied filter on the owner field), so the source filters *then* paginates
  — fixing a false-empty where a user's rows sat past the source's first `limit`
  rows. A defense-in-depth gateway-side post-filter drops any non-owner row a broad
  connector returns; the owner column is fetched only for that check and stripped
  from the response unless explicitly requested and authorized. The permanent
  leakage red-team test now covers a `by_owner` domain.

### Deferred — v0.4.5 Phase 3: OIDC
- **OIDC/OAuth is deferred to v0.5.** The core package is intentionally
  zero-runtime-dependency, and OIDC is not "nearly free" — it needs the first
  runtime dependency (JWKS/RS256 validation) or a full browser redirect flow. The
  identity-resolver seam already accepts a future OIDC source with no change to the
  `by_owner` or trusted-header behavior, so the deferral is structural-cost-free.

## [0.4.0] — 2026-06-08

### Removed — v0.4.0 §C C6: the git-YAML policy gate
- **Removed the declarative field-policy gate entirely** (`legal_mcp.policy_config`,
  the `--policy FILE` switch on `serve` / `serve-http`, `examples/policies/`, and
  the `policy` parameter threaded through the query/authorization/agent path). The
  **database permission grants are now the sole authorization gate**: a dormant,
  optional second file gate was attack + cognitive surface, and non-technical
  operators couldn't reason about it. The DB-grant gate already reproduced every
  disclosure outcome (field allow/deny, record scope, admin exemption, fail-closed
  unidentified contexts), so removing the policy gate drops no enforcement. The
  permanent leakage red-team test is now the sole guarantor of the core invariant.

### Added — v0.4.0 §C: arbitrary data sources, console-managed
- **Connect / disconnect a declared data source from the admin console** (C5): a
  disconnected source's domains leave the live catalog so queries against them
  fail closed (`unsupported_domain`), without editing the git-reviewed connector
  config. New `data_source_state` table (schema v20).
- **Per-user permission grants** (C3/C4): `permission_grants` accepts a `user_id`
  in addition to `group_id` (exactly-one), effective grant scope is `direct ∪
  groups`, and the admin console shows per-user effective permissions and per-domain
  grant holders.

### Added — v0.3 real read-through source (Feishu Bitable)
- **Feishu (Lark) Bitable connector** (`legal_mcp.connectors.feishu_bitable`): the
  first *real* read-through source. Config-driven catalog (only declared fields
  are queryable), `ConnectorQuery → Feishu search` translation, and a urllib
  client (tenant_access_token caching/refresh, pagination, error model) behind an
  injectable seam so the core is unit-tested with no network.
- **Connector-backed retrieval path** (`legal_mcp.connector_retrieval`): the
  connector-served counterpart of `execute_search_plan`. Same two-gate
  authorization — the field gate (`authorize_query_plan`) and record scope
  (`record_scope_project_ids`, now shared) — but rows come from `connector.query()`
  and row-level scope is applied as a `project_code` post-filter so the *same*
  decision reaches a non-SQL source. Proven equivalent to the SQLite path.
- **Composite connector + declarative connector config** (`connectors.composite`,
  `legal_mcp.connector_config`, `--connector FILE` on `serve` / `serve-http`):
  route each domain to its owning source, so one deployment mixes a real Feishu
  Bitable (`project`) with the local SQLite demo (the rest). Secrets via env, never
  in the file; a bad/incomplete config fails closed (refuses to start).
- **Mixed-source demo + crown-jewel test** (`examples/connectors/`,
  `examples/legal-demo/FEISHU-MIXED-DEMO.md`, `tests/test_feishu_mixed_e2e.py`):
  same question, same grants, different disclosure per role with the sensitive
  field served *only* from Feishu — proving the live path reads through the
  connector while authorization and audit stay in the gateway.

### Security
- The connector retrieval path preserves the core invariant: an unauthorized
  field's value never enters the model context, and out-of-scope rows from a
  broadly-credentialed source are dropped in the gateway before synthesis.

## [0.2.0] — 2026-06-07

The **permission-aware MCP gateway** pivot. Legal-MCP is no longer positioned as a
local SQLite legal-data platform; it is an open-source, self-hosted gateway that
sits between an AI client and existing data sources, answers questions with only
the fields a user is authorized to see, and audits every disclosure.

### Added
- **Declarative, git-committable field policy** (`legal_mcp.policy_config`,
  `examples/policies/legal-demo.policy.yaml`): `role → domain → field` allow/deny
  with default-deny, deny-over-allow, and row-level `record_scope`.
- **Two-gate authorization (defense in depth):** a field is disclosed only if it
  passes both the runtime DB grant *and* the declarative policy, wired into the
  live agent path (`authorize_query_plan` → `agent_graph.authorize_plan` →
  `run_agent_query` / `run_structured_query`).
- **Live `record_scope` enforcement:** policy row visibility now reaches graph
  execution, so `all`, `by_grant`, and default-deny row scopes affect live search
  results instead of only the in-process demo.
- **`--policy FILE` switch** on `serve` / `serve-http`. Default off; a bad policy
  file fails closed (refuses to start).
- **Admin/operator exemption from the policy gate** — admin already bypasses the
  DB-grant field check and is now exempt from the policy gate too (symmetric),
  removing a footgun where any policy omitting `admin` would lock operators out.
- **Read-through connector interface** (`legal_mcp.connectors`) and a SQLite demo
  connector; the query catalog is built from the connector.
- **Runnable legal demo** (`examples/legal-demo/`): in-process `run_demo.py` plus a
  live HTTP end-to-end (`seed_server_db.py`, `docker-compose.demo.yml`,
  `LIVE-DEMO.md`, `tests/test_live_policy_e2e.py`) — same question, different
  disclosure per user.
- **Permanent disclosure-leakage red-team test** (`tests/test_disclosure_leakage.py`)
  as a non-negotiable CI gate.
- Open-source release files: `LICENSE` (Apache-2.0), `SECURITY.md`,
  `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, this changelog; `pyproject` license
  metadata.
- Strategy docs: the gateway plan, one-page `threat-model.md` and `identity-model.md`.

### Changed
- README and product narrative reframed to the permission-aware gateway, with
  explicit non-goals.
- Governance schema separated from demo-source schema.
- `pyproject` version `0.1.0` → `0.2.0`; package description updated.

### Security
- Core invariant: an unauthorized field's *value* must never enter the model
  context during planning, retrieval, or synthesis.

### Notes
- The flagship synthetic demo data uses neutral placeholders (`ACME` / `示例项目`);
  a former internal codename was scrubbed from all tracked files.
