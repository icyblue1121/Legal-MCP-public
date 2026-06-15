"""Runnable legal demo for the permission-aware MCP gateway (plan §6 阶段6).

Shows the core promise end-to-end on **synthetic** data: one question, different
disclosure per user, every field decision auditable, and data read through a
connector rather than owned by the gateway.

    uv run python examples/legal-demo/run_demo.py

Flow per role: the **database permission grants** decide which requested fields
are allowed (v0.4.0 §C: the DB grants are the sole authorization gate, there is
no policy file) -> the connector reads those fields from the (demo) source ->
only allowed fields are disclosed, and every decision is printed as an audit
line. Identity fields (project_code, name) are exempt from the field gate, so
every role sees them — exactly as the gateway treats identity fields.
"""

from __future__ import annotations

import csv
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from legal_mcp import db
from legal_mcp.connectors.base import ConnectorQuery
from legal_mcp.connectors.sqlite_demo import SqliteDemoConnector
from legal_mcp.identity import (
    ROLE_AUDITOR,
    ROLE_BUSINESS,
    ROLE_LEGAL,
    create_user,
)
from legal_mcp.policy import AccessContext, authorize_fields

HERE = Path(__file__).resolve().parent
DEMO_DATA = HERE / "demo-data.csv"

# "这个项目的相关信息" -> these project fields. `legal_bp` is the sensitive one.
REQUESTED_FIELDS = ("project_code", "name", "contact_person", "legal_bp")
# Identity fields are exempt from the field gate (the gateway never withholds the
# handle a user needs to refer to a record), so every role sees them.
IDENTITY_FIELDS = frozenset({"project_code", "name"})

# Per-role DB field grants — the sole differentiator. Only legal is granted the
# sensitive legal_bp, so the DB grant alone produces the disclosure difference.
GRANTS_BY_ROLE = {
    ROLE_LEGAL: ("contact_person", "legal_bp"),
    ROLE_BUSINESS: ("contact_person",),
    ROLE_AUDITOR: (),
}


def _decision(allowed: bool, reason: str) -> SimpleNamespace:
    """A per-field audit record. Plain namespace (not a dataclass) so the demo
    loads cleanly via importlib without being registered in ``sys.modules``."""
    return SimpleNamespace(allowed=allowed, reason=reason)


def seed(database_path: Path) -> dict[str, AccessContext]:
    """Seed projects + one user per role with their DB grants. Returns contexts."""
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    contexts: dict[str, AccessContext] = {}
    try:
        with DEMO_DATA.open(encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                conn.execute(
                    "insert into projects "
                    "(project_code, name, stage, contact_person, legal_bp, website) "
                    "values (?, ?, ?, ?, ?, ?)",
                    (
                        row["project_code"],
                        row["name"],
                        row["stage"],
                        row["contact_person"],
                        row["legal_bp"],
                        row["website"],
                    ),
                )
        for role, fields in GRANTS_BY_ROLE.items():
            user = create_user(
                conn,
                email=f"{role}@legal-demo.local",
                display_name=f"{role.title()} User",
                role=role,
            )
            for field in fields:
                # Domain-wide grant (project_id NULL): the demo isn't about row
                # scope, only the field gate.
                conn.execute(
                    "insert into permission_grants "
                    "(user_id, operation, data_domain, field_name, project_id) "
                    "values (?, 'read', 'project', ?, null)",
                    (user["id"], field),
                )
            contexts[role] = AccessContext(user_id=int(user["id"]), role=role)
        conn.commit()
    finally:
        conn.close()
    return contexts


def disclose(
    connector: SqliteDemoConnector,
    database_path: Path,
    context: AccessContext,
    role: str,
) -> dict[str, Any]:
    gated = {f for f in REQUESTED_FIELDS if f not in IDENTITY_FIELDS}
    conn = db.connect(database_path)
    try:
        decision = authorize_fields(
            conn,
            context,
            operation="read",
            data_domain="project",
            project_id=None,
            requested_fields=gated,
        )
    finally:
        conn.close()

    decisions: dict[str, SimpleNamespace] = {}
    for field in REQUESTED_FIELDS:
        if field in IDENTITY_FIELDS:
            decisions[field] = _decision(True, "identity field (exempt from gate)")
        elif field in decision.allowed_fields:
            decisions[field] = _decision(True, "granted by DB permission grant")
        else:
            decisions[field] = _decision(
                False, decision.denied_fields.get(field, "field_not_granted")
            )

    allowed = tuple(f for f in REQUESTED_FIELDS if decisions[f].allowed)
    rows = (
        connector.query(ConnectorQuery(domain="project", filters={}, fields=allowed))
        if allowed
        else []
    )
    return {
        "role": role,
        "allowed_fields": allowed,
        "denied_fields": tuple(f for f in REQUESTED_FIELDS if not decisions[f].allowed),
        "rows": rows,
        "decisions": decisions,
    }


def run(database_path: Path) -> list[dict[str, Any]]:
    contexts = seed(database_path)
    connector = SqliteDemoConnector(database_path)
    return [
        disclose(connector, database_path, contexts[role], role)
        for role in (ROLE_LEGAL, ROLE_BUSINESS, ROLE_AUDITOR)
    ]


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        results = run(Path(tmp) / "demo.db")

    print("Question: 这个项目的相关信息（联系人/法务BP）？\n")
    for result in results:
        print(f"[{result['role']}] discloses: {', '.join(result['allowed_fields'])}")
        print(f"    withheld: {', '.join(result['denied_fields']) or '(none)'}")
        for row in result["rows"]:
            print(f"    {row}")
        print()

    print("Audit trail (per-field decision):")
    for result in results:
        for field, decision in result["decisions"].items():
            mark = "ALLOW" if decision.allowed else "DENY "
            print(f"  {result['role']:<9} project.{field:<14} {mark}  {decision.reason}")


if __name__ == "__main__":
    main()
