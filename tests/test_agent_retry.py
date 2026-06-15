from __future__ import annotations

from legal_mcp.agent_retry import is_retryable_plan_error


def test_retry_policy_allows_plan_shape_and_catalog_errors() -> None:
    for code in [
        "invalid_json",
        "unsupported_domain",
        "unsupported_operation",
        "unsupported_operator",
        "unknown_return_field",
        "unknown_filter_field",
        "invalid_return_field",
        "unsupported_field",
    ]:
        assert is_retryable_plan_error(code)


def test_retry_policy_rejects_authorization_and_access_errors() -> None:
    for code in [
        "filter_field_access_denied",
        "return_field_access_denied",
        "access_denied",
        "missing_rationale",
    ]:
        assert not is_retryable_plan_error(code)
