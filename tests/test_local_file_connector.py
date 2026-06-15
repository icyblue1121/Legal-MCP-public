"""Local file read-through connector (v0.5.5).

Covers all four formats (CSV / XLSX / JSON+JSONL / Markdown frontmatter): column
discovery via describe_schema, and query translation through the in-memory SQLite
stage so the operator set (eq / contains / in / identity or_fields / is_empty /
date_between) matches the demo source. Undeclared columns must never load.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from legal_mcp.connectors.base import DataConnector
from legal_mcp.connectors.local_file import (
    LocalFileConfig,
    LocalFileConnector,
)
from legal_mcp.connectors.base import ConnectorFilter, ConnectorQuery


def _csv(tmp_path: Path) -> Path:
    path = tmp_path / "projects.csv"
    path.write_text(
        "project_code,name,owner,signed_date,notes,secret\n"
        "MOON,月之子,alice,2024-03-01,hello,topsecret\n"
        "NOVA,新星,bob,2025-09-15,,topsecret\n",
        encoding="utf-8",
    )
    return path


def _config(path: Path, fmt: str, *, extra_fields: tuple[str, ...] = ()) -> LocalFileConfig:
    fields = [
        {"name": "project_code", "is_identity": True, "aliases": ["项目代号"]},
        {"name": "name", "is_identity": True, "aliases": ["项目名称"]},
        {"name": "owner"},
        {"name": "signed_date"},
        {"name": "notes"},
        *[{"name": f} for f in extra_fields],
    ]
    return LocalFileConfig.from_dict(
        {"domains": [{"name": "project", "path": str(path), "format": fmt, "fields": fields}]}
    )


def _connector(path: Path, fmt: str) -> LocalFileConnector:
    return LocalFileConnector(_config(path, fmt))


def test_satisfies_data_connector_protocol(tmp_path: Path) -> None:
    assert isinstance(_connector(_csv(tmp_path), "csv"), DataConnector)


def test_unsupported_format_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported format"):
        LocalFileConfig.from_dict(
            {"domains": [{"name": "x", "path": "/tmp/x.parquet", "format": "parquet"}]}
        )


def test_describe_schema_lists_real_columns(tmp_path: Path) -> None:
    connector = _connector(_csv(tmp_path), "csv")
    tables = {t.domain: t for t in connector.describe_schema()}
    # The real file columns (incl. the undeclared 'secret') are discoverable...
    assert tables["project"].fields == (
        "project_code",
        "name",
        "owner",
        "signed_date",
        "notes",
        "secret",
    )


def test_undeclared_column_never_loads(tmp_path: Path) -> None:
    # 'secret' is a real column but not declared; it must not be queryable/returned.
    connector = _connector(_csv(tmp_path), "csv")
    with pytest.raises(ValueError, match="unknown filter field: secret"):
        connector.query(
            ConnectorQuery(
                domain="project",
                filters=(ConnectorFilter(field="secret", operator="eq", value="topsecret"),),
                fields=("project_code",),
            )
        )


def test_csv_identity_or_fields_matches_code_or_name(tmp_path: Path) -> None:
    connector = _connector(_csv(tmp_path), "csv")
    rows = connector.query(
        ConnectorQuery(
            domain="project",
            filters=(
                ConnectorFilter(
                    field="identity",
                    operator="contains",
                    value="月之子",
                    or_fields=("project_code", "name"),
                ),
            ),
            fields=("project_code", "name"),
        )
    )
    assert rows == [{"project_code": "MOON", "name": "月之子"}]


def test_csv_in_operator(tmp_path: Path) -> None:
    connector = _connector(_csv(tmp_path), "csv")
    rows = connector.query(
        ConnectorQuery(
            domain="project",
            filters=(ConnectorFilter(field="project_code", operator="in", value=["MOON", "NOVA"]),),
            fields=("project_code",),
        )
    )
    assert sorted(r["project_code"] for r in rows) == ["MOON", "NOVA"]


def test_csv_date_between_and_is_empty(tmp_path: Path) -> None:
    connector = _connector(_csv(tmp_path), "csv")
    dated = connector.query(
        ConnectorQuery(
            domain="project",
            filters=(
                ConnectorFilter(
                    field="signed_date", operator="date_between", value=("2024-01-01", "2024-12-31")
                ),
            ),
            fields=("project_code",),
        )
    )
    assert dated == [{"project_code": "MOON"}]
    empty = connector.query(
        ConnectorQuery(
            domain="project",
            filters=(ConnectorFilter(field="notes", operator="is_empty"),),
            fields=("project_code",),
        )
    )
    assert empty == [{"project_code": "NOVA"}]


def test_json_array_source(tmp_path: Path) -> None:
    path = tmp_path / "p.json"
    path.write_text(
        json.dumps(
            [
                {"project_code": "MOON", "name": "月之子", "owner": "a", "signed_date": "2024-01-01", "notes": "x"},
                {"project_code": "NOVA", "name": "新星", "owner": "b", "signed_date": "2025-01-01", "notes": "y"},
            ]
        ),
        encoding="utf-8",
    )
    connector = _connector(path, "json")
    rows = connector.query(
        ConnectorQuery(
            domain="project",
            filters=(ConnectorFilter(field="name", operator="contains", value="新星"),),
            fields=("project_code",),
        )
    )
    assert rows == [{"project_code": "NOVA"}]


def test_jsonl_source(tmp_path: Path) -> None:
    path = tmp_path / "p.jsonl"
    path.write_text(
        json.dumps({"project_code": "MOON", "name": "月之子"})
        + "\n"
        + json.dumps({"project_code": "NOVA", "name": "新星"})
        + "\n",
        encoding="utf-8",
    )
    connector = LocalFileConnector(
        LocalFileConfig.from_dict(
            {
                "domains": [
                    {
                        "name": "project",
                        "path": str(path),
                        "format": "jsonl",
                        "fields": [
                            {"name": "project_code", "is_identity": True},
                            {"name": "name", "is_identity": True},
                        ],
                    }
                ]
            }
        )
    )
    rows = connector.query(
        ConnectorQuery(
            domain="project",
            filters=(ConnectorFilter(field="project_code", operator="eq", value="moon"),),
            fields=("name",),
        )
    )
    assert rows == [{"name": "月之子"}]  # eq is case-insensitive


def test_markdown_frontmatter_directory(tmp_path: Path) -> None:
    md_dir = tmp_path / "projects"
    md_dir.mkdir()
    (md_dir / "moon.md").write_text(
        "---\nproject_code: MOON\nname: 月之子\nsigned_date: 2024-03-01\n---\n# body ignored\n",
        encoding="utf-8",
    )
    (md_dir / "nova.md").write_text(
        "---\nproject_code: NOVA\nname: 新星\nsigned_date: 2025-09-15\n---\nbody\n",
        encoding="utf-8",
    )
    connector = LocalFileConnector(
        LocalFileConfig.from_dict(
            {
                "domains": [
                    {
                        "name": "project",
                        "path": str(md_dir),
                        "format": "md",
                        "fields": [
                            {"name": "project_code", "is_identity": True},
                            {"name": "name", "is_identity": True},
                            {"name": "signed_date"},
                        ],
                    }
                ]
            }
        )
    )
    columns = connector.describe_schema()[0].fields
    assert "project_code" in columns and "name" in columns
    rows = connector.query(
        ConnectorQuery(
            domain="project",
            filters=(
                ConnectorFilter(
                    field="signed_date", operator="date_between", value=("2025-01-01", "2025-12-31")
                ),
            ),
            fields=("project_code", "name"),
        )
    )
    assert rows == [{"project_code": "NOVA", "name": "新星"}]


def test_xlsx_source(tmp_path: Path) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    path = tmp_path / "p.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["project_code", "name", "signed_date"])
    sheet.append(["MOON", "月之子", "2024-03-01"])
    sheet.append(["NOVA", "新星", "2025-09-15"])
    workbook.save(path)

    connector = LocalFileConnector(
        LocalFileConfig.from_dict(
            {
                "domains": [
                    {
                        "name": "project",
                        "path": str(path),
                        "format": "xlsx",
                        "fields": [
                            {"name": "project_code", "is_identity": True},
                            {"name": "name", "is_identity": True},
                            {"name": "signed_date"},
                        ],
                    }
                ]
            }
        )
    )
    assert connector.describe_schema()[0].fields == ("project_code", "name", "signed_date")
    rows = connector.query(
        ConnectorQuery(
            domain="project",
            filters=(
                ConnectorFilter(
                    field="identity", operator="contains", value="nova",
                    or_fields=("project_code", "name"),
                ),
            ),
            fields=("project_code", "name"),
        )
    )
    assert rows == [{"project_code": "NOVA", "name": "新星"}]
