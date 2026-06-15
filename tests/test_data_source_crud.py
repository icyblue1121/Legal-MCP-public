"""Data Sources CRUD over the runtime registry (v0.5.8).

Enable/disable and delete a registered source, with the change taking effect on
the next request (hot). Credentials are an env-var *reference*, never a stored
secret value.
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

from legal_mcp import db
from legal_mcp.admin_data_sources import build_source_config, register_data_source
from legal_mcp.admin_data_sources import ColumnReview
from legal_mcp.admin_server import build_admin_server
from legal_mcp.connector_config import effective_connector_setup
from legal_mcp.identity import ROLE_ADMIN, create_user, hash_password


def _seed(tmp_path: Path) -> tuple[Path, Path]:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    csv_path = tmp_path / "vendors.csv"
    csv_path.write_text("vendor_code,name\nV1,Acme\n", encoding="utf-8")
    return database_path, csv_path


def _register(database_path: Path, csv_path: Path) -> None:
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
            audit_path=database_path.parent / "audit.jsonl",
        )
    finally:
        conn.close()


# --- db helpers ---------------------------------------------------------------


def test_status_disable_takes_domain_out_hot(tmp_path: Path) -> None:
    database_path, csv_path = _seed(tmp_path)
    _register(database_path, csv_path)
    assert "vendor" in effective_connector_setup(None, database_path).connector_domains

    conn = db.connect(database_path)
    try:
        assert db.set_data_source_status(conn, "vendors", status="disabled") is True
    finally:
        conn.close()
    # Disabled -> excluded from the live setup on the next request.
    assert effective_connector_setup(None, database_path) is None

    conn = db.connect(database_path)
    try:
        assert db.set_data_source_status(conn, "vendors", status="active") is True
    finally:
        conn.close()
    assert "vendor" in effective_connector_setup(None, database_path).connector_domains


def test_delete_removes_source(tmp_path: Path) -> None:
    database_path, csv_path = _seed(tmp_path)
    _register(database_path, csv_path)
    conn = db.connect(database_path)
    try:
        assert db.delete_data_source(conn, "vendors") is True
        assert db.delete_data_source(conn, "vendors") is False  # already gone
        assert db.list_data_sources(conn) == []
    finally:
        conn.close()
    assert effective_connector_setup(None, database_path) is None


def test_set_status_rejects_unknown_status(tmp_path: Path) -> None:
    database_path, csv_path = _seed(tmp_path)
    _register(database_path, csv_path)
    conn = db.connect(database_path)
    try:
        try:
            db.set_data_source_status(conn, "vendors", status="bogus")
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError for unknown status")
    finally:
        conn.close()


def test_secret_ref_is_a_reference_not_a_value(tmp_path: Path) -> None:
    # The credential model is env-reference: secret_ref holds an env var NAME.
    database_path, _ = _seed(tmp_path)
    conn = db.connect(database_path)
    try:
        conn.execute(
            "insert into data_sources (name, type, status, config_json, secret_ref) "
            "values (?, ?, 'draft', '{}', ?)",
            ("online-src", "feishu_bitable", "FEISHU_APP_SECRET"),
        )
        conn.commit()
        row = next(r for r in db.list_data_sources(conn) if r["name"] == "online-src")
    finally:
        conn.close()
    assert row["secret_ref"] == "FEISHU_APP_SECRET"  # a name, not a secret


# --- HTTP CRUD ----------------------------------------------------------------


@contextmanager
def _server(database_path: Path) -> Iterator[str]:
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
    conn = db.connect(path)
    try:
        create_user(
            conn, email="admin@example.com", display_name="Admin",
            role=ROLE_ADMIN, password_hash=hash_password("secret"),
        )
        conn.commit()
    finally:
        conn.close()


def _login(base_url: str) -> urllib.request.OpenerDirector:
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


def test_http_crud_lists_disables_and_deletes(tmp_path: Path) -> None:
    database_path, csv_path = _seed(tmp_path)
    _admin_db(database_path)
    _register(database_path, csv_path)

    with _server(database_path) as base_url:
        opener = _login(base_url)
        # The registry section lists the source with its status.
        with opener.open(f"{base_url}/admin/database", timeout=5) as response:
            page = response.read().decode()
        assert "Runtime-registered sources" in page
        assert "vendors" in page

        # Disable it.
        with _post(opener, f"{base_url}/admin/data-sources/status",
                   {"name": "vendors", "status": "disabled"}) as response:
            assert response.status == 200
        conn = db.connect(database_path)
        try:
            assert conn.execute(
                "select status from data_sources where name = 'vendors'"
            ).fetchone()["status"] == "disabled"
        finally:
            conn.close()

        # Delete it.
        with _post(opener, f"{base_url}/admin/data-sources/delete",
                   {"name": "vendors"}) as response:
            assert response.status == 200
        conn = db.connect(database_path)
        try:
            assert conn.execute("select count(*) c from data_sources").fetchone()["c"] == 0
        finally:
            conn.close()
