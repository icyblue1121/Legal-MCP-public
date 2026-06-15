"""Adapter for Chinese contract information imports."""

from __future__ import annotations

from pathlib import Path

from legal_mcp.import_pipeline.models import ImportRecord, SourceRow
from legal_mcp.import_pipeline.report import ImportReport

CONTRACT_COLUMNS = {
    "项目代号": "raw_project",
    "经办人": "handler",
    "收付款条件": "payment_terms",
    "合同概览": "summary",
    "币种": "currency",
    "总金额": "total_amount",
    "过期时间": "expiry_date",
    "签约时间": "signed_date",
    "对方签约公司": "counterparty",
    "我方签约公司": "company_entity",
    "合同主题": "title",
    "合同号": "contract_number",
    "收入或支出": "income_expense_type",
}


def is_contract_information(headers: list[str]) -> bool:
    return {"项目代号", "合同主题", "合同号"}.issubset(set(headers))


def adapt_contract_information_rows(
    path: Path,
    rows: list[SourceRow],
    report: ImportReport,
) -> list[ImportRecord]:
    records: list[ImportRecord] = []
    for row in rows:
        values = {
            canonical: _clean(row.values.get(source))
            for source, canonical in CONTRACT_COLUMNS.items()
            if source in row.values
        }
        if not any(values.values()):
            continue
        contract_number = values.get("contract_number")
        values["project_code"] = values.pop("raw_project", None)
        values["external_key"] = contract_number or values.get("title")
        records.append(ImportRecord("contracts", row.row_number, values))
    return records


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return value or None
