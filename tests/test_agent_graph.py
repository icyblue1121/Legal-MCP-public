from __future__ import annotations

from pathlib import Path

from legal_mcp import db
from legal_mcp.agent_steps import list_agent_steps
from legal_mcp.agent_graph import run_agent_query, run_structured_query
from legal_mcp.ai_provider import (
    AIMessage,
    AIProviderNotConfiguredError,
    AIProviderUnavailableError,
)
from legal_mcp.identity import ROLE_BUSINESS, create_user
from legal_mcp.policy import AccessContext


def _database_with_project(path: Path) -> None:
    db.initialize_database(path)
    conn = db.connect(path)
    try:
        project_id = conn.execute(
            """
            insert into projects (project_code, name, stage, legal_bp, website)
            values (?, ?, ?, ?, ?)
            """,
            ("ACME", "示例项目", "测试中", "张三", "https://example.test"),
        ).lastrowid
        conn.execute(
            """
            insert into licenses (project_id, external_key, license_type, rights_holder)
            values (?, ?, ?, ?)
            """,
            (project_id, "trademark_right", "trademark_right", "上海游碧曜网络科技有限公司"),
        )
        conn.commit()
    finally:
        conn.close()


def _database_with_related_records(path: Path) -> None:
    db.initialize_database(path)
    conn = db.connect(path)
    try:
        project_id = conn.execute(
            """
            insert into projects (project_code, name, stage, legal_bp)
            values (?, ?, ?, ?)
            """,
            ("ACME", "示例项目", "live", "张三"),
        ).lastrowid
        conn.execute(
            """
            insert into contracts (project_id, external_key, title, contract_number, counterparty)
            values (?, ?, ?, ?, ?)
            """,
            (project_id, "C-001", "腾讯框架合同", "C-001", "腾讯科技"),
        )
        conn.execute(
            """
            insert into licenses (project_id, external_key, license_type, actual_operator)
            values (?, ?, ?, ?)
            """,
            (project_id, "L-001", "版号", "某公司"),
        )
        conn.commit()
    finally:
        conn.close()


def _business_context_with_project_field_grant(
    database_path: Path,
    *,
    granted_field: str,
) -> AccessContext:
    conn = db.connect(database_path)
    try:
        project = conn.execute(
            "select id from projects where project_code = ?",
            ("ACME",),
        ).fetchone()
        user = create_user(
            conn,
            email=f"business-{granted_field}@example.com",
            display_name="Business User",
            role=ROLE_BUSINESS,
        )
        conn.execute(
            """
            insert into project_access (user_id, project_id, granted_by_user_id)
            values (?, ?, ?)
            """,
            (user["id"], project["id"], user["id"]),
        )
        group_id = conn.execute(
            "insert into user_groups (name) values (?)",
            (f"grp-{granted_field}",),
        ).lastrowid
        conn.execute(
            "insert into user_group_memberships (user_id, group_id) values (?, ?)",
            (user["id"], group_id),
        )
        conn.execute(
            """
            insert into permission_grants
              (group_id, operation, data_domain, field_name, project_id)
            values (?, ?, ?, ?, ?)
            """,
            (group_id, "read", "project", granted_field, project["id"]),
        )
        conn.commit()
        return AccessContext.from_user(user)
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


class FailingAIProvider:
    def complete(self, messages: list[AIMessage]) -> AIMessage:
        raise AssertionError("fast path should not call AI")


class SequenceAIProvider:
    def __init__(self, contents: list[str]) -> None:
        self.contents = contents
        self.calls = 0
        self.messages: list[list[AIMessage]] = []

    def complete(self, messages: list[AIMessage]) -> AIMessage:
        self.messages.append(messages)
        content = self.contents[self.calls]
        self.calls += 1
        return AIMessage(role="assistant", content=content)


class UnreachableAIProvider:
    """Simulates a configured-but-unreachable backend (ConfiguredAIProvider wrap)."""

    def complete(self, messages: list[AIMessage]) -> AIMessage:
        raise AIProviderUnavailableError(
            "AI backend at http://localhost:11434/v1 failed: connection refused"
        )


