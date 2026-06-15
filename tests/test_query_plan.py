from __future__ import annotations

from legal_mcp.query_plan import QueryFilter, QueryPlan, validate_query_plan


def test_project_search_plan_accepts_allowed_shape() -> None:
    plan = QueryPlan(
        domain="project",
        operation="search",
        filters=[QueryFilter(field="legal_bp", operator="eq", value="张三")],
        return_fields=["project_code", "name"],
        limit=50,
    )

    assert validate_query_plan(plan).ok is True


def test_query_plan_rejects_unknown_domain() -> None:
    plan = QueryPlan(
        domain="sql",
        operation="search",
        filters=[],
        return_fields=["*"],
        limit=50,
    )

    result = validate_query_plan(plan)

    assert result.ok is False
    assert result.error_code == "unsupported_domain"


def test_query_plan_rejects_unknown_operator() -> None:
    plan = QueryPlan(
        domain="project",
        operation="search",
        filters=[QueryFilter(field="legal_bp", operator="regex", value="张三")],
        return_fields=["project_code"],
        limit=50,
    )

    result = validate_query_plan(plan)

    assert result.ok is False
    assert result.error_code == "unsupported_operator"


def test_query_plan_rejects_wildcard_return_fields() -> None:
    plan = QueryPlan(
        domain="project",
        operation="search",
        filters=[],
        return_fields=["*"],
        limit=50,
    )

    result = validate_query_plan(plan)

    assert result.ok is False
    assert result.error_code == "wildcard_fields_not_allowed"


def test_query_plan_rejects_limit_above_maximum() -> None:
    plan = QueryPlan(
        domain="project",
        operation="search",
        filters=[],
        return_fields=["project_code"],
        limit=101,
    )

    result = validate_query_plan(plan)

    assert result.ok is False
    assert result.error_code == "invalid_limit"
