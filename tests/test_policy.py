from __future__ import annotations

import sqlite3

import pytest

from legal_mcp import db
from legal_mcp.identity import (
    ROLE_ADMIN,
    ROLE_AUDITOR,
    ROLE_BUSINESS,
    ROLE_LEGAL,
    create_user,
)
from legal_mcp.policy import (
    AccessContext,
    authorize_fields,
    can_query_content,
    project_is_visible,
    record_owner_subject,
    visible_project_ids,
)


def test_record_owner_subject_returns_the_context_attribute() -> None:
    context = AccessContext(
        user_id=1, role=ROLE_BUSINESS, email="a@x", external_subject="oidc|alice"
    )
    assert record_owner_subject(context, "external_subject") == "oidc|alice"
    assert record_owner_subject(context, "email") == "a@x"
    assert record_owner_subject(context, "user_id") == "1"


def test_record_owner_subject_is_fail_closed_not_unrestricted() -> None:
    # The red line: legacy / None / unrestricted-but-unmapped resolve to None, which
    # the caller treats as ZERO rows — never the visible_project_ids None=all sentinel.
    assert record_owner_subject(None, "external_subject") is None
    assert record_owner_subject(AccessContext.legacy(), "external_subject") is None
    assert record_owner_subject(AccessContext.local_operator(), "external_subject") is None
    # missing/blank attribute → None
    no_subject = AccessContext(user_id=1, role=ROLE_BUSINESS, external_subject=None)
    assert record_owner_subject(no_subject, "external_subject") is None
    blank = AccessContext(user_id=1, role=ROLE_BUSINESS, external_subject="   ")
    assert record_owner_subject(blank, "external_subject") is None


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


def test_admin_can_see_all_projects_and_query_content(
    conn: sqlite3.Connection,
) -> None:
    project_ids = {_project(conn, "GAME-001"), _project(conn, "GAME-002")}
    admin_user = create_user(
        conn,
        email="admin@example.com",
        display_name="Admin User",
        role=ROLE_ADMIN,
    )

    admin_context = AccessContext.from_user(admin_user)

    assert can_query_content(admin_context) is True
    assert visible_project_ids(conn, admin_context) == project_ids


def test_from_user_normalizes_required_identity_fields() -> None:
    context = AccessContext.from_user(
        {"id": "42", "role": ROLE_BUSINESS, "email": "business@example.com"},
        api_key_id=7,
    )

    assert context.user_id == 42
    assert context.role == ROLE_BUSINESS
    assert context.email == "business@example.com"
    assert context.api_key_id == 7


def test_legacy_context_is_fail_closed_by_default(
    conn: sqlite3.Connection,
) -> None:
    # v0.4.5 preflight: the shared migration token no longer bypasses the gates.
    # Without the explicit opt-in it has no grants, so it discloses nothing.
    project_id = _project(conn, "GAME-001")
    context = AccessContext.legacy()

    assert visible_project_ids(conn, context) == set()
    assert project_is_visible(conn, context, project_id) is False
    decision = authorize_fields(
        conn,
        context,
        operation="read",
        data_domain="project",
        project_id=project_id,
        requested_fields={"website", "legal_bp"},
    )
    assert decision.allowed_fields == set()
    assert decision.denied_fields == {
        "website": "field_not_granted",
        "legal_bp": "field_not_granted",
    }


def test_legacy_context_full_access_only_behind_explicit_opt_in(
    conn: sqlite3.Connection,
) -> None:
    # The migration escape hatch (--legacy-token-full-access) restores full access.
    project_id = _project(conn, "GAME-001")
    context = AccessContext.legacy(unrestricted=True)

    assert can_query_content(context) is True
    assert visible_project_ids(conn, context) is None
    assert project_is_visible(conn, context, project_id) is True
    decision = authorize_fields(
        conn,
        context,
        operation="read",
        data_domain="project",
        project_id=project_id,
        requested_fields={"website", "legal_bp"},
    )
    assert decision.allowed_fields == {"website", "legal_bp"}
    assert decision.denied_fields == {}


def test_none_context_is_fail_closed_at_all_gates(
    conn: sqlite3.Connection,
) -> None:
    # v0.4.5 Phase 1: with every entry point now minting an explicit context (the
    # resolver seam for network paths, ``local_operator()`` for stdio), a ``None``
    # context can only arrive by mistake — so it discloses nothing. Full local
    # access is the explicit ``local_operator()`` capability, never a None default.
    project_id = _project(conn, "GAME-001")

    assert can_query_content(None) is False
    assert visible_project_ids(conn, None) == set()
    assert project_is_visible(conn, None, project_id) is False
    decision = authorize_fields(
        conn,
        None,
        operation="read",
        data_domain="project",
        project_id=project_id,
        requested_fields={"website", "legal_bp"},
    )
    assert decision.allowed_fields == set()
    assert decision.denied_fields == {
        "website": "field_not_granted",
        "legal_bp": "field_not_granted",
    }


def test_local_operator_is_explicitly_unrestricted(
    conn: sqlite3.Connection,
) -> None:
    # The stdio local operator gets full access through an explicit capability,
    # not through the (now fail-closed) None default.
    project_id = _project(conn, "GAME-001")
    context = AccessContext.local_operator()

    assert can_query_content(context) is True
    assert visible_project_ids(conn, context) is None
    assert project_is_visible(conn, context, project_id) is True
    decision = authorize_fields(
        conn,
        context,
        operation="read",
        data_domain="project",
        project_id=project_id,
        requested_fields={"website", "legal_bp"},
    )
    assert decision.allowed_fields == {"website", "legal_bp"}


