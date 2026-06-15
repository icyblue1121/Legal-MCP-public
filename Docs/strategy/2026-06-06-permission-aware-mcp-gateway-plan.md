# Permission-Aware MCP Gateway 转向执行计划

> 生成日期：2026-06-06
> 状态：待执行
> 来源：基于 `2026-06-06-open-source-pivot.md` 的正式落地计划
> 目标读者：项目维护者、后续参与开发的 AI agent、潜在开源贡献者
> 修订：2026-06-06（rev 2，经一次代码核查后补强）。新增 §12 身份/信任/威胁模型、§13 开源发布必备件与仓库卫生、§14 版本号与命名；并在 §5–§9 就近补入行级授权、泄漏红队测试、连接器接口预留、本地模型路线、身份风险与 LICENSE 等动作。

---

## 0. 一句话决策

Legal-MCP 不再继续作为「本地 SQLite 法务数据平台」演进，而转向一个开源、自托管、权限感知的 MCP 问答网关。

新方向的核心不是重新保存企业数据，而是坐在 AI 与现有数据源之间，回答受权限约束的小问题，并审计每次披露。

**北极星场景：**

> 小事别问我，问 AI；AI 只回答你有权知道的那一部分。

---

## 1. 本计划的执行原则

### 1.1 先接管，后重构

旧代码库不清空、不重开、不马上大删。当前项目里已经存在可复用内核，包括 `agent_query`、服务端 query planning、字段级授权、身份识别、披露审计、HTTP/stdio MCP transport。这些是新方向的核心资产。

本计划的第一目标是改变项目叙事和验收标准，第二目标才是逐步拆掉旧平台化包袱。

### 1.2 先冻结，后删除

旧路线中的平台化模块先标记为 legacy 或 demo，不立即删除。只有当新架构有等价或更清晰的路径后，才删除旧代码。

### 1.3 先做一个锋利样板，再抽象通用框架

项目愿景是通用的，但第一个可演示版本仍使用法务场景作为 reference domain。法务样板证明价值后，再把数据源、domain、policy 进一步抽象。

### 1.4 不承诺绝对数据不外流

项目可以承诺「原始数据不需要搬到本系统」和「AI 只能看到授权后的最小披露结果」。如果外部 AI client 收到答案，答案本身可能进入外部模型上下文。只有部署本地或内网模型时，才可以承诺回答生成也不出内网。

---

## 2. 新产品定义

### 2.1 项目是什么

一个开源、自托管的 permission-aware MCP gateway：

1. 识别正在提问的终端用户。
2. 接收自然语言小问题。
3. 将问题转成受约束的 query plan。
4. 根据用户身份、数据 domain、记录范围、字段权限进行授权。
5. 通过连接器 read-through 查询现有数据源。
6. 只向 AI/client 披露授权字段。
7. 记录每一次允许、拒绝和实际披露。

### 2.2 项目不是什么

项目明确不做以下事情：

1. 不做法务数据库。
2. 不做业务数据录入系统。
3. 不做合同管理系统。
4. 不做企业主数据平台。
5. 不做 MDM、主体合并、别名治理平台。
6. 不做通用 BI 或报表平台。
7. 不做文档知识库替代品。
8. 不做所有数据源的官方连接器维护者。
9. 不以 SQLite 事实库作为长期 canonical business data store。

### 2.3 法务的新定位

法务从「产品边界」改为「旗舰样板」。

保留 legal reference domain，用它展示：

1. 项目联系人是谁。
2. 项目关联主体是谁。
3. 这件事该找谁办。
4. 当前用户能看哪些项目。
5. 同一个问题对不同用户返回不同字段。
6. 审计里能看到每次披露。

---

## 3. 旧工作区处理方案

### 3.1 Git 策略

推荐新建分支：

```sh
git switch -c codex/open-source-pivot
```

如果希望旧工作区完全不受影响，可以创建 worktree：

```sh
git worktree add ../Legal-MCP-open-pivot -b codex/open-source-pivot
```

默认执行方案使用同一个 repo 的新分支。原因是当前转向需要复用大量核心代码，新 repo 会增加迁移成本。

### 3.2 旧文档处理

