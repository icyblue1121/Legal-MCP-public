"""Minimal admin web server for local Legal-MCP administration.

Routing, deployment-mode boundary, authentication, and the concrete
request handler. Page rendering is composed from the admin_* mixins.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any
import html
import secrets
import sqlite3
import urllib.parse

from legal_mcp import db
from legal_mcp.admin_common import _LOCAL_OWNER_EMAIL, _SESSION_COOKIE, _SESSION_HOURS, MODE_LOCAL, MODE_TEAM, _is_loopback_host, _parse_session_expires_at, read_deployment_mode, write_deployment_mode
from legal_mcp.identity import ACTIVE, ROLE_ADMIN, create_user, hash_password, hash_token, verify_password

from legal_mcp.admin_render import RenderMixin
from legal_mcp.admin_users import UsersViewMixin
from legal_mcp.admin_manage import ManageViewMixin
from legal_mcp.admin_database import DatabaseViewMixin
from legal_mcp.admin_data_sources import DataSourcesWizardMixin
from legal_mcp.admin_misc import MiscViewMixin

if TYPE_CHECKING:
    from legal_mcp.connector_config import ConnectorSetup


class LegalMCPAdminServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        RequestHandlerClass: type[BaseHTTPRequestHandler],
        *,
        database_path: str | Path,
        mode: str = MODE_TEAM,
        connector_setup: "ConnectorSetup | None" = None,
    ) -> None:
        super().__init__(server_address, RequestHandlerClass)
        self.database_path = Path(database_path)
        self.mode = mode
        self.bind_host = server_address[0]
        # The data sources the gateway serves. Used by the Data Sources view and
        # the catalog-driven permissions form to show real domains/fields. May be
        # None only in legacy direct construction; build_admin_server always sets it.
        self.connector_setup = connector_setup

    @property
    def is_local_mode(self) -> bool:
        return self.mode == MODE_LOCAL


class LegalMCPAdminRequestHandler(
    RenderMixin,
    UsersViewMixin,
    ManageViewMixin,
    DatabaseViewMixin,
    DataSourcesWizardMixin,
    MiscViewMixin,
    BaseHTTPRequestHandler,
):
    server: LegalMCPAdminServer

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        if path in ("/", "/login"):
            if self.server.is_local_mode:
                # Local single-user deployment: land on the Data Sources screen
                # so the operator sees what the gateway serves first.
                self._redirect("/admin/database")
                return
            self._send_login_page()
            return

        if not path.startswith("/admin/"):
            self._send_html(
                HTTPStatus.NOT_FOUND, self._page("Not Found", "<p>Not found</p>")
            )
            return

        admin = self._current_admin()
        if admin is None:
            self._redirect("/login")
            return

        segments = [s for s in path.split("/") if s]  # e.g. admin users 5 edit
        if path == "/admin/users":
            self._send_users_page()
            return
        if path == "/admin/users/new":
            self._send_new_user_page()
            return
        if path == "/admin/users/manage":
            self._send_manage_page(query)
            return
        if (
            len(segments) == 4
            and segments[:2] == ["admin", "users"]
            and segments[3] == "edit"
        ):
            try:
                user_id = int(segments[2])
            except ValueError:
                self._send_html(
                    HTTPStatus.NOT_FOUND, self._page("Not Found", "<p>Not found</p>")
                )
                return
            self._send_user_edit_page(user_id, query)
            return
        if path == "/admin/database":
            self._send_database_page(query)
            return
        if path == "/admin/data-sources/new":
            self._send_data_source_new_page()
            return
        if path == "/admin/audit":
            self._send_audit_page(query)
            return
        if len(segments) == 3 and segments[:2] == ["admin", "audit"]:
            try:
                event_id = int(segments[2])
            except ValueError:
                self._send_html(
                    HTTPStatus.NOT_FOUND,
                    self._page("Not Found", "<p>Not found</p>"),
                )
                return
            self._send_audit_detail_page(event_id)
            return
        if path == "/admin/observability":
            self._send_observability_page()
            return
        if path == "/admin/deployment-mode":
            self._send_deployment_mode_page()
            return
        if path == "/admin/agent-settings":
            self._send_agent_settings_page()
            return
        self._send_html(HTTPStatus.NOT_FOUND, self._page("Not Found", "<p>Not found</p>"))

    def do_POST(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path == "/login":
            self._handle_login()
            return

        if not path.startswith("/admin/"):
            self._send_html(
                HTTPStatus.NOT_FOUND,
                self._page("Not Found", "<p>Not found</p>"),
            )
            return

        admin = self._current_admin()
        if admin is None:
            self._redirect("/login")
            return

        segments = [s for s in path.split("/") if s]

        if path == "/admin/users/provision":
            self._handle_provision_user(admin)
            return
        if path == "/admin/grants/create":
            self._handle_create_grant(admin)
            return
        if path == "/admin/keys/create":
            self._handle_create_key()
            return
        if path == "/admin/groups/create":
            self._handle_create_group()
            return
        if path == "/admin/group-memberships/create":
            self._handle_create_group_membership()
            return
        if path == "/admin/permissions/create":
            self._handle_create_permission()
            return
        if path == "/admin/agent-settings/update":
            self._handle_update_agent_settings()
            return
        if path == "/admin/deployment-mode":
            self._handle_deployment_mode(admin)
            return
        # Existing-record maintenance (Phase 3).
        if (
            len(segments) >= 4
            and segments[:2] == ["admin", "users"]
            and segments[3] in ("edit", "status", "password")
        ):
            try:
                user_id = int(segments[2])
            except ValueError:
                self._send_form_error(HTTPStatus.NOT_FOUND, "Unknown user.")
                return
            if segments[3] == "edit":
                self._handle_update_user(admin, user_id)
            elif segments[3] == "status":
                self._handle_user_status(user_id)
            else:
                self._handle_user_password(user_id)
            return
        if (
            len(segments) == 4
            and segments[:2] == ["admin", "keys"]
            and segments[3] in ("revoke", "relabel")
        ):
            try:
                key_id = int(segments[2])
            except ValueError:
                self._send_form_error(HTTPStatus.NOT_FOUND, "Unknown key.")
                return
            if segments[3] == "revoke":
                self._handle_revoke_key(key_id)
            else:
                self._handle_relabel_key(key_id)
            return
        if path == "/admin/groups/delete":
            self._handle_delete_group()
            return
        if path == "/admin/group-memberships/delete":
            self._handle_delete_membership()
            return
        if path == "/admin/permissions/delete":
            self._handle_delete_permission()
            return
        if path == "/admin/data-sources/disconnect":
            self._handle_data_source_toggle(disable=True)
            return
        if path == "/admin/data-sources/connect":
            self._handle_data_source_toggle(disable=False)
            return
        if path == "/admin/data-sources/introspect":
            self._handle_data_source_introspect()
            return
        if path == "/admin/data-sources/register":
            self._handle_data_source_register()
            return
        if path == "/admin/data-sources/status":
            self._handle_data_source_set_status()
            return
        if path == "/admin/data-sources/delete":
            self._handle_data_source_delete()
            return

        self._send_html(
            HTTPStatus.NOT_FOUND,
            self._page("Not Found", "<p>Not found</p>"),
        )

    def _handle_login(self) -> None:
        fields = self._read_form_fields()
        email = fields.get("email", "")
        password = fields.get("password", "")
        conn = db.connect(self.server.database_path)
        try:
            user = conn.execute(
                """
                select * from users
                where email = ? and role = ? and status = ?
                """,
                (email, ROLE_ADMIN, ACTIVE),
            ).fetchone()
            if user is None or not verify_password(password, user["password_hash"]):
                self._send_login_page(
                    HTTPStatus.UNAUTHORIZED,
                    "Invalid admin email or password.",
                )
                return

            token = secrets.token_urlsafe(32)
            expires_at = (
                datetime.now(timezone.utc) + timedelta(hours=_SESSION_HOURS)
            ).isoformat()
            conn.execute(
                """
                insert into admin_sessions (user_id, session_hash, expires_at)
                values (?, ?, ?)
                """,
                (user["id"], hash_token(token), expires_at),
            )
            conn.commit()
        finally:
            conn.close()

        self._redirect(
            "/admin/users",
            headers=[
                (
                    "Set-Cookie",
                    f"{_SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={_SESSION_HOURS * 3600}",
                )
            ],
        )

    def _current_admin(self) -> sqlite3.Row | None:
        if self.server.is_local_mode:
            return self._local_owner()

        cookie_header = self.headers.get("Cookie")
        if not cookie_header:
            return None
        cookie = SimpleCookie()
        cookie.load(cookie_header)
        morsel = cookie.get(_SESSION_COOKIE)
        if morsel is None or not morsel.value:
            return None

        session_hash = hash_token(morsel.value)
        conn = db.connect(self.server.database_path)
        try:
            row = conn.execute(
                """
                select
                  users.id,
                  users.email,
                  users.display_name,
                  users.role,
                  users.status,
                  admin_sessions.expires_at
                from admin_sessions
                join users on users.id = admin_sessions.user_id
                where admin_sessions.session_hash = ?
                """,
                (session_hash,),
            ).fetchone()
        finally:
            conn.close()
        if row is None or row["role"] != ROLE_ADMIN or row["status"] != ACTIVE:
            return None
        expires_at = _parse_session_expires_at(row["expires_at"])
        if expires_at is None:
            return None
        if expires_at <= datetime.now(timezone.utc):
            return None
        return row

    def _local_owner(self) -> sqlite3.Row | None:
        conn = db.connect(self.server.database_path)
        try:
            return conn.execute(
                """
                select id, email, display_name, role, status
                from users
                where role = ? and status = ?
                order by id
                limit 1
                """,
                (ROLE_ADMIN, ACTIVE),
            ).fetchone()
        finally:
            conn.close()

    def _send_login_page(
        self,
        status: HTTPStatus = HTTPStatus.OK,
        message: str | None = None,
    ) -> None:
        message_html = ""
        if message is not None:
            message_html = (
                f'<div class="flash flash-error">{html.escape(message)}</div>'
            )
        body = f"""
        <div class="login-wrap">
          <div class="login-card">
            <p class="brand">Legal-MCP</p>
            <h1>Admin Login</h1>
            {message_html}
            <form method="post" action="/login">
              <label>Email <input type="email" name="email" required></label>
              <label>Password <input type="password" name="password" required></label>
              <button type="submit">Log in</button>
            </form>
          </div>
        </div>
        """
        self._send_html(status, self._page("Admin Login", body))

    def _send_deployment_mode_page(
        self,
        message: str | None = None,
        message_kind: str = "info",
    ) -> None:
        current = self.server.mode
        if current == MODE_LOCAL:
            # Switching to team adds password protection — guard against a
            # lockout by requiring a password that is set and verified here.
            form = """
            <h2>Switch to Team Deployment</h2>
            <p class="subtitle">Team mode requires the admin password on every
            login. To avoid locking yourself out, set and confirm an admin
            password now — you will be logged in and switched in one step.</p>
            <form method="post" action="/admin/deployment-mode">
              <input type="hidden" name="target" value="team">
              <label>Admin email <input type="email" name="email" required></label>
              <label>New password <input type="password" name="password" required></label>
              <label>Confirm password <input type="password" name="password_confirm" required></label>
              <button type="submit">Set password & switch to Team</button>
            </form>
            """
        else:
            loopback = _is_loopback_host(self.server.bind_host)
            if loopback:
                form = """
                <h2>Switch to Local Deployment</h2>
                <p class="subtitle">Local mode removes the login requirement and is
                only appropriate for single-user use on this machine. Anyone who
                can reach this server will have full admin access.</p>
                <form method="post" action="/admin/deployment-mode">
                  <input type="hidden" name="target" value="local">
                  <label class="inline"><input type="checkbox" name="confirm" value="yes" required>
                    I understand this disables the admin login.</label>
                  <button type="submit">Switch to Local</button>
                </form>
                """
            else:
                form = (
                    '<h2>Switch to Local Deployment</h2>'
                    '<p class="subtitle">Local mode is only allowed on a loopback '
                    f'host; this server is bound to {html.escape(self.server.bind_host)}. '
                    "Restart with a loopback --host to use local mode.</p>"
                )
        mode_label = "Local Deployment" if current == MODE_LOCAL else "Team Deployment"
        body = f"""
        <h1>Deployment Mode</h1>
        <p class="subtitle">Current mode: <strong>{html.escape(mode_label)}</strong>.</p>
        {form}
        """
        self._send_html(
            HTTPStatus.OK,
            self._admin_layout(
                "Deployment Mode",
                "",
                body,
                message=message,
                message_kind=message_kind,
            ),
        )

    def _handle_deployment_mode(self, admin: sqlite3.Row) -> None:
        fields = self._read_form_fields()
        target = fields.get("target", "").strip()
        if target not in (MODE_LOCAL, MODE_TEAM):
            self._send_form_error(HTTPStatus.BAD_REQUEST, "Invalid target mode.")
            return
        if target == self.server.mode:
            self._redirect("/admin/deployment-mode")
            return

        if target == MODE_TEAM:
            self._switch_to_team(fields)
        else:
            self._switch_to_local()

    def _switch_to_team(self, fields: dict[str, str]) -> None:
        email = fields.get("email", "").strip()
        password = fields.get("password", "")
        confirm = fields.get("password_confirm", "")
        if not password or password != confirm:
            self._send_deployment_mode_page(
                "Passwords are required and must match.", message_kind="error"
            )
            return

        conn = db.connect(self.server.database_path)
        try:
            user = conn.execute(
                "select * from users where email = ? and role = ? and status = ?",
                (email, ROLE_ADMIN, ACTIVE),
            ).fetchone()
            if user is None:
                self._send_deployment_mode_page(
                    "No active admin user with that email.", message_kind="error"
                )
                return
            # Set the password, then verify it — only switch if login would work.
            password_hash = hash_password(password)
            conn.execute(
                "update users set password_hash = ? where id = ?",
                (password_hash, user["id"]),
            )
            conn.commit()
            if not verify_password(password, password_hash):
                self._send_deployment_mode_page(
                    "Password verification failed; mode unchanged.",
                    message_kind="error",
                )
                return
            # Establish an authenticated session so the admin stays logged in.
            token = secrets.token_urlsafe(32)
            expires_at = (
                datetime.now(timezone.utc) + timedelta(hours=_SESSION_HOURS)
            ).isoformat()
            conn.execute(
                "insert into admin_sessions (user_id, session_hash, expires_at)"
                " values (?, ?, ?)",
                (user["id"], hash_token(token), expires_at),
            )
            write_deployment_mode(conn, MODE_TEAM)
            conn.commit()
        finally:
            conn.close()
        self.server.mode = MODE_TEAM
        self._redirect(
            "/admin/users",
            headers=[
                (
                    "Set-Cookie",
                    f"{_SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={_SESSION_HOURS * 3600}",
                )
            ],
        )

    def _switch_to_local(self) -> None:
        if not _is_loopback_host(self.server.bind_host):
            self._send_deployment_mode_page(
                "Local mode is only allowed on a loopback host.",
                message_kind="error",
            )
            return
        _ensure_local_owner(self.server.database_path)
        conn = db.connect(self.server.database_path)
        try:
            write_deployment_mode(conn, MODE_LOCAL)
        finally:
            conn.close()
        self.server.mode = MODE_LOCAL
        self._redirect("/admin/database")

    def log_message(self, format: str, *args: Any) -> None:
        return


def build_admin_server(
    *,
    host: str,
    port: int,
    database_path: str | Path,
    mode: str = MODE_TEAM,
    connector_setup: "ConnectorSetup | None" = None,
) -> LegalMCPAdminServer:
    if mode not in (MODE_LOCAL, MODE_TEAM):
        raise ValueError(f"Unknown deployment mode: {mode!r}")
    db.initialize_database(database_path)
    if connector_setup is None:
        # No --connector config: the gateway serves every domain from the bundled
        # SQLite demo, so show exactly that in the Data Sources view.
        from legal_mcp.connector_config import build_connector_setup

        connector_setup = build_connector_setup({}, database_path=database_path)
    # Persisted mode is authoritative; --mode only seeds it on first startup.
    conn = db.connect(database_path)
    try:
        persisted = read_deployment_mode(conn)
        if persisted is None:
            write_deployment_mode(conn, mode)
            effective_mode = mode
        else:
            effective_mode = persisted
    finally:
        conn.close()
    if effective_mode == MODE_LOCAL and not _is_loopback_host(host):
        raise ValueError(
            "Local mode skips the admin password and is only allowed on a "
            f"loopback host (127.0.0.1, localhost, ::1); got --host {host!r}. "
            "Use --mode team for a network-accessible admin server."
        )
    if effective_mode == MODE_LOCAL:
        _ensure_local_owner(database_path)
    return LegalMCPAdminServer(
        (host, port),
        LegalMCPAdminRequestHandler,
        database_path=database_path,
        mode=effective_mode,
        connector_setup=connector_setup,
    )


def _ensure_local_owner(database_path: str | Path) -> None:
    """Guarantee an admin identity exists to act as in passwordless local mode."""
    conn = db.connect(database_path)
    try:
        existing = conn.execute(
            "select id from users where role = ? and status = ? order by id limit 1",
            (ROLE_ADMIN, ACTIVE),
        ).fetchone()
        if existing is None:
            create_user(
                conn,
                email=_LOCAL_OWNER_EMAIL,
                display_name="Local Owner",
                role=ROLE_ADMIN,
            )
    finally:
        conn.close()
