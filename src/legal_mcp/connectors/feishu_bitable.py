"""Feishu (Lark) Bitable read-through connector (pivot v0.3, plan §7 v0.3).

The first *real* read-through source: it proves the gateway can read an external
data source directly, with business data owned by Feishu, not by this project.

Design (mirrors the connector contract in ``base.py``):

* **Config-driven catalog.** Unlike the SQLite demo, a generic Bitable cannot be
  introspected into gateway domains/fields/aliases without operator intent. The
  catalog comes from a declared config (``FeishuBitableConfig``) — a reviewable,
  git-committable artifact, and a security posture: only declared fields are
  queryable. (Validating config against the live Feishu ``fields`` API is a
  reserved slot, not done in v0.3.)
* **Injectable client seam.** All HTTP/credential concerns live behind a
  ``BitableClient`` protocol, so the connector's translation logic is unit-tested
  against a fake client with no network. The concrete urllib-based client is a
  thin, separately-marked adapter (the only part needing real credentials).

Authorization, record-scope, and audit stay in the gateway, around the connector
— never inside it. The connector returns raw source rows.
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

# Plan operator -> Feishu ``records/search`` condition operator. Feishu has no
# native ``in`` (its API marks it "暂未支持"), so ``in`` is expanded into a
# one-level ``children`` OR-group instead (see ``_build_filter``). ``contains`` is
# Feishu's native substring match — this is what restores fuzzy name search.
#
# v0.5.1 adds the emptiness and date comparisons that the SQLite path already had.
# ``is_empty`` maps to Feishu's native ``isEmpty`` (empty value array). The date
# comparisons map to Feishu's ordered operators; ``date_between`` has no single
# Feishu operator, so it expands to ``isGreaterEqual`` AND ``isLessEqual`` in a
# ``children`` AND-group (see ``_build_filter``).
#
# Live caveat: for Bitable *DateTime* fields, Feishu expects the comparison value
# in its own envelope (``["ExactDate", "<ms-timestamp>"]`` or relative tokens),
# not a bare ISO string. The planner normalizes "上个月"→absolute dates; mapping
# that absolute date to Feishu's timestamp envelope is a deployment concern and is
# not done here — these translations are exercised by structural unit tests.
_FEISHU_OPERATORS = {
    "eq": "is",
    "contains": "contains",
    "is_empty": "isEmpty",
    "date_before": "isLess",
    "date_after": "isGreater",
}


@dataclass(frozen=True)
class FeishuFieldConfig:
    """A queryable Bitable field, declared by the operator."""

    name: str
    is_identity: bool = False
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class FeishuDomainConfig:
    """One gateway domain backed by one Bitable table."""

    name: str
    table_id: str
    fields: tuple[FeishuFieldConfig, ...]
    relationship_filter_fields: tuple[str, ...] = ()
    record_scope: RecordScope = RecordScope()


@dataclass(frozen=True)
class FeishuBitableConfig:
    """Declared mapping from gateway domains to one Bitable app's tables.

    Credentials (``app_id`` / ``app_secret``) are intentionally NOT held here:
    they belong to the client, sourced from the environment, never committed.
    """

    app_token: str
    domains: tuple[FeishuDomainConfig, ...]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FeishuBitableConfig":
        app_token = data.get("app_token")
        if not app_token:
            raise ValueError("feishu config requires 'app_token'")
        domains: list[FeishuDomainConfig] = []
        for raw_domain in data.get("domains") or []:
            raw_domain = raw_domain or {}
            name = raw_domain.get("name")
            table_id = raw_domain.get("table_id")
            if not name or not table_id:
                raise ValueError("each feishu domain requires 'name' and 'table_id'")
            fields = tuple(
                FeishuFieldConfig(
                    name=field["name"],
                    is_identity=bool(field.get("is_identity", False)),
                    aliases=tuple(field.get("aliases") or ()),
                )
                for field in (raw_domain.get("fields") or [])
                if field and field.get("name")
            )
            domains.append(
                FeishuDomainConfig(
                    name=name,
                    table_id=table_id,
                    fields=fields,
                    relationship_filter_fields=tuple(
                        raw_domain.get("relationship_filter_fields") or ()
                    ),
                    record_scope=record_scope_from_dict(raw_domain.get("record_scope")),
                )
            )
        return cls(app_token=app_token, domains=tuple(domains))


@runtime_checkable
class BitableClient(Protocol):
    """Pure HTTP seam: token auth + a single search request. No domain logic."""

    def search_records(
        self,
        *,
        table_id: str,
        field_names: list[str],
        filter: dict[str, Any] | None,
        page_size: int,
    ) -> list[dict[str, Any]]:
        """Return the ``fields`` dict of each matching record (field name -> value)."""
        ...

    def list_fields(self, *, table_id: str) -> list[str]:
        """Return a table's column names, for config scaffolding (v0.4.0 §D)."""
        ...


def _as_list(value: Any) -> list[Any]:
    """The values of an ``in`` filter as a list (a bare scalar becomes a singleton)."""
    if isinstance(value, list | tuple | set):
        return list(value)
    return [value]


