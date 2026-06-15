"""Unit tests for the v0.4.5 Phase 1 identity resolution seam.

The live HTTP path (test_http_server) already exercises ``BearerTokenSource``
end-to-end through one registered source. These tests pin the *seam contract* that
Phase 2 depends on — single-source precedence, conflict rejection, no DB hit until
a source is present, and the ``external_subject`` threading that ``by_owner``
(Phase 4) will consume.
"""

from __future__ import annotations

import sqlite3

import pytest

from legal_mcp import db
from legal_mcp.identity import ROLE_BUSINESS, create_api_key, create_user
from legal_mcp.identity_resolver import (
    BearerTokenSource,
    ConflictingIdentitySources,
    TrustedHeaderSource,
    resolve_access_context,
)
from legal_mcp.policy import AccessContext

_TRUSTED_PROXY = "10.0.0.5"
_UNTRUSTED_PEER = "203.0.113.9"
_HEADER = "X-Legal-MCP-User"


def _header_source(peer_address, *, email_fallback=False):
    return TrustedHeaderSource(
        header_name=_HEADER,
        trusted_proxies=(_TRUSTED_PROXY,),
        peer_address=peer_address,
        allow_email_fallback=email_fallback,
    )


@pytest.fixture()
def db_path(tmp_path):
    path = tmp_path / "legal.db"
    db.initialize_database(path)
    return path


def _connect_spy(db_path):
    """A ``connect`` callable that records whether it was invoked, so a test can
    assert the orchestrator never touches the DB for anonymous/conflicting requests."""
    calls = {"n": 0}

    def connect() -> sqlite3.Connection:
        calls["n"] += 1
        return db.connect(db_path)

    return connect, calls


class _StubSource:
    """A second identity source for exercising the conflict rule. It never needs
    the DB — presence alone is enough to trigger the precedence check."""

    name = "stub_header"

    def __init__(self, present: bool) -> None:
        self._present = present

    def is_present(self, headers) -> bool:
        return self._present

    def resolve(self, headers, conn) -> AccessContext | None:  # pragma: no cover
        raise AssertionError("conflict must be detected before resolve")


def _seed_user(db_path, *, email="business@example.com", external_subject=None):
    conn = db.connect(db_path)
    try:
        user = create_user(
            conn,
            email=email,
            display_name="Business User",
            role=ROLE_BUSINESS,
            external_subject=external_subject,
        )
        created = create_api_key(conn, user_id=user["id"], label="cli")
    finally:
        conn.close()
    return user, created


def test_bearer_source_resolves_active_api_key_to_user_context(db_path) -> None:
    user, created = _seed_user(db_path)
    connect, calls = _connect_spy(db_path)

    context = resolve_access_context(
        {"Authorization": f"Bearer {created.plaintext}"},
        [BearerTokenSource(bearer_token="legacy-token")],
        connect,
    )

    assert context is not None
    assert context.user_id == user["id"]
    assert context.role == ROLE_BUSINESS
    assert context.api_key_id == created.api_key_id
    assert context.unrestricted is False
    assert context.identity_source == "bearer_token"
    assert calls["n"] == 1


def test_bearer_source_threads_external_subject(db_path) -> None:
    # by_owner (Phase 4) keys row ownership on the federated subject, so the seam
    # must carry users.external_subject onto the AccessContext.
    _seed_user(db_path, email="first@example.com", external_subject="oidc|abc-123")
    _, created = _seed_user(
        db_path, email="second@example.com", external_subject="oidc|second"
    )
    connect, _ = _connect_spy(db_path)

    context = resolve_access_context(
        {"Authorization": f"Bearer {created.plaintext}"},
        [BearerTokenSource(bearer_token="legacy-token")],
        connect,
    )

    assert context is not None
    assert context.external_subject == "oidc|second"


def test_revoked_api_key_is_denied(db_path) -> None:
    _, created = _seed_user(db_path)
    conn = db.connect(db_path)
    try:
        conn.execute(
            "update api_keys set status = 'revoked' where id = ?",
            (created.api_key_id,),
        )
        conn.commit()
    finally:
        conn.close()
    connect, _ = _connect_spy(db_path)

    context = resolve_access_context(
        {"Authorization": f"Bearer {created.plaintext}"},
        [BearerTokenSource(bearer_token="legacy-token")],
        connect,
    )

    assert context is None


