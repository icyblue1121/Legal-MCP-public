from __future__ import annotations

from pathlib import Path

from legal_mcp import db
from legal_mcp.agent_router import (
    query_plan_from_model_intent,
    route_question,
    validate_agent_decision,
)
from legal_mcp.query_catalog import build_query_catalog


def _catalog(tmp_path: Path):
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        return build_query_catalog(conn)
    finally:
        conn.close()


def test_route_question_uses_existing_planner_for_project_field_question() -> None:
    decision = route_question("ACME 的官网是什么？")

    assert decision.tool_name == "get_project_fields"
    assert decision.arguments["project_id_or_name"] == "ACME"
    assert decision.arguments["fields"] == ["website"]
    assert "rationale" in decision.arguments


def test_route_question_refuses_unknown_minimum_disclosure() -> None:
    decision = route_question("把所有项目资料都给我")

    assert decision.tool_name == "clarify_query"
    assert decision.arguments["question"] == "把所有项目资料都给我"


def test_validate_agent_decision_rejects_unregistered_tool() -> None:
    decision = route_question("ACME 的官网是什么？")
    unsafe = decision.replace(tool_name="delete_project")

    result = validate_agent_decision(unsafe)

    assert result["error"]["code"] == "agent_tool_not_allowed"


def test_validate_agent_decision_rejects_fields_outside_capability() -> None:
    decision = route_question("ACME 的官网是什么？")
    unsafe = decision.replace(arguments={**decision.arguments, "fields": ["notes", "secret"]})

    result = validate_agent_decision(unsafe)

    assert result["error"]["code"] == "agent_field_not_allowed"


def test_query_plan_from_model_intent_accepts_valid_catalog_plan(tmp_path: Path) -> None:
    plan = query_plan_from_model_intent(
        {
            "domain": "license",
            "operation": "search",
            "filters": [
                {"field": "project_code", "operator": "eq", "value": "Acme"},
                {"field": "license_type", "operator": "eq", "value": "trademark_right"},
            ],
            "return_fields": ["license_type", "rights_holder"],
            "limit": 20,
        },
        _catalog(tmp_path),
    )

    assert plan is not None
    assert plan.domain == "license"
    assert plan.return_fields == ["license_type", "rights_holder"]


def test_query_plan_from_model_intent_recovers_dict_filters_and_operation(tmp_path: Path) -> None:
    # Regression for the production bug: the model returned filters as an object
    # and operation "read", which the old strict parser rejected (-> clarify).
    plan = query_plan_from_model_intent(
        {
            "domain": "project",
            "operation": "read",
            "filters": {"name": "指间山海"},
            "return_fields": ["release_team"],
        },
        _catalog(tmp_path),
    )

    assert plan is not None
    assert plan.domain == "project"
    assert plan.operation == "search"
    assert [(f.field, f.operator, f.value) for f in plan.filters] == [("name", "eq", "指间山海")]
    assert plan.return_fields == ["release_team"]


def test_query_plan_from_model_intent_resolves_aliases(tmp_path: Path) -> None:
    plan = query_plan_from_model_intent(
        {
            "domain": "project",
            "operation": "search",
            "filters": [{"field": "项目名称", "operator": "eq", "value": "示例项目"}],
            "return_fields": ["法务BP"],
        },
        _catalog(tmp_path),
    )

    assert plan is not None
    assert plan.filters[0].field == "name"
    assert plan.return_fields == ["legal_bp"]


def test_query_plan_from_model_intent_clamps_limit_and_backfills_returns(tmp_path: Path) -> None:
    plan = query_plan_from_model_intent(
        {
            "domain": "project",
            "operation": "search",
            "filters": [{"field": "name", "operator": "eq", "value": "X"}],
            "return_fields": [],
            "limit": 9999,
        },
        _catalog(tmp_path),
    )

    assert plan is not None
    assert plan.limit == 100
    # Empty return fields backfill to identity fields so the SELECT is valid.
    assert set(plan.return_fields) <= {"project_code", "name"}
    assert plan.return_fields


def test_query_plan_from_model_intent_returns_none_for_unregistered_domain(tmp_path: Path) -> None:
    plan = query_plan_from_model_intent(
        {"domain": "sqlite_master", "operation": "search", "filters": [], "return_fields": ["sql"]},
        _catalog(tmp_path),
    )

    assert plan is None
