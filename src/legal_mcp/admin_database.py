"""Data Sources page: the sources the gateway serves and their declared
domains and field names.

Metadata only. This page renders domain names, field names, identity markers,
and the record-scope mode — it never renders a row's actual field *value*. That
boundary is the page's security contract (v0.4.0 §C): an operator can see what is
connected and how it is scoped without the console becoming a data browser.

This replaces the pre-pivot ``/admin/database`` page (raw row table, aggregated
entities, project-alias CRUD, spreadsheet import), all of which contradicted the
post-pivot non-goals (the gateway does not own or browse business data).
"""
from __future__ import annotations

from http import HTTPStatus
import html
import urllib.parse

from legal_mcp import db


# Human labels for the known connector source types. Unknown types fall back to
# their raw connector name.
_SOURCE_LABELS = {
    "sqlite_demo": "Local SQLite (bundled demo)",
    "feishu_bitable": "Feishu Bitable (read-through)",
}

# Human labels for the record-scope modes declared on a domain.
_SCOPE_LABELS = {
    "none": "No row scope — domain/field grant is the only gate",
    "by_governed_code": "Row scope by governed code",
    "by_owner": "Row scope by owner — each user sees only their own rows",
}


class DatabaseViewMixin:
    """Data Sources page: connected sources, their domains and field names."""

    def _send_database_page(
        self, query: dict[str, list[str]], message: str | None = None
    ) -> None:
        flash = self._qs_str(query, "flash") or message
        setup = self.server.connector_setup

        catalog = tuple(setup.connector.catalog()) if setup is not None else ()
        domain_source = (
            setup.connector.domain_sources()
            if setup is not None and hasattr(setup.connector, "domain_sources")
            else {}
        )

        by_source: dict[str, list] = {}
        for domain in catalog:
            src = domain_source.get(domain.name, "unknown")
            by_source.setdefault(src, []).append(domain)

        conn = db.connect(self.server.database_path)
        try:
            disabled = db.disabled_data_sources(conn)
        finally:
            conn.close()

        holders = self._grant_holders_by_domain()

        field_count = sum(len(d.fields) for d in catalog)
        cards = "".join(
            f'<div class="card"><div class="n">{n}</div><div class="t">{html.escape(label)}</div></div>'
            for label, n in (
                ("Sources", len(by_source)),
                ("Domains", len(catalog)),
                ("Fields", field_count),
            )
        )

        if by_source:
            sections = "\n".join(
                self._source_section(
                    src, by_source[src], holders, src in disabled
                )
                for src in sorted(by_source)
            )
        else:
            sections = (
                '<p class="empty">No data sources configured. Start the admin '
                "server with <code>--connector</code> to point it at the same "
                "config the gateway uses.</p>"
            )

        body = f"""
        <h1>Data Sources</h1>
        <p class="subtitle">What the gateway serves: each source, the domains it
        serves, and per domain the declared field <em>names</em> and record scope.
        Field values are never shown here.</p>
        <p><a class="button" href="/admin/data-sources/new">+ Add data source</a></p>
        <div class="cards">{cards}</div>
        {sections}
        {self._registry_sources_section()}
        """
        self._send_html(
            HTTPStatus.OK,
            self._admin_layout(
                "Data Sources",
                "database",
                body,
                html.escape(flash) if flash else None,
            ),
        )

    def _declared_source_names(self) -> set[str]:
        """Names of the sources the connector config declares (C5 toggle target)."""
        setup = getattr(self.server, "connector_setup", None)
        connector = getattr(setup, "connector", None) if setup is not None else None
        if connector is None or not hasattr(connector, "domain_sources"):
            return set()
        return set(connector.domain_sources().values())

    def _handle_data_source_toggle(self, *, disable: bool) -> None:
        """Connect/disconnect a declared source from the console (v0.4.0 §C C5).

        Only a *declared* source can be toggled — an unknown name is rejected so
        the console can never invent a source that bypasses the reviewed config.
        Disconnecting drops the source's domains from the live catalog
        (fail-closed); reconnecting restores them.
        """
        fields = self._read_form_fields()
        source_name = fields.get("source_name", "").strip()
        if source_name not in self._declared_source_names():
            self._send_form_error(HTTPStatus.BAD_REQUEST, "Unknown data source.")
            return
        conn = db.connect(self.server.database_path)
        try:
            db.set_data_source_disabled(conn, source_name, disabled=disable)
        finally:
            conn.close()
        label = _SOURCE_LABELS.get(source_name, source_name)
        flash = (
            f"Disconnected {label} — its domains are no longer queryable."
            if disable
            else f"Reconnected {label}."
        )
        self._redirect("/admin/database?flash=" + urllib.parse.quote(flash))

    def _grant_holders_by_domain(self) -> dict[str, list[tuple[str, str]]]:
        """Map each data domain to who holds a grant on it (C3/C4 visibility).

        Returns ``{domain: [(kind, label), ...]}`` where kind is ``group`` or
        ``user``. Grant *holders* are governance metadata (group names, user
        emails) — not a data source's row values — so showing them keeps the
        page's metadata-only contract intact.
        """
        conn = db.connect(self.server.database_path)
        try:
            rows = conn.execute(
                """
                select pg.data_domain,
                  ug.name as group_name, u.email as user_email
                from permission_grants pg
                left join user_groups ug on ug.id = pg.group_id
                left join users u on u.id = pg.user_id
                where pg.allowed = 1
                """
            ).fetchall()
        finally:
            conn.close()
        holders: dict[str, set[tuple[str, str]]] = {}
        for row in rows:
            if row["group_name"] is not None:
                holder = ("group", str(row["group_name"]))
            elif row["user_email"] is not None:
                holder = ("user", str(row["user_email"]))
            else:
                continue
            holders.setdefault(str(row["data_domain"]), set()).add(holder)
        return {domain: sorted(items) for domain, items in holders.items()}

    def _source_section(
        self,
        source_name: str,
        domains: list,
        holders: dict[str, list[tuple[str, str]]],
        is_disabled: bool,
    ) -> str:
        label = _SOURCE_LABELS.get(source_name, source_name)
        n = len(domains)
        domain_blocks = "\n".join(
            self._domain_block(domain, holders.get(domain.name, []))
            for domain in sorted(domains, key=lambda d: d.name)
        )
        return (
            f'<div class="ds-source{" ds-disconnected" if is_disabled else ""}">'
            f'<div class="ds-source-h">'
            f"<h2>{html.escape(label)}</h2>"
            f"{self._source_toggle(source_name, is_disabled)}"
            "</div>"
            f'<p class="subtitle">{n} domain{"s" if n != 1 else ""}'
            + (
                ' &middot; <span class="pill disabled">Disconnected</span> '
                "&mdash; its domains are not queryable"
                if is_disabled
                else ' &middot; <span class="pill active">Connected</span>'
            )
            + "</p>"
            f"{domain_blocks}"
            "</div>"
        )

    def _source_toggle(self, source_name: str, is_disabled: bool) -> str:
        """A connect/disconnect button for one declared source (v0.4.0 §C C5).

        Disconnect only disables a *declared* source (config stays the reviewed
        git YAML); reconnect re-enables it. Adding a brand-new source needs a
        reviewed scaffold draft and lands with Phase 3 §D.
        """
        action = "connect" if is_disabled else "disconnect"
        verb = "Reconnect" if is_disabled else "Disconnect"
        kind = ' class="small"' if is_disabled else ' class="small danger"'
        confirm = (
            ""
            if is_disabled
            else (
                ' onsubmit="return confirm('
                "'Disconnect this source? Its domains stop being queryable until "
                "reconnected.')\""
            )
        )
        return (
            f'<form method="post" action="/admin/data-sources/{action}"{confirm}>'
            f'<input type="hidden" name="source_name" value="{html.escape(source_name)}">'
            f"<button type=\"submit\"{kind}>{verb}</button>"
            "</form>"
        )

    def _domain_block(self, domain, holders: list[tuple[str, str]]) -> str:
        scope = domain.record_scope
        scope_label = _SCOPE_LABELS.get(scope.mode, scope.mode)
        if scope.mode == "by_governed_code":
            scope_label += f" (<code>{html.escape(scope.field)}</code>)"
        elif scope.mode == "by_owner":
            scope_label += (
                f" (<code>{html.escape(scope.field)}</code>"
                f" = <code>{html.escape(scope.subject)}</code>)"
            )
        chips = "".join(
            '<span class="field-chip">'
            f"{html.escape(field.name)}"
            + ('<span class="tag">identity</span>' if field.is_identity else "")
            + "</span>"
            for field in domain.fields
        ) or '<span class="empty">No fields declared.</span>'
        if holders:
            holder_chips = "".join(
                '<span class="field-chip">'
                f"{html.escape(label)}<span class=\"tag\">{kind}</span>"
                "</span>"
                for kind, label in holders
            )
            holders_html = f'<div class="ds-holders">Granted to: {holder_chips}</div>'
        else:
            holders_html = (
                '<div class="ds-holders"><span class="empty">'
                "No grants reference this domain yet.</span></div>"
            )
        return (
            '<div class="ds-domain">'
            '<div class="ds-domain-h">'
            f'<span class="ds-name">{html.escape(domain.name)}</span>'
            f'<span class="ds-scope">{scope_label}</span>'
            "</div>"
            f'<div class="ds-fields">{chips}</div>'
            f"{holders_html}"
            "</div>"
        )
