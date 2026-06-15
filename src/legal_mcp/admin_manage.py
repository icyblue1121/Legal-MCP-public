"""Manage-users page: tabs, edit, and record maintenance."""
from __future__ import annotations

from http import HTTPStatus
import html
import sqlite3
import urllib.parse

from legal_mcp import db
from legal_mcp.admin_common import _ALLOWED_ROLES, _MANAGE_TABS, _PAGE_SIZE
from legal_mcp.admin_operations import AdminOperationError, relabel_api_key, revoke_api_key, set_user_companies, set_user_groups, set_user_password, set_user_projects, set_user_status, update_user
from legal_mcp.identity import ACTIVE, DISABLED, ROLE_ADMIN, ROLE_AUDITOR, ROLE_BUSINESS, ROLE_LEGAL, create_api_key


# Pseudo-domains for operational grants (manage_users / view_audit etc.) that are
# not connector data domains. Data domains come from the live connector catalog.
_OPERATIONAL_DOMAINS = ("admin", "audit")


class ManageViewMixin:
    """Manage-users page: tabs, edit, and record maintenance."""

    def _assignable_catalog(self) -> tuple[list[str], dict[str, list[str]]]:
        """Return (data domains, field names per domain) from the live catalog.

        Drives the permissions form options and validates submitted grants, so an
        operator cannot grant a domain/field the connected sources do not declare.
        Operational grants (manage_users etc.) use the ``_OPERATIONAL_DOMAINS``
        pseudo-domains, which are not part of the data catalog.
        """
        setup = getattr(self.server, "connector_setup", None)
        catalog = tuple(setup.connector.catalog()) if setup is not None else ()
        fields_by_domain = {
            domain.name: [field.name for field in domain.fields] for domain in catalog
        }
        return sorted(fields_by_domain), fields_by_domain

    def _handle_create_grant(self, admin: sqlite3.Row) -> None:
        fields = self._read_form_fields()
        try:
            user_id = self._parse_required_int(fields, "user_id", "User")
            project_id = self._parse_required_int(fields, "project_id", "Project")
        except ValueError as exc:
            self._send_form_error(HTTPStatus.BAD_REQUEST, str(exc))
            return

        conn = db.connect(self.server.database_path)
        try:
            try:
                conn.execute(
                    """
                    insert or ignore into project_access
                      (user_id, project_id, granted_by_user_id)
                    values (?, ?, ?)
                    """,
                    (user_id, project_id, admin["id"]),
                )
                conn.commit()
            except sqlite3.IntegrityError:
                self._send_form_error(
                    HTTPStatus.BAD_REQUEST,
                    "User or project does not exist.",
                )
                return
        finally:
            conn.close()
        self._redirect_manage("users", flash="Project access granted.")

    def _handle_create_key(self) -> None:
        fields = self._read_form_fields()
        try:
            user_id = self._parse_required_int(fields, "user_id", "User")
        except ValueError as exc:
            self._send_form_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        label = fields.get("label", "").strip()
        if not label:
            self._send_form_error(HTTPStatus.BAD_REQUEST, "Label is required.")
            return

        conn = db.connect(self.server.database_path)
        try:
            try:
                created_key = create_api_key(conn, user_id=user_id, label=label)
            except sqlite3.IntegrityError:
                self._send_form_error(HTTPStatus.BAD_REQUEST, "User does not exist.")
                return
        finally:
            conn.close()
        self._send_key_created_page(created_key.prefix, created_key.plaintext)

    def _handle_create_group(self) -> None:
        fields = self._read_form_fields()
        name = fields.get("name", "").strip()
        description = fields.get("description", "").strip() or None
        if not name:
            self._send_form_error(HTTPStatus.BAD_REQUEST, "Group name is required.")
            return
        conn = db.connect(self.server.database_path)
        try:
            try:
                conn.execute(
                    "insert into user_groups (name, description) values (?, ?)",
                    (name, description),
                )
                conn.commit()
            except sqlite3.IntegrityError:
                self._send_form_error(HTTPStatus.CONFLICT, "Group already exists.")
                return
        finally:
            conn.close()
        self._redirect_manage("groups", flash="Group created.")

    def _handle_create_group_membership(self) -> None:
        fields = self._read_form_fields()
        try:
            user_id = self._parse_required_int(fields, "user_id", "User")
            group_id = self._parse_required_int(fields, "group_id", "Group")
        except ValueError as exc:
            self._send_form_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        conn = db.connect(self.server.database_path)
        try:
            conn.execute(
                "insert or ignore into user_group_memberships (user_id, group_id) values (?, ?)",
                (user_id, group_id),
            )
            conn.commit()
        finally:
            conn.close()
        self._redirect_manage("groups", flash="Member added to group.")

    def _handle_create_permission(self) -> None:
        fields = self._read_form_fields()
        try:
            group_id, user_id = self._parse_grantee(fields)
        except ValueError as exc:
            self._send_form_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        operation = fields.get("operation", "").strip()
        data_domain = fields.get("data_domain", "").strip()
        field_name = fields.get("field_name", "").strip() or None
        project_id_value = fields.get("project_id", "").strip()
        project_id = int(project_id_value) if project_id_value else None
        if not operation or not data_domain:
            self._send_form_error(
                HTTPStatus.BAD_REQUEST,
                "Operation and data domain are required.",
            )
            return
        data_domains, fields_by_domain = self._assignable_catalog()
        if data_domain not in set(data_domains) | set(_OPERATIONAL_DOMAINS):
            self._send_form_error(
                HTTPStatus.BAD_REQUEST,
                f"Unknown data domain: {data_domain}. "
                "The connected sources do not serve it.",
            )
            return
        if (
            field_name
            and data_domain in fields_by_domain
            and field_name not in fields_by_domain[data_domain]
        ):
            self._send_form_error(
                HTTPStatus.BAD_REQUEST,
                f"Field {field_name!r} is not a declared field of domain "
                f"{data_domain!r}.",
            )
            return
        conn = db.connect(self.server.database_path)
        try:
            conn.execute(
                """
                insert or ignore into permission_grants
                  (group_id, user_id, operation, data_domain, field_name, project_id)
                values (?, ?, ?, ?, ?, ?)
                """,
                (group_id, user_id, operation, data_domain, field_name, project_id),
            )
            conn.commit()
        finally:
            conn.close()
        self._redirect_manage("permissions", flash="Permission granted.")

    def _parse_grantee(
        self, fields: dict[str, str]
    ) -> tuple[int | None, int | None]:
        """Resolve the grant target to exactly one of (group_id, user_id).

        The form posts a single ``grantee`` select with ``g<id>`` / ``u<id>``
        values. Legacy callers that post a bare ``group_id`` (or ``user_id``)
        still work. Raises ``ValueError`` when the target is missing, malformed,
        or ambiguous.
        """
        grantee = fields.get("grantee", "").strip()
        group_id: int | None = None
        user_id: int | None = None
        if grantee:
            kind, raw = grantee[:1], grantee[1:]
            try:
                target = int(raw)
            except ValueError:
                raise ValueError("Grantee is invalid.")
            if kind == "g":
                group_id = target
            elif kind == "u":
                user_id = target
            else:
                raise ValueError("Grantee is invalid.")
        else:
            for key, setter in (("group_id", "g"), ("user_id", "u")):
                value = fields.get(key, "").strip()
                if value:
                    try:
                        parsed = int(value)
                    except ValueError:
                        raise ValueError(f"{key} is invalid.")
                    if setter == "g":
                        group_id = parsed
                    else:
                        user_id = parsed
        if (group_id is None) == (user_id is None):
            raise ValueError("Select exactly one grantee: a group or a user.")
        return group_id, user_id

    @staticmethod
    def _grantee_cell(row: sqlite3.Row) -> str:
        """Render a grant's grantee: a group name or a direct user email."""
        if row["group_name"] is not None:
            return f'{html.escape(row["group_name"])} <span class="tag">group</span>'
        return f'{html.escape(row["user_email"] or "")} <span class="tag">user</span>'

    def _send_manage_page(self, query: dict[str, list[str]]) -> None:
        tab = self._qs_str(query, "tab", "users")
        if tab not in {key for key, _, _ in _MANAGE_TABS}:
            tab = "users"
        q = self._qs_str(query, "q")
        flash = self._qs_str(query, "flash") or None

        tab_bar = '<div class="tabs">' + "".join(
            '<a href="/admin/users/manage?tab={key}#{anchor}"{cls}>{label}</a>'.format(
                key=key,
                anchor=anchor,
                cls=' class="active"' if key == tab else "",
                label=html.escape(label),
            )
            for key, label, anchor in _MANAGE_TABS
        ) + "</div>"

        if tab == "users":
            content = self._manage_users_tab(query, q)
        elif tab == "groups":
            content = self._manage_groups_tab(query, q)
        elif tab == "permissions":
            content = self._manage_permissions_tab(query, q)
        else:
            content = self._manage_keys_tab(query, q)

        body = f"""
        <a class="back" href="/admin/users">&larr; Users</a>
        <h1>Manage users</h1>
        <p class="subtitle">Users, groups &amp; members, permissions, and API keys.</p>
        {tab_bar}
        {content}
        """
        self._send_html(
            HTTPStatus.OK,
            self._admin_layout(
                "Manage Users",
                "users",
                body,
                html.escape(flash) if flash else None,
            ),
        )

    def _manage_users_tab(self, query: dict[str, list[str]], q: str) -> str:
        page = self._qs_int(query, "users_page")
        like = f"%{q}%"
        sort_col, sort_dir, order_by = self._sort(
            query,
            {
                "id": "id",
                "email": "email",
                "name": "display_name",
                "role": "role",
                "status": "status",
            },
            "id",
        )
        conn = db.connect(self.server.database_path)
        try:
            where = ""
            params: tuple = ()
            if q:
                where = "where email like ? or display_name like ?"
                params = (like, like)
            total = conn.execute(
                f"select count(*) as n from users {where}", params
            ).fetchone()["n"]
            rows = conn.execute(
                f"""
                select id, email, display_name, role, status, created_at
                from users {where}
                order by {order_by}
                limit ? offset ?
                """,
                (*params, _PAGE_SIZE, (page - 1) * _PAGE_SIZE),
            ).fetchall()
        finally:
            conn.close()

        base_params = {
            "tab": "users",
            "q": q,
            "sort": sort_col,
            "dir": sort_dir,
        }
        ret = (
            f"/admin/users/manage?tab=users&users_page={page}"
            + (f"&q={urllib.parse.quote(q)}" if q else "")
            + f"&sort={sort_col}&dir={sort_dir}#users"
        )
        ret_attr = html.escape(ret)
        body_rows = "\n".join(
            "<tr>"
            f"<td>{row['id']}</td>"
            f"<td>{html.escape(row['email'])}</td>"
            f"<td>{html.escape(row['display_name'])}</td>"
            f"<td>{html.escape(row['role'])}</td>"
            f"<td>{self._status_pill(row['status'])}</td>"
            '<td><div class="rowacts">'
            f'<a class="btn-small" href="/admin/users/{row["id"]}/edit?ret={urllib.parse.quote(ret)}">Edit</a>'
            + self._status_toggle_form(row["id"], row["status"], ret_attr)
            + "</div></td>"
            "</tr>"
            for row in rows
        )
        if not body_rows:
            body_rows = '<tr><td colspan="6"><span class="empty">No users match.</span></td></tr>'
        pager = self._pager(
            "/admin/users/manage", base_params, "users_page", page, total, "users"
        )
        head = self._sortable_head(
            "/admin/users/manage", base_params, "users_page", "users",
            sort_col, sort_dir,
            [
                ("id", "ID"), ("email", "Email"), ("name", "Name"),
                ("role", "Role"), ("status", "Status"), (None, "Actions"),
            ],
        )
        return (
            self._search_form("users", q, "users")
            + f"<table>{head}<tbody>{body_rows}</tbody></table>{pager}"
        )

    def _status_toggle_form(self, user_id: int, status: str, ret_attr: str) -> str:
        next_status = DISABLED if status == ACTIVE else ACTIVE
        label = "Disable" if status == ACTIVE else "Enable"
        danger = " danger" if status == ACTIVE else ""
        confirm = (
            ' onsubmit="return confirm(\'Disable this user? Their API keys will stop working.\')"'
            if status == ACTIVE
            else ""
        )
        return (
            f'<form class="inline" method="post" action="/admin/users/{user_id}/status"{confirm}>'
            f'<input type="hidden" name="status" value="{next_status}">'
            f'<input type="hidden" name="_return" value="{ret_attr}">'
            f'<button type="submit" class="small{danger}">{label}</button>'
            "</form>"
        )

    def _manage_groups_tab(self, query: dict[str, list[str]], q: str) -> str:
        groups_page = self._qs_int(query, "groups_page")
        members_page = self._qs_int(query, "members_page")
        like = f"%{q}%"
        conn = db.connect(self.server.database_path)
        try:
            gwhere, gparams = ("where name like ?", (like,)) if q else ("", ())
            g_total = conn.execute(
                f"select count(*) as n from user_groups {gwhere}", gparams
            ).fetchone()["n"]
            group_rows = conn.execute(
                f"""
                select g.id, g.name, g.description,
                  (select count(*) from user_group_memberships m where m.group_id = g.id) as members
                from user_groups g {gwhere}
                order by g.id limit ? offset ?
                """,
                (*gparams, _PAGE_SIZE, (groups_page - 1) * _PAGE_SIZE),
            ).fetchall()
            m_total = conn.execute(
                "select count(*) as n from user_group_memberships"
            ).fetchone()["n"]
            member_rows = conn.execute(
                """
                select m.id, u.id as user_id, u.email, g.id as group_id, g.name
                from user_group_memberships m
                join users u on u.id = m.user_id
                join user_groups g on g.id = m.group_id
                order by m.id limit ? offset ?
                """,
                (_PAGE_SIZE, (members_page - 1) * _PAGE_SIZE),
            ).fetchall()
            all_users = conn.execute(
                "select id, email from users order by email"
            ).fetchall()
            all_groups = conn.execute(
                "select id, name from user_groups order by name"
            ).fetchall()
        finally:
            conn.close()

        ret = f"/admin/users/manage?tab=groups#groups"
        ret_attr = html.escape(ret)
        group_body = "\n".join(
            "<tr>"
            f"<td>{row['id']}</td>"
            f"<td>{html.escape(row['name'])}</td>"
            f"<td>{html.escape(row['description'] or '')}</td>"
            f"<td>{row['members']}</td>"
            "<td>"
            + (
                f'<form class="inline" method="post" action="/admin/groups/delete"'
                ' onsubmit="return confirm(\'Delete this empty group?\')">'
                f'<input type="hidden" name="group_id" value="{row["id"]}">'
                f'<input type="hidden" name="_return" value="{ret_attr}">'
                '<button type="submit" class="small danger">Delete</button></form>'
                if row["members"] == 0
                else '<span class="empty">in use</span>'
            )
            + "</td></tr>"
            for row in group_rows
        ) or '<tr><td colspan="5"><span class="empty">No groups.</span></td></tr>'
        member_body = "\n".join(
            "<tr>"
            f"<td>{html.escape(row['email'])}</td>"
            f"<td>{html.escape(row['name'])}</td>"
            "<td>"
            f'<form class="inline" method="post" action="/admin/group-memberships/delete">'
            f'<input type="hidden" name="user_id" value="{row["user_id"]}">'
            f'<input type="hidden" name="group_id" value="{row["group_id"]}">'
            f'<input type="hidden" name="_return" value="{ret_attr}">'
            '<button type="submit" class="small danger">Remove</button></form>'
            "</td></tr>"
            for row in member_rows
        ) or '<tr><td colspan="3"><span class="empty">No memberships.</span></td></tr>'

        user_opts = self._options(all_users, "id", lambda r: r["email"])
        group_opts = self._options(all_groups, "id", lambda r: r["name"])
        g_pager = self._pager(
            "/admin/users/manage", {"tab": "groups", "q": q}, "groups_page",
            groups_page, g_total, "groups",
        )
        m_pager = self._pager(
            "/admin/users/manage", {"tab": "groups"}, "members_page",
            members_page, m_total, "members",
        )
        return (
            '<h2 id="groups">Create group</h2>'
            '<form method="post" action="/admin/groups/create">'
            '<label>Name <input type="text" name="name" required></label>'
            '<label>Description <input type="text" name="description"></label>'
            f'<input type="hidden" name="_return" value="{ret_attr}">'
            '<button type="submit">Create group</button></form>'
            + self._search_form("groups", q, "groups").replace('id="groups"', 'id="groups-search"')
            + "<table><thead><tr><th>ID</th><th>Name</th><th>Description</th>"
            "<th>Members</th><th>Actions</th></tr></thead>"
            f"<tbody>{group_body}</tbody></table>{g_pager}"
            '<h2 id="members">Add user to group</h2>'
            '<form method="post" action="/admin/group-memberships/create">'
            f'<label>User <select name="user_id" required>{user_opts}</select></label>'
            f'<label>Group <select name="group_id" required>{group_opts}</select></label>'
            f'<input type="hidden" name="_return" value="{ret_attr}">'
            '<button type="submit">Add member</button></form>'
            "<table><thead><tr><th>User</th><th>Group</th><th>Actions</th></tr></thead>"
            f"<tbody>{member_body}</tbody></table>{m_pager}"
        )

    def _manage_permissions_tab(self, query: dict[str, list[str]], q: str) -> str:
        page = self._qs_int(query, "perms_page")
        like = f"%{q}%"
        sort_col, sort_dir, order_by = self._sort(
            query,
            {
                "id": "permission_grants.id",
                "grantee": "coalesce(user_groups.name, users.email)",
                "operation": "permission_grants.operation",
                "domain": "permission_grants.data_domain",
            },
            "id",
        )
        conn = db.connect(self.server.database_path)
        try:
            where, params = (
                (
                    "where user_groups.name like ? or users.email like ?",
                    (like, like),
                )
                if q
                else ("", ())
            )
            total = conn.execute(
                f"""
                select count(*) as n from permission_grants
                left join user_groups on user_groups.id = permission_grants.group_id
                left join users on users.id = permission_grants.user_id
                {where}
                """,
                params,
            ).fetchone()["n"]
            rows = conn.execute(
                f"""
                select permission_grants.id, user_groups.name as group_name,
                  users.email as user_email,
                  permission_grants.operation, permission_grants.data_domain,
                  permission_grants.field_name, projects.project_code
                from permission_grants
                left join user_groups on user_groups.id = permission_grants.group_id
                left join users on users.id = permission_grants.user_id
                left join projects on projects.id = permission_grants.project_id
                {where}
                order by {order_by} limit ? offset ?
                """,
                (*params, _PAGE_SIZE, (page - 1) * _PAGE_SIZE),
            ).fetchall()
            all_groups = conn.execute(
                "select id, name from user_groups order by name"
            ).fetchall()
            all_users = conn.execute(
                "select id, email from users order by email"
            ).fetchall()
            all_projects = conn.execute(
                "select id, project_code, name from projects order by project_code"
            ).fetchall()
        finally:
            conn.close()

        base_params = {"tab": "permissions", "q": q, "sort": sort_col, "dir": sort_dir}
        ret = (
            f"/admin/users/manage?tab=permissions&perms_page={page}"
            f"&sort={sort_col}&dir={sort_dir}#permissions"
        )
        ret_attr = html.escape(ret)
        body_rows = "\n".join(
            "<tr>"
            f"<td>{row['id']}</td>"
            f"<td>{self._grantee_cell(row)}</td>"
            f"<td>{html.escape(row['operation'])}</td>"
            f"<td>{html.escape(row['data_domain'])}</td>"
            f"<td>{html.escape(row['field_name'] or '')}</td>"
            f"<td>{html.escape(row['project_code'] or 'All projects')}</td>"
            "<td>"
            f'<form class="inline" method="post" action="/admin/permissions/delete"'
            ' onsubmit="return confirm(\'Remove this permission grant?\')">'
            f'<input type="hidden" name="permission_id" value="{row["id"]}">'
            f'<input type="hidden" name="_return" value="{ret_attr}">'
            '<button type="submit" class="small danger">Remove</button></form>'
            "</td></tr>"
            for row in rows
        ) or '<tr><td colspan="7"><span class="empty">No permission grants.</span></td></tr>'

        grantee_groups = "".join(
            f'<option value="g{r["id"]}">{html.escape(r["name"])}</option>'
            for r in all_groups
        )
        grantee_users = "".join(
            f'<option value="u{r["id"]}">{html.escape(r["email"])}</option>'
            for r in all_users
        )
        grantee_opts = (
            (f'<optgroup label="Groups">{grantee_groups}</optgroup>' if grantee_groups else "")
            + (f'<optgroup label="Users">{grantee_users}</optgroup>' if grantee_users else "")
        )
        project_opts = self._options(
            all_projects, "id", lambda r: f"{r['project_code']}: {r['name']}"
        )
        operation_opts = "\n".join(
            f'<option value="{v}">{v}</option>'
            for v in ("read", "import", "manage_users", "manage_keys",
                      "manage_permissions", "view_audit")
        )
        data_domains, fields_by_domain = self._assignable_catalog()
        data_opts = "".join(
            f'<option value="{html.escape(d)}">{html.escape(d)}</option>'
            for d in data_domains
        )
        op_opts = "".join(
            f'<option value="{v}">{v}</option>' for v in _OPERATIONAL_DOMAINS
        )
        domain_opts = (
            (f'<optgroup label="Data domains">{data_opts}</optgroup>' if data_opts else "")
            + f'<optgroup label="Operational">{op_opts}</optgroup>'
        )
        all_fields = sorted({f for fields in fields_by_domain.values() for f in fields})
        field_datalist = '<datalist id="field-options">' + "".join(
            f'<option value="{html.escape(f)}">' for f in all_fields
        ) + "</datalist>"
        pager = self._pager(
            "/admin/users/manage", base_params, "perms_page",
            page, total, "permissions",
        )
        head = self._sortable_head(
            "/admin/users/manage", base_params, "perms_page", "permissions",
            sort_col, sort_dir,
            [
                ("id", "ID"), ("grantee", "Grantee"), ("operation", "Operation"),
                ("domain", "Domain"), (None, "Field"), (None, "Project"),
                (None, "Actions"),
            ],
        )
        return (
            '<h2 id="permissions">Grant permission</h2>'
            '<form method="post" action="/admin/permissions/create">'
            f'<label>Grantee <select name="grantee" required>{grantee_opts}</select></label>'
            f'<label>Operation <select name="operation" required>{operation_opts}</select></label>'
            f'<label>Domain <select name="data_domain" required>{domain_opts}</select></label>'
            '<label>Field <input type="text" name="field_name" list="field-options">'
            f'{field_datalist}</label>'
            '<label>Project <select name="project_id">'
            f'<option value="">All projects</option>{project_opts}</select></label>'
            f'<input type="hidden" name="_return" value="{ret_attr}">'
            '<button type="submit">Grant permission</button></form>'
            + self._search_form("permissions", q, "permissions").replace(
                'id="permissions"', 'id="permissions-search"'
            )
            + f"<table>{head}<tbody>{body_rows}</tbody></table>{pager}"
        )

    def _manage_keys_tab(self, query: dict[str, list[str]], q: str) -> str:
        page = self._qs_int(query, "keys_page")
        like = f"%{q}%"
        sort_col, sort_dir, order_by = self._sort(
            query,
            {
                "id": "api_keys.id",
                "user": "users.email",
                "label": "api_keys.label",
                "status": "api_keys.status",
            },
            "id",
        )
        conn = db.connect(self.server.database_path)
        try:
            where, params = (
                (
                    "where users.email like ? or api_keys.label like ? "
                    "or api_keys.key_prefix like ?",
                    (like, like, like),
                )
                if q
                else ("", ())
            )
            total = conn.execute(
                f"""
                select count(*) as n from api_keys
                join users on users.id = api_keys.user_id {where}
                """,
                params,
            ).fetchone()["n"]
            rows = conn.execute(
                f"""
                select api_keys.id, users.email, api_keys.key_prefix,
                  api_keys.label, api_keys.status, api_keys.created_at
                from api_keys join users on users.id = api_keys.user_id {where}
                order by {order_by} limit ? offset ?
                """,
                (*params, _PAGE_SIZE, (page - 1) * _PAGE_SIZE),
            ).fetchall()
            all_users = conn.execute(
                "select id, email from users order by email"
            ).fetchall()
        finally:
            conn.close()

        base_params = {"tab": "keys", "q": q, "sort": sort_col, "dir": sort_dir}
        ret = (
            f"/admin/users/manage?tab=keys&keys_page={page}"
            f"&sort={sort_col}&dir={sort_dir}#keys"
        )
        ret_attr = html.escape(ret)
        body_rows = "\n".join(
            "<tr>"
            f"<td>{row['id']}</td>"
            f"<td>{html.escape(row['email'])}</td>"
            f"<td>{html.escape(row['key_prefix'])}</td>"
            f"<td>{html.escape(row['label'])}</td>"
            f"<td>{self._status_pill(row['status'])}</td>"
            '<td><div class="rowacts">'
            + (
                f'<form class="inline" method="post" action="/admin/keys/{row["id"]}/revoke"'
                ' onsubmit="return confirm(\'Revoke this key? It cannot be undone.\')">'
                f'<input type="hidden" name="_return" value="{ret_attr}">'
                '<button type="submit" class="small danger">Revoke</button></form>'
                if row["status"] == ACTIVE
                else ""
            )
            + f'<form class="inline" method="post" action="/admin/keys/{row["id"]}/relabel">'
            '<input type="text" name="label" placeholder="New label" '
            'style="min-width:120px;padding:5px 8px">'
            f'<input type="hidden" name="_return" value="{ret_attr}">'
            '<button type="submit" class="small">Relabel</button></form>'
            "</div></td></tr>"
            for row in rows
        ) or '<tr><td colspan="6"><span class="empty">No API keys.</span></td></tr>'

        user_opts = self._options(all_users, "id", lambda r: r["email"])
        pager = self._pager(
            "/admin/users/manage", base_params, "keys_page", page, total, "keys"
        )
        head = self._sortable_head(
            "/admin/users/manage", base_params, "keys_page", "keys",
            sort_col, sort_dir,
            [
                ("id", "ID"), ("user", "User"), (None, "Prefix"),
                ("label", "Label"), ("status", "Status"), (None, "Actions"),
            ],
        )
        return (
            '<h2 id="keys">Create API key</h2>'
            '<p class="hint" style="color:var(--muted);font-size:13px;margin:0 0 12px">'
            "The key is shown only once, immediately after creation.</p>"
            '<form method="post" action="/admin/keys/create">'
            f'<label>User <select name="user_id" required>{user_opts}</select></label>'
            '<label>Label <input type="text" name="label" required></label>'
            f'<input type="hidden" name="_return" value="{ret_attr}">'
            '<button type="submit">Create key</button></form>'
            + self._search_form("keys", q, "keys").replace('id="keys"', 'id="keys-search"')
            + f"<table>{head}<tbody>{body_rows}</tbody></table>{pager}"
        )

    def _send_user_edit_page(
        self,
        user_id: int,
        query: dict[str, list[str]],
        message: str | None = None,
        status: HTTPStatus = HTTPStatus.OK,
    ) -> None:
        ret = self._qs_str(query, "ret") or "/admin/users/manage?tab=users#users"
        if not ret.startswith("/admin/"):
            ret = "/admin/users/manage?tab=users#users"
        conn = db.connect(self.server.database_path)
        try:
            user = conn.execute(
                "select id, email, display_name, role, status from users where id = ?",
                (user_id,),
            ).fetchone()
            if user is None:
                conn.close()
                self._send_form_error(HTTPStatus.NOT_FOUND, "That user does not exist.")
                return
            group_rows = conn.execute(
                "select id, name, description from user_groups order by name"
            ).fetchall()
            member_ids = {
                int(r["group_id"])
                for r in conn.execute(
                    "select group_id from user_group_memberships where user_id = ?",
                    (user_id,),
                ).fetchall()
            }
            project_rows = conn.execute(
                "select id, project_code, name, stage, department from projects order by project_code"
            ).fetchall()
            grant_ids = {
                int(r["project_id"])
                for r in conn.execute(
                    "select project_id from project_access where user_id = ?",
                    (user_id,),
                ).fetchall()
            }
            company_rows = conn.execute(
                "select id, name, unified_social_credit_code from companies order by name"
            ).fetchall()
            company_grant_ids = {
                int(r["company_id"])
                for r in conn.execute(
                    "select company_id from company_access where user_id = ?",
                    (user_id,),
                ).fetchall()
            }
            # C4: effective permissions = the user's direct grants ∪ their
            # groups' grants. Read-only here; grants are created on the
            # permissions tab. Mirrors the authorization scope in
            # legal_mcp.policy.grant_scope_clause.
            effective_grants = conn.execute(
                """
                select pg.operation, pg.data_domain, pg.field_name,
                  projects.project_code, ug.name as group_name
                from permission_grants pg
                left join user_groups ug on ug.id = pg.group_id
                left join projects on projects.id = pg.project_id
                where pg.allowed = 1 and (
                    pg.user_id = ?
                    or pg.group_id in (
                      select group_id from user_group_memberships
                      where user_id = ?
                    )
                )
                order by pg.data_domain, pg.operation, pg.field_name
                """,
                (user_id, user_id),
            ).fetchall()
        finally:
            conn.close()

        ret_attr = html.escape(ret)
        role_options = "\n".join(
            f'<option value="{html.escape(role)}"'
            f'{" selected" if role == user["role"] else ""}>{html.escape(role)}</option>'
            for role in (ROLE_BUSINESS, ROLE_LEGAL, ROLE_AUDITOR, ROLE_ADMIN)
        )
        if group_rows:
            group_items = "\n".join(
                self._checkrow(
                    name="group_ids",
                    value=str(row["id"]),
                    title=row["name"],
                    sub=row["description"] or "No description",
                    perms=[],
                    checked=int(row["id"]) in member_ids,
                )
                for row in group_rows
            )
            group_block = self._checklist_section("groups-cl", group_items)
        else:
            group_block = '<p class="empty">No groups yet.</p>'
        if project_rows:
            project_items = "\n".join(
                self._checkrow(
                    name="project_ids",
                    value=str(row["id"]),
                    title=f"{row['project_code']} — {row['name']}",
                    sub=" · ".join(
                        p for p in (row["stage"], row["department"]) if p
                    ),
                    perms=[],
                    checked=int(row["id"]) in grant_ids,
                )
                for row in project_rows
            )
            project_block = self._checklist_section("projects-cl", project_items)
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
                    checked=int(row["id"]) in company_grant_ids,
                )
                for row in company_rows
            )
            company_block = self._checklist_section("companies-cl", company_items)
        else:
            company_block = '<p class="empty">No companies imported yet.</p>'

        if effective_grants:
            grant_rows = "\n".join(
                "<tr>"
                f"<td>{html.escape(r['data_domain'])}</td>"
                f"<td>{html.escape(r['operation'])}</td>"
                f"<td>{html.escape(r['field_name'] or 'All fields')}</td>"
                f"<td>{html.escape(r['project_code'] or 'All projects')}</td>"
                "<td>"
                + (
                    "Direct"
                    if r["group_name"] is None
                    else f'via {html.escape(r["group_name"])}'
                )
                + "</td></tr>"
                for r in effective_grants
            )
            effective_block = (
                "<table><thead><tr><th>Domain</th><th>Operation</th><th>Field</th>"
                "<th>Project</th><th>Source</th></tr></thead>"
                f"<tbody>{grant_rows}</tbody></table>"
            )
        else:
            effective_block = (
                '<p class="empty">No effective permissions '
                "(no direct or group grants).</p>"
            )

        body = f"""
        <a class="back" href="{ret_attr}">&larr; Manage users</a>
        <h1>Edit user</h1>
        <p class="subtitle">{html.escape(user['email'])} · {self._status_pill(user['status'])}</p>
        <form class="provision" method="post" action="/admin/users/{user_id}/edit">
          <input type="hidden" name="_return" value="{ret_attr}">
          <section class="panel">
            <h2>Identity</h2>
            <div class="grid">
              <label>Display name <input type="text" name="display_name" value="{html.escape(user['display_name'])}" required></label>
              <label>Role <select name="role" required>{role_options}</select></label>
            </div>
          </section>
          <section class="panel">
            <h2>Groups</h2>
            {group_block}
          </section>
          <section class="panel">
            <h2>Project Access</h2>
            {project_block}
          </section>
          <section class="panel">
            <h2>Company Access</h2>
            {company_block}
          </section>
          <section class="panel">
            <h2>Effective permissions</h2>
            <p class="hint">The user's direct grants combined with their groups'
            grants. Read-only — manage grants on the
            <a href="/admin/users/manage?tab=permissions#permissions">permissions tab</a>.</p>
            {effective_block}
          </section>
          <div class="actionbar">
            <button type="submit">Save changes</button>
            <a class="btn btn-secondary" href="{ret_attr}">Cancel</a>
          </div>
        </form>
        <form class="provision" method="post" action="/admin/users/{user_id}/password" style="margin-top:8px">
          <input type="hidden" name="_return" value="{ret_attr}">
          <section class="panel">
            <h2>Set password</h2>
            <p class="hint">Only <code>admin</code> users use a password (admin login). Stored for other roles but not yet used for auth.</p>
            <div class="grid">
              <label>New password <input type="password" name="password" required></label>
            </div>
            <div class="actionbar"><button type="submit">Update password</button></div>
          </section>
        </form>
        <script>
        (function(){{
          document.querySelectorAll('.checklist-section').forEach(function(sec){{
            var list=sec.querySelector('.checklist'),
                filter=sec.querySelector('.cl-filter'),
                count=sec.querySelector('.cl-count'),
                rows=Array.prototype.slice.call(list.querySelectorAll('.checkrow'));
            function visible(){{return rows.filter(function(r){{return r.style.display!=='none';}});}}
            function update(){{
              var n=rows.filter(function(r){{return r.querySelector('input').checked;}}).length;
              count.textContent=n+' selected';
            }}
            filter.addEventListener('input',function(){{
              var q=filter.value.toLowerCase();
              rows.forEach(function(r){{
                r.style.display=r.textContent.toLowerCase().indexOf(q)>-1?'':'none';
              }});
            }});
            sec.querySelector('.cl-all').addEventListener('click',function(){{
              visible().forEach(function(r){{r.querySelector('input').checked=true;}});update();
            }});
            sec.querySelector('.cl-none').addEventListener('click',function(){{
              visible().forEach(function(r){{r.querySelector('input').checked=false;}});update();
            }});
            sec.querySelector('.cl-invert').addEventListener('click',function(){{
              visible().forEach(function(r){{var i=r.querySelector('input');i.checked=!i.checked;}});update();
            }});
            list.addEventListener('change',update);update();
          }});
        }})();
        </script>
        """
        self._send_html(
            status,
            self._admin_layout(
                "Edit User",
                "users",
                body,
                message,
                message_kind="error" if message else "info",
            ),
        )

    def _handle_update_user(self, admin: sqlite3.Row, user_id: int) -> None:
        fields = self._read_form_multi()

        def single(key: str) -> str:
            values = fields.get(key, [])
            return values[0].strip() if values else ""

        display_name = single("display_name")
        role = single("role")
        if not display_name:
            self._send_form_error(HTTPStatus.BAD_REQUEST, "Display name is required.")
            return
        if role not in _ALLOWED_ROLES:
            self._send_form_error(HTTPStatus.BAD_REQUEST, "A valid role is required.")
            return
        try:
            group_ids = self._parse_id_list(fields.get("group_ids", []))
            project_ids = self._parse_id_list(fields.get("project_ids", []))
            company_ids = self._parse_id_list(fields.get("company_ids", []))
        except ValueError:
            self._send_form_error(HTTPStatus.BAD_REQUEST, "Invalid selection.")
            return

        conn = db.connect(self.server.database_path)
        try:
            update_user(conn, user_id=user_id, display_name=display_name, role=role)
            set_user_groups(conn, user_id=user_id, group_ids=group_ids)
            set_user_projects(
                conn,
                user_id=user_id,
                project_ids=project_ids,
                granted_by_user_id=admin["id"],
            )
            set_user_companies(
                conn,
                user_id=user_id,
                company_ids=company_ids,
                granted_by_user_id=admin["id"],
            )
        except AdminOperationError as exc:
            self._send_form_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        finally:
            conn.close()
        self._redirect_return(
            {"_return": single("_return")}, "/admin/users/manage?tab=users#users"
        )

    def _handle_user_status(self, user_id: int) -> None:
        fields = self._read_form_fields()
        status = fields.get("status", "").strip()
        conn = db.connect(self.server.database_path)
        try:
            set_user_status(conn, user_id=user_id, status=status)
        except AdminOperationError as exc:
            self._send_form_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        finally:
            conn.close()
        self._redirect_return(fields, "/admin/users/manage?tab=users#users")

    def _handle_user_password(self, user_id: int) -> None:
        fields = self._read_form_fields()
        password = fields.get("password", "")
        conn = db.connect(self.server.database_path)
        try:
            set_user_password(conn, user_id=user_id, password=password)
        except AdminOperationError as exc:
            self._send_form_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        finally:
            conn.close()
        self._redirect_return(fields, "/admin/users/manage?tab=users#users")

    def _handle_revoke_key(self, key_id: int) -> None:
        fields = self._read_form_fields()
        conn = db.connect(self.server.database_path)
        try:
            revoke_api_key(conn, key_id=key_id)
        except AdminOperationError as exc:
            self._send_form_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        finally:
            conn.close()
        self._redirect_return(fields, "/admin/users/manage?tab=keys#keys")

    def _handle_relabel_key(self, key_id: int) -> None:
        fields = self._read_form_fields()
        label = fields.get("label", "").strip()
        conn = db.connect(self.server.database_path)
        try:
            relabel_api_key(conn, key_id=key_id, label=label)
        except AdminOperationError as exc:
            self._send_form_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        finally:
            conn.close()
        self._redirect_return(fields, "/admin/users/manage?tab=keys#keys")

    def _handle_delete_group(self) -> None:
        fields = self._read_form_fields()
        try:
            group_id = self._parse_required_int(fields, "group_id", "Group")
        except ValueError as exc:
            self._send_form_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        conn = db.connect(self.server.database_path)
        try:
            members = conn.execute(
                "select count(*) as n from user_group_memberships where group_id = ?",
                (group_id,),
            ).fetchone()["n"]
            if members:
                self._send_form_error(
                    HTTPStatus.BAD_REQUEST,
                    "Cannot delete a group that still has members.",
                )
                return
            conn.execute("delete from permission_grants where group_id = ?", (group_id,))
            conn.execute("delete from user_groups where id = ?", (group_id,))
            conn.commit()
        finally:
            conn.close()
        self._redirect_return(fields, "/admin/users/manage?tab=groups#groups")

    def _handle_delete_membership(self) -> None:
        fields = self._read_form_fields()
        try:
            user_id = self._parse_required_int(fields, "user_id", "User")
            group_id = self._parse_required_int(fields, "group_id", "Group")
        except ValueError as exc:
            self._send_form_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        conn = db.connect(self.server.database_path)
        try:
            conn.execute(
                "delete from user_group_memberships where user_id = ? and group_id = ?",
                (user_id, group_id),
            )
            conn.commit()
        finally:
            conn.close()
        self._redirect_return(fields, "/admin/users/manage?tab=groups#members")

    def _handle_delete_permission(self) -> None:
        fields = self._read_form_fields()
        try:
            permission_id = self._parse_required_int(
                fields, "permission_id", "Permission"
            )
        except ValueError as exc:
            self._send_form_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        conn = db.connect(self.server.database_path)
        try:
            conn.execute("delete from permission_grants where id = ?", (permission_id,))
            conn.commit()
        finally:
            conn.close()
        self._redirect_return(
            fields, "/admin/users/manage?tab=permissions#permissions"
        )

    def _send_key_created_page(self, prefix: str, plaintext: str) -> None:
        body = f"""
        <a class="back" href="/admin/users/manage?tab=keys#keys">&larr; API Keys</a>
        <h1>API Key Created</h1>
        <div class="keypanel">
          <p class="label">Prefix {html.escape(prefix)}</p>
          <code>{html.escape(plaintext)}</code>
          <p class="warn">Copy this now — it will not be shown again.</p>
        </div>
        <div class="actionbar">
          <a class="btn" href="/admin/users/manage?tab=keys#keys">Back to API keys</a>
        </div>
        """
        self._send_html(
            HTTPStatus.OK, self._admin_layout("API Key Created", "users", body)
        )
