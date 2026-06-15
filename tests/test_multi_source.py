"""Multi-source fallback + source disambiguation (v0.4.9).

A domain configured with several sources answers from the primary; an empty
primary falls back to the other sources in declared order. Exactly one source
with rows answers (tagged ``data_source``); several sources with rows return a
``source_disambiguation`` so the agent asks the user which source to use, and
the choice is honored by a plan that pins ``data_source``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from legal_mcp import db
from legal_mcp.agent_graph import run_agent_query
from legal_mcp.ai_provider import AIMessage
from legal_mcp.connector_config import ConnectorSetup
from legal_mcp.connectors.base import (
    ConnectorDomain,
    ConnectorField,
    ConnectorQuery,
    RecordScope,
)
from legal_mcp.connectors.composite import CompositeConnector
from legal_mcp.policy import AccessContext


def _project_domain() -> ConnectorDomain:
    return ConnectorDomain(
        name="project",
        table="projects",
        fields=(
            ConnectorField(domain="project", name="project_code", is_identity=True),
            ConnectorField(domain="project", name="name", is_identity=True),
            ConnectorField(domain="project", name="legal_bp"),
            ConnectorField(domain="project", name="website"),
        ),
        record_scope=RecordScope(mode="none"),
    )


class _FakeSource:
    """A named connector serving the project domain with canned rows."""

    def __init__(self, name: str, rows: list[dict[str, Any]]) -> None:
        self.name = name
        self.rows = rows
        self.query_count = 0

    def catalog(self) -> tuple[ConnectorDomain, ...]:
        return (_project_domain(),)

    def query(self, query: ConnectorQuery) -> list[dict[str, Any]]:
        self.query_count += 1
        return list(self.rows)


class StubAIProvider:
    def __init__(self, content: str) -> None:
        self.content = content

    def complete(self, messages: list[AIMessage]) -> AIMessage:
        return AIMessage(role="assistant", content=self.content)


def _setup(*sources: _FakeSource) -> ConnectorSetup:
    return ConnectorSetup(
        connector=CompositeConnector({"project": list(sources)}),
        connector_domains=frozenset({"project"}),
    )


def _plan_json(**extra: Any) -> str:
    plan = {
        "domain": "project",
        "operation": "search",
        "filters": [{"field": "name", "operator": "eq", "value": "指间山海"}],
        "return_fields": ["legal_bp"],
        "limit": 10,
        **extra,
    }
    return json.dumps(plan, ensure_ascii=False)


def _run(
    tmp_path: Path,
    setup: ConnectorSetup,
    plan_json: str,
    *,
    thread_id: str = "conv-multi-source",
) -> dict[str, Any]:
    database_path = tmp_path / "legal.db"
    if not database_path.exists():
        db.initialize_database(database_path)
    return run_agent_query(
        question="指间山海的法务BP是谁",
        database_path=database_path,
        checkpoint_path=tmp_path / "agent-checkpoints.sqlite",
        audit_path=tmp_path / "audit.jsonl",
        thread_id=thread_id,
        ai_provider=StubAIProvider(plan_json),
        access_context=AccessContext.local_operator(),
        connector_setup=setup,
    )


_ROW = {"project_code": "VT-0010", "name": "指间山海", "legal_bp": "乙BP"}


def test_primary_hit_skips_fallback_and_tags_source(tmp_path: Path) -> None:
    primary = _FakeSource("feishu", [_ROW])
    fallback = _FakeSource("local-db", [dict(_ROW, legal_bp="丙BP")])
    result = _run(tmp_path, _setup(primary, fallback), _plan_json())

    assert result["status"] == "success"
    assert result["result"]["projects"] == [{"legal_bp": "乙BP"}]
    assert result["result"]["data_source"] == "feishu"
    assert fallback.query_count == 0  # primary answered; fallback never queried


def test_empty_primary_falls_back_to_single_other_source(tmp_path: Path) -> None:
    primary = _FakeSource("feishu", [])
    fallback = _FakeSource("local-db", [_ROW])
    result = _run(tmp_path, _setup(primary, fallback), _plan_json())

    assert result["result"]["projects"] == [{"legal_bp": "乙BP"}]
    assert result["result"]["data_source"] == "local-db"


def test_all_sources_empty_keeps_primary_no_rows_result(tmp_path: Path) -> None:
    primary = _FakeSource("feishu", [])
    fallback = _FakeSource("local-db", [])
    result = _run(tmp_path, _setup(primary, fallback), _plan_json())

    assert result["result"]["projects"] == []
    assert result["result"]["data_source"] == "feishu"
    assert "source_disambiguation" not in result["result"]


def test_multiple_sources_with_rows_ask_for_a_choice(tmp_path: Path) -> None:
    primary = _FakeSource("feishu", [])
    second = _FakeSource("backup-feishu", [_ROW])
    third = _FakeSource("local-db", [dict(_ROW, legal_bp="丙BP")])
    result = _run(tmp_path, _setup(primary, second, third), _plan_json())

    # No rows are returned until the user picks a source — provenance never mixes.
    assert result["result"]["projects"] == []
    disambiguation = result["result"]["source_disambiguation"]
    assert [entry["source"] for entry in disambiguation["sources"]] == [
        "backup-feishu",
        "local-db",
    ]
    assert all(entry["record_count"] == 1 for entry in disambiguation["sources"])

    # The pending choice is recorded as conversation context for the next turn.
    conn = db.connect(tmp_path / "legal.db")
    try:
        row = conn.execute(
            "select safe_context_json from agent_turn_context order by id desc limit 1"
        ).fetchone()
    finally:
        conn.close()
    context = json.loads(row["safe_context_json"])
    assert context["pending_source_choice"]["sources"] == ["backup-feishu", "local-db"]
    assert context["pending_source_choice"]["domain"] == "project"


def test_plan_data_source_pins_the_named_source(tmp_path: Path) -> None:
    primary = _FakeSource("feishu", [_ROW])
    fallback = _FakeSource("local-db", [dict(_ROW, legal_bp="丙BP")])
    result = _run(
        tmp_path, _setup(primary, fallback), _plan_json(data_source="local-db")
    )

    assert result["result"]["projects"] == [{"legal_bp": "丙BP"}]
    assert result["result"]["data_source"] == "local-db"
    assert primary.query_count == 0  # pinned: the primary is not consulted


def test_unknown_data_source_fails_closed(tmp_path: Path) -> None:
    primary = _FakeSource("feishu", [_ROW])
    result = _run(tmp_path, _setup(primary), _plan_json(data_source="nope"))

    assert result["status"] == "error"
    assert result["error"]["code"] == "unknown_data_source"


def test_disabled_source_is_skipped_during_fallback(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        db.set_data_source_disabled(conn, "local-db", disabled=True)
    finally:
        conn.close()

    primary = _FakeSource("feishu", [])
    disabled = _FakeSource("local-db", [_ROW])
    result = _run(tmp_path, _setup(primary, disabled), _plan_json())

    assert result["result"]["projects"] == []
    assert disabled.query_count == 0
