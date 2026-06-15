# Legal-MCP — 权限感知的 MCP 网关

[English](README.md) | 简体中文

**Legal-MCP 是一个开源、可自托管、权限感知的 MCP 网关。** 它位于 AI 客户端和你
现有的数据源之间：回答自然语言问题，只披露提问用户有权看到的字段，并对每一次
披露留痕审计。它不会把你的业务数据搬走重存。

**北极星：** 小问题别来问我，问 AI；AI 只回答你有权知道的那一部分。

当前版本：**v0.4.9**。

## 工作原理

每个问题都走同一条完全在服务端执行的管线：

1. **识别** 提问的最终用户（个人 API key、可信代理头，或本地 operator）。
2. **规划** —— 服务端配置的大模型把自然语言问题转成一个受约束的 JSON
   `QueryPlan`（域、过滤条件、返回字段、limit），且只能引用已注册字段目录中的
   字段。没有 SQL，没有自由检索。
3. **授权** —— 按用户身份、数据域、记录范围、字段级授权校验计划。默认拒绝：
   未授权字段直接拒绝，而不是打码。
4. **读取** —— 通过只读连接器从你*现有*的数据源取数（飞书多维表格、内置的
   SQLite 演示库……）。过滤条件下推到源端；网关持有数据行的时间不超过一次已
   授权的查询。
5. **披露** —— 只把已授权的返回字段交给 AI 客户端。
6. **审计** —— 记录每一次允许、拒绝和披露（谁问了什么、执行了哪个计划、返回
   了哪些字段和记录、来自哪个数据源）。

AI 客户端只看到一个 MCP 工具 —— `agent_query`，永远拿不到数据库句柄、SQL、
模型工具或可执行的计划。

### 非目标

- 不做你业务事实（项目、合同、证照……）的权威数据库，不提供录入/CRUD 界面。
- 不做实体主数据（MDM）或别名治理。
- 不做 BI / 报表平台，也不做文档知识库。
- 不为所有可能的数据源维护官方连接器。

法务领域只是**参考演示**（旗舰示例），不是产品边界。可复用的内核是
权限授权 + 连接器 + 审计。

> **数据边界：** 网关承诺*原始数据留在源端*、*只披露已授权字段*。它**不**承诺
> 答案永远不出你的网络 —— 使用外部 AI 客户端时，答案会进入该模型的上下文。
> 想要全程不出内网，请使用本地模型（见
> [examples/legal-demo/LOCAL-MODEL-DEMO.md](examples/legal-demo/LOCAL-MODEL-DEMO.md)；
> `legal-mcp doctor --probe-ai` 可验证端点连通性）。

## 安装

```sh
uv tool install --upgrade legal-mcp && legal-mcp setup
```

或使用自带安装脚本（等价，可透传客户端参数）：

```sh
./install.sh --client cursor
# 在本仓库 checkout 中做本地开发：
LEGAL_MCP_PACKAGE=. ./install.sh --client cursor
```

`legal-mcp setup --client CLIENT` 会写入本地 stdio MCP 配置。支持的客户端：
`claude`（Claude Desktop）、`claude-code`、`cursor`、`windsurf`、`vscode`、
`codex`、`generic`。配置坏了随时重跑 setup 修复。

## 单机快速上手

```sh
# 1. 把台账导入本地治理/演示库
legal-mcp import path/to/project-ledger.xlsx

# 2. 配置服务端 planner 模型（任何 OpenAI 兼容端点，包括本地 Ollama）
export LEGAL_MCP_AI_PROVIDER=openai-compatible
export LEGAL_MCP_AI_MODEL=qwen2.5:14b
export LEGAL_MCP_AI_BASE_URL=http://127.0.0.1:11434/v1
export LEGAL_MCP_AI_API_KEY=unused-for-local

# 3. 健康检查，然后启动 stdio MCP 服务
legal-mcp doctor
legal-mcp serve
```

在任意 MCP 客户端提问：`MOON的法务BP是谁`、`指间山海的官网`、`我能访问哪些项目`。

## CLI 一览

| 命令 | 用途 |
| --- | --- |
| `legal-mcp serve` | stdio MCP 服务（本地单用户，完整 operator 权限） |
| `legal-mcp serve-http` | 团队共享的 HTTP MCP 服务 |
| `legal-mcp serve-admin` | 管理后台（用户、组、授权、API key、数据源） |
| `legal-mcp admin` | 管理引导命令（如 `admin create-user`） |
| `legal-mcp proxy` | 本地 stdio ⇄ 远程 HTTP 的桥接，供团队成员使用 |
| `legal-mcp setup` | 写 MCP 客户端配置（本地或 `--remote-url`） |
| `legal-mcp import` | 导入 CSV/XLSX 到本地库 |
| `legal-mcp doctor` | 安装 / schema / 客户端配置 / AI 端点健康检查 |
| `legal-mcp scaffold-connector` | 从飞书多维表格真实列生成连接器配置草稿 |

