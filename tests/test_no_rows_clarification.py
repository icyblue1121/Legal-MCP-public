"""Explainable no_rows + clarification (v0.5.4).

An authorized-but-empty result must not be a dead "找不到": it returns leak-free
guidance built from catalog metadata + the user's own filters. An ambiguous
identity match renders a "did you mean" candidate list. Neither path discloses an
out-of-scope value — candidates are already record-scoped and field-gated, and the
no_rows hint never touches fetched rows.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from legal_mcp import db
from legal_mcp.agent_graph import format_answer, run_agent_query
from legal_mcp.ai_provider import AIMessage
from legal_mcp.policy import AccessContext


def _seed(path: Path) -> None:
    db.initialize_database(path)
    conn = db.connect(path)
    try:
        conn.execute(
            "insert into projects (project_code, name, stage, legal_bp) values (?, ?, ?, ?)",
            ("VT-0001", "虚拟测试项目001", "live", "甲BP"),
        )
        conn.commit()
    finally:
        conn.close()


class StubAIProvider:
    def __init__(self, content: str) -> None:
        self.content = content

    def complete(self, messages: list[AIMessage]) -> AIMessage:
        return AIMessage(role="assistant", content=self.content)


def _run(tmp_path: Path, database_path: Path, question: str, plan: str) -> dict[str, Any]:
    return run_agent_query(
        question=question,
        database_path=database_path,
        checkpoint_path=tmp_path / "agent-checkpoints.sqlite",
        audit_path=tmp_path / "audit.jsonl",
        thread_id="conv-norows",
        ai_provider=StubAIProvider(plan),
        access_context=AccessContext.local_operator(),
        connector_setup=None,
    )


def _eq_plan(field: str, value: str, return_field: str) -> str:
    return (
        '{"domain":"project","operation":"search",'
        f'"filters":[{{"field":"{field}","operator":"eq","value":"{value}"}}],'
        f'"return_fields":["{return_field}"],"limit":5}}'
    )


def test_no_rows_returns_structured_clarification(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    _seed(database_path)
    # A token that exists under no code or name -> rewrite cannot help -> no_rows.
    result = _run(
        tmp_path, database_path, "ZZZ-9999 的法务BP是谁", _eq_plan("name", "ZZZ-9999", "legal_bp")
    )
    assert result["status"] == "success"
    clarification = result["clarification"]
    assert clarification["reason"] == "no_rows"
    assert clarification["domain"] == "project"
    # The user's own search term is echoed (their input, safe).
    assert clarification["searched"] == [
        {"field": "name", "operator": "eq", "value": "ZZZ-9999"}
    ]
    # Metadata only: filterable + identity fields.
    assert "legal_bp" in clarification["available_filters"]
    assert set(clarification["identity_fields"]) == {"project_code", "name"}
    # Identity-eq triggers the code-vs-name nudge.
    assert any("代号" in s for s in clarification["suggestions"])


def test_no_rows_answer_is_guidance_not_bare_json(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    _seed(database_path)
    result = _run(
        tmp_path, database_path, "ZZZ-9999 的法务BP是谁", _eq_plan("name", "ZZZ-9999", "legal_bp")
    )
    answer = result["answer"]
    assert "没有找到匹配的记录" in answer
    assert answer != "{}"
    # Leak check: the seeded (non-matching) project's value never appears.
    assert "甲BP" not in answer
    assert "虚拟测试项目001" not in answer


def test_non_empty_result_has_no_clarification(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    _seed(database_path)
    # identity contains hits VT-0001 by code -> real rows, no clarification.
    plan = (
        '{"domain":"project","operation":"search",'
        '"filters":[{"field":"identity","operator":"contains","value":"VT-0001"}],'
        '"return_fields":["legal_bp"],"limit":5}'
    )
    result = _run(tmp_path, database_path, "VT-0001 的法务BP是谁", plan)
    assert result["status"] == "success"
    assert "clarification" not in result
    assert "甲BP" in result["answer"]


def test_format_answer_renders_identity_candidates() -> None:
    # Unit test of the "did you mean" rendering: candidates are already scoped rows.
    state = {
        "tool_result": {
            "projects": [
                {"project_code": "SH-01", "name": "指间山海"},
                {"project_code": "SH-02", "name": "山海经"},
            ],
            "identity_disambiguation": {"token": "山海", "candidate_count": 2},
        }
    }
    answer = format_answer(state)["answer"]
    assert "山海" in answer
    assert "匹配到多个" in answer
    assert "SH-01" in answer and "SH-02" in answer
