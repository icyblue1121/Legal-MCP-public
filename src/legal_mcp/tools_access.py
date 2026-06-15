"""Access visibility MCP tools."""

from __future__ import annotations

import sqlite3
from typing import Any

from legal_mcp.policy import AccessContext, grant_scope_clause, visible_project_ids
from legal_mcp.tool_catalog import CONTRACT_FIELDS, LICENSE_FIELDS, PROJECT_FIELDS

FIELDS_BY_DOMAIN = {
    "project": PROJECT_FIELDS,
    "contract": CONTRACT_FIELDS,
    "license": LICENSE_FIELDS,
}


def describe_my_access(
    conn: sqlite3.Connection,
    arguments: dict[str, Any],
    access_context: AccessContext | None,
) -> dict[str, Any]:
    return {"access": build_access_summary(conn, access_context)}


def build_access_summary(
    conn: sqlite3.Connection,
    access_context: AccessContext | None,
) -> dict[str, Any]:
    visible = visible_project_ids(conn, access_context)
    if visible == set():
        project_rows = []
    else:
        params: list[Any] = []
        where = ""
        if visible is not None:
            placeholders = ", ".join("?" for _ in visible)
            where = f" where id in ({placeholders})"
            params.extend(sorted(visible))
        project_rows = conn.execute(
            f"""
            select id, project_code, name
            from projects{where}
            order by project_code
            """,
            params,
        ).fetchall()

    return {
        "message": (
            "当前用户可见项目如下；如果目标项目不在列表中，"
            "可能是权限未开通，也可能是资料库尚未收录。"
        ),
        "projects": [
            {
                "project_code": row["project_code"],
                "name": row["name"],
                "fields": _allowed_fields_by_domain(conn, access_context, int(row["id"])),
            }
            for row in project_rows
        ],
    }


def _allowed_fields_by_domain(
    conn: sqlite3.Connection,
    access_context: AccessContext | None,
    project_id: int,
) -> dict[str, list[str]]:
    scope = grant_scope_clause(conn, access_context)
    if scope is None or not _has_field_grants(conn, access_context):
        return {
            domain: sorted(fields)
            for domain, fields in FIELDS_BY_DOMAIN.items()
        }

    scope_sql, scope_params = scope
    rows = conn.execute(
        f"""
        select data_domain, field_name
        from permission_grants
        where {scope_sql}
          and operation = 'read'
          and allowed = 1
          and (project_id is null or project_id = ?)
        """,
        [*scope_params, project_id],
    ).fetchall()

    allowed = {domain: set() for domain in FIELDS_BY_DOMAIN}
    for row in rows:
        domain = str(row["data_domain"])
        if domain not in FIELDS_BY_DOMAIN:
            continue
        field_name = row["field_name"]
        if field_name is None:
            allowed[domain].update(FIELDS_BY_DOMAIN[domain])
        elif field_name in FIELDS_BY_DOMAIN[domain]:
            allowed[domain].add(str(field_name))

    return {
        domain: sorted(fields)
        for domain, fields in allowed.items()
    }


def _has_field_grants(
    conn: sqlite3.Connection,
    access_context: AccessContext | None,
) -> bool:
    scope = grant_scope_clause(conn, access_context)
    if scope is None:
        return False
    scope_sql, scope_params = scope
    row = conn.execute(
        f"""
        select 1
        from permission_grants
        where {scope_sql}
          and operation = 'read'
          and allowed = 1
        limit 1
        """,
        scope_params,
    ).fetchone()
    return row is not None
