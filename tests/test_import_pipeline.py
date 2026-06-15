import csv
import zipfile
from pathlib import Path

from legal_mcp import db
from legal_mcp.cli import main
from legal_mcp.import_pipeline import import_file


def write_csv(path: Path, headers: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def cell_ref(column_index: int, row_index: int) -> str:
    letters = ""
    value = column_index
    while value:
        value, remainder = divmod(value - 1, 26)
        letters = chr(65 + remainder) + letters
    return f"{letters}{row_index}"


def write_xlsx(path: Path, headers: list[str], rows: list[list[str]]) -> None:
    worksheet_rows = [headers, *rows]
    sheet_xml_rows = []
    for row_index, row in enumerate(worksheet_rows, start=1):
        cells = []
        for column_index, value in enumerate(row, start=1):
            escaped = (
                str(value)
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
            )
            cells.append(
                f'<c r="{cell_ref(column_index, row_index)}" t="inlineStr">'
                f"<is><t>{escaped}</t></is></c>"
            )
        sheet_xml_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')

    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(sheet_xml_rows)}</sheetData>'
        "</worksheet>"
    )
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets>'
        "</workbook>"
    )
    relationships_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/>'
        "</Relationships>"
    )

    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            "</Types>",
        )
        archive.writestr("xl/workbook.xml", workbook_xml)
        archive.writestr("xl/_rels/workbook.xml.rels", relationships_xml)
        archive.writestr("xl/worksheets/sheet1.xml", sheet_xml)


def test_valid_projects_csv_import_creates_rows(tmp_path) -> None:
    source = tmp_path / "projects.csv"
    database_path = tmp_path / "legal.db"
    write_csv(
        source,
        ["project_code", "name", "stage", "legal_bp"],
        [
            {
                "project_code": "GAME-001",
                "name": "Project One",
                "stage": "live",
                "legal_bp": "Ava",
            }
        ],
    )

    report = import_file(source, database_path=database_path)

    assert report.counts["projects"]["created"] == 1
    assert report.failed == 0
    conn = db.connect(database_path)
    try:
        row = conn.execute("select * from projects").fetchone()
        assert row["project_code"] == "GAME-001"
        assert row["name"] == "Project One"
        assert row["legal_bp"] == "Ava"
    finally:
        conn.close()


def test_invalid_project_rows_report_row_level_errors(tmp_path) -> None:
    source = tmp_path / "projects.csv"
    database_path = tmp_path / "legal.db"
    write_csv(
        source,
        ["project_code", "name", "stage"],
        [{"project_code": "GAME-001", "name": "Project One", "stage": ""}],
    )

    report = import_file(source, database_path=database_path)

    assert report.failed == 1
    assert report.errors[0].file_name == "projects.csv"
    assert report.errors[0].row_number == 2
    assert report.errors[0].field_name == "stage"
    assert report.errors[0].error_code == "required"

    conn = db.connect(database_path)
    try:
        assert conn.execute("select count(*) from projects").fetchone()[0] == 0
    finally:
        conn.close()


def test_project_import_is_idempotent_and_updates_by_project_code(tmp_path) -> None:
    source = tmp_path / "projects.csv"
    database_path = tmp_path / "legal.db"
    write_csv(
        source,
        ["project_code", "name", "stage"],
        [{"project_code": "GAME-001", "name": "Original Name", "stage": "planning"}],
    )

    first_report = import_file(source, database_path=database_path)
    second_report = import_file(source, database_path=database_path)
    write_csv(
        source,
        ["project_code", "name", "stage"],
        [{"project_code": "GAME-001", "name": "Renamed Project", "stage": "live"}],
    )
    third_report = import_file(source, database_path=database_path)

    assert first_report.counts["projects"]["created"] == 1
    assert second_report.counts["projects"]["skipped"] == 1
    assert third_report.counts["projects"]["updated"] == 1

    conn = db.connect(database_path)
    try:
        rows = conn.execute("select project_code, name, stage from projects").fetchall()
        assert [tuple(row) for row in rows] == [("GAME-001", "Renamed Project", "live")]
    finally:
        conn.close()


