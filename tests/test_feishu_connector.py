"""Feishu Bitable read-through connector (pivot v0.3, plan §7 v0.3).

These tests pin the *testable* core of the connector: a config-driven catalog and
the ConnectorQuery -> Feishu-request translation. The real HTTP/credential layer
is isolated behind an injectable client seam, so the connector is exercised here
entirely against a fake client (no network).
"""

from __future__ import annotations

from typing import Any

import pytest

from legal_mcp.connectors.base import ConnectorFilter, ConnectorQuery, DataConnector
from legal_mcp.connectors.feishu_bitable import (
    FeishuBitableConfig,
    FeishuBitableConnector,
)


def _config_dict() -> dict[str, Any]:
    return {
        "app_token": "bascnDemoAppToken",
        "domains": [
            {
                "name": "project",
                "table_id": "tblProject",
                "fields": [
                    {"name": "project_code", "is_identity": True, "aliases": ["项目代号"]},
                    {"name": "name", "is_identity": True, "aliases": ["项目名称", "游戏名称"]},
                    {"name": "contact_person", "aliases": ["联系人", "对接人"]},
                ],
            },
            {
                "name": "contract",
                "table_id": "tblContract",
                "relationship_filter_fields": ["project_code"],
                "fields": [
                    {"name": "contract_number"},
                    {"name": "counterparty"},
                ],
            },
        ],
    }


class _FakeClient:
    """Records search calls and returns canned records. No network."""

    def __init__(self, records: list[dict[str, Any]] | None = None) -> None:
        self.records = records or []
        self.calls: list[dict[str, Any]] = []

    def search_records(
        self,
        *,
        table_id: str,
        field_names: list[str],
        filter: dict[str, Any] | None,
        page_size: int,
    ) -> list[dict[str, Any]]:
        self.calls.append(
            {
                "table_id": table_id,
                "field_names": field_names,
                "filter": filter,
                "page_size": page_size,
            }
        )
        return self.records


def test_config_from_dict_parses_domains_and_fields() -> None:
    config = FeishuBitableConfig.from_dict(_config_dict())
    assert config.app_token == "bascnDemoAppToken"

    domains = {domain.name: domain for domain in config.domains}
    assert set(domains) == {"project", "contract"}
    assert domains["project"].table_id == "tblProject"
    assert domains["contract"].relationship_filter_fields == ("project_code",)

    project_fields = {field.name: field for field in domains["project"].fields}
    assert project_fields["project_code"].is_identity is True
    assert project_fields["project_code"].aliases == ("项目代号",)
    assert project_fields["contact_person"].is_identity is False


def _by_owner_config(record_scope: dict[str, Any]) -> dict[str, Any]:
    return {
        "app_token": "bascnDemoAppToken",
        "domains": [
            {
                "name": "journal",
                "table_id": "tblJournal",
                "record_scope": record_scope,
                "fields": [{"name": "owner"}, {"name": "entry"}],
            }
        ],
    }


def test_config_parses_by_owner_record_scope() -> None:
    # v0.4.5 Phase 4: by_owner now loads (was rejected as reserved). field + subject
    # are carried onto the domain's RecordScope.
    config = FeishuBitableConfig.from_dict(
        _by_owner_config({"mode": "by_owner", "field": "owner_email", "subject": "email"})
    )
    scope = config.domains[0].record_scope
    assert scope.mode == "by_owner"
    assert scope.field == "owner_email"
    assert scope.subject == "email"


def test_config_by_owner_subject_defaults_to_external_subject() -> None:
    config = FeishuBitableConfig.from_dict(
        _by_owner_config({"mode": "by_owner", "field": "owner"})
    )
    assert config.domains[0].record_scope.subject == "external_subject"


def test_config_by_owner_requires_explicit_field() -> None:
    # There is no safe default for the owner column (unlike by_governed_code's
    # project_code), so omitting it fails closed at load.
    with pytest.raises(ValueError, match="requires an explicit 'field'"):
        FeishuBitableConfig.from_dict(_by_owner_config({"mode": "by_owner"}))


