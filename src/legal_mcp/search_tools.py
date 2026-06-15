"""Internal domain search executors for graph-owned retrieval."""

from __future__ import annotations

import sqlite3
from typing import Any

from legal_mcp.identity_match import identity_filter, rank_identity_rows
from legal_mcp.policy import AccessContext, visible_company_ids, visible_project_ids
from legal_mcp.query_authorization import authorize_query_plan
from legal_mcp.query_plan import VIRTUAL_IDENTITY_FIELD, QueryFilter, QueryPlan
from legal_mcp.tool_catalog import CONTRACT_FIELDS, LICENSE_FIELDS, PROJECT_FIELDS, SEAL_FIELDS

PROJECT_COLUMNS = {field: f"projects.{field}" for field in PROJECT_FIELDS}
CONTRACT_COLUMNS = {field: f"contracts.{field}" for field in CONTRACT_FIELDS}
LICENSE_COLUMNS = {field: f"licenses.{field}" for field in LICENSE_FIELDS}
SEAL_COLUMNS = {
    "company": "company_seals.company",
    "seal_type": "company_seals.seal_type",
    "custodian": "company_seals.custodian",
    "storage_location": "company_seals.storage_location",
    "status": "company_seals.status",
    "borrower": "company_seals.borrower",
    "borrowed_at": "company_seals.borrowed_at",
    "borrow_reason": "company_seals.borrow_reason",
    "expected_return_at": "company_seals.expected_return_at",
    "actual_return_at": "company_seals.actual_return_at",
}

PROJECT_IDENTITY_COLUMNS = {
    "project_code": "projects.project_code collate nocase",
    "name": "projects.name collate nocase",
}
CONTRACT_FILTER_COLUMNS = {**CONTRACT_COLUMNS, **PROJECT_IDENTITY_COLUMNS}
LICENSE_FILTER_COLUMNS = {**LICENSE_COLUMNS, **PROJECT_IDENTITY_COLUMNS}

# Identity columns each domain's virtual ``identity`` filter ORs over (v0.4.8). The
# ``collate nocase`` makes the ``eq`` variant case-insensitive too, so a token finds
# a code or a name regardless of case — the same lenient match the connector path
# gives. The matching field-name tuples drive the precision ranking / candidates.
PROJECT_IDENTITY_FILTER_COLUMNS = dict(PROJECT_IDENTITY_COLUMNS)
CONTRACT_IDENTITY_FILTER_COLUMNS = {
    "contract_number": "contracts.contract_number collate nocase",
    "title": "contracts.title collate nocase",
}
LICENSE_IDENTITY_FILTER_COLUMNS = {
    "license_type": "licenses.license_type collate nocase",
    "identifier": "licenses.identifier collate nocase",
}
SEAL_IDENTITY_FILTER_COLUMNS = {
    "company": "companies.name collate nocase",
    "seal_type": "company_seals.seal_type collate nocase",
}
PROJECT_IDENTITY_FIELDS = ("project_code", "name")
CONTRACT_IDENTITY_FIELDS = ("contract_number", "title")
LICENSE_IDENTITY_FIELDS = ("license_type", "identifier")
SEAL_IDENTITY_FIELDS = ("company", "seal_type")


def search_projects(
    conn: sqlite3.Connection,
    plan: QueryPlan,
    *,
    access_context: AccessContext | None,
) -> dict[str, Any]:
    authorization = authorize_query_plan(conn, plan, access_context)
    if not authorization.ok:
        return _error(authorization.error_code or "query_access_denied", authorization.message or "")
    identity_query = identity_filter(plan)
    select_fields = _select_fields(plan, identity_query, PROJECT_IDENTITY_FIELDS)
    where, params = _where_for_plan(plan, PROJECT_COLUMNS, PROJECT_IDENTITY_FILTER_COLUMNS)
    where.extend(_visible_project_filter(conn, access_context, "projects.id", params))
    rows = conn.execute(
        f"""
        select {_select_list(select_fields, PROJECT_COLUMNS)}
        from projects
        {_where_clause(where)}
        order by projects.project_code
        limit ?
        """,
        (*params, plan.limit),
    ).fetchall()
    return _identity_result(
        plan, [dict(row) for row in rows], identity_query, PROJECT_IDENTITY_FIELDS, "projects"
    )


