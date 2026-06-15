from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from legal_mcp.planner import plan_query
from legal_mcp.query_catalog import QueryCatalog
from legal_mcp.query_plan import SUPPORTED_OPERATIONS, SUPPORTED_OPERATORS, QueryFilter, QueryPlan
from legal_mcp.tool_catalog import agent_capabilities


@dataclass(frozen=True)
class AgentToolDecision:
    tool_name: str
    arguments: dict[str, Any]
    reason: str

    def replace(self, **changes: Any) -> "AgentToolDecision":
        return replace(self, **changes)


def route_question(question: str) -> AgentToolDecision:
    plan = plan_query(question)
    arguments = dict(plan.arguments)
    arguments.setdefault("rationale", f"agent_query: {plan.reason}")
    arguments.setdefault("source_client", "legal-mcp-agent")
    return AgentToolDecision(
        tool_name=plan.tool_name,
        arguments=arguments,
        reason=plan.reason,
    )


def query_plan_from_model_intent(
    intent: dict[str, Any],
    catalog: QueryCatalog,
) -> QueryPlan | None:
    """Normalize a server-side model intent into a constrained QueryPlan.

    Tolerant by design: the model may return filters as a list of
    ``{field, operator, value}`` objects OR as a flat ``{field: value}`` mapping,
    may use operation synonyms (``read``/``get``/``select`` ...), and may use
    Chinese or alias field names. Anything that cannot be recovered to a
    registered field is kept as-is so the catalog validator can report it,
    rather than silently dropping it. Returns ``None`` only when the domain is
    not a registered domain.
    """
    domain = intent.get("domain")
    if not isinstance(domain, str) or domain not in catalog.domains:
        return None

    filters = _normalize_filters(intent.get("filters"), domain, catalog)
    return_fields = _normalize_return_fields(intent.get("return_fields"), domain, catalog)
    if not return_fields and domain != "cross_domain":
        domain_catalog = catalog.domains.get(domain)
        if domain_catalog is not None:
            return_fields = sorted(domain_catalog.identity_fields) or sorted(domain_catalog.fields)[:1]

    data_source = intent.get("data_source")
    return QueryPlan(
        domain=domain,
        operation=_normalize_operation(intent.get("operation")),
        filters=filters,
        return_fields=return_fields,
        limit=_normalize_limit(intent.get("limit")),
        data_source=data_source.strip()
        if isinstance(data_source, str) and data_source.strip()
        else None,
    )


def _normalize_operation(raw: Any) -> str:
    # Execution dispatches on domain, not operation, so an unrecognized
    # operation (e.g. the model's "read") safely normalizes to "search".
    if isinstance(raw, str) and raw.strip().lower() in SUPPORTED_OPERATIONS:
        return raw.strip().lower()
    return "search"


def _normalize_limit(raw: Any) -> int:
    if isinstance(raw, bool):
        return 20
    if isinstance(raw, int):
        limit = raw
    else:
        try:
            limit = int(raw)
        except (TypeError, ValueError):
            return 20
    return max(0, min(limit, 100))


def _normalize_filters(raw: Any, domain: str, catalog: QueryCatalog) -> list[QueryFilter]:
    filters: list[QueryFilter] = []
    if isinstance(raw, dict):
        # Flat {field: value} mapping -> eq filters.
        for key, value in raw.items():
            if not isinstance(key, str):
                continue
            field = catalog.resolve_field(domain, key) or key
            filters.append(QueryFilter(field=field, operator="eq", value=value))
        return filters
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            field = item.get("field")
            if not isinstance(field, str) or not field.strip():
                continue
            resolved = catalog.resolve_field(domain, field) or field
            operator = item.get("operator")
            if not isinstance(operator, str) or operator not in SUPPORTED_OPERATORS:
                operator = "eq"
            filters.append(QueryFilter(field=resolved, operator=operator, value=item.get("value")))
        return filters
    return filters


def _normalize_return_fields(raw: Any, domain: str, catalog: QueryCatalog) -> list[str]:
    if not isinstance(raw, list):
        return []
    fields: list[str] = []
    for entry in raw:
        if not isinstance(entry, str) or not entry.strip():
            continue
        fields.append(catalog.resolve_field(domain, entry) or entry)
    return fields


def validate_agent_decision(decision: AgentToolDecision) -> dict[str, Any]:
    allowed = {capability.name: capability for capability in agent_capabilities()}
    capability = allowed.get(decision.tool_name)
    if capability is None:
        return _agent_error(
            "agent_tool_not_allowed",
            "agent selected a tool outside its read capability boundary",
        )

    fields = decision.arguments.get("fields")
    if fields is not None:
        if not isinstance(fields, list) or not all(isinstance(field, str) for field in fields):
            return _agent_error("agent_field_not_allowed", "fields must be a list of strings")
        unknown_fields = sorted(set(fields) - set(capability.return_fields))
        if unknown_fields:
            return _agent_error(
                "agent_field_not_allowed",
                "agent selected fields outside the tool capability",
                details={"fields": unknown_fields},
            )

    if capability.requires_fields and "fields" not in decision.arguments:
        return _agent_error("agent_fields_required", "agent must request explicit fields")

    return {"ok": True}


def clarify_result(question: str) -> dict[str, Any]:
    return {
        "clarification": {
            "question": question,
            "message": "请明确项目、合同、证照或字段范围，以便按最小披露原则查询。",
        }
    }


def _agent_error(
    code: str,
    message: str,
    *,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details or {}}}
