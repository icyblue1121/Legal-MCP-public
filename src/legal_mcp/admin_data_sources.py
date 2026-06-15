"""Add-data-source wizard + guardrails (v0.5.7).

A server-rendered, admin-only flow to onboard a new data source at runtime into the
``data_sources`` registry (v0.5.6), so a deployment can connect a source without
editing the YAML config or restarting. The flow is:

1. **Choose type + connect** — pick a registered connector type and its connection
   parameters (a local file path + format; an online source names its resource id
   and credential env refs). ``/admin/data-sources/new``.
2. **Introspect** — ``describe_schema`` lists the source's real *column names*
   (values-free), so the operator reviews a real schema. ``/admin/data-sources/introspect``.
3. **Review + enable** — tick the columns to expose, mark identity fields, set the
   record scope, add aliases, then register. ``/admin/data-sources/register``.

Security guardrails (the reason the wizard and the guardrails ship together):

* **Admin only.** The routes are reached only after the ``_current_admin`` check.
* **Default-deny by construction.** A new source defaults to ``record_scope: none``
  and is registered with **zero permission grants**, so its fields are denied to
  everyone until an admin grants them — onboarding never auto-discloses.
* **Only declared columns.** Only the columns the operator ticked are written into
  the source declaration; an un-ticked column is never queryable. No row value ever
  passes through the wizard — only column names and metadata.
* **Audited.** Registration writes an audit record (a high-value event).
"""

from __future__ import annotations

import html
import json
import sqlite3
import urllib.parse
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any

from legal_mcp import db
from legal_mcp.audit import DEFAULT_AUDIT_PATH, write_audit_record
from legal_mcp.connectors.base import RECORD_SCOPE_MODES, record_scope_from_dict
from legal_mcp.connector_config import build_source_connector

# Connector types the wizard can onboard. ``local_file`` needs no service;
# ``tencent_docs`` is the online sample (v0.5.9) and needs an access token in the
# environment to introspect. Keep in sync with the connector config factory.
WIZARD_TYPES = {
    "local_file": "Local file (CSV / XLSX / JSON / JSONL / Markdown)",
    "tencent_docs": "Tencent Docs smart table (online)",
}

_LOCAL_FILE_FORMATS = ("csv", "xlsx", "json", "jsonl", "md")


@dataclass(frozen=True)
class ColumnReview:
    """One column's review decisions from the wizard form."""

    name: str
    include: bool
    is_identity: bool
    aliases: tuple[str, ...]


def build_source_config(
    *,
    source_type: str,
    name: str,
    domain: str,
    connect: dict[str, str],
    columns: list[ColumnReview],
    record_scope: dict[str, str] | None,
) -> dict[str, Any]:
    """Assemble a connector source declaration from reviewed wizard input (v0.5.7).

    Only *included* columns are written, so an un-ticked column is never queryable.
    The record scope defaults to ``none`` (default-deny in concert with zero grants)
    unless the operator explicitly chose another mode. Raises ``ValueError`` on a
    bad type / empty selection / invalid scope, so the wizard fails closed.
    """
    if source_type not in WIZARD_TYPES:
        raise ValueError(f"unsupported source type: {source_type!r}")
    if not name.strip() or not domain.strip():
        raise ValueError("source name and domain are required")
    included = [column for column in columns if column.include]
    if not included:
        raise ValueError("select at least one column to expose")

    fields = [
        {
            "name": column.name,
            **({"is_identity": True} if column.is_identity else {}),
            **({"aliases": list(column.aliases)} if column.aliases else {}),
        }
        for column in included
    ]
    # Validate/normalize the scope through the shared parser (fails closed on a bad
    # mode); default is ``none`` — no row scope, so the field gate is the only gate.
    scope_block = record_scope or {"mode": "none"}
    record_scope_from_dict(scope_block)  # raises on an invalid block

    domain_decl: dict[str, Any] = {
        "name": domain,
        "fields": fields,
        "record_scope": scope_block,
    }
    if source_type == "local_file":
        domain_decl["path"] = connect.get("path", "")
        domain_decl["format"] = connect.get("format", "")
        return {"type": source_type, "name": name, "domains": [domain_decl]}
    if source_type == "tencent_docs":
        domain_decl["sheet_id"] = connect.get("sheet_id", "")
        source: dict[str, Any] = {
            "type": source_type,
            "name": name,
            "file_id": connect.get("file_id", ""),
            "domains": [domain_decl],
        }
        if connect.get("access_token_env"):
            source["access_token_env"] = connect["access_token_env"]
        return source
    raise ValueError(f"unsupported source type: {source_type!r}")


