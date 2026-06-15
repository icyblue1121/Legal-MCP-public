"""Unit tests for the Langfuse embed proxy helpers (no live Langfuse needed)."""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from legal_mcp import db
from legal_mcp.admin_common import _SESSION_COOKIE
from legal_mcp.admin_observability import (
    _authorized,
    _strip_frame_ancestors,
    observability_config,
)
from legal_mcp.identity import ROLE_ADMIN, ROLE_BUSINESS, create_user, hash_token

_LANGFUSE_ENV = {
    "LANGFUSE_BASE_URL": "http://127.0.0.1:3000",
    "LANGFUSE_INIT_USER_EMAIL": "admin@example.com",
    "LANGFUSE_INIT_USER_PASSWORD": "secret",
}


def _set_env(monkeypatch, **overrides) -> None:
    for key in (
        "LANGFUSE_BASE_URL",
        "LANGFUSE_INIT_USER_EMAIL",
        "LANGFUSE_INIT_USER_PASSWORD",
        "LEGAL_MCP_OBSERVABILITY_PORT",
    ):
        monkeypatch.delenv(key, raising=False)
    for key, value in overrides.items():
        monkeypatch.setenv(key, value)


def test_strip_frame_ancestors_removes_only_that_directive() -> None:
    csp = "default-src 'self'; frame-ancestors 'none'; connect-src 'self'"
    out = _strip_frame_ancestors(csp)
    assert "frame-ancestors" not in out
    assert "default-src 'self'" in out
    assert "connect-src 'self'" in out


def test_config_enabled_requires_url_and_credentials(monkeypatch) -> None:
    _set_env(monkeypatch)
    assert observability_config().enabled is False

    _set_env(monkeypatch, LANGFUSE_BASE_URL="http://x:3000")
    assert observability_config().enabled is False  # missing credentials

    _set_env(monkeypatch, **_LANGFUSE_ENV)
    assert observability_config().enabled is True


def test_config_port_defaults_and_overrides(monkeypatch) -> None:
    _set_env(monkeypatch, **_LANGFUSE_ENV)
    assert observability_config().port == 8767
    _set_env(monkeypatch, **_LANGFUSE_ENV, LEGAL_MCP_OBSERVABILITY_PORT="9000")
    assert observability_config().port == 9000
    _set_env(monkeypatch, **_LANGFUSE_ENV, LEGAL_MCP_OBSERVABILITY_PORT="not-a-number")
    assert observability_config().port == 8767  # falls back on bad value


def test_authorized_local_mode_bypasses_session(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    assert _authorized(database_path, "local", cookie_header=None) is True


def _make_admin_session(database_path: Path, *, expires: datetime) -> str:
    conn = db.connect(database_path)
    try:
        user = create_user(
            conn, email="a@b.c", display_name="A", role=ROLE_ADMIN
        )
        token = secrets.token_urlsafe(32)
        conn.execute(
            "insert into admin_sessions (user_id, session_hash, expires_at)"
            " values (?, ?, ?)",
            (user["id"], hash_token(token), expires.isoformat()),
        )
        conn.commit()
        return token
    finally:
        conn.close()


def test_authorized_team_mode_session_gate(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    token = _make_admin_session(
        database_path, expires=datetime.now(timezone.utc) + timedelta(hours=1)
    )

    assert _authorized(database_path, "team", cookie_header=None) is False
    assert _authorized(database_path, "team", f"{_SESSION_COOKIE}=bogus") is False
    assert _authorized(database_path, "team", f"{_SESSION_COOKIE}={token}") is True


def test_authorized_team_mode_rejects_expired_session(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    token = _make_admin_session(
        database_path, expires=datetime.now(timezone.utc) - timedelta(minutes=1)
    )
    assert _authorized(database_path, "team", f"{_SESSION_COOKIE}={token}") is False


def test_authorized_team_mode_rejects_non_admin(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        user = create_user(
            conn, email="biz@example.com", display_name="Biz", role=ROLE_BUSINESS
        )
        token = secrets.token_urlsafe(32)
        conn.execute(
            "insert into admin_sessions (user_id, session_hash, expires_at)"
            " values (?, ?, ?)",
            (
                user["id"],
                hash_token(token),
                (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    assert _authorized(database_path, "team", f"{_SESSION_COOKIE}={token}") is False
