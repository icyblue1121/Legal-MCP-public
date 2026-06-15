"""Fine-grained license MCP tools."""

from __future__ import annotations

import sqlite3
from typing import Any

from legal_mcp.lookup import ProjectLookupResult, lookup_project
from legal_mcp.policy import AccessContext, authorize_fields, project_is_visible
from legal_mcp.tool_catalog import LICENSE_FIELDS

LICENSE_IDENTITY_FIELDS = ("license_type", "identifier")
DEFAULT_LICENSE_LIMIT = 20
MAX_LICENSE_LIMIT = 100


def list_project_licenses(
    conn: sqlite3.Connection,
    arguments: dict[str, Any],
    access_context: AccessContext | None,
) -> dict[str, Any]:
    query = arguments.get("project_id_or_name")
    fields = arguments.get("fields")
    limit = arguments.get("limit", DEFAULT_LICENSE_LIMIT)
    if not isinstance(query, str) or not query.strip():
        return _error("validation_error", "project_id_or_name is required")
    if not isinstance(fields, list) or not fields:
        return _error("validation_error", "fields is required")
    if not isinstance(limit, int) or limit < 1 or limit > MAX_LICENSE_LIMIT:
        return _error("validation_error", "limit must be an integer between 1 and 100")

    requested = {field for field in fields if isinstance(field, str)}
    if len(requested) != len(fields) or not requested.issubset(set(LICENSE_FIELDS)):
        return _error("validation_error", "fields must contain known license field names")

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
        data_domain="license",
        project_id=project_id,
        requested_fields=requested - set(LICENSE_IDENTITY_FIELDS),
    )
    if decision.denied_fields:
        return _field_access_error(decision.denied_fields)

    rows = conn.execute(
        """
        select
          id,
          project_id,
          external_key,
          license_type,
          identifier,
          entity_name,
          issuer,
          approval_number,
          rights_holder,
          copyright_holder,
          operating_entity,
          actual_operator,
          authorization_relation,
          expiry_date,
          notes
        from licenses
        where project_id = ?
        order by license_type, id
        limit ?
        """,
        (project_id, limit),
    ).fetchall()

    projected = [*LICENSE_IDENTITY_FIELDS, *sorted(decision.allowed_fields)]
    return {
        "licenses": [
            {
                field: row[field]
                for field in projected
                if field in row.keys()
            }
            for row in rows
        ]
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