def _needs_child_group(query_filter: ConnectorFilter) -> bool:
    """Whether a filter expands to a ``children`` group (no flat Feishu form).

    ``or_fields`` and ``in`` become OR-groups; ``date_between`` becomes an AND-group
    of two ordered conditions (``isGreaterEqual`` .. ``isLessEqual``).
    """
    return (
        bool(query_filter.or_fields)
        or query_filter.operator in ("in", "date_between")
    )


def _flatten_cell(value: Any) -> Any:
    """Normalize a Feishu cell to a scalar.

    Feishu's ``records/search`` returns text cells as rich-text *segment lists*
    (e.g. ``[{"text": "MOON", "type": "text"}]``), not bare strings. Left as-is,
    a list breaks the gateway's record-scope post-filter (``str(list) != "MOON"``,
    so every row is dropped) and would disclose the raw envelope instead of the
    text. Flatten segment lists (and the occasional ``{"text": ...}`` dict) to a
    plain string; numbers and already-scalar values pass through unchanged.
    """
    if isinstance(value, list):
        return "".join(
            str(seg.get("text", "")) if isinstance(seg, dict) else str(seg)
            for seg in value
        )
    if isinstance(value, dict):
        return str(value.get("text", ""))
    return value


class FeishuBitableConnector:
    """Read-through connector over a Feishu Bitable, via an injected client."""

    name = "feishu_bitable"

    def __init__(self, config: FeishuBitableConfig, client: BitableClient) -> None:
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
                    table=domain.table_id,
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

        records = self._client.search_records(
            table_id=domain.table_id,
            field_names=select_fields,
            filter=filter_body,
            page_size=int(query.limit),
        )
        # Project to exactly the requested fields (a record must never carry
        # undeclared columns back into the gateway) and flatten Feishu's rich-text
        # cells to scalars so record-scope and disclosure see plain values.
        return [
            {name: _flatten_cell(record[name]) for name in select_fields if name in record}
            for record in records
        ]

    def _build_filter(
        self, filters: tuple[ConnectorFilter, ...], filter_fields: set[str]
    ) -> dict[str, Any] | None:
        """Translate operator-aware filters into a Feishu ``records/search`` filter.

        With only flat single-field equality/contains this is a flat ``and`` of
        conditions (the proven path). When any filter needs an OR — an ``in``
        (Feishu has no native ``IN``) or an ``or_fields`` multi-field match (v0.4.8,
        e.g. a virtual ``identity`` token) — the whole query is expressed as a
        one-level ``children`` group (the documented and/or shape): each such filter
        becomes an ``or`` group, and every plain filter a single-condition group
        AND-ed in.
        """
        for query_filter in filters:
            names = query_filter.or_fields or (query_filter.field,)
            for name in names:
                if name not in filter_fields:
                    raise ValueError(f"unknown filter field: {name}")
        if not filters:
            return None
        if not any(_needs_child_group(query_filter) for query_filter in filters):
            return {
                "conjunction": "and",
                "conditions": [self._condition(query_filter) for query_filter in filters],
            }
        children: list[dict[str, Any]] = []
        for query_filter in filters:
            if query_filter.or_fields:
                # OR the same ``operator value`` across each named field.
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
                values = _as_list(query_filter.value)
                children.append(
                    {
                        "conjunction": "or",
                        "conditions": [
                            {
                                "field_name": query_filter.field,
                                "operator": "is",
                                "value": [str(value)],
                            }
                            for value in values
                        ],
                    }
                )
            elif query_filter.operator == "date_between":
                # No single Feishu operator: a closed range is the AND of an
                # inclusive lower and upper bound.
                start, end = query_filter.value
                children.append(
                    {
                        "conjunction": "and",
                        "conditions": [
                            {
                                "field_name": query_filter.field,
                                "operator": "isGreaterEqual",
                                "value": [str(start)],
                            },
                            {
                                "field_name": query_filter.field,
                                "operator": "isLessEqual",
                                "value": [str(end)],
                            },
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
        """One ``eq``/``contains`` filter as a Feishu condition (value is an array)."""
        return cls._condition_for(
            query_filter.field, query_filter.operator, query_filter.value
        )

    @staticmethod
    def _condition_for(field: str, operator: str, value: Any) -> dict[str, Any]:
        """A single Feishu condition for an explicit field/operator/value."""
        feishu_operator = _FEISHU_OPERATORS.get(operator)
        if feishu_operator is None:
            raise ValueError(f"feishu connector cannot push down operator: {operator}")
        # ``isEmpty`` is a unary predicate: Feishu takes an empty value array.
        condition_value = [] if operator == "is_empty" else [str(value)]
        return {
            "field_name": field,
            "operator": feishu_operator,
            "value": condition_value,
        }

    def describe_schema(self) -> tuple[SourceTable, ...]:
        """List each configured table's *real* columns (v0.4.0 §D).

        Reads the live Bitable ``fields`` for every configured domain's table, so
        ``scaffold-connector`` can draft a config from real columns. Read-only and
        values-free — only column names, never row data.
        """
        return tuple(
            SourceTable(
                domain=domain.name,
                table=domain.table_id,
                fields=tuple(self._client.list_fields(table_id=domain.table_id)),
            )
            for domain in self._config.domains
        )

    def _domain(self, name: str) -> FeishuDomainConfig:
        for domain in self._config.domains:
            if domain.name == name:
                return domain
        raise ValueError(f"unknown domain: {name}")


# --- Concrete HTTP client (the only credential-bound part) -------------------

_DEFAULT_BASE_URL = "https://open.feishu.cn"
_MAX_PAGE_SIZE = 500  # Feishu's search records page-size ceiling.
_TOKEN_SAFETY_MARGIN = 60.0  # Refresh slightly before real expiry.

# A transport turns one HTTP call into a parsed JSON dict. It is the seam that
# keeps the client testable without network: tests inject a fake transport.
Transport = Callable[[str, str, dict[str, str], Any], dict[str, Any]]


class FeishuApiError(RuntimeError):
    """A Feishu Open Platform response with a non-zero ``code``."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"feishu api error {code}: {message}")
        self.code = code
        self.message = message


def _urllib_transport(
    method: str, url: str, headers: dict[str, str], payload: Any
) -> dict[str, Any]:  # pragma: no cover - network I/O, verified against live Feishu
    import urllib.request

    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


class FeishuClient:
    """``BitableClient`` over the Feishu Open Platform via stdlib urllib.

    Holds the credentials, caches the tenant_access_token, and follows pagination
    up to the requested page size. Authorization stays in the gateway; this is a
    dumb read pipe. Verifying it against live Feishu needs real app_id/app_secret.
    """

    def __init__(
        self,
        *,
        app_id: str,
        app_secret: str,
        app_token: str,
        base_url: str = _DEFAULT_BASE_URL,
        transport: Transport | None = None,
        now: Callable[[], float] | None = None,
    ) -> None:
        self._app_id = app_id
        self._app_secret = app_secret
        self._app_token = app_token
        self._base_url = base_url.rstrip("/")
        self._transport = transport or _urllib_transport
        self._now = now or time.monotonic
        self._token: str | None = None
        self._token_expiry = 0.0

    def search_records(
        self,
        *,
        table_id: str,
        field_names: list[str],
        filter: dict[str, Any] | None,
        page_size: int,
    ) -> list[dict[str, Any]]:
        headers = {
            "Authorization": f"Bearer {self._ensure_token()}",
            "Content-Type": "application/json; charset=utf-8",
        }
        body: dict[str, Any] = {
            "field_names": list(field_names),
            "automatic_fields": False,
        }
        if filter is not None:
            body["filter"] = filter

        limit = int(page_size)
        per_page = min(limit, _MAX_PAGE_SIZE)
        path = f"/open-apis/bitable/v1/apps/{self._app_token}/tables/{table_id}/records/search"
        collected: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            params: dict[str, Any] = {"page_size": per_page}
            if page_token:
                params["page_token"] = page_token
            url = f"{self._base_url}{path}?{urllib.parse.urlencode(params)}"
            data = self._post(url, headers, body)
            for item in data.get("items") or []:
                collected.append(item.get("fields") or {})
                if len(collected) >= limit:
                    return collected[:limit]
            if not data.get("has_more"):
                break
            page_token = data.get("page_token")
            if not page_token:
                break
        return collected[:limit]

    def list_fields(self, *, table_id: str) -> list[str]:
        headers = {
            "Authorization": f"Bearer {self._ensure_token()}",
            "Content-Type": "application/json; charset=utf-8",
        }
        path = f"/open-apis/bitable/v1/apps/{self._app_token}/tables/{table_id}/fields"
        names: list[str] = []
        page_token: str | None = None
        while True:
            params: dict[str, Any] = {"page_size": _MAX_PAGE_SIZE}
            if page_token:
                params["page_token"] = page_token
            url = f"{self._base_url}{path}?{urllib.parse.urlencode(params)}"
            resp = self._transport("GET", url, headers, None)
            self._check(resp)
            data = resp.get("data") or {}
            for item in data.get("items") or []:
                name = item.get("field_name")
                if name:
                    names.append(str(name))
            if not data.get("has_more"):
                break
            page_token = data.get("page_token")
            if not page_token:
                break
        return names

    def _ensure_token(self) -> str:
        if self._token is not None and self._now() < self._token_expiry:
            return self._token
        url = f"{self._base_url}/open-apis/auth/v3/tenant_access_token/internal"
        headers = {"Content-Type": "application/json; charset=utf-8"}
        resp = self._transport("POST", url, headers, {"app_id": self._app_id, "app_secret": self._app_secret})
        self._check(resp)
        self._token = resp["tenant_access_token"]
        self._token_expiry = self._now() + float(resp.get("expire", 0)) - _TOKEN_SAFETY_MARGIN
        return self._token

    def _post(self, url: str, headers: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
        resp = self._transport("POST", url, headers, body)
        self._check(resp)
        return resp.get("data") or {}

    @staticmethod
    def _check(resp: dict[str, Any]) -> None:
        code = resp.get("code", 0)
        if code != 0:
            raise FeishuApiError(int(code), str(resp.get("msg", "")))
