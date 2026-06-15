-- Legal-MCP schema. Tables fall into two groups, machine-checked in db.py
-- (GOVERNANCE_TABLES / DEMO_SOURCE_TABLES) and tests/test_schema.py, per plan
-- §4.1/§4.2:
--   * GOVERNANCE  — the product core the gateway legitimately holds: users,
--     groups, permission_grants, audit_*, agent_*, settings, schema_version.
--   * DEMO SOURCE — reference legal facts a real deployment serves from its own
--     system via a connector, NOT canonical business data owned here.
-- The two groups are interleaved below (FK creation order), so demo-source
-- tables are tagged inline with "[demo source]".

create table if not exists schema_version (
  id integer primary key check (id = 1),
  version integer not null,
  updated_at text not null default (datetime('now'))
);

insert into schema_version (id, version)
values (1, 25)
on conflict(id) do update set
  version = excluded.version,
  updated_at = datetime('now');

-- [demo source] projects/contracts/licenses/risks are reference legal facts;
-- a real deployment serves them through a connector, not from these tables.
create table if not exists projects (
  id integer primary key,
  project_code text not null unique,
  name text not null,
  stage text not null,
  legal_bp text,
  department text,
  release_team text,
  contact_person text,
  website text,
  notes text,
  created_at text not null default (datetime('now')),
  updated_at text not null default (datetime('now'))
);

create table if not exists contracts (
  id integer primary key,
  project_id integer not null references projects(id),
  external_key text not null,
  title text not null,
  handler text,
  payment_terms text,
  currency text,
  total_amount text,
  expiry_date text,
  counterparty text,
  company_entity text,
  signed_date text,
  contract_number text,
  income_expense_type text,
  summary text,
  created_at text not null default (datetime('now')),
  updated_at text not null default (datetime('now')),
  unique(project_id, external_key)
);

create table if not exists licenses (
  id integer primary key,
  project_id integer not null references projects(id),
  external_key text not null,
  license_type text not null,
  identifier text,
  entity_name text,
  issuer text,
  approval_number text,
  rights_holder text,
  copyright_holder text,
  operating_entity text,
  actual_operator text,
  authorization_relation text,
  expiry_date text,
  notes text,
  created_at text not null default (datetime('now')),
  updated_at text not null default (datetime('now')),
  unique(project_id, external_key)
);

create table if not exists risks (
  id integer primary key,
  project_id integer not null references projects(id),
  external_key text not null,
  description text not null,
  status text not null,
  source text,
  created_at text not null default (datetime('now')),
  updated_at text not null default (datetime('now')),
  unique(project_id, external_key)
);

create table if not exists companies (
  id integer primary key,
  name text not null unique,
  unified_social_credit_code text,
  created_at text not null default (datetime('now')),
  updated_at text not null default (datetime('now'))
);

create table if not exists company_seals (
  id integer primary key,
  company_id integer not null references companies(id),
  company text not null,
  seal_type text not null,
  custodian text,
  storage_location text,
  status text not null,
  borrower text,
  borrowed_at text,
  borrow_reason text,
  expected_return_at text,
  actual_return_at text,
  created_at text not null default (datetime('now')),
  updated_at text not null default (datetime('now')),
  unique(company_id, seal_type)
);

create table if not exists users (
  id integer primary key,
  email text not null unique,
  display_name text not null,
  role text not null check (role in ('admin', 'legal', 'business', 'auditor')),
  status text not null default 'active' check (status in ('active', 'disabled')),
  password_hash text,
  external_subject text,
  created_at text not null default (datetime('now')),
  updated_at text not null default (datetime('now'))
);

create table if not exists api_keys (
  id integer primary key,
  user_id integer not null references users(id),
  key_prefix text not null,
  key_hash text not null,
  label text not null,
  status text not null default 'active' check (status in ('active', 'revoked')),
  last_used_at text,
  created_at text not null default (datetime('now')),
  revoked_at text
);

