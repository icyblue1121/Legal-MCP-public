# Mixed-source demo: a real Feishu Bitable + the local SQLite demo (v0.3)

Where [`LIVE-DEMO.md`](LIVE-DEMO.md) serves everything from the local SQLite demo,
this serves the **`project` domain live from a real Feishu Bitable** while the
rest (contract, license, cross-domain) keeps coming from SQLite — one gateway
answering over a **mixed database**. The DB-grant gate, record-scope, and audit
are unchanged: a real read-through source slots in under the *same* authorization.

This is the v0.3 proof that the gateway reads through an external source it does
**not** own, and discloses only the fields a user is authorized to see.

## What it proves

Same question, different disclosure per role — but the sensitive field now
physically lives in Feishu, not in this project:

| Role     | `MOON的法务BP是谁` | Outcome |
| -------- | ------------------ | ------- |
| legal    | ✅ sees `legal_bp` (read from Feishu) | `success` |
| business | ❌ withheld         | `return_field_access_denied` — no DB grant for `legal_bp` |
| auditor  | ❌ withheld         | `access_denied` — auditors read the audit trail, not content |

The fake-client version of this exact flow is asserted by
`tests/test_feishu_mixed_e2e.py` (no network). The steps below run it against
**your** Bitable.

## Prerequisites

1. A **Feishu custom app** (Open Platform → Developer Console → your app) with the
   **bitable read** scope, added as a collaborator on the target Base. Copy its
   **App ID** and **App Secret** from *Credentials & Basic Info*.
2. A **Bitable** with a table whose rows have at least `project_code`, `name`,
   `contact_person`, `legal_bp` columns. From the table URL, note:
   - the app token — the `bascn…` / `…/base/<app_token>` segment;
   - the table id — the `tbl…` segment (`?table=<table_id>`).
3. The row's `project_code` values must match `project_code`s in the gateway's
   governance DB (the seed below creates `MOON`, `STAR`, … from
   [`demo-data.csv`](demo-data.csv)). Record-scope is computed from governance
   grants and applied to Feishu rows by that code.

## Run it

```sh
# 1. Seed the governance DB + per-role API keys (projects, grants, users).
uv run python examples/legal-demo/seed_server_db.py data/legal-demo-server.db

# 2. Point the connector config at YOUR Bitable. Either edit
#    examples/connectors/feishu-bitable.connector.yaml (app_token + table_id),
#    or copy it under data/ (gitignored) and edit there.

# 3. Export the app credentials (never commit these).
export FEISHU_APP_ID=cli_xxxxxxxxxxxxxxxx
export FEISHU_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# 4. Start the gateway with the connector.
uv run legal-mcp serve-http --host 127.0.0.1 --port 8767 \
  --db data/legal-demo-server.db \
  --audit-log data/legal-demo-audit.jsonl \
  --connector examples/connectors/feishu-bitable.connector.yaml

# 5. Ask as legal, then business — the legal_bp value is fetched from Feishu.
TOKEN=$(python -c "import json;print(json.load(open('data/legal-demo-server.tokens.json'))['legal'])")
curl -s localhost:8767/mcp -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"agent_query","arguments":{"rationale":"demo","question":"MOON的法务BP是谁"}}}'
```

Swap the token for `business` / `auditor` to see the same question withheld.

## Honest boundaries (v0.3)

- **Connector path serves the `project` domain.** A domain is connector-servable
  when its row-scope identity (`project_code`) is one of its own returnable
  fields — true for `project`. Child domains (contract/license) over a connector,
  whose scope identity is a denormalized column, are a later version; in the mix
  they stay on the local SQLite path.
- **Equality filters only on the connector path.** Identity questions
  (`<project>的<field>`) translate cleanly; a richer operator is reported as
  `unsupported_operator`, never silently dropped. `contains`/date filters and
  `cross_domain` stay on the SQLite path.
- **Record-scope is applied in the gateway** (allowed `project_code`s post-filter
  the Feishu rows). Out-of-scope rows are dropped before they can reach the model.
  Pushing the scope down into the Feishu query is a reserved optimization.
- **The gateway holds the Feishu app's broad credentials** and reads on every
  user's behalf, then filters per-user. This is inherent to read-through; the
  invariant is that an unauthorized value never enters the model context. For
  "no data leaves the intranet", pair this with a self-hosted model (v0.3.5).