| 文件 | 新处理方式 |
| --- | --- |
| `README.md` | 全面重写第一屏和产品叙事，保留必要安装说明 |
| `Docs/strategy/2026-06-06-open-source-pivot.md` | 保留为战略来源，不改为执行计划 |
| `Docs/strategy/2026-06-06-permission-aware-mcp-gateway-plan.md` | 本计划，作为后续执行总纲 |
| `Docs/v1-implementation-plan.md` | 标记为 legacy local SQLite legal-data plan |
| `TODOS.md` | 拆成 legacy backlog 与 new roadmap |
| `Docs/team-deployment.md` | 后续改写为自托管 gateway 部署文档 |
| `Docs/client-setup.md` | 保留，但改成外部 AI client 接入 gateway 的说明 |
| `Docs/agent-observability.md` | 保留，纳入 audit/observability 核心文档 |

### 3.3 旧代码处理

| 模块 | 新定位 | 处理动作 |
| --- | --- | --- |
| `agent_query` / `agent_graph.py` | 核心自然语言入口 | 保留并强化 |
| `query_plan.py` | 受约束 query plan | 保留并逐步 domain-agnostic |
| `query_authorization.py` | 核心授权边界 | 保留并强化 |
| `query_catalog.py` | 字段目录 | 改造为 connector-provided catalog |
| `agent_fast_path.py` | 确定性快路径 planner（机制属 core，词表属 demo） | 机制保留并 domain-agnostic；法务词表 / `trademark_right` 等规则下沉到 legal demo connector 的 `fast_intents()`。它不碰 DB、不绕授权，不是治理隐患 |
| `policy.py` | 核心策略引擎 | 改造为声明式 policy |
| `identity.py` | 核心身份识别 | 保留并支持更多上游身份 |
| `audit.py` | 核心事件审计 | 保留 |
| `disclosure_audit.py` | 核心披露审计 | 保留并强化 |
| `mcp_server.py` | MCP transport 入口 | 保留，默认只暴露 `agent_query` |
| `http_server.py` | 自托管 HTTP 服务 | 保留，调整定位 |
| `schema.sql` | 当前混合事实表和治理表 | 拆分为 governance schema 与 demo schema |
| `db.py` | 当前 SQLite 主库访问 | 改造为 governance DB + demo source helper |
| `tools_project.py` | 法务/项目领域工具 | 降级为 legal demo connector 内部能力 |
| `tools_contract.py` | 法务/合同领域工具 | 降级为 legal demo connector 内部能力 |
| `tools_license.py` | 法务/证照领域工具 | 降级为 legal demo connector 内部能力 |
| `import_pipeline/` | import-to-own 平台路线 | 标记为 legacy/demo，不作为主路径 |
| `admin_*` | 当前混合业务后台 | 改为用户、策略、审计、连接器配置后台 |
| `setup_wizard.py` | 客户端配置入口 | 保留，文案改为 gateway setup |
| `doctor.py` | 环境检查 | 保留，检查项改为 policy/connector/audit |

---

## 4. 目标架构

```text
External AI Client
        |
        v
MCP Server
        |
        v
agent_query
        |
        v
Identity Resolver
        |
        v
Policy Engine
        |
        v
Query Planner
        |
        v
Connector Catalog
        |
        v
Read-through Connector
        |
        v
Existing Data Source
```

### 4.1 服务端只持有的内容

1. users
2. groups
3. identities
4. policies
5. connector configs
6. audit events
7. audit disclosures
8. agent runs
9. planning traces
10. demo data

### 4.2 服务端不长期持有的内容

1. 项目事实数据。
2. 合同事实数据。
3. 证照事实数据。
4. 员工事实数据。
5. 供应商事实数据。
6. 客户事实数据。
7. 企业主体主数据。

这些内容由现有数据源负责，本项目通过 connector read-through 获取。

---

## 5. 第一个新版本范围

### 5.1 推荐版本名

推荐使用：

```text
v0.2 Open Governance Pivot
```

如果希望表达这是一个重大方向变化，也可以使用：

```text
v2.0 Permission-Aware MCP Gateway
```

当前建议使用 v0.2。原因是项目仍处于开源定位重塑期，低版本号更符合外部预期。

> 注意：当前 `pyproject.toml` 版本是 `0.1.0`，但 README / git 里程碑用的是 v1.3–v1.5.2。v0.2 对 pyproject（0.1.0 → 0.2.0）是顺的，却与团队的里程碑命名冲突，读者会困惑「README 讲 v1.5.2 功能、包却是 0.2」。发布前必须统一一套版本方案，并在首次公开发布前定好 PyPI 包名（首发包名有黏性）。详见 §14。

