# 身份模型（一页纸，v0.4.5 Phase 1–2）

> 配套：权限感知 MCP 网关计划 §12、§5.2 第 13 项。与 `threat-model.md` 互读。
> 目标：落地一个**最小但真实**的多用户身份路径，让「同一问题、不同用户、不同字段」不只靠 demo 假数据，而是由身份 + 授权真正决定。
> v0.4.5 Phase 1 把原先散在 `http_server` 里的一段身份分支提升为一个可插拔的**身份解析 seam**（`identity_resolver.py`），并把 `context=None` 的最后一处 fall-through 收紧为 fail-closed。
> v0.4.5 Phase 2 在同一 seam 注册第二个身份源：可信反向代理注入的 HTTP 头（`TrustedHeaderSource`），信任代理边界而非原始头。

## 身份从哪来：AccessContext

所有授权都围绕一个 `AccessContext`（`policy.py`）：

| 字段 | 含义 |
| --- | --- |
| `user_id` | 本地用户主键（`None` = 未识别 / 遗留共享令牌） |
| `role` | `admin` / `legal` / `business` / `auditor`（`identity.py`） |
| `email` | 展示用 |
| `api_key_id` | 命中的每用户 API key（审计追溯到具体密钥） |
| `legacy_shared_token` | 迁移期共享令牌；默认 fail-closed，全访问仅 `--legacy-token-full-access` opt-in，应尽快淘汰 |
| `unrestricted` | 显式「看全部」能力（`admin` / 本地 stdio operator / opt-in legacy）；从不由缺省上下文推断（v0.4.5 preflight） |
| `external_subject` | 可信上游的联邦主体（`users.external_subject`）；可信反代头（Phase 2）按它把头值映射到本地用户，也是 `record_scope: by_owner`（Phase 4）的行级归属主体——外部 SaaS 表按它判断「这行是谁的」，而非本地 `user_id` |
| `identity_source` | 由哪个身份源解析出本上下文（`bearer_token` / `legacy` / `trusted_header` / `local`），落入审计 `audit_events.identity_source`，让审计者区分 api-key 披露与可信代理披露；是源**标签**，绝非令牌（Phase 2） |

**最小真实路径**：HTTP MCP 服务 + 每用户 API key。客户端带自己的 key → 网关验证 → 构造该用户的 `AccessContext`。身份不是参数捏造，而是经密钥验证得来。

## 身份从哪解析：IdentityResolver seam（v0.4.5 Phase 1）

一次请求如何变成一个 `AccessContext`，统一收口到一个窄接口 `identity_resolver.py`。设计目的是**后续阶段靠「注册一个身份源」扩展，而非改网关**：

| # | 身份源 | 信任假设 | 落地 |
| --- | --- | --- | --- |
| 1 | 每用户 API key（`Authorization: Bearer <key>`） | 密钥即持有者；`identity.verify_api_key` 校验 active 用户/密钥 | ✅ Phase 1：`BearerTokenSource`（同时处理 legacy 共享令牌） |
| 2 | 可信反向代理注入的 HTTP 头（如 `X-Legal-MCP-User`） | 信任的是**代理边界**而非原始头；代理已认证真人，且 TCP peer 必须是配置的可信代理（`--trusted-proxy`） | ✅ Phase 2：`TrustedHeaderSource`——校验 peer 是可信代理才接受头，头值映射 `external_subject`（`--trusted-header-email-fallback` 可选回退 email），非可信 peer 的头 fail-closed 拒绝 |
| 3 | OIDC/OAuth（最小） | 同上游 IdP 的信任 | ⏭️ **v0.4.5 Phase 3 决定推迟到 v0.5**：核心包零运行时依赖，OIDC 非「近乎免费」（需首个运行时依赖 PyJWT[crypto] 或浏览器跳转流）。seam 已可无改动接入未来的 OIDC 源，推迟无结构成本 |

**精度由构造保证——一次请求只经唯一一个被接受的身份源认证**：seam 先对所有源做 presence 检测，

- 0 个源 → `None`（未认证 → 401），且不触库；
- ≥2 个源 → `ConflictingIdentitySources`（拒 → 401），**在任何解析/触库之前**就拒，绝不静默择一。legacy 共享令牌**算作一个身份源**，所以「bearer + 可信头」是冲突而非优先级问题；
- 恰好 1 个源 → 该源 `resolve`（必要时才触库）。

这是 preflight 结构性修复的另一半：**没有任何路径能让两个凭据悄悄收敛成一个身份**。`身份穿透` 的边界（见下）也由此明确——seam 在网关内解析出真人身份并喂给网关自己的授权模型，连接器对数据源始终以**单一共享服务凭据**自证身份，不冒充最终用户。

## 授权怎么组合：行级 × 字段级 × 双门

一次披露要回答两个独立问题，再叠加两道门：

### 1. 行级：能看到「哪些记录」（record scope）

`visible_project_ids(conn, context)`：

- `admin` → 全部项目；
- `legal` / `business` → `project_access` 显式授权的项目；
- 其它 / 无 → 空集（默认看不到）；
- `legacy_shared_token` → 默认空集（fail-closed）；仅 `--legacy-token-full-access` opt-in 时 → 全部；
- `context=None` → **空集（fail-closed，v0.4.5 Phase 1）**。`None` 已不再表示「看全部」；全访问是显式的 `unrestricted` 能力（`admin` / 本地 operator / opt-in legacy）。一个误传的 `None` 只会拒绝披露。

行级范围由 DB grant 的 `project_id`（`NULL` = 域内全局）+ `project_access` 决定，与字段授权解耦。

