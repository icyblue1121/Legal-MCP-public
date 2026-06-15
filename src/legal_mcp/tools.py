"""MCP tool definitions and execution."""

from __future__ import annotations

import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from legal_mcp import db
from legal_mcp.audit import DEFAULT_AUDIT_PATH, write_audit_record
from legal_mcp.connector_config import ConnectorSetup
from legal_mcp.disclosure_audit import Disclosure, write_audit_event
from legal_mcp.lookup import ProjectLookupResult, lookup_project
from legal_mcp.planner import asks_for_access_scope, plan_query
from legal_mcp.policy import (
    AccessContext,
    can_query_content,
    project_is_visible,
    visible_project_ids,
)
from legal_mcp.tools_access import build_access_summary, describe_my_access
from legal_mcp.tools_contract import get_contract_fields, list_project_contracts
from legal_mcp.tools_license import list_project_licenses
from legal_mcp.tools_project import get_project_fields, resolve_project


def call_tool(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    database_path: str | Path,
    audit_path: str | Path = DEFAULT_AUDIT_PATH,
    access_context: AccessContext | None = None,
    connector_setup: ConnectorSetup | None = None,
) -> dict[str, Any]:
    rationale = arguments.get("rationale")
    source_client = arguments.get("source_client")
    if not isinstance(rationale, str) or not rationale.strip():
        result = _error("missing_rationale", "rationale is required")
        _audit(tool_name, rationale, source_client, arguments, result, audit_path)
        _audit_database(
            database_path,
            access_context,
            tool_name,
            rationale,
            source_client,
            arguments,
            result,
            [],
        )
        return result

    if tool_name != "describe_my_access" and not can_query_content(access_context):
        result = _error("access_denied", "user is not allowed to query project content")
        _audit(tool_name, rationale, source_client, arguments, result, audit_path)
        _audit_database(
            database_path,
            access_context,
            tool_name,
            rationale,
            source_client,
            arguments,
            result,
            [],
        )
        return result

    disclosures: list[Disclosure] = []
    try:
        conn = db.connect(database_path)
        try:
            if tool_name == "list_projects":
                result = _list_projects(conn, arguments, access_context)
            elif tool_name == "describe_my_access":
                result = describe_my_access(conn, arguments, access_context)
            elif tool_name == "plan_query":
                question = arguments.get("question")
                if not isinstance(question, str) or not question.strip():
                    result = _error("validation_error", "question is required")
                else:
                    plan = plan_query(question)
                    result = {
                        "plan": {
                            "tool_name": plan.tool_name,
                            "arguments": plan.arguments,
                            "reason": plan.reason,
                        }
                    }
            elif tool_name == "resolve_project":
                query = arguments.get("query")
                if isinstance(query, str) and asks_for_access_scope(query):
                    result = describe_my_access(conn, arguments, access_context)
                else:
                    result = resolve_project(conn, arguments, access_context)
            elif tool_name == "get_project_fields":
                result = get_project_fields(conn, arguments, access_context)
                _append_project_field_disclosures(conn, arguments, result, disclosures)
                _append_project_denied_field_disclosures(conn, arguments, result, disclosures)
            elif tool_name == "get_contract_fields":
                result = get_contract_fields(conn, arguments, access_context)
                _append_contract_field_disclosures(conn, arguments, result, disclosures)
                _append_contract_denied_field_disclosures(conn, arguments, result, disclosures)
            elif tool_name == "list_project_contracts":
                result = list_project_contracts(conn, arguments, access_context)
                _append_project_child_denied_field_disclosures(
                    conn,
                    arguments,
                    result,
                    disclosures,
                    record_type="contract",
                )
            elif tool_name == "list_project_licenses":
                result = list_project_licenses(conn, arguments, access_context)
                _append_project_child_denied_field_disclosures(
                    conn,
                    arguments,
                    result,
                    disclosures,
                    record_type="license",
                )
            elif tool_name == "agent_query":
                question = arguments.get("question")
                if not isinstance(question, str) or not question.strip():
                    result = _error("validation_error", "question is required")
                else:
                    from legal_mcp.agent_graph import run_agent_query
                    from legal_mcp.ai_provider import ConfiguredAIProvider

                    thread_id = arguments.get("thread_id")
                    graph_result = run_agent_query(
                        question=question,
                        database_path=database_path,
                        audit_path=audit_path,
                        access_context=access_context,
                        thread_id=thread_id if isinstance(thread_id, str) else None,
                        ai_provider=ConfiguredAIProvider(database_path),
                        connector_setup=connector_setup,
                    )
                    _append_graph_result_disclosures(conn, graph_result, disclosures)
                    result = _client_safe_agent_result(graph_result)
            elif tool_name == "structured_query":
                query = arguments.get("query")
                if not isinstance(query, dict):
                    result = _error("validation_error", "query is required")
                else:
                    from legal_mcp.agent_graph import run_structured_query

                    result = run_structured_query(
                        query=query,
                        database_path=database_path,
                        audit_path=audit_path,
                        access_context=access_context,
                        connector_setup=connector_setup,
                    )
                    _append_graph_result_disclosures(conn, result, disclosures)
            elif tool_name == "agent_write":
                instruction = arguments.get("instruction")
                if not isinstance(instruction, str) or not instruction.strip():
                    result = _error("validation_error", "instruction is required")
                else:
                    result = _agent_write_proposal(instruction)
            elif tool_name == "get_project_context":
                result = _error(
                    "deprecated_tool",
                    "get_project_context is deprecated; use fine-grained field tools",
                )
            elif tool_name == "list_expiring_licenses":
                result = _list_expiring_licenses(conn, arguments, access_context)
            elif tool_name == "list_open_risks":
                result = _list_open_risks(conn, arguments, access_context, disclosures)
            else:
                result = _error("validation_error", f"unknown tool: {tool_name}")
            _append_access_summary_to_project_not_found(conn, result, access_context)
        finally:
            conn.close()
    except sqlite3.Error as exc:
        result = _error("database_error", "database operation failed", details={"reason": str(exc)})

    disclosures.extend(_disclosures_from_result(result))
    _audit(tool_name, rationale, source_client, arguments, result, audit_path)
    _audit_database(
        database_path,
        access_context,
        tool_name,
        rationale,
        source_client,
        arguments,
        result,
        disclosures,
    )
    return result


