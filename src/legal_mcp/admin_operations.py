"""Transactional admin mutations for the v1.5 admin panel.

These helpers keep multi-step admin actions atomic: either every row is
written and committed together, or nothing is. They deliberately bypass the
self-committing ``identity`` helpers so the whole operation shares one
transaction and rolls back cleanly on any failure.
"""

from __future__ import annotations

import secrets
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass

from legal_mcp.identity import (
    ACTIVE,
    DISABLED,
    REVOKED,
    _API_KEY_PREFIX_LENGTH,
    hash_password,
    hash_token,
)


class ProvisioningError(Exception):
    """A provisioning input was invalid or conflicted with existing data."""


class AdminOperationError(Exception):
    """An admin mutation input was invalid or conflicted with existing data."""


@dataclass(frozen=True)
class ProvisionedUser:
    user_id: int
    email: str
    created_group_id: int | None
    membership_count: int
    project_grant_count: int
    company_grant_count: int
    api_key_plaintext: str | None
    api_key_prefix: str | None


def provision_user(
    conn: sqlite3.Connection,
    *,
    email: str,
    display_name: str,
    role: str,
    granted_by_user_id: int,
    group_ids: Sequence[int] = (),
    project_ids: Sequence[int] = (),
    company_ids: Sequence[int] = (),
    new_group_name: str | None = None,
    new_group_description: str | None = None,
    create_api_key: bool = False,
    api_key_label: str = "",
) -> ProvisionedUser:
    """Create a user plus memberships, project grants, and an optional key.

    The whole operation runs in a single transaction. On any integrity error
    (duplicate email, duplicate group, unknown group/project id) nothing is
    committed and a :class:`ProvisioningError` is raised with a user-facing
    message.
    """
    if create_api_key and not api_key_label:
        raise ProvisioningError("An API key label is required to create a key.")

    try:
        cursor = conn.execute(
            """
            insert into users (email, display_name, role, status)
            values (?, ?, ?, ?)
            """,
            (email, display_name, role, ACTIVE),
        )
        user_id = int(cursor.lastrowid)

        created_group_id: int | None = None
        all_group_ids = list(group_ids)
        if new_group_name:
            group_cursor = conn.execute(
                "insert into user_groups (name, description) values (?, ?)",
                (new_group_name, new_group_description or None),
            )
            created_group_id = int(group_cursor.lastrowid)
            all_group_ids.append(created_group_id)

        for group_id in all_group_ids:
            conn.execute(
                """
                insert into user_group_memberships (user_id, group_id)
                values (?, ?)
                """,
                (user_id, group_id),
            )

        for project_id in project_ids:
            conn.execute(
                """
                insert into project_access
                  (user_id, project_id, granted_by_user_id)
                values (?, ?, ?)
                """,
                (user_id, project_id, granted_by_user_id),
            )

        for company_id in company_ids:
            conn.execute(
                """
                insert into company_access
                  (user_id, company_id, granted_by_user_id)
                values (?, ?, ?)
                """,
                (user_id, company_id, granted_by_user_id),
            )

        api_key_plaintext: str | None = None
        api_key_prefix: str | None = None
        if create_api_key:
            api_key_plaintext = "lmcp_" + secrets.token_urlsafe(32)
            api_key_prefix = api_key_plaintext[:_API_KEY_PREFIX_LENGTH]
            conn.execute(
                """
                insert into api_keys (user_id, key_prefix, key_hash, label, status)
                values (?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    api_key_prefix,
                    hash_token(api_key_plaintext),
                    api_key_label,
                    ACTIVE,
                ),
            )

        conn.commit()
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        raise ProvisioningError(_describe_integrity_error(exc)) from exc
    except Exception:
        conn.rollback()
        raise

    return ProvisionedUser(
        user_id=user_id,
        email=email,
        created_group_id=created_group_id,
        membership_count=len(all_group_ids),
        project_grant_count=len(project_ids),
        company_grant_count=len(company_ids),
        api_key_plaintext=api_key_plaintext,
        api_key_prefix=api_key_prefix,
    )


def _describe_integrity_error(exc: sqlite3.IntegrityError) -> str:
    message = str(exc)
    if "users.email" in message:
        return "A user with that email already exists."
    if "user_groups.name" in message:
        return "A group with that name already exists."
    if "FOREIGN KEY" in message:
        return "A selected group, project, or company no longer exists."
    return "Could not provision the user with the given selections."


# --- Existing-user maintenance ---------------------------------------------


def update_user(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    display_name: str | None = None,
    role: str | None = None,
) -> None:
    """Update a user's display name and/or role."""
    sets: list[str] = []
    params: list[object] = []
    if display_name is not None:
        sets.append("display_name = ?")
        params.append(display_name)
    if role is not None:
        sets.append("role = ?")
        params.append(role)
    if not sets:
        return
    sets.append("updated_at = datetime('now')")
    params.append(user_id)
    try:
        cursor = conn.execute(
            f"update users set {', '.join(sets)} where id = ?", params
        )
        conn.commit()
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        raise AdminOperationError(_describe_integrity_error(exc)) from exc
    if cursor.rowcount == 0:
        raise AdminOperationError("That user no longer exists.")


def set_user_status(
    conn: sqlite3.Connection, *, user_id: int, status: str
) -> None:
    """Enable (active) or disable a user. Disabled users cannot authenticate."""
    if status not in (ACTIVE, DISABLED):
        raise AdminOperationError("Invalid user status.")
    cursor = conn.execute(
        "update users set status = ?, updated_at = datetime('now') where id = ?",
        (status, user_id),
    )
    conn.commit()
    if cursor.rowcount == 0:
        raise AdminOperationError("That user no longer exists.")


def set_user_password(
    conn: sqlite3.Connection, *, user_id: int, password: str
) -> None:
    """Set or reset a user's password.

    Note: only ``admin`` users authenticate with a password (admin login).
    For other roles the password is stored but not yet used for auth.
    """
    if not password:
        raise AdminOperationError("Password cannot be empty.")
    cursor = conn.execute(
        "update users set password_hash = ?, updated_at = datetime('now') where id = ?",
        (hash_password(password), user_id),
    )
    conn.commit()
    if cursor.rowcount == 0:
        raise AdminOperationError("That user no longer exists.")


def set_user_groups(
    conn: sqlite3.Connection, *, user_id: int, group_ids: Sequence[int]
) -> None:
    """Replace a user's group memberships with the given set (differential sync)."""
    desired = {int(g) for g in group_ids}
    try:
        current = {
            int(row["group_id"])
            for row in conn.execute(
                "select group_id from user_group_memberships where user_id = ?",
                (user_id,),
            ).fetchall()
        }
        for group_id in desired - current:
            conn.execute(
                "insert into user_group_memberships (user_id, group_id) values (?, ?)",
                (user_id, group_id),
            )
        for group_id in current - desired:
            conn.execute(
                "delete from user_group_memberships where user_id = ? and group_id = ?",
                (user_id, group_id),
            )
        conn.commit()
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        raise AdminOperationError(_describe_integrity_error(exc)) from exc


def set_user_projects(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    project_ids: Sequence[int],
    granted_by_user_id: int,
) -> None:
    """Replace a user's project access grants with the given set."""
    desired = {int(p) for p in project_ids}
    try:
        current = {
            int(row["project_id"])
            for row in conn.execute(
                "select project_id from project_access where user_id = ?",
                (user_id,),
            ).fetchall()
        }
        for project_id in desired - current:
            conn.execute(
                """
                insert into project_access (user_id, project_id, granted_by_user_id)
                values (?, ?, ?)
                """,
                (user_id, project_id, granted_by_user_id),
            )
        for project_id in current - desired:
            conn.execute(
                "delete from project_access where user_id = ? and project_id = ?",
                (user_id, project_id),
            )
        conn.commit()
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        raise AdminOperationError(_describe_integrity_error(exc)) from exc


def set_user_companies(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    company_ids: Sequence[int],
    granted_by_user_id: int,
) -> None:
    """Replace a user's company access grants with the given set."""
    desired = {int(c) for c in company_ids}
    try:
        current = {
            int(row["company_id"])
            for row in conn.execute(
                "select company_id from company_access where user_id = ?",
                (user_id,),
            ).fetchall()
        }
        for company_id in desired - current:
            conn.execute(
                """
                insert into company_access (user_id, company_id, granted_by_user_id)
                values (?, ?, ?)
                """,
                (user_id, company_id, granted_by_user_id),
            )
        for company_id in current - desired:
            conn.execute(
                "delete from company_access where user_id = ? and company_id = ?",
                (user_id, company_id),
            )
        conn.commit()
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        raise AdminOperationError(_describe_integrity_error(exc)) from exc


def revoke_api_key(conn: sqlite3.Connection, *, key_id: int) -> None:
    """Revoke an API key. Revoked keys no longer authenticate."""
    cursor = conn.execute(
        """
        update api_keys
        set status = ?, revoked_at = datetime('now')
        where id = ? and status = ?
        """,
        (REVOKED, key_id, ACTIVE),
    )
    conn.commit()
    if cursor.rowcount == 0:
        raise AdminOperationError("That key does not exist or is already revoked.")


def relabel_api_key(
    conn: sqlite3.Connection, *, key_id: int, label: str
) -> None:
    """Change an API key's label."""
    if not label:
        raise AdminOperationError("Label is required.")
    cursor = conn.execute(
        "update api_keys set label = ? where id = ?", (label, key_id)
    )
    conn.commit()
    if cursor.rowcount == 0:
        raise AdminOperationError("That key no longer exists.")
