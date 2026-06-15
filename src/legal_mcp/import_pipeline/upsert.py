"""SQLite upsert layer for canonical import records."""

from __future__ import annotations

import sqlite3

from legal_mcp.import_pipeline.models import ImportRecord
from legal_mcp.import_pipeline.report import ImportReport

ENTITY_COLUMNS = {
    "projects": (
        "project_code",
        "name",
        "stage",
        "legal_bp",
        "department",
        "release_team",
        "contact_person",
        "website",
        "notes",
    ),
    "contracts": (
        "external_key",
        "title",
        "handler",
        "payment_terms",
        "currency",
        "total_amount",
        "expiry_date",
        "counterparty",
        "company_entity",
        "signed_date",
        "contract_number",
        "income_expense_type",
        "summary",
    ),
    "licenses": (
        "external_key",
        "license_type",
        "identifier",
        "entity_name",
        "issuer",
        "approval_number",
        "rights_holder",
        "copyright_holder",
        "operating_entity",
        "actual_operator",
        "authorization_relation",
        "expiry_date",
        "notes",
    ),
    "risks": ("external_key", "description", "status", "source"),
}


def upsert_records(
    conn: sqlite3.Connection, records: list[ImportRecord], report: ImportReport
) -> None:
    for record in records:
        outcome = _upsert_record(conn, record)
        report.add_count(record.entity, outcome)


def _upsert_record(conn: sqlite3.Connection, record: ImportRecord) -> str:
    if record.entity == "projects":
        return _upsert_project(conn, record.values)
    return _upsert_child(conn, record.entity, record.values)


def _upsert_project(conn: sqlite3.Connection, values: dict[str, str | None]) -> str:
    existing = conn.execute(
        "select * from projects where project_code = ?", (values["project_code"],)
    ).fetchone()
    insert_values = _project_values(values)
    if existing is None:
        _insert(conn, "projects", insert_values)
        return "created"
    if _matches(existing, insert_values):
        return "skipped"
    _update(conn, "projects", insert_values, "project_code = ?", (values["project_code"],))
    return "updated"


def _upsert_child(
    conn: sqlite3.Connection, entity: str, values: dict[str, str | None]
) -> str:
    project_id = conn.execute(
        "select id from projects where project_code = ?", (values["project_code"],)
    ).fetchone()["id"]
    table_values = {
        "project_id": project_id,
        **{
            column: values.get(column)
            for column in ENTITY_COLUMNS[entity]
        },
    }
    existing = conn.execute(
        f"select * from {entity} where project_id = ? and external_key = ?",
        (project_id, values["external_key"]),
    ).fetchone()
    if existing is None:
        _insert(conn, entity, table_values)
        return "created"
    if _matches(existing, table_values):
        return "skipped"
    _update(
        conn,
        entity,
        table_values,
        "project_id = ? and external_key = ?",
        (project_id, values["external_key"]),
    )
    return "updated"


def _project_values(values: dict[str, str | None]) -> dict[str, str | None]:
    return {column: values.get(column) for column in ENTITY_COLUMNS["projects"]}


def _insert(
    conn: sqlite3.Connection, table: str, values: dict[str, str | int | None]
) -> None:
    columns = tuple(values)
    placeholders = ", ".join("?" for _ in columns)
    conn.execute(
        f"insert into {table} ({', '.join(columns)}) values ({placeholders})",
        tuple(values[column] for column in columns),
    )


def _update(
    conn: sqlite3.Connection,
    table: str,
    values: dict[str, str | int | None],
    where_clause: str,
    where_values: tuple[object, ...],
) -> None:
    update_values = {
        column: value
        for column, value in values.items()
        if column not in {"project_code", "project_id", "external_key"}
    }
    assignments = ", ".join(f"{column} = ?" for column in update_values)
    conn.execute(
        f"update {table} set {assignments}, updated_at = datetime('now') "
        f"where {where_clause}",
        (*update_values.values(), *where_values),
    )


def _matches(
    existing: sqlite3.Row, values: dict[str, str | int | None]
) -> bool:
    return all(existing[column] == value for column, value in values.items())
