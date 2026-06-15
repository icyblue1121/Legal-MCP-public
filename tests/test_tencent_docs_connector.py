"""Tencent Docs smart-table read-through connector (v0.5.9).

Pins the testable core: a config-driven catalog and the ConnectorQuery -> filter
translation. The HTTP/credential layer is behind an injectable client seam, so the
connector is exercised entirely against a fake client (no network), exactly like
the Feishu connector.
"""

from __future__ import annotations

from typing import Any

import pytest

from legal_mcp.connectors.base import ConnectorFilter, ConnectorQuery, DataConnector
from legal_mcp.connectors.tencent_docs import (
    TencentDocsConfig,
    TencentDocsConnector,
)


def _config_dict() -> dict[str, Any]:
    return {
        "file_id": "fileDemo",
        "domains": [
            {
                "name": "project",
                "sheet_id": "sheetProject",
                "fields": [
                    {"name": "project_code", "is_identity": True, "aliases": ["项目代号"]},
                    {"name": "name", "is_identity": True},
                    {"name": "signed_date"},
                    {"name": "owner"},
                ],
            }
        ],
    }


class _FakeClient:
    def __init__(self, records: list[dict[str, Any]] | None = None) -> None:
        self.records = records or []
        self.calls: list[dict[str, Any]] = []

    def list_records(self, *, sheet_id, field_names, filter, page_size):
        self.calls.append({"sheet_id": sheet_id, "field_names": field_names, "filter": filter})
        return self.records

    def list_fields(self, *, sheet_id):
        return ["project_code", "name", "signed_date", "owner", "undeclared"]


def _connector(records: list[dict[str, Any]] | None = None) -> tuple[TencentDocsConnector, _FakeClient]:
    client = _FakeClient(records)
    return TencentDocsConnector(TencentDocsConfig.from_dict(_config_dict()), client), client


def test_satisfies_protocol_and_config_requires_file_id() -> None:
    connector, _ = _connector()
    assert isinstance(connector, DataConnector)
    with pytest.raises(ValueError, match="file_id"):
        TencentDocsConfig.from_dict({"domains": []})


def test_catalog_reflects_config() -> None:
    connector, _ = _connector()
    domain = connector.catalog()[0]
    assert domain.name == "project"
    assert domain.table == "sheetProject"
    code = next(f for f in domain.fields if f.name == "project_code")
    assert code.is_identity and "项目代号" in code.aliases


def test_eq_and_contains_are_flat_conditions() -> None:
    connector, client = _connector([])
    connector.query(
        ConnectorQuery(
            domain="project",
            filters=(
                ConnectorFilter(field="project_code", operator="eq", value="MOON"),
                ConnectorFilter(field="name", operator="contains", value="nova"),
            ),
            fields=("name",),
        )
    )
    assert client.calls[0]["filter"] == {
        "conjunction": "and",
        "conditions": [
            {"field_name": "project_code", "operator": "equal", "value": ["MOON"]},
            {"field_name": "name", "operator": "contains", "value": ["nova"]},
        ],
    }


def test_is_empty_is_unary() -> None:
    connector, client = _connector([])
    connector.query(
        ConnectorQuery(
            domain="project",
            filters=(ConnectorFilter(field="owner", operator="is_empty"),),
            fields=("name",),
        )
    )
    assert client.calls[0]["filter"]["conditions"][0] == {
        "field_name": "owner",
        "operator": "isEmpty",
        "value": [],
    }


def test_in_expands_to_children_or_group() -> None:
    connector, client = _connector([])
    connector.query(
        ConnectorQuery(
            domain="project",
            filters=(ConnectorFilter(field="project_code", operator="in", value=["MOON", "STAR"]),),
            fields=("name",),
        )
    )
    assert client.calls[0]["filter"] == {
        "conjunction": "and",
        "children": [
            {
                "conjunction": "or",
                "conditions": [
                    {"field_name": "project_code", "operator": "equal", "value": ["MOON"]},
                    {"field_name": "project_code", "operator": "equal", "value": ["STAR"]},
                ],
            }
        ],
    }


