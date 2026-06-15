"""Connector-backed retrieval for the live agent path (pivot v0.3).

The SQLite demo retrieves via raw SQL in :mod:`legal_mcp.search_tools`. A real
read-through source (Feishu, …) cannot: it has no SQL, no integer ``project_id``
foreign keys, and the gateway must never hold its rows longer than a single
authorized query. This module is the connector-served counterpart of
``execute_search_plan``: same DB-grant authorization, same result shape, but the
rows come from ``connector.query()`` instead of SQL.

Authorization stays *here in the gateway*, around the connector — never inside it:

* **Field gate** — reuses :func:`legal_mcp.query_authorization.authorize_query_plan`
  (the DB permission grants), identical to the SQLite path. The connector only
  ever sees already-authorized return fields.
* **Record scope** — reuses :func:`legal_mcp.search_tools.record_scope_project_ids`
  (the single source of truth) and maps the governance project ids to
  ``project_code`` values, so the *same* row-level decision reaches a non-SQL
  source. Rows are filtered by their scope field after the fetch and before they
  can reach the model. Out-of-scope rows never enter the answer.

Record scope by mode (v0.4.5 Phase 4): ``by_governed_code`` is set-membership over
governed codes and post-filters (it can't be pushed through the equality-only
connector); ``by_owner`` is a single ``owner == subject`` equality and is pushed
**down** into the connector filters so the source filters *then* limits — fixing the
post-filter false-empty for the owner case. A by_owner domain scopes to the
requester's own subject (``external_subject`` / ``email`` / ``user_id``); no
identifiable subject → zero rows, never "all".

User filter operators are pushed down to the source so a connector-served domain
answers a *fuzzy* search the same way the SQLite path does (v0.4.7): ``eq`` /
``contains`` / ``in`` are translated to :class:`ConnectorFilter` predicates and
handed to the connector, which maps them to its native query (Feishu's
``contains``; SQLite ``LIKE``). Before v0.4.7 only equality was translated, so
switching the live source to a connector silently dropped every ``contains`` /
``in`` search to ``unsupported_operator`` and forced exact-name guessing. A
richer operator the gateway does not yet push down (``is_empty`` / ``date_*``) is
still reported, not silently dropped. The ``by_owner`` scope predicate stays an
equality and OVERRIDES any client filter on the owner field.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from legal_mcp.connectors.base import (
    ConnectorDomain,
    ConnectorFilter,
    ConnectorQuery,
    DataConnector,
)
from legal_mcp.identity_match import identity_filter, rank_identity_rows
from legal_mcp.policy import AccessContext, record_owner_subject
from legal_mcp.query_authorization import authorize_query_plan
from legal_mcp.query_catalog import build_query_catalog_from_connector
from legal_mcp.query_plan import VIRTUAL_IDENTITY_FIELD, QueryPlan
from legal_mcp.search_tools import record_scope_project_ids

# The result key each legacy domain returns under, matching ``search_tools``
# exactly so the connector path is indistinguishable downstream (format_answer,
# audit). A domain not listed here returns under its own name.
_RESULT_KEY = {"project": "projects", "contract": "contracts", "license": "licenses"}


def result_key_for_domain(domain: str) -> str:
    """The result-dict key a domain's rows are returned under."""
    return _RESULT_KEY.get(domain, domain)