### 5.2 v0.2 必须完成

1. README 第一屏改为 permission-aware MCP gateway。
2. README 明确 non-goals。
3. MCP production catalog 默认只暴露 `agent_query`。
4. SQLite legal data 改为 reference demo source，不再作为产品主数据叙事。
5. 新增 connector interface 的最小形态。
6. 新增或改造一个 SQLite demo connector。
7. policy 支持字段级 allow/deny。
8. `agent_query` 在 demo connector 上完成 read-through 查询。
9. 越权字段不披露。
10. 每次允许、拒绝、披露写入 audit。
11. 加入开源发布必备件：LICENSE（用 GitHub「Add license」生成，推荐 Apache-2.0，含专利授权）、CONTRIBUTING（把 non-goals 钉进去）、SECURITY.md（漏洞披露渠道）。详见 §13。
12. 产出一页 `Docs/threat-model.md`：写清信任边界与「网关即新服务账号」的风险。详见 §12。
13. 至少落地一个**最小但真实**的多用户身份路径（如 HTTP + 每用户 API key），不能只靠 demo 假数据演示「同问题不同字段」。详见 §12。

### 5.3 v0.2 不做

1. 不接飞书真实 API。
2. 不改项目名。
3. 不删除全部 import pipeline。
4. 不重写 admin UI。
5. 不做多连接器 marketplace。
6. 不做复杂 policy UI。
7. 不做主体 MDM。
8. 不做企业 SSO 的完整实现。

---

## 6. 迁移阶段

### 阶段 1：战略接管

**目标：** 让项目叙事、文档和路线从旧平台路线切换到新 gateway 路线。

**修改文件：**

1. `README.md`
2. `TODOS.md`
3. `Docs/v1-implementation-plan.md`
4. `Docs/strategy/2026-06-06-permission-aware-mcp-gateway-plan.md`

**验收标准：**

1. README 第一段不再描述为 local SQLite-backed legal project context。
2. README 明确写出 non-goals。
3. 旧 v1 plan 标记为 legacy。
4. TODOs 不再把 admin 录入、MDM、import-to-own 作为主路线。

### 阶段 2：模块标记与测试重心调整

**目标：** 不大改代码，先把测试和模块定位转向「权限披露」。

**修改文件：**

1. `tests/test_query_authorization.py`
2. `tests/test_disclosure_audit.py`
3. `tests/test_agent_graph.py`
4. `tests/test_mcp_server.py`
5. `src/legal_mcp/query_authorization.py`
6. `src/legal_mcp/disclosure_audit.py`
7. `src/legal_mcp/mcp_server.py`

**新增或强化测试：**

1. 普通用户问项目主体时，未授权字段不返回。
2. 法务用户问项目主体时，授权字段返回。
3. 同一个问题对不同用户返回不同结果。
4. 拒绝披露写入 audit。
5. 允许披露写入 audit。
6. production MCP tool list 只暴露 `agent_query`。
7. 泄漏红队测试（permanent gate）：用多种自然语言话术尝试套出 deny 字段，断言响应与 LLM 上下文里都拿不到该字段值。该测试进 CI，不可回退——对一个靠权限立身的项目，一次字段泄漏回归就毁信誉。

**验收标准：**

```sh
uv run pytest tests/test_query_authorization.py tests/test_disclosure_audit.py tests/test_mcp_server.py -q
```

测试通过，并且失败用例能证明越权披露被阻止。

### 阶段 3：引入 connector interface

**目标：** 把数据访问从 SQLite 事实主库抽象为 read-through connector。

**新增文件：**

1. `src/legal_mcp/connectors/__init__.py`
2. `src/legal_mcp/connectors/base.py`
3. `src/legal_mcp/connectors/sqlite_demo.py`

**修改文件：**

1. `src/legal_mcp/query_catalog.py`
2. `src/legal_mcp/agent_graph.py`
3. `src/legal_mcp/query_plan.py`
4. `src/legal_mcp/db.py`
5. `src/legal_mcp/agent_fast_path.py`（法务词表下沉，`build_query_plan` 改为调用 `connector.fast_intents()`）

**最小接口：**

