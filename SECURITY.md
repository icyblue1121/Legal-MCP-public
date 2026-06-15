# Security Policy

Legal-MCP is a permission-aware MCP gateway: its entire value is correct, audited
disclosure. Security reports are taken seriously.

## Reporting a vulnerability

Please report privately, not in a public issue:

- Use **GitHub → Security → Report a vulnerability** (private advisory), or
- email the maintainer (see the repository owner's profile).

Include repro steps and the impact (especially any way to read a field a user is
not authorized to see). We aim to acknowledge within a few days.

## Security model (what the gateway does and does not promise)

- **The gateway is the trust boundary.** Under read-through, the gateway holds a
  broad credential to a data source and filters per user. Its correctness *is*
  the security boundary — see `Docs/strategy/2026-06-06-permission-aware-mcp-gateway-plan.md` §12.
- **Core invariant:** an unauthorized field's *value* must never enter the model
  context — not during planning, retrieval, or answer synthesis. This is guarded
  by a permanent leakage red-team test (`tests/test_disclosure_leakage.py`) and
  must never regress.
- **Authorization is default-deny, deny-over-allow**, enforced by the database
  permission grants — the sole authorization gate (see `policy.py`
  `authorize_fields` and `query_authorization.py`).
- **What is NOT promised:** that an answer never leaves your network. If you use
  an external AI client, the (authorized) answer can enter that external model's
  context. Only a locally-hosted model keeps the whole exchange on-prem.

## Scope

In scope: field/record authorization bypass, disclosure leakage, audit evasion,
identity confusion, injection in the query path. Out of scope: issues that
require an already-trusted operator/admin, or the documented non-goals.
