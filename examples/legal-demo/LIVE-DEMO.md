# Live grant demo (over HTTP)

Where [`run_demo.py`](run_demo.py) shows the disclosure decision **in-process**,
this proves it on the **live HTTP gateway**: `legal-mcp serve-http` against a clean
synthetic seed, driven with **per-user API keys**. Same question — different
disclosure per user, decided by the **database permission grants** (v0.4.0 §C: the
DB grants are the sole authorization gate; there is no policy file).

All data is synthetic ([`demo-data.csv`](demo-data.csv)). Nothing is real.

## What it proves

The production HTTP endpoint exposes only one tool, `agent_query` (the north-star
UX: ask a question, get the answer you're allowed to see). The demo asks the same
question as three users and the gateway answers each differently:

| Role     | `MOON的法务BP是谁` | Outcome |
| -------- | ------------------ | ------- |
| legal    | ✅ sees `legal_bp` | `success` — legal is granted `legal_bp` |
| business | ❌ withheld        | `return_field_access_denied` — **no DB grant** for `legal_bp` |
| auditor  | ❌ withheld        | `access_denied` — auditors read the audit trail, not project content |

The point is **business**: it runs the *same* query as legal, but the seed grants
`legal_bp` only to legal, so the DB grant alone makes the difference. The denied
value never appears anywhere in the response.

The question is answered by the deterministic fast-path planner, so the demo needs
**no LLM / API key**. The same per-role disclosure outcome (over a mixed Feishu +
SQLite source) is asserted by `tests/test_feishu_mixed_e2e.py`.

## Run it locally

```sh
# 1. Seed a clean DB + per-role API keys (both land under data/, gitignored).
uv run python examples/legal-demo/seed_server_db.py data/legal-demo-server.db

# 2. Start the gateway.
uv run legal-mcp serve-http --host 127.0.0.1 --port 8767 \
  --db data/legal-demo-server.db \
  --audit-log data/legal-demo-audit.jsonl

# 3. Ask as legal, then business (token from the tokens file):
TOKEN=$(python -c "import json;print(json.load(open('data/legal-demo-server.tokens.json'))['legal'])")
curl -s localhost:8767/mcp -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"agent_query","arguments":{"rationale":"demo","question":"MOON的法务BP是谁"}}}'
```

## Run it in Docker

Use [`../../docker-compose.demo.yml`](../../docker-compose.demo.yml):

```sh
docker compose -f docker-compose.demo.yml run --rm seed      # creates data/legal-demo-server.db
docker compose -f docker-compose.demo.yml up -d gateway      # serve-http on :8767
# then the same curl as above against localhost:8767
```

## Live vs in-process (honest notes)

- **Only `agent_query` is exposed over HTTP** (plan §5.2 #3). `structured_query`
  and the field tools are internal-debug only, so the live demo uses `agent_query`.
- **`record_scope` is enforced on the live path**: row visibility comes from the
  requester's `project_access` + the grant's `project_id` scope, applied in the
  graph-owned search executor.
- **One gate on the live path**: a field is released only if the requester holds a
  DB grant for it. `admin` is exempt (the operator); see
  [`../../Docs/strategy/identity-model.md`](../../Docs/strategy/identity-model.md).
