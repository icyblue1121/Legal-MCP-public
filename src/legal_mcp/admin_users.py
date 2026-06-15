"""Users home and guided one-panel provisioning views."""
from __future__ import annotations

from http import HTTPStatus
from typing import Any
import html
import sqlite3

from legal_mcp import db
from legal_mcp.admin_common import _ALLOWED_ROLES
from legal_mcp.admin_operations import ProvisioningError, provision_user
from legal_mcp.identity import ROLE_ADMIN, ROLE_AUDITOR, ROLE_BUSINESS, ROLE_LEGAL, create_api_key


class UsersViewMixin:
    """Users home and guided one-panel provisioning views."""

    def _send_users_page(self, message: str | None = None) -> None:
        body = """
        <h1>Users</h1>
        <p class="subtitle">Create a new user, or manage existing users, groups, permissions, and API keys.</p>
        <div class="actionbar">
          <a class="btn" href="/admin/users/new">New user</a>
          <a class="btn btn-secondary" href="/admin/users/manage">Manage users</a>
        </div>
        """
        flash = html.escape(message) if message is not None else None
        self._send_html(
            HTTPStatus.OK,
            self._admin_layout("Admin Users", "users", body, flash),
        )

    def _send_new_user_page(
        self,
        message: str | None = None,
        status: HTTPStatus = HTTPStatus.OK,
        values: dict[str, str] | None = None,
    ) -> None:
        values = values or {}
        conn = db.connect(self.server.database_path)
        try:
            project_rows = conn.execute(
                """
                select id, project_code, name, stage, department,
                       legal_bp, release_team
                from projects
                order by project_code
                """
            ).fetchall()
            group_rows = conn.execute(
                "select id, name, description from user_groups order by name"
            ).fetchall()
            company_rows = conn.execute(
                "select id, name, unified_social_credit_code from companies order by name"
            ).fetchall()
            perm_rows = conn.execute(
                """
                select group_id, operation, data_domain, field_name
                from permission_grants
                order by group_id
                """
            ).fetchall()
        finally:
            conn.close()

        perms_by_group: dict[int, list[str]] = {}
        for row in perm_rows:
            label = f"{row['operation']}:{row['data_domain']}"
            if row["field_name"]:
                label += f".{row['field_name']}"
            perms_by_group.setdefault(row["group_id"], []).append(label)

        role_options = "\n".join(
            f'<option value="{html.escape(role)}"'
            f'{" selected" if values.get("role") == role else ""}>'
            f"{html.escape(role)}</option>"
            for role in (ROLE_BUSINESS, ROLE_LEGAL, ROLE_AUDITOR, ROLE_ADMIN)
        )
        email_val = html.escape(values.get("email", ""))
        name_val = html.escape(values.get("display_name", ""))

        if group_rows:
            group_items = "\n".join(
                self._checkrow(
                    name="group_ids",
                    value=str(row["id"]),
                    title=row["name"],
                    sub=row["description"] or "No description",
                    perms=perms_by_group.get(row["id"], []),
                )
                for row in group_rows
            )
            group_block = f'<div class="checklist">{group_items}</div>'
        else:
            group_block = '<p class="empty">No groups yet. Create one below.</p>'

        if project_rows:
            project_items = "\n".join(
                self._checkrow(
                    name="project_ids",
                    value=str(row["id"]),
                    title=f"{row['project_code']} — {row['name']}",
                    sub=" · ".join(
                        part
                        for part in (
                            row["stage"],
                            row["department"],
                            f"Legal BP: {row['legal_bp']}" if row["legal_bp"] else "",
                            f"Team: {row['release_team']}" if row["release_team"] else "",
                        )
                        if part
                    ),
                    perms=[],
                )
                for row in project_rows
            )
            project_block = f'<div class="checklist">{project_items}</div>'
        else:
            project_block = '<p class="empty">No projects imported yet.</p>'

        if company_rows:
            company_items = "\n".join(
                self._checkrow(
                    name="company_ids",
                    value=str(row["id"]),
                    title=row["name"],
                    sub=row["unified_social_credit_code"] or "No credit code",
                    perms=[],
                )
                for row in company_rows
            )
            company_block = f'<div class="checklist">{company_items}</div>'
        else:
            company_block = '<p class="empty">No companies imported yet.</p>'

        body = f"""
        <a class="back" href="/admin/users">&larr; Users</a>
        <h1>New User</h1>
        <p class="subtitle">Create a user and configure access in one step. The whole form is saved as a single transaction.</p>
        <form class="provision" method="post" action="/admin/users/provision">
          <section class="panel">
            <h2>Identity</h2>
            <div class="grid">
              <label>Email <input type="email" name="email" value="{email_val}" required></label>
              <label>Display name <input type="text" name="display_name" value="{name_val}" required></label>
              <label>Role <select name="role" required>{role_options}</select></label>
            </div>
          </section>
          <section class="panel">
            <h2>API Key</h2>
            <p class="hint">Optional. The key is shown only once, immediately after creation.</p>
            <div class="grid">
              <label class="check"><input type="checkbox" name="create_api_key" value="1"> Create an API key now</label>
              <label>Key label <input type="text" name="api_key_label" placeholder="e.g. laptop-cli"></label>
            </div>
          </section>
          <section class="panel">
            <h2>Groups</h2>
            <p class="hint">Groups act as permission templates — members inherit the group's field permissions.</p>
            {group_block}
            <div class="grid" style="margin-top:16px">
              <label>New group name <input type="text" name="new_group_name" placeholder="optional"></label>
              <label>New group description <input type="text" name="new_group_description" placeholder="optional"></label>
            </div>
          </section>
          <section class="panel">
            <h2>Project Access</h2>
            <p class="hint">Grant visibility to specific projects.</p>
            {project_block}
          </section>
          <section class="panel">
            <h2>Company Access</h2>
            <p class="hint">Grant visibility to specific companies and their seals/licenses.</p>
            {company_block}
          </section>
          <div class="actionbar">
            <button type="submit">Create User</button>
            <a class="btn btn-secondary" href="/admin/users">Cancel</a>
          </div>
        </form>
        """
        self._send_html(
            status,
            self._admin_layout(
                "New User",
                "users",
                body,
                message,
                message_kind="error" if message else "info",
            ),
        )

    def _handle_provision_user(self, admin: sqlite3.Row) -> None:
        fields = self._read_form_multi()

        def single(key: str) -> str:
            values = fields.get(key, [])
            return values[0].strip() if values else ""

        email = single("email")
        display_name = single("display_name")
        role = single("role")
        submitted = {"email": email, "display_name": display_name, "role": role}
        if not email:
            self._send_new_user_page(
                "Email is required.", HTTPStatus.BAD_REQUEST, submitted
            )
            return
        if not display_name:
            self._send_new_user_page(
                "Display name is required.", HTTPStatus.BAD_REQUEST, submitted
            )
            return
        if role not in _ALLOWED_ROLES:
            self._send_new_user_page(
                "A valid role is required.", HTTPStatus.BAD_REQUEST, submitted
            )
            return

        create_key = bool(fields.get("create_api_key"))
        api_key_label = single("api_key_label")
        if create_key and not api_key_label:
            self._send_new_user_page(
                "An API key label is required to create a key.",
                HTTPStatus.BAD_REQUEST,
                submitted,
            )
            return

        try:
            group_ids = self._parse_id_list(fields.get("group_ids", []))
            project_ids = self._parse_id_list(fields.get("project_ids", []))
            company_ids = self._parse_id_list(fields.get("company_ids", []))
        except ValueError:
            self._send_new_user_page(
                "Invalid selection.", HTTPStatus.BAD_REQUEST, submitted
            )
            return

        new_group_name = single("new_group_name") or None
        new_group_description = single("new_group_description") or None

        conn = db.connect(self.server.database_path)
        try:
            result = provision_user(
                conn,
                email=email,
                display_name=display_name,
                role=role,
                granted_by_user_id=admin["id"],
                group_ids=group_ids,
                project_ids=project_ids,
                company_ids=company_ids,
                new_group_name=new_group_name,
                new_group_description=new_group_description,
                create_api_key=create_key,
                api_key_label=api_key_label,
            )
        except ProvisioningError as exc:
            self._send_new_user_page(str(exc), HTTPStatus.CONFLICT, submitted)
            return
        finally:
            conn.close()

        self._send_provision_success(result)

    def _send_provision_success(self, result: Any) -> None:
        key_block = ""
        if result.api_key_plaintext is not None:
            key_block = f"""
            <h2>API Key</h2>
            <div class="keypanel">
              <p class="label">Prefix {html.escape(result.api_key_prefix)}</p>
              <code>{html.escape(result.api_key_plaintext)}</code>
              <p class="warn">Copy this now — it will not be shown again.</p>
            </div>
            """
        body = f"""
        <a class="back" href="/admin/users">&larr; Users</a>
        <h1>User Created</h1>
        <p class="subtitle">{html.escape(result.email)} was provisioned successfully.</p>
        <h2>Summary</h2>
        <ul class="summary-list">
          <li><span class="k">User ID</span><span>{html.escape(str(result.user_id))}</span></li>
          <li><span class="k">Email</span><span>{html.escape(result.email)}</span></li>
          <li><span class="k">Group memberships</span><span>{result.membership_count}</span></li>
          <li><span class="k">Project grants</span><span>{result.project_grant_count}</span></li>
          <li><span class="k">Company grants</span><span>{result.company_grant_count}</span></li>
        </ul>
        {key_block}
        <div class="actionbar">
          <a class="btn" href="/admin/users/new">Create another</a>
          <a class="btn btn-secondary" href="/admin/users">Back to users</a>
        </div>
        """
        self._send_html(
            HTTPStatus.OK,
            self._admin_layout("User Created", "users", body),
        )