def search_contracts(
    conn: sqlite3.Connection,
    plan: QueryPlan,
    *,
    access_context: AccessContext | None,
) -> dict[str, Any]:
    authorization = authorize_query_plan(conn, plan, access_context)
    if not authorization.ok:
        return _error(authorization.error_code or "query_access_denied", authorization.message or "")
    identity_query = identity_filter(plan)
    select_fields = _select_fields(plan, identity_query, CONTRACT_IDENTITY_FIELDS)
    where, params = _where_for_plan(plan, CONTRACT_FILTER_COLUMNS, CONTRACT_IDENTITY_FILTER_COLUMNS)
    where.extend(_visible_project_filter(conn, access_context, "contracts.project_id", params))
    rows = conn.execute(
        f"""
        select {_select_list(select_fields, CONTRACT_COLUMNS)}
        from contracts
        join projects on projects.id = contracts.project_id
        {_where_clause(where)}
        order by projects.project_code, contracts.contract_number, contracts.external_key
        limit ?
        """,
        (*params, plan.limit),
    ).fetchall()
    return _identity_result(
        plan, [dict(row) for row in rows], identity_query, CONTRACT_IDENTITY_FIELDS, "contracts"
    )


def search_licenses(
    conn: sqlite3.Connection,
    plan: QueryPlan,
    *,
    access_context: AccessContext | None,
) -> dict[str, Any]:
    authorization = authorize_query_plan(conn, plan, access_context)
    if not authorization.ok:
        return _error(authorization.error_code or "query_access_denied", authorization.message or "")
    identity_query = identity_filter(plan)
    select_fields = _select_fields(plan, identity_query, LICENSE_IDENTITY_FIELDS)
    where, params = _where_for_plan(plan, LICENSE_FILTER_COLUMNS, LICENSE_IDENTITY_FILTER_COLUMNS)
    where.extend(_visible_project_filter(conn, access_context, "licenses.project_id", params))
    rows = conn.execute(
        f"""
        select {_select_list(select_fields, LICENSE_COLUMNS)}
        from licenses
        join projects on projects.id = licenses.project_id
        {_where_clause(where)}
        order by projects.project_code, licenses.license_type, licenses.external_key
        limit ?
        """,
        (*params, plan.limit),
    ).fetchall()
    return _identity_result(
        plan, [dict(row) for row in rows], identity_query, LICENSE_IDENTITY_FIELDS, "licenses"
    )


def search_seals(
    conn: sqlite3.Connection,
    plan: QueryPlan,
    *,
    access_context: AccessContext | None,
) -> dict[str, Any]:
    authorization = authorize_query_plan(conn, plan, access_context)
    if not authorization.ok:
        return _error(authorization.error_code or "query_access_denied", authorization.message or "")
    identity_query = identity_filter(plan)
    select_fields = _select_fields(plan, identity_query, SEAL_IDENTITY_FIELDS)
    where, params = _where_for_plan(plan, SEAL_COLUMNS, SEAL_IDENTITY_FILTER_COLUMNS)
    where.extend(_visible_company_filter(conn, access_context, "company_seals.company_id", params))
    rows = conn.execute(
        f"""
        select {_select_list(select_fields, SEAL_COLUMNS)}
        from company_seals
        join companies on companies.id = company_seals.company_id
        {_where_clause(where)}
        order by companies.name, company_seals.seal_type
        limit ?
        """,
        (*params, plan.limit),
    ).fetchall()
    return _identity_result(
        plan, [dict(row) for row in rows], identity_query, SEAL_IDENTITY_FIELDS, "seals"
    )


def search_cross_domain(
    conn: sqlite3.Connection,
    plan: QueryPlan,
    *,
    access_context: AccessContext | None,
) -> dict[str, Any]:
    authorization = authorize_query_plan(conn, plan, access_context)
    if not authorization.ok:
        return _error(authorization.error_code or "query_access_denied", authorization.message or "")
    term = _cross_domain_term(plan)
    if not term:
        return {"projects": [], "contracts": [], "licenses": []}
    limit = plan.limit
    return {
        "projects": search_projects(
            conn,
            QueryPlan(
                domain="project",
                operation="search",
                filters=[QueryFilter(field="legal_bp", operator="contains", value=term)],
                return_fields=_available_fields(plan.return_fields, PROJECT_COLUMNS),
                limit=limit,
            ),
            access_context=access_context,
        )["projects"],
        "contracts": _search_contracts_any(conn, term, limit, access_context),
        "licenses": _search_licenses_any(conn, term, limit, access_context),
    }


