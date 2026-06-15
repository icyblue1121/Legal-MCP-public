# 竞争战略：在 RAG/知识库时代，Legal-MCP 的生态位与开发重点

> 生成日期：2026-06-08
> 状态：战略论证（供路线图取舍、对外定位、投资人/客户沟通使用）
> 目标读者：项目维护者、后续参与开发的 AI agent、潜在投资人与早期客户
> 方法：本文档的两个核心论断均经过对源码的实证核查（见 §3 与 §4），不是宣称。

---

## 0. 一句话决策

不要在检索/编排上与 RAG 知识库竞争——那一层正在快速商品化。Legal-MCP 要占的是它们**架构上做不到、又不得不需要**的那一层：**字段级授权与披露审计的控制平面**。RAG 不是敌人，RAG 恰恰制造了 Legal-MCP 要解决的问题。

---

## 1. 把市场拆成两个平面

| 平面 | 是什么 | 谁在做 |
|---|---|---|
| **数据平面 Data Plane** | 答案从哪来：检索质量、向量库、知识图谱、Agent 编排、聊天 UI | Yuxi / Dify / RAGFlow / FastGPT / Coze —— 全在这层卷 |
| **控制平面 Control Plane** | 谁有权看到什么：身份、数据域、记录范围、**字段级**披露、审计、合规留痕 | 几乎无人认真做 |

三个趋势构成顺风：

1. **数据平面在快速商品化。** RAG + LangGraph 已是开源八件套，追检索是追一辆加速的车。
2. **控制平面在快速变刚需。** 大模型进企业的最大阻力不是"答得准"，是"法务/安全敢不敢让它连真实数据"。
3. **RAG 本身把授权搞砸了**（§2、§3 详证）——这是最该讲、且能用证据钉死的故事。

---

## 2. 原理：RAG 为什么必然破坏权限

RAG 标准管线：`文档 → 切块 → 向量化 → 写入向量库 → 相似度检索 top-k → 塞进 prompt → 生成`。权限在这条链上断三次：

**断点一 · 权限绑在源、切块即剥离。** 源系统的访问控制是活的、细的（行级/字段级，每次访问实时裁决）。一旦切块向量化写入向量库，块就与源 ACL 彻底脱钩——向量库原生没有"谁能看"的概念。**复制即失控。**

**断点二 · 切块混合密级、无字段可过滤。** 一条记录里"姓名[公开]/薪资[HR]/身份证[受限]"密级不同，但切块按 token 窗口切，不按权限边界切。一个块成原子单位：整块给则泄密、整块不给则召回崩。**RAG 没有"字段"概念，做不到"返回这块但抹掉薪资"。**

**断点三 · 检索相似度驱动、对权限失明。** ANN 只按相关性排序，零授权维度。由此泄露**可被查询、且攻击者无需知道秘密存在**——问"法务部薪酬最高的是谁"，相关受限块就被捞进 prompt。

常见补丁都补不住：metadata 标签会过期（权限漂移）、粒度太粗、字段失明；prompt 约束不是安全边界（可越狱）；输出端事后过滤时秘密已进 LLM 上下文；每用户一套索引不可扩展。

**根因：RAG 为检索把数据跨信任边界复制进共享索引，这个动作把"丰富、动态、字段级"的访问模型拍扁成"静态、粗粒度的标签"（甚至没有），而检索机制本身对权限失明。**

---

## 3. 证据章节（一）：Yuxi 在架构上做不到字段级授权

> 审查对象：`github.com/xerrors/Yuxi`（语析），分支 `main`，后端 `backend/package/yuxi/`。
> 方法：三路独立精读（权限模型 / RAG 管线 / Agent 层）互相印证。
> 论断成立范围：其**原生 Milvus 向量库 + 知识图谱路径**（外部 Dify/Notion 连接器委托给第三方系统，有各自访问模型，不在此论断内）。

**核心结论：** Yuxi 的授权最细只到"整个知识库"这一级、且二元。一旦用户能访问某知识库，里面每个文档/块/字段原样返回。无字段级脱敏、无行级过滤、无披露审计。这不是配置缺失，是数据模型层面就没有。

| 我的论断 | Yuxi 代码层面的实证 |
|---|---|
| RAG 把权限拍扁到知识库级 | `knowledge/manager.py`：`share_config.access_level ∈ {global,department,user}`，作用于整库；`KnowledgeChunk` 表只有 `created_by/updated_by`，无权限列 |
| 向量丢失源 ACL | `implementations/milvus.py` `_create_new_collection`：schema 7 字段（id/content/chunk_id/file_id/chunk_index/embedding/sparse），**零 ACL/owner/dept** |
| 检索对权限失明 | `aquery()` 的 `collection.search(expr=file_expr)`，`file_expr` 只可能是可选文件名过滤；不传则纯 top-k，无任何 expr；检索器闭包只带 `(query_text, kb_id, **kwargs)`，**用户身份不下传** |
| 字段级脱敏不可能 | chunk = 单个 `content` VARCHAR(≤65535)，无字段结构 |
| Agent/prompt 不是安全边界 | `agents/toolkits/kbs/tools.py` `query_kb`：门禁仅 `kb_id ∈ visible_kbs`，过后整库内容入 LLM；**MCP 工具用服务端静态凭证，不转发提问用户身份** |
| 无合规留痕 | 仅 `OperationLog`(operation 串 + IP)，无字段级披露审计；Langfuse 是 LLM 遥测非授权审计 |
| 连那层粗门禁都有漏 | REST 端点 `POST /databases/{kb_id}/query` 仅挂 `Depends(get_admin_user)`，**未调 `check_accessible`** |

