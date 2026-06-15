"""Authorization checks for constrained query plans."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from legal_mcp.disclosure_audit import Disclosure
from legal_mcp.policy import AccessContext, authorize_fields, visible_project_ids
from legal_mcp.query_plan import VIRTUAL_IDENTITY_FIELD, QueryPlan, validate_query_plan

if TYPE_CHECKING:
    from legal_mcp.query_catalog import QueryCatalog

PROJECT_IDENTITY_FIELDS = frozenset({"project_code", "name"})
CONTRACT_IDENTITY_FIELDS = frozenset({"contract_number", "title"})
LICENSE_IDENTITY_FIELDS = frozenset({"license_type", "identifier"})
SEAL_IDENTITY_FIELDS = frozenset({"company", "seal_type"})
PROJECT_RELATIONSHIP_FILTER_FIELDS = frozenset({"project_code", "name"})
MAX_LICENSE_DETAIL_RETURN_FIELDS = 4


@dataclass(frozen=True)
class AuthorizedQueryPlan:
    plan: QueryPlan


@dataclass(frozen=True)
class QueryAuthorizationResult:
    ok: bool
    authorized_plan: AuthorizedQueryPlan | None = None
    error_code: str | None = None
    message: str | None = None
    disclosures: list[Disclosure] = field(default_factory=list)


def authorize_query_plan(
    conn: sqlite3.Connection,
    plan: QueryPlan,
    access_context: AccessContext | None,
    catalog: "QueryCatalog | None" = None,
) -> QueryAuthorizationResult:
    """Authorize a query plan against the database permission grants.

    The DB grants are the single authorization gate (v0.4.0 §C C6): a field is
    released only if the requester holds a grant for it, default-deny, with
    deny-over-allow. There is no parallel file gate.

    When ``catalog`` is provided, a domain's identity and relationship-filter
    fields are read from it (sourced from the connector's ``is_identity`` flags),
    so an arbitrary domain authorizes correctly without a per-domain code branch
    here (v0.4.0 §B). When ``catalog`` is ``None`` the legacy hard-coded metadata
    for the three built-in domains is used, so existing callers are untouched.
    """
    allowed_domains = set(catalog.domains) if catalog is not None else None
    validation = validate_query_plan(plan, allowed_domains)
    if not validation.ok:
        return QueryAuthorizationResult(
            ok=False,
            error_code=validation.error_code,
            message=validation.message,
        )

    if plan.domain == "cross_domain":
        # A cross-domain free-text search carries only a synthetic `q` filter, so
        # there is no concrete field to map; the per-domain DB grant check is the
        # authority for cross-domain queries.
        return _authorize_cross_domain(conn, plan, access_context)

    identity_fields = _identity_fields(plan.domain, catalog)
    relationship_filter_fields = _relationship_filter_fields(plan.domain, catalog)
    # The virtual ``identity`` filter (v0.4.8) is exempt from the field gate exactly
    # as the identity fields it expands to are: it can only ever reach an identity
    # column, so it never releases a non-identity field and cannot widen disclosure.
    filter_fields = (
        {query_filter.field for query_filter in plan.filters}
        - identity_fields
        - relationship_filter_fields
        - {VIRTUAL_IDENTITY_FIELD}
    )
    return_fields = set(plan.return_fields) - identity_fields
    if (
        plan.domain == "license"
        and len(return_fields) > MAX_LICENSE_DETAIL_RETURN_FIELDS
    ):
        return QueryAuthorizationResult(
            ok=False,
            error_code="overbroad_return_fields",
            message="license queries must request a narrow set of explicit fields",
        )
    project_ids = _authorization_project_ids(conn, access_context, plan.domain, catalog)

    filter_denials = _denials_for_fields(
        conn,
        access_context,
        data_domain=plan.domain,
        project_ids=project_ids,
        fields=filter_fields,
        record_type=plan.domain,
    )
    if filter_denials:
        return QueryAuthorizationResult(
            ok=False,
            error_code="filter_field_access_denied",
            message="one or more filter fields are not granted",
            disclosures=filter_denials,
        )

    return_denials = _denials_for_fields(
        conn,
        access_context,
        data_domain=plan.domain,
        project_ids=project_ids,
        fields=return_fields,
        record_type=plan.domain,
    )
    if return_denials:
        return QueryAuthorizationResult(
            ok=False,
            error_code="return_field_access_denied",
            message="one or more return fields are not granted",
            disclosures=return_denials,
        )

    return QueryAuthorizationResult(ok=True, authorized_plan=AuthorizedQueryPlan(plan))


def _authorize_cross_domain(
    conn: sqlite3.Connection,
    plan: QueryPlan,
    access_context: AccessContext | None,
) -> QueryAuthorizationResult:
    project_ids = _authorization_project_ids(conn, access_context)
    checks = {
        "project": {"legal_bp", "name"} - PROJECT_IDENTITY_FIELDS,
        "contract": {"counterparty", "handler", "title"} - CONTRACT_IDENTITY_FIELDS,
        "license": {"actual_operator", "operating_entity", "license_type"}
        - LICENSE_IDENTITY_FIELDS,
    }
    disclosures: list[Disclosure] = []
    for domain, fields in checks.items():
        disclosures.extend(
            _denials_for_fields(
                conn,
                access_context,
                data_domain=domain,
                project_ids=project_ids,
                fields=fields,
                record_type=domain,
            )
        )
    if disclosures:
        return QueryAuthorizationResult(
            ok=False,
            error_code="filter_field_access_denied",
            message="one or more cross-domain search fields are not granted",
            disclosures=disclosures,
        )
    return QueryAuthorizationResult(ok=True, authorized_plan=AuthorizedQueryPlan(plan))


def _authorization_project_ids(
    conn: sqlite3.Connection,
    access_context: AccessContext | None,
    domain: str | None = None,
    catalog: "QueryCatalog | None" = None,
) -> set[int | None]:
    # A ``none``-scope domain has no project dimension: its grants are global
    # (project_id NULL), so the field gate must evaluate at project_id None rather
    # than the requester's visible projects (which would be empty and vacuously
    # pass the gate, since there is no row scope to mask it). v0.4.0 §A.
    if catalog is not None and domain is not None:
        domain_catalog = catalog.domains.get(domain)
        if domain_catalog is not None and domain_catalog.record_scope.mode == "none":
            return {None}
    visible = visible_project_ids(conn, access_context)
    if visible is None:
        return {None}
    return set(visible)


def _denials_for_fields(
    conn: sqlite3.Connection,
    access_context: AccessContext | None,
    *,
    data_domain: str,
    project_ids: set[int | None],
    fields: set[str],
    record_type: str,
) -> list[Disclosure]:
    if not fields:
        return []

    disclosures: list[Disclosure] = []
    for project_id in sorted(project_ids, key=lambda value: -1 if value is None else value):
        decision = authorize_fields(
            conn,
            access_context,
            operation="read",
            data_domain=data_domain,
            project_id=project_id,
            requested_fields=fields,
        )
        for field_name, reason in sorted(decision.denied_fields.items()):
            disclosures.append(
                Disclosure(
                    project_id=project_id,
                    record_type=record_type,
                    record_id=None,
                    field_name=field_name,
                    decision="denied",
                    reason=reason,
                )
            )
    return disclosures


def _identity_fields(
    domain: str, catalog: "QueryCatalog | None" = None
) -> frozenset[str]:
    if catalog is not None:
        domain_catalog = catalog.domains.get(domain)
        if domain_catalog is not None:
            return frozenset(domain_catalog.identity_fields)
    if domain == "project":
        return PROJECT_IDENTITY_FIELDS
    if domain == "contract":
        return CONTRACT_IDENTITY_FIELDS
    if domain == "license":
        return LICENSE_IDENTITY_FIELDS
    if domain == "seal":
        return SEAL_IDENTITY_FIELDS
    return frozenset()


def _relationship_filter_fields(
    domain: str, catalog: "QueryCatalog | None" = None
) -> frozenset[str]:
    if catalog is not None:
        domain_catalog = catalog.domains.get(domain)
        if domain_catalog is not None:
            return frozenset(domain_catalog.relationship_filter_fields)
    if domain in {"contract", "license", "risk"}:
        return PROJECT_RELATIONSHIP_FILTER_FIELDS
    return frozenset()