def execute_connector_plan(
    connector: DataConnector,
    plan: QueryPlan,
    *,
    conn: sqlite3.Connection,
    access_context: AccessContext | None,
) -> dict[str, Any]:
    """Retrieve an authorized plan from a connector. ``conn`` is the governance DB.

    Returns the same ``{"projects": [...]}`` shape as ``execute_search_plan`` on
    success, or ``{"error": {...}}`` on a denied/unsupported plan. The domain's
    record scope is resolved from its connector declaration (v0.4.0 §A), so a
    non-project domain is served without the legacy ``project_code`` assumption.
    """
    domain = _connector_domain(connector, plan.domain)
    if domain is None:
        return _error("unsupported_domain", "query domain is not supported by the connector path")
    result_key = _RESULT_KEY.get(plan.domain, plan.domain)
    scope = domain.record_scope

    # Per-domain identity/relationship metadata comes from the connector catalog
    # (``is_identity`` flags), so a new domain authorizes correctly with no edit to
    # query_authorization (v0.4.0 §B).
    catalog = build_query_catalog_from_connector(connector, conn)
    domain_catalog = catalog.domains.get(plan.domain)
    identity_fields = (
        tuple(sorted(domain_catalog.identity_fields)) if domain_catalog else ()
    )

    # Field gate (re-check: the graph's authorize_plan node already ran it,
    # exactly as the SQLite path re-checks inside execute_search_plan).
    authorization = authorize_query_plan(conn, plan, access_context, catalog=catalog)
    if not authorization.ok:
        return _error(
            authorization.error_code or "query_access_denied",
            authorization.message or "",
        )

    filters, unsupported = _connector_filters(plan, identity_fields)
    if unsupported is not None:
        return _error(
            "unsupported_operator",
            f"connector retrieval does not push down operator '{unsupported}' "
            f"(supported: {', '.join(sorted(_PUSHABLE_OPERATORS))})",
        )

    # Record scope, by mode:
    #   none             — no row scope; the field gate is the only gate.
    #   by_governed_code — set-membership over governed codes; can't be pushed
    #                      through the equality-only connector, so it post-filters
    #                      (documented fallback, see the false-empty note below).
    #   by_owner         — a single equality (owner == subject); pushed DOWN so the
    #                      source filters *then* limits — no false-empty (Phase 4).
    allowed_codes: frozenset[str] | None = None
    owner_subject: str | None = None
    scope_field: str | None = None
    if scope.mode == "none":
        pass
    elif scope.mode == "by_owner":
        scope_field = scope.field
        owner_subject = record_owner_subject(access_context, scope.subject)
        if not owner_subject:
            # Fail-closed red line: anonymous / legacy / unmapped → zero rows. This
            # does NOT route through the None=all sentinel, so it can never widen.
            return {result_key: []}
        # Push the owner predicate down. It OVERRIDES any client-supplied filter on
        # the owner field, so a user cannot request a peer's rows by adding their
        # own ``owner == peer`` filter (drop any such filter, then append our own).
        filters = [f for f in filters if f.field != scope_field]
        filters.append(ConnectorFilter(field=scope_field, operator="eq", value=owner_subject))
    else:  # by_governed_code
        scope_field = scope.field
        allowed_codes = _record_scope_codes(conn, access_context)
        if allowed_codes is not None and not allowed_codes:
            return {result_key: []}  # default-deny row scope: disclose nothing.

    # Fetch the authorized return fields, plus the scope field (so the post-filter
    # safety net can see it) and — for an identity query — the identity fields (so
    # the precision ranking / candidate list can compare and disambiguate). Both are
    # identity-or-scope columns, projected out below unless asked for, so they never
    # widen disclosure.
    identity_query = identity_filter(plan)
    fetch_names: tuple[str, ...] = tuple(plan.return_fields)
    if scope_field is not None:
        fetch_names = (*fetch_names, scope_field)
    if identity_query is not None:
        fetch_names = (*fetch_names, *identity_fields)
    fetch_fields = tuple(dict.fromkeys(fetch_names))
    try:
        rows = connector.query(
            ConnectorQuery(
                domain=plan.domain,
                filters=tuple(filters),
                fields=fetch_fields,
                limit=plan.limit,
            )
        )
    except ValueError as exc:
        return _error("unsupported_field", str(exc))

    if allowed_codes is not None and scope_field is not None:
        # by_governed_code post-filter. Documented fallback limitation: because the
        # set scope can't be pushed down, the source applies ``limit`` *before* this
        # filter, so a user whose rows sit past the source's first ``limit`` rows can
        # see a false-empty. by_owner avoids this by pushing its equality down.
        rows = [row for row in rows if str(row.get(scope_field, "")) in allowed_codes]
    if owner_subject is not None and scope_field is not None:
        # by_owner defense-in-depth: the pushdown already narrowed at the source;
        # this drops any row a buggy/over-broad connector returned that isn't the
        # owner's, so a connector bug degrades to a false-empty, never a leak.
        rows = [row for row in rows if str(row.get(scope_field, "")) == owner_subject]

    if identity_query is not None:
        # Identity query: rank exact-vs-substring and project to identity ∪ return
        # fields. The rows are already record-scoped and field-gated, so a candidate
        # list cannot disclose an out-of-scope row or an ungranted field (v0.4.8).
        _, token = identity_query
        matched, ambiguous = rank_identity_rows(
            rows,
            token=token,
            identity_fields=identity_fields,
            return_fields=plan.return_fields,
        )
        result: dict[str, Any] = {result_key: matched[: plan.limit]}
        if ambiguous:
            result["identity_disambiguation"] = {
                "token": token,
                "candidate_count": len(matched),
            }
        return result

    # Project to exactly the authorized return fields (drop the scope field if the
    # plan did not ask for it).
    projected = [
        {field: row[field] for field in plan.return_fields if field in row}
        for row in rows[: plan.limit]
    ]
    return {result_key: projected}


