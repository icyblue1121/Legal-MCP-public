from __future__ import annotations

import pytest

from legal_mcp.agent_fast_path import is_access_question, plan_fast_path


def test_fast_path_routes_access_question() -> None:
    decision = plan_fast_path("我能访问哪些项目？")

    assert decision is not None
    assert decision.intent == "access"
    assert decision.plan is None


def test_fast_path_recognizes_permission_scope_phrasing() -> None:
    # The dogfood phrasing "我有什么权限" must still bypass the model (v0.4.6 §A).
    assert is_access_question("我有什么权限")
    decision = plan_fast_path("我有什么权限")
    assert decision is not None
    assert decision.intent == "access"


def test_fast_path_plans_managed_company_seal_status_question() -> None:
    decision = plan_fast_path("我管理的公司印章状态")

    assert decision is not None
    assert decision.intent == "search"
    assert decision.plan is not None
    assert decision.plan.domain == "seal"
    assert decision.plan.return_fields == [
        "company",
        "seal_type",
        "status",
        "storage_location",
        "borrower",
        "borrowed_at",
        "borrow_reason",
        "expected_return_at",
        "actual_return_at",
    ]


@pytest.mark.parametrize(
    "question",
    [
        "请查询项目代码 VT-0001 的法务 BP 是谁",
        "ARBITRARY-42 的官网是什么？",
        "Acme 的商标在哪家公司？",
        "张三是哪些项目的法务BP？",
    ],
)
def test_fast_path_no_longer_plans_project_or_license_fields(question: str) -> None:
    # v0.4.6 §A: the global business fast path is gone. Project/license field
    # questions — including verbose ones the old `的`-splitter mis-parsed into
    # `name = "请查询项目代码 VT-0001"` — now defer to the catalog-bound planner.
    assert plan_fast_path(question) is None
