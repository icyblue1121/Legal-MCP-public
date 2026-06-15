"""Concrete Feishu HTTP client (pivot v0.3).

The client is the only credential-bound part of the connector. These tests pin
its behaviour against a *fake transport* (no network): token fetch + caching +
refresh, the search request shape, pagination, and the error model. Verifying it
against live Feishu still needs real app_id/app_secret — see the connector docs.
"""

from __future__ import annotations

from typing import Any

import pytest

from legal_mcp.connectors.feishu_bitable import (
    BitableClient,
    FeishuApiError,
    FeishuClient,
)


def _token(expire: int = 7200) -> dict[str, Any]:
    return {"code": 0, "msg": "ok", "tenant_access_token": "t-abc", "expire": expire}


def _search(items: list[dict[str, Any]], *, has_more: bool = False, page_token: str = "") -> dict[str, Any]:
    return {
        "code": 0,
        "data": {
            "items": [{"record_id": f"r{i}", "fields": f} for i, f in enumerate(items)],
            "has_more": has_more,
            "page_token": page_token,
        },
    }


class _FakeTransport:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    def __call__(self, method: str, url: str, headers: dict[str, str], payload: Any) -> dict[str, Any]:
        self.requests.append({"method": method, "url": url, "headers": headers, "payload": payload})
        return self._responses.pop(0)


class _Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def _client(transport: _FakeTransport, clock: _Clock | None = None) -> FeishuClient:
    return FeishuClient(
        app_id="cli_x",
        app_secret="secret",
        app_token="bascnX",
        transport=transport,
        now=clock or _Clock(),
    )


def test_client_satisfies_bitable_client_protocol() -> None:
    assert isinstance(_client(_FakeTransport([])), BitableClient)


def test_client_fetches_token_then_searches() -> None:
    transport = _FakeTransport([_token(), _search([{"name": "Demo"}])])
    client = _client(transport)

    rows = client.search_records(
        table_id="tblP",
        field_names=["name"],
        filter={"conjunction": "and", "conditions": [{"field_name": "name", "operator": "is", "value": ["Demo"]}]},
        page_size=20,
    )
    assert rows == [{"name": "Demo"}]

    token_req = transport.requests[0]
    assert token_req["method"] == "POST"
    assert token_req["url"].endswith("/open-apis/auth/v3/tenant_access_token/internal")
    assert token_req["payload"] == {"app_id": "cli_x", "app_secret": "secret"}

    search_req = transport.requests[1]
    assert search_req["method"] == "POST"
    assert "/open-apis/bitable/v1/apps/bascnX/tables/tblP/records/search" in search_req["url"]
    assert "page_size=20" in search_req["url"]
    assert search_req["headers"]["Authorization"] == "Bearer t-abc"
    assert search_req["payload"]["field_names"] == ["name"]
    assert search_req["payload"]["automatic_fields"] is False
    assert search_req["payload"]["filter"]["conjunction"] == "and"


def test_client_omits_filter_when_none() -> None:
    transport = _FakeTransport([_token(), _search([])])
    _client(transport).search_records(
        table_id="tblP", field_names=["name"], filter=None, page_size=20
    )
    assert "filter" not in transport.requests[1]["payload"]


def test_client_caches_token_across_calls() -> None:
    transport = _FakeTransport([_token(), _search([]), _search([])])
    client = _client(transport)
    client.search_records(table_id="tblP", field_names=["name"], filter=None, page_size=20)
    client.search_records(table_id="tblP", field_names=["name"], filter=None, page_size=20)

    token_reqs = [r for r in transport.requests if r["url"].endswith("/internal")]
    assert len(token_reqs) == 1


def test_client_refreshes_token_after_expiry() -> None:
    clock = _Clock()
    transport = _FakeTransport([_token(expire=7200), _search([]), _token(expire=7200), _search([])])
    client = _client(transport, clock)
    client.search_records(table_id="tblP", field_names=["name"], filter=None, page_size=20)
    clock.t += 7200  # advance past expiry
    client.search_records(table_id="tblP", field_names=["name"], filter=None, page_size=20)

    token_reqs = [r for r in transport.requests if r["url"].endswith("/internal")]
    assert len(token_reqs) == 2


def test_client_paginates_until_limit() -> None:
    transport = _FakeTransport(
        [
            _token(),
            _search([{"name": "a"}, {"name": "b"}], has_more=True, page_token="PT2"),
            _search([{"name": "c"}, {"name": "d"}], has_more=False),
        ]
    )
    rows = _client(transport).search_records(
        table_id="tblP", field_names=["name"], filter=None, page_size=3
    )
    assert [r["name"] for r in rows] == ["a", "b", "c"]  # truncated to limit

    search_reqs = [r for r in transport.requests if "records/search" in r["url"]]
    assert len(search_reqs) == 2
    assert "page_token=PT2" in search_reqs[1]["url"]


def test_client_raises_on_nonzero_code() -> None:
    transport = _FakeTransport([{"code": 99991663, "msg": "app ticket invalid"}])
    with pytest.raises(FeishuApiError):
        _client(transport).search_records(
            table_id="tblP", field_names=["name"], filter=None, page_size=20
        )
