"""Import report structures."""

from __future__ import annotations

from dataclasses import dataclass, field


ENTITY_NAMES = ("projects", "contracts", "licenses", "risks")
COUNT_NAMES = ("created", "updated", "skipped", "failed")


@dataclass(frozen=True)
class ImportIssue:
    file_name: str
    row_number: int
    field_name: str
    error_code: str
    message: str


@dataclass
class ImportReport:
    source_rows: int = 0
    counts: dict[str, dict[str, int]] = field(
        default_factory=lambda: {
            entity: {count_name: 0 for count_name in COUNT_NAMES}
            for entity in ENTITY_NAMES
        }
    )
    errors: list[ImportIssue] = field(default_factory=list)
    warnings: list[ImportIssue] = field(default_factory=list)

    @property
    def failed(self) -> int:
        return len(self.errors)

    def add_count(self, entity: str, outcome: str) -> None:
        self.counts[entity][outcome] += 1

    def add_error(
        self,
        *,
        file_name: str,
        row_number: int,
        field_name: str,
        error_code: str,
        message: str,
    ) -> None:
        self.errors.append(
            ImportIssue(file_name, row_number, field_name, error_code, message)
        )

    def add_warning(
        self,
        *,
        file_name: str,
        row_number: int,
        field_name: str,
        error_code: str,
        message: str,
    ) -> None:
        self.warnings.append(
            ImportIssue(file_name, row_number, field_name, error_code, message)
        )