## MCP 工具面

生产环境（`--agent-public-only` 或 `LEGAL_MCP_AGENT_PUBLIC_ONLY=true`）下，
`tools/list` 只暴露：

- **`agent_query`** —— 自然语言读查询。服务端 LangGraph 工作流完成规划、校验、
  授权、执行和答案整形。外部 AI 客户端无法直接访问数据库工具 —— 项目、合同、
  证照及跨域检索都在服务端内部运行，过滤字段和返回字段都先过权限检查再整形
  给客户端。

不加该开关时，目录还包括 `describe_my_access`（你可见的项目和可读字段）、
`structured_query`（可信客户端直接提交已约束的计划，仍走同样的校验/授权路径）
和 `agent_write`（仅提案，不写数据）。

### 值得了解的 agent 行为

- **回合隔离。** 每次 `agent_query` 调用都是独立回合：从本回合的问题重新规划，
  即使同一会话 `thread_id` 也绝不会重放上一回合的计划。历史回合只以窄而安全的
  会话上下文（已向提问者披露过的实体标识和字段名）输入 planner，所以
  `它的官网呢` 这类追问能解析上一回合的实体而不继承状态。
- **身份解析。** 裸项目 token（`MOON`、`月之子`、`nova`、`山海`）会同时匹配
  域的*所有*身份字段（代号或名称、全称或简称），按精确度排序；真正有歧义时
  返回排序后的候选列表（`identity_disambiguation`），而不是瞎猜。
- **操作符下推。** `eq` / `contains` / `in` 过滤条件会翻译成源端原生查询
  （飞书过滤 API、SQLite `LIKE`），模糊搜索大小写不敏感。更复杂的操作符
  （`is_empty`、`date_*`）会明确报告不支持，而不是静默丢弃。
- **多源回退（v0.4.9）。** 一个域可由多个数据源按优先级提供。主源有结果就直接
  作答（结果带 `data_source` 标注）；主源为空则逐源回退。若*多个*源都有结果，
  网关返回 `source_disambiguation`（源名 + 记录数，不返回数据行），由 agent 问
  用户采信哪个源；下一回合通过计划的可选 `data_source` 字段锁定选择。未知的源
  名 fail-closed 报错。
- **可诊断的空结果。** 已授权但为空的结果在审计中标注 `no_rows`，与拒绝和
  planner 失败区分。每回合的规划尝试记录在 `agent_steps`（按 `turn_id` 键控）。
- **有界计划修复。** planner 只在目录校验出错后重试受约束的 JSON 计划，绝不
  重试授权拒绝。
- **字段目录门控。** 源端新增列不会因为存在就可查。必须在连接器配置（或字段
  目录）中声明、按需配别名，并被授权覆盖。

## 接入你的数据（连接器）

一份可评审、可入库的 YAML 文件声明哪些域来自哪些源。文件中绝不含密钥 ——
source 只引用存放凭证的*环境变量名*。配置错误或不完整会 fail-closed：服务拒绝
启动。带注释的完整示例见
[examples/connectors/feishu-bitable.connector.yaml](examples/connectors/feishu-bitable.connector.yaml)。

```yaml
version: 1
sources:
  - type: feishu_bitable            # project 域，实时来自飞书
    app_token: bascnYourAppToken    # 非密钥资源 id
    app_id_env: FEISHU_APP_ID       # 密钥来自环境变量
    app_secret_env: FEISHU_APP_SECRET
    domains:
      - name: project
        table_id: tblYourTableId
        fields:                     # 只有声明的字段才可被查询
          - {name: project_code, is_identity: true, aliases: ["项目代号"]}
          - {name: name, is_identity: true, aliases: ["项目名称"]}
          - {name: legal_bp, aliases: ["法务BP"]}
  - type: sqlite_demo               # 本地库作为 project 的命名回退源
    name: local-db
    domains: [project]
```

传给任一服务即可：`legal-mcp serve-http … --connector config.yaml`。

- **源类型：** `feishu_bitable`（实时飞书/Lark 多维表格；国际版租户用
  `base_url: https://open.larksuite.com`）和 `sqlite_demo`（本地治理库）。
- **同域多源：** 声明顺序即优先级；服务同一域的多个源必须有不同的 `name`。
  未被任何源声明的域由本地 SQLite 演示库提供。
- **脚手架：** `legal-mcp scaffold-connector --app-token … --table
  project:tblXXXX` 内省真实列（只取列名，绝不取值），生成草稿配置供人工评审。
- **每域记录范围：** `by_governed_code`（按治理项目代号决定行可见性 ——
  默认）、`by_owner`（用户只能看 owner 列匹配自己身份的行；下推到源端）、
  `none`（只有字段门禁）。
