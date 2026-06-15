"""Local user identity and API key helpers."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

ROLE_ADMIN = "admin"
ROLE_LEGAL = "legal"
ROLE_BUSINESS = "business"
ROLE_AUDITOR = "auditor"

ACTIVE = "active"
DISABLED = "disabled"
REVOKED = "revoked"

_PASSWORD_ALGORITHM = "pbkdf2_sha256"
_PASSWORD_ITERATIONS = 200_000
_PASSWORD_SALT_BYTES = 16
_API_KEY_PREFIX_LENGTH = 12


@dataclass(frozen=True)
class CreatedAPIKey:
    api_key_id: int
    plaintext: str
    prefix: str


@dataclass(frozen=True)
class VerifiedAPIKey:
    user: dict[str, Any]
    api_key: dict[str, Any]


def hash_password(password: str) -> str:
    """Hash a password using PBKDF2-SHA256 with a random salt."""
    salt = secrets.token_bytes(_PASSWORD_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        _PASSWORD_ITERATIONS,
    )
    salt_b64 = base64.b64encode(salt).decode("ascii")
    digest_b64 = base64.b64encode(digest).decode("ascii")
    return f"{_PASSWORD_ALGORITHM}${_PASSWORD_ITERATIONS}${salt_b64}${digest_b64}"


def verify_password(password: str, password_hash: str | None) -> bool:
    """Return whether a password matches a stored PBKDF2-SHA256 hash."""
    if not password_hash:
        return False

    try:
        algorithm, iterations_text, salt_b64, digest_b64 = password_hash.split("$")
        iterations = int(iterations_text)
        salt = base64.b64decode(salt_b64.encode("ascii"), validate=True)
        expected_digest = base64.b64decode(digest_b64.encode("ascii"), validate=True)
    except (AttributeError, binascii.Error, ValueError, TypeError):
        return False

    if algorithm != _PASSWORD_ALGORITHM or iterations != _PASSWORD_ITERATIONS:
        return False

    try:
        actual_digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            iterations,
        )
    except OverflowError:
        return False
    return hmac.compare_digest(actual_digest, expected_digest)


def hash_token(token: str) -> str:
    """Hash an API token for storage."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_user(
    conn: sqlite3.Connection,
    *,
    email: str,
    display_name: str,
    role: str,
    password_hash: str | None = None,
    external_subject: str | None = None,
) -> dict[str, Any]:
    """Create an active local user and return the stored user row."""
    cursor = conn.execute(
        """
        insert into users (email, display_name, role, status, password_hash, external_subject)
        values (?, ?, ?, ?, ?, ?)
        """,
        (email, display_name, role, ACTIVE, password_hash, external_subject),
    )
    conn.commit()
    return get_user(conn, int(cursor.lastrowid))


def get_user(conn: sqlite3.Connection, user_id: int) -> dict[str, Any]:
    """Fetch a user by ID."""
    row = conn.execute("select * from users where id = ?", (user_id,)).fetchone()
    if row is None:
        raise LookupError(f"user not found: {user_id}")
    return dict(row)


def create_api_key(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    label: str,
) -> CreatedAPIKey:
    """Create an API key and return the plaintext token once."""
    plaintext = "lmcp_" + secrets.token_urlsafe(32)
    prefix = plaintext[:_API_KEY_PREFIX_LENGTH]
    cursor = conn.execute(
        """
        insert into api_keys (user_id, key_prefix, key_hash, label, status)
        values (?, ?, ?, ?, ?)
        """,
        (user_id, prefix, hash_token(plaintext), label, ACTIVE),
    )
    conn.commit()
    return CreatedAPIKey(
        api_key_id=int(cursor.lastrowid),
        plaintext=plaintext,
        prefix=prefix,
    )


def verify_external_subject(
    conn: sqlite3.Connection,
    subject: str,
    *,
    allow_email_fallback: bool = False,
) -> dict[str, Any] | None:
    """Resolve a trusted-upstream subject to an active user row, or ``None``.

    Used by the v0.4.5 Phase 2 trusted-header identity source. The subject maps to
    ``users.external_subject`` first; it falls back to ``users.email`` only when the
    deployment explicitly enables it (external subjects are the canonical federated
    key — email is a pilot convenience). Every non-active user (disabled / revoked)
    and every unknown subject fails closed, exactly like ``verify_api_key``.
    """
    row = conn.execute(
        "select * from users where external_subject = ? and status = ?",
        (subject, ACTIVE),
    ).fetchone()
    if row is None and allow_email_fallback:
        row = conn.execute(
            "select * from users where email = ? and status = ?",
            (subject, ACTIVE),
        ).fetchone()
    return dict(row) if row is not None else None


def verify_api_key(
    conn: sqlite3.Connection,
    plaintext: str,
) -> VerifiedAPIKey | None:
    """Verify an API key and return the associated active user and key rows."""
    prefix = plaintext[:_API_KEY_PREFIX_LENGTH]
    token_hash = hash_token(plaintext)
    rows = conn.execute(
        """
        select
          api_keys.id as api_key_id,
          api_keys.user_id as api_key_user_id,
          api_keys.key_prefix,
          api_keys.key_hash,
          api_keys.label,
          api_keys.status as api_key_status,
          api_keys.last_used_at,
          api_keys.created_at as api_key_created_at,
          api_keys.revoked_at,
          users.id as user_id,
          users.email,
          users.display_name,
          users.role,
          users.status as user_status,
          users.password_hash,
          users.external_subject,
          users.created_at as user_created_at,
          users.updated_at as user_updated_at
        from api_keys
        join users on users.id = api_keys.user_id
        where api_keys.key_prefix = ?
        """,
        (prefix,),
    ).fetchall()

    for row in rows:
        if not hmac.compare_digest(row["key_hash"], token_hash):
            continue
        if row["api_key_status"] != ACTIVE or row["user_status"] != ACTIVE:
            return None

        last_used_at = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "update api_keys set last_used_at = ? where id = ?",
            (last_used_at, row["api_key_id"]),
        )
        conn.commit()

        user = {
            "id": row["user_id"],
            "email": row["email"],
            "display_name": row["display_name"],
            "role": row["role"],
            "status": row["user_status"],
            "external_subject": row["external_subject"],
            "created_at": row["user_created_at"],
            "updated_at": row["user_updated_at"],
        }
        api_key = {
            "id": row["api_key_id"],
            "user_id": row["api_key_user_id"],
            "key_prefix": row["key_prefix"],
            "label": row["label"],
            "status": row["api_key_status"],
            "last_used_at": last_used_at,
            "created_at": row["api_key_created_at"],
            "revoked_at": row["revoked_at"],
        }
        return VerifiedAPIKey(user=user, api_key=api_key)

    return None
