# Legal-MCP 1.6.0 开发方案（导入落库 + 主体 + 批量 + Schema 迁移）

> **由 1.5.2 拆分而来。** 这些改动属于**数据库重构级别**（迁移框架、建表、主体一等公民、批量写、审计落库），范围大、风险高，从 1.5.2 上移为独立的 1.6.0 大版本。1.5.2 仅保留管理界面的细微改动，见 [admin-ux-1-5-2](2026-06-05-admin-ux-1-5-2.md)。

> **For agentic workers:** REQUIRED SUB-SKILL: 用 superpowers:subagent-driven-development 或 superpowers:executing-plans 逐任务实现。任务用 `- [ ]` 复选框跟踪。

**背景：** 1.5.1 已落地 Users 重构、管理页（Tab/分页/搜索/排序/保持位置）、用户与 Key 的停用/编辑、Database 页（摘要/主体聚合/别名）、拖拽上传**占位**端点，并把 `admin_server.py` 按 Mixin 拆成解耦模块。1.6.0 把 1.5.1 显式留挂的项做实，并修掉过程中暴露的**真实库 schema 不兼容**这个阻塞问题。

**本期目标（按优先级）：**
1. **Schema 迁移框架**（阻塞项）：现有库升级时 `create table if not exists` 不会补列，导致 `no such column: handler` 等错误、无法用真实数据。建立基于 `schema_version` 的迁移执行器。
2. **导入真正落库**：把 Database 拖拽上传从占位接到现成的 `import_file()`，做「预览(dry-run) → 确认 → 落库(事务/可回滚) → 报告」。
3. **主体（实体）一等公民**：从只读聚合升级为可下钻/可管理。
4. **批量操作**：管理页多选 + 批量停用/启用、批量授权/收回、批量吊销 Key。
5. **后台变更审计**：记录每一次 admin 写操作（谁/何时/什么/前后值）。
6. **团队模式密码重置**：补 CLI/UI 重置流程（1.5 期间出现"忘记密码进不去"）。
7. **性能与收尾**：大数据量分页/索引、`admin_manage.py` 二次拆分、文档。

**架构基线：** 延续 1.5.1 的轻量 `http.server` + Mixin 模块结构（[admin_server.py](src/legal_mcp/admin_server.py) + `admin_render/users/manage/database/misc/common`）。导入复用 [import_pipeline](src/legal_mcp/import_pipeline) 的 `import_file()`。不引入前端框架。

---

## 现状关键事实（已核对）

| 事实 | 位置 | 对方案的意义 |
|------|------|------|
| `import_file(path, *, database_path) -> ImportReport` 已可用 | [import_pipeline/__init__.py](src/legal_mcp/import_pipeline/__init__.py) | 导入落库直接调它，无需重写管线 |
| `ImportReport`：`source_rows / counts[entity][outcome] / errors / warnings / failed` | [import_pipeline/report.py](src/legal_mcp/import_pipeline/report.py) | 现成的结果模型，直接渲染报告 |
| Schema 走 `executescript(create table if not exists …)` + `schema_version`(当前 16)，**无 ALTER 迁移** | [db.py](src/legal_mcp/db.py)、[schema.sql](src/legal_mcp/schema.sql) | 旧库新列补不上 → 真实库报 `no such column: handler`。必须建迁移机制 |
| 主体目前是查询期聚合（contracts/licenses 实体字段去重） | `admin_database.py` `_aggregate_entities` | 升级为可下钻；是否建表见"开放决策" |
| 管理页行操作已带 `_return`（保持位置）、复选框增强已在编辑页 | `admin_manage.py` | 批量操作复用既有 toolbar/复选框模式 |

---

## 开放决策（建议默认，待确认）

> 为不阻塞成文，先给推荐默认；实现前如有异议再调整。

| 议题 | 推荐默认 | 备选 |
|------|---------|------|
| **导入是否落库前先预览** | 是：先 dry-run 出报告，用户点"确认导入"才写库 | 直接落库（更快但易误操作） |
| **主体建模** | **建 `entities` 表**（规范化 + 与 contracts/licenses 关联），保留聚合视图作为回填来源 | 仍只读聚合 + 下钻（工作量小，但不能管理/纠错） |
| **迁移策略** | 写**显式编号迁移脚本**（0001…），按 `schema_version` 顺序执行；幂等、有 `PRAGMA user_version` 兜底 | 自动 diff 列（复杂、风险高，不采用） |
| **批量操作触达范围** | 用户 Tab（停用/启用/授权）、API Key Tab（吊销）；权限/分组本期不做批量 | 全 Tab 批量 |
| **审计落库范围** | 所有 admin 写路由（create/edit/status/password/revoke/delete/import/alias） | 仅高危操作（停用/改密/吊销/导入） |

---

## 假设

