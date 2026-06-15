from __future__ import annotations

import pytest

from legal_mcp.planner import plan_query


@pytest.mark.parametrize(
    "question",
    [
        "我能访问哪些项目？",
        "用户查询自己能访问哪些项目",
        "我可以看到哪些项目和字段信息？",
        "当前用户有哪些项目权限？",
        "查询用户权限",
        "查看我的用户权限",
        "我有哪些权限？",
        "我的权限是什么？",
    ],
)
def test_planner_maps_access_scope_questions_to_describe_my_access(
    question: str,
) -> None:
    plan = plan_query(question)

    assert plan.tool_name == "describe_my_access"
    assert plan.arguments == {}


@pytest.mark.parametrize(
    ("question", "field"),
    [
        ("代号 T 的项目代号是什么？", "project_code"),
        ("代号 T 的项目名称是什么？", "name"),
        ("代号 T 的上线状态是什么？", "stage"),
        ("代号 T 的法务BP是谁？", "legal_bp"),
        ("代号 T 的所属部门是什么？", "department"),
        ("代号 T 的发行团队是什么？", "release_team"),
        ("代号 T 的联系人是谁？", "contact_person"),
        ("代号 T 的官网是什么？", "website"),
        ("代号 T 的备注是什么？", "notes"),
    ],
)
def test_planner_maps_each_project_field_question_to_single_project_field(
    question: str,
    field: str,
) -> None:
    plan = plan_query(question)

    assert plan.tool_name == "get_project_fields"
    assert plan.arguments["project_id_or_name"] == "T"
    assert plan.arguments["fields"] == [field]


def test_planner_maps_website_question_to_project_fields() -> None:
    plan = plan_query("ACME 的官网是什么？")

    assert plan.tool_name == "get_project_fields"
    assert plan.arguments["project_id_or_name"] == "ACME"
    assert plan.arguments["fields"] == ["website"]


def test_planner_keeps_full_non_code_project_name_for_website_questions() -> None:
    plan = plan_query("示例项目 的官网是什么？")

    assert plan.tool_name == "get_project_fields"
    assert plan.arguments["project_id_or_name"] == "示例项目"
    assert plan.arguments["fields"] == ["website"]


def test_planner_keeps_full_spaced_project_name_for_website_questions() -> None:
    plan = plan_query("Project One 的官网是什么？")

    assert plan.tool_name == "get_project_fields"
    assert plan.arguments["project_id_or_name"] == "Project One"
    assert plan.arguments["fields"] == ["website"]


def test_planner_keeps_full_project_alias_for_website_questions() -> None:
    plan = plan_query("ACME项目部 的官网是什么？")

    assert plan.tool_name == "get_project_fields"
    assert plan.arguments["project_id_or_name"] == "ACME项目部"
    assert plan.arguments["fields"] == ["website"]


def test_planner_maps_project_operator_question_to_license_fields() -> None:
    plan = plan_query("代号 T 的运营方是谁")

    assert plan.tool_name == "list_project_licenses"
    assert plan.arguments["project_id_or_name"] == "T"
    assert plan.arguments["fields"] == ["actual_operator", "operating_entity"]


def test_planner_maps_contract_amount_question_to_contract_fields() -> None:
    plan = plan_query("合同 SHYBYBZ2025000082 的总金额是多少？")

    assert plan.tool_name == "get_contract_fields"
    assert plan.arguments["contract_number"] == "SHYBYBZ2025000082"
    assert plan.arguments["fields"] == ["currency", "total_amount"]