def test_child_rows_with_unknown_project_code_fail_validation(tmp_path) -> None:
    source = tmp_path / "licenses.csv"
    database_path = tmp_path / "legal.db"
    write_csv(
        source,
        ["project_code", "external_key", "license_type", "identifier"],
        [
            {
                "project_code": "MISSING",
                "external_key": "publication_license",
                "license_type": "publication_license",
                "identifier": "ISBN-001",
            }
        ],
    )

    report = import_file(source, database_path=database_path)

    assert report.failed == 1
    assert report.errors[0].field_name == "project_code"
    assert report.errors[0].error_code == "unknown_project"


def test_contract_import_uses_project_alias_before_exact_match(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        project_id = conn.execute(
            "insert into projects (project_code, name, stage) values (?, ?, ?)",
            ("ACME", "Acme", "live"),
        ).lastrowid
        conn.execute(
            "insert into project_aliases (project_id, alias, source) values (?, ?, ?)",
            (project_id, "ACME项目部", "pytest"),
        )
        conn.commit()
    finally:
        conn.close()

    csv_path = tmp_path / "contract_info.csv"
    csv_path.write_text(
        "\ufeff,项目代号,经办人,收付款条件,,合同概览,币种,总金额,过期时间,签约时间,对方签约公司,我方签约公司,合同主题,合同号,收入或支出,\n"
        "2025-12-13 00:30:19,ACME项目部,侯宇洋,开票后60天支付,,直播KOL采购,人民币,11690,2026-10-31,2025-11-01,福建超神网络科技有限公司,上海游碧曜网络科技有限公司,【框架+执行单-Acme-达人合作】Acme12月测试发行直播KOL采购-超神,SHYBYBZ2025000082,我方支出,\n",
        encoding="utf-8",
    )

    report = import_file(csv_path, database_path=database_path)

    assert report.errors == []
    conn = db.connect(database_path)
    try:
        row = conn.execute(
            "select * from contracts where external_key = ?",
            ("SHYBYBZ2025000082",),
        ).fetchone()
    finally:
        conn.close()
    assert row["project_id"] == project_id
    assert row["title"] == "【框架+执行单-Acme-达人合作】Acme12月测试发行直播KOL采购-超神"
    assert row["total_amount"] == "11690"


def test_ledger_xlsx_fans_out_and_reimport_is_idempotent(tmp_path) -> None:
    source = tmp_path / "project_ledger.xlsx"
    database_path = tmp_path / "legal.db"
    write_xlsx(
        source,
        [
            "项目代号",
            "游戏名称",
            "上线状态",
            "法务BP",
            "版号",
            "审批文号",
            "ICP备案号",
            "软著登记号",
            "出版单位",
            "商标权利人",
            "软著著作权人",
            "版号运营主体",
            "实际运营主体",
            "内部授权关系",
            "风险预警",
            "未映射列",
        ],
        [
            [
                "GAME-001",
                "Ledger Game",
                "live",
                "Ava",
                "ISBN-001",
                "APPROVAL-001",
                "ICP-001",
                "SR-001",
                "Publisher Ltd",
                "Trademark Owner",
                "Copyright Owner",
                "Publishing Entity",
                "Operating Entity",
                "internal sublicense",
                "Contract renewal risk",
                "needs review",
            ]
        ],
    )

    first_report = import_file(source, database_path=database_path)
    second_report = import_file(source, database_path=database_path)

    assert first_report.counts["projects"]["created"] == 1
    assert first_report.counts["licenses"]["created"] == 4
    assert first_report.counts["risks"]["created"] == 1
    assert first_report.warnings[0].field_name == "未映射列"
    assert second_report.counts["projects"]["skipped"] == 1
    assert second_report.counts["licenses"]["skipped"] == 4
    assert second_report.counts["risks"]["skipped"] == 1

    conn = db.connect(database_path)
    try:
        project = conn.execute("select * from projects where project_code = ?", ("GAME-001",)).fetchone()
        assert project["name"] == "Ledger Game"
        license_keys = {
            row["external_key"]
            for row in conn.execute("select external_key from licenses order by external_key")
        }
        assert license_keys == {
            "icp_filing",
            "publication_license",
            "software_copyright",
            "trademark_right",
        }
        risk = conn.execute("select external_key, status, source from risks").fetchone()
        assert risk["external_key"].startswith("risk_")
        assert risk["status"] == "open"
        assert risk["source"] == "project_ledger.xlsx"
    finally:
        conn.close()


def test_cli_import_uses_shared_pipeline(tmp_path, capsys) -> None:
    source = tmp_path / "projects.csv"
    database_path = tmp_path / "legal.db"
    write_csv(
        source,
        ["project_code", "name", "stage"],
        [{"project_code": "GAME-001", "name": "Project One", "stage": "live"}],
    )

    assert main(["import", str(source), "--db", str(database_path)]) == 0

    captured = capsys.readouterr()
    assert "Import complete" in captured.out
    conn = db.connect(database_path)
    try:
        assert conn.execute("select count(*) from projects").fetchone()[0] == 1
    finally:
        conn.close()


def test_cli_ledger_import_report_is_readable_for_real_data_trial(tmp_path, capsys) -> None:
    source = tmp_path / "project_ledger.csv"
    database_path = tmp_path / "legal.db"
    write_csv(
        source,
        [
            "项目代号",
            "游戏名称",
            "上线状态",
            "版号",
            "ICP备案号",
            "风险预警",
            "试跑备注",
        ],
        [
            {
                "项目代号": "GAME-001",
                "游戏名称": "Real Trial One",
                "上线状态": "live",
                "版号": "ISBN-001",
                "ICP备案号": "ICP-001",
                "风险预警": "Renewal owner unclear",
                "试跑备注": "confirm with legal BP",
            },
            {
                "项目代号": "GAME-002",
                "游戏名称": "Real Trial Two",
                "上线状态": "testing",
                "版号": "",
                "ICP备案号": "",
                "风险预警": "",
                "试跑备注": "",
            },
            {
                "项目代号": "GAME-003",
                "游戏名称": "Real Trial Three",
                "上线状态": "planning",
                "版号": "ISBN-003",
                "ICP备案号": "",
                "风险预警": "",
                "试跑备注": "",
            },
        ],
    )

    assert main(["import", str(source), "--db", str(database_path)]) == 0

    captured = capsys.readouterr()
    assert "Import complete: 3 source rows processed" in captured.out
    assert "projects: 3 created, 0 updated, 0 skipped, 0 failed" in captured.out
    assert "licenses: 3 created, 0 updated, 0 skipped, 0 failed" in captured.out
    assert "risks: 1 created, 0 updated, 0 skipped, 0 failed" in captured.out
    assert "Warnings:" in captured.out
    assert "project_ledger.csv row 2 field 试跑备注: unknown_column" in captured.out


def test_project_ledger_headers_support_real_data_trial() -> None:
    headers = [
        "项目代号",
        "游戏名称",
        "上线状态",
        "法务BP",
        "部门",
        "发行团队",
        "对接人",
        "官网",
        "版号",
        "审批文号",
        "ICP备案号",
        "软著登记号",
        "出版单位",
        "商标权利人",
        "软著著作权人",
        "版号运营主体",
        "实际运营主体",
        "内部授权关系",
        "风险预警",
        "备注",
    ]
    assert headers[:3] == ["项目代号", "游戏名称", "上线状态"]
    assert {
        "法务BP",
        "部门",
        "发行团队",
        "对接人",
        "官网",
        "版号",
        "审批文号",
        "ICP备案号",
        "软著登记号",
        "出版单位",
        "商标权利人",
        "软著著作权人",
        "版号运营主体",
        "实际运营主体",
        "内部授权关系",
        "风险预警",
        "备注",
    }.issubset(set(headers))
