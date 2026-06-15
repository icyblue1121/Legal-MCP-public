"""Tencent Docs smart-table (腾讯文档智能表格) read-through connector (v0.5.9).

The second *online* read-through source (after Feishu Bitable), chosen as the v0.5
online sample: smart tables are the highest-value online source for Chinese legal
teams and their model is close to Feishu's, so the connector contract carries over.

It mirrors the Feishu connector's architecture deliberately, so the two share a
mental model and the gateway's authorization/audit stay uniform:

* **Config-driven catalog.** A declared config (``TencentDocsConfig``) maps gateway
  domains to a smart-table file/sheet and its reviewed columns — only declared
  columns are queryable.
* **Injectable client seam.** All HTTP/credential concerns live behind a
  ``SmartSheetClient`` protocol, so the translation logic is unit-tested against a
  fake client with no network. The concrete urllib client is a thin,
  separately-marked adapter (the only credential-bound part).

Authorization, record-scope, and audit stay in the gateway, around the connector —
never inside it. The connector returns raw source rows.

Live caveat: the exact smart-table filter/value envelope is a documented
integration point. The operator *translation* here (which Tencent operator each
gateway operator maps to, and the OR/range grouping) is pinned by unit tests; the
concrete client (marked no-cover) is what a deployment verifies against the live
API with real credentials, exactly as the Feishu client was.
"""

from __future__ import annotations

import json
import time
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from legal_mcp.connectors.base import (
    ConnectorDomain,
    ConnectorField,
    ConnectorFilter,
    ConnectorQuery,
    RecordScope,
    SourceTable,
    record_scope_from_dict,
)

# Gateway operator -> Tencent smart-table single-condition operator. Multi-value
# (``in``) and ranges (``date_between``) have no single operator and expand to
# grouped conditions (see ``_build_filter``). ``is_empty`` is the unary emptiness
# predicate.
_TENCENT_OPERATORS = {
    "eq": "equal",
    "contains": "contains",
    "is_empty": "isEmpty",
    "date_before": "less",
    "date_after": "greater",
}


@dataclass(frozen=True)
class TencentFieldConfig:
    name: str
    is_identity: bool = False
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class TencentDomainConfig:
    name: str
    sheet_id: str
    fields: tuple[TencentFieldConfig, ...]
    relationship_filter_fields: tuple[str, ...] = ()
    record_scope: RecordScope = RecordScope()


@dataclass(frozen=True)
class TencentDocsConfig:
    """Declared mapping from gateway domains to one smart-table file's sheets.

    Credentials (the access token) are NOT held here — they belong to the client,
    sourced from the environment, never committed.
    """

    file_id: str
    domains: tuple[TencentDomainConfig, ...]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TencentDocsConfig":
        file_id = data.get("file_id")
        if not file_id:
            raise ValueError("tencent_docs config requires 'file_id'")
        domains: list[TencentDomainConfig] = []
        for raw_domain in data.get("domains") or []:
            raw_domain = raw_domain or {}
            name = raw_domain.get("name")
            sheet_id = raw_domain.get("sheet_id")
            if not name or not sheet_id:
                raise ValueError("each tencent_docs domain requires 'name' and 'sheet_id'")
            fields = tuple(
                TencentFieldConfig(
                    name=field["name"],
                    is_identity=bool(field.get("is_identity", False)),
                    aliases=tuple(field.get("aliases") or ()),
                )
                for field in (raw_domain.get("fields") or [])
                if field and field.get("name")
            )
            domains.append(
                TencentDomainConfig(
                    name=name,
                    sheet_id=sheet_id,
                    fields=fields,
                    relationship_filter_fields=tuple(
                        raw_domain.get("relationship_filter_fields") or ()
                    ),
                    record_scope=record_scope_from_dict(raw_domain.get("record_scope")),
                )
            )
        return cls(file_id=file_id, domains=tuple(domains))


@runtime_checkable
class SmartSheetClient(Protocol):
    """Pure HTTP seam: list a sheet's records and its columns. No domain logic."""

    def list_records(
        self,
        *,
        sheet_id: str,
        field_names: list[str],
        filter: dict[str, Any] | None,
        page_size: int,
    ) -> list[dict[str, Any]]:
        """Return the field dict of each matching record (field name -> value)."""
        ...

    def list_fields(self, *, sheet_id: str) -> list[str]:
        """Return a sheet's column names, for config scaffolding / introspection."""
        ...


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list | tuple | set):
        return list(value)
    return [value]


