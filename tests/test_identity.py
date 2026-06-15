from __future__ import annotations

import sqlite3

import pytest

from legal_mcp import db
from legal_mcp.identity import (
    ACTIVE,
    DISABLED,
    REVOKED,
    ROLE_ADMIN,
    ROLE_BUSINESS,
    ROLE_LEGAL,
    create_api_key,
    create_user,
    get_user,
    hash_password,
    verify_api_key,
    verify_password,
)


@pytest.fixture()
def conn(tmp_path) -> sqlite3.Connection:
    db_path = tmp_path / "legal.db"
    db.initialize_database(db_path)
    connection = db.connect(db_path)
    try:
        yield connection
    finally:
        connection.close()


def test_hash_password_verify_password_create_user_and_get_user(
    conn: sqlite3.Connection,
) -> None:
    password_hash = hash_password("correct horse battery staple")

    assert password_hash.startswith("pbkdf2_sha256$200000$")
    assert verify_password("correct horse battery staple", password_hash) is True
    assert verify_password("wrong password", password_hash) is False
    assert verify_password("anything", None) is False
    assert verify_password("anything", "pbkdf2_sha1$1$salt$digest") is False
    assert verify_password("anything", "not-a-valid-hash") is False
    assert (
        verify_password(
            "anything",
            "pbkdf2_sha256$999999999999999999999$YWJj$YWJj",
        )
        is False
    )

    created = create_user(
        conn,
        email="admin@example.com",
        display_name="Admin User",
        role=ROLE_ADMIN,
        password_hash=password_hash,
    )

    assert created["email"] == "admin@example.com"
    assert created["display_name"] == "Admin User"
    assert created["role"] == ROLE_ADMIN
    assert created["status"] == ACTIVE
    assert created["password_hash"] == password_hash
    assert verify_password("correct horse battery staple", created["password_hash"]) is True
    assert get_user(conn, created["id"]) == created

    with pytest.raises(LookupError):
        get_user(conn, created["id"] + 1)


def test_create_api_key_returns_plaintext_once_and_verifies_user_key_and_role(
    conn: sqlite3.Connection,
) -> None:
    password_hash = hash_password("legal-password")
    user = create_user(
        conn,
        email="legal@example.com",
        display_name="Legal User",
        role=ROLE_LEGAL,
        password_hash=password_hash,
    )

    created_key = create_api_key(conn, user_id=user["id"], label="local dev")

    assert created_key.plaintext.startswith("lmcp_")
    assert created_key.prefix == created_key.plaintext[:12]

    stored_key = conn.execute(
        "select * from api_keys where id = ?", (created_key.api_key_id,)
    ).fetchone()
    assert stored_key is not None
    assert stored_key["key_prefix"] == created_key.prefix
    assert stored_key["key_hash"] != created_key.plaintext
    assert stored_key["status"] == ACTIVE
    assert stored_key["last_used_at"] is None

    verified = verify_api_key(conn, created_key.plaintext)

    assert verified is not None
    assert verified.user["id"] == user["id"]
    assert verified.user["role"] == ROLE_LEGAL
    assert "password_hash" not in verified.user
    assert verified.api_key["id"] == created_key.api_key_id
    assert verified.api_key["key_prefix"] == created_key.prefix
    assert verified.api_key["last_used_at"] is not None
    assert "key_hash" not in verified.api_key
    assert verify_api_key(conn, "lmcp_not-the-real-secret") is None


def test_revoked_api_key_and_disabled_user_credentials_do_not_verify(
    conn: sqlite3.Connection,
) -> None:
    business_password_hash = hash_password("business-password")
    business_user = create_user(
        conn,
        email="business@example.com",
        display_name="Business User",
        role=ROLE_BUSINESS,
        password_hash=business_password_hash,
    )
    revoked_key = create_api_key(conn, user_id=business_user["id"], label="revoked")
    conn.execute(
        "update api_keys set status = ? where id = ?",
        (REVOKED, revoked_key.api_key_id),
    )
    conn.commit()

    assert verify_api_key(conn, revoked_key.plaintext) is None

    disabled_password_hash = hash_password("disabled-password")
    disabled_user = create_user(
        conn,
        email="disabled@example.com",
        display_name="Disabled User",
        role=ROLE_BUSINESS,
        password_hash=disabled_password_hash,
    )
    disabled_key = create_api_key(conn, user_id=disabled_user["id"], label="disabled")
    conn.execute(
        "update users set status = ? where id = ?",
        (DISABLED, disabled_user["id"]),
    )
    conn.commit()

    assert verify_api_key(conn, disabled_key.plaintext) is None
