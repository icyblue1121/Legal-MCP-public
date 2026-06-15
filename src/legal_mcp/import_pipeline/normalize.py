"""Normalize imported row values into canonical record shapes."""

from __future__ import annotations

from pathlib import Path

from legal_mcp.import_pipeline.models import ImportRecord, SourceRow

NORMALIZED_ENTITIES = {"projects", "contracts", "licenses", "risks"}


def normalized_entity_for_path(path: Path) -> str | None:
    stem = path.stem.lower()
    return stem if stem in NORMALIZED_ENTITIES else None


def normalize_rows(entity: str, rows: list[SourceRow]) -> list[ImportRecord]:
    records: list[ImportRecord] = []
    for row in rows:
        if _is_empty_row(row.values):
            continue
        records.append(
            ImportRecord(
                entity=entity,
                row_number=row.row_number,
                values={
                    str(key).strip(): _clean(value)
                    for key, value in row.values.items()
                    if key is not None and str(key).strip()
                },
            )
        )
    return records


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _is_empty_row(values: dict[str, str | None]) -> bool:
    return not any(_clean(value) for value in values.values())