def test_date_between_expands_to_children_and_range() -> None:
    connector, client = _connector([])
    connector.query(
        ConnectorQuery(
            domain="project",
            filters=(
                ConnectorFilter(
                    field="signed_date", operator="date_between", value=("2024-01-01", "2024-12-31")
                ),
            ),
            fields=("name",),
        )
    )
    assert client.calls[0]["filter"]["children"][0] == {
        "conjunction": "and",
        "conditions": [
            {"field_name": "signed_date", "operator": "greaterEqual", "value": ["2024-01-01"]},
            {"field_name": "signed_date", "operator": "lessEqual", "value": ["2024-12-31"]},
        ],
    }


def test_or_fields_identity_token_is_children_or() -> None:
    connector, client = _connector([])
    connector.query(
        ConnectorQuery(
            domain="project",
            filters=(
                ConnectorFilter(
                    field="identity", operator="contains", value="山海",
                    or_fields=("project_code", "name"),
                ),
            ),
            fields=("name",),
        )
    )
    assert client.calls[0]["filter"]["children"][0] == {
        "conjunction": "or",
        "conditions": [
            {"field_name": "project_code", "operator": "contains", "value": ["山海"]},
            {"field_name": "name", "operator": "contains", "value": ["山海"]},
        ],
    }


def test_query_projects_to_requested_fields() -> None:
    connector, _ = _connector([{"name": "Moon", "project_code": "MOON", "owner": "a"}])
    rows = connector.query(
        ConnectorQuery(
            domain="project",
            filters=(ConnectorFilter(field="project_code", operator="eq", value="MOON"),),
            fields=("name", "project_code"),
        )
    )
    assert rows == [{"name": "Moon", "project_code": "MOON"}]


def test_describe_schema_lists_columns() -> None:
    connector, _ = _connector()
    table = connector.describe_schema()[0]
    assert table.domain == "project"
    assert "undeclared" in table.fields  # discovery is values-free and unfiltered


def test_unknown_filter_field_rejected() -> None:
    connector, _ = _connector()
    with pytest.raises(ValueError, match="unknown filter field"):
        connector.query(
            ConnectorQuery(
                domain="project",
                filters=(ConnectorFilter(field="nope", operator="eq", value="x"),),
                fields=("name",),
            )
        )


def test_wizard_build_source_config_for_tencent() -> None:
    # v0.5.9: the wizard can declare an online source. build_source_config emits the
    # tencent shape (top-level file_id, per-domain sheet_id, access_token_env ref).
    from legal_mcp.admin_data_sources import ColumnReview, build_source_config

    config = build_source_config(
        source_type="tencent_docs",
        name="contracts-online",
        domain="contract",
        connect={
            "file_id": "fileX",
            "sheet_id": "sheetX",
            "access_token_env": "TENCENT_DOCS_TOKEN",
        },
        columns=[ColumnReview("contract_number", include=True, is_identity=True, aliases=())],
        record_scope=None,
    )
    assert config["type"] == "tencent_docs"
    assert config["file_id"] == "fileX"
    assert config["access_token_env"] == "TENCENT_DOCS_TOKEN"
    assert config["domains"][0]["sheet_id"] == "sheetX"
    assert config["domains"][0]["record_scope"] == {"mode": "none"}


def test_config_factory_fails_closed_without_token(tmp_path) -> None:
    from legal_mcp import db
    from legal_mcp.connector_config import build_connector_setup

    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    with pytest.raises(ValueError, match="TENCENT_DOCS_TOKEN"):
        build_connector_setup(
            {
                "sources": [
                    {
                        "type": "tencent_docs",
                        "file_id": "fileDemo",
                        "domains": [
                            {"name": "project", "sheet_id": "s1", "fields": [{"name": "name"}]}
                        ],
                    }
                ]
            },
            database_path=database_path,
            env={},  # no token -> fail closed
        )
