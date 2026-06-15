from __future__ import annotations

from pathlib import Path

from legal_mcp import db
from legal_mcp.identity import ROLE_BUSINESS, ROLE_LEGAL, create_user
from legal_mcp.policy import AccessContext
from legal_mcp.query_authorization import authorize_query_plan
from legal_mcp.query_plan import QueryFilter, QueryPlan


def _seed_user_without_project_field(
    tmp_path: Path,
    *,
    denied_field: str,
    granted_fields: set[str],
):
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    project_id = conn.execute(
        """
        insert into projects (project_code, name, stage, legal_bp, website)
        values (?, ?, ?, ?, ?)
        """,
        ("ACME", "示例项目", "live", "张三", "https://acme.example"),
    ).lastrowid
    user = create_user(
        conn,
        email=f"legal-{denied_field}@example.com",
        display_name="Legal User",
        role=ROLE_LEGAL,
    )
    grantor = create_user(
        conn,
        email=f"grantor-{denied_field}@example.com",
        display_name="Grantor",
        role=ROLE_LEGAL,
    )
    conn.execute(
        """
        insert into project_access (user_id, project_id, granted_by_user_id)
        values (?, ?, ?)
        """,
        (user["id"], project_id, grantor["id"]),
    )
    group_id = conn.execute(
        "insert into user_groups (name) values (?)",
        (f"group-{denied_field}",),
    ).lastrowid
    conn.execute(
        "insert into user_group_memberships (user_id, group_id) values (?, ?)",
        (user["id"], group_id),
    )
    for field in sorted(granted_fields):
        conn.execute(
            """
            insert into permission_grants
              (group_id, operation, data_domain, field_name, project_id)
            values (?, ?, ?, ?, ?)
            """,
            (group_id, "read", "project", field, project_id),
        )
    conn.commit()
    return conn, AccessContext.from_user(user)


def _insert_user(conn, email: str) -> int:
    user = create_user(conn, email=email, display_name="Business User", role=ROLE_BUSINESS)
    return int(user["id"])


def _insert_project(conn, code: str) -> int:
    cursor = conn.execute(
        "insert into projects (project_code, name, stage) values (?, ?, ?)",
        (code, code, "live"),
    )
    return int(cursor.lastrowid)


def _grant_project_access(conn, user_id: int, project_id: int) -> None:
    conn.execute(
        """
        insert into project_access (user_id, project_id, granted_by_user_id)
        values (?, ?, ?)
        """,
        (user_id, project_id, user_id),
    )


def _grant_field(conn, user_id: int, project_id: int, domain: str, field: str) -> None:
    group_id = conn.execute(
        "insert into user_groups (name) values (?)",
        (f"grp-{user_id}-{project_id}-{domain}-{field}",),
    ).lastrowid
    conn.execute(
        "insert or ignore into user_group_memberships (user_id, group_id) values (?, ?)",
        (user_id, group_id),
    )
    conn.execute(
        """
        insert into permission_grants
          (group_id, operation, data_domain, field_name, project_id)
        values (?, ?, ?, ?, ?)
        """,
        (group_id, "read", domain, field, project_id),
    )


