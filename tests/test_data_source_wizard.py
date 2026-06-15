"""Add-data-source wizard + guardrails (v0.5.7).

Covers the pure core (config assembly, introspection, registration), the HTTP
happy path (introspect -> review -> register), the admin-only guardrail, and the
default-deny security contract for a wizard-onboarded source.
"""

from __future__ import annotations

import http.cookiejar
import json
import threading
import urllib.parse
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from legal_mcp import db
from legal_mcp.admin_data_sources import (
    ColumnReview,
    build_source_config,
    introspect_columns,
    register_data_source,
)
from legal_mcp.admin_server import build_admin_server
from legal_mcp.agent_graph import run_structured_query
from legal_mcp.identity import ROLE_BUSINESS, ROLE_ADMIN, create_user, hash_password
from legal_mcp.policy import AccessContext


def _seed(tmp_path: Path) -> Path:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    return database_path


def _csv(tmp_path: Path) -> Path:
    path = tmp_path / "vendors.csv"
    path.write_text("vendor_code,name,owner\nV1,Acme,alice\n", encoding="utf-8")
    return path


# --- pure core ----------------------------------------------------------------


def test_build_source_config_includes_only_ticked_columns() -> None:
    config = build_source_config(
        source_type="local_file",
        name="vendors",
        domain="vendor",
        connect={"path": "/data/v.csv", "format": "csv"},
        columns=[
            ColumnReview("vendor_code", include=True, is_identity=True, aliases=("代号",)),
            ColumnReview("name", include=True, is_identity=True, aliases=()),
            ColumnReview("owner", include=False, is_identity=False, aliases=()),
        ],
        record_scope=None,
    )
    domain = config["domains"][0]
    field_names = {f["name"] for f in domain["fields"]}
    assert field_names == {"vendor_code", "name"}  # 'owner' un-ticked -> excluded
    assert domain["record_scope"] == {"mode": "none"}  # default-deny default
    code = next(f for f in domain["fields"] if f["name"] == "vendor_code")
    assert code["is_identity"] is True
    assert code["aliases"] == ["代号"]


def test_build_source_config_requires_a_selection() -> None:
    with pytest.raises(ValueError, match="at least one column"):
        build_source_config(
            source_type="local_file",
            name="v",
            domain="vendor",
            connect={"path": "x", "format": "csv"},
            columns=[ColumnReview("a", include=False, is_identity=False, aliases=())],
            record_scope=None,
        )


def test_build_source_config_rejects_unknown_type() -> None:
    with pytest.raises(ValueError, match="unsupported source type"):
        build_source_config(
            source_type="parquet_lake",
            name="v",
            domain="d",
            connect={},
            columns=[ColumnReview("a", include=True, is_identity=False, aliases=())],
            record_scope=None,
        )


def test_introspect_columns_lists_real_columns(tmp_path: Path) -> None:
    database_path = _seed(tmp_path)
    csv_path = _csv(tmp_path)
    columns = introspect_columns(
        "local_file", {"path": str(csv_path), "format": "csv"}, database_path=str(database_path)
    )
    assert columns == ("vendor_code", "name", "owner")


def test_register_data_source_writes_active_row_and_audits(tmp_path: Path) -> None:
    database_path = _seed(tmp_path)
    csv_path = _csv(tmp_path)
    audit_path = tmp_path / "audit.jsonl"
    config = build_source_config(
        source_type="local_file",
        name="vendors",
        domain="vendor",
        connect={"path": str(csv_path), "format": "csv"},
        columns=[ColumnReview("vendor_code", include=True, is_identity=True, aliases=())],
        record_scope=None,
    )
    conn = db.connect(database_path)
    try:
        register_data_source(
            conn,
            name="vendors",
            source_type="local_file",
            config=config,
            database_path=str(database_path),
            created_by_user_id=None,
            audit_path=audit_path,
        )
        row = conn.execute(
            "select status, type, config_json from data_sources where name = 'vendors'"
        ).fetchone()
    finally:
        conn.close()
    assert row["status"] == "active"
    assert row["type"] == "local_file"
    assert "vendor" in row["config_json"]
    assert "admin.data_source.register" in audit_path.read_text()


def test_register_rejects_invalid_config(tmp_path: Path) -> None:
    database_path = _seed(tmp_path)
    # A local_file config pointing at a bad format fails to build -> not persisted.
    bad = {
        "type": "local_file",
        "name": "bad",
        "domains": [{"name": "x", "path": "/nope", "format": "parquet", "fields": []}],
    }
    conn = db.connect(database_path)
    try:
        with pytest.raises(ValueError):
            register_data_source(
                conn,
                name="bad",
                source_type="local_file",
                config=bad,
                database_path=str(database_path),
                created_by_user_id=None,
                audit_path=tmp_path / "a.jsonl",
            )
        count = conn.execute("select count(*) c from data_sources").fetchone()["c"]
    finally:
        conn.close()
    assert count == 0


# --- security contract: default-deny for a wizard-onboarded source ------------


