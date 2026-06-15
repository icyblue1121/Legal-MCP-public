from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, TypedDict

from legal_mcp import db
from legal_mcp.agent_fast_path import plan_fast_path
from legal_mcp.agent_observability import build_trace_metadata, flush_langfuse, langfuse_callbacks
from legal_mcp.agent_retry import is_retryable_plan_error, repair_messages
from legal_mcp.agent_steps import record_agent_step
from legal_mcp.agent_router import (
    clarify_result,
    query_plan_from_model_intent,
)
from legal_mcp.audit import DEFAULT_AUDIT_PATH
from legal_mcp.ai_provider import AIMessage, AIProvider, AIProviderNotConfiguredError
from legal_mcp.conversation_context import load_conversation_context, record_turn_context
from legal_mcp.policy import AccessContext
from legal_mcp.connector_config import ConnectorSetup
from legal_mcp.connector_retrieval import execute_connector_plan, result_key_for_domain
from legal_mcp.connectors.sqlite_demo import SqliteDemoConnector
from legal_mcp.query_authorization import authorize_query_plan
from legal_mcp.query_catalog import (
    QueryCatalog,
    build_query_catalog_from_connector,
    catalog_context_for_prompt,
)
from legal_mcp.query_plan import VIRTUAL_IDENTITY_FIELD, QueryFilter, QueryPlan
from legal_mcp.search_tools import execute_search_plan
from legal_mcp.tools_access import describe_my_access

MAX_PLAN_ATTEMPTS = 3

# State a single turn produces. A natural-language turn must start with none of
# these present (v0.4.6 §C): each turn replans from the current question, never
# inherits a previous turn's plan/result/error. A fresh per-turn checkpoint id
# (§B) guarantees this in normal operation; ``start_turn`` clears any that leak in
# as a second guardrail for future entry points.
TURN_LOCAL_KEYS = (
    "query_type",
    "normalized_question",
    "query_plan",
    "tool_result",
    "answer",
    "error",
    "tool_calls",
    "model_intent",
    "clarify_reason",
    "planner_source",
    "planner_reason",
    "diagnostic",
    "clarification",
)


class AgentState(TypedDict, total=False):
    # Turn envelope (v0.4.6 §B): conversation_id is the client thread id (groups
    # turns); turn_id is unique per agent_query invocation and keys the LangGraph
    # checkpoint so one turn's state can never become the next turn's state.
    conversation_id: str
    turn_id: str
    input_mode: str  # "natural_language" | "structured"
    question: str
    conversation_context: dict[str, Any]
    query_type: str
    normalized_question: str
    query_plan: QueryPlan
    tool_result: dict[str, Any]
    answer: str
    error: dict[str, Any]
    tool_calls: list[dict[str, Any]]
    model_intent: dict[str, Any]
    clarify_reason: str
    planner_source: str
    planner_reason: str
    diagnostic: dict[str, Any]
    # Leak-free guidance for an authorized-but-empty result (v0.5.4). Built from
    # catalog metadata + the user's own filters, never from fetched rows.
    clarification: dict[str, Any]


def run_agent_query(
    *,
    question: str,
    database_path: str | Path,
    checkpoint_path: str | Path | None = None,
    audit_path: str | Path = DEFAULT_AUDIT_PATH,
    access_context: AccessContext | None = None,
    thread_id: str | None = None,
    ai_provider: AIProvider | None = None,
    connector_setup: ConnectorSetup | None = None,
) -> dict[str, Any]:
    # thread_id is the client *conversation* id, echoed back unchanged. Each call
    # is its own turn with a fresh turn_id; the turn_id (not the conversation id)
    # keys the LangGraph checkpoint, so a second question on the same thread cannot
    # replay the first question's plan (v0.4.6 §B).
    conversation_id = thread_id or str(uuid.uuid4())
    turn_id = str(uuid.uuid4())
    actual_checkpoint_path = (
        Path(checkpoint_path) if checkpoint_path else _default_checkpoint_path(database_path)
    )
    state = _run_graph(
        question=question,
        database_path=database_path,
        audit_path=audit_path,
        access_context=access_context,
        checkpoint_path=actual_checkpoint_path,
        conversation_id=conversation_id,
        turn_id=turn_id,
        ai_provider=ai_provider,
        connector_setup=connector_setup,
    )
    result = _result_from_state(state, conversation_id)
    _record_agent_run(database_path, conversation_id, question, result)
    return result


def run_structured_query(
    *,
    query: dict[str, Any],
    database_path: str | Path,
    checkpoint_path: str | Path | None = None,
    audit_path: str | Path = DEFAULT_AUDIT_PATH,
    access_context: AccessContext | None = None,
    thread_id: str | None = None,
    connector_setup: ConnectorSetup | None = None,
) -> dict[str, Any]:
    plan = _query_plan_from_payload(query)
    conversation_id = thread_id or str(uuid.uuid4())
    turn_id = str(uuid.uuid4())
    actual_checkpoint_path = (
        Path(checkpoint_path) if checkpoint_path else _default_checkpoint_path(database_path)
    )
    state = _run_graph(
        question="structured_query",
        database_path=database_path,
        audit_path=audit_path,
        access_context=access_context,
        checkpoint_path=actual_checkpoint_path,
        conversation_id=conversation_id,
        turn_id=turn_id,
        structured_plan=plan,
        connector_setup=connector_setup,
    )
    result = _result_from_state(state, conversation_id)
    _record_agent_run(database_path, conversation_id, "structured_query", result)
    return result


def _result_from_state(state: AgentState, thread_id: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "answer": state.get("answer") or "",
        "thread_id": thread_id,
        "tool_calls": state.get("tool_calls") or [],
        "status": "error" if state.get("error") else "success",
    }
    if state.get("tool_result") is not None:
        result["result"] = state["tool_result"]
    if state.get("clarification"):
        # Structured, leak-free no_rows guidance (v0.5.4), alongside the rendered
        # answer string, so a client can drive its own clarification UX.
        result["clarification"] = state["clarification"]
    if state.get("error"):
        result["error"] = state["error"]
    return result


