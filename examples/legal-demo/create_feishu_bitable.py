#!/usr/bin/env python3
"""Create a richer demo Feishu (Lark) Bitable for the Legal-MCP connector demo.

Builds a multi-table Bitable you can open and see in Feishu, populated with
synthetic legal-ops data, and prints a ready-to-use connector config pointing at
the tables it created. The gateway then serves the ``project`` domain LIVE from
this Bitable (the proven mixed-source path), while the connector only ever sees
the declared columns.

Security
--------
* Credentials are read from the environment, NEVER passed on the command line and
  NEVER printed:  FEISHU_APP_ID, FEISHU_APP_SECRET.
* The Bitable **column headers are the English canonical field names** the
  connector queries by (project_code, name, legal_bp, ...). Chinese is handled by
  the connector's `aliases` (a Chinese question is mapped to the canonical field
  in the gateway), so the same table works for natural-language questions.

Prerequisites (Feishu Open Platform -> your custom app)
-------------------------------------------------------
* Permissions (scopes):  bitable:app  (create/read Bitable) and  drive:drive
  (create files in a folder).  Without drive:drive the app cannot place the new
  Bitable in a folder you can see.
* A target FOLDER you can open in Feishu, shared with the app (so you can see the
  result). Pass its token with --folder or FEISHU_FOLDER_TOKEN. The folder token
  is the last path segment of the folder URL (.../drive/folder/<FOLDER_TOKEN>).

Usage
-----
    export FEISHU_APP_ID=cli_xxx
    export FEISHU_APP_SECRET=xxx
    python examples/legal-demo/create_feishu_bitable.py \
        --folder fldcnXXXXXXXX \
        --name "Legal-MCP 演示数据" \
        --write-config data/feishu-demo.connector.yaml

    # Preview the tables/rows without any network call or credentials:
    python examples/legal-demo/create_feishu_bitable.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

BASE_URL_DEFAULT = "https://open.feishu.cn"

# Feishu Bitable field type ids used here: 1 = text (multi-line), 2 = number.
TEXT = 1
NUMBER = 2

# --- The demo dataset --------------------------------------------------------
# project_code values intentionally include MOON / STAR / NOVA so this table is
# coherent with examples/legal-demo/seed_server_db.py (record-scope binds a Feishu
# row to a governance project by project_code). The rest enrich the view.

PROJECT_TABLE = {
    "name": "项目",
    "fields": [
        ("project_code", TEXT),
        ("name", TEXT),
        ("stage", TEXT),
        ("legal_bp", TEXT),
        ("department", TEXT),
        ("release_team", TEXT),
        ("contact_person", TEXT),
        ("website", TEXT),
        ("notes", TEXT),
    ],
    "rows": [
        ["MOON", "Project Moon 月之子", "live", "BP-Morgan", "发行一部", "曙光发行组", "Alice Chen", "https://moon.example", "旗舰二次元项目"],
        ["STAR", "Project Star 群星", "planning", "BP-Quinn", "发行二部", "银河发行组", "Bob Li", "https://star.example", "立项评审中"],
        ["NOVA", "Project Nova 新星", "live", "BP-Reed", "发行一部", "曙光发行组", "Carol Wu", "https://nova.example", "海外同步上线"],
        ["AURORA", "Project Aurora 极光", "soft_launch", "BP-Morgan", "海外发行部", "极地发行组", "David Han", "https://aurora.example", "东南亚小范围测试"],
        ["COMET", "Project Comet 彗星", "live", "BP-Quinn", "发行二部", "银河发行组", "Erin Zhao", "https://comet.example", "买量中"],
        ["PULSAR", "Project Pulsar 脉冲", "sunset", "BP-Reed", "发行一部", "曙光发行组", "Frank Sun", "https://pulsar.example", "停服公告已发"],
        ["QUASAR", "Project Quasar 类星体", "live", "BP-Morgan", "海外发行部", "极地发行组", "Grace Lin", "https://quasar.example", "日服流水稳定"],
        ["ORBIT", "Project Orbit 轨道", "planning", "BP-Quinn", "发行二部", "银河发行组", "Henry Ma", "https://orbit.example", "IP 授权谈判中"],
    ],
}

CONTRACT_TABLE = {
    "name": "合同",
    "fields": [
        ("contract_number", TEXT),
        ("project_code", TEXT),
        ("title", TEXT),
        ("counterparty", TEXT),
        ("company_entity", TEXT),
        ("handler", TEXT),
        ("total_amount", NUMBER),
        ("currency", TEXT),
        ("signed_date", TEXT),
        ("expiry_date", TEXT),
    ],
    "rows": [
        ["HT-2026-001", "MOON", "腾讯独家发行框架协议", "腾讯科技（深圳）有限公司", "上海游碧曜网络科技有限公司", "王法务", 5000000, "CNY", "2026-01-15", "2028-01-14"],
        ["HT-2026-002", "MOON", "字节跳动联运协议", "北京字节跳动科技有限公司", "上海游碧曜网络科技有限公司", "王法务", 1200000, "CNY", "2026-02-01", "2027-01-31"],
        ["HT-2026-003", "STAR", "美术外包合同", "杭州原力数字科技有限公司", "上海游碧曜网络科技有限公司", "李法务", 800000, "CNY", "2026-03-10", "2026-12-31"],
        ["HT-2026-004", "NOVA", "海外发行代理协议", "Sea Limited", "Yobi Game Pte. Ltd.", "张法务", 2000000, "USD", "2026-02-20", "2029-02-19"],
        ["HT-2026-005", "NOVA", "音乐授权许可合同", "摩登天空文化发展有限公司", "上海游碧曜网络科技有限公司", "李法务", 300000, "CNY", "2026-04-01", "2028-03-31"],
        ["HT-2026-006", "AURORA", "云服务采购合同", "阿里云计算有限公司", "Yobi Game Pte. Ltd.", "张法务", 1500000, "CNY", "2026-01-05", "2027-01-04"],
        ["HT-2026-007", "COMET", "买量投放服务协议", "上海巨量引擎网络技术有限公司", "上海游碧曜网络科技有限公司", "王法务", 3000000, "CNY", "2026-03-01", "2026-12-31"],
        ["HT-2026-008", "QUASAR", "日本发行联运协议", "株式会社ミクシィ", "Yobi Game KK", "张法务", 4500000, "JPY", "2026-02-15", "2028-02-14"],
        ["HT-2026-009", "ORBIT", "IP 授权意向书", "万代南梦宫娱乐", "上海游碧曜网络科技有限公司", "李法务", 0, "CNY", "2026-05-01", "2026-08-01"],
        ["HT-2026-010", "PULSAR", "停服数据迁移服务合同", "深圳市腾讯计算机系统有限公司", "上海游碧曜网络科技有限公司", "王法务", 200000, "CNY", "2026-04-15", "2026-07-15"],
    ],
}

LICENSE_TABLE = {
    "name": "证照",
    "fields": [
        ("project_code", TEXT),
        ("license_type", TEXT),
        ("rights_holder", TEXT),
        ("operating_entity", TEXT),
        ("actual_operator", TEXT),
        ("approval_number", TEXT),
        ("expiry_date", TEXT),
    ],
    "rows": [
        ["MOON", "版号", "上海游碧曜网络科技有限公司", "上海游碧曜网络科技有限公司", "腾讯科技（深圳）有限公司", "ISBN 978-7-498-12345-6", "长期有效"],
        ["MOON", "商标权", "上海游碧曜网络科技有限公司", "上海游碧曜网络科技有限公司", "上海游碧曜网络科技有限公司", "第 60123456 号", "2034-05-20"],
        ["STAR", "著作权登记", "上海游碧曜网络科技有限公司", "上海游碧曜网络科技有限公司", "上海游碧曜网络科技有限公司", "国作登字-2026-A-00012345", "长期有效"],
        ["NOVA", "版号", "上海游碧曜网络科技有限公司", "Yobi Game Pte. Ltd.", "Sea Limited", "ISBN 978-7-498-23456-7", "长期有效"],
        ["NOVA", "ICP 备案", "Yobi Game Pte. Ltd.", "Yobi Game Pte. Ltd.", "Yobi Game Pte. Ltd.", "沪ICP备2026012345号", "2027-06-30"],
        ["AURORA", "商标权", "上海游碧曜网络科技有限公司", "Yobi Game Pte. Ltd.", "Yobi Game Pte. Ltd.", "第 60223456 号", "2035-01-10"],
        ["QUASAR", "版号", "上海游碧曜网络科技有限公司", "Yobi Game KK", "株式会社ミクシィ", "ISBN 978-7-498-34567-8", "长期有效"],
        ["COMET", "文网文", "上海游碧曜网络科技有限公司", "上海游碧曜网络科技有限公司", "上海游碧曜网络科技有限公司", "沪网文〔2026〕1234-567号", "2028-09-30"],
    ],
}

def _seal_row(
    company: str,
    seal_type: str,
    custodian: str,
    custodian_email: str,
    storage_location: str,
    status: str,
    borrower: str = "",
    borrowed_at: str = "",
    borrow_reason: str = "",
    expected_return_at: str = "",
    actual_return_at: str = "",
) -> list[str]:
    return [
        company,
        seal_type,
        custodian,
        custodian_email,
        storage_location,
        status,
        borrower,
        borrowed_at,
        borrow_reason,
        expected_return_at,
        actual_return_at,
    ]


SEAL_TABLE = {
    "name": "公司印章管理",
    "fields": [
        ("company", TEXT),
        ("seal_type", TEXT),
        ("custodian", TEXT),
        ("custodian_email", TEXT),
        ("storage_location", TEXT),
        ("status", TEXT),
        ("borrower", TEXT),
        ("borrowed_at", TEXT),
        ("borrow_reason", TEXT),
        ("expected_return_at", TEXT),
        ("actual_return_at", TEXT),
    ],
    "rows": [
        _seal_row(
            "上海青岚科技有限公司",
            "公章",
            "Alice Chen",
            "alice.seals@example.com",
            "上海总部 18F 法务保险柜 A01",
            "在手中",
        ),
        _seal_row(
            "上海青岚科技有限公司",
            "合同章",
            "Alice Chen",
            "alice.seals@example.com",
            "上海总部 18F 法务保险柜 A02",
            "外借中",
            "王浩",
            "2026-06-12 10:30",
            "OA-20260612-001 线下签署渠道合作协议",
            "2026-06-13 18:00",
        ),
        _seal_row(
            "上海青岚科技有限公司",
            "财务章",
            "Alice Chen",
            "alice.seals@example.com",
            "上海总部 18F 财务保险柜 F01",
            "在手中",
        ),
        _seal_row(
            "北京星河互动有限公司",
            "公章",
            "Alice Chen",
            "alice.seals@example.com",
            "北京分部 9F 行政保险柜 B01",
            "在手中",
        ),
        _seal_row(
            "北京星河互动有限公司",
            "合同章",
            "Alice Chen",
            "alice.seals@example.com",
            "北京分部 9F 行政保险柜 B02",
            "在手中",
        ),
        _seal_row(
            "北京星河互动有限公司",
            "财务章",
            "Alice Chen",
            "alice.seals@example.com",
            "北京分部 9F 财务保险柜 BF01",
            "外借中",
            "刘敏",
            "2026-06-11 15:00",
            "OA-20260611-004 银行开户材料补盖章",
            "2026-06-12 17:00",
        ),
        _seal_row(
            "深圳云舟网络有限公司",
            "公章",
            "Bob Li",
            "bob.seals@example.com",
            "深圳办公室 12F 法务保险柜 S01",
            "在手中",
        ),
        _seal_row(
            "深圳云舟网络有限公司",
            "合同章",
            "Bob Li",
            "bob.seals@example.com",
            "深圳办公室 12F 法务保险柜 S02",
            "外借中",
            "赵强",
            "2026-06-12 09:15",
            "OA-20260612-006 供应商补充协议用印",
            "2026-06-14 12:00",
        ),
        _seal_row(
            "深圳云舟网络有限公司",
            "财务章",
            "Bob Li",
            "bob.seals@example.com",
            "深圳办公室 12F 财务保险柜 SF01",
            "在手中",
        ),
    ],
}

TABLES = [PROJECT_TABLE, CONTRACT_TABLE, LICENSE_TABLE, SEAL_TABLE]

# The project and seal domains are wired to the gateway by default. The
# contract/license tables enrich the Feishu view; wiring them needs matching DB
# grants.
CONNECTOR_DOMAINS = {
    "project": {
        "table": "项目",
        "fields": [
            {"name": "project_code", "is_identity": True, "aliases": ["项目代号", "项目编号"]},
            {"name": "name", "is_identity": True, "aliases": ["项目名称", "游戏名称"]},
            {"name": "stage", "aliases": ["上线状态", "阶段"]},
            {"name": "legal_bp", "aliases": ["法务BP", "法务bp", "法务"]},
            {"name": "department", "aliases": ["所属部门", "部门"]},
            {"name": "release_team", "aliases": ["发行团队", "发行组"]},
            {"name": "contact_person", "aliases": ["对接人", "联系人"]},
            {"name": "website", "aliases": ["官网", "网址"]},
            {"name": "notes", "aliases": ["备注"]},
        ],
    },
    "seal": {
        "table": "公司印章管理",
        "record_scope": {"mode": "by_owner", "field": "custodian_email", "subject": "email"},
        "fields": [
            {"name": "company", "is_identity": True, "aliases": ["公司", "公司名称"]},
            {"name": "seal_type", "is_identity": True, "aliases": ["印章类型", "章类型"]},
            {"name": "custodian", "aliases": ["保管人"]},
            {"name": "custodian_email", "aliases": ["保管人邮箱"]},
            {"name": "storage_location", "aliases": ["保管地点", "存放地点"]},
            {"name": "status", "aliases": ["现在状态", "当前状态", "印章状态"]},
            {"name": "borrower", "aliases": ["外借人", "借用人"]},
            {"name": "borrowed_at", "aliases": ["外借时间", "借出时间"]},
            {"name": "borrow_reason", "aliases": ["外借原因", "借用原因"]},
            {"name": "expected_return_at", "aliases": ["预计归还时间"]},
            {"name": "actual_return_at", "aliases": ["实际归还时间"]},
        ],
    },
}


class FeishuError(RuntimeError):
    pass


def _http_json(method: str, url: str, headers: dict[str, str], payload: Any) -> dict[str, Any]:
    """One HTTP call -> parsed JSON. Feishu returns a JSON {code,msg} body even on
    4xx (e.g. 403 for a missing scope), so read the error body and surface it
    instead of a bare HTTPError — that message tells you exactly what to fix."""
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            raise FeishuError(f"HTTP {exc.code} {exc.reason} from {url}: {raw[:300]}") from exc
        raise FeishuError(
            f"HTTP {exc.code} from {url} — feishu code {body.get('code')}: {body.get('msg')}"
        ) from exc


def _api(method: str, url: str, token: str | None, payload: Any) -> dict[str, Any]:
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = _http_json(method, url, headers, payload)
    if body.get("code", 0) != 0:
        raise FeishuError(f"feishu api error {body.get('code')}: {body.get('msg')}")
    return body.get("data") or {}


def _tenant_token(base_url: str, app_id: str, app_secret: str) -> str:
    # The token endpoint returns the token at the response ROOT (not under data),
    # so read the whole body rather than going through _api (which strips to data).
    body = _http_json(
        "POST",
        f"{base_url}/open-apis/auth/v3/tenant_access_token/internal",
        {"Content-Type": "application/json; charset=utf-8"},
        {"app_id": app_id, "app_secret": app_secret},
    )
    if body.get("code", 0) != 0 or not body.get("tenant_access_token"):
        raise FeishuError(f"could not obtain tenant_access_token: {body.get('msg')}")
    return str(body["tenant_access_token"])


def _create_app(base_url: str, token: str, name: str, folder_token: str | None) -> dict[str, str]:
    payload: dict[str, Any] = {"name": name}
    if folder_token:
        payload["folder_token"] = folder_token
    data = _api("POST", f"{base_url}/open-apis/bitable/v1/apps", token, payload)
    app = data.get("app") or {}
    return {"app_token": app.get("app_token", ""), "url": app.get("url", "")}


def _create_table(base_url: str, token: str, app_token: str, spec: dict[str, Any]) -> str:
    fields = [{"field_name": name, "type": ftype} for name, ftype in spec["fields"]]
    payload = {"table": {"name": spec["name"], "default_view_name": "表格", "fields": fields}}
    data = _api(
        "POST", f"{base_url}/open-apis/bitable/v1/apps/{app_token}/tables", token, payload
    )
    return str(data.get("table_id", ""))


def _insert_rows(base_url: str, token: str, app_token: str, table_id: str, spec: dict[str, Any]) -> int:
    field_names = [name for name, _ in spec["fields"]]
    records = [
        {"fields": {field_names[i]: row[i] for i in range(len(field_names))}}
        for row in spec["rows"]
    ]
    _api(
        "POST",
        f"{base_url}/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_create",
        token,
        {"records": records},
    )
    return len(records)


def _connector_yaml(app_token: str, table_ids: dict[str, str]) -> str:
    lines = [
        "# Generated by create_feishu_bitable.py — points the gateway's `project`",
        "# domain at the live Feishu Bitable created above. Secrets stay in the env.",
        "version: 1",
        "sources:",
        "  - type: feishu_bitable",
        f"    app_token: {app_token}",
        "    app_id_env: FEISHU_APP_ID",
        "    app_secret_env: FEISHU_APP_SECRET",
        "    domains:",
    ]
    for domain, spec in CONNECTOR_DOMAINS.items():
        table_id = table_ids.get(spec["table"], "REPLACE_ME")
        lines.append(f"      - name: {domain}")
        lines.append(f"        table_id: {table_id}")
        if spec.get("record_scope"):
            lines.append(
                "        record_scope: "
                f"{json.dumps(spec['record_scope'], ensure_ascii=False)}"
            )
        lines.append("        fields:")
        for field in spec["fields"]:
            entry = {k: v for k, v in field.items()}
            lines.append(f"          - {json.dumps(entry, ensure_ascii=False)}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create a demo Feishu Bitable for Legal-MCP.")
    parser.add_argument("--name", default="Legal-MCP 演示数据", help="Bitable app name.")
    parser.add_argument("--folder", default=os.environ.get("FEISHU_FOLDER_TOKEN"),
                        help="Target folder token (or set FEISHU_FOLDER_TOKEN). "
                             "Required for the result to be visible in your space.")
    parser.add_argument("--base-url", default=os.environ.get("FEISHU_BASE_URL", BASE_URL_DEFAULT))
    parser.add_argument("--write-config", help="Path to write the generated connector YAML.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the tables/rows that WOULD be created; no network, no creds.")
    args = parser.parse_args(argv)

    if args.dry_run:
        total = sum(len(t["rows"]) for t in TABLES)
        print(f"[dry-run] would create Bitable {args.name!r} with {len(TABLES)} tables, "
              f"{total} rows total:")
        for table in TABLES:
            print(f"  - {table['name']}: {len(table['fields'])} columns, {len(table['rows'])} rows "
                  f"({', '.join(name for name, _ in table['fields'])})")
        print("\n[dry-run] generated connector config preview:\n")
        print(_connector_yaml("bascnDEMO", {t["name"]: f"tbl_{t['name']}" for t in TABLES}))
        return 0

    app_id = os.environ.get("FEISHU_APP_ID")
    app_secret = os.environ.get("FEISHU_APP_SECRET")
    if not app_id or not app_secret:
        print("ERROR: set FEISHU_APP_ID and FEISHU_APP_SECRET in the environment.", file=sys.stderr)
        return 2
    if not args.folder:
        print("WARNING: no --folder / FEISHU_FOLDER_TOKEN given. The Bitable will be owned by the "
              "app and may NOT be visible in your space. Pass a folder you can open in Feishu.",
              file=sys.stderr)

    base_url = args.base_url.rstrip("/")
    print("· obtaining tenant_access_token …")
    token = _tenant_token(base_url, app_id, app_secret)
    print(f"· creating Bitable {args.name!r} …")
    app = _create_app(base_url, token, args.name, args.folder)
    app_token = app["app_token"]

    table_ids: dict[str, str] = {}
    for spec in TABLES:
        table_id = _create_table(base_url, token, app_token, spec)
        n = _insert_rows(base_url, token, app_token, table_id, spec)
        table_ids[spec["name"]] = table_id
        print(f"· table {spec['name']!r}: table_id={table_id}, {n} rows inserted")

    print("\n✅ Done. Open it in Feishu:")
    print(f"   {app.get('url') or '(url not returned; check your folder)'}")
    print(f"   app_token = {app_token}")
    for name, tid in table_ids.items():
        print(f"   table {name} = {tid}")

    config = _connector_yaml(app_token, table_ids)
    if args.write_config:
        os.makedirs(os.path.dirname(args.write_config) or ".", exist_ok=True)
        with open(args.write_config, "w", encoding="utf-8") as handle:
            handle.write(config)
        print(f"\n· wrote connector config -> {args.write_config}")
    else:
        print("\n--- connector config (save it and pass with --connector) ---")
        print(config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
