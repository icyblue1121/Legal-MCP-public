from __future__ import annotations

from pathlib import Path
from typing import Any

import importlib.util

from legal_mcp import db
from legal_mcp.connector_config import build_connector_setup
from legal_mcp.connectors.feishu_bitable import FeishuBitableConnector
from legal_mcp.identity import ROLE_BUSINESS, create_user
from legal_mcp.policy import AccessContext
from legal_mcp.tools import call_tool


REPO_ROOT = Path(__file__).resolve().parents[1]
CREATE_FEISHU_PATH = REPO_ROOT / "examples" / "legal-demo" / "create_feishu_bitable.py"
_spec = importlib.util.spec_from_file_location("create_feishu_bitable", CREATE_FEISHU_PATH)
create_feishu_bitable = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(create_feishu_bitable)

SEAL_SOURCE = {
    "type": "feishu_bitable",
    "app_token": "bascnSealDemo",
    "domains": [
        {
            "name": "seal",
            "table_id": "tblSeal",
            "record_scope": {
                "mode": "by_owner",
                "field": "custodian_email",
                "subject": "email",
            },
            "fields": [
                {"name": "company", "is_identity": True, "aliases": ["公司"]},
                {"name": "seal_type", "is_identity": True, "aliases": ["印章类型"]},
                {"name": "custodian", "aliases": ["保管人"]},
                {"name": "custodian_email", "aliases": ["保管人邮箱"]},
                {"name": "storage_location", "aliases": ["保管地点"]},
                {"name": "status", "aliases": ["现在状态"]},
                {"name": "borrower", "aliases": ["外借人"]},
                {"name": "borrowed_at", "aliases": ["外借时间"]},
                {"name": "borrow_reason", "aliases": ["外借原因"]},
                {"name": "expected_return_at", "aliases": ["预计归还时间"]},
                {"name": "actual_return_at", "aliases": ["实际归还时间"]},
            ],
        }
    ],
}


SEAL_ROWS = [
    {
        "company": "上海青岚科技有限公司",
        "seal_type": "公章",
        "custodian": "Alice Chen",
        "custodian_email": "alice.seals@example.com",
        "storage_location": "上海总部 18F 法务保险柜 A01",
        "status": "在手中",
        "borrower": "",
        "borrowed_at": "",
        "borrow_reason": "",
        "expected_return_at": "",
        "actual_return_at": "",
    },
    {
        "company": "上海青岚科技有限公司",
        "seal_type": "合同章",
        "custodian": "Alice Chen",
        "custodian_email": "alice.seals@example.com",
        "storage_location": "上海总部 18F 法务保险柜 A02",
        "status": "外借中",
        "borrower": "王浩",
        "borrowed_at": "2026-06-12 10:30",
        "borrow_reason": "OA-20260612-001 线下签署渠道合作协议",
        "expected_return_at": "2026-06-13 18:00",
        "actual_return_at": "",
    },
    {
        "company": "上海青岚科技有限公司",
        "seal_type": "财务章",
        "custodian": "Alice Chen",
        "custodian_email": "alice.seals@example.com",
        "storage_location": "上海总部 18F 财务保险柜 F01",
        "status": "在手中",
        "borrower": "",
        "borrowed_at": "",
        "borrow_reason": "",
        "expected_return_at": "",
        "actual_return_at": "",
    },
    {
        "company": "北京星河互动有限公司",
        "seal_type": "公章",
        "custodian": "Alice Chen",
        "custodian_email": "alice.seals@example.com",
        "storage_location": "北京分部 9F 行政保险柜 B01",
        "status": "在手中",
        "borrower": "",
        "borrowed_at": "",
        "borrow_reason": "",
        "expected_return_at": "",
        "actual_return_at": "",
    },
    {
        "company": "北京星河互动有限公司",
        "seal_type": "合同章",
        "custodian": "Alice Chen",
        "custodian_email": "alice.seals@example.com",
        "storage_location": "北京分部 9F 行政保险柜 B02",
        "status": "在手中",
        "borrower": "",
        "borrowed_at": "",
        "borrow_reason": "",
        "expected_return_at": "",
        "actual_return_at": "",
    },
    {
        "company": "北京星河互动有限公司",
        "seal_type": "财务章",
        "custodian": "Alice Chen",
        "custodian_email": "alice.seals@example.com",
        "storage_location": "北京分部 9F 财务保险柜 BF01",
        "status": "外借中",
        "borrower": "刘敏",
        "borrowed_at": "2026-06-11 15:00",
        "borrow_reason": "OA-20260611-004 银行开户材料补盖章",
        "expected_return_at": "2026-06-12 17:00",
        "actual_return_at": "",
    },
    {
        "company": "深圳云舟网络有限公司",
        "seal_type": "公章",
        "custodian": "Bob Li",
        "custodian_email": "bob.seals@example.com",
        "storage_location": "深圳办公室 12F 法务保险柜 S01",
        "status": "在手中",
        "borrower": "",
        "borrowed_at": "",
        "borrow_reason": "",
        "expected_return_at": "",
        "actual_return_at": "",
    },
    {
        "company": "深圳云舟网络有限公司",
        "seal_type": "合同章",
        "custodian": "Bob Li",
        "custodian_email": "bob.seals@example.com",
        "storage_location": "深圳办公室 12F 法务保险柜 S02",
        "status": "外借中",
        "borrower": "赵强",
        "borrowed_at": "2026-06-12 09:15",
        "borrow_reason": "OA-20260612-006 供应商补充协议用印",
        "expected_return_at": "2026-06-14 12:00",
        "actual_return_at": "",
    },
    {
        "company": "深圳云舟网络有限公司",
        "seal_type": "财务章",
        "custodian": "Bob Li",
        "custodian_email": "bob.seals@example.com",
        "storage_location": "深圳办公室 12F 财务保险柜 SF01",
        "status": "在手中",
        "borrower": "",
        "borrowed_at": "",
        "borrow_reason": "",
        "expected_return_at": "",
        "actual_return_at": "",
    },
]


