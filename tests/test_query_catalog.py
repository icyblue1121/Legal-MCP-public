from __future__ import annotations

from pathlib import Path

from legal_mcp import db
from legal_mcp.query_catalog import build_query_catalog, catalog_context_for_prompt
from legal_mcp.query_plan import QueryFilter, QueryPlan


def test_query_catalog_reads_registered_fields_from_sqlite_schema(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        catalog = build_query_catalog(conn)
    finally:
        conn.close()

    assert "project" in catalog.domains
    assert "license" in catalog.domains
    assert "rights_holder" in catalog.domains["license"].fields
    assert catalog.domains["license"].field_aliases["商标权利人"] == "rights_holder"
    assert catalog.domains["license"].relationship_filter_fields == {"project_code", "name"}


def test_catalog_context_for_prompt_contains_schema_not_tools(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        context = catalog_context_for_prompt(build_query_catalog(conn))
    finally:
        conn.close()

    assert "license" in context
    assert "rights_holder" in context
    assert "get_project_fields" not in context
    assert "database handle" not in context


def test_query_catalog_validates_child_project_identity_filter(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        catalog = build_query_catalog(conn)
    finally:
        conn.close()

    plan = QueryPlan(
        domain="license",
        operation="search",
        filters=[
            QueryFilter(field="project_code", operator="eq", value="Acme"),
            QueryFilter(field="license_type", operator="eq", value="trademark_right"),
        ],
        return_fields=["license_type", "rights_holder"],
        limit=20,
    )

    assert catalog.validate_plan(plan).ok


def test_query_catalog_accepts_virtual_identity_filter(tmp_path: Path) -> None:
    # v0.4.8: 'identity' is a legal virtual filter field on a domain that declares
    # identity fields, even though it is not a real column.
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        catalog = build_query_catalog(conn)
    finally:
        conn.close()

    plan = QueryPlan(
        domain="project",
        operation="search",
        filters=[QueryFilter(field="identity", operator="contains", value="MOON")],
        return_fields=["legal_bp"],
        limit=20,
    )
    assert catalog.validate_plan(plan).ok
    # ...but rejected on cross_domain, which declares no identity fields.
    cross = QueryPlan(
        domain="cross_domain",
        operation="search",
        filters=[QueryFilter(field="identity", operator="contains", value="MOON")],
        return_fields=["project_code"],
        limit=20,
    )
    assert catalog.validate_plan(cross).error_code == "unknown_filter_field"


def test_catalog_prompt_advertises_identity_usage(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        context = catalog_context_for_prompt(build_query_catalog(conn))
    finally:
        conn.close()
    assert "identity_usage" in context
    assert "virtual_filter_fields" in context


def test_query_catalog_registers_cross_domain_and_drops_risk(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        catalog = build_query_catalog(conn)
    finally:
        conn.close()

    assert "cross_domain" in catalog.domains
    assert "risk" not in catalog.domains
    cross = QueryPlan(
        domain="cross_domain",
        operation="search",
        filters=[QueryFilter(field="q", operator="contains", value="张三")],
        return_fields=["project_code", "name"],
        limit=20,
    )
    assert catalog.validate_plan(cross).ok


def test_catalog_prompt_exposes_operations_operators_and_filter_shape(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        context = catalog_context_for_prompt(build_query_catalog(conn))
    finally:
        conn.close()

    assert "supported_operations" in context
    assert "supported_operators" in context
    assert "filter_shape" in context
    assert "cross_domain" in context


def test_query_catalog_rejects_unregistered_domain(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        catalog = build_query_catalog(conn)
    finally:
        conn.close()

    plan = QueryPlan(
        domain="sqlite_master",
        operation="search",
        filters=[],
        return_fields=["sql"],
        limit=20,
    )

    result = catalog.validate_plan(plan)
    assert not result.ok
    assert result.error_code == "unsupported_domain"
