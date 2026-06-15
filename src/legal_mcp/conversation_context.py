"""Safe, turn-scoped conversation memory for multi-turn agent queries (v0.4.6 §D).

A follow-up like ``它的官网呢`` ("and its website?") needs the entity the previous
turn resolved. The dangerous way to provide it is to let a prior LangGraph plan or
result survive as mutable graph state — that is exactly the stale-plan bug v0.4.6
fixes. Instead this module stores a *narrow, safe* context object and feeds it to
the planner prompt as **input**, never as inherited `query_plan`/`tool_result`.

What counts as safe to remember:

* entity identities the requester already supplied (an ``eq`` filter on an identity
  field — they asked about it, so they know it) or identity values that came back in
  an authorized result row;
* the field names the turn returned (``recent_fields``) and the executed tool.

It deliberately stores no unauthorized field values and no raw connector rows, and
it is only written for a turn that actually *succeeded with rows* — a failed or
empty turn must not become the next turn's entity (so a guess is never promoted).
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from legal_mcp.query_catalog import QueryCatalog
from legal_mcp.query_plan import QueryPlan

# Result keys carrying row lists, mirroring search_tools / connector_retrieval.
_ROW_LIST_KEYS = ("projects", "contracts", "licenses")


def load_conversation_context(
    conn: sqlite3.Connection, conversation_id: str
) -> dict[str, Any]:
    """The most recent safe context for a conversation, or ``{}`` if none."""
    try:
        row = conn.execute(
            """
            select safe_context_json
            from agent_turn_context
            where conversation_id = ?
            order by id desc
            limit 1
            """,
            (conversation_id,),
        ).fetchone()
    except sqlite3.Error:
        return {}
    if row is None:
        return {}
    try:
        parsed = json.loads(row["safe_context_json"])
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def record_turn_context(
    conn: sqlite3.Connection,
    *,
    conversation_id: str,
    turn_id: str,
    plan: QueryPlan,
    result: dict[str, Any],
    catalog: QueryCatalog,
) -> None:
    """Persist safe context from a successful, non-empty search turn.

    No-op when the result errored, returned no rows, or carries no entity
    identity — an unresolved turn must not be promoted as conversation memory.
    """
    if not isinstance(result, dict) or "error" in result:
        return
    disambiguation = result.get("source_disambiguation")
    if isinstance(disambiguation, dict):
        # A pending source choice (v0.4.9): remember the plan and the offered
        # source names so the next turn ("用飞书那个") can re-emit the same plan
        # with a pinned data_source. Filters were supplied by the requester and
        # source names are deployment config — both safe to echo.
        _write_context(
            conn,
            conversation_id=conversation_id,
            turn_id=turn_id,
            context={
                "pending_source_choice": {
                    "domain": plan.domain,
                    "filters": [
                        {"field": f.field, "operator": f.operator, "value": f.value}
                        for f in plan.filters
                    ],
                    "return_fields": list(plan.return_fields),
                    "sources": [
                        str(entry.get("source"))
                        for entry in disambiguation.get("sources", [])
                        if isinstance(entry, dict) and entry.get("source")
                    ],
                }
            },
        )
        return
    rows = _first_row_list(result)
    if not rows:
        return
    entity = _safe_entity(plan, rows[0], catalog)
    if entity is None:
        return
    context = {
        "recent_entities": [entity],
        "recent_fields": list(plan.return_fields),
        "last_successful_tool": f"{plan.domain}/{plan.operation}",
    }
    if isinstance(result.get("data_source"), str):
        context["data_source"] = result["data_source"]
    _write_context(
        conn, conversation_id=conversation_id, turn_id=turn_id, context=context
    )


def _write_context(
    conn: sqlite3.Connection,
    *,
    conversation_id: str,
    turn_id: str,
    context: dict[str, Any],
) -> None:
    conn.execute(
        """
        insert into agent_turn_context (conversation_id, turn_id, safe_context_json)
        values (?, ?, ?)
        on conflict(conversation_id, turn_id) do update set
          safe_context_json = excluded.safe_context_json
        """,
        (conversation_id, turn_id, json.dumps(context, ensure_ascii=False, sort_keys=True)),
    )


def _first_row_list(result: dict[str, Any]) -> list[dict[str, Any]]:
    for key, value in result.items():
        if isinstance(value, list) and value and all(isinstance(item, dict) for item in value):
            return value
    return []


def _safe_entity(
    plan: QueryPlan, row: dict[str, Any], catalog: QueryCatalog
) -> dict[str, Any] | None:
    domain_catalog = catalog.domains.get(plan.domain)
    if domain_catalog is None:
        return None
    identity_fields = domain_catalog.identity_fields
    if not identity_fields:
        return None  # e.g. cross_domain — no single entity to carry forward

    identity: dict[str, Any] = {}
    # 1) eq-filter values on identity fields: the requester supplied these, so they
    #    are already known to the requester and safe to echo back as context.
    for query_filter in plan.filters:
        if (
            query_filter.field in identity_fields
            and query_filter.operator == "eq"
            and isinstance(query_filter.value, (str, int, float))
        ):
            identity[query_filter.field] = query_filter.value
    # 2) identity values that came back in the authorized result row.
    for field in identity_fields:
        value = row.get(field)
        if value not in (None, ""):
            identity.setdefault(field, value)

    if not identity:
        return None
    return {"domain": plan.domain, "identity": identity}