def test_config_by_owner_rejects_unknown_subject() -> None:
    with pytest.raises(ValueError, match="subject"):
        FeishuBitableConfig.from_dict(
            _by_owner_config({"mode": "by_owner", "field": "owner", "subject": "role"})
        )


def test_connector_satisfies_data_connector_protocol() -> None:
    connector = FeishuBitableConnector(
        FeishuBitableConfig.from_dict(_config_dict()), _FakeClient()
    )
    assert isinstance(connector, DataConnector)
    assert connector.name == "feishu_bitable"


def test_catalog_reflects_config_with_legal_vocab() -> None:
    connector = FeishuBitableConnector(
        FeishuBitableConfig.from_dict(_config_dict()), _FakeClient()
    )
    domains = {domain.name: domain for domain in connector.catalog()}
    assert set(domains) == {"project", "contract"}

    project = domains["project"]
    # The connector exposes the Feishu table id as the ConnectorDomain.table.
    assert project.table == "tblProject"
    field_names = {field.name for field in project.fields}
    assert {"project_code", "name", "contact_person"} <= field_names

    project_code = next(f for f in project.fields if f.name == "project_code")
    assert project_code.is_identity is True
    assert "项目代号" in project_code.aliases

    # Child domains can be filtered by their project's identity (denormalized
    # column on the child table in Feishu — no join).
    assert "project_code" in domains["contract"].relationship_filter_fields


def _connector(records: list[dict[str, Any]] | None = None) -> tuple[
    FeishuBitableConnector, _FakeClient
]:
    client = _FakeClient(records)
    connector = FeishuBitableConnector(FeishuBitableConfig.from_dict(_config_dict()), client)
    return connector, client


def test_query_translates_filters_and_fields_to_search_request() -> None:
    connector, client = _connector(
        [{"name": "Demo", "contact_person": "Alice", "project_code": "DEMO"}]
    )
    rows = connector.query(
        ConnectorQuery(
            domain="project",
            filters=(ConnectorFilter(field="project_code", operator="eq", value="DEMO"),),
            fields=("name", "contact_person"),
            limit=10,
        )
    )

    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["table_id"] == "tblProject"
    assert call["field_names"] == ["name", "contact_person"]
    assert call["page_size"] == 10
    assert call["filter"] == {
        "conjunction": "and",
        "conditions": [
            {"field_name": "project_code", "operator": "is", "value": ["DEMO"]}
        ],
    }
    # Rows are projected to exactly the requested fields (no undeclared leakage).
    assert rows == [{"name": "Demo", "contact_person": "Alice"}]


def test_query_flattens_feishu_rich_text_cells_to_scalars() -> None:
    # Feishu's records/search returns text cells as rich-text *segment lists*
    # (e.g. [{"text": "MOON", "type": "text"}]), not bare strings. The connector
    # must flatten them: a list value breaks the gateway's record-scope post-filter
    # (str(list) != "MOON", dropping every row) and would disclose the raw envelope.
    connector, _ = _connector(
        [
            {
                "project_code": [{"text": "MOON", "type": "text"}],
                "name": [{"text": "Project Moon "}, {"text": "月之子"}],
                "contact_person": "Alice",  # already-scalar values pass through
            }
        ]
    )
    rows = connector.query(
        ConnectorQuery(
            domain="project",
            filters=(ConnectorFilter(field="project_code", operator="eq", value="MOON"),),
            fields=("project_code", "name", "contact_person"),
            limit=10,
        )
    )
    assert rows == [
        {"project_code": "MOON", "name": "Project Moon 月之子", "contact_person": "Alice"}
    ]


def test_query_with_no_filters_sends_null_filter() -> None:
    connector, client = _connector([])
    connector.query(ConnectorQuery(domain="project", filters=(), fields=("name",)))
    assert client.calls[0]["filter"] is None