def introspect_columns(
    source_type: str, connect: dict[str, str], *, database_path: str
) -> tuple[str, ...]:
    """List a prospective source's real column names (values-free) for review.

    Builds a connector from the connection parameters with *no* declared fields and
    calls ``describe_schema`` — discovery never depends on (or returns) row values.
    """
    if source_type == "local_file":
        source: dict[str, Any] = {
            "type": "local_file",
            "name": "__introspect__",
            "domains": [
                {
                    "name": "__introspect__",
                    "path": connect.get("path", ""),
                    "format": connect.get("format", ""),
                    "fields": [],
                }
            ],
        }
    elif source_type == "tencent_docs":
        # Online introspection needs an access token in the environment (read by the
        # concrete client). Built here with no declared fields; describe_schema lists
        # the live columns, values-free.
        source = {
            "type": "tencent_docs",
            "name": "__introspect__",
            "file_id": connect.get("file_id", ""),
            "domains": [{"name": "__introspect__", "sheet_id": connect.get("sheet_id", ""), "fields": []}],
        }
        if connect.get("access_token_env"):
            source["access_token_env"] = connect["access_token_env"]
    else:
        raise ValueError(f"unsupported source type: {source_type!r}")
    connector, _ = build_source_connector(source, database_path=database_path)
    tables = connector.describe_schema()
    columns: list[str] = []
    for table in tables:
        for column in table.fields:
            if column and column not in columns:
                columns.append(column)
    return tuple(columns)


def register_data_source(
    conn: sqlite3.Connection,
    *,
    name: str,
    source_type: str,
    config: dict[str, Any],
    database_path: str,
    created_by_user_id: int | None,
    audit_path: str | Any = DEFAULT_AUDIT_PATH,
) -> None:
    """Persist a reviewed source as an ``active`` registry row, fail-closed (v0.5.7).

    Validates the declaration by *building the connector* (a malformed declaration
    raises before anything is written), then inserts the row and records an audit
    event. The new source carries no grants, so its domain is default-deny until an
    admin grants its fields.
    """
    # Validate by construction — never persist a source the gateway cannot build.
    build_source_connector(config, database_path=database_path)
    served = [domain.get("name") for domain in config.get("domains", [])]
    conn.execute(
        "insert into data_sources (name, type, status, config_json, created_by_user_id) "
        "values (?, ?, 'active', ?, ?)",
        (name, source_type, json.dumps(config, ensure_ascii=False), created_by_user_id),
    )
    conn.commit()
    write_audit_record(
        tool_name="admin.data_source.register",
        rationale="admin onboarded a data source via the wizard",
        source_client="admin",
        arguments={"name": name, "type": source_type, "domains": served},
        result_status="ok",
        error_code=None,
        audit_path=audit_path,
    )