-- [demo source] hybrid: authorization meaning + legal-project dependency.
-- Plan §6 阶段4 migrates this into the generic policy/grant system.
create table if not exists project_access (
  id integer primary key,
  user_id integer not null references users(id),
  project_id integer not null references projects(id),
  granted_by_user_id integer not null references users(id),
  created_at text not null default (datetime('now')),
  unique(user_id, project_id)
);

create table if not exists company_access (
  id integer primary key,
  user_id integer not null references users(id),
  company_id integer not null references companies(id),
  granted_by_user_id integer not null references users(id),
  created_at text not null default (datetime('now')),
  unique(user_id, company_id)
);

create table if not exists user_groups (
  id integer primary key,
  name text not null unique,
  description text,
  created_at text not null default (datetime('now')),
  updated_at text not null default (datetime('now'))
);

create table if not exists user_group_memberships (
  id integer primary key,
  user_id integer not null references users(id),
  group_id integer not null references user_groups(id),
  created_at text not null default (datetime('now')),
  unique(user_id, group_id)
);

-- A grant is keyed by exactly one grantee: a group (scalable path) or a single
-- user (ad-hoc direct grant, v0.4.0 §C C3). The exactly-one CHECK keeps the two
-- mutually exclusive; a user's effective grants are direct ∪ their groups'.
create table if not exists permission_grants (
  id integer primary key,
  group_id integer references user_groups(id),
  user_id integer references users(id),
  operation text not null,
  data_domain text not null,
  field_name text,
  project_id integer references projects(id),
  allowed integer not null default 1 check (allowed in (0, 1)),
  created_at text not null default (datetime('now')),
  check ((group_id is null) <> (user_id is null)),
  unique(group_id, operation, data_domain, field_name, project_id),
  unique(user_id, operation, data_domain, field_name, project_id)
);

-- [demo source] alias mapping for legal projects.
create table if not exists project_aliases (
  id integer primary key,
  project_id integer not null references projects(id),
  alias text not null unique,
  source text,
  created_at text not null default (datetime('now')),
  updated_at text not null default (datetime('now'))
);

create table if not exists admin_sessions (
  id integer primary key,
  user_id integer not null references users(id),
  session_hash text not null unique,
  expires_at text not null,
  created_at text not null default (datetime('now'))
);

create table if not exists audit_events (
  id integer primary key,
  timestamp text not null default (datetime('now')),
  user_id integer references users(id),
  api_key_id integer references api_keys(id),
  source_client text,
  tool_name text not null,
  rationale text,
  arguments_summary text not null,
  result_status text not null,
  error_code text,
  response_record_count integer not null default 0,
  -- Which identity source resolved the request (v0.4.5 Phase 2): bearer_token /
  -- legacy / trusted_header / local. A source label for the audit trail, never a
  -- token. Added last so an ALTER ADD COLUMN on existing DBs (db.py) keeps the
  -- same column order as a fresh build.
  identity_source text
);

create table if not exists audit_disclosures (
  id integer primary key,
  audit_event_id integer not null references audit_events(id),
  project_id integer references projects(id),
  record_type text not null,
  record_id integer,
  field_name text,
  group_id integer references user_groups(id),
  decision text not null check (decision in ('allowed', 'denied')),
  reason text not null
);

-- Full request/response payloads for the audit detail view. A sidecar table
-- (not extra columns on audit_events) so it is created safely on existing
-- databases without an ALTER migration. Only events written after this ships
-- have a row here; older events fall back to "detail not captured".
create table if not exists audit_event_details (
  audit_event_id integer primary key references audit_events(id),
  arguments_json text,
  response_json text,
  truncated integer not null default 0,
  created_at text not null default (datetime('now'))
);

