# 威胁模型（一页纸，v0.2）

> 配套：权限感知 MCP 网关计划 §12。v0.5 的安全模型在此基础上展开，而非从零写。
> 范围：v0.2 的合成数据演示 + 最小真实身份路径。真实飞书源 / 身份穿透属 v0.3+。

## 一句话

**网关代码的正确性，就是安全边界本身。** 网关代表用户向数据源取数，并在数据到达 LLM 之前裁剪到该用户有权看到的字段。过滤逻辑出 bug，等同于越权泄漏。

## 信任边界

```
  用户 / MCP 客户端
        │  （每用户 API key 标识身份）
        ▼
 ┌─────────────────────────────────────────────┐
 │  Legal-MCP 网关（信任边界在这里）             │
 │   1. 识别身份      → AccessContext            │
 │   2. 规划查询      → QueryPlan                │
 │   3. 授权（DB grant，唯一门） → 字段+行级范围 │
 │   4. 取数          → 连接器只取被放行的字段   │
 │   5. 审计          → 每次披露决策落库         │
 └─────────────────────────────────────────────┘
        │  仅放行字段
        ▼
   LLM 上下文 / 数据源（连接器）
```

- **边界内可信**：授权 + 过滤逻辑。这部分的正确性是安全本身。
- **边界外不可信**：LLM 上下文、客户端、最终回答。任何进入 LLM 的内容都视同可能外泄。

## 头号不变式（§12.4）

> **未授权字段的值，在 planning、检索、合成的任一阶段都不得进入 LLM 上下文。**

实现要点：授权作用在 **query plan** 上（`query_authorization.py`）。被拒字段在取数前就从 plan 剥离，连接器根本不取它的值。网关可以把「**为什么被拒**」的 reason 交给 LLM，但**绝不交值**。由 §6 阶段2 的永久泄漏红队测试（`tests/test_disclosure_leakage.py`）作为不可回退的 CI gate 守护。

## 唯一授权门：DB 权限授予

字段被放行，必须通过**唯一一道门**（deny 优先、默认拒绝、fail-closed）：

- **DB 权限授予**（`permission_grants`，运行时、可按项目/用户组/用户）——谁被授予了什么，由控制台管理、`authorize_fields` 强制（v0.4.0 §C）。

身份/关系导航字段豁免字段门（用户引用记录所需的把手）。例外：`admin`（运营者）豁免字段门——它是网关运营者而非普通用户，行为本身是高价值审计对象；`legacy_shared_token` **默认不豁免**（无 grant → 字段/行门一律拒绝，fail-closed），仅当部署显式开启 `--legacy-token-full-access` 迁移逃生口时才放行全部。未识别请求由网络层在边界 401 拒绝（不进入授权）。「看全部」是一个显式能力 `unrestricted`（`admin` / 本地 stdio operator / opt-in legacy），不再从缺省上下文推断（v0.4.5 preflight）。

> **取舍（v0.4.0 §C C6，已决策 rev2）**：移除了曾经并行的 git YAML policy 门。控制台 DB grant（本就默认拒）是**唯一**授权路径——一个休眠的可选文件门既是攻击面又是认知负担，非技术用户也无法对其推理。代价：DB-grant 的正确性即是整个边界（没有第二道兜底），因此**泄漏红队测试是唯一守门人**，DB 完整性 + 审计是信任根。详见 `identity-model.md`。

## 主要威胁与应对

