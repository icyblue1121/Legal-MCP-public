from __future__ import annotations

from pathlib import Path

from legal_mcp import db
from legal_mcp.agent_steps import list_agent_steps, record_agent_step
from legal_mcp.query_plan import QueryFilter, QueryPlan


def test_record_and_list_agent_steps(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        plan = QueryPlan(
            domain="project",
            operation="search",
            filters=[QueryFilter(field="project_code", operator="eq", value="ACME")],
            return_fields=["website"],
            limit=1,
        )
        record_agent_step(
            conn,
            thread_id="thread-1",
            turn_id="turn-1",
            step_index=1,
            planner_source="ai",
            status="selected",
            reason="catalog-bound project field plan",
            plan=plan,
        )
        conn.commit()
        rows = list_agent_steps(conn, "thread-1")
    finally:
        conn.close()

    assert rows[0]["planner_source"] == "ai"
    assert rows[0]["turn_id"] == "turn-1"
    assert rows[0]["status"] == "selected"
    assert '"website"' in rows[0]["plan_json"]


def test_list_agent_steps_filters_by_turn(tmp_path: Path) -> None:
    # v0.4.6 §F: two turns of one conversation each persist a step_index = 1 plan;
    # list_agent_steps narrows to one turn when given turn_id, else lists both.
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        for turn_id in ("turn-a", "turn-b"):
            record_agent_step(
                conn,
                thread_id="conv-1",
                turn_id=turn_id,
                step_index=1,
                planner_source="ai",
                status="selected",
            )
        conn.commit()
        all_steps = list_agent_steps(conn, "conv-1")
        one_turn = list_agent_steps(conn, "conv-1", turn_id="turn-b")
    finally:
        conn.close()

    assert {step["turn_id"] for step in all_steps} == {"turn-a", "turn-b"}
    assert [step["turn_id"] for step in one_turn] == ["turn-b"]
