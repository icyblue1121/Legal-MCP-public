from __future__ import annotations

from pathlib import Path

from legal_mcp import db
from legal_mcp.identity import ROLE_LEGAL, create_user
from legal_mcp.policy import AccessContext
from legal_mcp.query_plan import QueryFilter, QueryPlan
from legal_mcp.search_tools import (
    execute_search_plan,
    search_contracts,
    search_cross_domain,
    search_licenses,
    search_projects,
)


def _seed_database(path: Path) -> AccessContext:
    db.initialize_database(path)
    conn = db.connect(path)
    try:
        visible_a = _insert_project(conn, "Acme", "示例项目", "张三")
        visible_b = _insert_project(conn, "代号 T", "指尖魔宠", "张三")
        hidden = _insert_project(conn, "HIDDEN", "隐藏项目", "张三")
        _insert_contract(conn, visible_a, "C-001", "腾讯框架合同", "腾讯科技", "张三")
        _insert_contract(conn, visible_b, "C-002", "米哈游联运合同", "米哈游", "李四")
        _insert_contract(conn, hidden, "C-999", "隐藏合同", "腾讯科技", "张三")
        _insert_license(conn, visible_a, "L-001", "版号", "上海运营公司", "某公司")
        _insert_license(conn, visible_b, "L-002", "软著", "某公司", "某公司")
        _insert_license(conn, hidden, "L-999", "隐藏资质", "某公司", "某公司")
        user = create_user(
            conn,
            email="legal-search@example.com",
            display_name="Legal Search",
            role=ROLE_LEGAL,
        )
        grantor = create_user(
            conn,
            email="legal-search-grantor@example.com",
            display_name="Grantor",
            role=ROLE_LEGAL,
        )
        for project_id in (visible_a, visible_b):
            conn.execute(
                """
                insert into project_access (user_id, project_id, granted_by_user_id)
                values (?, ?, ?)
                """,
                (user["id"], project_id, grantor["id"]),
            )
            _grant_field(conn, user["id"], project_id, "project", "legal_bp")
            _grant_field(conn, user["id"], project_id, "contract", "counterparty")
            _grant_field(conn, user["id"], project_id, "contract", "handler")
            _grant_field(conn, user["id"], project_id, "license", "actual_operator")
            _grant_field(conn, user["id"], project_id, "license", "operating_entity")
        conn.commit()
        return AccessContext.from_user(user)
    finally:
        conn.close()


def _insert_project(conn, code: str, name: str, legal_bp: str) -> int:
    cursor = conn.execute(
        """
        insert into projects (
          project_code, name, stage, legal_bp, department, release_team
        )
        values (?, ?, ?, ?, ?, ?)
        """,
        (code, name, "live", legal_bp, "法务部", "发行中心"),
    )
    return int(cursor.lastrowid)


def _insert_contract(
    conn,
    project_id: int,
    contract_number: str,
    title: str,
    counterparty: str,
    handler: str,
) -> None:
    conn.execute(
        """
        insert into contracts (
          project_id, external_key, title, contract_number, counterparty, handler
        )
        values (?, ?, ?, ?, ?, ?)
        """,
        (project_id, contract_number, title, contract_number, counterparty, handler),
    )


def _insert_license(
    conn,
    project_id: int,
    external_key: str,
    license_type: str,
    operating_entity: str,
    actual_operator: str,
) -> None:
    conn.execute(
        """
        insert into licenses (
          project_id, external_key, license_type, operating_entity, actual_operator
        )
        values (?, ?, ?, ?, ?)
        """,
        (project_id, external_key, license_type, operating_entity, actual_operator),
    )


