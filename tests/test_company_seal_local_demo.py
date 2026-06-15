from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

from legal_mcp import db
from legal_mcp.identity import ROLE_BUSINESS
from legal_mcp.policy import AccessContext
from legal_mcp.tools import call_tool


REPO_ROOT = Path(__file__).resolve().parents[1]
SEED_PATH = REPO_ROOT / "examples" / "legal-demo" / "seed_server_db.py"

_spec = importlib.util.spec_from_file_location("legal_demo_seed", SEED_PATH)
seed_mod = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(seed_mod)


def _business_context(database_path: Path) -> AccessContext:
    conn = db.connect(database_path)
    try:
        row = conn.execute(
            "select * from users where role = ? order by id limit 1", (ROLE_BUSINESS,)
        ).fetchone()
        return AccessContext.from_user(dict(row))
    finally:
        conn.close()


def test_business_account_gets_managed_company_seal_status_table(tmp_path: Path) -> None:
    database_path = tmp_path / "legal-demo-server.db"
    seed_mod.seed_server_db(database_path)

    result = call_tool(
        "agent_query",
        {"question": "我管理的公司印章状态", "rationale": "local seal status demo"},
        database_path=database_path,
        access_context=_business_context(database_path),
    )

    assert result["status"] == "success"
    answer = result["answer"]
    assert "| 公司 | 印章类型 | 现在状态 | 保管地点 | 外借人 | 外借时间 | 外借原因 | 预计归还时间 | 实际归还时间 |" in answer
    assert "上海青岚科技有限公司" in answer
    assert "北京星河互动有限公司" in answer
    assert "深圳云舟网络有限公司" not in answer
    assert "| 上海青岚科技有限公司 | 合同章 | 外借中 |" in answer
    assert "| 北京星河互动有限公司 | 财务章 | 外借中 |" in answer
    assert "OA-20260612-001 线下签署渠道合作协议" in answer


def test_seeded_business_user_has_company_access_not_all_companies(tmp_path: Path) -> None:
    database_path = tmp_path / "legal-demo-server.db"
    seed_mod.seed_server_db(database_path)
    context = _business_context(database_path)

    conn = db.connect(database_path)
    try:
        rows = conn.execute(
            """
            select companies.name
            from company_access
            join companies on companies.id = company_access.company_id
            where company_access.user_id = ?
            order by companies.name
            """,
            (context.user_id,),
        ).fetchall()
    finally:
        conn.close()

    assert [row["name"] for row in rows] == [
        "上海青岚科技有限公司",
        "北京星河互动有限公司",
    ]
