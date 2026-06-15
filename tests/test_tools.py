from datetime import date, timedelta

import pytest

from legal_mcp import db
from legal_mcp.ai_provider import AIMessage
from legal_mcp.identity import ROLE_AUDITOR, ROLE_BUSINESS, ROLE_LEGAL, create_user
from legal_mcp.policy import AccessContext
from legal_mcp.tools import call_tool


class _StubPlanner:
    """Offline stand-in for the server-side AI planner."""

    def __init__(self, content: str) -> None:
        self.content = content

    def complete(self, messages: list[AIMessage]) -> AIMessage:
        return AIMessage(role="assistant", content=self.content)


def seed_project(conn, *, code: str = "GAME-001", name: str = "Project One") -> int:
    cursor = conn.execute(
        """
        insert into projects (
          project_code, name, stage, legal_bp, department, release_team,
          contact_person, website
        )
        values (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            code,
            name,
            "live",
            "Ava",
            "Publishing",
            "Release A",
            "Morgan",
            "https://example.test",
        ),
    )
    conn.commit()
    return int(cursor.lastrowid)


def grant_project_access(conn, *, user_id: int, project_id: int) -> None:
    grantor = create_user(
        conn,
        email=f"grantor-{user_id}-{project_id}@example.com",
        display_name="Grantor",
        role=ROLE_LEGAL,
    )
    conn.execute(
        """
        insert into project_access (user_id, project_id, granted_by_user_id)
        values (?, ?, ?)
        """,
        (user_id, project_id, grantor["id"]),
    )
    conn.commit()


def grant_field_access(
    conn,
    *,
    user_id: int,
    project_id: int,
    data_domain: str,
    field_name: str,
) -> None:
    group_id = conn.execute(
        "insert into user_groups (name) values (?)",
        (f"group-{user_id}-{project_id}-{data_domain}-{field_name}",),
    ).lastrowid
    conn.execute(
        "insert into user_group_memberships (user_id, group_id) values (?, ?)",
        (user_id, group_id),
    )
    conn.execute(
        """
        insert into permission_grants
          (group_id, operation, data_domain, field_name, project_id)
        values (?, ?, ?, ?, ?)
        """,
        (group_id, "read", data_domain, field_name, project_id),
    )
    conn.commit()


def test_all_tools_require_rationale(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)

    for tool_name, arguments in {
        "list_projects": {},
        "get_project_context": {"project_id_or_name": "GAME-001"},
        "list_expiring_licenses": {},
        "list_open_risks": {},
    }.items():
        result = call_tool(tool_name, arguments, database_path=database_path)

        assert result["error"]["code"] == "missing_rationale"


def test_describe_my_access_returns_visible_projects_and_granted_fields(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        visible_project_id = seed_project(conn, code="GAME-001", name="Visible Project")
        seed_project(conn, code="GAME-002", name="Hidden Project")
        legal_user = create_user(
            conn,
            email="legal@example.com",
            display_name="Legal User",
            role=ROLE_LEGAL,
        )
        grant_project_access(conn, user_id=legal_user["id"], project_id=visible_project_id)
        grant_field_access(
            conn,
            user_id=legal_user["id"],
            project_id=visible_project_id,
            data_domain="project",
            field_name="website",
        )
        grant_field_access(
            conn,
            user_id=legal_user["id"],
            project_id=visible_project_id,
            data_domain="contract",
            field_name="total_amount",
        )
        grant_field_access(
            conn,
            user_id=legal_user["id"],
            project_id=visible_project_id,
            data_domain="license",
            field_name="actual_operator",
        )
        context = AccessContext.from_user(legal_user)
    finally:
        conn.close()

    result = call_tool(
        "describe_my_access",
        {"rationale": "confirm accessible project scope"},
        database_path=database_path,
        access_context=context,
    )

    assert result["access"]["projects"] == [
        {
            "project_code": "GAME-001",
            "name": "Visible Project",
            "fields": {
                "project": ["website"],
                "contract": ["total_amount"],
                "license": ["actual_operator"],
            },
        }
    ]
    assert "GAME-002" not in str(result)


def test_structured_query_runs_through_graph_authorization(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        project_id = seed_project(conn, code="ACME", name="示例项目")
        conn.execute("update projects set legal_bp = ? where id = ?", ("张三", project_id))
        legal_user = create_user(
            conn,
            email="structured-query@example.com",
            display_name="Structured Query",
            role=ROLE_LEGAL,
        )
        grant_project_access(conn, user_id=legal_user["id"], project_id=project_id)
        grant_field_access(
            conn,
            user_id=legal_user["id"],
            project_id=project_id,
            data_domain="project",
            field_name="legal_bp",
        )
        context = AccessContext.from_user(legal_user)
    finally:
        conn.close()

    result = call_tool(
        "structured_query",
        {
            "query": {
                "domain": "project",
                "operation": "search",
                "filters": [{"field": "legal_bp", "operator": "eq", "value": "张三"}],
                "return_fields": ["project_code", "name"],
                "limit": 20,
            },
            "rationale": "find projects by legal bp",
        },
        database_path=database_path,
        access_context=context,
    )

    assert result["status"] == "success"
    assert result["result"]["projects"] == [
        {"project_code": "ACME", "name": "示例项目"}
    ]


def test_call_tool_db_grant_authorizes_field_on_live_path(tmp_path) -> None:
    # A DB grant for legal_bp reaches the live agent path through call_tool and
    # authorizes the field — the DB grant is the sole gate (v0.4.0 §C).
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        project_id = seed_project(conn, code="ACME", name="示例项目")
        conn.execute("update projects set legal_bp = ? where id = ?", ("张三", project_id))
        legal_user = create_user(
            conn,
            email="grant-live@example.com",
            display_name="Grant Live",
            role=ROLE_LEGAL,
        )
        grant_project_access(conn, user_id=legal_user["id"], project_id=project_id)
        grant_field_access(
            conn,
            user_id=legal_user["id"],
            project_id=project_id,
            data_domain="project",
            field_name="legal_bp",
        )
        context = AccessContext.from_user(legal_user)
    finally:
        conn.close()

    query = {
        "query": {
            "domain": "project",
            "operation": "search",
            "filters": [{"field": "name", "operator": "eq", "value": "示例项目"}],
            "return_fields": ["legal_bp"],
            "limit": 20,
        },
        "rationale": "fetch legal bp",
    }

    granted = call_tool(
        "structured_query", query, database_path=database_path, access_context=context
    )
    assert granted["status"] == "success"
    assert granted["result"]["projects"] == [{"legal_bp": "张三"}]


def test_agent_write_returns_proposal_not_direct_write(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        seed_project(conn, code="Acme", name="示例项目")
    finally:
        conn.close()

    result = call_tool(
        "agent_write",
        {
            "instruction": "把 Acme 的法务 BP 改成李四",
            "rationale": "draft update for review",
        },
        database_path=database_path,
        access_context=AccessContext.local_operator(),
    )

    conn = db.connect(database_path)
    try:
        row = conn.execute(
            "select legal_bp from projects where project_code = ?",
            ("Acme",),
        ).fetchone()
    finally:
        conn.close()

    assert result["proposal"]["requires_approval"] is True
    assert "diff" in result["proposal"]
    assert row["legal_bp"] == "Ava"


def test_project_not_found_error_includes_visible_access_summary(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        visible_project_id = seed_project(conn, code="GAME-001", name="Visible Project")
        legal_user = create_user(
            conn,
            email="legal@example.com",
            display_name="Legal User",
            role=ROLE_LEGAL,
        )
        grant_project_access(conn, user_id=legal_user["id"], project_id=visible_project_id)
        context = AccessContext.from_user(legal_user)
    finally:
        conn.close()

    result = call_tool(
        "get_project_fields",
        {
            "project_id_or_name": "MISSING",
            "fields": ["website"],
            "rationale": "query project website",
        },
        database_path=database_path,
        access_context=context,
    )

    assert result["error"]["code"] == "not_found"
    assert "当前用户可见项目" in result["error"]["message"]
    assert result["error"]["details"]["access"]["projects"][0]["project_code"] == "GAME-001"


def test_get_project_fields_includes_requested_project_fields(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        project_id = seed_project(conn)
        conn.execute(
            """
            insert into licenses (
              project_id, external_key, license_type, identifier, expiry_date
            )
            values (?, ?, ?, ?, ?)
            """,
            (project_id, "publication", "publication_license", "ISBN-001", None),
        )
        conn.commit()
    finally:
        conn.close()

    result = call_tool(
        "get_project_fields",
        {
            "project_id_or_name": "GAME-001",
            "fields": ["legal_bp", "department", "release_team", "contact_person", "website"],
            "rationale": "draft contract context",
        },
        database_path=database_path,
        access_context=AccessContext.local_operator(),
    )

    assert result == {
        "project": {
            "legal_bp": "Ava",
            "department": "Publishing",
            "release_team": "Release A",
            "contact_person": "Morgan",
            "website": "https://example.test",
        }
    }


def test_resolve_project_returns_identity_fields(
    tmp_path,
) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        seed_project(conn, code="ACME", name="Acme")
    finally:
        conn.close()

    result = call_tool(
        "resolve_project",
        {
            "query": "ACME",
            "rationale": "query official website",
        },
        database_path=database_path,
        access_context=AccessContext.local_operator(),
    )

    assert result == {
        "project": {
            "project_code": "ACME",
            "name": "Acme",
        }
    }


def test_resolve_project_delegates_user_permission_questions_to_access_summary(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        project_id = seed_project(conn, code="GAME-001", name="Visible Project")
        legal_user = create_user(
            conn,
            email="legal@example.com",
            display_name="Legal User",
            role=ROLE_LEGAL,
        )
        grant_project_access(conn, user_id=legal_user["id"], project_id=project_id)
        context = AccessContext.from_user(legal_user)
    finally:
        conn.close()

    result = call_tool(
        "resolve_project",
        {"query": "查询用户权限", "rationale": "check user permissions"},
        database_path=database_path,
        access_context=context,
    )

    assert "access" in result
    assert result["access"]["projects"][0]["project_code"] == "GAME-001"


def test_get_project_fields_requires_fields(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        seed_project(conn, code="ACME", name="Acme")
    finally:
        conn.close()

    result = call_tool(
        "get_project_fields",
        {"project_id_or_name": "ACME", "rationale": "query project"},
        database_path=database_path,
        access_context=AccessContext.local_operator(),
    )

    assert result["error"]["code"] == "validation_error"


def test_get_project_fields_returns_only_requested_fields(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        seed_project(conn, code="ACME", name="Acme")
    finally:
        conn.close()

    result = call_tool(
        "get_project_fields",
        {
            "project_id_or_name": "ACME",
            "fields": ["website"],
            "rationale": "query official website",
        },
        database_path=database_path,
        access_context=AccessContext.local_operator(),
    )

    assert result == {
        "project": {
            "website": "https://example.test",
        }
    }


def test_get_project_fields_denies_ungranted_project_fields(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        project_id = seed_project(conn, code="ACME", name="Acme")
        legal_user = create_user(
            conn,
            email="legal@example.com",
            display_name="Legal User",
            role=ROLE_LEGAL,
        )
        grant_project_access(conn, user_id=legal_user["id"], project_id=project_id)
        grant_field_access(
            conn,
            user_id=legal_user["id"],
            project_id=project_id,
            data_domain="project",
            field_name="website",
        )
        context = AccessContext.from_user(legal_user)
    finally:
        conn.close()

    result = call_tool(
        "get_project_fields",
        {
            "project_id_or_name": "ACME",
            "fields": ["website", "notes"],
            "rationale": "query official website and notes",
        },
        database_path=database_path,
        access_context=context,
    )

    assert result["error"]["code"] == "field_access_denied"
    assert result["error"]["details"]["denied_fields"] == {
        "notes": "field_not_granted"
    }
    assert "project" not in result


def test_get_project_context_rejects_full_context_calls(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        seed_project(conn, code="ACME", name="Acme")
    finally:
        conn.close()

    result = call_tool(
        "get_project_context",
        {"project_id_or_name": "ACME", "rationale": "legacy query"},
        database_path=database_path,
        access_context=AccessContext.local_operator(),
    )

    assert result["error"]["code"] == "deprecated_tool"


def test_get_contract_fields_returns_only_requested_amount(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        project_id = seed_project(conn, code="ACME", name="Acme")
        conn.execute(
            """
            insert into contracts (
              project_id, external_key, title, contract_number, total_amount, currency
            )
            values (?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                "SHYBYBZ2025000082",
                "Acme KOL Contract",
                "SHYBYBZ2025000082",
                "11690",
                "人民币",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    result = call_tool(
        "get_contract_fields",
        {
            "contract_number": "SHYBYBZ2025000082",
            "fields": ["total_amount", "currency"],
            "rationale": "query contract amount",
        },
        database_path=database_path,
        access_context=AccessContext.local_operator(),
    )

    assert result == {
        "contract": {
            "contract_number": "SHYBYBZ2025000082",
            "title": "Acme KOL Contract",
            "currency": "人民币",
            "total_amount": "11690",
        }
    }


def test_get_contract_fields_denies_ungranted_contract_fields(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        project_id = seed_project(conn, code="ACME", name="Acme")
        conn.execute(
            """
            insert into contracts (
              project_id, external_key, title, contract_number, total_amount, currency
            )
            values (?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                "SHYBYBZ2025000082",
                "Acme KOL Contract",
                "SHYBYBZ2025000082",
                "11690",
                "人民币",
            ),
        )
        legal_user = create_user(
            conn,
            email="legal@example.com",
            display_name="Legal User",
            role=ROLE_LEGAL,
        )
        grant_project_access(conn, user_id=legal_user["id"], project_id=project_id)
        grant_field_access(
            conn,
            user_id=legal_user["id"],
            project_id=project_id,
            data_domain="contract",
            field_name="total_amount",
        )
        context = AccessContext.from_user(legal_user)
        conn.commit()
    finally:
        conn.close()

    result = call_tool(
        "get_contract_fields",
        {
            "contract_number": "SHYBYBZ2025000082",
            "fields": ["total_amount", "currency"],
            "rationale": "query contract amount and currency",
        },
        database_path=database_path,
        access_context=context,
    )

    assert result["error"]["code"] == "field_access_denied"
    assert result["error"]["details"]["denied_fields"] == {
        "currency": "field_not_granted"
    }
    assert "contract" not in result


def test_list_project_contracts_returns_visible_project_contracts(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        project_id = seed_project(conn, code="ACME", name="Acme")
        conn.executemany(
            """
            insert into contracts (
              project_id, external_key, title, contract_number, total_amount, currency
            )
            values (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    project_id,
                    "SHYBYBZ2025000082",
                    "Acme KOL Contract",
                    "SHYBYBZ2025000082",
                    "11690",
                    "人民币",
                ),
                (
                    project_id,
                    "SHYBYBZ2025000081",
                    "Acme Creator Contract",
                    "SHYBYBZ2025000081",
                    "7000",
                    "人民币",
                ),
            ],
        )
        legal_user = create_user(
            conn,
            email="legal@example.com",
            display_name="Legal User",
            role=ROLE_LEGAL,
        )
        grant_project_access(conn, user_id=legal_user["id"], project_id=project_id)
        grant_field_access(
            conn,
            user_id=legal_user["id"],
            project_id=project_id,
            data_domain="contract",
            field_name="total_amount",
        )
        grant_field_access(
            conn,
            user_id=legal_user["id"],
            project_id=project_id,
            data_domain="contract",
            field_name="currency",
        )
        context = AccessContext.from_user(legal_user)
        conn.commit()
    finally:
        conn.close()

    result = call_tool(
        "list_project_contracts",
        {
            "project_id_or_name": "ACME",
            "fields": ["total_amount", "currency"],
            "rationale": "query project contracts",
        },
        database_path=database_path,
        access_context=context,
    )

    assert result == {
        "contracts": [
            {
                "contract_number": "SHYBYBZ2025000081",
                "title": "Acme Creator Contract",
                "currency": "人民币",
                "total_amount": "7000",
            },
            {
                "contract_number": "SHYBYBZ2025000082",
                "title": "Acme KOL Contract",
                "currency": "人民币",
                "total_amount": "11690",
            },
        ]
    }


def test_list_project_contracts_denies_ungranted_contract_fields(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        project_id = seed_project(conn, code="ACME", name="Acme")
        conn.execute(
            """
            insert into contracts (
              project_id, external_key, title, contract_number, total_amount, currency
            )
            values (?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                "SHYBYBZ2025000082",
                "Acme KOL Contract",
                "SHYBYBZ2025000082",
                "11690",
                "人民币",
            ),
        )
        legal_user = create_user(
            conn,
            email="legal@example.com",
            display_name="Legal User",
            role=ROLE_LEGAL,
        )
        grant_project_access(conn, user_id=legal_user["id"], project_id=project_id)
        grant_field_access(
            conn,
            user_id=legal_user["id"],
            project_id=project_id,
            data_domain="contract",
            field_name="total_amount",
        )
        context = AccessContext.from_user(legal_user)
        conn.commit()
    finally:
        conn.close()

    result = call_tool(
        "list_project_contracts",
        {
            "project_id_or_name": "ACME",
            "fields": ["total_amount", "currency"],
            "rationale": "query project contracts",
        },
        database_path=database_path,
        access_context=context,
    )

    assert result["error"]["code"] == "field_access_denied"
    assert result["error"]["details"]["denied_fields"] == {
        "currency": "field_not_granted"
    }
    assert "contracts" not in result


def test_list_project_contracts_returns_access_denied_without_access(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        project_id = seed_project(conn, code="ACME", name="Acme")
        conn.execute(
            """
            insert into contracts (
              project_id, external_key, title, contract_number, total_amount
            )
            values (?, ?, ?, ?, ?)
            """,
            (
                project_id,
                "SHYBYBZ2025000082",
                "Acme KOL Contract",
                "SHYBYBZ2025000082",
                "11690",
            ),
        )
        legal_user = create_user(
            conn,
            email="legal@example.com",
            display_name="Legal User",
            role=ROLE_LEGAL,
        )
        context = AccessContext.from_user(legal_user)
        conn.commit()
    finally:
        conn.close()

    result = call_tool(
        "list_project_contracts",
        {
            "project_id_or_name": "ACME",
            "fields": ["total_amount"],
            "rationale": "query project contracts",
        },
        database_path=database_path,
        access_context=context,
    )

    assert result["error"]["code"] == "access_denied"
    assert "联系管理员" in result["error"]["message"]


def test_legal_user_can_list_project_license_operator_fields_by_project_code(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        project_id = seed_project(conn, code="T", name="Project T")
        conn.execute(
            """
            insert into licenses (
              project_id, external_key, license_type, identifier,
              operating_entity, actual_operator
            )
            values (?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                "publication_license",
                "publication_license",
                "ISBN-T",
                "版号运营主体 T",
                "实际运营主体 T",
            ),
        )
        legal_user = create_user(
            conn,
            email="legal@example.com",
            display_name="Legal User",
            role=ROLE_LEGAL,
        )
        grant_project_access(conn, user_id=legal_user["id"], project_id=project_id)
        grant_field_access(
            conn,
            user_id=legal_user["id"],
            project_id=project_id,
            data_domain="license",
            field_name="actual_operator",
        )
        grant_field_access(
            conn,
            user_id=legal_user["id"],
            project_id=project_id,
            data_domain="license",
            field_name="operating_entity",
        )
        context = AccessContext.from_user(legal_user)
        conn.commit()
    finally:
        conn.close()

    result = call_tool(
        "list_project_licenses",
        {
            "project_id_or_name": "T",
            "fields": ["actual_operator", "operating_entity"],
            "rationale": "query project operator",
        },
        database_path=database_path,
        access_context=context,
    )

    assert result == {
        "licenses": [
            {
                "license_type": "publication_license",
                "identifier": "ISBN-T",
                "actual_operator": "实际运营主体 T",
                "operating_entity": "版号运营主体 T",
            }
        ]
    }


def test_list_project_licenses_denies_ungranted_license_fields(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        project_id = seed_project(conn, code="T", name="Project T")
        conn.execute(
            """
            insert into licenses (
              project_id, external_key, license_type, identifier,
              operating_entity, actual_operator
            )
            values (?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                "publication_license",
                "publication_license",
                "ISBN-T",
                "版号运营主体 T",
                "实际运营主体 T",
            ),
        )
        legal_user = create_user(
            conn,
            email="legal@example.com",
            display_name="Legal User",
            role=ROLE_LEGAL,
        )
        grant_project_access(conn, user_id=legal_user["id"], project_id=project_id)
        grant_field_access(
            conn,
            user_id=legal_user["id"],
            project_id=project_id,
            data_domain="license",
            field_name="actual_operator",
        )
        context = AccessContext.from_user(legal_user)
        conn.commit()
    finally:
        conn.close()

    result = call_tool(
        "list_project_licenses",
        {
            "project_id_or_name": "T",
            "fields": ["actual_operator", "operating_entity"],
            "rationale": "query project operator",
        },
        database_path=database_path,
        access_context=context,
    )

    assert result["error"]["code"] == "field_access_denied"
    assert result["error"]["details"]["denied_fields"] == {
        "operating_entity": "field_not_granted"
    }
    assert "licenses" not in result


def test_resolve_project_returns_not_found_for_ambiguous_project(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        seed_project(conn, code="GAME-001", name="Shared Name")
        seed_project(conn, code="GAME-002", name="Shared Name")
    finally:
        conn.close()

    result = call_tool(
        "resolve_project",
        {"query": "Shared Name", "rationale": "review status"},
        database_path=database_path,
        access_context=AccessContext.local_operator(),
    )

    assert result["error"]["code"] == "not_found"


def test_expiring_license_boundaries_exclude_null_and_late_dates(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    today = date.today()
    conn = db.connect(database_path)
    try:
        project_id = seed_project(conn)
        conn.executemany(
            """
            insert into licenses (
              project_id, external_key, license_type, identifier, expiry_date
            )
            values (?, ?, ?, ?, ?)
            """,
            [
                (project_id, "expires_today", "publication", "A", today.isoformat()),
                (
                    project_id,
                    "expires_boundary",
                    "publication",
                    "B",
                    (today + timedelta(days=30)).isoformat(),
                ),
                (
                    project_id,
                    "expires_late",
                    "publication",
                    "C",
                    (today + timedelta(days=31)).isoformat(),
                ),
                (project_id, "no_expiry", "publication", "D", None),
            ],
        )
        conn.commit()
    finally:
        conn.close()

    result = call_tool(
        "list_expiring_licenses",
        {"days_ahead": 30, "rationale": "renewal planning"},
        database_path=database_path,
        access_context=AccessContext.local_operator(),
    )

    assert [license_["external_key"] for license_ in result["licenses"]] == [
        "expires_today",
        "expires_boundary",
    ]


def test_list_expiring_licenses_filters_to_business_project_access_grants(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    expiry_date = (date.today() + timedelta(days=7)).isoformat()
    conn = db.connect(database_path)
    try:
        visible_project_id = seed_project(conn, code="GAME-001", name="Visible Project")
        hidden_project_id = seed_project(conn, code="GAME-002", name="Hidden Project")
        conn.executemany(
            """
            insert into licenses (
              project_id, external_key, license_type, identifier, expiry_date
            )
            values (?, ?, ?, ?, ?)
            """,
            [
                (visible_project_id, "visible-license", "publication", "A", expiry_date),
                (hidden_project_id, "hidden-license", "publication", "B", expiry_date),
            ],
        )
        business_user = create_user(
            conn,
            email="business@example.com",
            display_name="Business User",
            role=ROLE_BUSINESS,
        )
        grant_project_access(conn, user_id=business_user["id"], project_id=visible_project_id)
        context = AccessContext.from_user(business_user)
        conn.commit()
    finally:
        conn.close()

    result = call_tool(
        "list_expiring_licenses",
        {"days_ahead": 30, "rationale": "renewal planning"},
        database_path=database_path,
        access_context=context,
    )

    assert [license_["external_key"] for license_ in result["licenses"]] == [
        "visible-license"
    ]
    assert [license_["project_code"] for license_ in result["licenses"]] == ["GAME-001"]


def test_open_risks_exclude_closed_risks(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        project_id = seed_project(conn)
        conn.executemany(
            """
            insert into risks (project_id, external_key, description, status)
            values (?, ?, ?, ?)
            """,
            [
                (project_id, "risk-open", "Needs review", "open"),
                (project_id, "risk-closed", "Resolved", "closed"),
            ],
        )
        conn.commit()
    finally:
        conn.close()

    result = call_tool(
        "list_open_risks",
        {"project_code": "GAME-001", "rationale": "prepare weekly summary"},
        database_path=database_path,
        access_context=AccessContext.local_operator(),
    )

    assert [risk["external_key"] for risk in result["risks"]] == ["risk-open"]


def test_list_open_risks_filters_to_business_project_access_grants(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        visible_project_id = seed_project(conn, code="GAME-001", name="Visible Project")
        hidden_project_id = seed_project(conn, code="GAME-002", name="Hidden Project")
        conn.executemany(
            """
            insert into risks (project_id, external_key, description, status)
            values (?, ?, ?, ?)
            """,
            [
                (visible_project_id, "visible-risk", "Needs review", "open"),
                (hidden_project_id, "hidden-risk", "Also needs review", "open"),
            ],
        )
        business_user = create_user(
            conn,
            email="business@example.com",
            display_name="Business User",
            role=ROLE_BUSINESS,
        )
        grant_project_access(conn, user_id=business_user["id"], project_id=visible_project_id)
        context = AccessContext.from_user(business_user)
        conn.commit()
    finally:
        conn.close()

    result = call_tool(
        "list_open_risks",
        {"rationale": "prepare weekly summary"},
        database_path=database_path,
        access_context=context,
    )
    filtered_result = call_tool(
        "list_open_risks",
        {"project_code": "GAME-001", "rationale": "prepare weekly summary"},
        database_path=database_path,
        access_context=context,
    )

    assert [risk["external_key"] for risk in result["risks"]] == ["visible-risk"]
    assert [risk["project_code"] for risk in result["risks"]] == ["GAME-001"]
    assert [risk["external_key"] for risk in filtered_result["risks"]] == [
        "visible-risk"
    ]
    assert [risk["project_code"] for risk in filtered_result["risks"]] == ["GAME-001"]


def test_list_projects_filters_to_business_project_access_grants(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        visible_project_id = seed_project(conn, code="GAME-001", name="Visible Project")
        seed_project(conn, code="GAME-002", name="Hidden Project")
        business_user = create_user(
            conn,
            email="business@example.com",
            display_name="Business User",
            role=ROLE_BUSINESS,
        )
        grant_project_access(conn, user_id=business_user["id"], project_id=visible_project_id)
        context = AccessContext.from_user(business_user)
    finally:
        conn.close()

    result = call_tool(
        "list_projects",
        {"rationale": "review accessible projects"},
        database_path=database_path,
        access_context=context,
    )

    assert [project["project_code"] for project in result["projects"]] == ["GAME-001"]


def test_get_project_fields_returns_access_denied_for_hidden_project(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        seed_project(conn, code="GAME-001", name="Visible Project")
        seed_project(conn, code="GAME-002", name="Hidden Project")
        business_user = create_user(
            conn,
            email="business@example.com",
            display_name="Business User",
            role=ROLE_BUSINESS,
        )
        context = AccessContext.from_user(business_user)
    finally:
        conn.close()

    result = call_tool(
        "get_project_fields",
        {
            "project_id_or_name": "GAME-002",
            "fields": ["website"],
            "rationale": "review project context",
        },
        database_path=database_path,
        access_context=context,
    )

    assert result["error"]["code"] == "access_denied"
    assert "联系管理员" in result["error"]["message"]


def test_get_project_fields_returns_not_found_when_name_is_ambiguous(
    tmp_path,
) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        visible_project_id = seed_project(conn, code="GAME-001", name="Shared Name")
        seed_project(conn, code="GAME-002", name="Shared Name")
        business_user = create_user(
            conn,
            email="business@example.com",
            display_name="Business User",
            role=ROLE_BUSINESS,
        )
        grant_project_access(conn, user_id=business_user["id"], project_id=visible_project_id)
        context = AccessContext.from_user(business_user)
    finally:
        conn.close()

    result = call_tool(
        "get_project_fields",
        {
            "project_id_or_name": "Shared Name",
            "fields": ["website"],
            "rationale": "review project context",
        },
        database_path=database_path,
        access_context=context,
    )

    assert result["error"]["code"] == "not_found"


def test_get_project_fields_returns_not_found_for_ambiguous_visible_projects(
    tmp_path,
) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        visible_project_id = seed_project(conn, code="GAME-001", name="Shared Name")
        other_visible_project_id = seed_project(conn, code="GAME-002", name="Shared Name")
        seed_project(conn, code="GAME-003", name="Shared Name")
        business_user = create_user(
            conn,
            email="business@example.com",
            display_name="Business User",
            role=ROLE_BUSINESS,
        )
        grant_project_access(conn, user_id=business_user["id"], project_id=visible_project_id)
        grant_project_access(
            conn,
            user_id=business_user["id"],
            project_id=other_visible_project_id,
        )
        context = AccessContext.from_user(business_user)
    finally:
        conn.close()

    result = call_tool(
        "get_project_fields",
        {
            "project_id_or_name": "Shared Name",
            "fields": ["website"],
            "rationale": "review project context",
        },
        database_path=database_path,
        access_context=context,
    )

    assert result["error"]["code"] == "not_found"


def test_get_project_fields_hidden_ambiguous_candidates_return_not_found(
    tmp_path,
) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        seed_project(conn, code="GAME-001", name="Shared Name")
        seed_project(conn, code="GAME-002", name="Shared Name")
        business_user = create_user(
            conn,
            email="business@example.com",
            display_name="Business User",
            role=ROLE_BUSINESS,
        )
        context = AccessContext.from_user(business_user)
    finally:
        conn.close()

    result = call_tool(
        "get_project_fields",
        {
            "project_id_or_name": "Shared Name",
            "fields": ["website"],
            "rationale": "review project context",
        },
        database_path=database_path,
        access_context=context,
    )

    assert result["error"]["code"] == "not_found"
    assert result["error"]["candidates"] == []


def test_legal_user_list_projects_filters_to_project_access_grants(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        visible_project_id = seed_project(conn, code="GAME-001", name="First Project")
        seed_project(conn, code="GAME-002", name="Second Project")
        legal_user = create_user(
            conn,
            email="legal@example.com",
            display_name="Legal User",
            role=ROLE_LEGAL,
        )
        grant_project_access(conn, user_id=legal_user["id"], project_id=visible_project_id)
        context = AccessContext.from_user(legal_user)
    finally:
        conn.close()

    result = call_tool(
        "list_projects",
        {"rationale": "review accessible projects"},
        database_path=database_path,
        access_context=context,
    )

    assert [project["project_code"] for project in result["projects"]] == ["GAME-001"]


def test_legal_user_get_project_fields_returns_access_denied_for_hidden_project(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        visible_project_id = seed_project(conn, code="ACME", name="Acme")
        seed_project(conn, code="OTHER", name="Other Project")
        legal_user = create_user(
            conn,
            email="legal@test.com",
            display_name="Legal User",
            role=ROLE_LEGAL,
        )
        grant_project_access(conn, user_id=legal_user["id"], project_id=visible_project_id)
        context = AccessContext.from_user(legal_user)
    finally:
        conn.close()

    result = call_tool(
        "get_project_fields",
        {
            "project_id_or_name": "OTHER",
            "fields": ["website"],
            "rationale": "review project context",
        },
        database_path=database_path,
        access_context=context,
    )

    assert result["error"]["code"] == "access_denied"
    assert "联系管理员" in result["error"]["message"]


def test_auditor_cannot_call_content_tools(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        seed_project(conn)
        auditor_user = create_user(
            conn,
            email="auditor@example.com",
            display_name="Auditor User",
            role=ROLE_AUDITOR,
        )
        context = AccessContext.from_user(auditor_user)
    finally:
        conn.close()

    for tool_name, arguments in {
        "list_projects": {"rationale": "audit project list"},
        "get_project_context": {
            "project_id_or_name": "GAME-001",
            "rationale": "audit project details",
        },
        "list_expiring_licenses": {"rationale": "audit license list"},
        "list_open_risks": {"rationale": "audit risk list"},
    }.items():
        result = call_tool(
            tool_name,
            arguments,
            database_path=database_path,
            access_context=context,
        )

        assert result["error"]["code"] == "access_denied"


def test_agent_query_uses_server_configured_ai_provider(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        project_id = seed_project(conn, code="ACME", name="示例项目")
        conn.execute(
            """
            insert into licenses (project_id, external_key, license_type, rights_holder)
            values (?, ?, ?, ?)
            """,
            (project_id, "trademark_right", "trademark_right", "上海游碧曜网络科技有限公司"),
        )
        conn.execute(
            "update agent_settings set ai_api_key = ?, ai_model = ? where id = 1",
            ("server-key", "server-model"),
        )
        conn.commit()
    finally:
        conn.close()

    for name in (
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "LEGAL_MCP_AI_PROVIDER",
        "LEGAL_MCP_AI_MODEL",
        "LEGAL_MCP_AI_BASE_URL",
        "LEGAL_MCP_AI_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    seen: dict[str, object] = {}

    class FakeProvider:
        def __init__(
            self,
            *,
            api_key: str,
            model: str,
            base_url: str | None = None,
            use_json_mode: bool = True,
        ) -> None:
            seen["api_key"] = api_key
            seen["model"] = model
            seen["base_url"] = base_url
            seen["use_json_mode"] = use_json_mode

        def complete(self, messages: list[AIMessage]) -> AIMessage:
            return AIMessage(
                role="assistant",
                content=(
                    '{"domain":"license","operation":"search",'
                    '"filters":['
                    '{"field":"project_code","operator":"eq","value":"ACME"},'
                    '{"field":"license_type","operator":"eq","value":"trademark_right"}'
                    '],'
                    '"return_fields":["license_type","rights_holder"],'
                    '"limit":20}'
                ),
            )

    monkeypatch.setattr("legal_mcp.ai_provider.OpenAICompatibleProvider", FakeProvider)

    result = call_tool(
        "agent_query",
        {"question": "请告诉我 ACME trademark holder", "rationale": "pytest"},
        database_path=database_path,
        audit_path=tmp_path / "audit.jsonl",
        access_context=AccessContext.local_operator(),
    )

    assert seen["api_key"] == "server-key"
    assert seen["model"] == "server-model"
    assert result["status"] == "success"
    assert "上海游碧曜网络科技有限公司" in result["answer"]


def test_agent_query_access_fast_path_does_not_load_ai_config(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # v0.4.6 §A: the project-field fast path is gone, but the access-scope fast
    # path stays deterministic — an access question must still resolve without
    # ever loading or calling the AI backend.
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        seed_project(conn, code="ACME", name="示例项目")
    finally:
        conn.close()

    def fail_load_config(database_path):
        raise AssertionError("access fast path should not load AI config")

    monkeypatch.setattr("legal_mcp.agent_config.load_agent_config", fail_load_config)

    result = call_tool(
        "agent_query",
        {"question": "我有什么权限", "rationale": "pytest"},
        database_path=database_path,
        audit_path=tmp_path / "audit.jsonl",
        access_context=AccessContext.local_operator(),
    )

    assert result["status"] == "success"
    assert result["tool_calls"][0]["tool_name"] == "describe_my_access"


def test_agent_query_mcp_response_does_not_expose_executable_internal_plan(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        project_id = seed_project(conn, code="Acme", name="示例项目")
        conn.execute(
            """
            insert into licenses (project_id, external_key, license_type, rights_holder)
            values (?, ?, ?, ?)
            """,
            (project_id, "trademark_right", "trademark_right", "上海游碧曜网络科技有限公司"),
        )
        conn.commit()
    finally:
        conn.close()

    for name in (
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "LEGAL_MCP_AI_PROVIDER",
        "LEGAL_MCP_AI_MODEL",
        "LEGAL_MCP_AI_BASE_URL",
        "LEGAL_MCP_AI_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    # The graph is model-driven; inject a stub planner so the test runs offline.
    monkeypatch.setattr(
        "legal_mcp.ai_provider.provider_from_config",
        lambda _config: _StubPlanner(
            '{"domain":"license","operation":"search",'
            '"filters":['
            '{"field":"project_code","operator":"eq","value":"Acme"},'
            '{"field":"license_type","operator":"eq","value":"trademark_right"}'
            '],'
            '"return_fields":["license_type","rights_holder"]}'
        ),
    )

    result = call_tool(
        "agent_query",
        {"question": "Acme 的商标在哪家公司", "rationale": "pytest"},
        database_path=database_path,
        audit_path=tmp_path / "audit.jsonl",
        access_context=AccessContext.local_operator(),
    )

    assert result["status"] == "success"
    assert "上海游碧曜网络科技有限公司" in result["answer"]
    assert "result" not in result
    assert all("plan" not in tool_call for tool_call in result["tool_calls"])
