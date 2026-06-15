"""Unit tests for safe conversation memory and the turn-local reset (v0.4.6 §C/§D)."""

from __future__ import annotations

from pathlib import Path

from legal_mcp import db
from legal_mcp.agent_graph import start_turn
from legal_mcp.conversation_context import load_conversation_context, record_turn_context
from legal_mcp.query_catalog import build_query_catalog
from legal_mcp.query_plan import QueryFilter, QueryPlan


def _catalog(database_path: Path):
    conn = db.connect(database_path)
    try:
        return build_query_catalog(conn)
    finally:
        conn.close()


def _plan(code: str, field: str) -> QueryPlan:
    return QueryPlan(
        domain="project",
        operation="search",
        filters=[QueryFilter(field="project_code", operator="eq", value=code)],
        return_fields=[field],
        limit=1,
    )


def test_record_and_load_round_trip_carries_identity(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    catalog = _catalog(database_path)
    conn = db.connect(database_path)
    try:
        record_turn_context(
            conn,
            conversation_id="conv-1",
            turn_id="turn-1",
            plan=_plan("VT-0010", "name"),
            result={"projects": [{"name": "山海计划", "project_code": "VT-0010"}]},
            catalog=catalog,
        )
        conn.commit()
        context = load_conversation_context(conn, "conv-1")
    finally:
        conn.close()

    assert context["recent_fields"] == ["name"]
    assert context["last_successful_tool"] == "project/search"
    entity = context["recent_entities"][0]
    assert entity["domain"] == "project"
    assert entity["identity"]["project_code"] == "VT-0010"
    assert entity["identity"]["name"] == "山海计划"


def test_empty_result_is_not_remembered(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    catalog = _catalog(database_path)
    conn = db.connect(database_path)
    try:
        record_turn_context(
            conn,
            conversation_id="conv-2",
            turn_id="turn-1",
            plan=_plan("VT-0010", "name"),
            result={"projects": []},
            catalog=catalog,
        )
        conn.commit()
        assert load_conversation_context(conn, "conv-2") == {}
    finally:
        conn.close()


def test_errored_result_is_not_remembered(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    catalog = _catalog(database_path)
    conn = db.connect(database_path)
    try:
        record_turn_context(
            conn,
            conversation_id="conv-3",
            turn_id="turn-1",
            plan=_plan("VT-0010", "name"),
            result={"error": {"code": "return_field_access_denied", "message": "no"}},
            catalog=catalog,
        )
        conn.commit()
        assert load_conversation_context(conn, "conv-3") == {}
    finally:
        conn.close()


def test_latest_turn_wins(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    catalog = _catalog(database_path)
    conn = db.connect(database_path)
    try:
        for turn_id, code in (("turn-1", "VT-0001"), ("turn-2", "VT-0010")):
            record_turn_context(
                conn,
                conversation_id="conv-4",
                turn_id=turn_id,
                plan=_plan(code, "name"),
                result={"projects": [{"project_code": code}]},
                catalog=catalog,
            )
        conn.commit()
        context = load_conversation_context(conn, "conv-4")
    finally:
        conn.close()

    assert context["recent_entities"][0]["identity"]["project_code"] == "VT-0010"


def test_start_turn_clears_stale_turn_local_state_for_natural_language() -> None:
    # Defense in depth (§C): if an existing state object is handed in, a stale
    # plan/result/error/answer is dropped so the turn replans from the question.
    dirty = {
        "input_mode": "natural_language",
        "question": "  VT-0010 的官网是什么  ",
        "query_type": "access",
        "query_plan": _plan("VT-0001", "legal_bp"),
        "tool_result": {"projects": [{"legal_bp": "stale"}]},
        "answer": "stale answer",
        "error": {"code": "stale"},
    }

    update = start_turn(dirty)

    assert update["normalized_question"] == "VT-0010 的官网是什么"
    for key in ("query_type", "query_plan", "tool_result", "answer", "error"):
        assert update[key] is None


def test_start_turn_preserves_structured_plan() -> None:
    plan = _plan("VT-0010", "website")
    update = start_turn(
        {"input_mode": "structured", "question": "structured_query", "query_plan": plan}
    )
    # A structured turn keeps its caller-supplied plan; only the question is touched.
    assert "query_plan" not in update
    assert update["normalized_question"] == "structured_query"