```python
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class ConnectorField:
    domain: str
    name: str
    description: str
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class ConnectorQuery:
    domain: str
    filters: dict[str, Any]
    fields: tuple[str, ...]
    limit: int = 20


class DataConnector(Protocol):
    name: str

    def catalog(self) -> tuple[ConnectorField, ...]:
        ...

    def query(self, query: ConnectorQuery) -> list[dict[str, Any]]:
        ...

    # 可选：确定性快路径意图。连接器自带领域词表，命中则返回受约束查询
    # （绕过 LLM planning，但仍走网关授权）；未命中返回 None 回退 LLM。
    def fast_intents(self, question: str) -> ConnectorQuery | None:
        ...
```

**验收标准：**

1. query catalog 可以从 connector catalog 构建。
2. demo connector 可以查询当前 SQLite legal demo 数据。
3. `agent_query` 不需要直接知道 `projects/contracts/licenses` 表结构。

**接口预留（v0.2 实现保持最小，但在注释/设计里留好这些槽位，避免 v0.3 接飞书时破坏性返工）：**

1. 记录范围谓词（record scope）必须与用户查询条件（filters）可区分；授权的**行级过滤要能下推给 connector**，而不是返回全量再在网关里裁剪（既搬大量数据又扩大泄漏面）。
2. capability 声明：connector 告知哪些 filter / field 能下推、哪些需网关侧处理。
3. 区分「字段不存在」与「字段存在但无权」——后者不得把 schema 暴露给 LLM。
4. 分页 / 游标与明确的错误模型。
5. 可选 `fast_intents(question)`：连接器自带领域词表的确定性快路径，命中绕过 LLM planning（降延迟与成本，利于本地模型），未命中回退 LLM，产出仍经授权。legal demo 的现有快路径词表迁到这里——把「机制」留在 core、「法务知识」下沉到连接器。

### 阶段 4：治理 schema 与 demo schema 拆分

**目标：** 从数据库层面切开「治理数据」和「业务事实 demo 数据」。

**修改文件：**

1. `src/legal_mcp/schema.sql`
2. `src/legal_mcp/db.py`
3. `tests/test_schema.py`

**拆分方式：**

`schema.sql` 中保留治理表作为产品核心：

1. `schema_version`
2. `users`
3. `api_keys`
4. `user_groups`
5. `user_group_memberships`
6. `permission_grants`
7. `audit_events`
8. `audit_disclosures`
9. `audit_event_details`
10. `agent_runs`
11. `agent_steps`
12. `agent_settings`
13. `deployment_settings`

将以下表标记为 demo source tables：

1. `projects`
2. `contracts`
3. `licenses`
4. `risks`
5. `project_aliases`
6. `project_access`

`project_access` 当前同时带有授权含义和项目事实依赖。新架构下应逐步迁移到通用 policy/grant 体系，避免绑定 legal project。

**验收标准：**

1. schema 测试能区分 governance tables 与 demo source tables。
2. README 不再称 SQLite 是 canonical business database。
3. demo 仍可运行。

### 阶段 5：声明式 policy

**目标：** 让字段级权限成为项目最重要的开源 artifact。

**新增文件：**

1. `examples/policies/legal-demo.policy.yaml`
2. `tests/test_policy_config.py`

**修改文件：**

1. `src/legal_mcp/policy.py`
2. `src/legal_mcp/query_authorization.py`

**policy 示例：**

```yaml
version: 1
roles:
  legal:
    # record_scope = 行级：该角色能看到“哪些记录”（与“哪些字段”解耦）
    record_scope:
      project: all            # legal 可见全部项目
    allow:
      - domain: project
        fields: ["project_code", "name", "contact_person", "company_entity"]
      - domain: contract
        fields: ["title", "counterparty", "company_entity", "expiry_date"]
  business:
    record_scope:
      project: by_grant       # business 只能看被显式授权的项目（迁移自 project_access）
    allow:
      - domain: project
        fields: ["project_code", "name", "contact_person"]
    deny:
      - domain: contract
        fields: ["company_entity", "total_amount", "payment_terms"]
```

**验收标准：**

1. policy 文件可加载。
2. 同一字段对不同角色产生不同授权结果。
3. deny 优先于 allow。
4. 未声明字段默认拒绝。
5. 测试覆盖 allow、deny、default deny、audit reason。
6. record_scope 决定记录可见性，与字段授权解耦；business 角色只能看到被授权的记录。
7. 测试同时覆盖字段级（allow/deny/default deny）与行级（record_scope）两类越权。`project_access` 的授权语义迁入此处，不再绑定 legal project。

