# Legal demo

A 5-minute, runnable demonstration of the permission-aware MCP gateway's core
promise: **one question, different disclosure per user, every decision audited,
data read through a connector.**

All data here is **synthetic** (`demo-data.csv`). Nothing is real.

## Run it

```sh
uv run python examples/legal-demo/run_demo.py
```

It seeds a throwaway SQLite database from `demo-data.csv`, then for the same
question runs the gateway flow for three users.

## What it shows

The question asks for project info including `legal_bp` (the legal business
partner — sensitive). The **database permission grants** decide per role (v0.4.0
§C: the DB grants are the sole authorization gate; only legal is granted
`legal_bp`):

| Role     | Sees `legal_bp`? | Sees `project_code`, `name`, `contact_person`? |
| -------- | ---------------- | ---------------------------------------------- |
| legal    | ✅ yes           | ✅ yes                                          |
| business | ❌ no (not granted)  | ✅ yes                                       |
| auditor  | ❌ no            | identity only (`project_code`, `name`)         |

The flow is: **DB grants decide allowed fields → connector reads only those fields
from the source → only allowed fields are disclosed.** A denied field's *value*
never reaches a user who may not see it, and each decision prints as an audit
line.

## How it maps to the architecture

- **Authorization** — `legal_mcp.policy.authorize_fields` checks the DB permission
  grants (default-deny, deny-over-allow); identity fields are exempt, and row-level
  `record_scope` is applied separately.
- **Connector** — `legal_mcp.connectors.sqlite_demo.SqliteDemoConnector` reads
  the demo source through the `DataConnector` interface; the gateway core never
  hard-codes the legal tables.
- **Disclosure** — only granted fields are ever requested from the connector, so
  unauthorized values are never fetched into the answer.

This example is verified by `tests/test_legal_demo_example.py`.

## See also

- [`LIVE-DEMO.md`](LIVE-DEMO.md) — the same promise proven over the **live HTTP
  gateway** (`serve-http`) with per-user API keys, local and in Docker.
- [`questions.md`](questions.md) — the natural-language questions this demo answers.
- The migration plan: `Docs/strategy/2026-06-06-permission-aware-mcp-gateway-plan.md`.
