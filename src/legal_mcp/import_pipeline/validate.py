"""Validation for canonical import records."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from legal_mcp.import_pipeline.models import ImportRecord
from legal_mcp.import_pipeline.report import ImportReport

REQUIRED_FIELDS = {
    "projects": ("project_code", "name", "stage"),
    "contracts": ("project_code", "external_key", "title"),
    "licenses": ("project_code", "external_key", "license_type"),
    "risks": ("project_code", "external_key", "description", "status"),
}


def validate_records(
    conn: sqlite3.Connection,
    path: Path,
    records: list[ImportRecord],
    report: ImportReport,
) -> list[ImportRecord]:
    valid: list[ImportRecord] = []
    project_codes_in_batch = {
        record.values.get("project_code")
        for record in records
        if record.entity == "projects" and record.values.get("project_code")
    }
    existing_project_codes = _existing_project_codes(conn)

    for record in records:
        if _record_has_errors(
            conn,
            path,
            record,
            report,
            existing_project_codes | project_codes_in_batch,
        ):
            report.add_count(record.entity, "failed")
            continue
        valid.append(record)
    return valid


def _record_has_errors(
    conn: sqlite3.Connection,
    path: Path,
    record: ImportRecord,
    report: ImportReport,
    known_project_codes: set[str | None],
) -> bool:
    has_errors = False
    for field_name in REQUIRED_FIELDS[record.entity]:
        if not record.values.get(field_name):
            report.add_error(
                file_name=path.name,
                row_number=record.row_number,
                field_name=field_name,
                error_code="required",
                message=f"{field_name} is required.",
            )
            has_errors = True

    project_code = record.values.get("project_code")
    if (
        record.entity != "projects"
        and project_code
        and project_code not in known_project_codes
    ):
        report.add_error(
            file_name=path.name,
            row_number=record.row_number,
            field_name="project_code",
            error_code="unknown_project",
            message=f"Project code '{project_code}' does not exist.",
        )
        has_errors = True
    return has_errors


def _existing_project_codes(conn: sqlite3.Connection) -> set[str]:
    return {
        row["project_code"]
        for row in conn.execute("select project_code from projects")
    }
