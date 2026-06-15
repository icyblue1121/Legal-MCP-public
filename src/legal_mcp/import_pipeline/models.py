"""Internal row models for import pipeline stages."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceRow:
    row_number: int
    values: dict[str, str | None]


@dataclass(frozen=True)
class ImportRecord:
    entity: str
    row_number: int
    values: dict[str, str | None]