def _default_checkpoint_path(database_path: str | Path) -> Path:
    return Path(database_path).with_name("legal-mcp-agent-checkpoints.sqlite")


def _run_graph(
    *,
    question: str,
    database_path: str | Path,
    audit_path: str | Path,
    access_context: AccessContext | None,
    checkpoint_path: Path,
    conversation_id: str,
    turn_id: str,
    ai_provider: AIProvider | None = None,
    structured_plan: QueryPlan | None = None,
    connector_setup: ConnectorSetup | None = None,
) -> AgentState:
    # v0.5.6: fold runtime-registered (active) data sources into the static setup,
    # so a DB-registered source takes effect without a restart. No active rows ->
    # the static setup is returned unchanged.
    from legal_mcp.connector_config import effective_connector_setup

    connector_setup = effective_connector_setup(connector_setup, database_path)
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
        from langgraph.graph import END, START, StateGraph
    except ImportError:
        return _run_linear_graph(
            question=question,
            database_path=database_path,
            audit_path=audit_path,
            access_context=access_context,
            checkpoint_path=checkpoint_path,
            conversation_id=conversation_id,
            turn_id=turn_id,
            ai_provider=ai_provider,
            structured_plan=structured_plan,
            connector_setup=connector_setup,
        )

    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_conn = sqlite3.connect(checkpoint_path, check_same_thread=False)
    try:
        checkpointer = SqliteSaver(checkpoint_conn)
        graph = _build_langgraph(
            database_path=database_path,
            audit_path=audit_path,
            access_context=access_context,
            checkpointer=checkpointer,
            state_graph_cls=StateGraph,
            start=START,
            end=END,
            ai_provider=ai_provider,
            connector_setup=connector_setup,
        )
        # The checkpoint thread is the per-turn id, NOT the conversation id, so a
        # later turn on the same conversation starts from an empty checkpoint and
        # cannot inherit this turn's query_plan/tool_result/answer (v0.4.6 §B).
        config: dict[str, Any] = {"configurable": {"thread_id": turn_id}}
        callbacks = langfuse_callbacks()
        if callbacks:
            config["callbacks"] = callbacks
            config["metadata"] = build_trace_metadata(
                thread_id=conversation_id,
                turn_id=turn_id,
                tool_name=None,
                status="started",
                user_id=(
                    str(access_context.user_id)
                    if access_context is not None and access_context.user_id is not None
                    else None
                ),
            )
        initial_state = _initial_state(
            question=question,
            conversation_id=conversation_id,
            turn_id=turn_id,
            structured_plan=structured_plan,
        )
        try:
            return graph.invoke(initial_state, config)
        finally:
            if callbacks:
                flush_langfuse()
    finally:
        checkpoint_conn.close()


def _initial_state(
    *,
    question: str,
    conversation_id: str,
    turn_id: str,
    structured_plan: QueryPlan | None,
) -> AgentState:
    """The clean turn envelope (v0.4.6 §C). No turn-local result keys are present."""
    if structured_plan is not None:
        return {
            "conversation_id": conversation_id,
            "turn_id": turn_id,
            "question": "structured_query",
            "input_mode": "structured",
            "query_plan": structured_plan,
        }
    return {
        "conversation_id": conversation_id,
        "turn_id": turn_id,
        "question": question,
        "input_mode": "natural_language",
    }


def _build_langgraph(
    *,
    database_path: str | Path,
    audit_path: str | Path,
    access_context: AccessContext | None,
    checkpointer: Any,
    state_graph_cls: Any,
    start: str,
    end: str,
    ai_provider: AIProvider | None = None,
    connector_setup: ConnectorSetup | None = None,
) -> Any:
    workflow = state_graph_cls(AgentState)
    workflow.add_node("start_turn", start_turn)
    workflow.add_node("load_context", lambda state: load_context(state, database_path))
    workflow.add_node("classify_intent", classify_intent)
    workflow.add_node(
        "plan_query",
        lambda state: plan_query(
            state, database_path, ai_provider, connector_setup=connector_setup
        ),
    )
    workflow.add_node(
        "validate_plan", lambda state: validate_plan(state, database_path, connector_setup)
    )
    workflow.add_node(
        "authorize_plan",
        lambda state: authorize_plan(state, database_path, access_context, connector_setup),
    )
    workflow.add_node(
        "execute_plan",
        lambda state: execute_plan(
            state, database_path, audit_path, access_context, connector_setup
        ),
    )
    workflow.add_node("format_answer", format_answer)
    workflow.add_node(
        "record_context", lambda state: record_context(state, database_path, connector_setup)
    )
    workflow.add_edge(start, "start_turn")
    workflow.add_edge("start_turn", "load_context")
    workflow.add_edge("load_context", "classify_intent")
    workflow.add_edge("classify_intent", "plan_query")
    workflow.add_edge("plan_query", "validate_plan")
    workflow.add_edge("validate_plan", "authorize_plan")
    workflow.add_edge("authorize_plan", "execute_plan")
    workflow.add_edge("execute_plan", "format_answer")
    workflow.add_edge("format_answer", "record_context")
    workflow.add_edge("record_context", end)
    return workflow.compile(checkpointer=checkpointer)