### 阶段 6：最小 demo

**目标：** 产出一个可以给外部用户看的演示。

**新增文件：**

1. `examples/legal-demo/README.md`
2. `examples/legal-demo/questions.md`
3. `examples/legal-demo/demo-data.csv` 或 `examples/legal-demo/demo.db`

**演示问题：**

1. `这个项目该找谁办？`
2. `这个项目的联系人是谁？`
3. `这个项目的关联主体是哪个？`
4. `我能看这个项目的合同主体吗？`
5. `我能访问哪些项目？`

**演示用户：**

1. legal user：可看联系人、项目主体、合同主体。
2. business user：可看联系人，不可看合同主体和金额。
3. auditor user：可看 audit，不一定可看业务敏感字段。

**验收标准：**

1. legal user 得到授权答案。
2. business user 被拒绝或得到脱敏答案。
3. audit 中能看到 allowed disclosure。
4. audit 中能看到 denied disclosure。
5. MCP client 侧无法直接调用底层数据库工具。

---

## 7. 后续版本路线

### v0.3：真实 read-through source

优先选择一个真实数据源：

1. 飞书多维表格。
2. Postgres。
3. Google Sheets。

建议优先飞书多维表格，因为它最贴近当前中文企业内部表格工作流，也最能证明「不替代现有系统」。

### v0.3.5：本地 / 自托管模型路径（与隐私受众强相关，不可省）

最可能自托管本网关的人，恰恰是不能把数据发给外部 LLM 的人。问答必然要过一个 LLM，因此必须支持本地模型，否则对核心受众直接破功。

1. 把 LLM 后端抽象为可配置端点（`ai_provider.py` 已有基础），支持 Ollama / vLLM / 任意 OpenAI 兼容端点。
2. 跑通一个本地模型的端到端 demo。
3. 在承诺矩阵里写清：**仅当配置本地模型时**，才能承诺「问答全程不出内网」。

### v0.4：身份穿透（最小版本已在 v0.2 落地，见 §12）

把 v0.2 的最小身份升级为更真实的团队身份：

1. HTTP header identity。
2. 反向代理传入用户。
3. API key to user mapping。
4. OIDC/OAuth 的最小接入。

### v0.5：开源贡献面

重点服务外部贡献者：

1. connector authoring guide。
2. policy cookbook。
3. security model 文档。
4. audit model 文档。
5. reference connector tests。

### v1.0：稳定开源版本

v1.0 的标准不是功能多，而是边界清晰：

1. 默认安全。
2. policy 可读可测。
3. connector 接口稳定。
4. demo 一眼看懂。
5. 文档能让一个小团队一天内自托管试用。

---

## 8. 风险与应对

### 8.1 范围回潮

**风险：** 又开始做录入、MDM、BI、法务字段平台。

**应对：** README 和 CONTRIBUTING 明确 non-goals。任何新功能必须能追溯到北极星场景。

### 8.2 通用化过早

**风险：** 为了支持任何职位，抽象过多，demo 失去锋利度。

**应对：** v0.2 只保留 legal demo。通用的是 policy、connector、audit，不是第一个 demo 的业务字段。

### 8.3 安全承诺过度

**风险：** 用户误以为使用外部 AI client 时答案也不会离开本机或内网。

**应对：** 文档明确区分原始数据、授权披露结果、模型上下文。

### 8.4 连接器维护失控

**风险：** 官方维护太多连接器，项目变成集成平台。

**应对：** 官方只维护 1-2 个连接器。其他连接器通过接口和测试模板交给社区。

### 8.5 旧代码沉没成本

**风险：** 因为已有 import/admin/schema 代码而继续沿旧方向加功能。

**应对：** 先标记 legacy，再通过新测试和新接口逐步替换。删除发生在新路径稳定之后。

### 8.6 身份穿透是命门（头号技术风险）

**风险：** 整个价值主张依赖「网关知道是谁在问」。但 MCP 现实是一个 client = 一份配置 = 一个凭证，默认网关只看到 client 身份而非真人。若把这一步一直推后，v0.2 的「同问题不同字段」就只是 demo 假数据，真实多用户场景跑不通。

**应对：** 把最小真实身份路径拉到 v0.2（§5.2 第 13 项、§12），并把身份模型当作头号风险持续验证，而不是一个后续版本的功能点。

### 8.7 网关即新「服务账号」（信任边界上移）