- 延续 local/team 部署模式与现有权限模型。
- 迁移需对**已有生产库**（Docker `./data/legal.db`、本地 `~/.legal-mcp/legal.db`）安全：升级前自动备份、逐步 ALTER、失败回滚。
- 导入文件类型限 `.xlsx/.csv`，单文件、大小受限（沿用 1.5.1 的 `_MAX_UPLOAD_BYTES`）。
- 主体建表为增量迁移，不破坏既有 contracts/licenses 数据。

## 非目标

- 不引入前端框架 / SPA。
- 不做通用 SQL 编辑器、不做导入的字段映射可视化编辑器（1.6.1 候选）。
- 不做多租户。
- 不重写权限模型。

## 成功标准

- 旧库（含缺 `handler` 列的库）启动后台能**自动迁移到当前 schema 版本**且数据无损；`serve-admin` 不再报 `no such column`。
- Database 页拖拽 `.xlsx/.csv` → 看到**预览报告**（每实体 created/updated/skipped/failed + 错误清单）→ 确认后**落库**并刷新摘要；出错整单回滚。
- 主体可在 Database 页**下钻**（某主体关联了哪些项目/合同/许可），（按默认）可建/改/合并主体。
- 管理页可多选用户**批量停用/启用/授权**、多选 Key **批量吊销**，操作后保持位置。
- 每次后台写操作在审计中留痕（操作者/时间/动作/对象/摘要），可在 Audit 页筛选查看。
- team 模式可通过 CLI/UI **重置 admin 密码**。
- 全部新路径有集成测试；现有 1.5.x 测试保持全绿。

---

## 发布形态（Phases）

### Phase 1：Schema 迁移框架（阻塞项，先做）

- [ ] 在 [db.py](src/legal_mcp/db.py) 增加迁移执行器：读 `schema_version.version`，按编号顺序应用 `migrations/NNNN_*.sql`（或 Python 迁移），每步在事务内，结束写新版本号。
- [ ] 新建 `src/legal_mcp/migrations/` 目录，写**回填迁移**：对历史缺列（如 `contracts.handler`、其它 1.3→1.5 期间新增列）做 `ALTER TABLE ADD COLUMN IF NOT EXISTS` 等价逻辑（SQLite 需先探测 `PRAGMA table_info` 再决定是否 ADD）。
- [ ] `initialize_database` 升级流程：①若库已存在且版本落后 → 先**备份**（`legal.db.bak-<version>`）②逐步迁移 ③失败回滚并报清晰错误。
- [ ] 兼容全新库：`create table` 全量建好后直接置为最新版本，跳过历史迁移。
- [ ] 验证：用一个"旧版本"库样本（缺 `handler`）跑迁移 → 列补齐、数据保留、版本号更新；`serve-admin` 正常启动。

### Phase 2：导入真正落库（dry-run → 确认 → 提交）

接 1.5.1 的占位端点 `/admin/database/import`（multipart 解析已具备）。

- [ ] 上传后落临时文件，调用一个**dry-run 版** import：复用 [import_file](src/legal_mcp/import_pipeline/__init__.py) 的读取/适配/校验，但 `validate` 后**不 commit**（或在子事务里 upsert 后 rollback），产出 `ImportReport` 用于预览。
- [ ] 预览页：按实体展示 `source_rows / created / updated / skipped / failed` 计数 + 错误清单（文件/行/字段/错误码/消息），给「确认导入」「取消」。
- [ ] 「确认导入」：用同一文件正式 `import_file()` 落库，整单**事务**，任何错误回滚；成功后回 Database 摘要并 flash 计数。
- [ ] 临时文件清理；并发/重复提交防护（确认用一次性 token 或 hash 校验文件一致）。
- [ ] 支持多文件？本期单文件；多文件留 1.6.1。
- [ ] 验证：导入 `contracts.xlsx` 样本 → 预览计数正确 → 确认后行数入库、摘要更新；含错误行的文件 → 预览报错、库不变。

### Phase 3：主体（实体）一等公民

（按默认决策：建 `entities` 表）

- [ ] 迁移新增 `entities` 表（name、规范名、类型、备注、来源）+ 关联表（entity ↔ project/contract/license，或在子表加 `entity_id` 外键）。
- [ ] 回填：从现有 contracts/licenses 实体字段聚合去重写入 `entities`，建立关联（用 1.5.1 的聚合逻辑做种子）。
- [ ] Database 页主体区升级：分页 + 搜索 + **下钻**（点主体看其关联的项目/合同/许可清单）。
- [ ] 主体管理：重命名、合并重复主体（把两条合并、关联改指）、改类型/备注。合并是高频纠错需求。
- [ ] 验证：聚合数与建表后一致；合并后关联正确改指、无悬空。

### Phase 4：批量操作