def test_disabled_user_is_denied(db_path) -> None:
    user, created = _seed_user(db_path)
    conn = db.connect(db_path)
    try:
        conn.execute(
            "update users set status = 'disabled' where id = ?",
            (user["id"],),
        )
        conn.commit()
    finally:
        conn.close()
    connect, _ = _connect_spy(db_path)

    context = resolve_access_context(
        {"Authorization": f"Bearer {created.plaintext}"},
        [BearerTokenSource(bearer_token="legacy-token")],
        connect,
    )

    assert context is None


def test_legacy_token_is_fail_closed_by_default(db_path) -> None:
    connect, _ = _connect_spy(db_path)

    context = resolve_access_context(
        {"Authorization": "Bearer legacy-token"},
        [BearerTokenSource(bearer_token="legacy-token")],
        connect,
    )

    assert context is not None
    assert context.legacy_shared_token is True
    assert context.unrestricted is False
    assert context.identity_source == "legacy"


def test_legacy_token_full_access_only_behind_opt_in(db_path) -> None:
    connect, _ = _connect_spy(db_path)

    context = resolve_access_context(
        {"Authorization": "Bearer legacy-token"},
        [BearerTokenSource(bearer_token="legacy-token", legacy_token_full_access=True)],
        connect,
    )

    assert context is not None
    assert context.unrestricted is True


def test_no_identity_source_present_returns_none_without_db_hit(db_path) -> None:
    connect, calls = _connect_spy(db_path)

    context = resolve_access_context(
        {},
        [BearerTokenSource(bearer_token="legacy-token")],
        connect,
    )

    assert context is None
    # Anonymous requests must not touch the auth DB (preserves the 401-not-503
    # behavior for credential-less requests).
    assert calls["n"] == 0


def test_conflicting_identity_sources_are_rejected_before_db_hit(db_path) -> None:
    # The structural half of the preflight fix: two credentials never silently
    # resolve to one identity. A bearer token *and* another present source is a
    # conflict — and it is detected before any resolution, so the DB is untouched.
    connect, calls = _connect_spy(db_path)

    with pytest.raises(ConflictingIdentitySources):
        resolve_access_context(
            {"Authorization": "Bearer legacy-token"},
            [
                BearerTokenSource(bearer_token="legacy-token"),
                _StubSource(present=True),
            ],
            connect,
        )

    assert calls["n"] == 0


def test_single_present_source_resolves_even_when_another_is_absent(db_path) -> None:
    # A registered-but-absent source does not count toward the conflict tally.
    _, created = _seed_user(db_path)
    connect, _ = _connect_spy(db_path)

    context = resolve_access_context(
        {"Authorization": f"Bearer {created.plaintext}"},
        [
            BearerTokenSource(bearer_token="legacy-token"),
            _StubSource(present=False),
        ],
        connect,
    )

    assert context is not None
    assert context.user_id is not None


# --- v0.4.5 Phase 2: trusted reverse-proxy header source -------------------


def test_trusted_header_from_trusted_peer_resolves_to_user(db_path) -> None:
    user, _ = _seed_user(db_path, external_subject="oidc|alice")
    connect, calls = _connect_spy(db_path)

    context = resolve_access_context(
        {_HEADER: "oidc|alice"},
        [_header_source(_TRUSTED_PROXY)],
        connect,
    )

    assert context is not None
    assert context.user_id == user["id"]
    assert context.external_subject == "oidc|alice"
    assert context.api_key_id is None
    assert context.identity_source == "trusted_header"
    assert calls["n"] == 1


def test_trusted_header_from_untrusted_peer_is_rejected(db_path) -> None:
    # A spoofed identity header arriving from a non-proxy peer must be denied
    # fail-closed — never honored, even though the subject names a real user.
    _seed_user(db_path, external_subject="oidc|alice")
    connect, _ = _connect_spy(db_path)

    context = resolve_access_context(
        {_HEADER: "oidc|alice"},
        [_header_source(_UNTRUSTED_PEER)],
        connect,
    )

    assert context is None


def test_trusted_header_unknown_subject_fails_closed(db_path) -> None:
    _seed_user(db_path, external_subject="oidc|alice")
    connect, _ = _connect_spy(db_path)

    context = resolve_access_context(
        {_HEADER: "oidc|nobody"},
        [_header_source(_TRUSTED_PROXY)],
        connect,
    )

    assert context is None


