"""Passwordless embedded Langfuse for the admin backend.

Langfuse always requires a session (no "disable auth" switch) and ships
``frame-ancestors 'none'`` + ``connect-src 'self'``. So embedding it without a
login page means a *same-origin* reverse proxy that (1) injects a server-held
NextAuth session cookie on the way upstream and (2) strips the frame-blocking
response headers. The proxy runs as a second loopback listener that mirrors
Langfuse at its root path; the Audit page embeds it in an iframe.

Access is gated by the admin session: the proxy listener shares the admin host,
and browser cookies are host- (not port-) scoped, so the admin session cookie
reaches it and is validated the same way as the main admin server.

See ``Docs/superpowers/spikes/2026-06-05-langfuse-passwordless-embed.md``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any
import http.cookiejar
import json
import os
import re
import threading
import urllib.error
import urllib.parse
import urllib.request

from legal_mcp import db
from legal_mcp.admin_common import _SESSION_COOKIE, MODE_LOCAL, _parse_session_expires_at
from legal_mcp.identity import ACTIVE, ROLE_ADMIN, hash_token

_DEFAULT_OBSERVABILITY_PORT = 8767
_LANGFUSE_SESSION_COOKIE = "next-auth.session-token"
# Hop-by-hop and connection-control headers we must not forward verbatim.
_DROP_REQUEST_HEADERS = {"host", "cookie", "connection", "accept-encoding", "content-length"}
_DROP_RESPONSE_HEADERS = {
    "content-encoding",
    "content-length",
    "transfer-encoding",
    "connection",
    "set-cookie",
    "x-frame-options",
}


class ObservabilityConfig:
    """Resolved Langfuse embed configuration, or ``enabled == False``."""

    def __init__(self) -> None:
        self.base_url = (os.environ.get("LANGFUSE_BASE_URL") or "").rstrip("/")
        self.email = os.environ.get("LANGFUSE_INIT_USER_EMAIL") or ""
        self.password = os.environ.get("LANGFUSE_INIT_USER_PASSWORD") or ""
        self.project_id = os.environ.get("LANGFUSE_INIT_PROJECT_ID") or ""
        try:
            self.port = int(
                os.environ.get("LEGAL_MCP_OBSERVABILITY_PORT")
                or _DEFAULT_OBSERVABILITY_PORT
            )
        except ValueError:
            self.port = _DEFAULT_OBSERVABILITY_PORT

    @property
    def enabled(self) -> bool:
        """Embed is possible only with a base URL and bootstrap credentials."""
        return bool(self.base_url and self.email and self.password)


def observability_config() -> ObservabilityConfig:
    return ObservabilityConfig()


class _LangfuseSession:
    """Holds and refreshes a Langfuse NextAuth session cookie."""

    def __init__(self, config: ObservabilityConfig) -> None:
        self._config = config
        self._token: str | None = None
        self._lock = threading.Lock()

    def cookie_value(self, *, force_refresh: bool = False) -> str | None:
        with self._lock:
            if self._token is None or force_refresh:
                self._token = _login_langfuse(self._config)
            return self._token


def _login_langfuse(config: ObservabilityConfig) -> str | None:
    """Run the NextAuth credentials flow and return the session-token value."""
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    try:
        with opener.open(f"{config.base_url}/api/auth/csrf", timeout=10) as resp:
            csrf = json.loads(resp.read().decode())["csrfToken"]
        body = urllib.parse.urlencode(
            {
                "csrfToken": csrf,
                "email": config.email,
                "password": config.password,
                "json": "true",
            }
        ).encode()
        req = urllib.request.Request(
            f"{config.base_url}/api/auth/callback/credentials",
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with opener.open(req, timeout=10) as resp:
            resp.read()
    except (urllib.error.URLError, OSError, ValueError, KeyError):
        return None
    for cookie in jar:
        # Over HTTPS the cookie is prefixed (``__Secure-next-auth.session-token``).
        if cookie.name.endswith(_LANGFUSE_SESSION_COOKIE):
            return cookie.value
    return None


def _strip_frame_ancestors(csp: str) -> str:
    """Remove the ``frame-ancestors`` directive so the page can be iframed."""
    directives = [
        d.strip()
        for d in csp.split(";")
        if d.strip() and not d.strip().lower().startswith("frame-ancestors")
    ]
    return "; ".join(directives)


def _authorized(database_path: str | Path, mode: str, cookie_header: str | None) -> bool:
    """Mirror the admin server's session check (local mode is passwordless)."""
    if mode == MODE_LOCAL:
        return True
    if not cookie_header:
        return False
    cookie = SimpleCookie()
    try:
        cookie.load(cookie_header)
    except Exception:
        return False
    morsel = cookie.get(_SESSION_COOKIE)
    if morsel is None or not morsel.value:
        return False
    conn = db.connect(database_path)
    try:
        row = conn.execute(
            """
            select users.role, users.status, admin_sessions.expires_at
            from admin_sessions
            join users on users.id = admin_sessions.user_id
            where admin_sessions.session_hash = ?
            """,
            (hash_token(morsel.value),),
        ).fetchone()
    finally:
        conn.close()
    if row is None or row["role"] != ROLE_ADMIN or row["status"] != ACTIVE:
        return False
    expires_at = _parse_session_expires_at(row["expires_at"])
    return expires_at is not None and expires_at > datetime.now(timezone.utc)


