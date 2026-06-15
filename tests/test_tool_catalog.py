from __future__ import annotations

from legal_mcp.tool_catalog import (
    CATALOG,
    ToolCapability,
    agent_capabilities,
    capability_by_name,
    tool_definitions,
)


def test_catalog_entries_have_machine_readable_capabilities() -> None:
    get_project = CATALOG["get_project_fields"]

    assert isinstance(get_project, ToolCapability)
    assert get_project.data_domain == "project"
    assert get_project.operation == "read"
    assert "website" in get_project.return_fields
    assert get_project.requires_project_scope is True


def test_tool_definitions_include_catalog_metadata() -> None:
    definitions = tool_definitions(internal_debug=True)
    names = {tool["name"] for tool in definitions}
    resolve_project = next(
        tool for tool in definitions if tool["name"] == "resolve_project"
    )
    get_project = next(
        tool for tool in definitions if tool["name"] == "get_project_fields"
    )
    list_licenses = next(
        tool for tool in definitions if tool["name"] == "list_project_licenses"
    )

    assert "describe_my_access" in names
    assert "permissions" in resolve_project["description"]
    assert get_project["x-legal-mcp"]["data_domain"] == "project"
    assert "website" in get_project["x-legal-mcp"]["return_fields"]
    assert list_licenses["x-legal-mcp"]["data_domain"] == "license"
    assert "actual_operator" in list_licenses["x-legal-mcp"]["return_fields"]


def test_read_capability_declares_minimum_disclosure_fields() -> None:
    capability = capability_by_name("get_project_fields")

    assert capability.operation == "read"
    assert capability.side_effect == "none"
    assert capability.agent_allowed is True
    assert capability.requires_fields is True
    assert "website" in capability.return_fields


def test_agent_capabilities_only_include_safe_read_tools_for_v14() -> None:
    capabilities = agent_capabilities()
    names = [capability.name for capability in capabilities]

    assert "get_project_fields" in names
    assert "list_project_licenses" in names
    assert "agent_query" not in names
    assert all(capability.operation == "read" for capability in capabilities)
    assert all(capability.side_effect == "none" for capability in capabilities)


def test_public_catalog_can_expose_only_agent_query() -> None:
    names = [tool["name"] for tool in tool_definitions(public_agent_only=True)]

    assert names == ["agent_query"]


def test_production_tool_definitions_expose_only_agent_query() -> None:
    names = [tool["name"] for tool in tool_definitions(public_agent_only=False)]

    assert names == ["agent_query"]


def test_internal_debug_tool_definitions_keep_structured_query_available() -> None:
    names = [tool["name"] for tool in tool_definitions(internal_debug=True)]

    assert "agent_query" in names
    assert "structured_query" in names
    assert "list_project_licenses" in names


def test_internal_catalog_keeps_legacy_tools_for_tests_only() -> None:
    names = [
        tool["name"]
        for tool in tool_definitions(public_agent_only=False, internal_debug=True)
    ]

    assert "get_project_fields" in names
    assert "list_project_contracts" in names
    assert "list_project_licenses" in names
    assert "propose_project_update" not in names