def test_authorize_license_query_allows_project_identity_filter_without_license_grant(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        user_id = _insert_user(conn, "business@example.com")
        project_id = _insert_project(conn, "Acme")
        _grant_project_access(conn, user_id, project_id)
        _grant_field(conn, user_id, project_id, "license", "rights_holder")
        result = authorize_query_plan(
            conn,
            QueryPlan(
                domain="license",
                operation="search",
                filters=[
                    QueryFilter(field="project_code", operator="eq", value="Acme"),
                    QueryFilter(field="license_type", operator="eq", value="trademark_right"),
                ],
                return_fields=["license_type", "rights_holder"],
                limit=20,
            ),
            AccessContext(user_id=user_id, role="business"),
        )
    finally:
        conn.close()

    assert result.ok


def test_authorize_license_query_rejects_overbroad_return_fields(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        user_id = _insert_user(conn, "business@example.com")
        project_id = _insert_project(conn, "Acme")
        _grant_project_access(conn, user_id, project_id)
        for field in (
            "rights_holder",
            "copyright_holder",
            "operating_entity",
            "actual_operator",
            "approval_number",
        ):
            _grant_field(conn, user_id, project_id, "license", field)
        result = authorize_query_plan(
            conn,
            QueryPlan(
                domain="license",
                operation="search",
                filters=[QueryFilter(field="project_code", operator="eq", value="Acme")],
                return_fields=[
                    "license_type",
                    "identifier",
                    "rights_holder",
                    "copyright_holder",
                    "operating_entity",
                    "actual_operator",
                    "approval_number",
                ],
                limit=20,
            ),
            AccessContext(user_id=user_id, role="business"),
        )
    finally:
        conn.close()

    assert result.ok is False
    assert result.error_code == "overbroad_return_fields"


def test_domain_wide_null_field_grant_authorizes_specific_field(tmp_path: Path) -> None:
    # A grant row with NULL field_name means "all fields in the domain".
    # describe_my_access honors this; authorize_fields must agree so a
    # domain-wide grant does not deny every specific field (the 1.4.3 bug).
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        user_id = _insert_user(conn, "business@example.com")
        project_id = _insert_project(conn, "Acme")
        _grant_project_access(conn, user_id, project_id)
        group_id = conn.execute(
            "insert into user_groups (name) values (?)", ("grp-domain-wide",)
        ).lastrowid
        conn.execute(
            "insert into user_group_memberships (user_id, group_id) values (?, ?)",
            (user_id, group_id),
        )
        conn.execute(
            """
            insert into permission_grants
              (group_id, operation, data_domain, field_name, project_id)
            values (?, 'read', 'project', NULL, NULL)
            """,
            (group_id,),
        )
        conn.commit()
        result = authorize_query_plan(
            conn,
            QueryPlan(
                domain="project",
                operation="search",
                filters=[QueryFilter(field="name", operator="eq", value="示例项目")],
                return_fields=["release_team"],
                limit=20,
            ),
            AccessContext(user_id=user_id, role="business"),
        )
    finally:
        conn.close()

    assert result.ok


def test_filter_field_requires_read_permission(tmp_path: Path) -> None:
    conn, context = _seed_user_without_project_field(
        tmp_path,
        denied_field="legal_bp",
        granted_fields={"website"},
    )
    try:
        plan = QueryPlan(
            domain="project",
            operation="search",
            filters=[QueryFilter(field="legal_bp", operator="eq", value="张三")],
            return_fields=["project_code", "name"],
            limit=20,
        )

        result = authorize_query_plan(conn, plan, context)
    finally:
        conn.close()

    assert result.ok is False
    assert result.error_code == "filter_field_access_denied"
    assert result.disclosures[0].field_name == "legal_bp"
    assert result.disclosures[0].decision == "denied"


def test_virtual_identity_filter_is_exempt_from_field_gate(tmp_path: Path) -> None:
    # v0.4.8: filtering on the virtual 'identity' field never trips the field gate,
    # exactly as the identity fields it expands to don't — even with no field grants
    # beyond a single return field, it authorizes.
    conn, context = _seed_user_without_project_field(
        tmp_path,
        denied_field="legal_bp",
        granted_fields={"legal_bp"},
    )
    try:
        plan = QueryPlan(
            domain="project",
            operation="search",
            filters=[QueryFilter(field="identity", operator="contains", value="ACME")],
            return_fields=["legal_bp"],
            limit=20,
        )
        result = authorize_query_plan(conn, plan, context)
    finally:
        conn.close()
    assert result.ok is True


def test_return_field_requires_read_permission(tmp_path: Path) -> None:
    conn, context = _seed_user_without_project_field(
        tmp_path,
        denied_field="website",
        granted_fields={"legal_bp"},
    )
    try:
        plan = QueryPlan(
            domain="project",
            operation="search",
            filters=[QueryFilter(field="legal_bp", operator="eq", value="张三")],
            return_fields=["project_code", "name", "website"],
            limit=20,
        )

        result = authorize_query_plan(conn, plan, context)
    finally:
        conn.close()

    assert result.ok is False
    assert result.error_code == "return_field_access_denied"
    assert result.disclosures[0].field_name == "website"
    assert result.disclosures[0].decision == "denied"
