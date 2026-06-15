# v0.3.5 本地 / 自托管模型路径 — 开发计划

> 配套：权限感知 MCP 网关计划 §7（v0.3.5）、§反向风险（第 601 行「本地模型一等路线项」）。
> 北极星：*小问题别来问我，问 AI；AI 只回答你有权知道的那一部分。*
> v0.3.5 的存在理由：**最该自托管本网关的人，恰恰是不能把数据发给外部 LLM 的人**。
> 问答必然要过一个 LLM，因此必须能把那个 LLM 也留在内网，否则对核心受众直接破功。
>
> **本版目标（明确）：一个本地模型要能驱动服务端的*全部*功能** —— 不是"也能用本地
> 模型"，而是把本地模型当作可以完全替代云端模型的一等后端，服务端凡是过模型的能力
> 都能由它驱动。

## 架构前提：服务端只有一个模型接缝

盘点结论（[agent_graph.py:417](../../src/legal_mcp/agent_graph.py) 是唯一调用点）：
**所有服务端模型调用都收口在 `AIProvider.complete()` 这一个接缝**，由
[agent_graph.py](../../src/legal_mcp/agent_graph.py) 的 planning 节点经
`ConfiguredAIProvider`（[tools.py:131](../../src/legal_mcp/tools.py)）消费；唯一过模型的
对外功能是 `agent_query`，`structured_query` 是确定性路径、不过模型。**没有第二处原生
模型调用。** 因此「让本地模型驱动全部功能」在架构上很干净：只要把 provider 构造那一处
解锁（任务 A），全部功能自动跟着走本地模型——不需要逐个功能去改。

这同时带来一个**必须守住的隐患**：既然只有一个接缝，这个接缝一旦静默降级，就是"全部
功能"一起静默失效。见任务 C。

## 0. 现状盘点（不是从零造）

OpenAI 兼容的服务端推理底座已经在仓库里：

- [ai_provider.py](../../src/legal_mcp/ai_provider.py) — `OpenAICompatibleProvider` 已接受
  `base_url`，底层用 `langchain_openai.ChatOpenAI`。指向 `http://localhost:11434/v1`
  （Ollama）或 vLLM 的 OpenAI 端点在传输层**已经能跑**。
- [agent_config.py](../../src/legal_mcp/agent_config.py) — 配置已有 `ai_provider` /
  `ai_model` / `ai_base_url` / `ai_api_key`，支持 env 覆盖 + DB `agent_settings` 持久化。
- [admin_misc.py](../../src/legal_mcp/admin_misc.py) — 管理后台已有「Model Configuration」表单。

**结论：传输能力已具备，缺的是「无 key / 本地」这条路被几处硬假设挡死，外加端到端验证与承诺文档。**
这版的工作量小、风险低，重点是把假设拆干净并证明它真能全程不出内网。

## 1. 范围与非目标

**做：**
- 让「没有 API key 的本地端点」成为合法配置（Ollama 默认无鉴权）。
- 让 JSON 输出在不支持 OpenAI `response_format` 的本地模型上也能稳。
- 管理后台能选「本地预设」（Ollama / vLLM / 自定义 OpenAI 兼容）。
- `doctor` / 启动期对配置的 AI 端点做一次连通性探针。
- 一个真实的本地模型端到端 demo（Ollama）。
- 承诺矩阵：**仅当配置本地模型时**才承诺「问答全程不出内网」。

**不做（防止范围蔓延）：**
- 不内置/打包任何模型权重，不做模型下载器。
- 不做多 provider 抽象框架（Anthropic / Gemini 原生 SDK 等）——一切走 OpenAI 兼容。
- 不做模型质量评测 / 路由 / 回退链。
- 不碰授权逻辑（v0.3.5 与 record/field 授权解耦，授权在 LLM 之外）。

## 2. 已识别的硬假设（这是真正的活）