def _connector_domain(connector: DataConnector, name: str) -> ConnectorDomain | None:
    """The connector's declared domain (carrying its record scope), or None."""
    for domain in connector.catalog():
        if domain.name == name:
            return domain
    return None


def _record_scope_codes(
    conn: sqlite3.Connection,
    access_context: AccessContext | None,
) -> frozenset[str] | None:
    """Record scope as ``project_code`` values: ``None`` = all, empty = deny."""
    ids = record_scope_project_ids(conn, access_context)
    if ids is None:
        return None
    if not ids:
        return frozenset()
    placeholders = ", ".join("?" for _ in ids)
    rows = conn.execute(
        f"select project_code from projects where id in ({placeholders})",
        tuple(sorted(ids)),
    ).fetchall()
    return frozenset(str(row["project_code"]) for row in rows)


# Plan operators the connector path pushes down to a source (v0.4.7, extended
# v0.5.1). These now cover the full ``QueryPlan`` operator surface: exact (``eq``),
# fuzzy substring (``contains``), multi-value (``in``), emptiness (``is_empty``),
# and the date comparisons (``date_before`` / ``date_after`` / ``date_between``) —
# all of which the SQLite *direct* path (``search_tools``) already translated, so
# a connector-served domain no longer silently drops them to ``unsupported_operator``.
_PUSHABLE_OPERATORS = frozenset(
    {"eq", "contains", "in", "is_empty", "date_before", "date_after", "date_between"}
)


def _connector_filters(
    plan: QueryPlan, identity_fields: tuple[str, ...]
) -> tuple[list[ConnectorFilter], str | None]:
    """Translate plan filters to operator-aware connector filters, or flag the
    first operator the connector path does not push down.

    A virtual ``identity`` filter (v0.4.8) is expanded to one ``ConnectorFilter``
    that ORs ``operator value`` across the domain's identity fields, so the source
    filters on a code-or-name match in a single pushed-down query.
    """
    filters: list[ConnectorFilter] = []
    for query_filter in plan.filters:
        if query_filter.operator not in _PUSHABLE_OPERATORS:
            return filters, query_filter.operator
        if query_filter.field == VIRTUAL_IDENTITY_FIELD:
            filters.append(
                ConnectorFilter(
                    field=VIRTUAL_IDENTITY_FIELD,
                    operator=query_filter.operator,
                    value=query_filter.value,
                    or_fields=identity_fields,
                )
            )
            continue
        filters.append(
            ConnectorFilter(
                field=query_filter.field,
                operator=query_filter.operator,
                value=query_filter.value,
            )
        )
    return filters, None


def _error(code: str, message: str) -> dict[str, Any]:
    return {"error": {"code": code, "message": message}}
