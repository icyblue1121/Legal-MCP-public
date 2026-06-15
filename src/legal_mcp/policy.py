"""Project access policy helpers."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from legal_mcp.identity import ROLE_ADMIN, ROLE_BUSINESS, ROLE_LEGAL


@dataclass(frozen=True)
class AccessContext:
    user_id: int | None
    role: str
    email: str | None = None
    api_key_id: int | None = None
    legacy_shared_token: bool = False
    # Explicit "see everything" capability — the single, named way to grant full
    # access (admin / local stdio operator / opt-in legacy token). Kept explicit so
    # full disclosure is never *inferred* from a flag like ``legacy_shared_token``.
    # A ``None`` context is fail-closed (v0.4.5 Phase 1); full access is *always*
    # this flag, never a default fall-through.
    unrestricted: bool = False
    # Federated subject from a trusted upstream (``users.external_subject``). The
    # canonical owner identity for ``record_scope: by_owner`` (v0.4.5 Phase 4):
    # external SaaS tables key ownership by this, not by the local ``user_id``.
    external_subject: str | None = None
    # Which identity source minted this context, for the audit trail (v0.4.5
    # Phase 2): ``"bearer_token"`` / ``"legacy"`` / ``"trusted_header"`` /
    # ``"local"``. Lets a reviewer tell an api-key disclosure from a trusted-proxy
    # one; it is a source *label*, never a token or secret.
    identity_source: str | None = None

    @classmethod
    def from_user(
        cls,
        user: dict[str, Any],
        api_key_id: int | None = None,
        identity_source: str | None = None,
    ) -> "AccessContext":
        return cls(
            user_id=int(user["id"]),
            role=str(user["role"]),
            email=user.get("email"),
            api_key_id=api_key_id,
            external_subject=user.get("external_subject"),
            identity_source=identity_source,
        )

    @classmethod
    def legacy(cls, *, unrestricted: bool = False) -> "AccessContext":
        """The shared migration bearer token. Fail-closed by default: it grants
        nothing unless the deployment explicitly opts into full access for the
        migration window (``--legacy-token-full-access``)."""
        return cls(
            user_id=None,
            role=ROLE_LEGAL,
            legacy_shared_token=True,
            unrestricted=unrestricted,
            identity_source="legacy",
        )

    @classmethod
    def local_operator(cls) -> "AccessContext":
        """The local stdio operator: no network identity, full local access. Made
        explicit so the network path's ``context is None`` can stay fail-closed."""
        return cls(
            user_id=None,
            role=ROLE_ADMIN,
            unrestricted=True,
            identity_source="local",
        )


def can_query_content(context: AccessContext | None) -> bool:
    # Fail-closed (v0.4.5 Phase 1): a ``None`` context grants nothing. Every entry
    # point now mints an explicit context — network paths through the resolver seam,
    # stdio through ``local_operator()`` — so ``None`` only reaches here by mistake,
    # and a mistake must deny rather than disclose. Full access is the explicit
    # ``unrestricted`` capability. The legacy token is *not* None — it falls through
    # to its (grantless) role and is denied at the field/row gates.
    if context is None:
        return False
    if context.unrestricted:
        return True
    return context.role in {ROLE_ADMIN, ROLE_LEGAL, ROLE_BUSINESS}


@dataclass(frozen=True)
class FieldAuthorizationDecision:
    allowed_fields: set[str]
    denied_fields: dict[str, str]


def visible_project_ids(
    conn: sqlite3.Connection,
    context: AccessContext | None,
) -> set[int] | None:
    # ``None`` return = unrestricted (see ``project_is_visible``). Reserve it for the
    # explicit ``unrestricted`` capability only. A ``None`` *context* is fail-closed
    # (v0.4.5 Phase 1): the empty set, i.e. no project is visible.
    if context is None:
        return set()
    if context.unrestricted:
        return None

    if context.role == ROLE_ADMIN:
        rows = conn.execute("select id from projects").fetchall()
        return {int(row["id"]) for row in rows}

    if context.role in {ROLE_BUSINESS, ROLE_LEGAL} and context.user_id is not None:
        rows = conn.execute(
            "select project_id from project_access where user_id = ?",
            (context.user_id,),
        ).fetchall()
        return {int(row["project_id"]) for row in rows}

    return set()


def visible_company_ids(
    conn: sqlite3.Connection,
    context: AccessContext | None,
) -> set[int] | None:
    # Company scope mirrors project scope: ``None`` is reserved for explicit
    # unrestricted access, while a missing context fails closed to no companies.
    if context is None:
        return set()
    if context.unrestricted:
        return None

    if context.role == ROLE_ADMIN:
        rows = conn.execute("select id from companies").fetchall()
        return {int(row["id"]) for row in rows}

    if context.role in {ROLE_BUSINESS, ROLE_LEGAL} and context.user_id is not None:
        rows = conn.execute(
            "select company_id from company_access where user_id = ?",
            (context.user_id,),
        ).fetchall()
        return {int(row["company_id"]) for row in rows}

    return set()


