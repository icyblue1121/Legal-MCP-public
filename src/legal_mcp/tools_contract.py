"""Fine-grained contract MCP tools."""

from __future__ import annotations

import sqlite3
from typing import Any

from legal_mcp.lookup import ProjectLookupResult, lookup_project
from legal_mcp.policy import AccessContext, authorize_fields, project_is_visible
from legal_mcp.tool_catalog import CONTRACT_FIELDS

CONTRACT_IDENTITY_FIELDS = ("contract_number", "title")
DEFAULT_CONTRACT_LIMIT = 20
MAX_CONTRACT_LIMIT = 100


def list_project_contracts(
    conn: sqlite3.Connection,
    arguments: dict[str, Any],
    access_context: AccessContext | None,
) -> dict[str, Any]:
    query = arguments.get("project_id_or_name")
    fields = arguments.get("fields")
    limit = arguments.get("limit", DEFAULT_CONTRACT_LIMIT)
    if not isinstance(query, str) or not query.strip():
        return _error("validation_error", "project_id_or_name is required")
    if not isinstance(fields, list) or not fields:
        return _error("validation_error", "fields is required")
    if not isinstance(limit, int) or limit < 1 or limit > MAX_CONTRACT_LIMIT:
        return _error("validation_error", "limit must be an integer between 1 and 100")

    requested = {field for field in fields if isinstance(field, str)}
    if len(requested) != len(fields) or not requested.issubset(set(CONTRACT_FIELDS)):
        return _error("validation_error", "fields must contain known contract field names")

    lookup = lookup_project(conn, query)
    if lookup.kind != ProjectLookupResult.FOUND:
        return _error("not_found", "project not found")
    project = lookup.project or {}
    project_id = int(project["id"])
    if not project_is_visible(conn, access_context, project_id):
        return _permission_error("project")
    decision = authorize_fields(
        conn,
        access_context,
        operation="read",
        data_domain="contract",
        project_id=project_id,
        requested_fields=requested - set(CONTRACT_IDENTITY_FIELDS),
    )
    if decision.denied_fields:
        return _field_access_error(decision.denied_fields)

    rows = conn.execute(
        """
        select
          id,
          project_id,
          external_key,
          title,
          handler,
          payment_terms,
          currency,
          total_amount,
          expiry_date,
          counterparty,
          company_entity,
          signed_date,
          contract_number,
          income_expense_type,
          summary
        from contracts
        where project_id = ?
        order by signed_date desc, id desc
        limit ?
        """,
        (project_id, limit),
    ).fetchall()

    projected = [*CONTRACT_IDENTITY_FIELDS, *sorted(decision.allowed_fields)]
    return {
        "contracts": [
            {
                field: row[field]
                for field in projected
                if field in row.keys()
            }
            for row in rows
        ]
    }


def get_contract_fields(
    conn: sqlite3.Connection,
    arguments: dict[str, Any],
    access_context: AccessContext | None,
) -> dict[str, Any]:
    contract_number = arguments.get("contract_number")
    fields = arguments.get("fields")
    if not isinstance(contract_number, str) or not contract_number.strip():
        return _error("validation_error", "contract_number is required")
    if not isinstance(fields, list) or not fields:
        return _error("validation_error", "fields is required")
    requested = {field for field in fields if isinstance(field, str)}
    if len(requested) != len(fields) or not requested.issubset(set(CONTRACT_FIELDS)):
        return _error("validation_error", "fields must contain known contract field names")

    row = conn.execute(
        """
        select
          id,
          project_id,
          external_key,
          title,
          handler,
          payment_terms,
          currency,
          total_amount,
          expiry_date,
          counterparty,
          company_entity,
          signed_date,
          contract_number,
          income_expense_type,
          summary
        from contracts
        where contract_number = ? or external_key = ?
        """,
        (contract_number, contract_number),
    ).fetchone()
    if row is None:
        return _error("not_found", "contract not found")
    project_id = int(row["project_id"])
    if not project_is_visible(conn, access_context, project_id):
        return _permission_error("contract")
    decision = authorize_fields(
        conn,
        access_context,
        operation="read",
        data_domain="contract",
        project_id=project_id,
        requested_fields=requested - set(CONTRACT_IDENTITY_FIELDS),
    )
    if decision.denied_fields:
        return _field_access_error(decision.denied_fields)

    projected = [*CONTRACT_IDENTITY_FIELDS, *sorted(decision.allowed_fields)]
    return {
        "contract": {
            field: row[field]
            for field in projected
            if field in row.keys()
        }
    }


def _error(code: str, message: str) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "candidates": [], "details": {}}}


def _permission_error(record_type: str) -> dict[str, Any]:
    target = "该合同所属项目" if record_type == "contract" else "该项目"
    return _error(
        "access_denied",
        f"权限不足，当前用户没有访问{target}的权限。请联系管理员开通项目访问权限。",
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
