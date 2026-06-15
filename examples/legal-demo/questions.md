# Demo questions

Natural-language questions the legal demo is meant to answer, and what the
gateway should do with each. "Disclosure differs by role" is the whole point.

| Question | Domain | Sensitive field | legal | business | auditor |
| --- | --- | --- | --- | --- | --- |
| 这个项目该找谁办？ / 联系人是谁？ | project | — | ✅ | ✅ | ✅ (identity) |
| 这个项目的法务 BP 是谁？ | project | `legal_bp` | ✅ | ❌ default-deny | ❌ |
| 我能访问哪些项目？ | project (scope) | — | all | by_grant | all |
| 这个项目的合同对方是谁？ | contract | `counterparty` | ✅ | depends | ❌ |
| 这个合同的签约主体 / 金额？ | contract | `company_entity`, `total_amount` | ✅ | ❌ explicit-deny | ❌ |

Notes:

- **default-deny vs explicit-deny:** `business` is *not granted* `legal_bp`
  (falls through to default-deny), and can be *explicitly denied* a contract field
  via a grant row with `allowed = 0` (deny-over-allow). Both end in "withheld",
  but the audit reason distinguishes them.
- **record_scope** is row-level and separate from fields: a user sees only the
  projects granted via `project_access` (the grant's `project_id` scope).
- The runnable `run_demo.py` exercises the project-field row of this table; the
  field-gate behavior is covered by `tests/test_query_authorization.py`.