def test_wizard_source_is_default_deny_to_a_non_admin(tmp_path: Path) -> None:
    database_path = _seed(tmp_path)
    csv_path = _csv(tmp_path)
    config = build_source_config(
        source_type="local_file",
        name="vendors",
        domain="vendor",
        connect={"path": str(csv_path), "format": "csv"},
        columns=[
            ColumnReview("vendor_code", include=True, is_identity=True, aliases=()),
            ColumnReview("name", include=True, is_identity=True, aliases=()),
            ColumnReview("owner", include=True, is_identity=False, aliases=()),
        ],
        record_scope=None,
    )
    conn = db.connect(database_path)
    try:
        register_data_source(
            conn,
            name="vendors",
            source_type="local_file",
            config=config,
            database_path=str(database_path),
            created_by_user_id=None,
            audit_path=tmp_path / "a.jsonl",
        )
        user = create_user(conn, email="biz@x.local", display_name="B", role=ROLE_BUSINESS)
        conn.commit()
        context = AccessContext(user_id=int(user["id"]), role=ROLE_BUSINESS)
    finally:
        conn.close()

    # A non-admin with no grants is denied the new source's non-identity field
    # (identity fields are exempt by design; 'owner' is the gated one).
    result = run_structured_query(
        query={
            "domain": "vendor",
            "operation": "search",
            "filters": [{"field": "vendor_code", "operator": "eq", "value": "V1"}],
            "return_fields": ["owner"],
            "limit": 5,
        },
        database_path=database_path,
        checkpoint_path=tmp_path / "ckpt.sqlite",
        audit_path=tmp_path / "audit.jsonl",
        access_context=context,
    )
    assert result["status"] == "error"
    assert "denied" in result["error"]["code"]


# --- HTTP integration ---------------------------------------------------------


@contextmanager
def _running_admin_server(database_path: Path) -> Iterator[str]:
    server = build_admin_server(host="127.0.0.1", port=0, database_path=database_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _admin_db(path: Path) -> None:
    db.initialize_database(path)
    conn = db.connect(path)
    try:
        create_user(
            conn,
            email="admin@example.com",
            display_name="Admin",
            role=ROLE_ADMIN,
            password_hash=hash_password("secret"),
        )
        conn.commit()
    finally:
        conn.close()


def _logged_in_opener(base_url: str) -> urllib.request.OpenerDirector:
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
    )
    body = urllib.parse.urlencode({"email": "admin@example.com", "password": "secret"}).encode()
    request = urllib.request.Request(
        f"{base_url}/login", data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST",
    )
    with opener.open(request, timeout=5) as response:
        assert response.status == 200
    return opener


def _post(opener, url, fields):
    request = urllib.request.Request(
        url, data=urllib.parse.urlencode(fields).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST",
    )
    return opener.open(request, timeout=5)


def test_wizard_http_happy_path(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    _admin_db(database_path)
    csv_path = _csv(tmp_path)
    with _running_admin_server(database_path) as base_url:
        opener = _logged_in_opener(base_url)
        # Step 1->2: introspect lists the real columns in a review form.
        with _post(
            opener,
            f"{base_url}/admin/data-sources/introspect",
            {"name": "vendors", "domain": "vendor", "type": "local_file",
             "path": str(csv_path), "format": "csv"},
        ) as response:
            review = response.read().decode()
        assert "vendor_code" in review and "owner" in review
        # Step 3: register, ticking two columns. Unticked 'owner' is excluded.
        with _post(
            opener,
            f"{base_url}/admin/data-sources/register",
            {
                "name": "vendors", "domain": "vendor", "type": "local_file",
                "path": str(csv_path), "format": "csv",
                "include__vendor_code": "on", "identity__vendor_code": "on",
                "include__name": "on", "identity__name": "on",
                "record_scope_mode": "none",
            },
        ) as response:
            assert response.status == 200

    conn = db.connect(database_path)
    try:
        row = conn.execute(
            "select status, config_json from data_sources where name = 'vendors'"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None and row["status"] == "active"
    fields = {f["name"] for f in json.loads(row["config_json"])["domains"][0]["fields"]}
    assert fields == {"vendor_code", "name"}  # 'owner' was not ticked


def test_wizard_register_requires_admin(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    _admin_db(database_path)
    csv_path = _csv(tmp_path)
    with _running_admin_server(database_path) as base_url:
        # No login: an unauthenticated POST is redirected to /login, writes nothing.
        anon = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with _post(
            anon,
            f"{base_url}/admin/data-sources/register",
            {"name": "x", "domain": "d", "type": "local_file",
             "path": str(csv_path), "format": "csv", "include__vendor_code": "on"},
        ) as response:
            # Redirected to the login page rather than registering.
            assert "/login" in response.geturl() or response.status == 200

    conn = db.connect(database_path)
    try:
        count = conn.execute("select count(*) c from data_sources").fetchone()["c"]
    finally:
        conn.close()
    assert count == 0
