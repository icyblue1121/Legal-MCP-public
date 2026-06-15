from __future__ import annotations

import sqlite3

import pytest

from legal_mcp import db
from legal_mcp.disclosure_audit import (
    Disclosure,
    count_audit_events,
    list_audit_events,
    write_audit_event,
)
from legal_mcp.identity import ROLE_BUSINESS, create_api_key, create_user
from legal_mcp.policy import AccessContext


@pytest.fixture()
def conn(tmp_path) -> sqlite3.Connection:
    db_path = tmp_path / "legal.db"
    db.initialize_database(db_path)
    connection = db.connect(db_path)
    try:
        yield connection
    finally:
        connection.close()


def _project(conn: sqlite3.Connection, code: str) -> int:
    cursor = conn.execute(
        "insert into projects (project_code, name, stage) values (?, ?, ?)",
        (code, f"{code} Project", "live"),
    )
    conn.commit()
    return int(cursor.lastrowid)


def test_write_audit_event_persists_event_and_disclosure(
    conn: sqlite3.Connection,
) -> None:
    user = create_user(
        conn,
        email="business@example.com",
        display_name="Business User",
        role=ROLE_BUSINESS,
    )
    key = create_api_key(conn, user_id=user["id"], label="pytest")
    project_id = _project(conn, "GAME-001")
    context = AccessContext.from_user(user, api_key_id=key.api_key_id)

    event_id = write_audit_event(
        conn,
        context=context,
        tool_name="list_contracts",
        rationale="contract review",
        source_client="pytest",
        arguments={"project_id": project_id, "rationale": "contract review"},
        result={"contracts": [{"id": 7, "title": "MSA"}]},
        disclosures=[
            Disclosure(
                project_id=project_id,
                record_type="contract",
                record_id=7,
                decision="allowed",
                reason="business user has project access",
            )
        ],
    )

    event = conn.execute(
        "select * from audit_events where id = ?",
        (event_id,),
    ).fetchone()
    assert event["user_id"] == user["id"]
    assert event["api_key_id"] == key.api_key_id
    assert event["tool_name"] == "list_contracts"
    assert event["result_status"] == "success"
    assert event["error_code"] is None
    assert event["response_record_count"] == 1

    disclosure = conn.execute(
        "select * from audit_disclosures where audit_event_id = ?",
        (event_id,),
    ).fetchone()
    assert disclosure["project_id"] == project_id
    assert disclosure["record_type"] == "contract"
    assert disclosure["record_id"] == 7
    assert disclosure["decision"] == "allowed"
    assert disclosure["reason"] == "business user has project access"


def test_write_audit_event_records_identity_source(
    conn: sqlite3.Connection,
) -> None:
    # v0.4.5 Phase 2: a reviewer must be able to tell which identity source
    # resolved a disclosure (api-key vs trusted proxy header) from the audit row.
    user = create_user(
        conn,
        email="proxied@example.com",
        display_name="Proxied User",
        role=ROLE_BUSINESS,
        external_subject="oidc|alice",
    )
    context = AccessContext.from_user(user, identity_source="trusted_header")

    event_id = write_audit_event(
        conn,
        context=context,
        tool_name="agent_query",
        rationale=None,
        source_client="pytest",
        arguments={},
        result={},
        disclosures=[],
    )

    row = conn.execute(
        "select user_id, api_key_id, identity_source from audit_events where id = ?",
        (event_id,),
    ).fetchone()
    assert row["user_id"] == user["id"]
    assert row["api_key_id"] is None
    assert row["identity_source"] == "trusted_header"


def test_write_audit_event_persists_disclosure_without_record_id(
    conn: sqlite3.Connection,
) -> None:
    user = create_user(
        conn,
        email="business@example.com",
        display_name="Business User",
        role=ROLE_BUSINESS,
    )
    project_id = _project(conn, "GAME-001")
    context = AccessContext.from_user(user)

    event_id = write_audit_event(
        conn,
        context=context,
        tool_name="summarize_project",
        rationale="project summary",
        source_client="pytest",
        arguments={"project_id": project_id},
        result={"summary": {"project_id": project_id, "risk_count": 0}},
        disclosures=[
            Disclosure(
                project_id=project_id,
                record_type="summary",
                record_id=None,
                decision="allowed",
                reason="aggregate project summary",
            )
        ],
    )

    disclosure = conn.execute(
        "select record_id from audit_disclosures where audit_event_id = ?",
        (event_id,),
    ).fetchone()
    assert disclosure["record_id"] is None