def _client_safe_agent_result(result: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {
        "answer": result.get("answer", ""),
        "thread_id": result.get("thread_id"),
        "tool_calls": [
            {
                "tool_name": tool_call.get("tool_name"),
                "reason": tool_call.get("reason"),
                "status": tool_call.get("status"),
            }
            for tool_call in result.get("tool_calls", [])
            if isinstance(tool_call, dict)
        ],
        "status": result.get("status", "success"),
    }
    if "error" in result:
        safe["error"] = result["error"]
    # v0.5.4: structured, leak-free no_rows guidance, so a client can drive its own
    # clarification UX alongside the rendered answer. Built from metadata + the
    # user's own filters only — safe to expose.
    if "clarification" in result:
        safe["clarification"] = result["clarification"]
    return safe


def _agent_write_proposal(instruction: str) -> dict[str, Any]:
    return {
        "proposal": {
            "requires_approval": True,
            "instruction": instruction,
            "diff": {
                "summary": "待人工审核的资料变更建议；v1.4.1 不直接写入 SQLite。",
                "operations": [],
            },
        }
    }


def _append_access_summary_to_project_not_found(
    conn: sqlite3.Connection,
    result: dict[str, Any],
    access_context: AccessContext | None,
) -> None:
    error = result.get("error")
    if not isinstance(error, dict):
        return
    if error.get("code") != "not_found" or error.get("message") != "project not found":
        return
    error["message"] = (
        "未找到项目。当前用户可见项目和字段已附在 details.access；"
        "如果目标项目不在列表中，可能是权限未开通，也可能是资料库尚未收录。"
    )
    details = error.setdefault("details", {})
    if isinstance(details, dict):
            details["access"] = build_access_summary(conn, access_context)


def _append_graph_result_disclosures(
    conn: sqlite3.Connection,
    result: dict[str, Any],
    disclosures: list[Disclosure],
) -> None:
    graph_result = result.get("result")
    if not isinstance(graph_result, dict):
        return
    for project in graph_result.get("projects", []):
        if not isinstance(project, dict):
            continue
        project_code = project.get("project_code")
        if not isinstance(project_code, str):
            continue
        row = conn.execute(
            "select id from projects where project_code = ?",
            (project_code,),
        ).fetchone()
        if row is None:
            continue
        project_id = int(row["id"])
        base = Disclosure(
            project_id=project_id,
            record_type="project",
            record_id=project_id,
            decision="allowed",
            reason="project_visible",
        )
        disclosures.append(base)
        disclosures.extend(_field_disclosures(base, project, {"project_code", "name"}))


def _list_projects(
    conn: sqlite3.Connection,
    arguments: dict[str, Any],
    access_context: AccessContext | None,
) -> dict[str, Any]:
    stage = arguments.get("stage")
    visible = visible_project_ids(conn, access_context)
    if visible == set():
        return {"projects": []}

    filters: list[str] = []
    params: list[Any] = []
    if stage:
        filters.append("stage = ?")
        params.append(stage)
    if visible is not None:
        placeholders = ", ".join("?" for _ in visible)
        filters.append(f"id in ({placeholders})")
        params.extend(sorted(visible))

    where = f" where {' and '.join(filters)}" if filters else ""
    rows = conn.execute(
        f"""
        select
          id,
          project_code,
          name,
          stage,
          legal_bp,
          department,
          release_team,
          contact_person,
          website,
          notes
        from projects{where}
        order by project_code
        """,
        params,
    ).fetchall()
    return {"projects": [dict(row) for row in rows]}


def _list_expiring_licenses(
    conn: sqlite3.Connection,
    arguments: dict[str, Any],
    access_context: AccessContext | None,
) -> dict[str, Any]:
    days_ahead = arguments.get("days_ahead", 30)
    if not isinstance(days_ahead, int) or days_ahead < 0:
        return _error("validation_error", "days_ahead must be a non-negative integer")
    visible = visible_project_ids(conn, access_context)
    if visible == set():
        return {"licenses": []}

    start = date.today().isoformat()
    end = (date.today() + timedelta(days=days_ahead)).isoformat()
    project_filter = ""
    params: list[Any] = [start, end]
    if visible is not None:
        placeholders = ", ".join("?" for _ in visible)
        project_filter = f" and projects.id in ({placeholders})"
        params.extend(sorted(visible))

    rows = conn.execute(
        f"""
        select licenses.*, projects.project_code, projects.name as project_name
        from licenses
        join projects on projects.id = licenses.project_id
        where licenses.expiry_date is not null
          and licenses.expiry_date >= ?
          and licenses.expiry_date <= ?
          {project_filter}
        order by licenses.expiry_date, projects.project_code, licenses.external_key
        """,
        params,
    ).fetchall()
    return {"licenses": [dict(row) for row in rows]}


def _list_open_risks(
    conn: sqlite3.Connection,
    arguments: dict[str, Any],
    access_context: AccessContext | None,
    disclosures: list[Disclosure],
) -> dict[str, Any]:
    project_code = arguments.get("project_code")
    visible = visible_project_ids(conn, access_context)
    if isinstance(project_code, str) and project_code.strip() and visible is not None:
        project = conn.execute(
            "select id from projects where project_code = ?",
            (project_code,),
        ).fetchone()
        if project is not None and int(project["id"]) not in visible:
            disclosures.append(
                Disclosure(
                    project_id=int(project["id"]),
                    record_type="project",
                    record_id=int(project["id"]),
                    decision="denied",
                    reason="project_hidden",
                )
            )
            return _error(
                "access_denied",
                "权限不足，当前用户没有访问该项目的权限。请联系管理员开通项目访问权限。",
            )

    if visible == set():
        return {"risks": []}

    filters = ["risks.status = 'open'"]
    params: list[Any] = []
    if project_code:
        filters.append("projects.project_code = ?")
        params.append(project_code)
    if visible is not None:
        placeholders = ", ".join("?" for _ in visible)
        filters.append(f"projects.id in ({placeholders})")
        params.extend(sorted(visible))

    rows = conn.execute(
        f"""
        select risks.*, projects.project_code, projects.name as project_name
        from risks
        join projects on projects.id = risks.project_id
        where {' and '.join(filters)}
        order by projects.project_code, risks.external_key
        """,
        params,
    ).fetchall()
    return {"risks": [dict(row) for row in rows]}


def _error(
    code: str,
    message: str,
    *,
    candidates: list[dict[str, Any]] | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "candidates": candidates or [],
            "details": details or {},
        }
    }


