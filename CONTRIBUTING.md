# Contributing to Legal-MCP

Thanks for your interest. Legal-MCP is an open-source, self-hosted,
**permission-aware MCP gateway**. The fastest way to get a change merged is to
keep it aligned with that scope.

## The one rule: trace every change to the north-star

> 小问题别来问我，问 AI；AI 只回答你有权知道的那一部分。

Before proposing a feature, ask: *does this serve "answer a permission-bounded
question and audit the disclosure"?* If not, it probably belongs elsewhere.

## Non-goals (PRs for these will be declined)

This project deliberately does **not** become:

- a canonical store of business facts (projects/contracts/…) — data stays in its source;
- a data-entry / CRUD UI;
- entity MDM / master-data / alias governance;
- a BI / reporting platform or a document knowledge base;
- the official maintainer of connectors for every data source (official = 1–2 only).

The legal domain is a **reference demo**, not the product boundary. See
`Docs/strategy/2026-06-06-permission-aware-mcp-gateway-plan.md` §2.2.

## What contributions are most welcome

- **Connectors** implementing `legal_mcp.connectors.base.DataConnector` for your
  own data source (start from `connectors/sqlite_demo.py`).
- **Authorization** improvements to the DB-grant gate (`policy.py`,
  `query_authorization.py`) — field/record-scope semantics.
- **Hardening** the disclosure/audit path and the leakage red-team tests.

## Development

```sh
uv run pytest -q            # full suite (must stay green)
uv run python examples/legal-demo/run_demo.py   # see the gateway flow
```

Guidelines:

- Add tests with every change; security-relevant changes need a test that would
  fail without the fix. The leakage gate (`tests/test_disclosure_leakage.py`) is
  a hard CI gate — never weaken it.
- Keep the core dependency-free where possible (`pyproject.toml` core
  `dependencies = []`); optional features may add deps lazily.
- Do not commit real client data. `Docs/` is git-ignored except `Docs/strategy/`;
  examples must use synthetic data only.
- Match the surrounding code style; keep changes surgical.