def execute_search_plan(
    conn: sqlite3.Connection,
    plan: QueryPlan,
    *,
    access_context: AccessContext | None,
) -> dict[str, Any]:
    # Defense in depth: an unknown field/operator that slipped past validation
    # becomes a structured error instead of an uncaught exception.
    try:
        if plan.domain == "project":
            return search_projects(conn, plan, access_context=access_context)
        if plan.domain == "contract":
            return search_contracts(conn, plan, access_context=access_context)
        if plan.domain == "license":
            return search_licenses(conn, plan, access_context=access_context)
        if plan.domain == "seal":
            return search_seals(conn, plan, access_context=access_context)
        if plan.domain == "cross_domain":
            return search_cross_domain(conn, plan, access_context=access_context)
    except ValueError as exc:
        return _error("unsupported_field", str(exc))
    return _error("unsupported_domain", "query domain is not supported")


def _search_contracts_any(
    conn: sqlite3.Connection,
    term: str,
    limit: int,
    access_context: AccessContext | None,
) -> list[dict[str, Any]]:
    params: list[Any] = [f"%{term}%", f"%{term}%", f"%{term}%"]
    where = [
        "(contracts.counterparty like ? or contracts.handler like ? or contracts.title like ?)"
    ]
    where.extend(_visible_project_filter(conn, access_context, "contracts.project_id", params))
    rows = conn.execute(
        f"""
        select contracts.contract_number, contracts.title
        from contracts
        join projects on projects.id = contracts.project_id
        {_where_clause(where)}
        order by projects.project_code, contracts.contract_number, contracts.external_key
        limit ?
        """,
        (*params, limit),
    ).fetchall()
    return [dict(row) for row in rows]


def _search_licenses_any(
    conn: sqlite3.Connection,
    term: str,
    limit: int,
    access_context: AccessContext | None,
) -> list[dict[str, Any]]:
    params: list[Any] = [f"%{term}%", f"%{term}%", f"%{term}%"]
    where = [
        "(licenses.actual_operator like ? or licenses.operating_entity like ? or licenses.license_type like ?)"
    ]
    where.extend(_visible_project_filter(conn, access_context, "licenses.project_id", params))
    rows = conn.execute(
        f"""
        select licenses.license_type, licenses.actual_operator
        from licenses
        join projects on projects.id = licenses.project_id
        {_where_clause(where)}
        order by projects.project_code, licenses.license_type, licenses.external_key
        limit ?
        """,
        (*params, limit),
    ).fetchall()
    return [dict(row) for row in rows]


def _where_for_plan(
    plan: QueryPlan,
    columns: dict[str, str],
    identity_columns: dict[str, str] | None = None,
) -> tuple[list[str], list[Any]]:
    where: list[str] = []
    params: list[Any] = []
    for query_filter in plan.filters:
        if query_filter.field == VIRTUAL_IDENTITY_FIELD:
            # Expand the virtual identity filter to an OR over the domain's identity
            # columns: ``(project_code like ? or name like ?)`` (v0.4.8), one
            # parenthesized group so it AND-composes with any other filter.
            if not identity_columns:
                raise ValueError("identity filter is not supported for this domain")
            ors = [
                _condition(column, query_filter.operator, query_filter.value, params)
                for column in identity_columns.values()
            ]
            where.append("(" + " or ".join(ors) + ")")
            continue
        column = columns.get(query_filter.field)
        if column is None:
            raise ValueError(f"unsupported filter field: {query_filter.field}")
        where.append(_condition(column, query_filter.operator, query_filter.value, params))
    return where, params


def _select_fields(
    plan: QueryPlan,
    identity_query: tuple[str, str] | None,
    identity_fields: tuple[str, ...],
) -> list[str]:
    """Fields to SELECT: the requested return fields, plus the identity fields when
    this is an identity query (so the ranking / candidate list can compare them)."""
    if identity_query is None:
        return list(plan.return_fields)
    return list(dict.fromkeys([*plan.return_fields, *identity_fields]))