class _FakeFeishuClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def search_records(self, *, table_id, field_names, filter, page_size):
        self.calls.append(
            {
                "table_id": table_id,
                "field_names": field_names,
                "filter": filter,
                "page_size": page_size,
            }
        )
        rows = SEAL_ROWS
        for condition in (filter or {}).get("conditions") or []:
            if condition["operator"] != "is":
                continue
            expected = set(condition["value"])
            rows = [
                row
                for row in rows
                if str(row.get(condition["field_name"], "")) in expected
            ]
        return [{field: row[field] for field in field_names if field in row} for row in rows]


def _seed_custodian(database_path: Path) -> AccessContext:
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        user = create_user(
            conn,
            email="alice.seals@example.com",
            display_name="Alice Chen",
            role=ROLE_BUSINESS,
        )
        group_id = conn.execute(
            "insert into user_groups (name) values (?)", ("seal-custodians",)
        ).lastrowid
        conn.execute(
            "insert into user_group_memberships (user_id, group_id) values (?, ?)",
            (user["id"], group_id),
        )
        for field in (
            "seal_type",
            "custodian",
            "storage_location",
            "status",
            "borrower",
            "borrowed_at",
            "borrow_reason",
            "expected_return_at",
            "actual_return_at",
        ):
            conn.execute(
                "insert into permission_grants "
                "(group_id, operation, data_domain, field_name, project_id) "
                "values (?, 'read', 'seal', ?, null)",
                (group_id, field),
            )
        conn.commit()
        return AccessContext.from_user(user)
    finally:
        conn.close()


def test_custodian_queries_owned_company_seal_status_after_oa_borrow(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    context = _seed_custodian(database_path)
    fake_client = _FakeFeishuClient()
    setup = build_connector_setup(
        {"sources": [SEAL_SOURCE]},
        database_path=database_path,
        feishu_connector_factory=lambda config: FeishuBitableConnector(
            config, fake_client
        ),
    )

    result = call_tool(
        "structured_query",
        {
            "query": {
                "domain": "seal",
                "operation": "list",
                "filters": [],
                "return_fields": [
                    "company",
                    "seal_type",
                    "custodian",
                    "storage_location",
                    "status",
                    "borrower",
                    "borrowed_at",
                    "borrow_reason",
                    "expected_return_at",
                    "actual_return_at",
                ],
                "limit": 20,
            },
            "rationale": "custodian asks for all seal status after OA borrow approval",
        },
        database_path=database_path,
        access_context=context,
        connector_setup=setup,
    )

    assert result["status"] == "success"
    rows = result["result"]["seal"]
    assert len(rows) == 6
    assert {row["company"] for row in rows} == {
        "上海青岚科技有限公司",
        "北京星河互动有限公司",
    }
    assert {
        (row["company"], row["seal_type"])
        for row in rows
        if row["status"] == "外借中"
    } == {
        ("上海青岚科技有限公司", "合同章"),
        ("北京星河互动有限公司", "财务章"),
    }
    assert "深圳云舟网络有限公司" not in str(result)
    assert fake_client.calls[0]["filter"] == {
        "conjunction": "and",
        "conditions": [
            {
                "field_name": "custodian_email",
                "operator": "is",
                "value": ["alice.seals@example.com"],
            }
        ],
    }


def test_feishu_demo_dry_run_creates_company_seal_management_table(capsys) -> None:
    status = create_feishu_bitable.main(["--dry-run"])

    assert status == 0
    output = capsys.readouterr().out
    assert "公司印章管理" in output
    assert "9 rows total" not in output
    assert "35 rows total" in output
    assert "name: seal" in output
    assert 'record_scope: {"mode": "by_owner", "field": "custodian_email", "subject": "email"}' in output
    assert '"公司"' in output
