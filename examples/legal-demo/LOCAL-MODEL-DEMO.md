# 本地模型 demo（问答全程不出内网）

[LIVE-DEMO.md](LIVE-DEMO.md) 用确定性 fast-path 证明授权，**不过 LLM**。本篇相反：
把网关指向一个**本地模型**（Ollama），用一个需要 LLM 规划的问题，证明
**同一个本地模型能驱动整条 agent_query 路径**，且整段问答不离开内网。

> 承诺边界见 [承诺矩阵](../../Docs/strategy/commitment-matrix.md)：只有在本地模型上，
> 才能说"问答全程不出内网"。

## 前置：起一个本地模型

```sh
# 装好 Ollama (https://ollama.com)，拉一个能产结构化输出的小模型。
ollama pull qwen2.5          # 7B 即可；建议 ≥8GB 内存
ollama serve                 # 暴露 OpenAI 兼容端点于 http://localhost:11434/v1
```

任意 OpenAI 兼容端点同理（vLLM 默认 `http://localhost:8000/v1`）。

## 配置网关用本地模型（无需 API key）

两种方式，等价：

```sh
# A. 环境变量（自托管端点不需要 key；占位 key 在运行时自动补）
export LEGAL_MCP_AI_BASE_URL=http://localhost:11434/v1
export LEGAL_MCP_AI_MODEL=qwen2.5
# 可选：LEGAL_MCP_AI_JSON_MODE=auto|on|off（默认 auto：自定义 base_url 即关闭
#       OpenAI json mode，靠提示词 + 解析兜底，兼容更多本地模型）
```

```text
# B. 管理后台 → Agent Settings → AI Provider 选 "Ollama (local)"
#    base URL 自动填好，API Key 留空即可。
```

先验证端点连得上、模型名对得上：

```sh
legal-mcp doctor --probe-ai
# ok: AI endpoint healthy: http://localhost:11434/v1 (model qwen2.5)
# 端点没起时会明确报 "AI endpoint unreachable: …"，而不是问答时才静默失败。
```

## 跑同一个三用户授权 demo —— 但这次过本地 LLM

```sh
# 1. 同 LIVE-DEMO：seed 干净 DB + 每用户 API key
uv run python examples/legal-demo/seed_server_db.py data/legal-demo-server.db

# 2. 起网关。AI 端点由上面的环境变量提供。
uv run legal-mcp serve-http --host 127.0.0.1 --port 8767 \
  --db data/legal-demo-server.db \
  --audit-log data/legal-demo-audit.jsonl

# 3. 问一个需要 LLM 规划的问题（不是 fast-path 能直接命中的固定模式），
#    分别以 legal / business 身份提问，得到不同披露：
TOKEN=$(python -c "import json;print(json.load(open('data/legal-demo-server.tokens.json'))['legal'])")
curl -s localhost:8767/mcp -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"agent_query","arguments":{"rationale":"demo","question":"帮我看看 MOON 这个项目的法务对接人是谁"}}}'
```

`legal` 能看到 `legal_bp`，`business` 因为没有 `legal_bp` 的 DB grant 被拒
（`return_field_access_denied`）——与 LIVE-DEMO 同样的授权结论，但这次规划由**本地模型**完成。

## 证明它真的没出网

- `legal-mcp doctor --probe-ai` 回显的 base URL 指向本机/内网，无外部地址。
- **断网测试**：拔掉外网（或防火墙挡掉出站），本地模型仍能完成上面的问答；
  此时若你换回云端端点（清掉 `LEGAL_MCP_AI_BASE_URL`），同样的问题会因端点不可达
  而**明确报错**（`ai_backend_unreachable`），而不是悄悄降级——这正是"本地模型驱动
  全部功能"的可见证据。

## 模型能力下限（诚实提示）

planner 产出的是受约束的 QueryPlan，格式问题有解析兜底（去围栏 + 提取首个 JSON 块），
但**语义**质量取决于模型。太弱的模型可能把问题映射错域/字段而走向 `clarify`。
qwen2.5 7B 一档及以上通常足以驱动本 demo；换更小的模型请自行验证。