| 威胁 | 后果 | 应对 |
| --- | --- | --- |
| 网关被攻破 / 服务账号凭据泄漏 | 攻击者得到「网关能取的全部数据」 | 网关即一个新的高权限服务账号——最小权限、凭据隔离、审计全量；列为 v0.5 重点 |
| 过滤逻辑出 bug | 越权字段进入 LLM / 回答 | 头号不变式 + 永久泄漏红队测试；授权落在 plan 上、被拒字段不取值；DB grant 默认拒 |
| LLM 被诱导越权（prompt 注入） | 模型尝试请求未授权字段 | 授权在网关内、与模型无关；模型至多拿到 reason，拿不到值 |
| catalog 暴露字段存在性 | 「该字段存在」本身是隐私信号 | 已记录为待决策（§12.5）：按角色裁剪 catalog vs 统一暴露但拒值 |
| 身份伪造 / 凭据共享 | 张冠李戴的披露 | 每用户 API key；`legacy_shared_token` 默认 fail-closed（不披露），全访问仅在 `--legacy-token-full-access` 迁移逃生口下开启，应尽快淘汰 |
| 伪造可信身份头（绕过反代直连网关注入 `X-Legal-MCP-User`） | 冒充任意用户 | 信任的是**代理边界**而非原始头：可信头身份源校验 TCP peer 必须是配置的可信代理（`--trusted-proxy`），非可信 peer 的头一律 fail-closed 拒绝；「bearer + 头」「legacy + 头」均触发冲突拒绝，绝不静默择一 |
| 运行时接入源凭据落库（v0.5.6–v0.5.8 `data_sources`） | DB 泄漏即源凭据泄漏 | **默认 env 引用**：`secret_ref` 只存环境变量名，明文凭据不入库——DB 泄漏不等于凭据泄漏；加密入库为显式 opt-in（需部署主密钥），尚未实装（v0.5.8 决策：保持 env 引用默认，加密延后）。新接入源默认 `record_scope:none` + 零 grant = 默认拒；仅 admin 可增删改、全程入审计 |

## v0.2 现状与缺口

- ✅ 授权落在 query plan；被拒字段取数前剥离。
- ✅ 唯一门：DB grant（`authorize_plan` 活路径强制）；`admin` 豁免、`legacy_shared_token` 默认 fail-closed（opt-in 才全访问）、未识别请求边界 401。
- ✅ 每次披露决策可审计（`audit_disclosures` + `agent_runs.error_code`）。
- ✅ 控制台（DB grant，默认拒）是唯一授权路径；曾经的 git YAML policy 门已彻底移除（v0.4.0 §C C6），不再有 `--policy` 参数或 `examples/policies/`。
- ✅ `legacy_shared_token` 默认已 fail-closed（v0.4.5 preflight）；全访问后门仅在显式 `--legacy-token-full-access` opt-in 下存在，迁移期产物，应尽快淘汰。
- ✅ `context=None` 的最后一处 fall-through 已收紧为 fail-closed（v0.4.5 Phase 1）：`can_query_content` / `visible_project_ids` / `authorize_fields` 对 `None` 上下文一律拒绝（拒/空集/全拒）。所有入口都经身份解析 seam（网络）或显式 `local_operator()`（stdio）铸造一个具名上下文，「看全部」收敛为唯一显式能力 `unrestricted`——一个误传的 `None` 现在只会拒绝披露，不再泄漏。
- ✅ 身份解析 seam（`identity_resolver.py`，v0.4.5 Phase 1）：一次请求只经**唯一**一个被接受的身份源认证；出现多个身份源（含 legacy 共享令牌）即拒（`ConflictingIdentitySources` → 401），不静默择一。
- ✅ 可信反代头身份源（`TrustedHeaderSource`，v0.4.5 Phase 2）：信任代理边界而非原始头——校验 TCP peer 是配置的可信代理才接受头，头值映射 `users.external_subject`（email 回退默认关），非可信 peer 的头 fail-closed 拒绝。`is_present` 与 peer 信任无关，故「bearer/legacy + 伪造头」必触发冲突拒绝、单独伪造头在 resolve 拒绝，两条路径都不静默放行。审计落库**由哪个源**解析（`audit_events.identity_source`：bearer_token / legacy / trusted_header / local），不存令牌。头配了但无可信代理 → 启动报错（避免静默全拒）。
- ✅ `record_scope: by_owner`「只看自己的行」（v0.4.5 Phase 4）：归属主体由独立解析器得出，无主体即**空集**（绝不复用 `None=全部` 哨兵——legacy/匿名/未映射统统零行）；归属等值**下推**并覆盖客户端过滤（查不了别人的行），修掉先 limit 后过滤的假空；纵深防御 post-filter 丢弃宽连接器返回的非本人行；归属列除非显式请求否则从响应剥离。泄漏红队测试已覆盖一个 by_owner 域，证明同事的行值永不进入上下文。
- ⏭️ 真实数据源、本地模型属 v0.3+；OIDC 推迟到 v0.5（Phase 3 决定）。