def test_write_audit_event_persists_denied_field_disclosure(
    conn: sqlite3.Connection,
) -> None:
    user = create_user(
        conn,
        email="business@example.com",
        display_name="Business User",
        role=ROLE_BUSINESS,
    )
    project_id = _project(conn, "GAME-001")
    context = AccessContext.from_user(user)

    event_id = write_audit_event(
        conn,
        context=context,
        tool_name="get_project_fields",
        rationale="query project notes",
        source_client="pytest",
        arguments={"project_id_or_name": "GAME-001", "fields": ["notes"]},
        result={
            "error": {
                "code": "field_access_denied",
                "message": "one or more requested fields are not granted",
                "candidates": [],
                "details": {"denied_fields": {"notes": "field_not_granted"}},
            }
        },
        disclosures=[
            Disclosure(
                project_id=project_id,
                record_type="project",
                record_id=project_id,
                field_name="notes",
                decision="denied",
                reason="field_not_granted",
            )
        ],
    )

    disclosure = conn.execute(
        """
        select project_id, record_type, record_id, field_name, decision, reason
        from audit_disclosures
        where audit_event_id = ?
        """,
        (event_id,),
    ).fetchone()
    assert dict(disclosure) == {
        "project_id": project_id,
        "record_type": "project",
        "record_id": project_id,
        "field_name": "notes",
        "decision": "denied",
        "reason": "field_not_granted",
    }


def test_write_audit_event_persists_without_context(
    conn: sqlite3.Connection,
) -> None:
    event_id = write_audit_event(
        conn,
        context=None,
        tool_name="list_public_projects",
        rationale=None,
        source_client="pytest",
        arguments={},
        result={"projects": []},
        disclosures=[],
    )

    event = conn.execute(
        "select user_id, api_key_id from audit_events where id = ?",
        (event_id,),
    ).fetchone()
    assert event["user_id"] is None
    assert event["api_key_id"] is None


def test_list_audit_events_filters_by_project_id_and_returns_email_and_tool_name(
    conn: sqlite3.Connection,
) -> None:
    user = create_user(
        conn,
        email="business@example.com",
        display_name="Business User",
        role=ROLE_BUSINESS,
    )
    project_id = _project(conn, "GAME-001")
    other_project_id = _project(conn, "GAME-002")
    context = AccessContext.from_user(user)

    write_audit_event(
        conn,
        context=context,
        tool_name="visible_tool",
        rationale=None,
        source_client=None,
        arguments={},
        result={"projects": [{"id": project_id}]},
        disclosures=[
            Disclosure(
                project_id=project_id,
                record_type="project",
                record_id=project_id,
                decision="allowed",
                reason="included in response",
            )
        ],
    )
    write_audit_event(
        conn,
        context=context,
        tool_name="hidden_tool",
        rationale=None,
        source_client=None,
        arguments={},
        result={"projects": [{"id": other_project_id}]},
        disclosures=[
            Disclosure(
                project_id=other_project_id,
                record_type="project",
                record_id=other_project_id,
                decision="allowed",
                reason="included in response",
            )
        ],
    )

    rows = list_audit_events(conn, project_id=project_id)

    assert len(rows) == 1
    assert rows[0]["email"] == "business@example.com"
    assert rows[0]["tool_name"] == "visible_tool"


@pytest.mark.parametrize("limit", [-10, 0])
def test_list_audit_events_normalizes_non_positive_limits(
    conn: sqlite3.Connection,
    limit: int,
) -> None:
    for index in range(2):
        write_audit_event(
            conn,
            context=None,
            tool_name=f"tool_{index}",
            rationale=None,
            source_client=None,
            arguments={},
            result={"projects": []},
            disclosures=[],
        )

    rows = list_audit_events(conn, limit=limit)

    assert len(rows) == 1


def test_list_audit_events_caps_large_limits(conn: sqlite3.Connection) -> None:
    for index in range(501):
        write_audit_event(
            conn,
            context=None,
            tool_name=f"tool_{index}",
            rationale=None,
            source_client=None,
            arguments={},
            result={"projects": []},
            disclosures=[],
        )

    rows = list_audit_events(conn, limit=9999)

    assert len(rows) == 500


def _seed_events(conn: sqlite3.Connection, count: int) -> None:
    for index in range(count):
        write_audit_event(
            conn,
            context=None,
            tool_name=f"tool_{index}",
            rationale=None,
            source_client=None,
            arguments={},
            result={"projects": []},
            disclosures=[],
        )


def test_count_audit_events_matches_total(conn: sqlite3.Connection) -> None:
    _seed_events(conn, 250)
    assert count_audit_events(conn) == 250


def test_list_audit_events_offset_paginates(conn: sqlite3.Connection) -> None:
    _seed_events(conn, 250)

    page1 = list_audit_events(conn, limit=100, offset=0)
    page3 = list_audit_events(conn, limit=100, offset=200)

    assert len(page1) == 100
    assert len(page3) == 50  # 250 total -> last page holds the remainder
    # Newest first, and pages do not overlap.
    assert page1[0]["id"] > page1[-1]["id"]
    assert {row["id"] for row in page1}.isdisjoint({row["id"] for row in page3})


def test_list_audit_events_offset_beyond_end_is_empty(
    conn: sqlite3.Connection,
) -> None:
    _seed_events(conn, 10)
    assert list_audit_events(conn, limit=100, offset=100) == []


def test_count_audit_events_respects_filters(conn: sqlite3.Connection) -> None:
    _seed_events(conn, 5)
    assert count_audit_events(conn, tool_name="tool_0") == 1
    assert count_audit_events(conn, tool_name="does-not-exist") == 0
