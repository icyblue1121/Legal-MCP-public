from __future__ import annotations

import contextlib
import json
import sqlite3
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from legal_mcp import db
from legal_mcp import http_server as http_server_module
from legal_mcp.http_server import build_http_server
from legal_mcp.identity import ROLE_BUSINESS, ROLE_LEGAL, create_api_key, create_user

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _database_with_project(path: Path) -> int:
    db.initialize_database(path)
    conn = db.connect(path)
    try:
        cursor = conn.execute(
            """
            insert into projects (project_code, name, stage, release_team, contact_person)
            values (?, ?, ?, ?, ?)
            """,
            ("Acme", "示例项目", "测试中", "上海发行中心", "沪小胖"),
        )
        conn.commit()
        return int(cursor.lastrowid)
    finally:
        conn.close()


@pytest.fixture
def http_service(tmp_path: Path):
    database_path = tmp_path / "legal.db"
    audit_path = tmp_path / "audit.jsonl"
    _database_with_project(database_path)
    server = build_http_server(
        host="127.0.0.1",
        port=0,
        database_path=database_path,
        audit_path=audit_path,
        bearer_token="secret-token",
        allowed_origins=("http://legal.internal",),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _post_json(url: str, body: dict, token: str = "secret-token", origin: str | None = None):
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
    if origin is not None:
        headers["Origin"] = origin
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with _OPENER.open(request, timeout=5) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def _http_error_payload(exc: urllib.error.HTTPError) -> dict:
    return json.loads(exc.read().decode("utf-8"))


def test_healthz_reports_ready(http_service) -> None:
    _, base_url = http_service

    with _OPENER.open(f"{base_url}/healthz", timeout=5) as response:
        payload = json.loads(response.read().decode("utf-8"))

    assert response.status == 200
    assert payload == {"service": "legal-mcp", "database": "ready"}


def test_mcp_get_probe_reports_http_transport(http_service) -> None:
    _, base_url = http_service

    with _OPENER.open(f"{base_url}/mcp", timeout=5) as response:
        payload = json.loads(response.read().decode("utf-8"))

    assert response.status == 200
    assert payload == {
        "service": "legal-mcp",
        "transport": "json-rpc-over-http",
        "endpoint": "/mcp",
        "methods": ["POST"],
    }


def test_http_mcp_rejects_direct_database_tool_call(http_service) -> None:
    _, base_url = http_service

    status, payload = _post_json(
        f"{base_url}/mcp",
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "get_project_fields",
                "arguments": {
                    "project_id_or_name": "Acme",
                    "fields": ["contact_person"],
                    "rationale": "team query",
                    "source_client": "pytest-http",
                },
            },
        },
        origin="http://legal.internal",
    )

    tool_payload = json.loads(payload["result"]["content"][0]["text"])
    assert status == 200
    assert payload["result"]["isError"] is True
    assert tool_payload["error"]["code"] == "tool_not_exposed"


