# TODOs

> **2026-06 pivot:** the roadmap below replaces the old platform-route backlog.
> Legal-MCP is now an open-source, self-hosted, **permission-aware MCP gateway**,
> not a legal-data platform. Authoritative plan:
> [Docs/strategy/2026-06-06-permission-aware-mcp-gateway-plan.md](Docs/strategy/2026-06-06-permission-aware-mcp-gateway-plan.md).
> The pre-pivot backlog is preserved (frozen) at the bottom of this file — it is
> **not** the roadmap. New features must trace to the north-star scenario:
> *小问题别来问我，问 AI；AI 只回答你有权知道的那一部分。*

## Non-goals (these block scope creep)

- Owning business facts (projects/contracts/licenses…) as a canonical DB.
- Data-entry / CRUD UI.
- Entity MDM / master-data / alias governance.
- BI / reporting platform or document knowledge base.
- Maintaining official connectors for every data source (official = 1–2 only).

## New roadmap — v0.2 "Open Governance Pivot"

Source of truth and acceptance criteria: the migration plan, §5–§6.

- [x] **阶段1 战略接管** — README first screen + non-goals; mark old v1 plan legacy;
      this TODOS rewrite.
- [x] **阶段2 测试重心** — strengthen authorization / disclosure / mcp_server tests;
      production catalog defaults to `agent_query` only; add a permanent leakage
      red-team test (CI gate).
- [x] **阶段3 connector interface** — `connectors/base.py` + `connectors/sqlite_demo.py`;
      build the query catalog from the connector; reserve `fast_intents()` and
      record-scope / capability slots; sink the legal fast-path vocab into the demo
      connector.
- [x] **阶段4 schema split** — separate governance tables from demo-source tables in
      `schema.sql` / `db.py`; stop calling SQLite the canonical business DB.
- [x] **阶段5 declarative policy** — `examples/policies/legal-demo.policy.yaml` with
      field-level allow/deny **and** record_scope; default-deny; deny > allow.
- [x] **阶段6 minimal demo** — `examples/legal-demo/` with data, questions, and 3 demo
      users (legal / business / auditor) showing different disclosures + audit.

### Open-source release essentials (do early, see plan §13)

- [x] LICENSE (GitHub "Add license", Apache-2.0), CONTRIBUTING, SECURITY.md.
- [x] One-page `Docs/threat-model.md` (plan §12).
- [x] Minimal but real multi-user identity path (not demo fake), `Docs/identity-model.md`.

## Later roadmap (post-v0.2, summarized — see plan §7)

- [x] **v0.3** real read-through source (Feishu Bitable). Connector + HTTP client,
      connector-backed retrieval with gateway-side two-gate auth, composite +
      `--connector` config (mix Feishu `project` with local SQLite), mixed-source
      demo + crown-jewel test. Boundaries: connector path serves the `project`
      domain with equality filters; child domains / operator pushdown deferred.