**这是 Legal-MCP 生态位的反向证明：** Yuxi（及同架构的 Dify/RAGFlow）不是"还没做"字段级授权，是**数据模型决定了做不了**——要做就得重写存储与检索栈，商业上不会为合规倒过来重构。

---

## 4. 证据章节（二）：Legal-MCP 确有能力实现这些功能

> 方法：四路独立精读自身源码（字段级授权引擎 / read-through 连接器 / 披露审计与最小披露 / 授权先于检索与身份授予），全部要求附 file:line 证据并诚实标注缺口。

**核心结论：对外定位宣称的能力，代码里是真实实现、有测试、有架构强制的，不是 README 承诺。**

| 宣称能力 | 代码实证 | 真实性 |
|---|---|---|
| 字段级授权（filter+return 双向） | `policy.py:121` `authorize_fields`；`query_authorization.py:89-119` 双 gate；`permission_grants` 表以 `field_name` 为列 | ✅ 真实、有测试 |
| 默认拒绝 + 硬拒 | 任一字段无授权 → 整个 plan 拒绝执行（`search_tools.py:31`），非悄悄丢字段 | ✅ 真实 |
| Read-through 不复制 | `connectors/feishu_bitable.py`：每次查询实时 HTTP 拉取，无缓存无 ETL；授权在网关、不在连接器（`connector_retrieval.py`：先字段 gate 再取数，取数后按 record-scope 过滤再投影） | ✅ 真实（生态窄，见缺口） |
| 最小披露（禁全量 dump） | `tools.py:164` `get_project_context` 已废弃返回 `deprecated_tool`；字段工具强制显式 field 列表，只返回 `decision.allowed_fields` | ✅ 真实、有测试 |
| 字段级披露审计 | `audit_disclosures` 表每字段一行（who/field/decision/reason）；`disclosure_audit.py:30` `write_audit_event` | ✅ 真实、有测试 |
| 授权先于检索（LangGraph） | `agent_graph.py` 节点序：validate_plan → **authorize_plan** → execute_plan；授权不过不执行 | ✅ 真实 |
| 按用户身份 + 授予 | `identity.py:143` API key→user 映射；`permission_grants` 支持 user/group/project 维度；`tools_access.py` `describe_my_access` 出"有效视图" | ✅ 真实 |
| 外部客户端只见 agent_query | 生产 catalog 仅暴露 `agent_query`，不给原始 DB 工具/SQL | ✅ 真实 |

**诚实的缺口（即 §6 路线图的来源，不藏）：**

1. **连接器生态窄。** 目前只有 Feishu Bitable（真实 read-through）+ SQLite（reference demo）。无 REST/SQL/其他 SaaS 连接器。架构支持配置化扩展（YAML 声明、密钥走环境变量），但未被广泛验证。
2. **审计仅 append-only、非防篡改。** JSONL 为 `"a"` 模式、DB 为 INSERT-only，但有 DB 凭证者仍可 DELETE/UPDATE；无加密、无哈希链。
3. **`agent_write` 仍 proposal-only**，不落库；group grants 代码已实现，Admin UI 可能未完全覆盖。
4. **字段级脱敏限结构化源。** 自由文本（PDF/合同）的"字段"是隐式的，需 span 级分类，目前未做，应明确为 best-effort。
5. Feishu 连接器目前仅支持等值过滤下推；by-owner 记录范围预留 v0.4.5。

→ 缺口都在"广度"与"加固"，不在"能不能做到"。**核心论点站得住。**

---

## 5. 生态位与竞合姿态

**定位一句话：** "任何 AI 知识平台与企业真实数据之间的、可审计的字段级授权网关。" RAG 平台是 data destination；Legal-MCP 是它们够不到的 authorized connector + 合规留痕层。

护城河是**别人结构上做不好**的事：read-through 不复制（他们天生要灌数据）、字段级×记录级最小披露（他们粒度停在知识库级）、每次披露可审计（他们的审计是"谁开了对话"）。**卖的不是"更好的答案"，是"敢上线的答案"**——预算来自安全/合规，不是创新预算。

按对象给明确姿态：

