"""scaffold-connector: draft a connector config from real columns (v0.4.0 §D, D1).

A new table becomes a declared domain by reading its *actual* schema, without
hand-typing every field — while preserving "only declared fields are queryable":
the draft is a starting point for human review. D1 asserts the draft, after a
no-op review, loads and serves the table with all discovered columns.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from legal_mcp import db
from legal_mcp.connector_config import build_connector_setup
from legal_mcp.connectors.feishu_bitable import (
    FeishuBitableConfig,
    FeishuBitableConnector,
)
from legal_mcp.scaffold import scaffold_connector_config


class _FakeClient:
    """A Bitable client whose schema/rows are canned (no network)."""

    def __init__(self, fields: list[str], rows: list[dict[str, Any]]) -> None:
        self._fields = fields
        self._rows = rows

    def list_fields(self, *, table_id: str) -> list[str]:
        return list(self._fields)

    def search_records(self, *, table_id, field_names, filter, page_size):
        return [{k: r[k] for k in field_names if k in r} for r in self._rows]


# A brand-new, non-project domain — the whole point of §D.
_FIELDS = ["member", "task", "salary"]


def _pointer_connector(fake: _FakeClient) -> FeishuBitableConnector:
    """A pointer-only connector: domain + table_id, NO fields declared yet."""
    return FeishuBitableConnector(
        FeishuBitableConfig.from_dict(
            {
                "app_token": "bascnDemo",
                "domains": [{"name": "staffing", "table_id": "tblStaffing", "fields": []}],
            }
        ),
        fake,
    )


def test_scaffold_draft_has_expected_shape() -> None:
    fake = _FakeClient(_FIELDS, [{"member": "Alice", "task": "drafting", "salary": "99"}])
    draft = scaffold_connector_config(_pointer_connector(fake), app_token="bascnDemo")

    # All real columns are declared; the first is the guessed identity field.
    for field in _FIELDS:
        assert field in draft
    assert "is_identity: true" in draft  # member (first column)
    assert draft.count("is_identity: true") == 1
    # Safe default for an arbitrary table: no row scope.
    assert "mode: none" in draft
    # It is a review artifact, not auto-applied.
    assert "REVIEW BEFORE USE" in draft


def test_scaffold_draft_loads_and_serves_the_table(tmp_path: Path) -> None:
    # D1: after a no-op review the draft loads and serves every discovered column.
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    fake = _FakeClient(_FIELDS, [{"member": "Alice", "task": "drafting", "salary": "99"}])

    draft = scaffold_connector_config(_pointer_connector(fake), app_token="bascnDemo")

    # "Loads": the draft is a valid connector config.
    data = yaml.safe_load(draft)
    setup = build_connector_setup(
        data,
        database_path=database_path,
        env={"FEISHU_APP_ID": "x", "FEISHU_APP_SECRET": "y"},
        feishu_connector_factory=lambda config: FeishuBitableConnector(config, fake),
    )

    # "Serves the table": the scaffolded domain carries all discovered columns,
    # with the first guessed as identity.
    catalog = {domain.name: domain for domain in setup.connector.catalog()}
    assert "staffing" in catalog
    assert {f.name for f in catalog["staffing"].fields} == set(_FIELDS)
    identity = {f.name for f in catalog["staffing"].fields if f.is_identity}
    assert identity == {"member"}
    assert catalog["staffing"].record_scope.mode == "none"
    assert "staffing" in setup.connector_domains


def test_sqlite_connector_describe_schema_lists_real_columns(tmp_path: Path) -> None:
    # The source-agnostic describe_schema: the SQLite demo introspects via PRAGMA.
    from legal_mcp.connectors.sqlite_demo import SqliteDemoConnector

    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    tables = {t.domain: t for t in SqliteDemoConnector(database_path).describe_schema()}

    assert "project" in tables
    assert tables["project"].table == "projects"
    # Real columns from the schema, values-free.
    assert {"project_code", "name", "legal_bp"} <= set(tables["project"].fields)