def _run_linear_graph(
    *,
    question: str,
    database_path: str | Path,
    audit_path: str | Path,
    access_context: AccessContext | None,
    checkpoint_path: Path,
    conversation_id: str,
    turn_id: str,
    ai_provider: AIProvider | None = None,
    structured_plan: QueryPlan | None = None,
    connector_setup: ConnectorSetup | None = None,
) -> AgentState:
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_conn = sqlite3.connect(checkpoint_path)
    try:
        checkpoint_conn.execute(
            """
            create table if not exists agent_checkpoints (
              thread_id text not null,
              node text not null,
              state_json text not null,
              created_at text not null default (datetime('now'))
            )
            """
        )
        state: AgentState = _initial_state(
            question=question,
            conversation_id=conversation_id,
            turn_id=turn_id,
            structured_plan=structured_plan,
        )
        nodes = (
            ("start_turn", lambda: start_turn(state)),
            ("load_context", lambda: load_context(state, database_path)),
            ("classify_intent", lambda: classify_intent(state)),
            (
                "plan_query",
                lambda: plan_query(
                    state, database_path, ai_provider, connector_setup=connector_setup
                ),
            ),
            ("validate_plan", lambda: validate_plan(state, database_path, connector_setup)),
            ("authorize_plan", lambda: authorize_plan(state, database_path, access_context, connector_setup)),
            (
                "execute_plan",
                lambda: execute_plan(
                    state, database_path, audit_path, access_context, connector_setup
                ),
            ),
            ("format_answer", lambda: format_answer(state)),
            ("record_context", lambda: record_context(state, database_path, connector_setup)),
        )
        for node_name, node in nodes:
            update = node()
            state.update(update)
            checkpoint_conn.execute(
                """
                insert into agent_checkpoints (thread_id, node, state_json)
                values (?, ?, ?)
                """,
                (
                    turn_id,
                    node_name,
                    json.dumps(_checkpoint_state(state), ensure_ascii=False, sort_keys=True),
                ),
            )
        checkpoint_conn.commit()
        return state
    finally:
        checkpoint_conn.close()


def start_turn(state: AgentState) -> dict[str, Any]:
    """Open a turn: normalize the question and clear any leaked turn-local state.

    A fresh per-turn checkpoint id already starts clean, so in normal operation
    this only sets ``normalized_question``. The clear is defense in depth for a
    future entry point that hands in an existing state object: a stale plan,
    result, error, or answer is dropped so a natural-language turn always replans
    from ``question`` (v0.4.6 §C). Structured turns keep their supplied plan.
    """
    update: dict[str, Any] = {"normalized_question": (state.get("question") or "").strip()}
    if state.get("input_mode") == "structured":
        return update
    for key in TURN_LOCAL_KEYS:
        if key == "normalized_question":
            continue
        if state.get(key) is not None:
            update[key] = None
    return update


def load_context(state: AgentState, database_path: str | Path) -> dict[str, Any]:
    """Load safe prior-turn context for this conversation (v0.4.6 §D).

    Returns a narrow, structured context object — entity identities and field
    names already disclosed to the requester — never a hydrated prior graph state.
    """
    conversation_id = state.get("conversation_id")
    if not conversation_id:
        return {}
    conn = db.connect(database_path)
    try:
        context = load_conversation_context(conn, conversation_id)
    finally:
        conn.close()
    return {"conversation_context": context} if context else {}


def classify_intent(state: AgentState) -> dict[str, Any]:
    """Decide the *kind* of request, deterministically where source-independent.

    Separated from planning (v0.4.6 §E): a caller-supplied structured plan and an
    access-scope question both skip the model; everything else is left unclassified
    for ``plan_query`` to ground against the live catalog. There is no path where a
    stale ``query_plan`` changes a natural-language turn's control flow (§C).
    """
    if state.get("input_mode") == "structured":
        return {"query_type": "search"}
    fast = plan_fast_path(state.get("question") or "")
    if fast is not None and fast.intent == "access":
        return {
            "query_type": "access",
            "planner_source": "fast_path",
            "planner_reason": fast.reason,
        }
    if fast is not None and fast.intent == "search" and fast.plan is not None:
        return {
            "query_type": "search",
            "query_plan": fast.plan,
            "planner_source": "fast_path",
            "planner_reason": fast.reason,
        }
    return {}


def plan_query(
    state: AgentState,
    database_path: str | Path,
    ai_provider: AIProvider | None = None,
    *,
    connector_setup: ConnectorSetup | None = None,
) -> dict[str, Any]:
    """Produce exactly one QueryPlan (or a clarification) for a natural-language turn.

    No-op when ``classify_intent`` already settled the turn (structured plan or
    access). Otherwise the catalog-bound planner runs — there is no global
    business fast path for project/license fields anymore (v0.4.6 §A).
    """
    if state.get("query_type"):
        return {}
    catalog = _catalog_for_database(database_path, connector_setup)
    return _classify_with_ai_planning(
        state.get("question") or "",
        ai_provider,
        catalog,
        database_path=database_path,
        conversation_id=state.get("conversation_id"),
        turn_id=state.get("turn_id"),
        conversation_context=state.get("conversation_context") or {},
    )