**风险：** read-through 让网关持有数据源的宽权限凭证，用一个账号代所有人读取再过滤——正是战略文档批判的「AI 走服务账号看到一切」，只是主体换成了网关本身。网关代码的正确性 = 安全边界本身。

**应对：** v0.2 出一页威胁模型（§12）；把「未授权字段绝不进入 LLM 上下文」立为命名不变式，配永久泄漏红队测试（§6 阶段2 第 7 项）。

### 8.8 本地模型缺位会赶走核心受众

**风险：** 目标人群是「数据不能出内网」的团队，但问答必须过 LLM；只能接外部 API 等于对最该采用的人破功。

**应对：** 把本地 / 自托管模型列为一等路线项（§7 v0.3.5），并在承诺矩阵中如实区分。

### 8.9 冻结后永不删除

**风险：** 「先冻结后删除」若无删除触发器，legacy 往往永远留着，正是项目想逃离的范围膨胀。

**应对：** 给删除绑定明确条件，例如：「`import_pipeline/`、`admin_*` 在 connector 接口 + policy 达到 v0.4、且无 demo 依赖时删除」。把删除绑到版本里程碑，而不是「以后再说」。

---

## 9. 第一周执行清单

### Day 1：文档换轨

1. 新建本计划。
2. 重写 README 第一屏。
3. 增加 non-goals。
4. 标记旧 v1 plan 为 legacy。
5. 重写 TODOs 为 new roadmap。
6. 用 GitHub「Add license」添加 LICENSE（推荐 Apache-2.0）；新增 CONTRIBUTING 与 SECURITY.md（§13）。
7. 决定 `Docs/` 的 git 跟踪策略——当前 `Docs/` 被 .gitignore 整体忽略，却已有 9 个旧文件被跟踪，处于半跟踪状态（含本计划尚未入库）；见 §13。

### Day 2：测试换轨

1. 强化 `test_query_authorization.py`。
2. 强化 `test_disclosure_audit.py`。
3. 强化 `test_mcp_server.py`。
4. 确认 production catalog 只有 `agent_query`。

### Day 3：connector interface

1. 新增 `connectors/base.py`。
2. 新增 `connectors/sqlite_demo.py`。
3. 让 query catalog 从 connector 生成。

### Day 4：demo source 重命名

1. 文档层面将 SQLite legal data 改称 demo source。
2. 代码注释和测试命名开始从 canonical database 转为 demo connector。
3. 不删除旧 import pipeline。

### Day 5：policy 文件

1. 新增 `examples/policies/legal-demo.policy.yaml`。
2. policy loader 支持 allow/deny/default deny。
3. 增加 policy tests。

### Day 6：端到端 demo

1. 写 legal demo 数据。
2. 写 demo questions。
3. 跑通 legal user 和 business user 的不同结果。
4. 检查 audit。

### Day 7：发布前整理

1. 跑核心测试。
2. 更新 README demo。
3. 更新 roadmap。
4. 写发布说明草稿。

---

## 10. 成功标准

### 产品成功标准

1. 用户能用一句自然语言问内部小问题。
2. AI 只得到这个用户有权看的字段。
3. 未授权字段不会出现在响应里。
4. 审计能回答「谁在什么时候问了什么，系统披露了什么」。
5. 数据仍留在原始数据源或 demo source 中。

### 开源成功标准

1. README 第一屏让人 30 秒内理解项目。
2. demo 让人 5 分钟内理解权限差异。
3. connector interface 让贡献者知道如何接自己的数据源。
4. policy 示例让安全/法务/IT 人能读懂。
5. non-goals 足够明确，能挡住平台化需求。

### 工程成功标准

1. 核心测试围绕权限、披露、审计，而不是 import-to-own。
2. `agent_query` 是默认公开入口。
3. 业务事实表不再被描述为产品主数据模型。
4. 旧模块被清楚标记为 core、demo、legacy 或 frozen。
5. 每个阶段都可独立回退。

---

## 11. 推荐立即动作

下一步只做三件事：

1. 创建 `codex/open-source-pivot` 分支。
2. 修改 `README.md`，正式换定位。
3. 修改 `TODOS.md`，冻结旧平台路线并写入 v0.2 roadmap。

完成这三件事后，再进入代码重构。不要先删代码。

---

## 12. 身份、信任与威胁模型（rev 2 补强，核心）