- **v0.3.5 local / self-hosted model path** (Ollama / vLLM / OpenAI-compatible).
      **Goal: a local model can drive the *entire* server-side feature set** — the
      most privacy-sensitive operators can't send data to an external LLM, so the
      one LLM the gateway needs must run inside their network. Architecturally clean:
      every server-side model call funnels through the single `AIProvider.complete()`
      seam ([agent_graph.py:417](src/legal_mcp/agent_graph.py)), consumed only by
      `agent_query` (`structured_query` is deterministic). Full design + acceptance:
      [Docs/strategy/2026-06-08-local-model-path-v0.3.5-plan.md](Docs/strategy/2026-06-08-local-model-path-v0.3.5-plan.md).
  - [x] **A. Unlock the no-key local endpoint** — `enabled` stops keying on
        `OPENAI_API_KEY`; a configured local `ai_base_url` (+ placeholder key) counts
        as enabled. `provider_from_config` / `build_ai_provider` build a real provider
        for a keyless local endpoint instead of returning `None`/`Noop`
        ([agent_config.py:36](src/legal_mcp/agent_config.py),
        [ai_provider.py](src/legal_mcp/ai_provider.py)). *Core unblocker.*
  - [x] **B. Stable JSON on local models** — make OpenAI `response_format` json mode
        optional (`ai_json_mode: auto|on|off`, local default off); lean on the prompt
        + `_strip_code_fence` + a first-`{...}`-block fallback parser
        ([ai_provider.py:54](src/legal_mcp/ai_provider.py)).
  - [x] **C. No silent degrade to Noop** — when a local model *is* configured but the
        endpoint is unreachable, fail loud instead of silently falling back to
        `NoopAIProvider`/`{}` ([agent_graph.py:391](src/legal_mcp/agent_graph.py)),
        so "drives all features" can't quietly become "drives nothing".
  - [x] **D. Admin local presets** — provider dropdown gains `Ollama (local)` /
        `vLLM (local)` / `OpenAI-compatible (custom)`, auto-filling base_url +
        placeholder key + json_mode=off; API key optional for local
        ([admin_misc.py:97](src/legal_mcp/admin_misc.py)). All map to the one
        `openai_compatible` backend — no new provider branch.
  - [x] **E. Endpoint connectivity probe** — `doctor` (and optional startup line)
        probes the configured `ai_base_url`, distinguishing unreachable / 401 /
        missing-model / OK ([doctor.py](src/legal_mcp/doctor.py)).
  - [x] **F. Local end-to-end demo + commitment matrix** — run the existing
        legal-demo against Ollama with zero external-LLM traffic; document that
        "Q&A never leaves the network" holds **only when a local model is configured**
        ([examples/legal-demo](examples/legal-demo), [README](README.md),
        [threat-model.md](Docs/strategy/threat-model.md)).
> **Re-sequenced 2026-06-08:** the data-source work now ships **first** as v0.4.0
> (it has no identity dependency and is execution-ready); identity passthrough +
> `by_owner` ship **second** as v0.4.5. This inverts the earlier order; rationale +
> phase plan:
> [Docs/strategy/2026-06-08-v0.4.0-v0.4.5-resequencing.md](Docs/strategy/2026-06-08-v0.4.0-v0.4.5-resequencing.md).

