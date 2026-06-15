"""Connector config + composite connector wiring (pivot v0.3).

Pins the mixed-source wiring: a Feishu source serves its declared domains, the
local SQLite demo serves the rest, the union catalog is correct, and the loader
fails closed on missing credentials / unknown source types / domain collisions.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from legal_mcp import db
from legal_mcp.connector_config import build_connector_setup
from legal_mcp.connectors.base import ConnectorQuery, DataConnector
from legal_mcp.connectors.composite import CompositeConnector
from legal_mcp.connectors.feishu_bitable import (
    FeishuBitableConfig,
    FeishuBitableConnector,
)


def _seed(tmp_path: Path) -> Path:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    return database_path


class _FakeClient:
    def __init__(self, records: list[dict[str, Any]] | None = None) -> None:
        self.records = records or []

    def search_records(self, *, table_id, field_names, filter, page_size):
        return self.records


def _fake_factory(records: list[dict[str, Any]] | None = None):
    return lambda config: FeishuBitableConnector(config, _FakeClient(records))


_FEISHU_SOURCE = {
    "type": "feishu_bitable",
    "app_token": "bascnDemo",
    "domains": [
        {
            "name": "project",
            "table_id": "tblProject",
            "fields": [
                {"name": "project_code", "is_identity": True, "aliases": ["项目代号"]},
                {"name": "name", "is_identity": True},
                {"name": "legal_bp", "aliases": ["法务BP"]},
            ],
        }
    ],
}


# --- CompositeConnector -------------------------------------------------------


def test_composite_routes_catalog_and_query(tmp_path: Path) -> None:
    from legal_mcp.connectors.sqlite_demo import SqliteDemoConnector

    sqlite = SqliteDemoConnector(_seed(tmp_path))
    feishu = FeishuBitableConnector(
        FeishuBitableConfig.from_dict(
            {"app_token": "t", "domains": _FEISHU_SOURCE["domains"]}
        ),
        _FakeClient([{"project_code": "MOON", "legal_bp": "BP-Morgan", "name": "Moon"}]),
    )
    composite = CompositeConnector({"project": feishu, "contract": sqlite, "license": sqlite})
    assert isinstance(composite, DataConnector)

    catalog = {domain.name: domain for domain in composite.catalog()}
    assert set(catalog) == {"project", "contract", "license"}
    # The project domain is served by Feishu (its table id), not SQLite.
    assert catalog["project"].table == "tblProject"

    rows = composite.query(
        ConnectorQuery(domain="project", filters=(), fields=("legal_bp", "project_code"))
    )
    assert rows == [{"legal_bp": "BP-Morgan", "project_code": "MOON"}]


def test_composite_rejects_unrouted_domain(tmp_path: Path) -> None:
    from legal_mcp.connectors.sqlite_demo import SqliteDemoConnector

    composite = CompositeConnector({"project": SqliteDemoConnector(_seed(tmp_path))})
    with pytest.raises(ValueError):
        composite.query(ConnectorQuery(domain="contract", filters=(), fields=("title",)))


# --- build_connector_setup ----------------------------------------------------


def test_setup_mixes_feishu_project_with_sqlite_rest(tmp_path: Path) -> None:
    setup = build_connector_setup(
        {"sources": [_FEISHU_SOURCE]},
        database_path=_seed(tmp_path),
        env={"FEISHU_APP_ID": "cli_x", "FEISHU_APP_SECRET": "secret"},
        feishu_connector_factory=_fake_factory(),
    )
    # project routes through the connector path; other demo domains stay on SQLite.
    assert setup.connector_domains == frozenset({"project"})
    catalog = {domain.name: domain for domain in setup.connector.catalog()}
    assert set(catalog) == {"project", "contract", "license", "seal"}
    assert catalog["project"].table == "tblProject"  # Feishu wins for project
    assert catalog["contract"].table == "contracts"  # SQLite serves the rest
    assert catalog["seal"].table == "company_seals"


def test_setup_routes_local_file_source(tmp_path: Path) -> None:
    csv_path = tmp_path / "vendors.csv"
    csv_path.write_text("vendor_code,name\nV1,Acme\n", encoding="utf-8")
    setup = build_connector_setup(
        {
            "sources": [
                {
                    "type": "local_file",
                    "domains": [
                        {
                            "name": "vendor",
                            "path": str(csv_path),
                            "format": "csv",
                            "fields": [
                                {"name": "vendor_code", "is_identity": True},
                                {"name": "name", "is_identity": True},
                            ],
                            "record_scope": {"mode": "none"},
                        }
                    ],
                }
            ]
        },
        database_path=_seed(tmp_path),
    )
    # The local-file domain routes through the connector path; the demo domains stay.
    assert "vendor" in setup.connector_domains
    catalog = {domain.name: domain for domain in setup.connector.catalog()}
    assert catalog["vendor"].table == str(csv_path)
    assert catalog["vendor"].record_scope.mode == "none"


def test_setup_without_sources_is_pure_sqlite(tmp_path: Path) -> None:
    setup = build_connector_setup({}, database_path=_seed(tmp_path))
    assert setup.connector_domains == frozenset()
    catalog = {domain.name for domain in setup.connector.catalog()}
    assert {"project", "contract", "license", "seal"} <= catalog


def test_setup_reads_credentials_from_env_via_real_client(tmp_path: Path) -> None:
    # No factory -> a real credential-bound FeishuClient is built (no network until
    # queried). Missing env must fail closed.
    setup = build_connector_setup(
        {"sources": [_FEISHU_SOURCE]},
        database_path=_seed(tmp_path),
        env={"FEISHU_APP_ID": "cli_x", "FEISHU_APP_SECRET": "secret"},
    )
    assert setup.connector_domains == frozenset({"project"})


def test_setup_fails_closed_on_missing_credentials(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="FEISHU_APP_SECRET"):
        build_connector_setup(
            {"sources": [_FEISHU_SOURCE]},
            database_path=_seed(tmp_path),
            env={"FEISHU_APP_ID": "cli_x"},  # secret missing
        )


# --- C5: disconnecting a source removes its domains from the live catalog ------


def test_disconnected_source_domains_leave_live_catalog(tmp_path: Path) -> None:
    # C5: a console-disconnected source's domains drop from the live catalog so
    # queries against them fail closed, while other sources stay queryable.
    from legal_mcp.agent_graph import _catalog_for_database
    from legal_mcp.query_plan import QueryPlan

    database_path = _seed(tmp_path)
    setup = build_connector_setup(
        {"sources": [_FEISHU_SOURCE]},
        database_path=database_path,
        env={"FEISHU_APP_ID": "cli_x", "FEISHU_APP_SECRET": "secret"},
        feishu_connector_factory=_fake_factory(),
    )

    # Baseline: project (Feishu) and contract (SQLite) are both present.
    catalog = _catalog_for_database(database_path, setup)
    assert "project" in catalog.domains and "contract" in catalog.domains

    # Disconnect the Feishu source from the console.
    conn = db.connect(database_path)
    try:
        db.set_data_source_disabled(conn, "feishu_bitable", disabled=True)
    finally:
        conn.close()

    catalog = _catalog_for_database(database_path, setup)
    # The disconnected source's domain is gone; the still-connected SQLite stays.
    assert "project" not in catalog.domains
    assert "contract" in catalog.domains
    # A plan against the disconnected domain fails closed (not in the catalog).
    result = catalog.validate_plan(
        QueryPlan(domain="project", operation="search", filters=[], return_fields=["name"])
    )
    assert not result.ok
    assert result.error_code == "unsupported_domain"


def test_setup_rejects_unknown_source_type(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown connector source type"):
        build_connector_setup(
            {"sources": [{"type": "postgres"}]},
            database_path=_seed(tmp_path),
            env={},
        )


def test_setup_rejects_same_named_sources_for_one_domain(tmp_path: Path) -> None:
    # Two sources MAY serve one domain (v0.4.9 fallback), but they must carry
    # distinct names so a user/plan can pick one; identical names fail closed.
    with pytest.raises(ValueError, match="distinct 'name'"):
        build_connector_setup(
            {"sources": [_FEISHU_SOURCE, _FEISHU_SOURCE]},
            database_path=_seed(tmp_path),
            env={"FEISHU_APP_ID": "cli_x", "FEISHU_APP_SECRET": "secret"},
            feishu_connector_factory=_fake_factory(),
        )


def test_setup_orders_multi_source_domain_by_declaration(tmp_path: Path) -> None:
    # A domain declared by several (distinctly named) sources is served primary-
    # first in declaration order, exposed via sources_for() for fallback.
    secondary = dict(_FEISHU_SOURCE, name="feishu-backup")
    sqlite_fallback = {"type": "sqlite_demo", "name": "local-db", "domains": ["project"]}
    setup = build_connector_setup(
        {"sources": [_FEISHU_SOURCE, secondary, sqlite_fallback]},
        database_path=_seed(tmp_path),
        env={"FEISHU_APP_ID": "cli_x", "FEISHU_APP_SECRET": "secret"},
        feishu_connector_factory=_fake_factory(),
    )
    assert setup.connector_domains == frozenset({"project"})
    names = [source.name for source in setup.sources_for("project")]
    assert names == ["feishu_bitable", "feishu-backup", "local-db"]
    # Single-source domains keep exactly one source.
    assert [source.name for source in setup.sources_for("contract")] == ["sqlite_demo"]


def test_setup_rejects_sqlite_source_with_unknown_domain(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown domains"):
        build_connector_setup(
            {"sources": [{"type": "sqlite_demo", "domains": ["nope"]}]},
            database_path=_seed(tmp_path),
            env={},
        )


def test_shipped_example_config_loads(tmp_path: Path) -> None:
    """Guard the committed example connector config against drift."""
    from legal_mcp.connector_config import load_connector_config

    example = (
        Path(__file__).resolve().parents[1]
        / "examples" / "connectors" / "feishu-bitable.connector.yaml"
    )
    setup = load_connector_config(
        example,
        database_path=_seed(tmp_path),
        env={"FEISHU_APP_ID": "cli_x", "FEISHU_APP_SECRET": "secret"},
    )
    assert setup.connector_domains == frozenset({"project", "seal"})
    catalog = {domain.name: domain for domain in setup.connector.catalog()}
    assert catalog["project"].table == "tblReplaceWithYourTableId"
    assert catalog["seal"].table == "tblReplaceWithYourSealTableId"
    assert catalog["seal"].record_scope.mode == "by_owner"
    assert catalog["seal"].record_scope.field == "custodian_email"
    assert catalog["seal"].record_scope.subject == "email"