def test_trusted_header_disabled_user_fails_closed(db_path) -> None:
    user, _ = _seed_user(db_path, external_subject="oidc|alice")
    conn = db.connect(db_path)
    try:
        conn.execute(
            "update users set status = 'disabled' where id = ?", (user["id"],)
        )
        conn.commit()
    finally:
        conn.close()
    connect, _ = _connect_spy(db_path)

    context = resolve_access_context(
        {_HEADER: "oidc|alice"},
        [_header_source(_TRUSTED_PROXY)],
        connect,
    )

    assert context is None


def test_trusted_header_email_fallback_is_off_by_default(db_path) -> None:
    # The header value is an email of a real user, but no external_subject matches
    # and the fallback is off → denied.
    _seed_user(db_path, email="alice@example.com", external_subject=None)
    connect, _ = _connect_spy(db_path)

    context = resolve_access_context(
        {_HEADER: "alice@example.com"},
        [_header_source(_TRUSTED_PROXY)],
        connect,
    )

    assert context is None


def test_trusted_header_email_fallback_resolves_when_enabled(db_path) -> None:
    user, _ = _seed_user(db_path, email="alice@example.com", external_subject=None)
    connect, _ = _connect_spy(db_path)

    context = resolve_access_context(
        {_HEADER: "alice@example.com"},
        [_header_source(_TRUSTED_PROXY, email_fallback=True)],
        connect,
    )

    assert context is not None
    assert context.user_id == user["id"]
    assert context.identity_source == "trusted_header"


def test_trusted_header_cidr_trust_entry_matches_peer(db_path) -> None:
    user, _ = _seed_user(db_path, external_subject="oidc|alice")
    connect, _ = _connect_spy(db_path)

    context = resolve_access_context(
        {_HEADER: "oidc|alice"},
        [
            TrustedHeaderSource(
                header_name=_HEADER,
                trusted_proxies=("10.0.0.0/24",),
                peer_address="10.0.0.42",
            )
        ],
        connect,
    )

    assert context is not None
    assert context.user_id == user["id"]


def test_bearer_and_trusted_header_together_are_rejected(db_path) -> None:
    # Two identity sources present → conflict, even from a *trusted* peer. A valid
    # API key plus a trusted identity header must not silently resolve to one.
    _, created = _seed_user(db_path, external_subject="oidc|alice")
    connect, calls = _connect_spy(db_path)

    with pytest.raises(ConflictingIdentitySources):
        resolve_access_context(
            {
                "Authorization": f"Bearer {created.plaintext}",
                _HEADER: "oidc|alice",
            },
            [
                BearerTokenSource(bearer_token="legacy-token"),
                _header_source(_TRUSTED_PROXY),
            ],
            connect,
        )

    assert calls["n"] == 0


def test_legacy_token_and_trusted_header_together_are_rejected(db_path) -> None:
    # The legacy shared token *is* a bearer source — "legacy token + trusted
    # header" must trip the same conflict, not only "api-key + header".
    _seed_user(db_path, external_subject="oidc|alice")
    connect, calls = _connect_spy(db_path)

    with pytest.raises(ConflictingIdentitySources):
        resolve_access_context(
            {
                "Authorization": "Bearer legacy-token",
                _HEADER: "oidc|alice",
            },
            [
                BearerTokenSource(bearer_token="legacy-token"),
                _header_source(_TRUSTED_PROXY),
            ],
            connect,
        )

    assert calls["n"] == 0


def test_spoofed_header_with_bearer_from_untrusted_peer_still_conflicts(db_path) -> None:
    # Even from an untrusted peer, a present header counts toward the conflict
    # tally, so "bearer + spoofed header" is rejected rather than silently served
    # as the bearer identity.
    _, created = _seed_user(db_path, external_subject="oidc|alice")
    connect, calls = _connect_spy(db_path)

    with pytest.raises(ConflictingIdentitySources):
        resolve_access_context(
            {
                "Authorization": f"Bearer {created.plaintext}",
                _HEADER: "oidc|alice",
            },
            [
                BearerTokenSource(bearer_token="legacy-token"),
                _header_source(_UNTRUSTED_PEER),
            ],
            connect,
        )

    assert calls["n"] == 0
