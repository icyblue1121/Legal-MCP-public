from __future__ import annotations

from pathlib import Path

from legal_mcp import db
from legal_mcp.connectors.base import ConnectorFilter, ConnectorQuery, DataConnector
from legal_mcp.connectors.sqlite_demo import SqliteDemoConnector
from legal_mcp.query_catalog import (
    build_query_catalog,
    build_query_catalog_from_connector,
)


def _seed(tmp_path: Path) -> Path:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    return database_path


def test_sqlite_demo_connector_satisfies_protocol(tmp_path: Path) -> None:
    connector = SqliteDemoConnector(_seed(tmp_path))
    assert isinstance(connector, DataConnector)


def test_connector_catalog_lists_demo_domains_with_legal_vocab(tmp_path: Path) -> None:
    connector = SqliteDemoConnector(_seed(tmp_path))
    domains = {domain.name: domain for domain in connector.catalog()}
    assert {"project", "contract", "license"} <= set(domains)

    project = domains["project"]
    field_names = {connector_field.name for connector_field in project.fields}
    assert "project_code" in field_names
    assert "contact_person" in field_names

    # The legal-specific vocabulary lives in the connector, not the core.
    project_code = next(f for f in project.fields if f.name == "project_code")
    assert "项目代号" in project_code.aliases
    assert project_code.is_identity

    # Child domains can be filtered by their project's identity.
    assert "project_code" in domains["license"].relationship_filter_fields


def test_query_catalog_can_be_built_from_connector(tmp_path: Path) -> None:
    database_path = _seed(tmp_path)
    connector = SqliteDemoConnector(database_path)
    conn = db.connect(database_path)
    try:
        baseline = build_query_catalog(conn)
        from_connector = build_query_catalog_from_connector(connector, conn)
    finally:
        conn.close()

    for domain in ("project", "contract", "license"):
        assert from_connector.domains[domain].fields == baseline.domains[domain].fields
        assert (
            from_connector.domains[domain].field_aliases
            == baseline.domains[domain].field_aliases
        )
        assert (
            from_connector.domains[domain].identity_fields
            == baseline.domains[domain].identity_fields
        )
        assert (
            from_connector.domains[domain].relationship_filter_fields
            == baseline.domains[domain].relationship_filter_fields
        )
    assert "cross_domain" in from_connector.domains


def test_connector_reads_demo_rows_by_own_field(tmp_path: Path) -> None:
    database_path = _seed(tmp_path)
    conn = db.connect(database_path)
    try:
        conn.execute(
            "insert into projects (project_code, name, stage, contact_person) "
            "values (?, ?, ?, ?)",
            ("DEMO", "Demo Project", "live", "Alice"),
        )
        conn.commit()
    finally:
        conn.close()

    connector = SqliteDemoConnector(database_path)
    rows = connector.query(
        ConnectorQuery(
            domain="project",
            filters=(ConnectorFilter(field="project_code", operator="eq", value="DEMO"),),
            fields=("name", "contact_person"),
        )
    )
    assert rows == [{"name": "Demo Project", "contact_person": "Alice"}]


def test_connector_or_fields_matches_any_named_field(tmp_path: Path) -> None:
    # v0.4.8: an or_fields filter (a virtual identity token) ORs the same predicate
    # across the named fields — a token hits a code OR a name, in one pushed query.
    database_path = _seed(tmp_path)
    conn = db.connect(database_path)
    try:
        conn.execute(
            "insert into projects (project_code, name, stage) values (?, ?, ?)",
            ("MOON", "Project Moon 月之子", "live"),
        )
        conn.execute(
            "insert into projects (project_code, name, stage) values (?, ?, ?)",
            ("NOVA", "新星 contains the word moon? no", "live"),
        )
        conn.commit()
    finally:
        conn.close()

    connector = SqliteDemoConnector(database_path)
    # Token in the NAME only (not the code) still matches via the OR.
    rows = connector.query(
        ConnectorQuery(
            domain="project",
            filters=(
                ConnectorFilter(
                    field="identity",
                    operator="contains",
                    value="月之子",
                    or_fields=("project_code", "name"),
                ),
            ),
            fields=("project_code", "name"),
        )
    )
    assert rows == [{"project_code": "MOON", "name": "Project Moon 月之子"}]