def _needs_group(query_filter: ConnectorFilter) -> bool:
    """Whether a filter expands to a condition group (no flat single form)."""
    return (
        bool(query_filter.or_fields)
        or query_filter.operator in ("in", "date_between")
    )


class TencentDocsConnector:
    """Read-through connector over a Tencent Docs smart table, via an injected client."""

    name = "tencent_docs"

    def __init__(self, config: TencentDocsConfig, client: SmartSheetClient) -> None:
        self._config = config
        self._client = client

    def catalog(self) -> tuple[ConnectorDomain, ...]:
        domains: list[ConnectorDomain] = []
        for domain in self._config.domains:
            fields = tuple(
                ConnectorField(
                    domain=domain.name,
                    name=field.name,
                    is_identity=field.is_identity,
                    aliases=field.aliases,
                )
                for field in domain.fields
            )
            domains.append(
                ConnectorDomain(
                    name=domain.name,
                    table=domain.sheet_id,
                    fields=fields,
                    relationship_filter_fields=domain.relationship_filter_fields,
                    record_scope=domain.record_scope,
                )
            )
        return tuple(domains)

    def query(self, query: ConnectorQuery) -> list[dict[str, Any]]:
        domain = self._domain(query.domain)
        known_fields = {field.name for field in domain.fields}
        select_fields = [name for name in query.fields if name in known_fields]
        if not select_fields:
            raise ValueError("query has no known return fields")

        filter_fields = known_fields | set(domain.relationship_filter_fields)
        filter_body = self._build_filter(query.filters, filter_fields)

        records = self._client.list_records(
            sheet_id=domain.sheet_id,
            field_names=select_fields,
            filter=filter_body,
            page_size=int(query.limit),
        )
        return [
            {name: record[name] for name in select_fields if name in record}
            for record in records
        ]

    def describe_schema(self) -> tuple[SourceTable, ...]:
        return tuple(
            SourceTable(
                domain=domain.name,
                table=domain.sheet_id,
                fields=tuple(self._client.list_fields(sheet_id=domain.sheet_id)),
            )
            for domain in self._config.domains
        )

    def _build_filter(
        self, filters: tuple[ConnectorFilter, ...], filter_fields: set[str]
    ) -> dict[str, Any] | None:
        """Translate operator-aware filters into a smart-table filter body.

        Flat single-field eq/contains/is_empty/date_before/date_after become a flat
        ``and`` of conditions. A filter that needs OR (``in`` / ``or_fields``) or a
        range (``date_between``) becomes a one-level ``children`` group AND-ed in.
        """
        for query_filter in filters:
            names = query_filter.or_fields or (query_filter.field,)
            for name in names:
                if name not in filter_fields:
                    raise ValueError(f"unknown filter field: {name}")
        if not filters:
            return None
        if not any(_needs_group(query_filter) for query_filter in filters):
            return {
                "conjunction": "and",
                "conditions": [self._condition(query_filter) for query_filter in filters],
            }
        children: list[dict[str, Any]] = []
        for query_filter in filters:
            if query_filter.or_fields:
                children.append(
                    {
                        "conjunction": "or",
                        "conditions": [
                            self._condition_for(field, query_filter.operator, query_filter.value)
                            for field in query_filter.or_fields
                        ],
                    }
                )
            elif query_filter.operator == "in":
                children.append(
                    {
                        "conjunction": "or",
                        "conditions": [
                            self._condition_for(query_filter.field, "eq", value)
                            for value in _as_list(query_filter.value)
                        ],
                    }
                )
            elif query_filter.operator == "date_between":
                start, end = query_filter.value
                children.append(
                    {
                        "conjunction": "and",
                        "conditions": [
                            self._condition_for(query_filter.field, "date_after_eq", start),
                            self._condition_for(query_filter.field, "date_before_eq", end),
                        ],
                    }
                )
            else:
                children.append(
                    {"conjunction": "and", "conditions": [self._condition(query_filter)]}
                )
        return {"conjunction": "and", "children": children}

    @classmethod
    def _condition(cls, query_filter: ConnectorFilter) -> dict[str, Any]:
        return cls._condition_for(query_filter.field, query_filter.operator, query_filter.value)

    @staticmethod
    def _condition_for(field: str, operator: str, value: Any) -> dict[str, Any]:
        # The two inclusive range bounds reuse the ordered operators.
        extra = {"date_after_eq": "greaterEqual", "date_before_eq": "lessEqual"}
        tencent_operator = _TENCENT_OPERATORS.get(operator) or extra.get(operator)
        if tencent_operator is None:
            raise ValueError(f"tencent_docs connector cannot push down operator: {operator}")
        condition_value: list[Any] = [] if operator == "is_empty" else [str(value)]
        return {"field_name": field, "operator": tencent_operator, "value": condition_value}

    def _domain(self, name: str) -> TencentDomainConfig:
        for domain in self._config.domains:
            if domain.name == name:
                return domain
        raise ValueError(f"unknown domain: {name}")


