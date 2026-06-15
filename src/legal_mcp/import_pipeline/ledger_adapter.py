"""Adapter for Chinese project ledger wide-table imports."""

from __future__ import annotations

import hashlib
from pathlib import Path

from legal_mcp.import_pipeline.models import ImportRecord, SourceRow
from legal_mcp.import_pipeline.report import ImportReport

PROJECT_COLUMNS = {
    "项目代号": "project_code",
    "游戏名称": "name",
    "上线状态": "stage",
    "法务BP": "legal_bp",
    "部门": "department",
    "发行团队": "release_team",
    "对接人": "contact_person",
    "官网": "website",
    "备注": "notes",
}

LICENSE_MAPPINGS = {
    "publication_license": {
        "版号": "identifier",
        "审批文号": "approval_number",
        "出版单位": "issuer",
        "版号运营主体": "operating_entity",
        "实际运营主体": "actual_operator",
        "内部授权关系": "authorization_relation",
    },
    "icp_filing": {
        "ICP备案号": "identifier",
        "实际运营主体": "actual_operator",
    },
    "software_copyright": {
        "软著登记号": "identifier",
        "软著著作权人": "copyright_holder",
        "实际运营主体": "actual_operator",
    },
    "trademark_right": {
        "商标权利人": "rights_holder",
    },
}

KNOWN_LEDGER_COLUMNS = set(PROJECT_COLUMNS) | {
    column for mapping in LICENSE_MAPPINGS.values() for column in mapping
} | {"风险预警"}


def is_ledger(headers: list[str]) -> bool:
    return {"项目代号", "游戏名称", "上线状态"}.issubset(set(headers))


def adapt_ledger_rows(
    path: Path, headers: list[str], rows: list[SourceRow], report: ImportReport
) -> list[ImportRecord]:
    records: list[ImportRecord] = []
    for row in rows:
        if _is_empty_row(row.values):
            continue
        project = {
            canonical: _clean(row.values.get(source))
            for source, canonical in PROJECT_COLUMNS.items()
            if source in row.values
        }
        records.append(ImportRecord("projects", row.row_number, project))

        for license_type, mapping in LICENSE_MAPPINGS.items():
            values = {
                canonical: _clean(row.values.get(source))
                for source, canonical in mapping.items()
                if source in row.values
            }
            if any(value for value in values.values()):
                values["project_code"] = project.get("project_code")
                values["external_key"] = license_type
                values["license_type"] = license_type
                records.append(ImportRecord("licenses", row.row_number, values))

        risk_text = _clean(row.values.get("风险预警"))
        if risk_text:
            records.append(
                ImportRecord(
                    "risks",
                    row.row_number,
                    {
                        "project_code": project.get("project_code"),
                        "external_key": _risk_external_key(risk_text),
                        "description": risk_text,
                        "status": "open",
                        "source": path.name,
                    },
                )
            )

        for header in headers:
            value = _clean(row.values.get(header))
            if header not in KNOWN_LEDGER_COLUMNS and value:
                report.add_warning(
                    file_name=path.name,
                    row_number=row.row_number,
                    field_name=header,
                    error_code="unknown_column",
                    message=f"Unknown ledger column '{header}' was not imported.",
                )
    return records


def _risk_external_key(description: str) -> str:
    normalized = " ".join(description.split()).lower()
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:12]
    return f"risk_{digest}"


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _is_empty_row(values: dict[str, str | None]) -> bool:
    return not any(_clean(value) for value in values.values())
