"""CSV reader for normalized and ledger imports."""

from __future__ import annotations

import csv
from pathlib import Path

from legal_mcp.import_pipeline.models import SourceRow


def read_csv(path: Path) -> tuple[list[str], list[SourceRow]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        headers = list(reader.fieldnames or [])
        rows = [
            SourceRow(row_number=index, values=dict(row))
            for index, row in enumerate(reader, start=2)
        ]
    return headers, rows