def _classify_with_ai_planning(
    question: str,
    ai_provider: AIProvider | None,
    catalog: QueryCatalog,
    *,
    database_path: str | Path,
    conversation_id: str | None,
    turn_id: str | None,
    conversation_context: dict[str, Any],
) -> dict[str, Any]:
    if ai_provider is None:
        return {"query_type": "clarify", "clarify_reason": "server AI is not configured"}

    model_intent: dict[str, Any] = {}
    last_response = ""
    error_code = ""
    error_message = ""

    for attempt in range(MAX_PLAN_ATTEMPTS):
        if attempt == 0:
            messages = [
                AIMessage(
                    role="system",
                    content=_planner_system_prompt(catalog, conversation_context),
                ),
                AIMessage(role="user", content=question),
            ]
        else:
            if not is_retryable_plan_error(error_code):
                break
            messages = repair_messages(
                catalog=catalog,
                question=question,
                previous_response=last_response,
                error_code=error_code,
                error_message=error_message,
            )

        try:
            response = ai_provider.complete(messages)
        except AIProviderNotConfiguredError:
            # Backend genuinely off → degrade gracefully to a clarify.
            return {"query_type": "clarify", "clarify_reason": "server AI is not configured"}
        except Exception as exc:
            # Backend IS configured but the request failed (e.g. a local endpoint
            # is unreachable). Fail loud and locatable instead of masking it as a
            # clarify — otherwise "local model drives everything" silently becomes
            # "drives nothing".
            return {
                "error": {
                    "code": "ai_backend_unreachable",
                    "message": str(exc) or "AI backend request failed",
                }
            }

        last_response = response.content
        parsed = _parse_json_object(response.content)
        if not isinstance(parsed, dict):
            error_code = "invalid_json"
            error_message = "model did not return a JSON object"
            continue

        model_intent = parsed

        if model_intent.get("intent") == "access":
            return {"query_type": "access", "model_intent": model_intent}
        if model_intent.get("intent") == "clarify":
            return {
                "query_type": "clarify",
                "model_intent": model_intent,
                "clarify_reason": "model could not map the question to authorized fields",
            }

        plan = query_plan_from_model_intent(model_intent, catalog)
        if plan is not None:
            validation = catalog.validate_plan(plan)
            if validation.ok:
                _record_step_safely(
                    database_path,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    step_index=attempt + 1,
                    planner_source="ai_retry" if attempt else "ai",
                    status="selected",
                    plan=plan,
                )
                return {
                    "query_type": "search",
                    "query_plan": plan,
                    "model_intent": model_intent,
                    "planner_source": "ai_retry" if attempt else "ai",
                }
            error_code = validation.error_code or "invalid_plan"
            error_message = validation.message or error_code
        else:
            error_code = "unsupported_domain"
            error_message = "model did not return a usable registered domain"

        _record_step_safely(
            database_path,
            conversation_id=conversation_id,
            turn_id=turn_id,
            step_index=attempt + 1,
            planner_source="ai_retry" if attempt else "ai",
            status="rejected",
            plan=plan if plan is not None else model_intent,
            error_code=error_code,
            error_message=error_message,
        )

        if not is_retryable_plan_error(error_code):
            break

    reason = error_message or "model did not return a usable plan"
    update: dict[str, Any] = {"query_type": "clarify", "clarify_reason": reason}
    if model_intent:
        update["model_intent"] = model_intent
    return update


