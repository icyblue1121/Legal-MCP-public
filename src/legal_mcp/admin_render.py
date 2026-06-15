"""Shared rendering, pagination, and form-parsing helpers."""
from __future__ import annotations

from http import HTTPStatus
import html
import sqlite3
import urllib.parse

from legal_mcp.admin_common import _NAV_ITEMS, _PAGE_SIZE, _STYLES


class RenderMixin:
    """Shared rendering, pagination, and form-parsing helpers."""

    @staticmethod
    def _qs_int(query: dict[str, list[str]], key: str, default: int = 1) -> int:
        try:
            return max(1, int(query.get(key, [str(default)])[0]))
        except (ValueError, TypeError):
            return default

    @staticmethod
    def _qs_str(query: dict[str, list[str]], key: str, default: str = "") -> str:
        return (query.get(key, [default])[0] or "").strip()

    def _redirect_manage(self, tab: str, *, flash: str | None = None) -> None:
        url = f"/admin/users/manage?tab={urllib.parse.quote(tab)}"
        if flash:
            url += f"&flash={urllib.parse.quote(flash)}"
        url += f"#{tab}"
        self._redirect(url)

    def _redirect_return(self, fields: dict[str, str], default: str) -> None:
        target = (fields.get("_return") or "").strip()
        if target.startswith("/admin/") and "\n" not in target and "\r" not in target:
            self._redirect(target)
            return
        self._redirect(default)

    def _pager(
        self,
        base_path: str,
        base_params: dict[str, str],
        page_key: str,
        page: int,
        total: int,
        anchor: str,
        page_size: int = _PAGE_SIZE,
    ) -> str:
        total_pages = max(1, (total + page_size - 1) // page_size)
        if total_pages <= 1:
            return f'<div class="pager"><span class="info">{total} total</span></div>'

        def link(target_page: int, label: str, current: bool = False) -> str:
            if current:
                return f'<span class="cur">{html.escape(label)}</span>'
            params = dict(base_params)
            params[page_key] = str(target_page)
            qs = urllib.parse.urlencode({k: v for k, v in params.items() if v != ""})
            return f'<a href="{base_path}?{qs}#{anchor}">{html.escape(label)}</a>'

        parts: list[str] = []
        if page > 1:
            parts.append(link(page - 1, "‹ Prev"))
        for p in range(1, total_pages + 1):
            parts.append(link(p, str(p), current=(p == page)))
        if page < total_pages:
            parts.append(link(page + 1, "Next ›"))
        parts.append(f'<span class="info">{total} total</span>')
        return '<div class="pager">' + "".join(parts) + "</div>"

    @staticmethod
    def _status_pill(status: str) -> str:
        cls = html.escape(status)
        return f'<span class="pill {cls}">{cls}</span>'

    @staticmethod
    def _sort(
        query: dict[str, list[str]],
        allowed: dict[str, str],
        default_col: str,
        default_dir: str = "asc",
    ) -> tuple[str, str, str]:
        """Resolve a whitelisted (column, direction, ORDER BY clause)."""
        col = (query.get("sort", [default_col])[0] or "").strip()
        if col not in allowed:
            col = default_col
        direction = (query.get("dir", [default_dir])[0] or "").strip().lower()
        if direction not in ("asc", "desc"):
            direction = default_dir
        return col, direction, f"{allowed[col]} {direction}"

    def _sortable_head(
        self,
        base_path: str,
        base_params: dict[str, str],
        page_key: str,
        anchor: str,
        sort_col: str,
        sort_dir: str,
        columns: list[tuple[str | None, str]],
    ) -> str:
        """Render a <thead> row; (key, label) with key=None = not sortable."""
        cells = []
        for key, label in columns:
            if key is None:
                cells.append(f"<th>{html.escape(label)}</th>")
                continue
            next_dir = "desc" if (key == sort_col and sort_dir == "asc") else "asc"
            params = dict(base_params)
            params["sort"] = key
            params["dir"] = next_dir
            params[page_key] = "1"
            qs = urllib.parse.urlencode({k: v for k, v in params.items() if v != ""})
            arrow = (
                " ▲"
                if (key == sort_col and sort_dir == "asc")
                else (" ▼" if key == sort_col else "")
            )
            cells.append(
                f'<th><a href="{base_path}?{qs}#{anchor}" '
                'style="color:inherit;text-decoration:none">'
                f"{html.escape(label)}{arrow}</a></th>"
            )
        return "<thead><tr>" + "".join(cells) + "</tr></thead>"

    def _options(
        self,
        rows: list[sqlite3.Row],
        value_key: str,
        label_fn,
        selected: set[int] | None = None,
    ) -> str:
        out = []
        for row in rows:
            value = str(row[value_key])
            sel = " selected" if selected and int(row[value_key]) in selected else ""
            out.append(
                f'<option value="{html.escape(value)}"{sel}>'
                f"{html.escape(label_fn(row))}</option>"
            )
        return "\n".join(out)

    def _search_form(self, tab: str, q: str, anchor: str) -> str:
        return (
            '<div class="toolbar" id="' + anchor + '">'
            '<form class="search" method="get" action="/admin/users/manage">'
            f'<input type="hidden" name="tab" value="{html.escape(tab)}">'
            f'<input type="search" name="q" value="{html.escape(q)}" '
            'placeholder="Search…">'
            '<button type="submit" class="small">Search</button>'
            + (
                f'<a class="btn-small" href="/admin/users/manage?tab={tab}#{anchor}">Clear</a>'
                if q
                else ""
            )
            + "</form><span class=\"spacer\"></span></div>"
        )

    def _checkrow(
        self,
        *,
        name: str,
        value: str,
        title: str,
        sub: str,
        perms: list[str],
        checked: bool = False,
    ) -> str:
        perms_html = ""
        if perms:
            shown = ", ".join(perms[:4])
            if len(perms) > 4:
                shown += f" +{len(perms) - 4} more"
            perms_html = f'<span class="perms">{html.escape(shown)}</span>'
        checked_attr = " checked" if checked else ""
        return (
            '<label class="checkrow">'
            f'<input type="checkbox" name="{html.escape(name)}" value="{html.escape(value)}"{checked_attr}>'
            '<span class="meta">'
            f'<span class="title">{html.escape(title)}</span>'
            f'<span class="sub">{html.escape(sub)}</span>'
            f"{perms_html}"
            "</span>"
            "</label>"
        )

    def _checklist_section(self, cid: str, items_html: str) -> str:
        """Wrap a checklist with a filter box and select-all/invert/count controls."""
        return (
            f'<div class="checklist-section" id="{cid}">'
            '<div class="toolbar">'
            '<input type="search" class="cl-filter" placeholder="Filter…" '
            'style="min-width:200px">'
            '<button type="button" class="small cl-all">Select all visible</button>'
            '<button type="button" class="small cl-invert">Invert visible</button>'
            '<button type="button" class="small cl-none">Clear visible</button>'
            '<span class="spacer"></span>'
            '<span class="count cl-count">0 selected</span>'
            "</div>"
            f'<div class="checklist">{items_html}</div>'
            "</div>"
        )

    def _parse_id_list(self, values: list[str]) -> list[int]:
        result = []
        for value in values:
            value = value.strip()
            if value:
                result.append(int(value))
        return result

    def _read_form_fields(self) -> dict[str, str]:
        return {key: values[0] for key, values in self._read_form_multi().items() if values}

    def _read_form_multi(self) -> dict[str, list[str]]:
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            content_length = 0
        body = self.rfile.read(content_length).decode("utf-8")
        return urllib.parse.parse_qs(body, keep_blank_values=True)

    def _parse_required_int(
        self,
        fields: dict[str, str],
        name: str,
        label: str,
    ) -> int:
        value = fields.get(name, "").strip()
        if not value:
            raise ValueError(f"{label} is required.")
        try:
            return int(value)
        except ValueError:
            raise ValueError(f"{label} must be a valid ID.") from None

    def _redirect(
        self,
        location: str,
        headers: list[tuple[str, str]] | None = None,
    ) -> None:
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        for key, value in headers or []:
            self.send_header(key, value)
        self.end_headers()

    def _send_html(self, status: HTTPStatus, body: str) -> None:
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_form_error(self, status: HTTPStatus, message: str) -> None:
        body = """
        <h1>Form Error</h1>
        <p class="subtitle">Please go back and correct the highlighted field.</p>
        """
        self._send_html(
            status,
            self._admin_layout(
                "Form Error", "users", body, html.escape(message), message_kind="error"
            ),
        )

    def _page(self, title: str, body: str) -> str:
        escaped_title = html.escape(title)
        return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escaped_title}</title>
  <style>{_STYLES}</style>
</head>
<body>
  {body}
</body>
</html>"""

    def _nav(self, active: str) -> str:
        links = "".join(
            '<a href="{path}"{cls}>{label}</a>'.format(
                path=path,
                cls=' class="active"' if key == active else "",
                label=html.escape(label),
            )
            for path, label, key in _NAV_ITEMS
        )
        mode_label = (
            "Local Deployment" if self.server.is_local_mode else "Team Deployment"
        )
        return (
            '<nav class="topnav">'
            '<span class="brand">Legal-MCP</span>'
            f"{links}"
            '<span class="spacer"></span>'
            f'<a class="mode" href="/admin/deployment-mode" title="Change deployment mode">{mode_label} ⚙</a>'
            "</nav>"
        )

    def _admin_layout(
        self,
        title: str,
        active: str,
        body: str,
        message: str | None = None,
        *,
        message_kind: str = "info",
    ) -> str:
        flash = ""
        if message is not None:
            kind_class = "" if message_kind == "info" else f" flash-{message_kind}"
            flash = f'<div class="flash{kind_class}">{message}</div>'
        inner = f'{self._nav(active)}<main class="container">{flash}{body}</main>'
        return self._page(title, inner)