def test_query_relationship_filter_maps_to_child_column() -> None:
    connector, client = _connector(
        [{"contract_number": "C-001", "counterparty": "Acme"}]
    )
    rows = connector.query(
        ConnectorQuery(
            domain="contract",
            filters=(ConnectorFilter(field="project_code", operator="eq", value="DEMO"),),
            fields=("contract_number", "counterparty"),
        )
    )
    call = client.calls[0]
    assert call["table_id"] == "tblContract"
    assert call["filter"]["conditions"][0]["field_name"] == "project_code"
    assert rows == [{"contract_number": "C-001", "counterparty": "Acme"}]


def test_query_contains_maps_to_feishu_contains_operator() -> None:
    # v0.4.7: a contains filter pushes Feishu's native substring operator down,
    # which is what restores fuzzy name search on the connector path.
    connector, client = _connector([{"name": "Project Nova 新星"}])
    connector.query(
        ConnectorQuery(
            domain="project",
            filters=(ConnectorFilter(field="name", operator="contains", value="nova"),),
            fields=("name",),
        )
    )
    assert client.calls[0]["filter"] == {
        "conjunction": "and",
        "conditions": [{"field_name": "name", "operator": "contains", "value": ["nova"]}],
    }


def test_query_is_empty_maps_to_feishu_isempty_operator() -> None:
    # v0.5.1: is_empty pushes Feishu's unary ``isEmpty`` (empty value array) down.
    connector, client = _connector([])
    connector.query(
        ConnectorQuery(
            domain="project",
            filters=(ConnectorFilter(field="contact_person", operator="is_empty"),),
            fields=("name",),
        )
    )
    assert client.calls[0]["filter"] == {
        "conjunction": "and",
        "conditions": [{"field_name": "contact_person", "operator": "isEmpty", "value": []}],
    }


def test_query_date_before_and_after_map_to_ordered_operators() -> None:
    # v0.5.1: date_before -> isLess, date_after -> isGreater (flat conditions).
    connector, client = _connector([])
    connector.query(
        ConnectorQuery(
            domain="project",
            filters=(
                ConnectorFilter(field="name", operator="date_after", value="2024-01-01"),
                ConnectorFilter(field="contact_person", operator="date_before", value="2025-01-01"),
            ),
            fields=("name",),
        )
    )
    assert client.calls[0]["filter"] == {
        "conjunction": "and",
        "conditions": [
            {"field_name": "name", "operator": "isGreater", "value": ["2024-01-01"]},
            {"field_name": "contact_person", "operator": "isLess", "value": ["2025-01-01"]},
        ],
    }


def test_query_date_between_maps_to_children_and_group() -> None:
    # v0.5.1: date_between has no single Feishu operator, so it expands to a closed
    # range — isGreaterEqual AND isLessEqual — inside a children AND-group.
    connector, client = _connector([])
    connector.query(
        ConnectorQuery(
            domain="project",
            filters=(
                ConnectorFilter(
                    field="name",
                    operator="date_between",
                    value=("2024-01-01", "2024-12-31"),
                ),
            ),
            fields=("name",),
        )
    )
    assert client.calls[0]["filter"] == {
        "conjunction": "and",
        "children": [
            {
                "conjunction": "and",
                "conditions": [
                    {"field_name": "name", "operator": "isGreaterEqual", "value": ["2024-01-01"]},
                    {"field_name": "name", "operator": "isLessEqual", "value": ["2024-12-31"]},
                ],
            }
        ],
    }


def test_query_in_maps_to_children_or_group() -> None:
    # Feishu has no native IN, so an `in` filter is expressed as a one-level
    # children OR-group of `is` conditions (the documented and/or shape).
    connector, client = _connector([])
    connector.query(
        ConnectorQuery(
            domain="project",
            filters=(
                ConnectorFilter(field="project_code", operator="in", value=["MOON", "STAR"]),
            ),
            fields=("name",),
        )
    )
    assert client.calls[0]["filter"] == {
        "conjunction": "and",
        "children": [
            {
                "conjunction": "or",
                "conditions": [
                    {"field_name": "project_code", "operator": "is", "value": ["MOON"]},
                    {"field_name": "project_code", "operator": "is", "value": ["STAR"]},
                ],
            }
        ],
    }