- [ ] 管理页"用户"Tab：行首加复选框 + 顶部**批量操作条**（选中 N 项 → 批量停用 / 启用 / 授权某项目 / 加入某分组）。
- [ ] "API Key"Tab：批量吊销选中 Key。
- [ ] 服务端批量端点：在一个事务内逐条执行，部分失败给出"成功 X / 失败 Y + 原因"，操作后**保持当前 Tab/页/筛选**（沿用 `_return`）。
- [ ] 复用 1.5.1 编辑页的"全选可见/反选/计数"客户端增强。
- [ ] 验证：选 3 个用户批量停用 → 全部 disabled、其 Key 失效、停在原页；批量授权幂等。

### Phase 5：后台变更审计

- [ ] 迁移新增 `admin_audit`（或复用 `audit_events`）表：actor_user_id、timestamp、action、target_type、target_id、summary、可选 before/after JSON。
- [ ] 在各写处理器（create/edit/status/password/revoke/delete/import/alias/批量）统一记一笔（用一个 `record_admin_action()` 助手，放 `admin_render` 或 `admin_common`）。改密只记"重置了密码"，**不存明文/哈希**。
- [ ] Audit 页增加"后台操作"视图/筛选（按 actor/动作/对象），分页复用既有 `_pager`。
- [ ] 验证：停用某用户后审计出现一行含 actor 与 target；改密不泄露密码。

### Phase 6：团队模式密码重置

- [ ] CLI：`legal-mcp admin reset-password --email … --db …`（交互或 `--password`），写 `password_hash`。
- [ ] （可选）UI：admin 给其它 admin 重置（1.5.1 编辑页已有"设置密码"，确认对 admin 角色生效即可）。
- [ ] 文档：Docker 场景 `docker exec … legal-mcp admin reset-password …` 示例。
- [ ] 验证：重置后用新密码可登录后台。

### Phase 7：性能与收尾

- [ ] 大数据量：对 users/api_keys/projects/contracts 常用排序/筛选列加索引；分页在数千行级评估是否需游标分页（否则保留 offset）。
- [ ] `admin_manage.py`（~944 行）二次拆分：`admin_manage_tabs.py`（四 Tab 渲染）+ `admin_manage_edit.py`（编辑/维护处理器）两个 mixin，延续解耦。
- [ ] 文档：更新 README / team-deployment（导入落库、主体管理、批量、迁移与备份、密码重置）。
- [ ] 全量回归 `pytest`；浏览器手测脚本。

---

## 建议文件改动

- [db.py](src/legal_mcp/db.py) + 新增 `src/legal_mcp/migrations/`：迁移执行器 + 历史回填 + 备份。
- [schema.sql](src/legal_mcp/schema.sql)：新增 `entities`/关联表与 `admin_audit`，版本号递增。
- `admin_database.py`：导入预览/确认/报告、主体下钻与管理。
- `admin_manage.py`（后拆分）：批量操作条与端点。
- `admin_render.py`/`admin_common.py`：`record_admin_action()` 助手、批量复选 UI 复用。
- [cli.py](src/legal_mcp/cli.py)：`admin reset-password`。
- `admin_misc.py`：Audit 页后台操作视图。
- `tests/`：迁移、导入落库（含失败回滚）、主体合并、批量操作、审计留痕、密码重置。

## 测试计划

- `pytest`（admin_server / identity / import_pipeline / db 迁移 / cli / docs）
- 迁移：旧库样本（缺列）→ 迁移后列齐、数据在、版本更新、有备份。
- 导入：正常文件预览计数正确→确认入库；错误文件预览报错、库不变；确认后整单回滚验证。
- 主体：聚合 vs 建表一致；合并后关联改指无悬空。
- 批量：多选停用/授权/吊销，事务与位置保持。
- 审计：每类写操作留痕、改密不泄露。
- 手测：Docker 重建后真实库自动迁移成功并能导入。

## 风险与权衡

- **迁移对生产库**：最高风险。必须升级前备份 + 逐步 ALTER + 失败回滚 + 充分用旧库样本测试。SQLite 的 ALTER 受限（不能改/删列），合并表需"新建-拷贝-改名"模式，谨慎。
- **导入 dry-run 的事务语义**：SQLite 嵌套事务用 SAVEPOINT；dry-run upsert 后 rollback 要确保不污染连接状态。
- **主体合并**：跨多子表改指要在一个事务内，避免悬空外键（开启了 `PRAGMA foreign_keys=ON`）。
- **批量部分失败**：要给清晰的"成功/失败"反馈而非整单失败或静默吞错。
- **审计体量**：写操作多时审计表增长，需保留期/分页；不要存敏感值。

## 依赖与顺序

Phase 1（迁移）是其它涉及 schema 变更（Phase 3 主体、Phase 5 审计）的前置。建议顺序：1 → 2 → (3、5 并行) → 4 → 6 → 7。

## 留给后续版本的开放项

- 导入字段映射可视化、多文件批量导入、导入历史/撤销。
- 主体去重的智能建议（相似名聚类）。
- 大规模数据的游标分页与全文检索。
- 后台操作的审计导出与告警。
</content>
</invoke>