| # | 位置 | 现状假设 | 为什么挡住本地模型 |
| - | --- | --- | --- |
| H1 | [agent_config.py:36](../../src/legal_mcp/agent_config.py) `enabled=bool(api_key)` | 「有 `OPENAI_API_KEY` 才算启用 agent」 | Ollama 无 key → agent 直接判为未启用 |
| H2 | [ai_provider.py](../../src/legal_mcp/ai_provider.py) `provider_from_config` / `build_ai_provider` | `if not config.ai_api_key: return None/Noop` | 无 key 的本地端点拿不到 provider |
| H3 | [ai_provider.py:54](../../src/legal_mcp/ai_provider.py) `response_format={"type":"json_object"}` | 假定端点支持 OpenAI JSON mode | 多数 Ollama 模型不识别此参数，可能报错或忽略 |
| H4 | [admin_misc.py:97](../../src/legal_mcp/admin_misc.py) provider 下拉只有 `openai_compatible`，默认 `gpt-4.1-mini` | UI 把云端当唯一选项 | 运营者无从在后台配本地端点 |
| H5 | 无端点连通性检查（[doctor.py](../../src/legal_mcp/doctor.py) 不探 AI） | 配错本地端点只能在问答时炸 | 自托管者第一步就踩坑且无定位线索 |
| H6 | [agent_graph.py:391](../../src/legal_mcp/agent_graph.py) `if ai_provider is None` + `NoopAIProvider` 返回 `"{}"` | 拿不到 provider 就静默走空响应 | **唯一接缝静默降级 = 全部功能一起静默失效**，用户以为"本地模型不工作"却无定位线索 |

## 3. 任务分解（每步带验收）

### A. 解除「必须有 key」的假设（核心解锁）
- `agent_config`：`enabled` 不再单看 `OPENAI_API_KEY`；改为「有可用端点即启用」——
  本地模型场景下 `ai_base_url` 指向本地且 `ai_provider != none` 即视为启用。
  为本地端点引入一个占位 key（如 `"local"`），因为 `ChatOpenAI` 需要非空 key，
  但**不**把「有 key」当作启用判据。
- `provider_from_config` / `build_ai_provider`：当 `ai_base_url` 指向本地端点时，
  允许空/占位 key 构造 provider，而非返回 `None`/`Noop`。
- **验收**：单测 `test_agent_config` + 新 `test_ai_provider` —
  给定 `ai_provider=openai_compatible, ai_base_url=http://localhost:11434/v1, ai_api_key=""`，
  `enabled is True` 且 `build_ai_provider` 返回 `OpenAICompatibleProvider`（非 Noop）。

### B. JSON 输出在本地模型上稳住
- 把 `response_format={"type":"json_object"}` 改为**可选**：新增配置项
  （如 `ai_json_mode: "auto"|"on"|"off"`，默认 `auto`）。本地预设默认 `off`，
  转而依赖 prompt 内「只输出 JSON」约束 + 已有的 `_strip_code_fence` 兜底解析。
- 复用已有 `_strip_code_fence`；如需更稳，加一个「提取首个 `{...}` 块」的容错解析，
  仅在 json mode 关闭时启用。
- **验收**：单测喂入「带 markdown 围栏 / 带前后散文」的伪模型响应，解析仍得到合法 plan dict；
  `response_format` 关闭时不再传该 kwarg（用 mock 断言 `ChatOpenAI` 调用参数）。

### C. 配了本地模型就不许静默降级（守住唯一接缝）
- 既然全部功能只过一个接缝，这个接缝静默走 Noop 就是"全部功能一起悄悄失效"。
- 当配置判定为「已启用本地模型」时，[agent_graph.py:391](../../src/legal_mcp/agent_graph.py)
  的 `ai_provider is None` 分支与 `ConfiguredAIProvider` 拿不到 provider 的情形，
  应**抛明确错误**（指向端点/配置），而非返回 `NoopAIProvider` 的 `"{}"` 空计划。