这一节是 rev 2 最重要的补强。它处理的是「这个产品到底成不成立」的问题——不是功能，是命门。

### 12.1 身份是命门，不是后续功能

整个价值主张是「AI 只告诉你**你**该看的部分」，它要求网关知道**正在提问的人是谁**。但 MCP 的现实是：一个 client（Claude Desktop / Cursor）= 一份 server 配置 = 一个凭证。默认情况下网关看到的是「这台 client 的身份」，而不是「坐在前面的人」。

因此「同一个问题对不同用户返回不同字段」这件事，**不能只靠 demo 里一个用户配一个 API key 来糊过去**。那只证明了授权引擎能区分身份，没证明真实团队部署里网关能区分 Alice 和 Bob。

### 12.2 v0.2 的最小但真实的身份路径

v0.2 必须落地一个**真实可跑**的多用户身份，而不是假数据：

1. stdio 本地部署：每个人用自己的 client + 自己的 API key（`identity.py` 的 api-key → user 已支持）。
2. HTTP 团队部署：网关接受每用户 API key，或由反向代理注入用户标识 header。
3. 写一份 `Docs/identity-model.md`，明确两种部署下「谁在问」分别如何确定，以及各自的信任假设。

真正成熟的身份穿透（OIDC/反代/SSO）留给 v0.4，但**最小真实版本必须在 v0.2 就跑通**，否则演示看起来像魔法，落地却没有路径。

### 12.3 信任边界：网关即新「服务账号」

read-through 架构下，网关持有数据源的宽权限凭证（飞书 token / Postgres 账号），**用一个账号代所有人读取，再在网关内按人过滤**。这恰恰是战略文档 §2.3 批判的「AI 走一个服务账号看到一切」——只是现在服务账号变成了网关本身。

结论：**网关代码的正确性，就是安全边界本身。** 这把要求压到了 §12.4 的不变式和审计上。

### 12.4 命名不变式：未授权字段绝不进入 LLM 上下文

把这条立为项目的头号安全不变式，并用永久测试守护：

> 未授权字段的值，在 planning、检索、合成的**任一阶段**都不得进入 LLM 上下文。

实现要点：授权作用在 query plan 上（`query_authorization.py` 已是此模型），被拒字段在取数前就从 plan 剥离，connector 根本不取它；网关可以把「为什么被拒」的 reason 给 LLM，但绝不给值。配合 §6 阶段2 第 7 项的泄漏红队测试作为不可回退的 CI gate。

### 12.5 catalog 可见性本身也是隐私决策

catalog 告诉 LLM「有哪些字段」。用户无权的字段，**要不要让它知道该字段存在**，是两种不同的隐私姿态。至少在 policy 设计文档里记一笔，决定按角色裁剪 catalog 还是统一暴露 catalog 但拒绝取值。

### 12.6 威胁模型文档（v0.2 一页，v0.5 展开）

v0.2 就产出一页 `Docs/threat-model.md`：信任边界、网关被攻破/过滤逻辑出 bug 的后果、为何过滤必须在网关内且在数据到达 LLM 之前完成。v0.5 的 security model 文档在此基础上展开，而不是从零写。

---

## 13. 开源发布必备件与仓库卫生（rev 2 补强）

### 13.1 法务 / 社区文件（地基，当前完全缺失）

核查结果：根目录没有 LICENSE / CONTRIBUTING / SECURITY / CODE_OF_CONDUCT / CHANGELOG，`pyproject.toml` 也没有 `license` 字段。没有 LICENSE 的 repo 在法律上是「保留所有权利」，别人不能合法使用或贡献——对一个以名望和社区贡献为目标的项目，这是自相矛盾的。

| 文件 | 动作 | 优先级 |
| --- | --- | --- |
| LICENSE | 用 GitHub「Add license」生成，推荐 Apache-2.0（含专利授权，适合工具/协议生态） | Day 1 |
| `pyproject.toml` license 字段 | 与 LICENSE 对齐 | Day 1 |
| CONTRIBUTING.md | 把 non-goals 钉进去，挡住平台化「顺手加功能」 | Day 1 |
| SECURITY.md | 漏洞披露渠道——安全定位项目缺它尤其刺眼 | Day 1 |
| CODE_OF_CONDUCT.md | 标准模板即可 | 首发前 |
| CHANGELOG.md | 记录 pivot 与版本 | 首发前 |