class DataSourcesWizardMixin:
    """Admin wizard pages + POST handlers for onboarding a data source (v0.5.7)."""

    def _send_data_source_new_page(self, message: str | None = None) -> None:
        type_options = "".join(
            f'<option value="{html.escape(key)}">{html.escape(label)}</option>'
            for key, label in WIZARD_TYPES.items()
        )
        format_options = "".join(
            f'<option value="{fmt}">{fmt}</option>' for fmt in _LOCAL_FILE_FORMATS
        )
        body = f"""
        <h1>Add data source</h1>
        <p class="subtitle">Connect a new source at runtime. Step 1 of 3: choose a
        type and where it lives, then introspect its columns. No data is read —
        only the schema.</p>
        <form method="post" action="/admin/data-sources/introspect" class="stack">
          <label>Source name<input name="name" required placeholder="vendor-register"></label>
          <label>Domain name<input name="domain" required placeholder="vendor"></label>
          <label>Type<select name="type">{type_options}</select></label>
          <fieldset><legend>Local file</legend>
            <label>File path<input name="path" placeholder="/data/vendors.csv"></label>
            <label>Format<select name="format">{format_options}</select></label>
          </fieldset>
          <fieldset><legend>Online (Tencent Docs)</legend>
            <label>File id<input name="file_id" placeholder="fileXXXX"></label>
            <label>Sheet id<input name="sheet_id" placeholder="sheetXXXX"></label>
            <label>Access-token env var<input name="access_token_env" placeholder="TENCENT_DOCS_TOKEN"></label>
          </fieldset>
          <button type="submit">Introspect columns &rarr;</button>
        </form>
        """
        self._send_html(
            HTTPStatus.OK,
            self._admin_layout("Add data source", "database", body, message),
        )

    _CONNECT_KEYS = ("path", "format", "file_id", "sheet_id", "access_token_env")

    @classmethod
    def _connect_from_form(cls, fields: dict[str, str]) -> dict[str, str]:
        return {key: fields.get(key, "").strip() for key in cls._CONNECT_KEYS}

    def _handle_data_source_introspect(self) -> None:
        fields = self._read_form_fields()
        name = fields.get("name", "").strip()
        domain = fields.get("domain", "").strip()
        source_type = fields.get("type", "").strip()
        connect = self._connect_from_form(fields)
        if not name or not domain:
            self._send_data_source_new_page("Source name and domain are required.")
            return
        try:
            columns = introspect_columns(source_type, connect, database_path=str(self.server.database_path))
        except (ValueError, OSError, ImportError) as exc:
            self._send_data_source_new_page(f"Could not read the source: {exc}")
            return
        if not columns:
            self._send_data_source_new_page("No columns were found in that source.")
            return
        self._send_data_source_review_page(name, domain, source_type, connect, columns)

    def _send_data_source_review_page(
        self,
        name: str,
        domain: str,
        source_type: str,
        connect: dict[str, str],
        columns: tuple[str, ...],
    ) -> None:
        rows = "\n".join(
            f"""<tr>
              <td><label><input type="checkbox" name="include__{html.escape(col)}" checked> {html.escape(col)}</label></td>
              <td><input type="checkbox" name="identity__{html.escape(col)}"></td>
              <td><input name="alias__{html.escape(col)}" placeholder="别名, comma-separated"></td>
            </tr>"""
            for col in columns
        )
        scope_options = "".join(
            f'<option value="{mode}">{mode}</option>' for mode in sorted(RECORD_SCOPE_MODES)
        )
        hidden = (
            f'<input type="hidden" name="name" value="{html.escape(name)}">'
            f'<input type="hidden" name="domain" value="{html.escape(domain)}">'
            f'<input type="hidden" name="type" value="{html.escape(source_type)}">'
            + "".join(
                f'<input type="hidden" name="{key}" value="{html.escape(connect.get(key, ""))}">'
                for key in self._CONNECT_KEYS
            )
        )
        body = f"""
        <h1>Review &amp; enable: {html.escape(domain)}</h1>
        <p class="subtitle">Step 2 of 3. Tick the columns to expose, mark identity
        fields, and choose the row scope. Un-ticked columns are never queryable. The
        source is registered with no grants, so it is default-deny until you grant
        its fields.</p>
        <form method="post" action="/admin/data-sources/register" class="stack">
          {hidden}
          <table class="grid">
            <tr><th>Expose column</th><th>Identity</th><th>Aliases</th></tr>
            {rows}
          </table>
          <label>Record scope<select name="record_scope_mode">{scope_options}</select></label>
          <label>Owner/scope field (only for by_owner / by_governed_code)
            <input name="record_scope_field" placeholder="owner_email / project_code"></label>
          <button type="submit">Register &amp; enable</button>
        </form>
        """
        self._send_html(
            HTTPStatus.OK,
            self._admin_layout("Review data source", "database", body, None),
        )

    def _handle_data_source_register(self) -> None:
        fields = self._read_form_fields()
        name = fields.get("name", "").strip()
        domain = fields.get("domain", "").strip()
        source_type = fields.get("type", "").strip()
        connect = self._connect_from_form(fields)

        columns: list[ColumnReview] = []
        for key in fields:
            if not key.startswith("include__"):
                continue
            col = key[len("include__"):]
            aliases = tuple(
                a.strip()
                for a in (fields.get(f"alias__{col}", "") or "").replace("，", ",").split(",")
                if a.strip()
            )
            columns.append(
                ColumnReview(
                    name=col,
                    include=True,  # presence of the checkbox key means ticked
                    is_identity=bool(fields.get(f"identity__{col}")),
                    aliases=aliases,
                )
            )

        scope = self._scope_from_form(fields)
        try:
            config = build_source_config(
                source_type=source_type,
                name=name,
                domain=domain,
                connect=connect,
                columns=columns,
                record_scope=scope,
            )
            conn = db.connect(self.server.database_path)
            try:
                register_data_source(
                    conn,
                    name=name,
                    source_type=source_type,
                    config=config,
                    database_path=str(self.server.database_path),
                    created_by_user_id=self._current_admin_user_id(),
                    audit_path=getattr(self.server, "audit_path", DEFAULT_AUDIT_PATH),
                )
            finally:
                conn.close()
        except (ValueError, sqlite3.IntegrityError, OSError, ImportError) as exc:
            self._send_form_error(HTTPStatus.BAD_REQUEST, f"Could not register source: {exc}")
            return

        flash = f"Registered “{name}”. Its fields are default-deny until you grant them."
        self._redirect("/admin/database?flash=" + urllib.parse.quote(flash))

    @staticmethod
    def _scope_from_form(fields: dict[str, str]) -> dict[str, str]:
        mode = (fields.get("record_scope_mode") or "none").strip()
        if mode == "none":
            return {"mode": "none"}
        scope: dict[str, str] = {"mode": mode}
        field = (fields.get("record_scope_field") or "").strip()
        if field:
            scope["field"] = field
        return scope

    def _registry_sources_section(self) -> str:
        """Render the runtime-registered sources with status + CRUD controls (v0.5.8).

        Metadata only — name, type, status, declared domains, and the credential
        *reference* (an env-var name, never a secret value). Empty string when no
        source has been registered, so the static-config view is unchanged."""
        conn = db.connect(self.server.database_path)
        try:
            rows = db.list_data_sources(conn)
        finally:
            conn.close()
        if not rows:
            return ""
        cards = "\n".join(self._registry_row(row) for row in rows)
        return (
            '<div class="ds-source">'
            "<h2>Runtime-registered sources</h2>"
            '<p class="subtitle">Added via the wizard and stored in the registry. '
            "Only <code>active</code> sources join the live catalog; credentials are "
            "stored as an env-var <em>reference</em>, never a secret value.</p>"
            f"{cards}</div>"
        )

    def _registry_row(self, row: Any) -> str:
        try:
            config = json.loads(row["config_json"])
            domains = ", ".join(d.get("name", "?") for d in config.get("domains", []))
        except (ValueError, TypeError):
            domains = "?"
        status = row["status"]
        secret = row["secret_ref"]
        secret_html = (
            f' &middot; cred env: <code>{html.escape(str(secret))}</code>' if secret else ""
        )
        # Enable<->disable toggle (draft/disabled -> active; active -> disabled).
        if status == "active":
            toggle = self._registry_form(row["name"], "status", "Disable", "disabled", "small")
        else:
            toggle = self._registry_form(row["name"], "status", "Enable", "active", "small")
        delete = self._registry_form(
            row["name"], "delete", "Delete", None, "small danger",
            confirm="Delete this source? Its domain leaves the catalog.",
        )
        return (
            '<div class="ds-domain"><div class="ds-domain-h">'
            f'<span class="ds-name">{html.escape(row["name"])}</span>'
            f'<span class="ds-scope">{html.escape(row["type"])} '
            f'&middot; <span class="pill {"active" if status == "active" else "disabled"}">'
            f'{html.escape(status)}</span> &middot; domains: {html.escape(domains)}{secret_html}</span>'
            f"</div><div>{toggle}{delete}</div></div>"
        )

    @staticmethod
    def _registry_form(
        name: str, action: str, label: str, status: str | None, css: str,
        *, confirm: str | None = None,
    ) -> str:
        status_field = (
            f'<input type="hidden" name="status" value="{html.escape(status)}">' if status else ""
        )
        onsubmit = f" onsubmit=\"return confirm('{confirm}')\"" if confirm else ""
        return (
            f'<form method="post" action="/admin/data-sources/{action}" '
            f'style="display:inline"{onsubmit}>'
            f'<input type="hidden" name="name" value="{html.escape(name)}">'
            f"{status_field}"
            f'<button type="submit" class="{css}">{label}</button></form>'
        )

    def _handle_data_source_set_status(self) -> None:
        fields = self._read_form_fields()
        name = fields.get("name", "").strip()
        status = fields.get("status", "").strip()
        conn = db.connect(self.server.database_path)
        try:
            try:
                changed = db.set_data_source_status(conn, name, status=status)
            except ValueError:
                self._send_form_error(HTTPStatus.BAD_REQUEST, "Invalid status.")
                return
        finally:
            conn.close()
        if not changed:
            self._send_form_error(HTTPStatus.BAD_REQUEST, "Unknown data source.")
            return
        write_audit_record(
            tool_name="admin.data_source.status",
            rationale="admin changed a data source status",
            source_client="admin",
            arguments={"name": name, "status": status},
            result_status="ok",
            error_code=None,
            audit_path=getattr(self.server, "audit_path", DEFAULT_AUDIT_PATH),
        )
        self._redirect("/admin/database?flash=" + urllib.parse.quote(f"{name} → {status}"))

    def _handle_data_source_delete(self) -> None:
        fields = self._read_form_fields()
        name = fields.get("name", "").strip()
        conn = db.connect(self.server.database_path)
        try:
            removed = db.delete_data_source(conn, name)
        finally:
            conn.close()
        if not removed:
            self._send_form_error(HTTPStatus.BAD_REQUEST, "Unknown data source.")
            return
        write_audit_record(
            tool_name="admin.data_source.delete",
            rationale="admin removed a data source",
            source_client="admin",
            arguments={"name": name},
            result_status="ok",
            error_code=None,
            audit_path=getattr(self.server, "audit_path", DEFAULT_AUDIT_PATH),
        )
        self._redirect(
            "/admin/database?flash=" + urllib.parse.quote(f"Deleted {name}; its domain left the catalog.")
        )

    def _current_admin_user_id(self) -> int | None:
        admin = self._current_admin()
        if admin is None:
            return None
        try:
            return int(admin["id"])
        except (KeyError, IndexError, TypeError, ValueError):
            return None