@pytest.mark.parametrize("role", [ROLE_BUSINESS, ROLE_LEGAL])
def test_scoped_user_can_see_only_project_access_grants_and_query_content(
    conn: sqlite3.Connection,
    role: str,
) -> None:
    visible_project_id = _project(conn, "GAME-001")
    hidden_project_id = _project(conn, "GAME-002")
    grantor = create_user(
        conn,
        email=f"grantor-{role}@example.com",
        display_name="Legal User",
        role=ROLE_LEGAL,
    )
    scoped_user = create_user(
        conn,
        email=f"{role}@example.com",
        display_name="Scoped User",
        role=role,
    )
    conn.execute(
        """
        insert into project_access (user_id, project_id, granted_by_user_id)
        values (?, ?, ?)
        """,
        (scoped_user["id"], visible_project_id, grantor["id"]),
    )
    conn.commit()

    context = AccessContext.from_user(scoped_user)

    assert can_query_content(context) is True
    assert visible_project_ids(conn, context) == {visible_project_id}
    assert hidden_project_id not in visible_project_ids(conn, context)
    assert project_is_visible(conn, context, visible_project_id) is True
    assert project_is_visible(conn, context, hidden_project_id) is False


@pytest.mark.parametrize("role", [ROLE_BUSINESS, ROLE_LEGAL])
def test_scoped_user_with_no_grants_gets_empty_visible_project_ids(
    conn: sqlite3.Connection,
    role: str,
) -> None:
    project_id = _project(conn, "GAME-001")
    scoped_user = create_user(
        conn,
        email=f"{role}@example.com",
        display_name="Scoped User",
        role=role,
    )

    context = AccessContext.from_user(scoped_user)

    assert visible_project_ids(conn, context) == set()
    assert project_is_visible(conn, context, project_id) is False


def test_auditor_visible_project_ids_is_empty_and_cannot_query_content(
    conn: sqlite3.Connection,
) -> None:
    _project(conn, "GAME-001")
    auditor_user = create_user(
        conn,
        email="auditor@example.com",
        display_name="Auditor User",
        role=ROLE_AUDITOR,
    )

    context = AccessContext.from_user(auditor_user)

    assert can_query_content(context) is False
    assert visible_project_ids(conn, context) == set()


def test_group_permission_allows_only_granted_project_fields(
    conn: sqlite3.Connection,
) -> None:
    project_id = _project(conn, "ACME")
    user = create_user(
        conn,
        email="legal-fields@example.com",
        display_name="Legal Fields",
        role=ROLE_LEGAL,
    )
    group_id = conn.execute(
        "insert into user_groups (name) values (?)",
        ("ACME Field Readers",),
    ).lastrowid
    conn.execute(
        "insert into user_group_memberships (user_id, group_id) values (?, ?)",
        (user["id"], group_id),
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

    context = AccessContext.from_user(user)
    decision = authorize_fields(
        conn,
        context,
        operation="read",
        data_domain="project",
        project_id=project_id,
        requested_fields={"website", "legal_bp"},
    )

    assert decision.allowed_fields == {"website"}
    assert decision.denied_fields == {"legal_bp": "field_not_granted"}


def test_direct_user_grant_allows_field_and_peer_is_denied(
    conn: sqlite3.Connection,
) -> None:
    # C3: a grant keyed directly to a user (no group) authorizes that user's
    # field; a peer without the grant is denied. Asserts the effective scope is
    # "direct ∪ groups", not group-only.
    project_id = _project(conn, "ACME")
    granted = create_user(
        conn, email="direct@example.com", display_name="Direct", role=ROLE_LEGAL
    )
    peer = create_user(
        conn, email="peer@example.com", display_name="Peer", role=ROLE_LEGAL
    )
    conn.execute(
        """
        insert into permission_grants
          (user_id, operation, data_domain, field_name, project_id)
        values (?, ?, ?, ?, ?)
        """,
        (granted["id"], "read", "project", "website", project_id),
    )
    conn.commit()

    granted_decision = authorize_fields(
        conn,
        AccessContext.from_user(granted),
        operation="read",
        data_domain="project",
        project_id=project_id,
        requested_fields={"website", "legal_bp"},
    )
    assert granted_decision.allowed_fields == {"website"}
    assert granted_decision.denied_fields == {"legal_bp": "field_not_granted"}

    peer_decision = authorize_fields(
        conn,
        AccessContext.from_user(peer),
        operation="read",
        data_domain="project",
        project_id=project_id,
        requested_fields={"website"},
    )
    assert peer_decision.allowed_fields == set()
    assert peer_decision.denied_fields == {"website": "field_not_granted"}


def test_grant_exactly_one_grantee_constraint(conn: sqlite3.Connection) -> None:
    # The schema enforces exactly one grantee: neither / both is rejected.
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "insert into permission_grants (operation, data_domain) "
            "values ('read', 'project')"
        )
    conn.rollback()
    group_id = conn.execute(
        "insert into user_groups (name) values ('G')"
    ).lastrowid
    user = create_user(
        conn, email="both@example.com", display_name="Both", role=ROLE_LEGAL
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            insert into permission_grants (group_id, user_id, operation, data_domain)
            values (?, ?, 'read', 'project')
            """,
            (group_id, user["id"]),
        )
