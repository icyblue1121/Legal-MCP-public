"""Unit tests for the shared identity ranking helper (v0.4.8)."""

from __future__ import annotations

from legal_mcp.identity_match import identity_filter, rank_identity_rows
from legal_mcp.query_plan import QueryFilter, QueryPlan

_ID = ("project_code", "name")


def _rows() -> list[dict[str, str]]:
    return [
        {"project_code": "MOON", "name": "Project Moon 月之子", "legal_bp": "BP-Morgan"},
        {"project_code": "SH1", "name": "指间山海", "legal_bp": "BP-A"},
        {"project_code": "SH2", "name": "山海经", "legal_bp": "BP-B"},
    ]


def test_identity_filter_extracts_operator_and_token() -> None:
    plan = QueryPlan(
        domain="project",
        operation="search",
        filters=[QueryFilter(field="identity", operator="contains", value="山海")],
        return_fields=["legal_bp"],
    )
    assert identity_filter(plan) == ("contains", "山海")


def test_identity_filter_absent_returns_none() -> None:
    plan = QueryPlan(
        domain="project",
        operation="search",
        filters=[QueryFilter(field="project_code", operator="eq", value="MOON")],
        return_fields=["legal_bp"],
    )
    assert identity_filter(plan) is None


def test_exact_code_hit_wins_over_substring_noise() -> None:
    rows = _rows() + [{"project_code": "X", "name": "moonlight", "legal_bp": "BP-X"}]
    matched, ambiguous = rank_identity_rows(
        rows, token="moon", identity_fields=_ID, return_fields=["legal_bp"]
    )
    assert ambiguous is False
    assert matched == [
        {"project_code": "MOON", "name": "Project Moon 月之子", "legal_bp": "BP-Morgan"}
    ]


def test_unique_substring_is_not_ambiguous() -> None:
    matched, ambiguous = rank_identity_rows(
        _rows(), token="月之子", identity_fields=_ID, return_fields=["legal_bp"]
    )
    assert ambiguous is False
    assert [row["project_code"] for row in matched] == ["MOON"]


def test_multiple_substrings_are_ambiguous_candidates() -> None:
    matched, ambiguous = rank_identity_rows(
        _rows(), token="山海", identity_fields=_ID, return_fields=["legal_bp"]
    )
    assert ambiguous is True
    assert sorted(row["project_code"] for row in matched) == ["SH1", "SH2"]
    # Each candidate carries identity fields first, then the requested return field.
    assert list(matched[0]) == ["project_code", "name", "legal_bp"]


def test_prefix_matches_rank_before_midstring() -> None:
    rows = [
        {"project_code": "A", "name": "x山海", "legal_bp": "BP-A"},  # mid-string
        {"project_code": "B", "name": "山海x", "legal_bp": "BP-B"},  # prefix
    ]
    matched, _ = rank_identity_rows(
        rows, token="山海", identity_fields=_ID, return_fields=["legal_bp"]
    )
    assert [row["project_code"] for row in matched] == ["B", "A"]


def test_candidate_limit_caps_the_list() -> None:
    rows = [
        {"project_code": f"P{i}", "name": f"shared-{i}", "legal_bp": "BP"}
        for i in range(15)
    ]
    matched, ambiguous = rank_identity_rows(
        rows, token="shared", identity_fields=_ID, return_fields=["legal_bp"],
        candidate_limit=10,
    )
    assert ambiguous is True
    assert len(matched) == 10
