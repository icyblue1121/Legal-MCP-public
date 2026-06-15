"""Database-backed audit logging for tool result disclosures."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from legal_mcp.audit import summarize_arguments
from legal_mcp.policy import AccessContext

# Per-field cap for stored audit payloads. Keeps large result sets from
# bloating the database while still capturing the question/answer for the
# detail view; oversize payloads are truncated and flagged.
_MAX_PAYLOAD_CHARS = 64 * 1024


@dataclass(frozen=True)
class Disclosure:
    project_id: int | None
    record_type: str
    record_id: int | None
    decision: str
    reason: str
    field_name: str | None = None
    group_id: int | None = None


def write_audit_event(
    conn: sqlite3.Connection,
    context: AccessContext | None,
    tool_name: str,
    rationale: str | None,
    source_client: str | None,
    arguments: dict[str, Any],
    result: dict[str, Any],
    disclosures: list[Disclosure],
) -> int:
    """Persist a tool audit event and its disclosure decisions."""
    error = result.get("error")
    result_status = "error" if error else "success"
    error_code = error.get("code") if isinstance(error, dict) else None
    user_id = context.user_id if context is not None else None
    api_key_id = context.api_key_id if context is not None else None
    identity_source = context.identity_source if context is not None else None

    cursor = conn.execute(
        """
        insert into audit_events (
          user_id,
          api_key_id,
          identity_source,
          source_client,
          tool_name,
          rationale,
          arguments_summary,
          result_status,
          error_code,
          response_record_count
        )
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            api_key_id,
            identity_source,
            source_client,
            tool_name,
            rationale,
            summarize_arguments(arguments),
            result_status,
            error_code,
            _count_records(result),
        ),
    )
    audit_event_id = int(cursor.lastrowid)

    conn.executemany(
        """
        insert into audit_disclosures (
          audit_event_id,
          project_id,
          record_type,
          record_id,
          field_name,
          group_id,
          decision,
          reason
        )
        values (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                audit_event_id,
                disclosure.project_id,
                disclosure.record_type,
                disclosure.record_id,
                disclosure.field_name,
                disclosure.group_id,
                disclosure.decision,
                disclosure.reason,
            )
            for disclosure in disclosures
        ],
    )

    arguments_json, args_truncated = _dump_capped(arguments)
    response_json, resp_truncated = _dump_capped(result)
    conn.execute(
        """
        insert into audit_event_details (
          audit_event_id, arguments_json, response_json, truncated
        )
        values (?, ?, ?, ?)
        """,
        (
            audit_event_id,
            arguments_json,
            response_json,
            1 if (args_truncated or resp_truncated) else 0,
        ),
    )
    conn.commit()
    return audit_event_id


def _dump_capped(obj: Any) -> tuple[str, bool]:
    """JSON-encode ``obj``, truncating to the payload cap. Returns (text, truncated)."""
    text = json.dumps(obj, ensure_ascii=False, sort_keys=True)
    if len(text) > _MAX_PAYLOAD_CHARS:
        return text[:_MAX_PAYLOAD_CHARS], True
    return text, False


def get_audit_event_detail(
    conn: sqlite3.Connection, audit_event_id: int
) -> dict[str, Any] | None:
    """Return the stored full payload for an audit event, or ``None`` if absent."""
    row = conn.execute(
        """
        select audit_event_id, arguments_json, response_json, truncated, created_at
        from audit_event_details
        where audit_event_id = ?
        """,
        (audit_event_id,),
    ).fetchone()
    return dict(row) if row is not None else None


def _audit_filters(
    user_id: int | None,
    project_id: int | None,
    tool_name: str | None,
) -> tuple[str, list[Any]]:
    """Build the shared WHERE clause and params for audit-event queries."""
    filters: list[str] = []
    params: list[Any] = []

    if user_id is not None:
        filters.append("audit_events.user_id = ?")
        params.append(user_id)
    if project_id is not None:
        filters.append(
            """
            exists (
              select 1
              from audit_disclosures
              where audit_disclosures.audit_event_id = audit_events.id
                and audit_disclosures.project_id = ?
            )
            """
        )
        params.append(project_id)
    if tool_name is not None:
        filters.append("audit_events.tool_name = ?")
        params.append(tool_name)

    where_clause = f"where {' and '.join(filters)}" if filters else ""
    return where_clause, params


def list_audit_events(
    conn: sqlite3.Connection,
    user_id: int | None = None,
    project_id: int | None = None,
    tool_name: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List persisted audit events with optional filters and pagination."""
    where_clause, params = _audit_filters(user_id, project_id, tool_name)
    normalized_limit = _normalize_limit(limit)
    normalized_offset = max(int(offset), 0)
    rows = conn.execute(
        f"""
        select
          audit_events.*,
          users.email as email
        from audit_events
        left join users on users.id = audit_events.user_id
        {where_clause}
        order by audit_events.timestamp desc, audit_events.id desc
        limit ? offset ?
        """,
        (*params, normalized_limit, normalized_offset),
    ).fetchall()
    return [dict(row) for row in rows]


def count_audit_events(
    conn: sqlite3.Connection,
    user_id: int | None = None,
    project_id: int | None = None,
    tool_name: str | None = None,
) -> int:
    """Count persisted audit events matching the same filters as listing."""
    where_clause, params = _audit_filters(user_id, project_id, tool_name)
    row = conn.execute(
        f"select count(*) as n from audit_events {where_clause}",
        (*params,),
    ).fetchone()
    return int(row["n"]) if row is not None else 0


def _count_records(result: dict[str, Any]) -> int:
    if result.get("error"):
        return 0

    count = 0
    for value in result.values():
        if isinstance(value, list):
            count += len(value)
        elif isinstance(value, dict):
            count += 1
    return count


def _normalize_limit(limit: int) -> int:
    return min(max(int(limit), 1), 500)
