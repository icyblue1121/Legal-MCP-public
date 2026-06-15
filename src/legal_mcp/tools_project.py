"""Fine-grained project MCP tools."""

from __future__ import annotations

import sqlite3
from typing import Any

from legal_mcp.lookup import ProjectLookupResult, lookup_project
from legal_mcp.policy import AccessContext, authorize_fields, project_is_visible
from legal_mcp.tool_catalog import PROJECT_FIELDS


def get_project_fields(
    conn: sqlite3.Connection,
    arguments: dict[str, Any],
    access_context: AccessContext | None,
) -> dict[str, Any]:
    query = arguments.get("project_id_or_name")
    fields = arguments.get("fields")
    if not isinstance(query, str) or not query.strip():
        return _error("validation_error", "project_id_or_name is required")
    if not isinstance(fields, list) or not fields:
        return _error("validation_error", "fields is required")
    requested = {field for field in fields if isinstance(field, str)}
    if len(requested) != len(fields) or not requested.issubset(set(PROJECT_FIELDS)):
        return _error("validation_error", "fields must contain known project field names")

    lookup = lookup_project(conn, query)
    if lookup.kind != ProjectLookupResult.FOUND:
        return _error("not_found", "project not found")
    project = lookup.project or {}
    project_id = int(project["id"])
    if not project_is_visible(conn, access_context, project_id):
        return _permission_error()
    decision = authorize_fields(
        conn,
        access_context,
        operation="read",
        data_domain="project",
        project_id=project_id,
        requested_fields=requested,
    )
    if decision.denied_fields:
        return _field_access_error(decision.denied_fields)

    return {
        "project": {
            field: project.get(field)
            for field in sorted(decision.allowed_fields)
            if field in project
        }
    }


def resolve_project(
    conn: sqlite3.Connection,
    arguments: dict[str, Any],
    access_context: AccessContext | None,
) -> dict[str, Any]:
    query = arguments.get("query")
    if not isinstance(query, str) or not query.strip():
        return _error("validation_error", "query is required")
    lookup = lookup_project(conn, query)
    if lookup.kind != ProjectLookupResult.FOUND:
        return _error("not_found", "project not found")
    project = lookup.project or {}
    if not project_is_visible(conn, access_context, int(project["id"])):
        return _permission_error()
    return {
        "project": {
            "project_code": project["project_code"],
            "name": project["name"],
        }
    }


def _error(code: str, message: str) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "candidates": [], "details": {}}}


def _permission_error() -> dict[str, Any]:
    return _error(
        "access_denied",
        "权限不足，当前用户没有访问该项目的权限。请联系管理员开通项目访问权限。",
    )


def _field_access_error(denied_fields: dict[str, str]) -> dict[str, Any]:
    return {
        "error": {
            "code": "field_access_denied",
            "message": "one or more requested fields are not granted",
            "candidates": [],
            "details": {"denied_fields": dict(sorted(denied_fields.items()))},
        }
    }