def test_query_in_combined_with_eq_uses_children_groups() -> None:
    # `in` AND another filter: both become child groups under a top-level `and`,
    # so the equality predicate still constrains the OR-expanded `in`.
    connector, client = _connector([])
    connector.query(
        ConnectorQuery(
            domain="project",
            filters=(
                ConnectorFilter(field="project_code", operator="in", value=["MOON", "STAR"]),
                ConnectorFilter(field="contact_person", operator="eq", value="Alice"),
            ),
            fields=("name",),
        )
    )
    filter_body = client.calls[0]["filter"]
    assert filter_body["conjunction"] == "and"
    assert {"field_name": "contact_person", "operator": "is", "value": ["Alice"]} in [
        cond for child in filter_body["children"] for cond in child["conditions"]
    ]


def test_query_or_fields_maps_to_children_or_group() -> None:
    # v0.4.8: an or_fields filter (a virtual identity token) is one children OR-group
    # applying the same operator/value across each named field.
    connector, client = _connector([{"name": "Project Moon 月之子"}])
    connector.query(
        ConnectorQuery(
            domain="project",
            filters=(
                ConnectorFilter(
                    field="identity",
                    operator="contains",
                    value="MOON",
                    or_fields=("project_code", "name"),
                ),
            ),
            fields=("name",),
        )
    )
    assert client.calls[0]["filter"] == {
        "conjunction": "and",
        "children": [
            {
                "conjunction": "or",
                "conditions": [
                    {"field_name": "project_code", "operator": "contains", "value": ["MOON"]},
                    {"field_name": "name", "operator": "contains", "value": ["MOON"]},
                ],
            }
        ],
    }


def test_query_or_fields_combined_with_eq_uses_children_groups() -> None:
    # An or_fields filter AND another filter: both become child groups under the
    # top-level `and`, so the plain equality still constrains the OR match.
    connector, client = _connector([])
    connector.query(
        ConnectorQuery(
            domain="project",
            filters=(
                ConnectorFilter(
                    field="identity",
                    operator="contains",
                    value="山海",
                    or_fields=("project_code", "name"),
                ),
                ConnectorFilter(field="contact_person", operator="eq", value="Alice"),
            ),
            fields=("name",),
        )
    )
    filter_body = client.calls[0]["filter"]
    assert filter_body["conjunction"] == "and"
    assert {"field_name": "contact_person", "operator": "is", "value": ["Alice"]} in [
        cond for child in filter_body["children"] for cond in child["conditions"]
    ]


def test_query_or_fields_rejects_unknown_field() -> None:
    connector, _ = _connector()
    with pytest.raises(ValueError):
        connector.query(
            ConnectorQuery(
                domain="project",
                filters=(
                    ConnectorFilter(
                        field="identity",
                        operator="contains",
                        value="x",
                        or_fields=("project_code", "ghost"),
                    ),
                ),
                fields=("name",),
            )
        )


def test_query_rejects_unknown_filter_field() -> None:
    connector, _ = _connector()
    with pytest.raises(ValueError):
        connector.query(
            ConnectorQuery(
                domain="project",
                filters=(ConnectorFilter(field="total_amount", operator="eq", value=100),),
                fields=("name",),
            )
        )


def test_query_rejects_unknown_domain() -> None:
    connector, _ = _connector()
    with pytest.raises(ValueError):
        connector.query(ConnectorQuery(domain="nope", filters=(), fields=("name",)))


def test_query_errors_when_no_known_return_field() -> None:
    connector, _ = _connector()
    with pytest.raises(ValueError):
        connector.query(ConnectorQuery(domain="project", filters=(), fields=("ghost",)))
