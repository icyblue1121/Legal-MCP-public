"""Identity resolution seam: map one request to exactly one ``AccessContext``.

v0.4.5 Phase 1 promotes the gateway's identity handling from an inline branch in
``http_server`` into a small, pluggable seam. The contract is deliberately narrow
so later phases extend it by *registering a source*, not by editing the gateway:

1. per-user API key (baseline, ``identity.verify_api_key``) — implemented here as
   :class:`BearerTokenSource`, which also handles the legacy shared token;
2. HTTP header injected by a trusted reverse proxy — :class:`TrustedHeaderSource`
   (Phase 2), trusting the proxy boundary and rejecting any header from an
   untrusted peer fail-closed;
3. OIDC/OAuth — v0.5 unless the header path leaves it nearly free.

**Precedence is unambiguous by construction.** A request authenticates through
*exactly one* accepted identity source. If more than one source is present the
request is rejected (:class:`ConflictingIdentitySources`) rather than silently
choosing — the legacy shared token counts as one such source, so "bearer + trusted
header" is a conflict, not a precedence puzzle. This is the structural half of the
preflight fix: there is no path by which two credentials quietly resolve to one
identity.
"""

from __future__ import annotations

import ipaddress
import sqlite3
from dataclasses import dataclass
from typing import Callable, Protocol, Sequence

from legal_mcp.identity import verify_api_key, verify_external_subject
from legal_mcp.policy import AccessContext


class ConflictingIdentitySources(Exception):
    """A request presented more than one identity source.

    Fail-closed: the gateway rejects rather than pick one. The HTTP layer maps this
    to 401 (it is an authentication failure, not a server error).
    """


class IdentitySource(Protocol):
    """One way a request can carry an identity.

    Split into presence detection (cheap, header-only, no I/O) and resolution
    (may hit the DB). The orchestrator detects presence across all sources to
    enforce single-source precedence *before* spending a DB round-trip on the one
    surviving source.
    """

    name: str

    def is_present(self, headers: "Headers") -> bool:
        """Whether this source's credential is present on the request."""
        ...

    def resolve(
        self, headers: "Headers", conn: sqlite3.Connection
    ) -> AccessContext | None:
        """Resolve to an ``AccessContext``, or ``None`` to deny (→ 401)."""
        ...


class Headers(Protocol):
    """The subset of a request's headers the seam reads.

    ``http.client.HTTPMessage`` (what ``BaseHTTPRequestHandler.headers`` is)
    satisfies this; so does a plain ``dict`` in tests.
    """

    def get(self, name: str, default: str | None = None) -> str | None: ...


@dataclass(frozen=True)
class BearerTokenSource:
    """``Authorization: Bearer <token>`` — per-user API key or legacy shared token.

    Behavior-identical to the pre-seam ``http_server._resolve_access_context``: an
    exact match against the configured legacy token yields a (fail-closed-by-default)
    legacy context; otherwise the token is verified as a per-user API key.
    """

    bearer_token: str | None = None
    legacy_token_full_access: bool = False
    name: str = "bearer_token"

    def _token(self, headers: Headers) -> str | None:
        authorization = headers.get("Authorization")
        if not authorization:
            return None
        scheme, _, token = authorization.partition(" ")
        if scheme != "Bearer" or not token:
            return None
        return token

    def is_present(self, headers: Headers) -> bool:
        return self._token(headers) is not None

    def resolve(
        self, headers: Headers, conn: sqlite3.Connection
    ) -> AccessContext | None:
        token = self._token(headers)
        if token is None:
            return None
        if self.bearer_token and token == self.bearer_token:
            return AccessContext.legacy(unrestricted=self.legacy_token_full_access)
        verified = verify_api_key(conn, token)
        if verified is None:
            return None
        return AccessContext.from_user(
            verified.user,
            api_key_id=verified.api_key["id"],
            identity_source=self.name,
        )


@dataclass(frozen=True)
class TrustedHeaderSource:
    """An identity header injected by a trusted reverse proxy (v0.4.5 Phase 2).

    The trust is in the **proxy boundary**, not the raw header: Legal-MCP trusts
    that the proxy has already authenticated the human, and that the proxy is the
    TCP peer of this request. A header carrying an identity from any *other* peer is
    a spoofing attempt and is rejected fail-closed — never silently honored.

    The header value maps to ``users.external_subject`` first (the canonical
    federated key); an ``users.email`` fallback is allowed only when the deployment
    explicitly enables it. Unknown / disabled users fail closed.

    ``peer_address`` is the request's TCP peer (``client_address[0]``), injected
    per request by the HTTP handler — the source is reconstructed each request, so
    this carries no cross-request state.
    """

    header_name: str
    trusted_proxies: tuple[str, ...]
    peer_address: str | None
    allow_email_fallback: bool = False
    name: str = "trusted_header"

    def _subject(self, headers: Headers) -> str | None:
        value = headers.get(self.header_name)
        if value is None:
            return None
        value = value.strip()
        return value or None

    def is_present(self, headers: Headers) -> bool:
        # Presence is peer-independent *on purpose*. A header from an untrusted peer
        # still counts as present so the single-source conflict check fires
        # ("bearer + spoofed header" → reject), and a lone spoofed header is denied
        # in ``resolve`` rather than quietly dropped. Both paths fail closed; no
        # request with a spoofed identity header is served.
        return self._subject(headers) is not None

    def _peer_is_trusted(self) -> bool:
        if self.peer_address is None:
            return False
        try:
            peer = ipaddress.ip_address(self.peer_address)
        except ValueError:
            return False
        # A dual-stack listener reports IPv4 peers as IPv4-mapped IPv6
        # (``::ffff:127.0.0.1``); compare on the embedded IPv4 so a ``127.0.0.1``
        # trust entry matches.
        if getattr(peer, "ipv4_mapped", None) is not None:
            peer = peer.ipv4_mapped
        for entry in self.trusted_proxies:
            try:
                network = ipaddress.ip_network(entry, strict=False)
            except ValueError:
                continue
            if peer in network:
                return True
        return False

    def resolve(
        self, headers: Headers, conn: sqlite3.Connection
    ) -> AccessContext | None:
        # Fail-closed: an identity header from an untrusted peer is a spoof attempt,
        # not an identity. Deny before touching the user table.
        if not self._peer_is_trusted():
            return None
        subject = self._subject(headers)
        if subject is None:
            return None
        user = verify_external_subject(
            conn, subject, allow_email_fallback=self.allow_email_fallback
        )
        if user is None:
            return None
        return AccessContext.from_user(user, identity_source=self.name)


def resolve_access_context(
    headers: Headers,
    sources: Sequence[IdentitySource],
    connect: Callable[[], sqlite3.Connection],
) -> AccessContext | None:
    """Resolve the one accepted identity for a request.

    - no source present → ``None`` (unauthenticated → 401);
    - more than one present → :class:`ConflictingIdentitySources` (reject → 401);
    - exactly one present → that source's :meth:`IdentitySource.resolve`.

    ``connect`` is only invoked when a single source is present, so anonymous
    requests never touch the database (preserving the pre-seam behavior, and the
    "auth subsystem unavailable → 503" semantics for credentialed requests only).
    """
    present = [source for source in sources if source.is_present(headers)]
    if not present:
        return None
    if len(present) > 1:
        raise ConflictingIdentitySources(
            "multiple identity sources presented: "
            + ", ".join(source.name for source in present)
        )

    conn = connect()
    try:
        return present[0].resolve(headers, conn)
    finally:
        conn.close()
