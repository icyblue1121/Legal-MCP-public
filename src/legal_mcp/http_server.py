"""HTTP transport for shared Legal-MCP team deployments."""

from __future__ import annotations

import json
import sqlite3
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from legal_mcp import db
from legal_mcp.agent_config import load_agent_config
from legal_mcp.connector_config import ConnectorSetup
from legal_mcp.identity_resolver import (
    BearerTokenSource,
    ConflictingIdentitySources,
    IdentitySource,
    TrustedHeaderSource,
    resolve_access_context,
)
from legal_mcp.mcp_protocol import handle_message
from legal_mcp.policy import AccessContext
from legal_mcp.startup import require_startup_checks


class LegalMCPHTTPServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        RequestHandlerClass: type[BaseHTTPRequestHandler],
        *,
        database_path: str | Path,
        audit_path: str | Path,
        bearer_token: str | None,
        allowed_origins: tuple[str, ...],
        public_agent_only: bool | None = None,
        min_client_version: str | None = None,
        connector_setup: ConnectorSetup | None = None,
        legacy_token_full_access: bool = False,
        trusted_identity_header: str | None = None,
        trusted_proxies: tuple[str, ...] = (),
        trusted_header_email_fallback: bool = False,
    ) -> None:
        if trusted_identity_header and not trusted_proxies:
            # A header source with no trusted peer would reject every request
            # fail-closed — safe, but a silent, opaque all-deny. Refuse to start
            # so the misconfiguration is loud, matching the connector's posture.
            raise ValueError(
                "trusted_identity_header requires at least one trusted_proxies entry"
            )
        super().__init__(server_address, RequestHandlerClass)
        self.database_path = Path(database_path)
        self.audit_path = Path(audit_path)
        self.bearer_token = bearer_token
        self.allowed_origins = allowed_origins
        self.connector_setup = connector_setup
        self.legacy_token_full_access = legacy_token_full_access
        self.trusted_identity_header = trusted_identity_header
        self.trusted_proxies = trusted_proxies
        self.trusted_header_email_fallback = trusted_header_email_fallback
        config = load_agent_config(database_path)
        self.public_agent_only = (
            config.public_agent_only
            if public_agent_only is None
            else public_agent_only
        )
        self.min_client_version = (
            config.min_client_version
            if min_client_version is None
            else min_client_version
        )