class UnconfiguredAIProvider:
    def complete(self, messages: list[AIMessage]) -> AIMessage:
        raise AIProviderNotConfiguredError("server AI provider is not configured")


def _run(tmp_path: Path, database_path: Path, question: str, provider=None, thread_id=None):
    return run_agent_query(
        question=question,
        database_path=database_path,
        checkpoint_path=tmp_path / "agent-checkpoints.sqlite",
        audit_path=tmp_path / "audit.jsonl",
        thread_id=thread_id,
        ai_provider=provider,
        # The local/library default context is now fail-closed (v0.4.5 Phase 1);
        # these tests exercise full-access query behavior, so they pass the explicit
        # local-operator capability instead of relying on a None fall-through.
        access_context=AccessContext.local_operator(),
    )


def test_run_agent_query_returns_answer_and_persists_run(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    _database_with_project(database_path)
    provider = StubAIProvider(
        '{"domain":"project","operation":"search",'
        '"filters":[{"field":"name","operator":"eq","value":"示例项目"}],'
        '"return_fields":["website"],"limit":1}'
    )

    result = _run(tmp_path, database_path, "ACME 的官网是什么？", provider, "pytest-thread")

    assert result["thread_id"] == "pytest-thread"
    assert "https://example.test" in result["answer"]
    assert result["tool_calls"][0]["tool_name"] == "project/search"

    conn = db.connect(database_path)
    try:
        row = conn.execute("select thread_id, status, selected_tool from agent_runs").fetchone()
    finally:
        conn.close()
    assert row["status"] == "success"
    assert row["selected_tool"] == "project/search"


def test_agent_graph_surfaces_unreachable_backend_as_loud_error(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    _database_with_project(database_path)

    result = _run(tmp_path, database_path, "q", UnreachableAIProvider(), "loud-thread")

    assert result["status"] == "error"
    assert result["error"]["code"] == "ai_backend_unreachable"
    assert "11434" in result["error"]["message"]


def test_agent_graph_unconfigured_backend_degrades_to_clarify(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    _database_with_project(database_path)

    result = _run(tmp_path, database_path, "q", UnconfiguredAIProvider(), "off-thread")

    assert result["status"] == "success"
    assert result["tool_calls"][0]["tool_name"] == "clarify_query"


_WEBSITE_PLAN = (
    '{"domain":"project","operation":"search",'
    '"filters":[{"field":"project_code","operator":"eq","value":"ACME"}],'
    '"return_fields":["website"],"limit":1}'
)


def test_agent_graph_plans_common_project_field_via_catalog(tmp_path: Path) -> None:
    # v0.4.6 §A: project field questions are catalog-bound model planning now, not
    # a global fast path. The end-to-end retrieval still works.
    database_path = tmp_path / "legal.db"
    _database_with_project(database_path)

    result = _run(
        tmp_path, database_path, "ACME 的官网是什么？", StubAIProvider(_WEBSITE_PLAN), "fast-thread"
    )

    assert result["status"] == "success"
    assert result["tool_calls"][0]["tool_name"] == "project/search"
    assert "https://example.test" in result["answer"]


def test_agent_graph_fast_path_uses_database_records_not_hardcoded_values(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        conn.execute(
            """
            insert into projects (project_code, name, stage, website)
            values (?, ?, ?, ?)
            """,
            ("ARBITRARY-42", "临时项目", "testing", "https://arbitrary.example"),
        )
        conn.commit()
    finally:
        conn.close()

    arbitrary_plan = (
        '{"domain":"project","operation":"search",'
        '"filters":[{"field":"project_code","operator":"eq","value":"ARBITRARY-42"}],'
        '"return_fields":["website"],"limit":1}'
    )
    result = _run(
        tmp_path,
        database_path,
        "ARBITRARY-42 的官网是什么？",
        StubAIProvider(arbitrary_plan),
        "fast-data-thread",
    )

    assert result["status"] == "success"
    assert "https://arbitrary.example" in result["answer"]


def test_agent_graph_retries_repairable_ai_plan_error(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    _database_with_project(database_path)
    provider = SequenceAIProvider(
        [
            '{"domain":"project","operation":"search",'
            '"filters":[{"field":"project_code","operator":"eq","value":"ACME"}],'
            '"return_fields":["web_address"],"limit":1}',
            '{"domain":"project","operation":"search",'
            '"filters":[{"field":"project_code","operator":"eq","value":"ACME"}],'
            '"return_fields":["website"],"limit":1}',
        ]
    )

    result = _run(tmp_path, database_path, "请查询 ACME 的页面链接", provider, "retry-thread")

    assert result["status"] == "success"
    assert provider.calls == 2
    assert "https://example.test" in result["answer"]
    retry_prompt = "\n".join(message.content for message in provider.messages[1])
    assert "unknown_return_field" in retry_prompt


def test_agent_graph_records_selected_ai_step_with_turn_id(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    _database_with_project(database_path)

    result = _run(
        tmp_path, database_path, "ACME 的官网是什么？", StubAIProvider(_WEBSITE_PLAN), "steps-fast"
    )

    conn = db.connect(database_path)
    try:
        steps = list_agent_steps(conn, "steps-fast")
    finally:
        conn.close()

    assert result["status"] == "success"
    assert steps[0]["planner_source"] == "ai"
    assert steps[0]["status"] == "selected"
    assert steps[0]["turn_id"]  # v0.4.6 §F: the selected plan is keyed to its turn
    assert '"website"' in steps[0]["plan_json"]


def test_agent_graph_records_ai_retry_steps(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    _database_with_project(database_path)
    provider = SequenceAIProvider(
        [
            '{"domain":"project","operation":"search",'
            '"filters":[{"field":"project_code","operator":"eq","value":"ACME"}],'
            '"return_fields":["web_address"],"limit":1}',
            '{"domain":"project","operation":"search",'
            '"filters":[{"field":"project_code","operator":"eq","value":"ACME"}],'
            '"return_fields":["website"],"limit":1}',
        ]
    )

    _run(tmp_path, database_path, "请查询 ACME 的页面链接", provider, "steps-retry")

    conn = db.connect(database_path)
    try:
        steps = list_agent_steps(conn, "steps-retry")
    finally:
        conn.close()

    assert [step["planner_source"] for step in steps] == ["ai", "ai_retry"]
    assert steps[0]["status"] == "rejected"
    assert steps[0]["error_code"] == "unknown_return_field"
    assert steps[-1]["status"] == "selected"


def test_agent_graph_does_not_retry_authorization_denial(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    _database_with_project(database_path)
    context = _business_context_with_project_field_grant(
        database_path,
        granted_field="legal_bp",
    )
    provider = SequenceAIProvider(
        [
            '{"domain":"project","operation":"search",'
            '"filters":[{"field":"project_code","operator":"eq","value":"ACME"}],'
            '"return_fields":["website"],"limit":1}',
            '{"domain":"project","operation":"search",'
            '"filters":[{"field":"project_code","operator":"eq","value":"ACME"}],'
            '"return_fields":["legal_bp"],"limit":1}',
        ]
    )

    result = run_agent_query(
        question="请查询 ACME 的页面链接",
        database_path=database_path,
        checkpoint_path=tmp_path / "agent-checkpoints.sqlite",
        audit_path=tmp_path / "audit.jsonl",
        access_context=context,
        thread_id="auth-denied",
        ai_provider=provider,
    )

    assert result["status"] == "error"
    assert result["error"]["code"] == "return_field_access_denied"
    assert provider.calls == 1


def test_agent_graph_db_grant_authorizes_field_on_live_path(tmp_path: Path) -> None:
    # On the live (LLM-planned) path, a DB grant for legal_bp authorizes the plan
    # that returns it — the DB grant is the sole field gate (v0.4.0 §C).
    database_path = tmp_path / "legal.db"
    _database_with_project(database_path)
    context = _business_context_with_project_field_grant(
        database_path,
        granted_field="legal_bp",
    )
    legal_bp_plan = (
        '{"domain":"project","operation":"search",'
        '"filters":[{"field":"project_code","operator":"eq","value":"ACME"}],'
        '"return_fields":["legal_bp"],"limit":1}'
    )

    granted = run_agent_query(
        question="ACME 的法务BP是谁？",
        database_path=database_path,
        checkpoint_path=tmp_path / "agent-checkpoints.sqlite",
        audit_path=tmp_path / "audit.jsonl",
        access_context=context,
        thread_id="grant-allowed",
        ai_provider=StubAIProvider(legal_bp_plan),
    )
    assert granted["status"] == "success"


def test_agent_graph_planned_query_still_runs_authorization(tmp_path: Path) -> None:
    # A model-planned project field is still field-gated: a user granted only
    # legal_bp cannot read website even when the planner produces a valid plan.
    database_path = tmp_path / "legal.db"
    _database_with_project(database_path)
    context = _business_context_with_project_field_grant(
        database_path,
        granted_field="legal_bp",
    )

    result = run_agent_query(
        question="ACME 的官网是什么？",
        database_path=database_path,
        checkpoint_path=tmp_path / "agent-checkpoints.sqlite",
        audit_path=tmp_path / "audit.jsonl",
        access_context=context,
        thread_id="fast-auth-denied",
        ai_provider=StubAIProvider(_WEBSITE_PLAN),
    )

    assert result["status"] == "error"
    assert result["error"]["code"] == "return_field_access_denied"


def test_agent_graph_builds_project_search_plan_for_legal_bp(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    _database_with_project(database_path)
    provider = StubAIProvider(
        '{"domain":"project","operation":"search",'
        '"filters":[{"field":"legal_bp","operator":"eq","value":"张三"}],'
        '"return_fields":["project_code","name"]}'
    )

    result = _run(tmp_path, database_path, "张三是哪些项目的法务BP？", provider)

    assert result["status"] == "success"
    assert result["tool_calls"][0]["tool_name"] == "project/search"
    assert result["tool_calls"][0]["plan"]["filters"] == [
        {"field": "legal_bp", "operator": "eq", "value": "张三"}
    ]
    assert "示例项目" in result["answer"]


def test_agent_graph_builds_contract_license_and_cross_domain_plans(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    _database_with_related_records(database_path)

    cases = [
        (
            '{"domain":"contract","operation":"search",'
            '"filters":[{"field":"counterparty","operator":"contains","value":"腾讯"}],'
            '"return_fields":["contract_number","title"]}',
            "contract/search",
        ),
        (
            '{"domain":"license","operation":"search",'
            '"filters":[{"field":"actual_operator","operator":"eq","value":"某公司"}],'
            '"return_fields":["license_type","actual_operator"]}',
            "license/search",
        ),
        (
            '{"domain":"cross_domain","operation":"search",'
            '"filters":[{"field":"q","operator":"contains","value":"张三"}],'
            '"return_fields":[]}',
            "cross_domain/search",
        ),
    ]
    for content, expected_tool_name in cases:
        result = _run(tmp_path, database_path, "q", StubAIProvider(content))
        assert result["status"] == "success"
        assert result["tool_calls"][0]["tool_name"] == expected_tool_name


def test_agent_graph_recovers_dict_filters_and_read_operation(tmp_path: Path) -> None:
    # Production regression: model returns object filters + operation "read".
    database_path = tmp_path / "legal.db"
    _database_with_project(database_path)
    provider = StubAIProvider(
        '{"domain":"project","operation":"read",'
        '"filters":{"name":"示例项目"},"return_fields":["website"]}'
    )

    result = _run(tmp_path, database_path, "示例项目的官网", provider)

    assert result["status"] == "success"
    assert result["tool_calls"][0]["tool_name"] == "project/search"
    assert "https://example.test" in result["answer"]


def test_agent_graph_parses_fenced_json(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    _database_with_project(database_path)
    provider = StubAIProvider(
        "```json\n"
        '{"domain":"project","operation":"search",'
        '"filters":[{"field":"name","operator":"eq","value":"示例项目"}],'
        '"return_fields":["website"]}\n'
        "```"
    )

    result = _run(tmp_path, database_path, "示例项目的官网", provider)

    assert result["status"] == "success"
    assert result["tool_calls"][0]["tool_name"] == "project/search"


def test_agent_graph_routes_access_intent_to_describe_my_access(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    _database_with_project(database_path)
    provider = StubAIProvider('{"intent":"access"}')

    result = _run(tmp_path, database_path, "我能访问哪些项目？", provider)

    assert result["status"] == "success"
    assert result["tool_calls"][0]["tool_name"] == "describe_my_access"


def test_agent_graph_routes_clarify_intent_with_reason(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    _database_with_project(database_path)
    provider = StubAIProvider('{"intent":"clarify"}')

    result = _run(tmp_path, database_path, "把所有项目资料都给我", provider)

    assert result["status"] == "success"
    assert result["tool_calls"][0]["tool_name"] == "clarify_query"
    assert "请明确" in result["answer"]

    conn = db.connect(database_path)
    try:
        row = conn.execute("select selected_tool, error_code from agent_runs").fetchone()
    finally:
        conn.close()
    assert row["selected_tool"] == "clarify_query"
    assert row["error_code"] and row["error_code"].startswith("clarify:")


def test_agent_graph_clarifies_when_ai_unavailable(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    _database_with_project(database_path)

    result = _run(tmp_path, database_path, "请分析示例项目整体情况", provider=None)

    assert result["status"] == "success"
    assert result["tool_calls"][0]["tool_name"] == "clarify_query"


def test_agent_graph_can_use_ai_provider_without_exposing_tools(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    _database_with_project(database_path)
    provider = StubAIProvider(
        '{"domain":"project","operation":"search","filters":[],"return_fields":["website"]}'
    )

    result = _run(tmp_path, database_path, "请查询 ACME 的页面链接", provider, "pytest-provider-thread")

    assert result["status"] == "success"
    assert provider.messages
    serialized_messages = "\n".join(message.content for message in provider.messages)
    assert "database handle" not in serialized_messages
    assert "get_project_fields" not in serialized_messages


def test_agent_graph_uses_server_ai_catalog_plan_for_non_regex_question(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    _database_with_project(database_path)
    provider = StubAIProvider(
        '{"domain":"license","operation":"search",'
        '"filters":['
        '{"field":"project_code","operator":"eq","value":"ACME"},'
        '{"field":"license_type","operator":"eq","value":"trademark_right"}'
        '],'
        '"return_fields":["license_type","rights_holder"],"limit":20}'
    )

    result = _run(tmp_path, database_path, "请告诉我 ACME trademark holder", provider)

    assert result["status"] == "success"
    assert result["tool_calls"][0]["tool_name"] == "license/search"
    assert "上海游碧曜网络科技有限公司" in result["answer"]
    serialized_messages = "\n".join(message.content for message in provider.messages)
    assert "rights_holder" in serialized_messages
    assert "get_project_fields" not in serialized_messages


def test_agent_graph_rejects_overbroad_license_return_fields(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    _database_with_project(database_path)
    provider = StubAIProvider(
        '{"domain":"license","operation":"search",'
        '"filters":[{"field":"project_code","operator":"eq","value":"ACME"}],'
        '"return_fields":['
        '"license_type","identifier","rights_holder","copyright_holder",'
        '"operating_entity","actual_operator","approval_number"'
        '],"limit":20}'
    )

    result = _run(tmp_path, database_path, "请返回 ACME 的所有 license 信息", provider)

    assert result["status"] == "error"
    assert result["error"]["code"] == "overbroad_return_fields"


def test_agent_graph_answers_project_trademark_rights_holder(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    _database_with_project(database_path)
    provider = StubAIProvider(
        '{"domain":"license","operation":"search",'
        '"filters":['
        '{"field":"project_code","operator":"eq","value":"Acme"},'
        '{"field":"license_type","operator":"eq","value":"trademark_right"}'
        '],'
        '"return_fields":["license_type","rights_holder"]}'
    )

    result = _run(tmp_path, database_path, "Acme 的商标在哪家公司", provider, "pytest-trademark-thread")

    assert result["status"] == "success"
    assert result["tool_calls"][0]["tool_name"] == "license/search"
    assert "上海游碧曜网络科技有限公司" in result["answer"]
