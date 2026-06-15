"""Deterministic query planner for minimum-disclosure tools."""

from __future__ import annotations

import re
from dataclasses import dataclass

PROJECT_FIELD_KEYWORDS = (
    ("project_code", ("项目代号", "project code")),
    ("name", ("项目名称", "游戏名称", "名称", "name")),
    ("stage", ("上线状态", "阶段", "stage")),
    ("legal_bp", ("法务bp", "法务", "legal bp")),
    ("department", ("所属部门", "项目部", "部门", "department")),
    ("release_team", ("发行团队", "发行组", "release team")),
    ("contact_person", ("联系人", "对接人", "contact person")),
    ("website", ("官网", "网址", "website")),
    ("notes", ("备注", "notes")),
)


@dataclass(frozen=True)
class QueryPlan:
    tool_name: str
    arguments: dict[str, object]
    reason: str


def plan_query(question: str) -> QueryPlan:
    normalized = question.strip()
    if _asks_for_access_scope(normalized):
        return QueryPlan(
            tool_name="describe_my_access",
            arguments={},
            reason="question asks which projects and fields the current user can access",
        )
    project_field = _project_field_from_question(normalized)
    if project_field is not None:
        field, keywords = project_field
        project = _project_from_field_question(normalized, keywords)
        return QueryPlan(
            tool_name="get_project_fields",
            arguments={"project_id_or_name": project, "fields": [field]},
            reason=f"question asks for project {field}",
        )
    if "运营方" in normalized or "运营主体" in normalized:
        project = _project_from_field_question(normalized, ("运营方", "运营主体"))
        return QueryPlan(
            tool_name="list_project_licenses",
            arguments={
                "project_id_or_name": project,
                "fields": ["actual_operator", "operating_entity"],
            },
            reason="question asks for license operator",
        )
    if "总金额" in normalized or "金额" in normalized:
        contract_number = _first_contract_number(normalized)
        return QueryPlan(
            tool_name="get_contract_fields",
            arguments={
                "contract_number": contract_number,
                "fields": ["currency", "total_amount"],
            },
            reason="question asks for contract amount",
        )
    return QueryPlan(
        tool_name="clarify_query",
        arguments={"question": question},
        reason="minimum necessary fields could not be determined",
    )


def _first_project_like_token(question: str) -> str:
    match = re.search(r"[A-Za-z][A-Za-z0-9_-]+", question)
    return match.group(0) if match else question


def _project_from_field_question(question: str, keywords: tuple[str, ...]) -> str:
    lower_question = question.lower()
    keyword_indexes = [
        lower_question.find(keyword.lower())
        for keyword in keywords
        if lower_question.find(keyword.lower()) >= 0
    ]
    keyword_index = min(keyword_indexes) if keyword_indexes else -1
    prefix = question[:keyword_index].strip() if keyword_index >= 0 else ""
    if prefix.endswith("的"):
        prefix = prefix[:-1].strip()
    if prefix.startswith("代号"):
        prefix = prefix.removeprefix("代号").strip()
    return prefix or _first_project_like_token(question)


def _project_field_from_question(question: str) -> tuple[str, tuple[str, ...]] | None:
    normalized = question.lower()
    matches: list[tuple[int, str, tuple[str, ...]]] = []
    for field, keywords in PROJECT_FIELD_KEYWORDS:
        indexes = [
            normalized.find(keyword.lower())
            for keyword in keywords
            if normalized.find(keyword.lower()) >= 0
        ]
        if indexes:
            matches.append((max(indexes), field, keywords))
    if not matches:
        return None
    _, field, keywords = max(matches, key=lambda match: match[0])
    return field, keywords


def _asks_for_access_scope(question: str) -> bool:
    return asks_for_access_scope(question)


def asks_for_access_scope(question: str) -> bool:
    normalized = question.lower()
    subject_terms = ("我", "自己", "当前用户", "用户")
    scope_terms = (
        "访问哪些项目",
        "看到哪些项目",
        "能访问",
        "能看到",
        "可见项目",
        "项目权限",
        "用户权限",
        "权限",
        "字段信息",
    )
    return any(term in normalized for term in subject_terms) and any(
        term in normalized for term in scope_terms
    )


def _first_contract_number(question: str) -> str:
    match = re.search(r"[A-Z]{2,}[A-Z0-9]*\d{6,}", question)
    return match.group(0) if match else question