def _record_step_safely(
    database_path: str | Path,
    *,
    conversation_id: str | None,
    turn_id: str | None,
    step_index: int,
    planner_source: str,
    status: str,
    reason: str | None = None,
    plan: QueryPlan | dict[str, Any] | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    if conversation_id is None or turn_id is None:
        return
    conn = db.connect(database_path)
    try:
        record_agent_step(
            conn,
            thread_id=conversation_id,
            turn_id=turn_id,
            step_index=step_index,
            planner_source=planner_source,
            status=status,
            reason=reason,
            plan=plan,
            error_code=error_code,
            error_message=error_message,
        )
        conn.commit()
    except sqlite3.IntegrityError:
        # A turn-keyed collision is now impossible under correct operation, so one
        # means a real keying bug — surface it (do NOT swallow it as before, which
        # made per-turn plan audit silently vanish exactly when needed; v0.4.6 §F).
        raise
    except sqlite3.Error:
        # Other telemetry failures (e.g. a locked/IO error) still fail closed in
        # production: a served query must not break because audit could not write.
        pass
    finally:
        conn.close()


def _catalog_for_database(
    database_path: str | Path, connector_setup: ConnectorSetup | None = None
) -> QueryCatalog:
    conn = db.connect(database_path)
    try:
        # Pivot 阶段3: the live query catalog is sourced from the read-through
        # connector, not hard-coded legal tables. Default = the SQLite demo
        # (proven equivalent to build_query_catalog in tests/test_connectors.py);
        # a configured connector (v0.3) supplies a mixed catalog instead, where
        # Feishu-served domains contribute their declared fields. Passing conn
        # keeps the live-column intersection identical for SQLite-backed domains.
        connector = (
            connector_setup.connector
            if connector_setup is not None
            else SqliteDemoConnector(database_path)
        )
        return build_query_catalog_from_connector(
            connector, conn, exclude_domains=_disconnected_domains(connector, conn)
        )
    finally:
        conn.close()


def _disconnected_domains(
    connector: "DataConnector", conn: sqlite3.Connection
) -> frozenset[str]:
    """Domains whose source an operator has disconnected (v0.4.0 §C C5).

    Maps each disabled source to the domains it serves via the connector's
    ``domain_sources()``; those drop out of the live catalog so queries against
    them fail closed. A connector without ``domain_sources`` (no composite
    routing) contributes nothing here.
    """
    disabled = db.disabled_data_sources(conn)
    if not disabled or not hasattr(connector, "domain_sources"):
        return frozenset()
    return frozenset(
        domain
        for domain, source in connector.domain_sources().items()
        if source in disabled
    )


def _planner_system_prompt(
    catalog: QueryCatalog, conversation_context: dict[str, Any] | None = None
) -> str:
    prompt = (
        "You are the Legal-MCP server-side query planner. Read the user question and the "
        "catalog, then return exactly one JSON object and nothing else.\n"
        "Return one of these shapes:\n"
        '  1. A data plan: {"domain": <a key of catalog.domains>, "operation": "search", '
        '"filters": [{"field": <field>, "operator": <one of supported_operators>, '
        '"value": <value>}], "return_fields": [<field>, ...], "limit": <int 1-100>}.\n'
        '  2. {"intent": "access"} if the user asks which projects or fields they can access.\n'
        '  3. {"intent": "clarify"} if the question is ambiguous or references a field that is '
        "not in the catalog.\n"
        "Rules: use only domains and canonical English field names found under catalog.domains. "
        "'filters' MUST be a JSON array of filter objects, never a bare mapping. Resolve any "
        "Chinese or alias name to its canonical field using field_aliases. A domain may also "
        "carry field_semantics describing what a field means, with example values and synonyms; "
        "use it to map a natural-language phrase to the right canonical field (it never grants "
        "access to a field — the server still enforces field authorization). Use domain "
        "'cross_domain' with a single {field:'q',operator:'contains'} filter for free-text "
        "searches that span domains. Never output SQL, table names, tool names, prose, or markdown.\n"
        "Identity rule: when the user names a project by a bare token and does NOT say whether it "
        "is a code or a name (e.g. 'MOON的法务BP是谁', '月之子', 'nova', '山海'), DO NOT guess a "
        "single identity field. Use one filter {\"field\": \"identity\", \"operator\": \"contains\", "
        "\"value\": <token>} — the server matches the token against every identity field and "
        "disambiguates. Only filter a specific identity field with 'eq' when the user explicitly "
        "says it is the 项目代号 (project_code) or 项目名称 (name).\n"
        "Example (bare token): question 'MOON的法务BP是谁' -> "
        '{"domain": "project", "operation": "search", '
        '"filters": [{"field": "identity", "operator": "contains", "value": "MOON"}], '
        '"return_fields": ["legal_bp"], "limit": 10}\n'
        "Example (explicit field): question '项目名称叫指间山海的发行团队是谁' -> "
        '{"domain": "project", "operation": "search", '
        '"filters": [{"field": "name", "operator": "eq", "value": "指间山海"}], '
        '"return_fields": ["release_team"], "limit": 1}\n'
        "Data source rule: a plan may carry an optional \"data_source\": <source name> to pin "
        "the query to one configured source. Only set it when the conversation context shows a "
        "pending_source_choice and the user names (or clearly picks) one of the listed sources — "
        "then re-emit that pending plan's domain and filters with the chosen \"data_source\". "
        "Never invent a source name.\n"
    )
    if conversation_context:
        # Conversation memory is INPUT, not inherited graph state (v0.4.6 §D). The
        # planner may use a listed identity to resolve an ellipsis like '它'/'这个
        # 项目', but it must still emit a fresh plan and only use identities present
        # here; if several entities fit, it should clarify rather than guess.
        prompt += (
            "Conversation context (prior safe entities from this same conversation; use only to "
            "resolve pronouns/ellipsis such as '它', '这个项目', '那它的', and never as a substitute "
            "for grounding the field in the catalog): "
            f"{json.dumps(conversation_context, ensure_ascii=False, sort_keys=True)}\n"
        )
    prompt += f"Catalog: {catalog_context_for_prompt(catalog)}"
    return prompt


def _parse_json_object(content: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        parsed = _extract_json_object(content)
    return parsed if isinstance(parsed, dict) else None


def _extract_json_object(content: str) -> dict[str, Any] | None:
    if not isinstance(content, str):
        return None
    start = content.find("{")
    end = content.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        parsed = json.loads(content[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def validate_plan(
    state: AgentState,
    database_path: str | Path,
    connector_setup: ConnectorSetup | None = None,
) -> dict[str, Any]:
    plan = state.get("query_plan")
    if plan is None or state.get("query_type") != "search":
        return {}
    catalog = _catalog_for_database(database_path, connector_setup)
    result = catalog.validate_plan(plan)
    if result.ok:
        return {}
    return {
        "error": {
            "code": result.error_code,
            "message": result.message,
            "details": {},
        }
    }


def authorize_plan(
    state: AgentState,
    database_path: str | Path,
    access_context: AccessContext | None,
    connector_setup: ConnectorSetup | None = None,
) -> dict[str, Any]:
    plan = state.get("query_plan")
    if plan is None or state.get("query_type") != "search" or state.get("error"):
        return {}
    catalog = _catalog_for_database(database_path, connector_setup)
    conn = db.connect(database_path)
    try:
        result = authorize_query_plan(conn, plan, access_context, catalog=catalog)
    finally:
        conn.close()
    if result.ok:
        return {}
    return {
        "error": {
            "code": result.error_code,
            "message": result.message,
            "details": {
                "denied_fields": sorted(
                    {
                        disclosure.field_name
                        for disclosure in result.disclosures
                        if disclosure.field_name is not None
                    }
                ),
                # Carry the per-field deny reason so the audit/caller can see WHY
                # (e.g. a DB-grant default-deny), not just which field.
                "reasons": {
                    disclosure.field_name: disclosure.reason
                    for disclosure in result.disclosures
                    if disclosure.field_name is not None
                },
            },
        }
    }


def execute_plan(
    state: AgentState,
    database_path: str | Path,
    audit_path: str | Path,
    access_context: AccessContext | None,
    connector_setup: ConnectorSetup | None = None,
) -> dict[str, Any]:
    if state.get("error"):
        return {}
    query_type = state.get("query_type")
    if query_type == "clarify":
        reason = state.get("clarify_reason") or "question needs a narrower retrieval scope"
        return {
            "tool_result": clarify_result(state["question"]),
            "tool_calls": [
                {
                    "tool_name": "clarify_query",
                    "reason": reason,
                    "status": "success",
                }
            ],
        }
    if query_type == "access":
        conn = db.connect(database_path)
        try:
            result = describe_my_access(
                conn,
                {"rationale": "agent_query: describe current access"},
                access_context,
            )
        finally:
            conn.close()
        return {
            "tool_result": result,
            "tool_calls": [
                {
                    "tool_name": "describe_my_access",
                    "reason": "question asks for current access scope",
                    "status": "error" if "error" in result else "success",
                }
            ],
        }

    plan = state["query_plan"]
    result = _dispatch_plan(plan, database_path, access_context, connector_setup)

    # v0.5.0: rescue the planner's residual single-identity-field ``eq`` false-empty.
    # When the planner guesses a lone identity column with ``eq`` (e.g. ``name eq
    # "MOON"`` for a project whose MOON is a *code*, or a case-mismatched name), an
    # authorized query returns zero rows although the entity exists. On an empty
    # result, rewrite that one filter to the virtual ``identity`` + ``contains``
    # filter — which ORs across every identity field, case-insensitively, with
    # precision ranking — and retry once. The rewrite only fires on a lone
    # identity-field ``eq``, so a deliberate non-identity equality is never
    # broadened; it is recorded so an operator can see the planner's plan was rescued.
    rewrite: dict[str, Any] | None = None
    if "error" not in result and _result_is_empty(result):
        identity_fields = _domain_identity_fields(plan.domain, database_path, connector_setup)
        rewritten = _identity_rewrite_plan(plan, identity_fields)
        if rewritten is not None:
            retry = _dispatch_plan(rewritten, database_path, access_context, connector_setup)
            if "error" not in retry and not _result_is_empty(retry):
                result = retry
                rewrite = {"reason": "eq_to_identity", "from_field": plan.filters[0].field}

    tool_name = f"{plan.domain}/{plan.operation}"
    is_error = "error" in result
    tool_call: dict[str, Any] = {
        "tool_name": tool_name,
        "reason": state.get("planner_reason") or "server-side retrieval plan",
        "plan": _plan_to_dict(plan),
        "status": "error" if is_error else "success",
    }
    if rewrite is not None:
        tool_call["rewrite"] = rewrite
    update: dict[str, Any] = {"tool_result": result, "tool_calls": [tool_call]}
    # v0.4.6 §G: an authorized-but-empty result is legitimate, but it must not look
    # like a denial or a planner failure in audit. Tag it so an operator can tell a
    # wrong filter (no_rows) apart from a denied field (error) — server-side only;
    # the diagnostic carries no field values and is stripped from the client view.
    if not is_error and _result_is_empty(result):
        diagnostic = {"reason": "no_rows", "matched_authorization": True}
        tool_call["diagnostic"] = diagnostic
        update["diagnostic"] = diagnostic
        # v0.5.4: turn the bare empty result into leak-free guidance. Built only
        # from catalog metadata + the user's own filters (the record scope and field
        # gate already ran, so this opens no new data path), it tells the user how to
        # rephrase instead of returning a dead "找不到".
        update["clarification"] = _no_rows_clarification(
            plan, database_path, connector_setup
        )
    return update


def _dispatch_plan(
    plan: QueryPlan,
    database_path: str | Path,
    access_context: AccessContext | None,
    connector_setup: ConnectorSetup | None,
) -> dict[str, Any]:
    """Run one plan against its domain's source(s) and return the result dict.

    Connector-served domains (e.g. Feishu) route through the multi-source fallback
    with the same DB-grant authorization applied in the gateway; everything else
    routes through the SQLite path. Each call owns its own short-lived connection so
    the v0.5.0 rewrite can re-dispatch without threading a connection through.
    """
    conn = db.connect(database_path)
    try:
        if connector_setup is not None and plan.domain in connector_setup.connector_domains:
            return _execute_connector_sources(
                connector_setup, plan, conn=conn, access_context=access_context
            )
        return execute_search_plan(conn, plan, access_context=access_context)
    finally:
        conn.close()


def _domain_identity_fields(
    domain: str,
    database_path: str | Path,
    connector_setup: ConnectorSetup | None,
) -> frozenset[str]:
    """The domain's declared identity fields, from the live catalog (v0.5.0).

    Empty for a domain that declares none, which disables the identity rewrite for
    that domain. Reuses ``_catalog_for_database`` so disabled sources are excluded
    uniformly, exactly as planning sees the catalog.
    """
    catalog = _catalog_for_database(database_path, connector_setup)
    domain_catalog = catalog.domains.get(domain)
    return frozenset(domain_catalog.identity_fields) if domain_catalog else frozenset()


def _no_rows_clarification(
    plan: QueryPlan,
    database_path: str | Path,
    connector_setup: ConnectorSetup | None,
) -> dict[str, Any]:
    """Structured, leak-free guidance for an authorized-but-empty result (v0.5.4).

    Built only from catalog *metadata* (the domain's filterable / identity fields)
    and the user's own filter inputs — never from fetched rows — so it cannot
    disclose an out-of-scope value. The record scope and field gate already ran
    (the result is empty *after* them); this adds no new data path. When the user
    pinned a specific identity field with ``eq``, it nudges the code-vs-name
    confusion that is the most common cause of a real-entity false-empty.
    """
    catalog = _catalog_for_database(database_path, connector_setup)
    domain_catalog = catalog.domains.get(plan.domain)
    identity_fields = sorted(domain_catalog.identity_fields) if domain_catalog else []
    available = sorted(domain_catalog.fields) if domain_catalog else []
    searched = [
        {"field": query_filter.field, "operator": query_filter.operator, "value": query_filter.value}
        for query_filter in plan.filters
    ]
    suggestions = ["没有找到匹配的记录。请确认筛选值是否正确，或换一种说法再试。"]
    declared_identity = domain_catalog.identity_fields if domain_catalog else set()
    used_identity_eq = any(
        query_filter.operator == "eq"
        and (query_filter.field in declared_identity or query_filter.field == VIRTUAL_IDENTITY_FIELD)
        for query_filter in plan.filters
    )
    if used_identity_eq and identity_fields:
        suggestions.append(
            "如果你用的是代号，试试名称（或反过来）；可用于识别的字段："
            + "、".join(identity_fields)
            + "。"
        )
    return {
        "reason": "no_rows",
        "domain": plan.domain,
        "searched": searched,
        "available_filters": available,
        "identity_fields": identity_fields,
        "suggestions": suggestions,
    }


def _identity_rewrite_plan(
    plan: QueryPlan, identity_fields: frozenset[str]
) -> QueryPlan | None:
    """A copy of ``plan`` with a lone identity-field ``eq`` rewritten to the virtual
    ``identity`` + ``contains`` filter, or ``None`` when the plan is not a single
    identity equality (v0.5.0).

    Restricting to a lone identity ``eq`` keeps the rescue surgical: a deliberate
    non-identity equality (``stage eq "dead"``) is never broadened, and a multi-
    filter plan is left untouched. A virtual ``identity`` ``eq`` is also rewritten,
    so a case-sensitive identity equality is rescued the same way.
    """
    if not identity_fields or len(plan.filters) != 1:
        return None
    only = plan.filters[0]
    if only.operator != "eq":
        return None
    if only.field not in identity_fields and only.field != VIRTUAL_IDENTITY_FIELD:
        return None
    if only.value is None or not str(only.value).strip():
        return None
    return QueryPlan(
        domain=plan.domain,
        operation=plan.operation,
        filters=[
            QueryFilter(field=VIRTUAL_IDENTITY_FIELD, operator="contains", value=only.value)
        ],
        return_fields=list(plan.return_fields),
        limit=plan.limit,
        data_source=plan.data_source,
    )


def _execute_connector_sources(
    connector_setup: ConnectorSetup,
    plan: QueryPlan,
    *,
    conn: sqlite3.Connection,
    access_context: AccessContext | None,
) -> dict[str, Any]:
    """Run a plan against a domain's configured sources, primary first (v0.4.9).

    * one configured source → exactly the pre-v0.4.9 behavior;
    * primary has rows (or errors) → its result stands, fallbacks are not queried;
    * primary is empty → query the fallbacks in declared order. Exactly one
      fallback with rows answers (tagged with ``data_source``); several with rows
      return a ``source_disambiguation`` so the agent asks the user which source
      to use; an errored fallback is skipped (the empty primary result stands).
    * ``plan.data_source`` pins the query to that named source only; an unknown
      name fails closed.

    Every per-source query runs through ``execute_connector_plan`` unchanged, so
    the field gate and record scope apply to each source individually — a
    fallback can never disclose more than it would as a primary.
    """
    sources = list(connector_setup.sources_for(plan.domain))
    disabled = db.disabled_data_sources(conn)
    sources = [source for source in sources if source.name not in disabled]
    if plan.data_source is not None:
        sources = [source for source in sources if source.name == plan.data_source]
        if not sources:
            return {
                "error": {
                    "code": "unknown_data_source",
                    "message": f"no configured data source named {plan.data_source!r} "
                    f"serves domain {plan.domain!r}",
                }
            }
    if not sources:
        return {
            "error": {
                "code": "unsupported_domain",
                "message": "query domain has no enabled data source",
            }
        }

    multi_source = len(connector_setup.sources_for(plan.domain)) > 1
    primary, *fallbacks = sources
    result = execute_connector_plan(primary, plan, conn=conn, access_context=access_context)
    if "error" in result or not _result_is_empty(result) or not fallbacks:
        if multi_source and "error" not in result:
            result["data_source"] = primary.name
        return result

    hits: list[tuple[str, dict[str, Any]]] = []
    for source in fallbacks:
        fallback_result = execute_connector_plan(
            source, plan, conn=conn, access_context=access_context
        )
        if "error" not in fallback_result and not _result_is_empty(fallback_result):
            hits.append((source.name, fallback_result))

    if not hits:
        result["data_source"] = primary.name
        return result
    if len(hits) == 1:
        name, hit = hits[0]
        hit["data_source"] = name
        return hit
    # Several sources answer: do not merge or pick silently — surface the choice.
    # Per-source counts only; the rows themselves stay unreturned until the user
    # picks a source, so the answer never mixes provenance.
    return {
        result_key_for_domain(plan.domain): [],
        "source_disambiguation": {
            "sources": [
                {
                    "source": name,
                    "record_count": sum(
                        len(value) for value in hit.values() if isinstance(value, list)
                    ),
                }
                for name, hit in hits
            ],
        },
    }


def _result_is_empty(result: dict[str, Any]) -> bool:
    """True if a successful search result carries no rows under any list key."""
    if "source_disambiguation" in result:
        return False  # a pending source choice is an answer, not a no-rows turn
    row_lists = [value for value in result.values() if isinstance(value, list)]
    if not row_lists:
        return False
    return all(len(value) == 0 for value in row_lists)


def format_answer(state: AgentState) -> dict[str, Any]:
    if state.get("error"):
        message = state["error"].get("message") or "agent query failed"
        return {"answer": message}
    result = state.get("tool_result") or {}
    if "error" in result:
        return {"error": result["error"], "answer": result["error"]["message"]}
    # v0.5.4: an authorized-but-empty result is rendered as leak-free guidance
    # instead of a bare "{}", so the user can rephrase. The clarification was built
    # from metadata + their own filters only.
    clarification = state.get("clarification")
    if clarification:
        return {"answer": _format_no_rows(clarification)}
    # An ambiguous identity match returned a "did you mean" candidate list. The
    # candidates are already record-scoped and field-gated (and only identity ∪
    # granted return fields), so listing them discloses nothing new (v0.5.4).
    if isinstance(result.get("identity_disambiguation"), dict):
        rendered = _format_identity_candidates(result)
        if rendered is not None:
            return {"answer": rendered}
    if isinstance(result.get("seals"), list):
        return {"answer": _markdown_table(result["seals"], _SEAL_TABLE_COLUMNS)}
    return {"answer": json.dumps(result, ensure_ascii=False, sort_keys=True)}


def _format_no_rows(clarification: dict[str, Any]) -> str:
    """Human-readable guidance for an empty result (v0.5.4). Values shown are the
    user's own search terms and field-name metadata only."""
    lines = list(clarification.get("suggestions") or ["没有找到匹配的记录。"])
    searched = clarification.get("searched") or []
    if searched:
        parts = [
            f"{item['field']} {item['operator']} {item['value']}"
            for item in searched
            if isinstance(item, dict)
        ]
        if parts:
            lines.append("你的查询条件：" + "；".join(parts) + "。")
    available = clarification.get("available_filters") or []
    if available:
        lines.append("可以筛选的字段：" + "、".join(available) + "。")
    return "\n".join(lines)


def _format_identity_candidates(result: dict[str, Any]) -> str | None:
    """Render the ambiguous identity candidate list as a "did you mean" prompt."""
    disambiguation = result["identity_disambiguation"]
    candidates = next(
        (value for key, value in result.items()
         if key != "identity_disambiguation" and isinstance(value, list)),
        None,
    )
    if not candidates:
        return None
    token = disambiguation.get("token", "")
    lines = [f"“{token}”匹配到多个，请确认是哪一个："]
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        pairs = "，".join(f"{key}={value}" for key, value in candidate.items())
        lines.append(f"- {pairs}")
    return "\n".join(lines)


_SEAL_TABLE_COLUMNS = (
    ("company", "公司"),
    ("seal_type", "印章类型"),
    ("status", "现在状态"),
    ("storage_location", "保管地点"),
    ("borrower", "外借人"),
    ("borrowed_at", "外借时间"),
    ("borrow_reason", "外借原因"),
    ("expected_return_at", "预计归还时间"),
    ("actual_return_at", "实际归还时间"),
)


def _markdown_table(
    rows: list[dict[str, Any]],
    columns: tuple[tuple[str, str], ...],
) -> str:
    headers = [label for _, label in columns]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        values = [_markdown_cell(row.get(field, "")) for field, _ in columns]
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def _markdown_cell(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ").strip()


def record_context(
    state: AgentState,
    database_path: str | Path,
    connector_setup: ConnectorSetup | None = None,
) -> dict[str, Any]:
    """Persist safe entity context from a successful, non-empty search (v0.4.6 §D).

    A failed, denied, or empty turn writes nothing, so it cannot be promoted as the
    next turn's entity. Best-effort: never fail a served query over context memory.
    """
    if state.get("query_type") != "search" or state.get("error"):
        return {}
    plan = state.get("query_plan")
    result = state.get("tool_result")
    conversation_id = state.get("conversation_id")
    turn_id = state.get("turn_id")
    if plan is None or not isinstance(result, dict) or not conversation_id or not turn_id:
        return {}
    catalog = _catalog_for_database(database_path, connector_setup)
    conn = db.connect(database_path)
    try:
        record_turn_context(
            conn,
            conversation_id=conversation_id,
            turn_id=turn_id,
            plan=plan,
            result=result,
            catalog=catalog,
        )
        conn.commit()
    except sqlite3.Error:
        pass
    finally:
        conn.close()
    return {}


def _checkpoint_state(state: AgentState) -> dict[str, Any]:
    serializable = dict(state)
    if isinstance(serializable.get("query_plan"), QueryPlan):
        serializable["query_plan"] = _plan_to_dict(serializable["query_plan"])
    return serializable


def _plan_to_dict(plan: QueryPlan) -> dict[str, Any]:
    return asdict(plan)


def _query_plan_from_payload(payload: dict[str, Any]) -> QueryPlan:
    filters = payload.get("filters", [])
    return QueryPlan(
        domain=str(payload.get("domain", "")),
        operation=str(payload.get("operation", "")),
        filters=[
            QueryFilter(
                field=str(item.get("field", "")),
                operator=str(item.get("operator", "")),
                value=item.get("value"),
            )
            for item in filters
            if isinstance(item, dict)
        ],
        return_fields=[
            str(field)
            for field in payload.get("return_fields", [])
            if isinstance(field, str)
        ],
        limit=payload.get("limit", 20),
    )


def _record_agent_run(
    database_path: str | Path,
    thread_id: str,
    question: str,
    result: dict[str, Any],
) -> None:
    tool_calls = result.get("tool_calls") or []
    selected_tool = None
    first_call: dict[str, Any] = {}
    if tool_calls and isinstance(tool_calls[0], dict):
        first_call = tool_calls[0]
        selected_tool = first_call.get("tool_name")
    error = result.get("error")
    error_code = error.get("code") if isinstance(error, dict) else None
    # A clarify is recorded as success; capture WHY in error_code so operators
    # can diagnose why a question did not retrieve data (reuses existing column).
    if error_code is None and selected_tool == "clarify_query":
        reason = first_call.get("reason")
        if isinstance(reason, str) and reason:
            error_code = f"clarify:{reason}"[:200]
    # v0.5.0: a lone identity-field `eq` that returned empty was rescued by the
    # `identity` + `contains` rewrite. The run succeeded (rows present), so record
    # the rescue in error_code — like the clarify/diagnostic markers — so an operator
    # can see the planner's lone-`eq` guess needed rescuing. Carries no field value.
    if error_code is None and isinstance(first_call.get("rewrite"), dict):
        reason = first_call["rewrite"].get("reason")
        if isinstance(reason, str) and reason:
            error_code = f"rewrite:{reason}"[:200]
    # v0.4.6 §G: a successful but empty search records a no_rows marker so the run
    # log distinguishes "wrong filter, zero rows" from a normal hit, without a
    # field value ever entering the audit.
    if error_code is None and isinstance(first_call.get("diagnostic"), dict):
        reason = first_call["diagnostic"].get("reason")
        if isinstance(reason, str) and reason:
            error_code = f"diagnostic:{reason}"[:200]
    conn = db.connect(database_path)
    try:
        conn.execute(
            """
            insert into agent_runs (
              thread_id,
              question_summary,
              status,
              selected_tool,
              error_code
            )
            values (?, ?, ?, ?, ?)
            """,
            (
                thread_id,
                question[:500],
                result.get("status", "error"),
                selected_tool,
                error_code,
            ),
        )
        conn.commit()
    finally:
        conn.close()