def _audit(
    tool_name: str,
    rationale: str | None,
    source_client: str | None,
    arguments: dict[str, Any],
    result: dict[str, Any],
    audit_path: str | Path,
) -> None:
    error = result.get("error")
    write_audit_record(
        tool_name=tool_name,
        rationale=rationale,
        source_client=source_client,
        arguments=arguments,
        result_status="error" if error else "success",
        error_code=error["code"] if error else None,
        audit_path=audit_path,
    )


def _audit_database(
    database_path: str | Path,
    access_context: AccessContext | None,
    tool_name: str,
    rationale: str | None,
    source_client: str | None,
    arguments: dict[str, Any],
    result: dict[str, Any],
    disclosures: list[Disclosure],
) -> None:
    try:
        conn = db.connect(database_path)
        try:
            write_audit_event(
                conn,
                context=access_context,
                tool_name=tool_name,
                rationale=rationale,
                source_client=source_client,
                arguments=arguments,
                result=result,
                disclosures=disclosures,
            )
        finally:
            conn.close()
    except sqlite3.Error as exc:
        print(f"legal-mcp: database audit write failed: {exc}", file=sys.stderr)
        return


def _disclosures_from_result(result: dict[str, Any]) -> list[Disclosure]:
    error = result.get("error")
    if error:
        if not isinstance(error, dict) or error.get("code") != "ambiguous_project":
            return []

        disclosures: list[Disclosure] = []
        for candidate in error.get("candidates", []):
            if isinstance(candidate, dict):
                disclosure = _disclosure_from_record(candidate, "project", candidate)
                if disclosure is not None:
                    disclosures.append(disclosure)
        return disclosures

    disclosures: list[Disclosure] = []
    project = result.get("project")
    if isinstance(project, dict):
        disclosure = _disclosure_from_record(project, "project", project)
        if disclosure is not None:
            disclosures.append(disclosure)
            disclosures.extend(_field_disclosures(disclosure, project, {"id", "project_code", "name"}))

    contract = result.get("contract")
    if isinstance(contract, dict):
        disclosure = _disclosure_from_record(contract, "contract", contract)
        if disclosure is not None:
            disclosures.append(disclosure)
            disclosures.extend(
                _field_disclosures(disclosure, contract, {"id", "contract_number", "title"})
            )

    for project_record in result.get("projects", []):
        if isinstance(project_record, dict):
            disclosure = _disclosure_from_record(project_record, "project", project_record)
            if disclosure is not None:
                disclosures.append(disclosure)

    for record_type in ("licenses", "contracts", "risks"):
        for record in result.get(record_type, []):
            if isinstance(record, dict):
                disclosure = _disclosure_from_record(record, record_type[:-1], None)
                if disclosure is not None:
                    disclosures.append(disclosure)

    return disclosures