def record_owner_subject(
    context: AccessContext | None,
    subject_attr: str,
) -> str | None:
    """The owner-identity value a ``by_owner`` domain scopes the requester's rows to,
    or ``None`` when there is no identifiable subject (v0.4.5 Phase 4).

    **Fail-closed red line.** This is *deliberately not* ``visible_project_ids`` /
    ``record_scope_project_ids``. Those return ``None = unrestricted`` for a legacy /
    ``None`` context — which for ``by_owner`` would mean *every owner's rows*. Here
    ``None`` means the **opposite**: no subject → the caller discloses **zero** rows.
    There is no "see all" result; ``by_owner`` always scopes to the requester's own
    subject. Anonymous, legacy, ``unrestricted``-but-unmapped, or a context missing
    the configured ``subject_attr`` all resolve to ``None`` → zero rows.

    ``subject_attr`` is one of :data:`~legal_mcp.connectors.base.OWNER_SUBJECT_ATTRS`
    (validated at connector-config load).
    """
    if context is None:
        return None
    value = getattr(context, subject_attr, None)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def project_is_visible(
    conn: sqlite3.Connection,
    context: AccessContext | None,
    project_id: int,
) -> bool:
    visible_ids = visible_project_ids(conn, context)
    if visible_ids is None:
        return True
    return project_id in visible_ids


def company_is_visible(
    conn: sqlite3.Connection,
    context: AccessContext | None,
    company_id: int,
) -> bool:
    visible_ids = visible_company_ids(conn, context)
    if visible_ids is None:
        return True
    return company_id in visible_ids


def user_group_ids(conn: sqlite3.Connection, context: AccessContext | None) -> set[int]:
    if context is None or context.user_id is None:
        return set()
    rows = conn.execute(
        "select group_id from user_group_memberships where user_id = ?",
        (context.user_id,),
    ).fetchall()
    return {int(row["group_id"]) for row in rows}


def grant_scope_clause(
    conn: sqlite3.Connection, context: AccessContext | None
) -> tuple[str, list[object]] | None:
    """SQL predicate + params selecting a context's *effective* grant rows.

    Effective grants are the requester's direct user grants UNION their groups'
    grants (v0.4.0 §C, C3/C4). Returns ``None`` when the context has no grant
    scope at all (no user identity and no group membership), i.e. nothing can
    match — callers treat that as default-deny.
    """
    conditions: list[str] = []
    params: list[object] = []
    if context is not None and context.user_id is not None:
        conditions.append("user_id = ?")
        params.append(context.user_id)
    group_ids = user_group_ids(conn, context)
    if group_ids:
        placeholders = ", ".join("?" for _ in group_ids)
        conditions.append(f"group_id in ({placeholders})")
        params.extend(sorted(group_ids))
    if not conditions:
        return None
    return "(" + " or ".join(conditions) + ")", params


def authorize_fields(
    conn: sqlite3.Connection,
    context: AccessContext | None,
    *,
    operation: str,
    data_domain: str,
    project_id: int | None,
    requested_fields: set[str],
) -> FieldAuthorizationDecision:
    # Fail-closed (v0.4.5 Phase 1): a ``None`` context is denied every field, the
    # same as a grantless context. Full access is the explicit ``unrestricted``
    # capability (admin / local operator / opt-in legacy), never a None default.
    if context is None:
        return FieldAuthorizationDecision(
            set(),
            {field: "field_not_granted" for field in requested_fields},
        )
    if context.unrestricted:
        return FieldAuthorizationDecision(set(requested_fields), {})
    if context.role == ROLE_ADMIN:
        return FieldAuthorizationDecision(set(requested_fields), {})

    # A grantless context — e.g. the legacy token without the opt-in — yields no
    # scope clause → default-deny every requested field.
    scope = grant_scope_clause(conn, context)
    if scope is None:
        return FieldAuthorizationDecision(
            set(),
            {field: "field_not_granted" for field in requested_fields},
        )

    scope_sql, scope_params = scope
    rows = conn.execute(
        f"""
        select field_name
        from permission_grants
        where {scope_sql}
          and operation = ?
          and data_domain = ?
          and (project_id is null or project_id = ?)
          and allowed = 1
        """,
        [*scope_params, operation, data_domain, project_id],
    ).fetchall()

    # A grant row with NULL field_name authorizes every field in the domain.
    # describe_my_access treats NULL the same way; authorize_fields must agree,
    # otherwise a domain-wide grant would deny every specific field.
    if any(row["field_name"] is None for row in rows):
        return FieldAuthorizationDecision(set(requested_fields), {})

    granted = {str(row["field_name"]) for row in rows if row["field_name"]}
    allowed = requested_fields & granted
    denied = {
        field: "field_not_granted"
        for field in requested_fields
        if field not in allowed
    }
    return FieldAuthorizationDecision(allowed, denied)