**`record_scope: by_owner`（v0.4.5 Phase 4）——「只看自己的行」**：域声明 `{mode: by_owner, field: <源的归属列>, subject: external_subject|email|user_id}`。归属主体由**独立**的解析器 `policy.record_owner_subject` 得出，其「无主体」结果是**空集**，**绝不**复用 `visible_project_ids` 的 `None=全部` 哨兵——legacy / 匿名 / `unrestricted` 但未映射，统统零行（这是本阶段头号防线：误走 `None=all` 会泄漏每个人的行）。归属等值被**下推**进连接器查询（覆盖客户端在归属列上的任何过滤，使「查别人的行」不可能），让源先过滤再分页，修掉 `by_governed_code` 那种「先 limit 后过滤→假空」的缺陷；网关侧再做一道纵深防御 post-filter，丢弃宽权限连接器返回的非本人行。归属列只为这道校验而取，除非显式请求且字段获授，否则从响应里剥离。`by_governed_code` 的集合成员（`in (...)`）无法走等值下推，仍是**有记录在案的** post-filter 回退（其 limit-before-filter 假空是已知边界）。

### 2. 字段级：在可见记录里能看「哪些字段」

字段放行须通过**唯一一道门**（deny 优先、默认拒绝、fail-closed）：

| 门 | 来源 | 角色 | 特点 |
| --- | --- | --- | --- |
| DB grant | `permission_grants`（按用户组/用户、操作、域、字段、项目） | 控制台管理、运行时可变 | 细粒度、可临时授予；`NULL` 字段 = 域内全字段 |

- 身份字段（如 `project_code`、`name`）与跨域关系导航过滤字段豁免字段门。
- 默认拒：未被授予的字段一律拒绝。例：`business` 未被授予 `legal_bp`，请求该字段即被拒（`return_field_access_denied`），且 reason 入审计。
- **`admin` 运营者豁免字段门**：`authorize_fields` 直接通过，无需 grant。`legacy_shared_token` **默认不豁免**（无 grant → fail-closed），全访问仅 `--legacy-token-full-access` opt-in。「看全部」收敛为唯一的显式能力 `unrestricted`（`admin` / 本地 operator / opt-in legacy）。
- v0.4.0 §C C6 起，曾经并行的 git YAML policy 门已彻底移除：控制台 DB grant 是唯一授权路径，不再有 `--policy` / `examples/policies/`。

接入点：`query_authorization.authorize_query_plan(conn, plan, ctx)`，经 `agent_graph` 的 `authorize_plan` 节点串到 `run_agent_query` / `run_structured_query`。

## admin / auditor 的特殊性

- `admin`：豁免字段门（`authorize_fields` 直接通过）。是「网关运营者」，不是普通用户；其行为本身是高价值审计对象。
- `auditor`：读审计轨迹，而非业务敏感内容；无字段 grant + 空行级范围，财务/实体字段默认拒。

## 风险与演进

- **`legacy_shared_token` 默认 fail-closed**（v0.4.5 preflight）：全访问后门仅在显式 `--legacy-token-full-access` opt-in 下存在，迁移期产物，持续盯防（头号风险）。
- **`context=None` 已收紧为 fail-closed**（v0.4.5 Phase 1）：所有入口经 resolver seam（网络）或显式 `local_operator()`（stdio）铸造具名上下文后，三道门（`can_query_content` / `visible_project_ids` / `authorize_fields`）对 `None` 一律拒绝，最后的 fall-through 已消除。
- **可信反代头边界（v0.4.5 Phase 2）**：可信头身份源信任的是**代理边界**——校验 TCP peer 是配置的可信代理（`--trusted-proxy`）才接受 `--trusted-identity-header`，否则视为伪造 fail-closed 拒绝。`is_present` 与 peer 信任无关：故「bearer/legacy + 伪造头」触发冲突拒绝、单独伪造头在 resolve 拒绝，两条路径都不静默放行。审计记录解析源（`identity_source`）而非令牌。盯防点：可信代理列表配置错误（把不可信网段纳入）会重新打开伪造面——peer 列表应最小化（理想是 sidecar 的 `127.0.0.1`）。
- **身份穿透（v0.4.5）**：在本网关里，「身份穿透」= 从可信上游解析出真人身份、用于**网关自己的**授权模型（`AccessContext` + record scope），并不等于把最终用户身份委派给 Feishu/SQLite 让数据源自行授权。连接器对每个源保持**单一共享服务凭据**：网关以自身身份向数据源自证，最终用户始终记录在网关自己的审计轨迹里。源侧的逐用户委派会改变连接器凭据模型、源权限语义与审计归属，属 v0.5+ 重写，不在 v0.4.5。
- **catalog 可见性（§12.5）**：是否让用户知道无权字段「存在」是独立隐私决策，待定。

## SSO：前置 OIDC 反代 → 可信头（v0.5 定稿）

**OIDC 不自研**——登录由成熟、可审计的反代（oauth2-proxy / Authelia / nginx-oidc）完成，认证后把已验证身份作为**可信头**传给网关，复用既有 `TrustedHeaderSource`（v0.4.5 Phase 2）。流程：浏览器 → OIDC 反代（与 IdP 完成 OIDC）→ 注入 `X-Legal-MCP-User: <subject>` → `serve-http --trusted-identity-header --trusted-proxy <代理IP>`。网关信任的是**代理边界**（TCP peer 必须是配置的可信代理）而非头本身，故直连伪造头被 fail-closed 拒绝；配了头但无可信代理则启动报错。SSO 只解决「你是谁」，「能看什么」仍由网关 grant 决定（用户须存在且 `external_subject` = 反代下发值）。部署样例与步骤见 `Docs/sso-reverse-proxy.md`。**内置 OIDC 登录流明确不在路线图。**
