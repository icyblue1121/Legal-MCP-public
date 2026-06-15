import json
import sqlite3

from legal_mcp import db
from legal_mcp.disclosure_audit import write_audit_event
from legal_mcp.identity import ROLE_AUDITOR, ROLE_BUSINESS, ROLE_LEGAL, create_user
from legal_mcp.policy import AccessContext
from legal_mcp.query_authorization import authorize_query_plan
from legal_mcp.query_plan import QueryFilter, QueryPlan
from legal_mcp.ai_provider import AIMessage
from legal_mcp.tools import call_tool


class _StubPlanner:
    """Offline stand-in for the server-side AI planner."""

    def __init__(self, content: str) -> None:
        self.content = content

    def complete(self, messages: list[AIMessage]) -> AIMessage:
        return AIMessage(role="assistant", content=self.content)


def test_audit_log_records_successful_and_failed_tool_calls(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    audit_path = tmp_path / "audit.jsonl"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        conn.execute(
            "insert into projects (project_code, name, stage) values (?, ?, ?)",
            ("GAME-001", "Project One", "live"),
        )
        conn.commit()
    finally:
        conn.close()

    call_tool(
        "list_projects",
        {"rationale": "status review", "source_client": "pytest"},
        database_path=database_path,
        audit_path=audit_path,
        access_context=AccessContext.local_operator(),
    )
    call_tool(
        "get_project_context",
        {"project_id_or_name": "Missing", "rationale": "status review"},
        database_path=database_path,
        audit_path=audit_path,
        access_context=AccessContext.local_operator(),
    )

    records = [json.loads(line) for line in audit_path.read_text().splitlines()]
    assert records[0]["tool_name"] == "list_projects"
    assert records[0]["rationale"] == "status review"
    assert records[0]["source_client"] == "pytest"
    assert records[0]["result_status"] == "success"
    assert records[0]["error_code"] is None
    assert records[1]["tool_name"] == "get_project_context"
    assert records[1]["result_status"] == "error"
    assert records[1]["error_code"] == "deprecated_tool"
    assert "Missing" in records[1]["arguments_summary"]


def test_tool_call_writes_database_audit_event_and_project_disclosure(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    audit_path = tmp_path / "audit.jsonl"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        project_id = conn.execute(
            "insert into projects (project_code, name, stage) values (?, ?, ?)",
            ("GAME-001", "Project One", "live"),
        ).lastrowid
        business_user = create_user(
            conn,
            email="business@example.com",
            display_name="Business User",
            role=ROLE_BUSINESS,
        )
        legal_user = create_user(
            conn,
            email="legal@example.com",
            display_name="Legal User",
            role=ROLE_LEGAL,
        )
        conn.execute(
            """
            insert into project_access (user_id, project_id, granted_by_user_id)
            values (?, ?, ?)
            """,
            (business_user["id"], project_id, legal_user["id"]),
        )
        conn.commit()
        context = AccessContext.from_user(business_user)
    finally:
        conn.close()

    call_tool(
        "list_projects",
        {
            "rationale": "prepare business summary",
            "source_client": "pytest-client",
        },
        database_path=database_path,
        audit_path=audit_path,
        access_context=context,
    )

    conn = db.connect(database_path)
    try:
        event = conn.execute("select * from audit_events").fetchone()
        disclosure = conn.execute("select * from audit_disclosures").fetchone()
    finally:
        conn.close()

    assert event["user_id"] == business_user["id"]
    assert event["tool_name"] == "list_projects"
    assert event["rationale"] == "prepare business summary"
    assert event["source_client"] == "pytest-client"
    assert disclosure["project_id"] == project_id
    assert disclosure["record_type"] == "project"
    assert disclosure["decision"] == "allowed"


def test_project_field_query_records_allowed_field_disclosure(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    audit_path = tmp_path / "audit.jsonl"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        conn.execute(
            "insert into projects (project_code, name, stage, website) values (?, ?, ?, ?)",
            ("ACME", "Acme", "live", "https://acme.example"),
        )
        conn.commit()
    finally:
        conn.close()

    result = call_tool(
        "get_project_fields",
        {
            "project_id_or_name": "ACME",
            "fields": ["website"],
            "rationale": "query website",
        },
        database_path=database_path,
        audit_path=audit_path,
        access_context=AccessContext.local_operator(),
    )

    assert result["project"]["website"] == "https://acme.example"
    conn = db.connect(database_path)
    try:
        rows = conn.execute(
            "select field_name, decision from audit_disclosures order by id"
        ).fetchall()
    finally:
        conn.close()
    assert ("website", "allowed") in [
        (row["field_name"], row["decision"]) for row in rows
    ]


def test_denied_project_field_query_records_denied_field_disclosure(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    audit_path = tmp_path / "audit.jsonl"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        project_id = conn.execute(
            """
            insert into projects (project_code, name, stage, website, notes)
            values (?, ?, ?, ?, ?)
            """,
            ("ACME", "Acme", "live", "https://acme.example", "Sensitive notes"),
        ).lastrowid
        business_user = create_user(
            conn,
            email="business@example.com",
            display_name="Business User",
            role=ROLE_BUSINESS,
        )
        legal_user = create_user(
            conn,
            email="legal@example.com",
            display_name="Legal User",
            role=ROLE_LEGAL,
        )
        conn.execute(
            """
            insert into project_access (user_id, project_id, granted_by_user_id)
            values (?, ?, ?)
            """,
            (business_user["id"], project_id, legal_user["id"]),
        )
        group_id = conn.execute(
            "insert into user_groups (name) values (?)",
            ("business-project-website",),
        ).lastrowid
        conn.execute(
            "insert into user_group_memberships (user_id, group_id) values (?, ?)",
            (business_user["id"], group_id),
        )
        conn.execute(
            """
            insert into permission_grants
              (group_id, operation, data_domain, field_name, project_id)
            values (?, ?, ?, ?, ?)
            """,
            (group_id, "read", "project", "website", project_id),
        )
        conn.commit()
        context = AccessContext.from_user(business_user)
    finally:
        conn.close()

    result = call_tool(
        "get_project_fields",
        {
            "project_id_or_name": "ACME",
            "fields": ["website", "notes"],
            "rationale": "query website and notes",
        },
        database_path=database_path,
        audit_path=audit_path,
        access_context=context,
    )

    conn = db.connect(database_path)
    try:
        event = conn.execute("select * from audit_events").fetchone()
        disclosures = conn.execute(
            """
            select project_id, record_type, record_id, field_name, decision, reason
            from audit_disclosures
            order by field_name
            """
        ).fetchall()
    finally:
        conn.close()

    assert result["error"]["code"] == "field_access_denied"
    assert event["result_status"] == "error"
    assert event["error_code"] == "field_access_denied"
    assert [dict(row) for row in disclosures] == [
        {
            "project_id": project_id,
            "record_type": "project",
            "record_id": project_id,
            "field_name": "notes",
            "decision": "denied",
            "reason": "field_not_granted",
        }
    ]


def test_denied_query_filter_field_can_be_audited(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        project_id = conn.execute(
            """
            insert into projects (project_code, name, stage, legal_bp)
            values (?, ?, ?, ?)
            """,
            ("ACME", "Acme", "live", "张三"),
        ).lastrowid
        business_user = create_user(
            conn,
            email="query-filter@example.com",
            display_name="Query Filter User",
            role=ROLE_BUSINESS,
        )
        legal_user = create_user(
            conn,
            email="query-filter-legal@example.com",
            display_name="Legal User",
            role=ROLE_LEGAL,
        )
        conn.execute(
            """
            insert into project_access (user_id, project_id, granted_by_user_id)
            values (?, ?, ?)
            """,
            (business_user["id"], project_id, legal_user["id"]),
        )
        conn.commit()
        context = AccessContext.from_user(business_user)
        plan = QueryPlan(
            domain="project",
            operation="search",
            filters=[QueryFilter(field="legal_bp", operator="eq", value="张三")],
            return_fields=["project_code", "name"],
            limit=20,
        )
        authorization = authorize_query_plan(conn, plan, context)
        result = {
            "error": {
                "code": authorization.error_code,
                "message": authorization.message,
            }
        }
        write_audit_event(
            conn,
            context,
            "structured_query",
            "find projects by legal bp",
            "pytest",
            {"query": {"domain": "project"}},
            result,
            authorization.disclosures,
        )
        disclosure = conn.execute(
            """
            select project_id, record_type, field_name, decision, reason
            from audit_disclosures
            """
        ).fetchone()
    finally:
        conn.close()

    assert authorization.error_code == "filter_field_access_denied"
    assert dict(disclosure) == {
        "project_id": project_id,
        "record_type": "project",
        "field_name": "legal_bp",
        "decision": "denied",
        # Per-user grants (v0.4.0 §C C3) unified the deny reason: a field with no
        # applicable grant — whether the user lacks groups or simply lacks this
        # grant — is "field_not_granted". The old "no_group_membership" special
        # case is gone now that a user can be granted fields directly.
        "reason": "field_not_granted",
    }


def test_agent_query_project_search_records_graph_disclosure(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        project_id = conn.execute(
            """
            insert into projects (project_code, name, stage, legal_bp)
            values (?, ?, ?, ?)
            """,
            ("ACME", "示例项目", "live", "张三"),
        ).lastrowid
        conn.commit()
    finally:
        conn.close()

    # The graph is model-driven; inject a stub planner so the test runs offline.
    monkeypatch.setattr(
        "legal_mcp.ai_provider.provider_from_config",
        lambda _config: _StubPlanner(
            '{"domain":"project","operation":"search",'
            '"filters":[{"field":"legal_bp","operator":"eq","value":"张三"}],'
            '"return_fields":["project_code","name"]}'
        ),
    )

    result = call_tool(
        "agent_query",
        {
            "question": "张三是哪些项目的法务BP？",
            "rationale": "agent smoke test",
        },
        database_path=database_path,
        access_context=AccessContext.local_operator(),
    )

    conn = db.connect(database_path)
    try:
        disclosure = conn.execute(
            """
            select project_id, record_type, record_id, decision, reason
            from audit_disclosures
            """
        ).fetchone()
    finally:
        conn.close()

    assert result["tool_calls"][0]["tool_name"] == "project/search"
    assert dict(disclosure) == {
        "project_id": project_id,
        "record_type": "project",
        "record_id": project_id,
        "decision": "allowed",
        "reason": "project_visible",
    }


def test_hidden_project_lookup_records_denied_disclosure_without_leaking_project(
    tmp_path,
) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        hidden_project_id = conn.execute(
            "insert into projects (project_code, name, stage) values (?, ?, ?)",
            ("GAME-002", "Hidden Project", "live"),
        ).lastrowid
        business_user = create_user(
            conn,
            email="business@example.com",
            display_name="Business User",
            role=ROLE_BUSINESS,
        )
        conn.commit()
        context = AccessContext.from_user(business_user)
    finally:
        conn.close()

    result = call_tool(
        "get_project_context",
        {"project_id_or_name": "GAME-002", "rationale": "prepare business summary"},
        database_path=database_path,
        access_context=context,
    )

    conn = db.connect(database_path)
    try:
        event = conn.execute("select * from audit_events").fetchone()
        disclosure = conn.execute("select * from audit_disclosures").fetchone()
    finally:
        conn.close()

    assert result["error"]["code"] == "deprecated_tool"
    assert result["error"]["candidates"] == []
    assert result["error"]["details"] == {}
    assert event["result_status"] == "error"
    assert event["error_code"] == "deprecated_tool"
    assert disclosure is None


def test_hidden_ambiguous_lookup_records_denied_disclosures_without_candidates(
    tmp_path,
) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        first_project_id = conn.execute(
            "insert into projects (project_code, name, stage) values (?, ?, ?)",
            ("GAME-001", "Shared Name", "live"),
        ).lastrowid
        second_project_id = conn.execute(
            "insert into projects (project_code, name, stage) values (?, ?, ?)",
            ("GAME-002", "Shared Name", "live"),
        ).lastrowid
        business_user = create_user(
            conn,
            email="business@example.com",
            display_name="Business User",
            role=ROLE_BUSINESS,
        )
        conn.commit()
        context = AccessContext.from_user(business_user)
    finally:
        conn.close()

    result = call_tool(
        "get_project_context",
        {"project_id_or_name": "Shared Name", "rationale": "prepare business summary"},
        database_path=database_path,
        access_context=context,
    )

    conn = db.connect(database_path)
    try:
        disclosures = conn.execute(
            "select project_id, record_type, decision from audit_disclosures order by project_id"
        ).fetchall()
    finally:
        conn.close()

    assert result["error"]["code"] == "deprecated_tool"
    assert result["error"]["candidates"] == []
    assert disclosures == []


def test_visible_ambiguous_lookup_records_allowed_candidate_disclosures(
    tmp_path,
) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        first_project_id = conn.execute(
            "insert into projects (project_code, name, stage) values (?, ?, ?)",
            ("GAME-001", "Shared Name", "live"),
        ).lastrowid
        second_project_id = conn.execute(
            "insert into projects (project_code, name, stage) values (?, ?, ?)",
            ("GAME-002", "Shared Name", "live"),
        ).lastrowid
        business_user = create_user(
            conn,
            email="business@example.com",
            display_name="Business User",
            role=ROLE_BUSINESS,
        )
        legal_user = create_user(
            conn,
            email="legal@example.com",
            display_name="Legal User",
            role=ROLE_LEGAL,
        )
        conn.executemany(
            """
            insert into project_access (user_id, project_id, granted_by_user_id)
            values (?, ?, ?)
            """,
            [
                (business_user["id"], first_project_id, legal_user["id"]),
                (business_user["id"], second_project_id, legal_user["id"]),
            ],
        )
        conn.commit()
        context = AccessContext.from_user(business_user)
    finally:
        conn.close()

    result = call_tool(
        "get_project_context",
        {"project_id_or_name": "Shared Name", "rationale": "prepare business summary"},
        database_path=database_path,
        access_context=context,
    )

    conn = db.connect(database_path)
    try:
        disclosures = conn.execute(
            """
            select project_id, record_type, record_id, decision, reason
            from audit_disclosures
            order by project_id
            """
        ).fetchall()
    finally:
        conn.close()

    assert result["error"]["code"] == "deprecated_tool"
    assert result["error"]["candidates"] == []
    assert disclosures == []


def test_open_risks_hidden_project_code_records_denied_disclosure_without_leak(
    tmp_path,
) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        visible_project_id = conn.execute(
            "insert into projects (project_code, name, stage) values (?, ?, ?)",
            ("GAME-001", "Visible Project", "live"),
        ).lastrowid
        hidden_project_id = conn.execute(
            "insert into projects (project_code, name, stage) values (?, ?, ?)",
            ("GAME-002", "Hidden Project", "live"),
        ).lastrowid
        conn.execute(
            "insert into risks (project_id, external_key, description, status) values (?, ?, ?, ?)",
            (hidden_project_id, "hidden-risk", "Hidden risk", "open"),
        )
        business_user = create_user(
            conn,
            email="business@example.com",
            display_name="Business User",
            role=ROLE_BUSINESS,
        )
        legal_user = create_user(
            conn,
            email="legal@example.com",
            display_name="Legal User",
            role=ROLE_LEGAL,
        )
        conn.execute(
            """
            insert into project_access (user_id, project_id, granted_by_user_id)
            values (?, ?, ?)
            """,
            (business_user["id"], visible_project_id, legal_user["id"]),
        )
        conn.commit()
        context = AccessContext.from_user(business_user)
    finally:
        conn.close()

    result = call_tool(
        "list_open_risks",
        {"project_code": "GAME-002", "rationale": "prepare business summary"},
        database_path=database_path,
        access_context=context,
    )

    conn = db.connect(database_path)
    try:
        disclosure = conn.execute(
            "select project_id, record_type, record_id, decision, reason from audit_disclosures"
        ).fetchone()
    finally:
        conn.close()

    assert result["error"]["code"] == "access_denied"
    assert disclosure["project_id"] == hidden_project_id
    assert disclosure["record_type"] == "project"
    assert disclosure["record_id"] == hidden_project_id
    assert disclosure["decision"] == "denied"
    assert disclosure["reason"] == "project_hidden"


def test_auditor_denial_records_database_audit_event_without_disclosure(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        auditor_user = create_user(
            conn,
            email="auditor@example.com",
            display_name="Auditor User",
            role=ROLE_AUDITOR,
        )
        context = AccessContext.from_user(auditor_user)
    finally:
        conn.close()

    result = call_tool(
        "get_project_context",
        {"project_id_or_name": "GAME-001", "rationale": "audit project details"},
        database_path=database_path,
        access_context=context,
    )

    conn = db.connect(database_path)
    try:
        event = conn.execute("select * from audit_events").fetchone()
        disclosure_count = conn.execute("select count(*) from audit_disclosures").fetchone()[0]
    finally:
        conn.close()

    assert result["error"]["code"] == "access_denied"
    assert event["user_id"] == auditor_user["id"]
    assert event["tool_name"] == "get_project_context"
    assert event["result_status"] == "error"
    assert event["error_code"] == "access_denied"
    assert disclosure_count == 0


def test_database_audit_failure_preserves_tool_response_and_warns(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        conn.execute(
            "insert into projects (project_code, name, stage) values (?, ?, ?)",
            ("GAME-001", "Project One", "live"),
        )
        conn.commit()
    finally:
        conn.close()

    def fail_audit(*args, **kwargs):
        raise sqlite3.OperationalError("audit store unavailable")

    monkeypatch.setattr("legal_mcp.tools.write_audit_event", fail_audit)

    result = call_tool(
        "list_projects",
        {"rationale": "status review"},
        database_path=database_path,
        access_context=AccessContext.local_operator(),
    )

    captured = capsys.readouterr()
    assert [project["project_code"] for project in result["projects"]] == ["GAME-001"]
    assert "database audit write failed" in captured.err
    assert "audit store unavailable" in captured.err