- [x] **v0.4.0 任意数据源：接入 · 授权 · 后台可见 — DONE & tagged `v0.4.0` (a657584, local, 2026-06-08).**
      Phase 1 (A+B) + Phase 2 (C0–C6) + Phase 3 (D) + security gate all green (401 tests).
      DB permission grants are the sole authorization gate (git-YAML policy门已删).
      ↓ (ships first, no identity dependency)
      — any connected source (not just `project`/`contract`/`license`) authorized
      at **domain + field** level (row level via `none`/`by_governed_code`), driven
      by the table's actual content and managed from the admin console.
      Source-agnostic. Detailed §A/§B/§C/§D design:
      [Docs/strategy/2026-06-08-arbitrary-domain-authorization-plan.md](Docs/strategy/2026-06-08-arbitrary-domain-authorization-plan.md).
  - [x] **Phase 1 (A+B) generalized record scope + catalog-driven metadata.** ✅ `db2363d`
        Domains declare `record_scope` (`none` | `by_governed_code`; `by_owner`
        reserved → loud error until v0.4.5); remove hard-coded `project_code`
        ([connector_retrieval.py:46,56,116-132](src/legal_mcp/connector_retrieval.py)).
        Identity fields from `is_identity`; relationship-filter / `cross_domain`
        become per-domain catalog attrs, replacing the hard-coded sets
        ([query_authorization.py:14-17,57-83,135-144,259-266](src/legal_mcp/query_authorization.py)).
        *Verify:* A1 (none-mode non-project table e2e), A2 (by_governed_code +
        crown-jewel test unchanged), B1 (new domain, no code edit).
  - [x] **Phase 2 (C) admin data-source & permission UX — consolidate the legacy
        `/admin/database` page into a Data Sources view (改造合并, decided 2026-06-07).**
        Page's new purpose: show which sources are connected, what domains/**field
        names** each serves (identity/sensitive + `record_scope` mode, **never row
        values**), which permissions go to which groups/users, and add/disconnect a
        source. Remove the old per-row content table + entities + xlsx/csv import box.
        - [x] C0 inject `ConnectorSetup` into the admin server (+ `--connector` on
          `serve-admin`; defaults to the bundled SQLite demo when absent).
        - [x] C1 Data Sources view (replaced [admin_database.py](src/legal_mcp/admin_database.py)
          body) — names/metadata only, no field values; old per-row table + entities +
          aliases + xlsx import all removed.
        - [x] C2 catalog-driven permissions form + grant validation (unknown domain /
          undeclared field rejected), replacing the hard-coded dropdown
          ([admin_manage.py](src/legal_mcp/admin_manage.py)).
        - [x] C3+C4 nullable `user_id` on `permission_grants` (`group_id` nullable, exactly-one
          CHECK; schema v18→19 + resumable rebuild migration in
          [db.py](src/legal_mcp/db.py)) → per-user grant; effective grant scope
          `direct ∪ groups'` ([policy.py](src/legal_mcp/policy.py) `grant_scope_clause`, also
          honored by `describe_my_access`); admin grantee = group | user; per-user
          effective-permissions panel on the edit page; per-domain grant holders on the
          Data Sources view.
        - [x] C5-disconnect **disconnect/reconnect a declared source from the console.**
          New `data_source_state` table (schema v19→20); `db.disabled_data_sources` /
          `set_data_source_disabled`. Live-catalog filter at `_catalog_for_database`
          (`exclude_domains` on `build_query_catalog_from_connector`) → a disabled
          source's domains leave the catalog so `validate_plan` denies them
          (`unsupported_domain`), uniformly for any source. Data Sources view shows
          per-source Connected/Disconnected state + a connect/disconnect toggle
          (`/admin/data-sources/{connect,disconnect}`); unknown source rejected.
        - C5-add **enable a brand-new source** = its fields come from a reviewed
          `scaffold-connector` draft (**depends on Phase 3 §D**, lands after D).
        *Verify:* C1/C2/C3/C4, C5-disconnect (disabled source's domain denied),
        + security test: page renders no business field value anywhere.
        - [x] C6 **removed the git YAML policy gate entirely** (decided rev2). DB grants
          (console-managed, default-deny) are the SINGLE authorization path.
          (1) Self-sufficiency proven: `seed_server_db` now grants `legal_bp` to legal
          only (not business), so the crown-jewel "legal sees / business denied" outcome
          comes from DB grants alone — `test_feishu_mixed_e2e` green (business denied
          `return_field_access_denied`). (2) Deleted `policy_config.py`, the `policy`
          param + `_policy_*` helpers in query_authorization, policy threading through
          connector_retrieval/search_tools/agent_graph/tools/http_server/mcp_*, CLI
          `--policy`, `examples/policies/`, and policy-only tests (test_policy_config,
          test_live_policy_e2e + cases). (3) leakage-test extension → folded into the
          Security gate below. (4) rewrote threat-model.md + commitment-matrix.md +
          identity-model.md + SECURITY/CONTRIBUTING/README + demo docs + CHANGELOG to
          single-gate; `run_demo.py` now drives disclosure via `authorize_fields`.
  - [x] **Phase 3 (D) content-driven scaffolding.** Optional `describe_schema()` on
        the connector interface ([connectors/base.py](src/legal_mcp/connectors/base.py)
        `SourceTable`); SQLite via `PRAGMA`, Feishu via a new `list_fields` client
        endpoint. `legal-mcp scaffold-connector --app-token --table domain:tbl` emits a
        draft YAML ([scaffold.py](src/legal_mcp/scaffold.py)) — all real columns, first
        guessed `is_identity`, blank aliases, `record_scope.mode: none` — for human
        review (preserves "only declared fields are queryable"; never auto-widens).
        *Verify:* D1 — draft loads via `build_connector_setup` and serves the table
        ([test_scaffold.py](tests/test_scaffold.py)). 400 passed.
  - [x] **Security gate** — `tests/test_disclosure_leakage.py` extended to an
        arbitrary non-project domain (`staffing`, `record_scope.mode: none`, served
        through `execute_connector_plan`): over-request / denied-only / projection
        attacks all refuse and the ungranted `salary` secret never appears. Holds for
        any domain, not just the legacy legal tables. 401 passed. → ready to tag v0.4.0.
- **v0.4.5 身份穿透 + by_owner 行级范围** (ships second) — authorize by the **real
      person** asking (api-key → HTTP header / reverse proxy → OIDC minimal), then
      `by_owner` row scope ("only your own rows") consuming the passed-through
      identity. #1 technical risk (gateway plan §8.6) → its own design pass.
  - [x] **Phase 1** identity interface design + pluggable `IdentityResolver` seam. ✅ `388928d`
        `identity_resolver.py`: single-source precedence (≥2 sources → `ConflictingIdentitySources`
        → 401), `BearerTokenSource` (api-key + legacy), `AccessContext.external_subject`;
        `context=None` hardened fail-closed at all 3 gates; `identity-model.md` rewritten.
  - [x] **Phase 2** HTTP header identity from a **trusted** reverse proxy only. ✅ working tree
        `TrustedHeaderSource`: trusts the proxy **boundary** — verifies the TCP peer is a
        configured `--trusted-proxy` before accepting `--trusted-identity-header`; header value
        maps to `users.external_subject` (`--trusted-header-email-fallback` optional). Untrusted
        peer's header rejected fail-closed; `bearer/legacy + header` → conflict reject. Audit
        records the resolving source (`audit_events.identity_source`, schema v21). Header configured
        without a trusted proxy → refuses to start. (16 new tests; 430 green.)
  - [x] **Phase 3** OIDC/OAuth minimal — **DEFERRED to v0.5** (decision = the
        deliverable). Not "nearly free": the core is zero-runtime-dependency and OIDC
        needs the first runtime dep (`PyJWT[crypto]`) or a browser redirect flow. The
        resolver seam already accepts a future OIDC source with no change to `by_owner`
        or the header trust model, so deferring costs nothing structurally. Not in the
        tag gate.
  - [x] **Phase 4** `record_scope: by_owner` — closes the item reserved in v0.4.0. ✅ working tree
        `RecordScope.subject` (external_subject/email/user_id); own-scope resolver
        `record_owner_subject` whose no-subject result is the **empty set** (NOT the
        `None=all` sentinel — legacy/anon/unrestricted-unmapped → zero rows); owner
        equality **pushed down** (overrides client filter, fixes the false-empty) +
        defense-in-depth post-filter; owner field stripped from projection unless
        requested; leakage test covers a by_owner domain (peer's value never leaks).
        13 new tests; 443 green.
- **v0.4.6 turn-safe agent queries** (LangGraph query redesign) — fixes the
      2026-06-08 dogfood false-negative: a later turn ran the earlier turn's plan.
      See [Docs/strategy/2026-06-08-v0.4.6-langgraph-query-redesign.md](Docs/strategy/2026-06-08-v0.4.6-langgraph-query-redesign.md).
  - [x] **§A** removed the global business fast path (project-code/field grammar);
        only the schema-independent access-scope fast path stays deterministic.
  - [x] **§B/§C** per-turn `turn_id` keys the LangGraph checkpoint (client
        `thread_id` stays the conversation id); natural-language classification gated
        on `input_mode == "structured"`, not "a plan exists"; `start_turn` clears
        leaked turn-local state as a second guardrail.
  - [x] **§D** `load_conversation_context`/`record_turn_context` nodes + new
        `agent_turn_context` table — safe entity context as planner *input*, written
        only for a successful non-empty turn (a failed/empty turn is never promoted).
  - [x] **§E** typed turn lifecycle: `start_turn → load_context → classify_intent →
        plan_query → validate_plan → authorize_plan → execute_plan → format_answer →
        record_context` (classification split from planning; placeholder node removed).
  - [x] **§F** `agent_steps` turn-keyed: `unique(thread_id, turn_id, step_index)`,
        integrity errors re-raised not swallowed (schema v22, rebuild migration).
  - [x] **§G** authorized empty result tagged `diagnostic: no_rows` in server audit
        (distinct from a denial); never exposes field values to the client.
  - 8 regression tests (`test_agent_turn_isolation.py`) + context/reset unit tests;
        full suite green (one pre-existing, unrelated `DROP COLUMN` env failure).
- **v0.5 自助数据源接入 + planner 检索增强** — 重定义（2026-06-13，决策见
      [Docs/strategy/2026-06-13-v0.5-plan.md](Docs/strategy/2026-06-13-v0.5-plan.md)）。
      OIDC 自研**移除**（改为文档化「前置 OIDC 反代 → 可信头 → 现有 `TrustedHeaderSource`」）。
  - **柱一 控制台一键接入框架**：源配置从静态 YAML → DB 持久化运行时注册表
        （`data_sources`，schema v23）+ 后台「添加数据源」向导（选类型→连接→`describe_schema` 自省
        →人工 review 字段/identity/record_scope→启用）+ 凭据存储（默认 env 引用）+ 安全护栏
        （仅 admin、默认拒、接入即审计、泄漏红队测试覆盖向导接入源）。
  - **柱二 连接器扩展**：`local_file` 本地文件连接器（CSV/XLSX/JSON/Markdown frontmatter，
        落临时 SQLite 复用算子）+ ≥1 个在线样板源（待定：腾讯文档智能表格/钉钉/Notion/Airtable）+
        连接器作者指南（兑现原 contributor surface）。
  - **柱三 planner 检索增强**：零结果 `identity` 改写修 `eq` 假空（执行层确定性、留审计）、
        catalog 字段语义增强（`field_semantics` 表）、**字段语义召回词生成 + 提示词治理**（接入期按字段名
        用 `AIProvider` 生成语义召回词、可审可治、不放宽授权——2026-06-13 新增）、`is_empty`/`date_*` 算子补全、
        空结果可解释。
  - **范围**：在线+本地结构化/半结构化表格。PDF/Word 正文 + 「权限模型能否治理 RAG 向量检索」
        = **v0.6+ 明确探索项**（计划 §7）。
  - **逐版本开发计划**（每事项一版，v0.5.0→v0.5.10）见
        [Docs/strategy/2026-06-13-v0.5-version-plan.md](Docs/strategy/2026-06-13-v0.5-version-plan.md)。
- **v1.0** stable: secure-by-default, readable/testable policy, stable connector API.

---

# FROZEN — pre-pivot platform backlog (kept for history, NOT the roadmap)

The items below were written under the abandoned "defensible legal-data platform"
goal. They are frozen, not active. Several (GUI data entry, import-to-own, MDM,
multi-source ingestion connectors) are now explicit non-goals. They are retained
only so the project's history and rationale stay legible; do not pick them up
without tracing to the north-star scenario first.

## [FROZEN] GUI upload and edit entrypoint

**What:** Add a Web GUI for uploading, reviewing, and editing legal project data.

**Why:** Non-technical legal users cannot maintain production data through CSV/XLSX files and command-line imports forever.

**Pros:** Lowers adoption friction, enables team usage, and creates a cleaner path for future upload/edit workflows.

**Cons:** Adds authentication, edit conflict handling, form validation, deployment, and support complexity.

**Context:** v1 intentionally defers the Web UI. The first product proof is a local stdio MCP server backed by canonical SQLite, with CSV/XLSX import handled through a shared validation pipeline. When the GUI is built, it must reuse the same validation/import/upsert layer instead of creating a second data path.

**Depends on / blocked by:** Complete v1 MCP query validation with real legal project data and confirm users get value from AI desktop clients querying the context.

## [FROZEN] Docker and intranet HTTP deployment

**What:** Add Docker packaging and an HTTP-based intranet deployment mode.

**Why:** Once the tool moves from individual local trial to team usage, a local stdio process is not enough for centralized data, rollout, and operations.

**Pros:** Enables shared deployment, shared database access, centralized upgrades, and enterprise-friendly operations.

**Cons:** Adds HTTP transport, authentication, Origin validation, port management, service monitoring, and deployment support.

**Context:** v1 uses local stdio MCP plus one-line install to validate the core value quickly across desktop AI clients. Docker/HTTP is the natural next step for team deployment, but including it in v1 would slow down the first proof.

**v1.1 status:** Selected as the next implementation milestone after successful real-project MCP query validation. *(Shipped; HTTP transport is reused by the gateway.)*

**Depends on / blocked by:** v1 runs successfully for at least one or two real local users, and the team confirms shared legal context data is needed.

## [FROZEN] OCR, PDF, and historical contract extraction

**What:** Add OCR/PDF parsing and automated extraction for historical contracts and legal risk memos.

**Why:** Long-term value depends on turning existing legal archives into queryable context instead of relying forever on manual or AI-assisted CSV preparation.

**Pros:** Reduces data entry cost, scales context coverage, and moves the product closer to the long-term context engine vision.

**Cons:** Adds document parsing, Chinese OCR, contract field extraction, source citation, confidence handling, and human review complexity.

**Context:** v1 is limited to structured CSV/XLSX import and MCP querying. Automated extraction should wait until the field model and real query patterns are validated with a small set of real projects. *(Now a non-goal: the gateway does not own or extract data.)*

**Depends on / blocked by:** Import three to five real legal projects in v1 and learn which fields are actually used by AI clients and legal users.

## [FROZEN] OA, Feishu, and CLM connectors

**What:** Add connectors for OA, Feishu, CLM, or other enterprise legal data systems.

**Why:** Enterprise legal context ultimately lives across multiple systems, and the product cannot rely on manual exports forever.

**Pros:** Keeps context fresher, reduces manual data movement, and moves closer to the real enterprise workflow.

**Cons:** Each connector adds permissions, field mapping, API limits, sync failures, audit boundaries, and vendor-specific support load.

**Context:** v1 does not replace existing systems. *(Re-scoped: read-through connectors are now the core architecture — but as a pluggable interface with only 1–2 officially maintained connectors, not an ingestion platform. See plan §6 阶段3 and v0.3.)*

**Depends on / blocked by:** v1 real usage clarifies the next highest-value data source: OA, Feishu sheets, CLM, or shared file storage.

## [FROZEN] Enterprise permissions and audit console

**What:** Add enterprise-grade permissions, multi-user access control, and an audit console.

**Why:** Legal data is sensitive. Team deployments need clear control over who can query which data, who imported or edited records, and what each AI client accessed.

**Pros:** Supports enterprise procurement, security review, internal compliance, and responsibility tracking.

**Cons:** Adds user accounts, roles, permission boundaries, audit UI, log retention policy, and operational support.

**v1.2 status:** Implemented local users, per-user API keys, project-level
access grants, DB-backed disclosure audit, and a lightweight Admin Web UI for
user, grant, key, and audit review workflows. *(This is the reused kernel — see
the new roadmap; field-level disclosure + audit is now the product's core, not a
backlog item.)*

**Post-v1.2:** Field-level permissions and SSO remain future enterprise work.
