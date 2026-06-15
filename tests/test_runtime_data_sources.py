"""Runtime data-source registry (v0.5.6).

A source registered in the ``data_sources`` table (status='active') joins the live
catalog and routes through the connector path *without a restart*; disabling it
removes its domain. Authorization is unchanged — a DB-registered domain flows
through the same gates as any other.
"""

from __future__ import annotations

import json
from pathlib import Path

from legal_mcp import db
from legal_mcp.agent_graph import run_structured_query
from legal_mcp.connector_config import effective_connector_setup
from legal_mcp.policy import AccessContext
from legal_mcp.query_catalog import build_query_catalog_from_connector


def _seed_with_csv(tmp_path: Path) -> tuple[Path, Path]:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    csv_path = tmp_path / "vendors.csv"
    csv_path.write_text("vendor_code,name\nV1,Acme\nV2,Globex\n", encoding="utf-8")
    return database_path, csv_path


def _vendor_config(csv_path: Path) -> str:
    return json.dumps(
        {
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
            ]
        }
    )


def _register(database_path: Path, csv_path: Path, *, status: str = "active") -> None:
    conn = db.connect(database_path)
    try:
        conn.execute(
            "insert into data_sources (name, type, status, config_json) values (?, ?, ?, ?)",
            ("vendors", "local_file", status, _vendor_config(csv_path)),
        )
        conn.commit()
    finally:
        conn.close()


def _set_status(database_path: Path, status: str) -> None:
    conn = db.connect(database_path)
    try:
        conn.execute(
            "update data_sources set status = ?, updated_at = datetime('now', ?) where name = 'vendors'",
            (status, "+1 second"),  # ensure the fingerprint's max(updated_at) advances
        )
        conn.commit()
    finally:
        conn.close()


def test_active_source_joins_catalog_and_routing(tmp_path: Path) -> None:
    database_path, csv_path = _seed_with_csv(tmp_path)
    _register(database_path, csv_path)

    setup = effective_connector_setup(None, database_path)
    assert setup is not None
    assert "vendor" in setup.connector_domains  # routes through the connector path
    conn = db.connect(database_path)
    try:
        catalog = build_query_catalog_from_connector(setup.connector, conn)
    finally:
        conn.close()
    assert "vendor" in catalog.domains
    assert catalog.domains["vendor"].record_scope.mode == "none"


def test_no_active_source_leaves_base_untouched(tmp_path: Path) -> None:
    database_path, csv_path = _seed_with_csv(tmp_path)
    _register(database_path, csv_path, status="draft")  # not active
    assert effective_connector_setup(None, database_path) is None


def test_active_source_served_end_to_end(tmp_path: Path) -> None:
    database_path, csv_path = _seed_with_csv(tmp_path)
    _register(database_path, csv_path)

    result = run_structured_query(
        query={
            "domain": "vendor",
            "operation": "search",
            "filters": [{"field": "vendor_code", "operator": "eq", "value": "V1"}],
            "return_fields": ["name"],
            "limit": 5,
        },
        database_path=database_path,
        checkpoint_path=tmp_path / "ckpt.sqlite",
        audit_path=tmp_path / "audit.jsonl",
        access_context=AccessContext.local_operator(),
    )
    assert result["status"] == "success"
    assert "Acme" in result["answer"]


def test_disabling_source_removes_domain_hot(tmp_path: Path) -> None:
    database_path, csv_path = _seed_with_csv(tmp_path)
    _register(database_path, csv_path)

    # Active: the domain is served.
    active = effective_connector_setup(None, database_path)
    assert active is not None and "vendor" in active.connector_domains

    # Disable it — the fingerprint changes, so the cache rebuilds and the domain
    # leaves the live setup with no restart.
    _set_status(database_path, "disabled")
    after = effective_connector_setup(None, database_path)
    assert after is None  # no active sources -> base (None) returned

    # A query against the now-unknown domain fails closed.
    result = run_structured_query(
        query={
            "domain": "vendor",
            "operation": "search",
            "filters": [{"field": "vendor_code", "operator": "eq", "value": "V1"}],
            "return_fields": ["name"],
            "limit": 5,
        },
        database_path=database_path,
        checkpoint_path=tmp_path / "ckpt2.sqlite",
        audit_path=tmp_path / "audit.jsonl",
        access_context=AccessContext.local_operator(),
    )
    assert result["status"] == "error"
    assert result["error"]["code"] == "unsupported_domain"
