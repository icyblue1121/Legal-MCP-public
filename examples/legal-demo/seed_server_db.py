"""Seed a clean, synthetic database for the *live* permission-aware gateway demo.

This builds a real SQLite database for `legal-mcp serve-http`: three users, each
with their own API key, plus synthetic projects and DB grants. It proves the
crown-jewel claim over real HTTP — *same question, different disclosure per user*
— with the **database permission grants as the sole authorization gate** (v0.4.0
§C: there is no parallel policy file).

    uv run python examples/legal-demo/seed_server_db.py [DB_PATH]

It writes the database and a sibling ``<db>.tokens.json`` holding the per-role API
keys (both land under ``data/`` and are gitignored). The legal vs business
contrast is the point and it lives entirely in the DB grants: legal is granted
``legal_bp``, business is not.

Live behavior (see ``examples/legal-demo/LIVE-DEMO.md``):

* ``legal``    — sees ``legal_bp`` (DB grant + project access).
* ``business`` — denied ``legal_bp`` (no DB grant for it); identical query to legal.
* ``auditor``  — most restricted: no field grants and empty row scope.
"""

from __future__ import annotations

import csv
import json
import sqlite3
import sys
from pathlib import Path

from legal_mcp import db
from legal_mcp.identity import (
    ROLE_AUDITOR,
    ROLE_BUSINESS,
    ROLE_LEGAL,
    create_api_key,
    create_user,
)

HERE = Path(__file__).resolve().parent
DEMO_DATA = HERE / "demo-data.csv"
COMPANY_SEALS_DATA = HERE / "company-seals.csv"
DEFAULT_DB = Path("data") / "legal-demo-server.db"

# Per-role DB field grants. legal_bp is the crown jewel: only legal is granted it,
# so the DB grant alone produces the legal-vs-business disclosure difference.
# project_code/name are identity fields (exempt from the field gate).
GRANTED_FIELDS_BY_ROLE = {
    ROLE_LEGAL: ("contact_person", "legal_bp"),
    ROLE_BUSINESS: ("contact_person",),
}

SEAL_GRANTED_FIELDS = (
    "status",
    "custodian",
    "storage_location",
    "borrower",
    "borrowed_at",
    "borrow_reason",
    "expected_return_at",
    "actual_return_at",
)

BUSINESS_COMPANIES = ("上海青岚科技有限公司", "北京星河互动有限公司")


def seed_server_db(database_path: Path) -> dict[str, str]:
    """Create the demo database and return ``{role: plaintext API token}``."""
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        project_ids = _seed_projects(conn)
        company_ids = _seed_companies_and_seals(conn)
        tokens: dict[str, str] = {}
        # legal and business: access to every demo project, but different field
        # grants — legal alone is granted legal_bp, so the DB grant is what makes
        # their disclosure differ on an identical query.
        for role in (ROLE_LEGAL, ROLE_BUSINESS):
            user = create_user(
                conn,
                email=f"{role}@legal-demo.local",
                display_name=f"{role.title()} User",
                role=role,
            )
            _grant_project_access(conn, user["id"], project_ids)
            _grant_fields(conn, user["id"], "project", GRANTED_FIELDS_BY_ROLE[role])
            company_scope = (
                company_ids.values()
                if role == ROLE_LEGAL
                else (company_ids[name] for name in BUSINESS_COMPANIES)
            )
            _grant_company_access(conn, user["id"], list(company_scope))
            _grant_fields(conn, user["id"], "seal", SEAL_GRANTED_FIELDS)
            tokens[role] = create_api_key(
                conn, user_id=user["id"], label=f"{role} demo key"
            ).plaintext
        # auditor: no field grants and no project access (empty row scope) — the
        # most restricted role, demonstrating defense in depth on the live path.
        auditor = create_user(
            conn,
            email="auditor@legal-demo.local",
            display_name="Auditor User",
            role=ROLE_AUDITOR,
        )
        tokens[ROLE_AUDITOR] = create_api_key(
            conn, user_id=auditor["id"], label="auditor demo key"
        ).plaintext
        conn.commit()
        return tokens
    finally:
        conn.close()


def _seed_projects(conn: sqlite3.Connection) -> list[int]:
    ids: list[int] = []
    with DEMO_DATA.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            cursor = conn.execute(
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
            ids.append(int(cursor.lastrowid))
    return ids


def _seed_companies_and_seals(conn: sqlite3.Connection) -> dict[str, int]:
    company_ids: dict[str, int] = {}
    with COMPANY_SEALS_DATA.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            company = row["company"]
            company_id = company_ids.get(company)
            if company_id is None:
                cursor = conn.execute(
                    "insert into companies (name, unified_social_credit_code) "
                    "values (?, ?)",
                    (company, row["unified_social_credit_code"]),
                )
                company_id = int(cursor.lastrowid)
                company_ids[company] = company_id
            conn.execute(
                """
                insert into company_seals
                  (company_id, company, seal_type, custodian, storage_location, status,
                   borrower, borrowed_at, borrow_reason, expected_return_at, actual_return_at)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    company_id,
                    company,
                    row["seal_type"],
                    row["custodian"],
                    row["storage_location"],
                    row["status"],
                    row["borrower"],
                    row["borrowed_at"],
                    row["borrow_reason"],
                    row["expected_return_at"],
                    row["actual_return_at"],
                ),
            )
    return company_ids


def _grant_project_access(
    conn: sqlite3.Connection, user_id: int, project_ids: list[int]
) -> None:
    for project_id in project_ids:
        conn.execute(
            "insert into project_access (user_id, project_id, granted_by_user_id) "
            "values (?, ?, ?)",
            (user_id, project_id, user_id),
        )


def _grant_company_access(
    conn: sqlite3.Connection, user_id: int, company_ids: list[int]
) -> None:
    for company_id in company_ids:
        conn.execute(
            "insert into company_access (user_id, company_id, granted_by_user_id) "
            "values (?, ?, ?)",
            (user_id, company_id, user_id),
        )


def _grant_fields(
    conn: sqlite3.Connection, user_id: int, domain: str, fields: tuple[str, ...]
) -> None:
    group_id = conn.execute(
        "insert into user_groups (name) values (?)", (f"grp-{user_id}-{domain}",)
    ).lastrowid
    conn.execute(
        "insert into user_group_memberships (user_id, group_id) values (?, ?)",
        (user_id, group_id),
    )
    for field in fields:
        # project_id NULL = domain-wide grant (matched by authorize_fields).
        conn.execute(
            "insert into permission_grants "
            "(group_id, operation, data_domain, field_name, project_id) "
            "values (?, ?, ?, ?, ?)",
            (group_id, "read", domain, field, None),
        )


def main() -> None:
    database_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DB
    database_path.parent.mkdir(parents=True, exist_ok=True)
    tokens = seed_server_db(database_path)
    tokens_path = database_path.with_suffix(".tokens.json")
    tokens_path.write_text(json.dumps(tokens, indent=2) + "\n", encoding="utf-8")
    print(f"seeded {database_path}")
    print(f"wrote per-role API tokens to {tokens_path}")
    for role, token in tokens.items():
        print(f"  {role:<9} {token}")


if __name__ == "__main__":
    main()
