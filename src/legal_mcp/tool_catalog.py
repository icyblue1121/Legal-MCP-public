"""Machine-readable MCP tool catalog."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

PROJECT_FIELDS = (
    "project_code",
    "name",
    "stage",
    "legal_bp",
    "department",
    "release_team",
    "contact_person",
    "website",
    "notes",
)

CONTRACT_FIELDS = (
    "contract_number",
    "title",
    "counterparty",
    "company_entity",
    "currency",
    "total_amount",
    "signed_date",
    "expiry_date",
    "payment_terms",
    "handler",
    "income_expense_type",
    "summary",
)

LICENSE_FIELDS = (
    "license_type",
    "identifier",
    "entity_name",
    "issuer",
    "approval_number",
    "rights_holder",
    "copyright_holder",
    "operating_entity",
    "actual_operator",
    "authorization_relation",
    "expiry_date",
    "notes",
)

SEAL_FIELDS = (
    "company",
    "seal_type",
    "custodian",
    "storage_location",
    "status",
    "borrower",
    "borrowed_at",
    "borrow_reason",
    "expected_return_at",
    "actual_return_at",
)

ToolOperation = Literal["read", "propose_write", "write", "admin"]
ToolSideEffect = Literal["none", "proposal", "write", "admin"]


@dataclass(frozen=True)
class ToolCapability:
    name: str
    description: str
    data_domain: str
    operation: ToolOperation
    filters: tuple[str, ...]
    return_fields: tuple[str, ...]
    requires_project_scope: bool
    result_kind: str
    default_limit: int | None = None
    max_limit: int | None = None
    side_effect: ToolSideEffect = "none"
    agent_allowed: bool = True
    requires_fields: bool = False
    requires_human_approval: bool = False


CATALOG: dict[str, ToolCapability] = {
    "plan_query": ToolCapability(
        name="plan_query",
        description="Plan a user question into one minimum-disclosure tool call.",
        data_domain="planner",
        operation="read",
        filters=("question",),
        return_fields=("tool_name", "arguments", "reason"),
        requires_project_scope=False,
        result_kind="single",
    ),
    "resolve_project": ToolCapability(
        name="resolve_project",
        description=(
            "Resolve a project by code, name, or alias. Do not use for user "
            "permissions; use describe_my_access for permission questions."
        ),
        data_domain="project",
        operation="read",
        filters=("query",),
        return_fields=("project_code", "name"),
        requires_project_scope=False,
        result_kind="single_or_candidates",
    ),
    "describe_my_access": ToolCapability(
        name="describe_my_access",
        description=(
            "Query the current user's permissions: visible projects, accessible "
            "project codes, and fields the user can read."
        ),
        data_domain="access",
        operation="read",
        filters=(),
        return_fields=("projects", "fields"),
        requires_project_scope=False,
        result_kind="list",
    ),
    "get_project_fields": ToolCapability(
        name="get_project_fields",
        description="Return selected project fields after field-level authorization.",
        data_domain="project",
        operation="read",
        filters=("project_id_or_name", "fields"),
        return_fields=PROJECT_FIELDS,
        requires_project_scope=True,
        result_kind="single",
        requires_fields=True,
    ),
    "list_project_contracts": ToolCapability(
        name="list_project_contracts",
        description="List contracts for a project with selected contract fields.",
        data_domain="contract",
        operation="read",
        filters=("project_id_or_name", "fields", "limit"),
        return_fields=CONTRACT_FIELDS,
        requires_project_scope=True,
        result_kind="list",
        default_limit=20,
        max_limit=100,
        requires_fields=True,
    ),
    "list_project_licenses": ToolCapability(
        name="list_project_licenses",
        description="List licenses for a project with selected license fields.",
        data_domain="license",
        operation="read",
        filters=("project_id_or_name", "fields", "limit"),
        return_fields=LICENSE_FIELDS,
        requires_project_scope=True,
        result_kind="list",
        default_limit=20,
        max_limit=100,
        requires_fields=True,
    ),
    "get_contract_fields": ToolCapability(
        name="get_contract_fields",
        description="Return selected fields for one contract.",
        data_domain="contract",
        operation="read",
        filters=("contract_number", "fields"),
        return_fields=CONTRACT_FIELDS,
        requires_project_scope=True,
        result_kind="single",
        requires_fields=True,
    ),
    "agent_query": ToolCapability(
        name="agent_query",
        description="Ask the service-side Legal-MCP agent a legal project question.",
        data_domain="agent",
        operation="read",
        filters=("question", "thread_id"),
        return_fields=("answer", "thread_id", "tool_calls", "status"),
        requires_project_scope=False,
        result_kind="single",
        side_effect="none",
        agent_allowed=False,
    ),
    "agent_write": ToolCapability(
        name="agent_write",
        description="Draft a write proposal for human review; does not mutate data.",
        data_domain="agent",
        operation="propose_write",
        filters=("instruction",),
        return_fields=("proposal",),
        requires_project_scope=False,
        result_kind="single",
        side_effect="proposal",
        agent_allowed=False,
        requires_human_approval=True,
    ),
    "structured_query": ToolCapability(
        name="structured_query",
        description="Run a constrained structured read query through the service-side graph.",
        data_domain="agent",
        operation="read",
        filters=("query",),
        return_fields=("answer", "result", "thread_id", "status"),
        requires_project_scope=False,
        result_kind="single",
        side_effect="none",
        agent_allowed=False,
    ),
    "propose_project_update": ToolCapability(
        name="propose_project_update",
        description="Draft a project update proposal for human review; does not write data.",
        data_domain="project",
        operation="propose_write",
        filters=("project_id_or_name", "changes"),
        return_fields=("proposal_id", "diff", "requires_approval"),
        requires_project_scope=True,
        result_kind="single",
        side_effect="proposal",
        agent_allowed=False,
        requires_human_approval=True,
    ),
}


def capability_by_name(name: str) -> ToolCapability:
    return CATALOG[name]


def agent_capabilities() -> list[ToolCapability]:
    return [
        capability
        for capability in CATALOG.values()
        if capability.agent_allowed
        and capability.operation == "read"
        and capability.side_effect == "none"
    ]


def tool_definitions(
    *,
    public_agent_only: bool = False,
    internal_debug: bool = False,
) -> list[dict[str, Any]]:
    if internal_debug:
        capabilities = [
            capability
            for capability in CATALOG.values()
            if capability.name != "propose_project_update"
        ]
    else:
        capabilities = [CATALOG["agent_query"]]
    return [_tool_definition(capability) for capability in capabilities]


def _tool_definition(capability: ToolCapability) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "rationale": {"type": "string"},
        "source_client": {"type": "string"},
    }
    required = ["rationale"]
    for filter_name in capability.filters:
        if filter_name == "fields":
            properties["fields"] = {
                "type": "array",
                "items": {"type": "string", "enum": sorted(capability.return_fields)},
            }
            required.append("fields")
        elif filter_name == "limit":
            properties["limit"] = {
                "type": "integer",
                "default": capability.default_limit,
                "maximum": capability.max_limit,
            }
        elif filter_name == "thread_id":
            properties[filter_name] = {"type": "string"}
        elif filter_name == "query":
            properties[filter_name] = {"type": "object"}
            required.append(filter_name)
        else:
            properties[filter_name] = {"type": "string"}
            required.append(filter_name)
    return {
        "name": capability.name,
        "description": capability.description,
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "required": required,
        },
        "x-legal-mcp": {
            "data_domain": capability.data_domain,
            "operation": capability.operation,
            "filters": list(capability.filters),
            "return_fields": list(capability.return_fields),
            "requires_project_scope": capability.requires_project_scope,
            "result_kind": capability.result_kind,
            "default_limit": capability.default_limit,
            "max_limit": capability.max_limit,
            "side_effect": capability.side_effect,
            "agent_allowed": capability.agent_allowed,
            "requires_fields": capability.requires_fields,
            "requires_human_approval": capability.requires_human_approval,
        },
    }