def test_http_mcp_accepts_named_user_api_key_for_granted_project(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    audit_path = tmp_path / "audit.jsonl"
    project_id = _database_with_project(database_path)
    conn = db.connect(database_path)
    try:
        grantor = create_user(
            conn,
            email="grantor@example.com",
            display_name="Grantor",
            role=ROLE_LEGAL,
        )
        business_user = create_user(
            conn,
            email="business@example.com",
            display_name="Business User",
            role=ROLE_BUSINESS,
        )
        conn.execute(
            """
            insert into project_access (user_id, project_id, granted_by_user_id)
            values (?, ?, ?)
            """,
            (business_user["id"], project_id, grantor["id"]),
        )
        group_id = conn.execute(
            "insert into user_groups (name) values (?)",
            ("business-project-code",),
        ).lastrowid
        conn.execute(
            "insert into user_group_memberships (user_id, group_id) values (?, ?)",
            (business_user["id"], group_id),
        )
        conn.execute(
            """
            insert into permission_grants
              (group_id, operation, data_domain, field_name, project_id)
            values (?, ?, ?, ?, ?)
            """,
            (group_id, "read", "project", "project_code", project_id),
        )
        conn.commit()
        api_key = create_api_key(conn, user_id=business_user["id"], label="pytest")
    finally:
        conn.close()

    server = build_http_server(
        host="127.0.0.1",
        port=0,
        database_path=database_path,
        audit_path=audit_path,
        bearer_token="legacy-token",
        allowed_origins=("http://legal.internal",),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, payload = _post_json(
            f"http://127.0.0.1:{server.server_port}/mcp",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "get_project_fields",
                    "arguments": {
                        "project_id_or_name": "Acme",
                        "fields": ["project_code"],
                        "rationale": "team query",
                        "source_client": "pytest-http",
                    },
                },
            },
            token=api_key.plaintext,
            origin="http://legal.internal",
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    tool_payload = json.loads(payload["result"]["content"][0]["text"])
    assert status == 200
    assert payload["result"]["isError"] is True
    assert tool_payload["error"]["code"] == "tool_not_exposed"


def test_http_mcp_requires_client_update_when_version_header_is_too_old(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "legal.db"
    audit_path = tmp_path / "audit.jsonl"
    _database_with_project(database_path)
    monkeypatch.setenv("LEGAL_MCP_MIN_CLIENT_VERSION", "1.4.3")
    server = build_http_server(
        host="127.0.0.1",
        port=0,
        database_path=database_path,
        audit_path=audit_path,
        bearer_token="secret-token",
        allowed_origins=("http://legal.internal",),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/mcp",
            data=b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}',
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer secret-token",
                "X-Legal-MCP-Client-Version": "1.4.2",
            },
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc:
            _OPENER.open(request, timeout=5)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert exc.value.code == 426
    assert _http_error_payload(exc.value) == {
        "error": "client_update_required",
        "minimum_client_version": "1.4.3",
    }


def test_http_mcp_rejects_missing_token(http_service) -> None:
    _, base_url = http_service
    request = urllib.request.Request(
        f"{base_url}/mcp",
        data=b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}',
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with pytest.raises(urllib.error.HTTPError) as exc:
        _OPENER.open(request, timeout=5)

    assert exc.value.code == 401


def test_http_mcp_rejects_disallowed_origin(http_service) -> None:
    _, base_url = http_service

    with pytest.raises(urllib.error.HTTPError) as exc:
        _post_json(
            f"{base_url}/mcp",
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            origin="http://evil.example",
        )

    assert exc.value.code == 403


def test_legacy_token_full_access_defaults_off(tmp_path: Path) -> None:
    # Security default: the legacy shared token does not bypass field/row grants
    # unless the deployment explicitly opts in (v0.4.5 preflight).
    database_path = tmp_path / "legal.db"
    audit_path = tmp_path / "audit.jsonl"
    _database_with_project(database_path)

    default_server = build_http_server(
        host="127.0.0.1",
        port=0,
        database_path=database_path,
        audit_path=audit_path,
        bearer_token="legacy-token",
        allowed_origins=("http://legal.internal",),
    )
    opt_in_server = build_http_server(
        host="127.0.0.1",
        port=0,
        database_path=database_path,
        audit_path=audit_path,
        bearer_token="legacy-token",
        allowed_origins=("http://legal.internal",),
        legacy_token_full_access=True,
    )
    try:
        assert default_server.legacy_token_full_access is False
        assert opt_in_server.legacy_token_full_access is True
    finally:
        default_server.server_close()
        opt_in_server.server_close()


def test_http_mcp_rejects_named_key_disallowed_origin_without_updating_last_used_at(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "legal.db"
    audit_path = tmp_path / "audit.jsonl"
    _database_with_project(database_path)
    conn = db.connect(database_path)
    try:
        user = create_user(
            conn,
            email="business-origin@example.com",
            display_name="Business Origin",
            role=ROLE_BUSINESS,
        )
        api_key = create_api_key(conn, user_id=user["id"], label="pytest")
    finally:
        conn.close()

    server = build_http_server(
        host="127.0.0.1",
        port=0,
        database_path=database_path,
        audit_path=audit_path,
        bearer_token="legacy-token",
        allowed_origins=("http://legal.internal",),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post_json(
                f"http://127.0.0.1:{server.server_port}/mcp",
                {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                token=api_key.plaintext,
                origin="http://evil.example",
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    conn = db.connect(database_path)
    try:
        last_used_at = conn.execute(
            "select last_used_at from api_keys where id = ?",
            (api_key.api_key_id,),
        ).fetchone()["last_used_at"]
    finally:
        conn.close()

    assert exc.value.code == 403
    assert last_used_at is None


def test_http_mcp_returns_auth_unavailable_when_named_key_auth_db_fails(
    http_service,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, base_url = http_service

    def fail_connect(database_path: Path) -> sqlite3.Connection:
        raise sqlite3.OperationalError("database unavailable")

    monkeypatch.setattr(http_server_module.db, "connect", fail_connect)

    with pytest.raises(urllib.error.HTTPError) as exc:
        _post_json(
            f"{base_url}/mcp",
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            token="lmcp_named-token",
            origin="http://legal.internal",
        )

    assert exc.value.code == 503
    assert _http_error_payload(exc.value) == {"error": "auth_unavailable"}


def test_http_mcp_rejects_revoked_named_user_api_key(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    audit_path = tmp_path / "audit.jsonl"
    _database_with_project(database_path)
    conn = db.connect(database_path)
    try:
        user = create_user(
            conn,
            email="revoked-http@example.com",
            display_name="Revoked HTTP",
            role=ROLE_BUSINESS,
        )
        api_key = create_api_key(conn, user_id=user["id"], label="revoked")
        conn.execute(
            "update api_keys set status = 'revoked' where id = ?",
            (api_key.api_key_id,),
        )
        conn.commit()
    finally:
        conn.close()

    server = build_http_server(
        host="127.0.0.1",
        port=0,
        database_path=database_path,
        audit_path=audit_path,
        bearer_token="legacy-token",
        allowed_origins=("http://legal.internal",),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post_json(
                f"http://127.0.0.1:{server.server_port}/mcp",
                {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                token=api_key.plaintext,
                origin="http://legal.internal",
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert exc.value.code == 401


def test_http_mcp_rejects_disabled_named_user_api_key(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    audit_path = tmp_path / "audit.jsonl"
    _database_with_project(database_path)
    conn = db.connect(database_path)
    try:
        user = create_user(
            conn,
            email="disabled-http@example.com",
            display_name="Disabled HTTP",
            role=ROLE_BUSINESS,
        )
        api_key = create_api_key(conn, user_id=user["id"], label="disabled")
        conn.execute(
            "update users set status = 'disabled' where id = ?",
            (user["id"],),
        )
        conn.commit()
    finally:
        conn.close()

    server = build_http_server(
        host="127.0.0.1",
        port=0,
        database_path=database_path,
        audit_path=audit_path,
        bearer_token="legacy-token",
        allowed_origins=("http://legal.internal",),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post_json(
                f"http://127.0.0.1:{server.server_port}/mcp",
                {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                token=api_key.plaintext,
                origin="http://legal.internal",
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert exc.value.code == 401


def test_http_mcp_allows_absent_origin_for_non_browser_clients(http_service) -> None:
    _, base_url = http_service

    status, payload = _post_json(
        f"{base_url}/mcp",
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )

    assert status == 200
    assert [tool["name"] for tool in payload["result"]["tools"]] == ["agent_query"]


def test_http_mcp_can_expose_only_agent_query_with_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "legal.db"
    audit_path = tmp_path / "audit.jsonl"
    _database_with_project(database_path)
    monkeypatch.setenv("LEGAL_MCP_AGENT_PUBLIC_ONLY", "true")
    server = build_http_server(
        host="127.0.0.1",
        port=0,
        database_path=database_path,
        audit_path=audit_path,
        bearer_token="secret-token",
        allowed_origins=("http://legal.internal",),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, payload = _post_json(
            f"http://127.0.0.1:{server.server_port}/mcp",
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            origin="http://legal.internal",
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert status == 200
    assert [tool["name"] for tool in payload["result"]["tools"]] == ["agent_query"]


# --- v0.4.5 Phase 2: trusted reverse-proxy header (HTTP wiring) -------------


def _seed_external_user(database_path: Path, external_subject: str) -> int:
    conn = db.connect(database_path)
    try:
        user = create_user(
            conn,
            email="proxied@example.com",
            display_name="Proxied User",
            role=ROLE_BUSINESS,
            external_subject=external_subject,
        )
        return int(user["id"])
    finally:
        conn.close()


@contextlib.contextmanager
def _serving(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/mcp"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _post_raw(url: str, *, headers: dict[str, str]):
    request = urllib.request.Request(
        url,
        data=b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}',
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    with _OPENER.open(request, timeout=5) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def test_http_mcp_trusted_header_resolves_from_trusted_peer(tmp_path: Path) -> None:
    # The test's TCP peer is 127.0.0.1; trusting it lets the proxy-injected header
    # authenticate with no bearer token. Proves client_address is threaded into the
    # header source.
    database_path = tmp_path / "legal.db"
    audit_path = tmp_path / "audit.jsonl"
    _database_with_project(database_path)
    _seed_external_user(database_path, "oidc|alice")
    server = build_http_server(
        host="127.0.0.1",
        port=0,
        database_path=database_path,
        audit_path=audit_path,
        bearer_token="legacy-token",
        allowed_origins=("http://legal.internal",),
        trusted_identity_header="X-Legal-MCP-User",
        trusted_proxies=("127.0.0.1",),
    )
    with _serving(server) as url:
        status, payload = _post_raw(
            url,
            headers={
                "Origin": "http://legal.internal",
                "X-Legal-MCP-User": "oidc|alice",
            },
        )

    assert status == 200
    assert "result" in payload


def test_http_mcp_trusted_header_from_untrusted_peer_is_rejected(
    tmp_path: Path,
) -> None:
    # Same header, but the configured trusted proxy is NOT the real peer
    # (127.0.0.1), so the header is a spoof attempt → 401 fail-closed.
    database_path = tmp_path / "legal.db"
    audit_path = tmp_path / "audit.jsonl"
    _database_with_project(database_path)
    _seed_external_user(database_path, "oidc|alice")
    server = build_http_server(
        host="127.0.0.1",
        port=0,
        database_path=database_path,
        audit_path=audit_path,
        bearer_token="legacy-token",
        allowed_origins=("http://legal.internal",),
        trusted_identity_header="X-Legal-MCP-User",
        trusted_proxies=("10.1.2.3",),
    )
    with _serving(server) as url:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post_raw(
                url,
                headers={
                    "Origin": "http://legal.internal",
                    "X-Legal-MCP-User": "oidc|alice",
                },
            )

    assert exc.value.code == 401
    assert _http_error_payload(exc.value) == {"error": "unauthorized"}


def test_http_mcp_bearer_and_trusted_header_conflict_returns_401(
    tmp_path: Path,
) -> None:
    # A bearer token AND a trusted identity header on one request is a conflict,
    # rejected even from a trusted peer — never silently resolved to one identity.
    database_path = tmp_path / "legal.db"
    audit_path = tmp_path / "audit.jsonl"
    _database_with_project(database_path)
    _seed_external_user(database_path, "oidc|alice")
    server = build_http_server(
        host="127.0.0.1",
        port=0,
        database_path=database_path,
        audit_path=audit_path,
        bearer_token="legacy-token",
        allowed_origins=("http://legal.internal",),
        trusted_identity_header="X-Legal-MCP-User",
        trusted_proxies=("127.0.0.1",),
    )
    with _serving(server) as url:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post_raw(
                url,
                headers={
                    "Origin": "http://legal.internal",
                    "Authorization": "Bearer legacy-token",
                    "X-Legal-MCP-User": "oidc|alice",
                },
            )

    assert exc.value.code == 401
    assert _http_error_payload(exc.value) == {"error": "conflicting_identity"}


def test_build_http_server_requires_trusted_proxy_for_header(tmp_path: Path) -> None:
    # A header source with no trusted peer would reject every request — refuse to
    # start so the misconfiguration is loud, not a silent all-deny.
    database_path = tmp_path / "legal.db"
    audit_path = tmp_path / "audit.jsonl"
    _database_with_project(database_path)
    with pytest.raises(ValueError):
        build_http_server(
            host="127.0.0.1",
            port=0,
            database_path=database_path,
            audit_path=audit_path,
            bearer_token="legacy-token",
            allowed_origins=("http://legal.internal",),
            trusted_identity_header="X-Legal-MCP-User",
            trusted_proxies=(),
        )
