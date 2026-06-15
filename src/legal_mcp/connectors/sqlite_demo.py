"""SQLite reference demo connector (pivot 阶段3).

Wraps the bundled SQLite legal data as a read-through source. This is the
flagship *example*, not the product's canonical store. The legal-specific
vocabulary (field aliases, identity fields, table mapping) lives here in the demo
connector, not in the gateway core — so the core can stay domain-agnostic.

Column and table names are taken only from this connector's own catalog before
being interpolated into SQL; values are always parameterized.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from legal_mcp import db
from legal_mcp.connectors.base import (
    ConnectorDomain,
    ConnectorField,
    ConnectorFilter,
    ConnectorQuery,
    SourceTable,
)
from legal_mcp.connectors.sqlite_filter import build_where
from legal_mcp.query_catalog import (
    DOMAIN_FIELDS,
    DOMAIN_TABLES,
    FIELD_ALIASES,
    IDENTITY_FIELDS,
)

_RELATIONSHIP_DOMAINS = {"contract", "license"}
_PROJECT_IDENTITY = ("project_code", "name")


class SqliteDemoConnector:
    """Read-through connector over the bundled SQLite legal demo."""

    name = "sqlite_demo"

    def __init__(self, database_path: Path | str) -> None:
        self._database_path = Path(database_path)

    def catalog(self) -> tuple[ConnectorDomain, ...]:
        domains: list[ConnectorDomain] = []
        for domain, fields in DOMAIN_FIELDS.items():
            identity = IDENTITY_FIELDS.get(domain, set())
            alias_by_target: dict[str, list[str]] = {}
            for alias, target in FIELD_ALIASES.get(domain, {}).items():
                if target in fields:
                    alias_by_target.setdefault(target, []).append(alias)
            connector_fields = tuple(
                ConnectorField(
                    domain=domain,
                    name=name,
                    is_identity=name in identity,
                    aliases=tuple(alias_by_target.get(name, ())),
                )
                for name in sorted(fields)
            )
            relationship = _PROJECT_IDENTITY if domain in _RELATIONSHIP_DOMAINS else ()
            domains.append(
                ConnectorDomain(
                    name=domain,
                    table=DOMAIN_TABLES[domain],
                    fields=connector_fields,
                    relationship_filter_fields=relationship,
                )
            )
        return tuple(domains)

    def domain_sources(self) -> dict[str, str]:
        """Map every domain this connector serves to its source name (v0.4.0 §C C5).

        Enables ``_disconnected_domains`` to honour a sqlite_demo disable flag so
        queries against those domains fail closed when the source is disconnected.
        """
        return {domain: self.name for domain in DOMAIN_FIELDS}

    def query(self, query: ConnectorQuery) -> list[dict[str, Any]]:
        domain = self._domain(query.domain)
        known_fields = {connector_field.name for connector_field in domain.fields}
        select_fields = [name for name in query.fields if name in known_fields]
        if not select_fields:
            raise ValueError("query has no known return fields")

        where, params, join = self._where(domain, known_fields, query.filters)
        select_list = ", ".join(f"{domain.table}.{name}" for name in select_fields)
        sql = f"select {select_list} from {domain.table}{join}"
        if where:
            sql += " where " + " and ".join(where)
        sql += " limit ?"
        params.append(int(query.limit))

        conn = db.connect(self._database_path)
        try:
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()
        return [dict(row) for row in rows]

    def describe_schema(self) -> tuple[SourceTable, ...]:
        """List each demo table's *real* columns via ``PRAGMA`` (v0.4.0 §D).

        Read-only and values-free — only column names — so ``scaffold-connector``
        can draft a config from a source's actual schema.
        """
        conn = db.connect(self._database_path)
        try:
            tables: list[SourceTable] = []
            for domain, table in DOMAIN_TABLES.items():
                rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
                tables.append(
                    SourceTable(
                        domain=domain,
                        table=table,
                        fields=tuple(str(row["name"]) for row in rows),
                    )
                )
            return tuple(tables)
        finally:
            conn.close()

    def _domain(self, name: str) -> ConnectorDomain:
        for domain in self.catalog():
            if domain.name == name:
                return domain
        raise ValueError(f"unknown domain: {name}")

    def _where(
        self,
        domain: ConnectorDomain,
        known_fields: set[str],
        filters: tuple[ConnectorFilter, ...],
    ) -> tuple[list[str], list[Any], str]:
        params: list[Any] = []
        join = ""

        def column_for(field: str) -> str:
            nonlocal join
            if field in known_fields:
                return f"{domain.table}.{field}"
            if field in domain.relationship_filter_fields:
                # Filter child rows by their parent project's identity.
                join = f" join projects on projects.id = {domain.table}.project_id"
                return f"projects.{field}"
            raise ValueError(f"unknown filter field: {field}")

        # Operator translation (eq/contains/in/is_empty/date_*/or_fields) lives in
        # ``sqlite_filter`` so the local_file connector reuses the same semantics.
        where = build_where(filters, column_for, params)
        return where, params, join
