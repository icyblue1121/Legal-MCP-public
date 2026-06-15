"""Constrained query plan types for service-side retrieval."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

SUPPORTED_DOMAINS = frozenset({"project", "contract", "license", "seal", "cross_domain"})
SUPPORTED_OPERATIONS = frozenset({"lookup", "search", "list", "aggregate"})
SUPPORTED_OPERATORS = frozenset(
    {"eq", "contains", "in", "is_empty", "date_before", "date_after", "date_between"}
)
MAX_LIMIT = 100

# A *virtual* filter field (v0.4.8). It is not a real column: a plan filter on
# ``identity`` is expanded — at retrieval time, in both execution paths — into an
# OR across the domain's identity fields (e.g. ``project_code`` / ``name``), so a
# bare project token ("MOON" / "月之子" / "nova" / "山海") matches a code *or* a
# name without the planner having to guess which single field to use. It is only
# valid on a domain that declares identity fields, and it is exempt from the field
# gate exactly as the identity fields it expands to are (it never widens disclosure
# and cannot reach a non-identity column). See ``legal_mcp.identity_match``.
VIRTUAL_IDENTITY_FIELD = "identity"


@dataclass(frozen=True)
class QueryFilter:
    field: str
    operator: str
    value: Any = None


@dataclass(frozen=True)
class QueryPlan:
    domain: str
    operation: str
    filters: list[QueryFilter]
    return_fields: list[str]
    limit: int = 20
    # Optional pinned data source (multi-source domains). ``None`` = the domain's
    # configured source order (primary first, fallbacks on empty). A name is only
    # meaningful when the deployment declares several sources for the domain; an
    # unknown name fails closed at execution (``unknown_data_source``).
    data_source: str | None = None


@dataclass(frozen=True)
class PlanValidationResult:
    ok: bool
    error_code: str | None = None
    message: str | None = None


def validate_query_plan(
    plan: QueryPlan, allowed_domains: "frozenset[str] | set[str] | None" = None
) -> PlanValidationResult:
    """Structural + enum validation. ``allowed_domains`` overrides the built-in
    ``SUPPORTED_DOMAINS`` whitelist so a connector-registered domain (one the
    catalog knows about) is not rejected by the static legacy set (v0.4.0 §A)."""
    domains = SUPPORTED_DOMAINS if allowed_domains is None else allowed_domains
    if plan.domain not in domains:
        return _invalid("unsupported_domain", "query domain is not supported")
    if plan.operation not in SUPPORTED_OPERATIONS:
        return _invalid("unsupported_operation", "query operation is not supported")
    if not isinstance(plan.limit, int) or plan.limit < 0 or plan.limit > MAX_LIMIT:
        return _invalid("invalid_limit", "query limit must be between 0 and 100")
    if plan.data_source is not None and (
        not isinstance(plan.data_source, str) or not plan.data_source.strip()
    ):
        return _invalid("invalid_data_source", "data_source must be a non-empty string")
    if "*" in plan.return_fields:
        return _invalid("wildcard_fields_not_allowed", "return fields must be explicit")
    for field in plan.return_fields:
        if not isinstance(field, str) or not field.strip():
            return _invalid("invalid_return_field", "return fields must be non-empty strings")
    for query_filter in plan.filters:
        if query_filter.operator not in SUPPORTED_OPERATORS:
            return _invalid("unsupported_operator", "query filter operator is not supported")
        if not isinstance(query_filter.field, str) or not query_filter.field.strip():
            return _invalid("invalid_filter_field", "filter fields must be non-empty strings")
    return PlanValidationResult(ok=True)


def _invalid(error_code: str, message: str) -> PlanValidationResult:
    return PlanValidationResult(ok=False, error_code=error_code, message=message)
