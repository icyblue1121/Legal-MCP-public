"""Retry policy helpers for constrained agent query planning."""

from __future__ import annotations

from legal_mcp.ai_provider import AIMessage
from legal_mcp.query_catalog import QueryCatalog, catalog_context_for_prompt

RETRYABLE_PLAN_ERRORS = frozenset(
    {
        "invalid_json",
        "unsupported_domain",
        "unsupported_operation",
        "unsupported_operator",
        "unknown_return_field",
        "unknown_filter_field",
        "invalid_return_field",
        "invalid_filter_field",
        "unsupported_field",
    }
)


def is_retryable_plan_error(error_code: str | None) -> bool:
    return error_code in RETRYABLE_PLAN_ERRORS


def repair_messages(
    *,
    catalog: QueryCatalog,
    question: str,
    previous_response: str,
    error_code: str,
    error_message: str,
) -> list[AIMessage]:
    return [
        AIMessage(
            role="system",
            content=(
                "You are repairing a Legal-MCP constrained JSON QueryPlan. "
                "Return exactly one JSON object and no prose. Use only the catalog. "
                f"Catalog: {catalog_context_for_prompt(catalog)}"
            ),
        ),
        AIMessage(
            role="user",
            content=(
                f"Question: {question}\n"
                f"Previous response: {previous_response}\n"
                f"Validation error code: {error_code}\n"
                f"Validation error message: {error_message}\n"
                "Return a corrected JSON object."
            ),
        ),
    ]