def _identity_result(
    plan: QueryPlan,
    rows: list[dict[str, Any]],
    identity_query: tuple[str, str] | None,
    identity_fields: tuple[str, ...],
    result_key: str,
) -> dict[str, Any]:
    """Wrap raw rows in the domain result, ranking identity matches when present.

    The rows are already record-scoped (the visible-project filter) and field-gated
    (authorization ran above), so the candidate list cannot leak (v0.4.8)."""
    if identity_query is None:
        return {result_key: rows}
    _, token = identity_query
    matched, ambiguous = rank_identity_rows(
        rows,
        token=token,
        identity_fields=identity_fields,
        return_fields=plan.return_fields,
    )
    result: dict[str, Any] = {result_key: matched[: plan.limit]}
    if ambiguous:
        result["identity_disambiguation"] = {"token": token, "candidate_count": len(matched)}
    return result


def _condition(column: str, operator: str, value: Any, params: list[Any]) -> str:
    if operator == "eq":
        params.append(value)
        return f"{column} = ?"
    if operator == "contains":
        params.append(f"%{value}%")
        return f"{column} like ?"
    if operator == "in":
        values = list(value) if isinstance(value, list | tuple | set) else [value]
        params.extend(values)
        return f"{column} in ({', '.join('?' for _ in values)})"
    if operator == "is_empty":
        return f"({column} is null or {column} = '')"
    if operator == "date_before":
        params.append(value)
        return f"{column} < ?"
    if operator == "date_after":
        params.append(value)
        return f"{column} > ?"
    if operator == "date_between":
        start, end = value
        params.extend([start, end])
        return f"{column} between ? and ?"
    raise ValueError(f"unsupported operator: {operator}")


def _visible_project_filter(
    conn: sqlite3.Connection,
    access_context: AccessContext | None,
    column: str,
    params: list[Any],
) -> list[str]:
    visible = record_scope_project_ids(conn, access_context)
    if visible is None:
        return []
    if not visible:
        return ["1 = 0"]
    params.extend(sorted(visible))
    return [f"{column} in ({', '.join('?' for _ in visible)})"]


def _visible_company_filter(
    conn: sqlite3.Connection,
    access_context: AccessContext | None,
    column: str,
    params: list[Any],
) -> list[str]:
    visible = visible_company_ids(conn, access_context)
    if visible is None:
        return []
    if not visible:
        return ["1 = 0"]
    params.extend(sorted(visible))
    return [f"{column} in ({', '.join('?' for _ in visible)})"]


def record_scope_project_ids(
    conn: sqlite3.Connection,
    access_context: AccessContext | None,
) -> set[int] | None:
    """Row-level visibility as governance project ids — the single source of truth.

    Returns ``None`` for unrestricted (admin / legacy token), a set of allowed ids
    for a restricted scope, or an empty set for default-deny. The SQLite path turns
    this into a SQL ``in (...)`` filter; the connector path
    (:mod:`legal_mcp.connector_retrieval`) maps the ids to ``project_code`` so the
    same scope decision reaches a non-SQL source. Keep them sharing this function.
    """
    return visible_project_ids(conn, access_context)


def _select_list(return_fields: list[str], columns: dict[str, str]) -> str:
    selected = []
    for field in return_fields:
        column = columns.get(field)
        if column is None:
            raise ValueError(f"unsupported return field: {field}")
        selected.append(f"{column} as {field}")
    return ", ".join(selected)


def _where_clause(where: list[str]) -> str:
    return f"where {' and '.join(where)}" if where else ""


def _cross_domain_term(plan: QueryPlan) -> str | None:
    for query_filter in plan.filters:
        if query_filter.field in {"q", "query", "term"} and isinstance(query_filter.value, str):
            return query_filter.value
    return None


def _available_fields(return_fields: list[str], columns: dict[str, str]) -> list[str]:
    fields = [field for field in return_fields if field in columns]
    return fields or ["project_code", "name"]


def _error(code: str, message: str) -> dict[str, Any]:
    return {"error": {"code": code, "message": message}}