def _disclosure_from_record(
    record: dict[str, Any],
    record_type: str,
    project: dict[str, Any] | None,
) -> Disclosure | None:
    project_id = project.get("id") if project is not None else record.get("project_id")
    record_id = record.get("id")
    if project_id is None:
        return None
    return Disclosure(
        project_id=int(project_id),
        record_type=record_type,
        record_id=int(record_id) if record_id is not None else None,
        decision="allowed",
        reason="project_visible",
    )


def _field_disclosures(
    base: Disclosure,
    record: dict[str, Any],
    identity_fields: set[str],
) -> list[Disclosure]:
    return [
        Disclosure(
            project_id=base.project_id,
            record_type=base.record_type,
            record_id=base.record_id,
            decision=base.decision,
            reason=base.reason,
            field_name=field,
            group_id=base.group_id,
        )
        for field in record
        if field not in identity_fields
    ]


def _append_project_field_disclosures(
    conn: sqlite3.Connection,
    arguments: dict[str, Any],
    result: dict[str, Any],
    disclosures: list[Disclosure],
) -> None:
    project = result.get("project")
    query = arguments.get("project_id_or_name")
    if not isinstance(project, dict) or not isinstance(query, str):
        return
    lookup = lookup_project(conn, query)
    if lookup.kind != ProjectLookupResult.FOUND or not lookup.project:
        return
    project_id = int(lookup.project["id"])
    base = Disclosure(
        project_id=project_id,
        record_type="project",
        record_id=project_id,
        decision="allowed",
        reason="project_visible",
    )
    disclosures.append(base)
    disclosures.extend(_field_disclosures(base, project, {"project_code", "name"}))