class LegalMCPHTTPRequestHandler(BaseHTTPRequestHandler):
    server: LegalMCPHTTPServer

    def do_GET(self) -> None:
        if self.path == "/healthz":
            self._handle_healthz()
            return
        if self.path == "/mcp":
            self._handle_mcp_probe()
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

    def do_POST(self) -> None:
        if self.path != "/mcp":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        if not self._origin_allowed():
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "origin_not_allowed"})
            return
        if not self._client_version_allowed():
            self._send_json(
                HTTPStatus.UPGRADE_REQUIRED,
                {
                    "error": "client_update_required",
                    "minimum_client_version": self.server.min_client_version,
                },
            )
            return
        try:
            access_context = self._resolve_access_context()
        except ConflictingIdentitySources:
            self._send_json(
                HTTPStatus.UNAUTHORIZED, {"error": "conflicting_identity"}
            )
            return
        except sqlite3.Error:
            self._send_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": "auth_unavailable"},
            )
            return
        if access_context is None:
            self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(content_length)
            message = json.loads(body.decode("utf-8"))
        except (ValueError, json.JSONDecodeError):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_json"})
            return

        response = handle_message(
            message,
            database_path=self.server.database_path,
            audit_path=self.server.audit_path,
            access_context=access_context,
            public_agent_only=self.server.public_agent_only,
            connector_setup=self.server.connector_setup,
        )
        if response is None:
            self._send_json(HTTPStatus.ACCEPTED, {})
            return
        self._send_json(HTTPStatus.OK, response)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _handle_healthz(self) -> None:
        try:
            db.initialize_database(self.server.database_path)
            self._send_json(HTTPStatus.OK, {"service": "legal-mcp", "database": "ready"})
        except Exception:
            self._send_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"service": "legal-mcp", "database": "unavailable"},
            )

    def _handle_mcp_probe(self) -> None:
        self._send_json(
            HTTPStatus.OK,
            {
                "service": "legal-mcp",
                "transport": "json-rpc-over-http",
                "endpoint": "/mcp",
                "methods": ["POST"],
            },
        )

    def _identity_sources(self) -> list[IdentitySource]:
        """The identity sources this deployment accepts, in no precedence order —
        the seam enforces single-source, not first-wins. The trusted-reverse-proxy
        header source is appended only when configured, and is handed this request's
        TCP peer so it can reject a header from an untrusted peer fail-closed."""
        sources: list[IdentitySource] = [
            BearerTokenSource(
                bearer_token=self.server.bearer_token,
                legacy_token_full_access=self.server.legacy_token_full_access,
            )
        ]
        if self.server.trusted_identity_header:
            sources.append(
                TrustedHeaderSource(
                    header_name=self.server.trusted_identity_header,
                    trusted_proxies=self.server.trusted_proxies,
                    peer_address=(
                        self.client_address[0] if self.client_address else None
                    ),
                    allow_email_fallback=self.server.trusted_header_email_fallback,
                )
            )
        return sources

    def _resolve_access_context(self) -> AccessContext | None:
        return resolve_access_context(
            self.headers,
            self._identity_sources(),
            lambda: db.connect(self.server.database_path),
        )

    def _origin_allowed(self) -> bool:
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        return origin in self.server.allowed_origins

    def _client_version_allowed(self) -> bool:
        minimum = self.server.min_client_version
        if not minimum:
            return True
        current = self.headers.get("X-Legal-MCP-Client-Version")
        if not current:
            return False
        return _version_at_least(current, minimum)

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def build_http_server(
    *,
    host: str,
    port: int,
    database_path: str | Path,
    audit_path: str | Path,
    bearer_token: str | None = None,
    allowed_origins: tuple[str, ...],
    public_agent_only: bool | None = None,
    min_client_version: str | None = None,
    connector_setup: ConnectorSetup | None = None,
    legacy_token_full_access: bool = False,
    trusted_identity_header: str | None = None,
    trusted_proxies: tuple[str, ...] = (),
    trusted_header_email_fallback: bool = False,
) -> LegalMCPHTTPServer:
    return LegalMCPHTTPServer(
        (host, port),
        LegalMCPHTTPRequestHandler,
        database_path=database_path,
        audit_path=audit_path,
        bearer_token=bearer_token,
        allowed_origins=allowed_origins,
        public_agent_only=public_agent_only,
        min_client_version=min_client_version,
        connector_setup=connector_setup,
        legacy_token_full_access=legacy_token_full_access,
        trusted_identity_header=trusted_identity_header,
        trusted_proxies=trusted_proxies,
        trusted_header_email_fallback=trusted_header_email_fallback,
    )


def serve_http(
    *,
    host: str,
    port: int,
    database_path: str | Path,
    audit_path: str | Path,
    bearer_token: str | None = None,
    allowed_origins: tuple[str, ...],
    update_check_url: str | None = None,
    public_agent_only: bool | None = None,
    min_client_version: str | None = None,
    connector_setup: ConnectorSetup | None = None,
    legacy_token_full_access: bool = False,
    trusted_identity_header: str | None = None,
    trusted_proxies: tuple[str, ...] = (),
    trusted_header_email_fallback: bool = False,
) -> None:
    require_startup_checks(database_path, remote_url=update_check_url)
    server = build_http_server(
        host=host,
        port=port,
        database_path=database_path,
        audit_path=audit_path,
        bearer_token=bearer_token,
        allowed_origins=allowed_origins,
        public_agent_only=public_agent_only,
        min_client_version=min_client_version,
        connector_setup=connector_setup,
        legacy_token_full_access=legacy_token_full_access,
        trusted_identity_header=trusted_identity_header,
        trusted_proxies=trusted_proxies,
        trusted_header_email_fallback=trusted_header_email_fallback,
    )
    try:
        server.serve_forever()
    finally:
        server.server_close()


def _version_tuple(version: str) -> tuple[int, ...]:
    parts: list[int] = []
    for part in version.split("."):
        digits = "".join(char for char in part if char.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def _version_at_least(current: str, minimum: str) -> bool:
    current_parts = list(_version_tuple(current))
    minimum_parts = list(_version_tuple(minimum))
    width = max(len(current_parts), len(minimum_parts))
    current_parts.extend([0] * (width - len(current_parts)))
    minimum_parts.extend([0] * (width - len(minimum_parts)))
    return tuple(current_parts) >= tuple(minimum_parts)