def _grant_field(
    conn,
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


def test_search_projects_by_legal_bp_returns_visible_projects(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    context = _seed_database(database_path)
    conn = db.connect(database_path)
    try:
        result = search_projects(
            conn,
            QueryPlan(
                domain="project",
                operation="search",
                filters=[QueryFilter(field="legal_bp", operator="eq", value="张三")],
                return_fields=["project_code", "name"],
                limit=20,
            ),
            access_context=context,
        )
    finally:
        conn.close()

    assert result == {
        "projects": [
            {"project_code": "Acme", "name": "示例项目"},
            {"project_code": "代号 T", "name": "指尖魔宠"},
        ]
    }


def test_search_contracts_by_counterparty_contains_respects_visibility(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    context = _seed_database(database_path)
    conn = db.connect(database_path)
    try:
        result = search_contracts(
            conn,
            QueryPlan(
                domain="contract",
                operation="search",
                filters=[QueryFilter(field="counterparty", operator="contains", value="腾讯")],
                return_fields=["contract_number", "title", "counterparty"],
                limit=20,
            ),
            access_context=context,
        )
    finally:
        conn.close()

    assert result == {
        "contracts": [
            {
                "contract_number": "C-001",
                "title": "腾讯框架合同",
                "counterparty": "腾讯科技",
            }
        ]
    }


def test_search_licenses_by_actual_operator_and_limit(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    context = _seed_database(database_path)
    conn = db.connect(database_path)
    try:
        result = search_licenses(
            conn,
            QueryPlan(
                domain="license",
                operation="search",
                filters=[QueryFilter(field="actual_operator", operator="eq", value="某公司")],
                return_fields=["license_type", "actual_operator"],
                limit=1,
            ),
            access_context=context,
        )
    finally:
        conn.close()

    assert result == {
        "licenses": [{"license_type": "版号", "actual_operator": "某公司"}]
    }


def test_search_cross_domain_matches_visible_records_only(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    context = _seed_database(database_path)
    conn = db.connect(database_path)
    try:
        result = search_cross_domain(
            conn,
            QueryPlan(
                domain="cross_domain",
                operation="search",
                filters=[QueryFilter(field="q", operator="contains", value="张三")],
                return_fields=["project_code", "name", "contract_number", "title"],
                limit=20,
            ),
            access_context=context,
        )
    finally:
        conn.close()

    assert [project["project_code"] for project in result["projects"]] == [
        "Acme",
        "代号 T",
    ]
    assert [contract["contract_number"] for contract in result["contracts"]] == ["C-001"]
    assert result["licenses"] == []
    assert "HIDDEN" not in str(result)


def test_execute_search_plan_dispatches_by_domain(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    context = _seed_database(database_path)
    conn = db.connect(database_path)
    try:
        result = execute_search_plan(
            conn,
            QueryPlan(
                domain="project",
                operation="search",
                filters=[QueryFilter(field="legal_bp", operator="eq", value="张三")],
                return_fields=["project_code"],
                limit=1,
            ),
            access_context=context,
        )
    finally:
        conn.close()

    assert result == {"projects": [{"project_code": "Acme"}]}


def test_search_licenses_can_filter_by_project_code(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        project_id = _insert_project(conn, "Acme", "示例项目", "张三")
        conn.execute(
            """
            insert into licenses (project_id, external_key, license_type, rights_holder)
            values (?, ?, ?, ?)
            """,
            (project_id, "trademark_right", "trademark_right", "上海游碧曜网络科技有限公司"),
        )
        conn.commit()
        result = execute_search_plan(
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
            access_context=AccessContext.local_operator(),
        )
    finally:
        conn.close()

    assert result == {
        "licenses": [
            {
                "license_type": "trademark_right",
                "rights_holder": "上海游碧曜网络科技有限公司",
            }
        ]
    }


# --- v0.4.8: virtual identity filter on the SQLite direct path ----------------


def _identity_seed(path: Path) -> AccessContext:
    """Visible projects sharing a name fragment (for ambiguity) plus a hidden one
    that also matches (to prove record scope is applied before ranking)."""
    db.initialize_database(path)
    conn = db.connect(path)
    try:
        moon = _insert_project(conn, "MOON", "Project Moon 月之子", "BP-Morgan")
        nova = _insert_project(conn, "NOVA", "Project Nova 新星", "BP-Nova")
        sh1 = _insert_project(conn, "SH1", "指间山海", "BP-Shanhai-A")
        sh2 = _insert_project(conn, "SH2", "山海经", "BP-Shanhai-B")
        hidden = _insert_project(conn, "SH9", "山海秘境", "BP-Hidden")
        user = create_user(
            conn, email="id-search@example.com", display_name="ID", role=ROLE_LEGAL
        )
        grantor = create_user(
            conn, email="id-grantor@example.com", display_name="G", role=ROLE_LEGAL
        )
        for project_id in (moon, nova, sh1, sh2):  # hidden deliberately NOT granted
            conn.execute(
                "insert into project_access (user_id, project_id, granted_by_user_id) "
                "values (?, ?, ?)",
                (user["id"], project_id, grantor["id"]),
            )
            _grant_field(conn, user["id"], project_id, "project", "legal_bp")
        conn.commit()
        return AccessContext.from_user(user)
    finally:
        conn.close()


def _identity_plan(token: str) -> QueryPlan:
    return QueryPlan(
        domain="project",
        operation="search",
        filters=[QueryFilter(field="identity", operator="contains", value=token)],
        return_fields=["legal_bp"],
        limit=20,
    )


def test_identity_exact_code_hit_on_sqlite_path(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    context = _identity_seed(database_path)
    conn = db.connect(database_path)
    try:
        result = search_projects(conn, _identity_plan("MOON"), access_context=context)
    finally:
        conn.close()
    assert "identity_disambiguation" not in result
    assert result["projects"] == [
        {"project_code": "MOON", "name": "Project Moon 月之子", "legal_bp": "BP-Morgan"}
    ]


def test_identity_case_insensitive_code_hit_on_sqlite_path(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    context = _identity_seed(database_path)
    conn = db.connect(database_path)
    try:
        result = search_projects(conn, _identity_plan("nova"), access_context=context)
    finally:
        conn.close()
    assert result["projects"] == [
        {"project_code": "NOVA", "name": "Project Nova 新星", "legal_bp": "BP-Nova"}
    ]


def test_identity_unique_name_fragment_on_sqlite_path(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    context = _identity_seed(database_path)
    conn = db.connect(database_path)
    try:
        result = search_projects(conn, _identity_plan("月之子"), access_context=context)
    finally:
        conn.close()
    assert "identity_disambiguation" not in result
    assert result["projects"] == [
        {"project_code": "MOON", "name": "Project Moon 月之子", "legal_bp": "BP-Morgan"}
    ]


def test_identity_ambiguous_token_lists_candidates_on_sqlite_path(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    context = _identity_seed(database_path)
    conn = db.connect(database_path)
    try:
        result = search_projects(conn, _identity_plan("山海"), access_context=context)
    finally:
        conn.close()
    # The hidden "山海秘境" (SH9) is out of scope and must not appear.
    assert result["identity_disambiguation"] == {"token": "山海", "candidate_count": 2}
    assert sorted(row["project_code"] for row in result["projects"]) == ["SH1", "SH2"]
    for row in result["projects"]:
        assert {"project_code", "name", "legal_bp"} <= set(row)