def test_connector_reads_child_rows_by_project_identity(tmp_path: Path) -> None:
    database_path = _seed(tmp_path)
    conn = db.connect(database_path)
    try:
        cursor = conn.execute(
            "insert into projects (project_code, name, stage) values (?, ?, ?)",
            ("DEMO", "Demo Project", "live"),
        )
        project_id = cursor.lastrowid
        conn.execute(
            "insert into contracts "
            "(project_id, external_key, title, contract_number, counterparty) "
            "values (?, ?, ?, ?, ?)",
            (project_id, "EK-001", "Demo Contract", "C-001", "Acme"),
        )
        conn.commit()
    finally:
        conn.close()

    connector = SqliteDemoConnector(database_path)
    rows = connector.query(
        ConnectorQuery(
            domain="contract",
            filters=(ConnectorFilter(field="project_code", operator="eq", value="DEMO"),),
            fields=("contract_number", "counterparty"),
        )
    )
    assert rows == [{"contract_number": "C-001", "counterparty": "Acme"}]


def _seed_dated_contracts(tmp_path: Path) -> Path:
    """Two contracts with distinct signed dates; one with an empty handler."""
    database_path = _seed(tmp_path)
    conn = db.connect(database_path)
    try:
        cursor = conn.execute(
            "insert into projects (project_code, name, stage) values (?, ?, ?)",
            ("DEMO", "Demo Project", "live"),
        )
        project_id = cursor.lastrowid
        conn.execute(
            "insert into contracts "
            "(project_id, external_key, title, contract_number, signed_date, handler) "
            "values (?, ?, ?, ?, ?, ?)",
            (project_id, "EK-1", "Contract 2024", "C-2024", "2024-03-01", "Alice"),
        )
        conn.execute(
            "insert into contracts "
            "(project_id, external_key, title, contract_number, signed_date, handler) "
            "values (?, ?, ?, ?, ?, ?)",
            (project_id, "EK-2", "Contract 2025", "C-2025", "2025-09-15", ""),
        )
        conn.commit()
    finally:
        conn.close()
    return database_path


def test_connector_is_empty_operator(tmp_path: Path) -> None:
    # v0.5.1: is_empty now pushes down to the connector (was unsupported).
    connector = SqliteDemoConnector(_seed_dated_contracts(tmp_path))
    rows = connector.query(
        ConnectorQuery(
            domain="contract",
            filters=(ConnectorFilter(field="handler", operator="is_empty"),),
            fields=("contract_number",),
        )
    )
    assert rows == [{"contract_number": "C-2025"}]


def test_connector_date_before_operator(tmp_path: Path) -> None:
    connector = SqliteDemoConnector(_seed_dated_contracts(tmp_path))
    rows = connector.query(
        ConnectorQuery(
            domain="contract",
            filters=(
                ConnectorFilter(field="signed_date", operator="date_before", value="2025-01-01"),
            ),
            fields=("contract_number",),
        )
    )
    assert rows == [{"contract_number": "C-2024"}]


def test_connector_date_after_operator(tmp_path: Path) -> None:
    connector = SqliteDemoConnector(_seed_dated_contracts(tmp_path))
    rows = connector.query(
        ConnectorQuery(
            domain="contract",
            filters=(
                ConnectorFilter(field="signed_date", operator="date_after", value="2025-01-01"),
            ),
            fields=("contract_number",),
        )
    )
    assert rows == [{"contract_number": "C-2025"}]


def test_connector_date_between_operator(tmp_path: Path) -> None:
    connector = SqliteDemoConnector(_seed_dated_contracts(tmp_path))
    rows = connector.query(
        ConnectorQuery(
            domain="contract",
            filters=(
                ConnectorFilter(
                    field="signed_date",
                    operator="date_between",
                    value=("2024-01-01", "2024-12-31"),
                ),
            ),
            fields=("contract_number",),
        )
    )
    assert rows == [{"contract_number": "C-2024"}]
