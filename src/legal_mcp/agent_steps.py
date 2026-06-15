"""Persistence helpers for agent planning step telemetry."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from typing import Any

from legal_mcp.query_plan import QueryPlan


def record_agent_step(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    turn_id: str,
    step_index: int,
    planner_source: str,
    status: str,
    model: str | None = None,
    reason: str | None = None,
    plan: QueryPlan | dict[str, Any] | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    conn.execute(
        """
        insert into agent_steps (
          thread_id,
          turn_id,
          step_index,
          planner_source,
          status,
          model,
          reason,
          plan_json,
          error_code,
          error_message
        )
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            thread_id,
            turn_id,
            step_index,
            planner_source,
            status,
            model,
            reason,
            _plan_json(plan),
            error_code,
            error_message,
        ),
    )


def list_agent_steps(
    conn: sqlite3.Connection,
    thread_id: str,
    *,
    turn_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """List planner steps for a conversation (``thread_id``).

    Without ``turn_id`` this returns every turn's steps ordered by turn then
    attempt; with ``turn_id`` it narrows to a single agent_query invocation. The
    turn-then-step ordering keeps each turn's attempts contiguous (v0.4.6 §F).
    """
    capped = max(1, min(int(limit), 500))
    if turn_id is None:
        rows = conn.execute(
            """
            select *
            from agent_steps
            where thread_id = ?
            order by turn_id, step_index, id
            limit ?
            """,
            (thread_id, capped),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            select *
            from agent_steps
            where thread_id = ? and turn_id = ?
            order by step_index, id
            limit ?
            """,
            (thread_id, turn_id, capped),
        ).fetchall()
    return [dict(row) for row in rows]


def _plan_json(plan: QueryPlan | dict[str, Any] | None) -> str | None:
    if plan is None:
        return None
    payload = asdict(plan) if isinstance(plan, QueryPlan) else plan
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)