- 授权和审计永远在网关里、包在连接器外面。连接器是只读的笨管道；字段门禁和
  记录范围对每个源单独生效，回退时也不例外。

## 身份与授权

- **个人 API key**（`lmcp_…`），由管理后台签发；每个请求按该用户授权。key 可
  重置；停用用户立即吊销其访问。
- **可信代理头** —— `serve-http --trusted-identity-header X-Auth-User
  --trusted-proxy 10.0.0.0/8` 允许 SSO 反向代理声明最终用户（经
  `users.external_subject` 映射）。来自不可信对端的头、或身份冲突，一律
  fail-closed 拒绝。
- **本地 operator** —— `legal-mcp serve`（stdio）以完整本地权限运行。
- 授权存在库里：用户、组、字段级授权、项目级记录范围，统一在管理后台
  （`serve-admin`）管理。无法识别身份的请求得到零行，绝不是"全部"。

管理后台保持监听 `127.0.0.1`（走 SSH 隧道），或置于 TLS 反向代理之后。

## 团队部署

内网主机跑一个共享 HTTP 服务，每个成员通过本地 stdio proxy 接入。完整指南：
[Docs/team-deployment.md](Docs/team-deployment.md)。

```sh
# 运维侧：引导管理员，启动两个服务
legal-mcp admin create-user --email admin@example.com --role admin \
  --display-name "Admin" --password "…" --db /data/legal.db
legal-mcp serve-admin --host 127.0.0.1 --port 8766 --db /data/legal.db
legal-mcp serve-http --host 0.0.0.0 --port 8765 --db /data/legal.db \
  --audit-log /data/audit.jsonl --agent-public-only --connector /data/connector.yaml

# 成员侧：用个人 key 把客户端指向共享服务
export LEGAL_MCP_API_KEY="lmcp_replace_with_the_user_api_key"
legal-mcp setup --client codex \
  --remote-url http://legal-mcp.internal:8765/mcp --api-key "$LEGAL_MCP_API_KEY"
```

Claude Code 用户把 `--client codex` 换成 `--client claude-code`。包含主机名、
客户端路径、token 或真实数据的部署笔记请放在 Git 之外的本地文档里。

### Docker

`docker-compose.yml` 用 `legal-mcp:v0.4.9` 镜像运行网关（`:8765`）和管理后台
（`:8766`），`./data` 挂载到 `/data`（数据库、审计日志、连接器 YAML）。
`pull_policy: never` 保证启动不依赖网络。

```sh
docker compose -f docker-compose.yml -f docker-compose.build.yml build legal-mcp
docker compose up -d
```

弱网主机可一次构建、打包传输：
`scripts/prepare-offline-images.sh` / `scripts/load-offline-images.sh`。

## 服务端模型与可观测性

planner 模型只在服务端配置 —— MCP 调用方永远不提供模型工具：

| 变量 | 含义 |
| --- | --- |
| `LEGAL_MCP_AI_PROVIDER` | `openai` 或 `openai-compatible` |
| `LEGAL_MCP_AI_MODEL` | planner 模型名 |
| `LEGAL_MCP_AI_BASE_URL` | 端点（支持内网 / 本地 Ollama） |
| `LEGAL_MCP_AI_API_KEY` | 端点凭证 |
| `LEGAL_MCP_AI_JSON_MODE` | 支持时强制 JSON 对象输出 |

管理员也可在 `/admin/agent-settings` 管理这些设置；环境变量优先于库中设置。

追踪只用**自托管 Langfuse**（`docker-compose.langfuse.yml`；设置
`LANGFUSE_PUBLIC_KEY`、`LANGFUSE_SECRET_KEY`、
`LANGFUSE_BASE_URL=http://127.0.0.1:3000`）。

审计存于治理库（`audit_events`、按字段的 `audit_disclosures`、完整请求/响应在
`audit_event_details`），外加只追加的 JSONL 日志（`--audit-log`）。

## 演示

- [examples/legal-demo](examples/legal-demo) —— 含种子数据的端到端法务演示。
- [examples/legal-demo/FEISHU-MIXED-DEMO.md](examples/legal-demo/FEISHU-MIXED-DEMO.md)
  —— `project` 实时来自真实飞书多维表格，其余来自 SQLite。
- [examples/legal-demo/LOCAL-MODEL-DEMO.md](examples/legal-demo/LOCAL-MODEL-DEMO.md)
  —— 用 Ollama 全程不出内网。

## 开发

```sh
uv sync
uv run pytest -q
```

真实客户数据、试用数据库、导出文件，以及含主机名/token 的部署笔记一律不入
Git —— 仓库刻意只带空的数据目录。版本历史见 [CHANGELOG.md](CHANGELOG.md)，
安全策略见 [SECURITY.md](SECURITY.md)，参与开发见
[CONTRIBUTING.md](CONTRIBUTING.md)。