# --- Concrete HTTP client (the only credential-bound part) -------------------

_DEFAULT_BASE_URL = "https://docs.qq.com"
_MAX_PAGE_SIZE = 1000

Transport = Callable[[str, str, dict[str, str], Any], dict[str, Any]]


class TencentDocsApiError(RuntimeError):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"tencent docs api error {code}: {message}")
        self.code = code
        self.message = message


def _urllib_transport(
    method: str, url: str, headers: dict[str, str], payload: Any
) -> dict[str, Any]:  # pragma: no cover - network I/O, verified against the live API
    import urllib.request

    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


class TencentDocsClient:  # pragma: no cover - credential-bound, verified live
    """``SmartSheetClient`` over Tencent Docs via stdlib urllib.

    Holds the access token and follows pagination up to the requested page size.
    Authorization stays in the gateway; this is a dumb read pipe. Verifying it needs
    a real access token and is the deployment's integration step.
    """

    def __init__(
        self,
        *,
        access_token: str,
        file_id: str,
        base_url: str = _DEFAULT_BASE_URL,
        transport: Transport | None = None,
        now: Callable[[], float] | None = None,
    ) -> None:
        self._access_token = access_token
        self._file_id = file_id
        self._base_url = base_url.rstrip("/")
        self._transport = transport or _urllib_transport
        self._now = now or time.monotonic

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json; charset=utf-8",
        }

    def list_records(
        self,
        *,
        sheet_id: str,
        field_names: list[str],
        filter: dict[str, Any] | None,
        page_size: int,
    ) -> list[dict[str, Any]]:
        limit = int(page_size)
        per_page = min(limit, _MAX_PAGE_SIZE)
        path = f"/openapi/smartsheet/v1/files/{self._file_id}/sheets/{sheet_id}/records/query"
        collected: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            body: dict[str, Any] = {"field_names": list(field_names), "page_size": per_page}
            if filter is not None:
                body["filter"] = filter
            if page_token:
                body["page_token"] = page_token
            data = self._post(f"{self._base_url}{path}", body)
            for item in data.get("records") or []:
                collected.append(item.get("fields") or {})
                if len(collected) >= limit:
                    return collected[:limit]
            page_token = data.get("next_page_token")
            if not data.get("has_more") or not page_token:
                break
        return collected[:limit]

    def list_fields(self, *, sheet_id: str) -> list[str]:
        path = f"/openapi/smartsheet/v1/files/{self._file_id}/sheets/{sheet_id}/fields"
        url = f"{self._base_url}{path}"
        resp = self._transport("GET", url, self._headers(), None)
        self._check(resp)
        data = resp.get("data") or {}
        return [str(f.get("field_name")) for f in (data.get("fields") or []) if f.get("field_name")]

    def _post(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        resp = self._transport("POST", url, self._headers(), body)
        self._check(resp)
        return resp.get("data") or {}

    @staticmethod
    def _check(resp: dict[str, Any]) -> None:
        code = resp.get("ret", resp.get("code", 0))
        if code:
            raise TencentDocsApiError(int(code), str(resp.get("msg", "")))