| 对象 | 姿态 | 理由 |
|---|---|---|
| 开源知识平台（Yuxi/Dify/RAGFlow） | **合作/寄生** | 它们是分发渠道。做"给 X 加一层合规网关"的接入，借其装机量；授权这块它们补不上 |
| 企业自研"DIY 安全" | **竞争（主战场）** | 用 prompt 限制/view/手写 RBAC 拼凑授权——过不了审计、覆盖不全、无留痕 |
| 大厂企业搜索（Glean 类） | **差异化避让** | 打"数据不出域 + 自托管 + 开源可审计"的细分 |
| MCP 协议本身 | **顺势，但要快** | 协议越普及越受益；一旦出官方授权规范，要么是参考实现、要么被吃掉。**这是时间窗。** |

---

## 6. "它们已有 RAG 和 LangGraph，我怎么应对"

三层应对：

1. **不自卷 RAG——把 RAG 变成"可授权的 RAG"。** Legal-MCP 的服务端 LangGraph 目的不是检索得更好，而是让检索路径**经过授权校验**（filter 与 return 字段都过闸门）。保持这种克制：它是"授权执行器"，不是通用 Agent 平台。

2. **讲清别人不敢讲的真相（§2/§3）：RAG 破坏权限边界。** 应对范式是 **authorize-before-retrieve**——不靠 LLM 自觉过滤，而在连接数据源、构造查询那一刻就按身份裁剪。这是架构正确性问题，reranker/prompt 防护补不上。

3. **战略上让 RAG 成为一个 connector，而非竞品。** 需要非结构化检索时，**调用**别人的向量检索作为受授权约束的 connector（只借检索、不借存储与权限），结果仍由网关裁剪、审计。RAG 越强，网关越值钱——站在上游收"过路费"。

接入时三种发力形态（可叠加）：
- **模式 A 分流**：敏感结构化数据不进向量库，留源头经 Legal-MCP 实时授权访问；向量库只放可共享低敏内容。给 Agent 双工具。（最符合 non-goal，最该主推）
- **模式 B 前置闸门**：必须对敏感内容检索时，查询先过 Legal-MCP 算授权范围，下推为对实时源 ACL 的硬过滤；无权内容不进 LLM。
- **模式 C 字段级脱敏**：块进上下文前按字段最小披露（抹薪资留姓名）——RAG 做不到、Legal-MCP 能做。

---

## 7. 下一阶段开发重点（排序即优先级）

对齐既有 v0.4（数据源先行）/ v0.4.5（身份+by_owner）路线，并由 §4 缺口反推：

**P0 — 把生态位做实（0–1 月）**
1. **接入 demo：Legal-MCP 作为 Dify/Yuxi 的授权网关**（模式 A）。这是验证战略的最小可信证据，优先于任何新功能，一次回答"区别/竞合/价值"。
2. **披露溯源（disclosure provenance）**：每个返回字段附"来自哪个源 + 依据哪条授权规则"，把审计升级成用户当场可见的可解释披露——正面差异化 RAG 黑箱检索。

**P1 — 拓宽 connector 与身份（1–3 月）**
3. **连接器框架标准化**：接新源（PG/MySQL/REST/SaaS）成配置而非改代码。护城河宽度 = connector 覆盖度。直接补 §4 缺口 1。
4. **身份联邦（SSO/OIDC）+ by_owner**：用企业现有身份系统，而非再建用户表。从 pilot 到可采购的门槛。
5. **authorize-before-retrieve 对非结构化源**：connector 读 PDF/文档时切块前按授权裁剪，把模式 B/C 落成代码。

**P2 — 让合规可被验收（3–6 月）**
6. **审计加固**：防篡改/哈希链/可导出披露报告/可回放"谁看了什么"。补 §4 缺口 2，把"安全特性"变"采购理由"。
7. **策略可治理性**：策略版本化、变更审计、模拟（"改这条规则谁的可见性会变"）。

**明确不做（守 non-goals，否则被拖回重平台）：** ❌ 自建向量库/知识图谱托管 ❌ 通用 Agent 编排平台/插件市场 ❌ 聊天 UI 当主入口（MCP-native，UI 只服务 Admin）。

---

## 8. 一页纸总结

- **生态位**：AI 与企业真实数据之间的字段级授权 + 审计控制平面，read-through 不复制。
- **对 RAG/知识库**：不竞争检索，寄生于其分发，占据其结构上做不好的授权层；用"RAG 破坏权限边界"作锋利反向叙事，且有 Yuxi 源码实证（§3）。
- **能力可信**：核心能力均经自身源码实证（§4），缺口在广度与加固，不在可行性。
- **核心动作**：做"授权网关接入 demo"，把竞品变渠道。
- **开发重点**：connector 广度 + 身份联邦 + 披露溯源 + 合规化审计——全部服务于"敢上线、过得了审计"这一个价值主张。

---

## 附：横向证据待补

本文档对 Yuxi 的论断已用源码钉死。若要把"主流开源知识库**普遍**缺字段级授权"从单点升级为行业级论断，下一步可同样精读 **Dify / RAGFlow** 的权限实现，补齐横向证据。