create table if not exists agent_runs (
  id integer primary key,
  thread_id text not null,
  question_summary text not null,
  status text not null check (status in ('success', 'error')),
  selected_tool text,
  error_code text,
  created_at text not null default (datetime('now'))
);

-- Planner step telemetry. ``thread_id`` is the client *conversation* id; a turn
-- is one agent_query invocation (v0.4.6 §F). Planner attempts restart at
-- step_index = 1 every turn, so the old unique(thread_id, step_index) collided
-- across turns of the same conversation and the collision was swallowed — exactly
-- when per-turn plan audit is needed to debug a stale plan. The unique key is now
-- per-turn so two turns can each persist their step_index = 1 plan.
create table if not exists agent_steps (
  id integer primary key,
  thread_id text not null,
  turn_id text not null,
  step_index integer not null,
  planner_source text not null check (planner_source in ('fast_path', 'ai', 'ai_retry')),
  status text not null check (status in ('candidate', 'validated', 'rejected', 'selected', 'error')),
  model text,
  reason text,
  plan_json text,
  error_code text,
  error_message text,
  created_at text not null default (datetime('now')),
  unique(thread_id, turn_id, step_index)
);

-- v0.4.6 §D: safe, turn-scoped conversation memory. A follow-up like "它的官网呢"
-- must resolve the prior entity WITHOUT replaying a previous LangGraph plan as
-- mutable state. This stores only entity identities and field names already
-- returned to the requester (never raw connector rows or unauthorized values),
-- keyed by (conversation_id, turn_id); ``load_conversation_context`` reads the
-- latest row and copies it into the planner prompt as input, not graph state.
create table if not exists agent_turn_context (
  id integer primary key,
  conversation_id text not null,
  turn_id text not null,
  safe_context_json text not null,
  created_at text not null default (datetime('now')),
  unique(conversation_id, turn_id)
);

create table if not exists agent_settings (
  id integer primary key check (id = 1),
  ai_provider text not null default 'openai_compatible',
  ai_model text not null default 'gpt-4.1-mini',
  ai_base_url text,
  ai_api_key text,
  updated_at text not null default (datetime('now'))
);

insert into agent_settings (id, ai_provider, ai_model)
values (1, 'openai_compatible', 'gpt-4.1-mini')
on conflict(id) do nothing;

-- Runtime-switchable deployment mode. Seeded from the --mode CLI flag on
-- first startup; the admin "deployment mode" toggle then writes here and the
-- persisted value is authoritative across restarts.
create table if not exists deployment_settings (
  id integer primary key check (id = 1),
  mode text not null check (mode in ('local', 'team'))
);

-- Console-managed connect/disconnect state for declared data sources
-- (v0.4.0 §C C5). A source's field declarations stay in the git-reviewed
-- connector config; the console only toggles a declared source on or off. No
-- row (or disabled=0) = enabled. A row with disabled=1 takes that source's
-- domains out of the LIVE catalog, so queries against them fail closed —
-- without editing the reviewed config.
create table if not exists data_source_state (
  source_name text primary key,
  disabled integer not null default 0 check (disabled in (0, 1)),
  updated_at text not null default (datetime('now')),
  updated_by text
);

-- Field semantic metadata (v0.5.2, schema v24). Per-field description, example
-- values, and synonyms (recall terms) that are injected into the planner prompt
-- so an oddly-named column can still be hit by natural language. Keyed by
-- (source, domain, field) so it applies uniformly to YAML-declared sources and
-- future DB-registered ones. ``origin`` distinguishes hand-authored ('manual')
-- from model-generated ('generated', v0.5.3) entries; both are reviewable and
-- audited. Synonyms only map a near-synonym to a canonical field — they never
-- widen field authorization (the field gate still applies) and carry no row data.
create table if not exists field_semantics (
  id integer primary key autoincrement,
  source text not null,
  domain text not null,
  field text not null,
  description text,
  examples text,   -- JSON array of example values
  synonyms text,   -- JSON array of recall terms (synonyms / colloquial forms)
  origin text not null default 'manual' check (origin in ('manual', 'generated')),
  updated_at text not null default (datetime('now')),
  unique (source, domain, field)
);