### 13.2 `Docs/` 跟踪策略（本轮已处理，采用 allowlist）

原状：`.gitignore` 里一行 `Docs/` 整体忽略，但已有 9 个 Docs 文件在该规则之前被跟踪——旧文档在库、本计划与战略文档不在库。

**重要发现（决定了方案选型）：** 核查 `Docs/` 时发现 `Docs/superpowers/plans/` 里有历史计划文档**内嵌真实业务数据**（真实人名、公司名、合同号、金额，如 `2026-05-23-legal-mcp-1.3-implementation.md`、`2026-05-26-...1.4.2....md`）。所幸这些文件当前**未被跟踪**。

因此**不能用「默认跟踪、再忽略敏感文件」(denylist)**——漏掉一个就泄漏。改为反向的 **allowlist**：

```gitignore
# Docs 默认本地（历史计划内嵌真实客户数据）。只放行经清洗的目录。
Docs/**
!Docs/strategy/
!Docs/strategy/**
```

本轮已落地此 `.gitignore`，并用 `git check-ignore` 验证：`Docs/strategy/` 可跟踪、real-data 文件仍被忽略。后续若要放行其它 Docs 子目录（如 `Docs/superpowers/`），必须先逐文件清洗真实数据再加白名单。

**两点遗留（需你处理）：**

1. ~~已跟踪的设计文档把内部项目代号当作别名示例。~~ **已处理（任务 A）**：design doc 等全部已跟踪文件与测试 fixture 里的内部代号与项目名已统一替换为中性占位代号 `ACME` / `示例项目`。
2. `Docs/strategy/` 现在是 untracked-but-trackable 状态；需要 `git add` 后才真正入库（本轮未自动提交）。

### 13.3 仓库卫生（本轮已处理）

- 已删除 6 个遗留 git worktree（`.worktrees/`，约 121 MB）。删除 worktree 不影响其分支与提交，分支 ref 全部保留。
- 已删除根目录 `build/` 构建产物（可再生，已 gitignore）。
- `trial-legal.db` 保留：是本地试用数据，且已被 `*.db` 忽略，不会进入仓库。

### 13.4 真实数据核查（结论：无泄漏风险）

`*.db`、`*.sqlite`、`data/**` 均已 gitignore，git 历史中无 `.db` 文件。README 已声明数据目录只发空壳。公开前再跑一次「无真实客户数据」核查即可，无需历史清洗。

### 13.5 仍待清理：陈旧分支（需你决定，未自动删除）

仓库有十余个已落地或废弃的分支（`codex/*`、`feat/admin-ux-*`、`phase4/*`、`backup/*` 等）。删分支是破坏性操作，未在本轮自动执行。其中 `codex/legal-mcp-v1.4-agent-entry`、`codex/legal-mcp-v1.4.1-langgraph-retrieval` 仍有未并入 main 的提交，删前需确认。其余多为已并入 main 的历史分支，可安全 prune。

---

## 14. 版本号与命名（rev 2 补强）

### 14.1 版本叙事不一致，发布前必须统一

`pyproject.toml` = `0.1.0`，而 README / git 里程碑用 v1.3–v1.5.2。「v0.2」对包版本是顺的，对里程碑命名是倒退。读者会困惑。发布前选定一套方案：

- **推荐**：包版本走 0.x（0.2.0 起），把过去的 v1.3–v1.5.2 明确标注为「pivot 前的内部里程碑命名」，在 CHANGELOG 里说明断点。
- 或：若想保留连续性则用 2.0.0，但与「开源重塑期、低版本号更稳」的取向相悖。

### 14.2 命名与 PyPI 包名：比计划原本估计的更紧急

战略文档把「通用 framework vs 法务专用」列为唯一要现在拍板的决策，并暗示改名。本计划 §5.3 选择 v0.2「不改项目名」——推迟代码改名是合理的，**但「首次公开发布用什么 PyPI 包名」必须在 0.2 公开发布前定**：首发包名有黏性，发了 `legal-mcp` 再改名会割裂用户、分散 star。

建议：

1. 现在就定**公开包名**（中性名，如 `permguard-mcp` / `mcp-gateway` 之类，最终由你拍板），即使代码内的包路径 `legal_mcp` 暂不改。
2. `legal` 作为 `examples/` 里的旗舰样板保留。
3. 代码层面的重命名可推迟，但不要在公开发布后才决定对外名字。