def _append_contract_field_disclosures(
    conn: sqlite3.Connection,
    arguments: dict[str, Any],
    result: dict[str, Any],
    disclosures: list[Disclosure],
) -> None:
    contract = result.get("contract")
    contract_number = arguments.get("contract_number")
    if not isinstance(contract, dict) or not isinstance(contract_number, str):
        return
    row = conn.execute(
        "select id, project_id from contracts where contract_number = ? or external_key = ?",
        (contract_number, contract_number),
    ).fetchone()
    if row is None:
        return
    base = Disclosure(
        project_id=int(row["project_id"]),
        record_type="contract",
        record_id=int(row["id"]),
        decision="allowed",
        reason="project_visible",
    )
    disclosures.append(base)
    disclosures.extend(_field_disclosures(base, contract, {"contract_number", "title"}))


def _append_project_denied_field_disclosures(
    conn: sqlite3.Connection,
    arguments: dict[str, Any],
    result: dict[str, Any],
    disclosures: list[Disclosure],
) -> None:
    denied_fields = _denied_fields_from_result(result)
    query = arguments.get("project_id_or_name")
    if not denied_fields or not isinstance(query, str):
        return
    lookup = lookup_project(conn, query)
    if lookup.kind != ProjectLookupResult.FOUND or not lookup.project:
        return
    project_id = int(lookup.project["id"])
    disclosures.extend(
        _denied_field_disclosures(
            project_id=project_id,
            record_type="project",
            record_id=project_id,
            denied_fields=denied_fields,
        )
    )


def _append_contract_denied_field_disclosures(
    conn: sqlite3.Connection,
    arguments: dict[str, Any],
    result: dict[str, Any],
    disclosures: list[Disclosure],
) -> None:
    denied_fields = _denied_fields_from_result(result)
    contract_number = arguments.get("contract_number")
    if not denied_fields or not isinstance(contract_number, str):
        return
    row = conn.execute(
        "select id, project_id from contracts where contract_number = ? or external_key = ?",
        (contract_number, contract_number),
    ).fetchone()
    if row is None:
        return
    disclosures.extend(
        _denied_field_disclosures(
            project_id=int(row["project_id"]),
            record_type="contract",
            record_id=int(row["id"]),
            denied_fields=denied_fields,
        )
    )


def _append_project_child_denied_field_disclosures(
    conn: sqlite3.Connection,
    arguments: dict[str, Any],
    result: dict[str, Any],
    disclosures: list[Disclosure],
    *,
    record_type: str,
) -> None:
    denied_fields = _denied_fields_from_result(result)
    query = arguments.get("project_id_or_name")
    if not denied_fields or not isinstance(query, str):
        return
    lookup = lookup_project(conn, query)
    if lookup.kind != ProjectLookupResult.FOUND or not lookup.project:
        return
    disclosures.extend(
        _denied_field_disclosures(
            project_id=int(lookup.project["id"]),
            record_type=record_type,
            record_id=None,
            denied_fields=denied_fields,
        )
    )


def _denied_fields_from_result(result: dict[str, Any]) -> dict[str, str]:
    error = result.get("error")
    if not isinstance(error, dict) or error.get("code") != "field_access_denied":
        return {}
    details = error.get("details")
    if not isinstance(details, dict):
        return {}
    denied_fields = details.get("denied_fields")
    if not isinstance(denied_fields, dict):
        return {}
    return {
        str(field): str(reason)
        for field, reason in denied_fields.items()
        if isinstance(field, str)
    }


def _denied_field_disclosures(
    *,
    project_id: int,
    record_type: str,
    record_id: int | None,
    denied_fields: dict[str, str],
) -> list[Disclosure]:
    return [
        Disclosure(
            project_id=project_id,
            record_type=record_type,
            record_id=record_id,
            field_name=field_name,
            decision="denied",
            reason=reason,
        )
        for field_name, reason in sorted(denied_fields.items())
    ]