create index if not exists idx_field_semantics_lookup on field_semantics(source, domain);

-- Runtime data-source registry (v0.5.6, schema v25). Lets a source be registered
-- at runtime (DB-persisted) instead of only in the static YAML connector config,
-- and take effect without a restart. ``config_json`` is the reviewed connector
-- source declaration (same shape as a YAML ``sources[]`` entry: type, domains,
-- fields, identity flags, record_scope, aliases) — the scaffold draft, persisted.
-- Secrets are never stored here: ``secret_ref`` names an env var (encrypted-at-rest
-- storage is a later opt-in). ``status`` gates whether the source joins the LIVE
-- catalog: only ``active`` rows do, so a new source defaults to ``draft`` (off) and
-- a domain with no grants stays default-deny.
create table if not exists data_sources (
  id integer primary key autoincrement,
  name text not null unique,
  type text not null,
  status text not null default 'draft' check (status in ('draft', 'active', 'disabled')),
  config_json text not null,
  secret_ref text,
  created_by_user_id integer references users(id),
  created_at text not null default (datetime('now')),
  updated_at text not null default (datetime('now'))
);

create index if not exists idx_data_sources_status on data_sources(status);

create index if not exists idx_projects_stage on projects(stage);
create index if not exists idx_projects_name on projects(name);
create index if not exists idx_projects_legal_bp on projects(legal_bp);
create index if not exists idx_projects_department on projects(department);
create index if not exists idx_projects_release_team on projects(release_team);
create index if not exists idx_contracts_counterparty on contracts(counterparty);
create index if not exists idx_contracts_handler on contracts(handler);
create index if not exists idx_contracts_expiry_date on contracts(expiry_date);
create index if not exists idx_licenses_license_type on licenses(license_type);
create index if not exists idx_licenses_expiry_date on licenses(expiry_date);
create index if not exists idx_licenses_actual_operator on licenses(actual_operator);
create index if not exists idx_licenses_operating_entity on licenses(operating_entity);
create index if not exists idx_risks_status on risks(status);
create index if not exists idx_risks_project_status on risks(project_id, status);
create index if not exists idx_company_seals_company_id on company_seals(company_id);
create index if not exists idx_company_seals_status on company_seals(status);
create index if not exists idx_users_external_subject on users(external_subject);
create index if not exists idx_api_keys_key_prefix on api_keys(key_prefix);
create index if not exists idx_api_keys_user_id on api_keys(user_id);
create index if not exists idx_project_access_project_id on project_access(project_id);
create index if not exists idx_company_access_company_id on company_access(company_id);
create index if not exists idx_user_group_memberships_user_id on user_group_memberships(user_id);
create index if not exists idx_permission_grants_group_id on permission_grants(group_id);
create index if not exists idx_permission_grants_user_id on permission_grants(user_id);
create index if not exists idx_project_aliases_project_id on project_aliases(project_id);
create index if not exists idx_admin_sessions_user_id on admin_sessions(user_id);
create index if not exists idx_audit_events_timestamp on audit_events(timestamp);
create index if not exists idx_audit_events_user_id on audit_events(user_id);
create index if not exists idx_audit_events_tool_name on audit_events(tool_name);
create index if not exists idx_audit_disclosures_audit_event_id on audit_disclosures(audit_event_id);
create index if not exists idx_audit_disclosures_project_id on audit_disclosures(project_id);
create index if not exists idx_agent_runs_thread_id on agent_runs(thread_id);
create index if not exists idx_agent_steps_thread_id on agent_steps(thread_id);
create index if not exists idx_agent_steps_status on agent_steps(status);
create index if not exists idx_agent_turn_context_conversation on agent_turn_context(conversation_id);
