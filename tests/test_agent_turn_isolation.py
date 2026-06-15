"""v0.4.6 regression suite: each agent_query turn is an isolated transaction.

These pin the two failures from the 2026-06-08 dogfood (see
Docs/strategy/2026-06-08-v0.4.6-langgraph-query-redesign.md):

1. the global business fast path over-parsed natural language into a wrong plan;
2. a stale LangGraph checkpoint let one turn's plan execute on the next turn.

The numbered tests map 1:1 to the plan's "Regression tests required before
tagging v0.4.6" section.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from legal_mcp import db
from legal_mcp.agent_graph import run_agent_query
from legal_mcp.ai_provider import AIMessage
from legal_mcp.connector_config import ConnectorSetup
from legal_mcp.connectors.base import ConnectorDomain, ConnectorField, ConnectorQuery, RecordScope
from legal_mcp.identity import ROLE_BUSINESS, create_user
from legal_mcp.policy import AccessContext

_VT0001_LEGAL_BP = "甲BP"
_VT0010_WEBSITE = "https://vt0010.example"


def _seed_two_projects(path: Path) -> None:
    db.initialize_database(path)
    conn = db.connect(path)
    try:
        conn.execute(
            "insert into projects (project_code, name, stage, legal_bp, website) "
            "values (?, ?, ?, ?, ?)",
            ("VT-0001", "虚拟测试项目001", "live", _VT0001_LEGAL_BP, "https://vt0001.example"),
        )
        conn.execute(
            "insert into projects (project_code, name, stage, legal_bp, website) "
            "values (?, ?, ?, ?, ?)",
            ("VT-0010", "虚拟测试项目010-山海计划", "live", "乙BP", _VT0010_WEBSITE),
        )
        conn.commit()
    finally:
        conn.close()


class StubAIProvider:
    """Returns a fixed planner reply and records the prompt it received."""

    def __init__(self, content: str) -> None:
        self.content = content
        self.messages: list[AIMessage] = []

    def complete(self, messages: list[AIMessage]) -> AIMessage:
        self.messages = messages
        return AIMessage(role="assistant", content=self.content)

    @property
    def prompt_text(self) -> str:
        return "\n".join(message.content for message in self.messages)


class FailingAIProvider:
    def complete(self, messages: list[AIMessage]) -> AIMessage:
        raise AssertionError("deterministic path must not call the model")


def _project_plan(code: str, field: str) -> str:
    return (
        '{"domain":"project","operation":"search",'
        f'"filters":[{{"field":"project_code","operator":"eq","value":"{code}"}}],'
        f'"return_fields":["{field}"],"limit":1}}'
    )


def _run(
    tmp_path: Path,
    database_path: Path,
    question: str,
    provider: Any,
    thread_id: str,
    *,
    access_context: AccessContext | None = None,
    connector_setup: ConnectorSetup | None = None,
) -> dict[str, Any]:
    return run_agent_query(
        question=question,
        database_path=database_path,
        checkpoint_path=tmp_path / "agent-checkpoints.sqlite",
        audit_path=tmp_path / "audit.jsonl",
        thread_id=thread_id,
        ai_provider=provider,
        access_context=access_context or AccessContext.local_operator(),
        connector_setup=connector_setup,
    )


def _turn_context_rows(database_path: Path, conversation_id: str) -> list[dict[str, Any]]:
    conn = db.connect(database_path)
    try:
        return [
            dict(row)
            for row in conn.execute(
                "select * from agent_turn_context where conversation_id = ?",
                (conversation_id,),
            )
        ]
    finally:
        conn.close()


# --- 1. No stale plan across same conversation id ----------------------------


def test_no_stale_plan_across_same_conversation_id(tmp_path: Path) -> None:
    # The stale-checkpoint bug only manifests on the LangGraph path; skip (do NOT
    # silently pass through the linear fallback) if LangGraph is absent.
    pytest.importorskip("langgraph.checkpoint.sqlite")
    database_path = tmp_path / "legal.db"
    _seed_two_projects(database_path)

    first = _run(
        tmp_path,
        database_path,
        "请查询项目代码 VT-0001 的法务 BP 是谁",
        StubAIProvider(_project_plan("VT-0001", "legal_bp")),
        "conv-stale",
    )
    assert _VT0001_LEGAL_BP in first["answer"]

    second = _run(
        tmp_path,
        database_path,
        "VT-0010 的官网是什么",
        StubAIProvider(_project_plan("VT-0010", "website")),
        "conv-stale",
    )

    # The second turn replans from its own question: website, filtering VT-0010 —
    # not the first turn's legal_bp plan resurrected from a shared checkpoint.
    assert _VT0010_WEBSITE in second["answer"]
    assert "legal_bp" not in second["answer"]
    assert second["tool_calls"][0]["plan"]["return_fields"] == ["website"]
    assert second["tool_calls"][0]["plan"]["filters"] == [
        {"field": "project_code", "operator": "eq", "value": "VT-0010"}
    ]


# --- 2. Access question does not poison the next turn -------------------------


def test_access_question_does_not_poison_next_turn(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    _seed_two_projects(database_path)

    # Access is deterministic and bypasses the model entirely.
    access = _run(tmp_path, database_path, "我有什么权限", FailingAIProvider(), "conv-access")
    assert access["tool_calls"][0]["tool_name"] == "describe_my_access"

    nxt = _run(
        tmp_path,
        database_path,
        "VT-0010 的官网是什么",
        StubAIProvider(_project_plan("VT-0010", "website")),
        "conv-access",
    )
    assert nxt["tool_calls"][0]["tool_name"] == "project/search"
    assert _VT0010_WEBSITE in nxt["answer"]


# --- 3. Failed turn does not become entity context ---------------------------


def test_failed_turn_does_not_become_entity_context(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    _seed_two_projects(database_path)

    empty = _run(
        tmp_path,
        database_path,
        "ZZZ-9999 的项目名称是什么",
        StubAIProvider(_project_plan("ZZZ-9999", "name")),
        "conv-empty",
    )
    # An authorized zero-row result, tagged as such — not a denial.
    assert empty["status"] == "success"
    assert empty["tool_calls"][0]["diagnostic"]["reason"] == "no_rows"
    # The empty turn was NOT promoted as conversation memory.
    assert _turn_context_rows(database_path, "conv-empty") == []

    follow_up = StubAIProvider('{"intent":"clarify"}')
    result = _run(tmp_path, database_path, "它的官网是什么", follow_up, "conv-empty")
    assert result["tool_calls"][0]["tool_name"] == "clarify_query"
    # No context was available to leak into the planner prompt.
    assert "Conversation context" not in follow_up.prompt_text


# --- 4. Follow-up uses context but replans -----------------------------------


def test_follow_up_uses_context_but_replans(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    _seed_two_projects(database_path)

    _run(
        tmp_path,
        database_path,
        "VT-0010 的项目名称是什么",
        StubAIProvider(_project_plan("VT-0010", "name")),
        "conv-followup",
    )
    rows = _turn_context_rows(database_path, "conv-followup")
    assert len(rows) == 1  # the successful, non-empty turn was remembered

    follow_up = StubAIProvider(_project_plan("VT-0010", "website"))
    result = _run(tmp_path, database_path, "它的官网是什么", follow_up, "conv-followup")

    # The plan is newly generated for THIS turn (website), and the prior identity
    # appears only as context fed to the planner — never as an inherited plan.
    assert result["tool_calls"][0]["plan"]["return_fields"] == ["website"]
    assert _VT0010_WEBSITE in result["answer"]
    assert "Conversation context" in follow_up.prompt_text
    assert "VT-0010" in follow_up.prompt_text


# --- 5. Global business fast path disabled -----------------------------------


def test_global_business_fast_path_disabled_but_access_survives(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    _seed_two_projects(database_path)

    # A project-field question with no model configured must clarify — proving no
    # fast path silently produced a (wrong) plan for it.
    field_q = _run(
        tmp_path, database_path, "请查询项目代码 VT-0001 的法务 BP 是谁", None, "conv-nofast"
    )
    assert field_q["tool_calls"][0]["tool_name"] == "clarify_query"

    # The access fast path still works without a model.
    access_q = _run(tmp_path, database_path, "我有什么权限", None, "conv-nofast")
    assert access_q["tool_calls"][0]["tool_name"] == "describe_my_access"


# --- 6. Connector-backed arbitrary domain works the same way -----------------


_STAFFING_DOMAIN = ConnectorDomain(
    name="staffing",
    table="tblStaffing",
    fields=(
        ConnectorField(domain="staffing", name="member", is_identity=True),
        ConnectorField(domain="staffing", name="task"),
        ConnectorField(domain="staffing", name="shift"),
    ),
    record_scope=RecordScope(mode="none"),
)


class _FakeStaffingConnector:
    name = "fake_staffing"

    def catalog(self) -> tuple[ConnectorDomain, ...]:
        return (_STAFFING_DOMAIN,)

    def query(self, query: ConnectorQuery) -> list[dict[str, Any]]:
        row = {"member": "Alice", "task": "drafting", "shift": "day"}
        return [{k: row[k] for k in query.fields if k in row}]


def _staffing_user(database_path: Path) -> AccessContext:
    conn = db.connect(database_path)
    try:
        user = create_user(
            conn, email="staffing@example.com", display_name="S", role=ROLE_BUSINESS
        )
        group_id = conn.execute(
            "insert into user_groups (name) values ('staffing-grp')"
        ).lastrowid
        conn.execute(
            "insert into user_group_memberships (user_id, group_id) values (?, ?)",
            (user["id"], group_id),
        )
        for field in ("task", "shift", "member"):
            conn.execute(
                "insert into permission_grants "
                "(group_id, operation, data_domain, field_name, project_id) "
                "values (?, 'read', 'staffing', ?, null)",
                (group_id, field),
            )
        conn.commit()
        return AccessContext.from_user(user)
    finally:
        conn.close()


def _staffing_plan(field: str) -> str:
    return (
        '{"domain":"staffing","operation":"search",'
        '"filters":[{"field":"member","operator":"eq","value":"Alice"}],'
        f'"return_fields":["{field}"],"limit":1}}'
    )


def test_connector_backed_arbitrary_domain_has_no_stale_plan(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    context = _staffing_user(database_path)
    setup = ConnectorSetup(
        connector=_FakeStaffingConnector(), connector_domains=frozenset({"staffing"})
    )

    first = _run(
        tmp_path,
        database_path,
        "Alice 的任务是什么",
        StubAIProvider(_staffing_plan("task")),
        "conv-staffing",
        access_context=context,
        connector_setup=setup,
    )
    assert first["tool_calls"][0]["tool_name"] == "staffing/search"
    assert "drafting" in first["answer"]

    second = _run(
        tmp_path,
        database_path,
        "她的班次呢",
        StubAIProvider(_staffing_plan("shift")),
        "conv-staffing",
        access_context=context,
        connector_setup=setup,
    )
    # The second field replans fresh; no project-specific parsing, no stale task plan.
    assert second["tool_calls"][0]["plan"]["return_fields"] == ["shift"]
    assert "day" in second["answer"]


# --- 7. Linear fallback still catches fast-path removal -----------------------


def test_linear_fallback_does_not_resurrect_fast_path(tmp_path: Path, monkeypatch) -> None:
    # Force the linear fallback by making the LangGraph import fail.
    monkeypatch.setitem(sys.modules, "langgraph.checkpoint.sqlite", None)
    database_path = tmp_path / "legal.db"
    _seed_two_projects(database_path)

    result = _run(
        tmp_path, database_path, "请查询项目代码 VT-0001 的法务 BP 是谁", None, "conv-linear"
    )

    # Model-planned or clarified — never a fast-path plan with name="请查询项目代码…".
    assert result["tool_calls"][0]["tool_name"] == "clarify_query"
    plan = result["tool_calls"][0].get("plan")
    assert plan is None or all("请查询" not in str(f.get("value")) for f in plan["filters"])


# --- 8. Planner audit is turn-keyed ------------------------------------------


def test_planner_audit_is_turn_keyed(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    _seed_two_projects(database_path)

    _run(
        tmp_path,
        database_path,
        "VT-0001 的官网是什么",
        StubAIProvider(_project_plan("VT-0001", "website")),
        "conv-audit",
    )
    _run(
        tmp_path,
        database_path,
        "VT-0010 的官网是什么",
        StubAIProvider(_project_plan("VT-0010", "website")),
        "conv-audit",
    )

    conn = db.connect(database_path)
    try:
        selected = [
            dict(row)
            for row in conn.execute(
                "select turn_id, step_index from agent_steps "
                "where thread_id = ? and status = 'selected'",
                ("conv-audit",),
            )
        ]
    finally:
        conn.close()

    # Both turns persisted their step_index = 1 plan under distinct turn ids — the
    # old unique(thread_id, step_index) would have collided and been swallowed.
    assert len(selected) == 2
    assert all(step["step_index"] == 1 for step in selected)
    assert len({step["turn_id"] for step in selected}) == 2