class _ObservabilityProxyServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        handler: type[BaseHTTPRequestHandler],
        *,
        config: ObservabilityConfig,
        database_path: str | Path,
        mode: str,
    ) -> None:
        super().__init__(server_address, handler)
        self.config = config
        self.database_path = database_path
        self.mode = mode
        self.session = _LangfuseSession(config)


class _ObservabilityProxyHandler(BaseHTTPRequestHandler):
    server: _ObservabilityProxyServer

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        self._proxy("GET")

    def do_POST(self) -> None:
        self._proxy("POST")

    def do_PUT(self) -> None:
        self._proxy("PUT")

    def do_DELETE(self) -> None:
        self._proxy("DELETE")

    def do_PATCH(self) -> None:
        self._proxy("PATCH")

    def _proxy(self, method: str) -> None:
        server = self.server
        if not _authorized(
            server.database_path, server.mode, self.headers.get("Cookie")
        ):
            self.send_error(403, "Admin authentication required")
            return

        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None

        # First attempt with the cached session; on auth bounce, refresh once.
        status = self._forward(method, body, force_refresh=False)
        if status in (401, 302):
            self._forward(method, body, force_refresh=True, already_sent=False)

    def _forward(
        self,
        method: str,
        body: bytes | None,
        *,
        force_refresh: bool,
        already_sent: bool = False,
    ) -> int:
        server = self.server
        token = server.session.cookie_value(force_refresh=force_refresh)
        if token is None:
            self.send_error(502, "Langfuse session unavailable")
            return 502

        upstream_url = server.config.base_url + self.path
        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in _DROP_REQUEST_HEADERS
        }
        headers["Cookie"] = f"{_LANGFUSE_SESSION_COOKIE}={token}"
        headers["Accept-Encoding"] = "identity"

        req = urllib.request.Request(
            upstream_url, data=body, headers=headers, method=method
        )
        try:
            resp = urllib.request.urlopen(req, timeout=30)
            status = resp.status
            resp_headers = resp.getheaders()
            payload = resp.read()
        except urllib.error.HTTPError as exc:
            status = exc.code
            resp_headers = list(exc.headers.items())
            payload = exc.read()
        except (urllib.error.URLError, OSError):
            self.send_error(502, "Langfuse upstream unreachable")
            return 502

        # A sign-in redirect means the session lapsed; let the caller retry.
        if status in (302, 401) and not force_refresh:
            location = next(
                (v for k, v in resp_headers if k.lower() == "location"), ""
            )
            if status == 401 or "/auth/sign-in" in location or "/api/auth" in location:
                return status

        self.send_response(status)
        for key, value in resp_headers:
            lower = key.lower()
            if lower in _DROP_RESPONSE_HEADERS:
                continue
            if lower == "content-security-policy":
                value = _strip_frame_ancestors(value)
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if method != "HEAD":
            self.wfile.write(payload)
        return status


def start_observability_proxy(
    *,
    host: str,
    database_path: str | Path,
    mode: str,
    config: ObservabilityConfig | None = None,
) -> _ObservabilityProxyServer | None:
    """Start the embed proxy in a background thread; ``None`` if not configured."""
    config = config or observability_config()
    if not config.enabled:
        return None
    server = _ObservabilityProxyServer(
        (host, config.port),
        _ObservabilityProxyHandler,
        config=config,
        database_path=database_path,
        mode=mode,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server
