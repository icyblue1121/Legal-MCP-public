"""v0.5.0 regression: the planner's lone identity-field ``eq`` false-empty rescue.

The planner is told to emit ``identity`` + ``contains`` for a bare project token,
but it still sometimes guesses a single identity column with ``eq`` — ``name eq
"MOON"`` when MOON is a *code*, or a case-mismatched name. That authorized query
returns zero rows although the project exists. ``execute_plan`` now rescues this:
on an empty result it rewrites a lone identity-field ``eq`` to ``identity`` +
``contains`` and retries once, recording the rescue.

These pin: (1) the rescue answers the dogfood ``MOON的法务BP是谁`` shape;
(2) a non-identity ``eq`` is never broadened; (3) a genuinely absent token stays a
clean ``no_rows`` (the rewrite that does not help is not marked).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from legal_mcp import db
from legal_mcp.agent_graph import run_agent_query
from legal_mcp.ai_provider import AIMessage
from legal_mcp.policy import AccessContext

_VT0001_LEGAL_BP = "甲BP"


def _seed(path: Path) -> None:
    db.initialize_database(path)
    conn = db.connect(path)
    try:
        conn.execute(
            "insert into projects (project_code, name, stage, legal_bp, website) "
            "values (?, ?, ?, ?, ?)",
            ("VT-0001", "虚拟测试项目001", "live", _VT0001_LEGAL_BP, "https://vt0001.example"),
        )
        conn.commit()
    finally:
        conn.close()


class StubAIProvider:
    def __init__(self, content: str) -> None:
        self.content = content

    def complete(self, messages: list[AIMessage]) -> AIMessage:
        return AIMessage(role="assistant", content=self.content)


def _eq_plan(field: str, value: str, return_field: str) -> str:
    return (
        '{"domain":"project","operation":"search",'
        f'"filters":[{{"field":"{field}","operator":"eq","value":"{value}"}}],'
        f'"return_fields":["{return_field}"],"limit":5}}'
    )


def _run(tmp_path: Path, database_path: Path, question: str, plan: str) -> dict[str, Any]:
    return run_agent_query(
        question=question,
        database_path=database_path,
        checkpoint_path=tmp_path / "agent-checkpoints.sqlite",
        audit_path=tmp_path / "audit.jsonl",
        thread_id="conv-rewrite",
        ai_provider=StubAIProvider(plan),
        access_context=AccessContext.local_operator(),
        connector_setup=None,
    )


def _run_error_codes(database_path: Path) -> list[str | None]:
    conn = db.connect(database_path)
    try:
        return [
            row["error_code"]
            for row in conn.execute("select error_code from agent_runs order by id")
        ]
    finally:
        conn.close()


def test_lone_identity_eq_falseempty_rescued_by_rewrite(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    _seed(database_path)

    # The planner guessed the wrong identity field: "VT-0001" is a *code*, but it
    # emitted ``name eq "VT-0001"`` — no project is *named* VT-0001, so the raw plan
    # is a false-empty. The rewrite to ``identity contains`` finds it by code.
    result = _run(
        tmp_path,
        database_path,
        "VT-0001 的法务BP是谁",
        _eq_plan("name", "VT-0001", "legal_bp"),
    )

    assert result["status"] == "success"
    assert _VT0001_LEGAL_BP in result["answer"]
    call = result["tool_calls"][0]
    assert call["rewrite"] == {"reason": "eq_to_identity", "from_field": "name"}
    # The planner's original plan is preserved for observability, not overwritten.
    assert call["plan"]["filters"] == [
        {"field": "name", "operator": "eq", "value": "VT-0001"}
    ]
    # The rescue is auditable via the reused error_code column.
    assert _run_error_codes(database_path) == ["rewrite:eq_to_identity"]


def test_non_identity_eq_empty_is_not_rewritten(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    _seed(database_path)

    # ``legal_bp`` is a real, non-identity field. A deliberate equality that finds
    # nothing must stay a clean no_rows — never broadened to an identity search.
    result = _run(
        tmp_path,
        database_path,
        "法务BP是丙BP的项目",
        _eq_plan("legal_bp", "丙BP", "name"),
    )

    assert result["status"] == "success"
    call = result["tool_calls"][0]
    assert "rewrite" not in call
    assert call["diagnostic"]["reason"] == "no_rows"
    assert _run_error_codes(database_path) == ["diagnostic:no_rows"]


def test_absent_token_stays_no_rows_when_rewrite_does_not_help(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    _seed(database_path)

    # A lone identity ``eq`` triggers the rewrite, but the token exists under no code
    # or name, so the retry is also empty: the result stays a clean no_rows and the
    # rewrite is NOT marked (a rescue that did not rescue is invisible).
    result = _run(
        tmp_path,
        database_path,
        "ZZZ-9999 的法务BP是谁",
        _eq_plan("name", "ZZZ-9999", "legal_bp"),
    )

    assert result["status"] == "success"
    call = result["tool_calls"][0]
    assert "rewrite" not in call
    assert call["diagnostic"]["reason"] == "no_rows"