- 保留 Noop 仅用于「显式 `ai_provider=none`」的真·禁用场景，不让它兜配错。
- **验收**：单测——本地端点已配但 provider 构造失败时，`agent_query` 返回可定位的错误
  （含端点信息），而非一个空披露结果；`ai_provider=none` 时仍走 Noop 不报错。

### D. 管理后台本地预设
- [admin_misc.py](../../src/legal_mcp/admin_misc.py) Model Configuration 表单：
  provider 下拉新增 `Ollama (local)` / `vLLM (local)` / `OpenAI-compatible (custom)`；
  选本地预设时自动填 `ai_base_url`（Ollama=`http://localhost:11434/v1`，vLLM=`http://localhost:8000/v1`）、
  占位 key、json_mode=off；API key 字段对本地预设变为可选。
- 三种预设本质都映射到同一个 `openai_compatible` 后端，只是默认值不同——**不**新增 provider 类型分支。
- **验收**：后台保存本地预设 → `agent_settings` 行写入正确 base_url/占位 key；
  `load_agent_config` 读回后 `enabled is True`。手动过一遍后台表单。

### E. 端点连通性探针
- [doctor.py](../../src/legal_mcp/doctor.py) 增一项 AI 端点检查：对配置的 `ai_base_url`
  发一次最小请求（列模型或一次极短 completion），区分「连不上 / 401 / 模型不存在 / OK」。
- 启动期（[startup.py](../../src/legal_mcp/startup.py)）可选打印一行 AI 后端状态（非致命）。
- **验收**：`legal-mcp doctor` 在本地端点未起时给出明确「连不上 <url>」而非堆栈；
  端点正常时报 OK 并回显 model 名。

### F. 本地端到端 demo + 承诺矩阵
- 在 [examples/legal-demo](../../examples/legal-demo) 基础上加一份本地模型跑法说明：
  `ollama pull <一个小模型>` → 配后台/env → 跑既有 demo 问题，得到与云端一致的**授权裁剪**结果。
  明确标注模型名、Ollama 版本、最小硬件假设。
- 新建/更新承诺矩阵（plan §601 要求）：明确「问答不出内网」**仅在配置本地模型时成立**；
  云端 provider 下问题文本会出内网。更新 [README](../../README.md) 受众段落与
  [threat-model.md](threat-model.md)，把「LLM 端点选择」列为部署者的显式隐私决策。
- **验收**：照文档从零（无外网 LLM）跑通 demo 三用户（legal/business/auditor）不同披露，
  全程无对外 LLM 网络请求（doctor 探针 + 离线断网验证）；文档里能一句话回答
  「我的问题文本会不会出内网？」且与代码默认行为一致。

## 4. 建议执行顺序

A（解锁）→ B（稳 JSON）→ C（不许静默降级）→ E（探针）→ D（后台 UX）→ F（demo + 承诺）。
A/B/C 是必须先行的真活（解锁 + 不静默坏掉是"驱动全部功能"的硬底线）；
D 是 UX 糖；E 让 D/F 可定位；F 是「证明 + 承诺」收口。

## 5. 风险

- **本地小模型 JSON / planning 质量不稳**：planner 产出的是受约束 QueryPlan，
  解析容错（B）能挡格式问题，但模型太弱可能产出语义差的 plan。
  缓解：demo 选一个已知能产结构化输出的模型，并在文档注明「模型能力下限」。
- **`langchain_openai` 对空 key 的行为**：需实测占位 key 是否被 `ChatOpenAI` 接受；
  若不接受，则统一塞 `"local"` 之类非空占位。A 步第一件事就是验证这个。
- **范围蔓延到「多 provider 框架」**：守住「一切走 OpenAI 兼容」，不引原生 SDK。

## 6. 与其它版本的关系

- 与 **v0.4 身份穿透** 正交：v0.3.5 只换「LLM 在哪」，不动「身份从哪来 / 授权怎么裁」。
- 与 **v0.4.5 内容驱动授权** 正交：授权发生在 LLM 之外，换本地模型不影响 record/field 两道门。
- 因此 v0.3.5 可独立插入、独立发布，不阻塞也不被阻塞。
