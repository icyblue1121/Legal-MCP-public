"""Shared import pipeline for CSV/XLSX legal project data."""

from __future__ import annotations

from pathlib import Path

from legal_mcp import db
from legal_mcp.db import DatabasePath
from legal_mcp.import_pipeline.contract_adapter import (
    adapt_contract_information_rows,
    is_contract_information,
)
from legal_mcp.import_pipeline.csv_reader import read_csv
from legal_mcp.import_pipeline.ledger_adapter import adapt_ledger_rows, is_ledger
from legal_mcp.import_pipeline.normalize import normalize_rows, normalized_entity_for_path
from legal_mcp.import_pipeline.project_matcher import match_project
from legal_mcp.import_pipeline.report import ImportReport
from legal_mcp.import_pipeline.upsert import upsert_records
from legal_mcp.import_pipeline.validate import validate_records
from legal_mcp.import_pipeline.xlsx_reader import read_xlsx


def import_file(path: str | Path, *, database_path: DatabasePath) -> ImportReport:
    source_path = Path(path)
    report = ImportReport()

    headers, source_rows = _read_source(source_path)
    report.source_rows = len(source_rows)

    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        if is_contract_information(headers):
            records = adapt_contract_information_rows(source_path, source_rows, report)
            records = _resolve_contract_projects(conn, records)
        elif is_ledger(headers):
            records = adapt_ledger_rows(source_path, headers, source_rows, report)
        else:
            entity = normalized_entity_for_path(source_path)
            if entity is None:
                report.add_error(
                    file_name=source_path.name,
                    row_number=1,
                    field_name="file_name",
                    error_code="unsupported_import_profile",
                    message=(
                        "File name must be one of projects, contracts, licenses, "
                        "risks, or a recognized project ledger."
                    ),
                )
                return report
            records = normalize_rows(entity, source_rows)

        valid_records = validate_records(conn, source_path, records, report)
        upsert_records(conn, valid_records, report)
        conn.commit()
    finally:
        conn.close()

    return report


def _resolve_contract_projects(conn, records):
    resolved = []
    for record in records:
        match = match_project(conn, record.values.get("project_code"))
        if match.project_code is None:
            resolved.append(record)
            continue
        values = {**record.values, "project_code": match.project_code}
        resolved.append(type(record)(record.entity, record.row_number, values))
    return resolved


def _read_source(source_path: Path):
    if source_path.suffix.lower() == ".csv":
        return read_csv(source_path)
    if source_path.suffix.lower() == ".xlsx":
        return read_xlsx(source_path)
    raise ValueError(f"Unsupported import file extension: {source_path.suffix}")


__all__ = ["ImportReport", "import_file"]
