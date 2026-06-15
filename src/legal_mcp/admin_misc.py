"""Audit and agent-settings views."""
from __future__ import annotations

from datetime import datetime
from http import HTTPStatus
import html
import json

from legal_mcp import db
from legal_mcp.admin_common import _AUDIT_PAGE_SIZE
from legal_mcp.admin_observability import observability_config
from legal_mcp.disclosure_audit import (
    count_audit_events,
    get_audit_event_detail,
    list_audit_events,
)


def _pretty_json(text: str | None) -> str:
    """Pretty-print stored JSON; fall back to raw text if it cannot parse
    (e.g. a truncated payload)."""
    if not text:
        return ""
    try:
        return json.dumps(
            json.loads(text), ensure_ascii=False, indent=2, sort_keys=True
        )
    except (ValueError, TypeError):
        return text


# Internal (self-hosted) software presets. Each maps to the single
# openai_compatible backend and only differs by the base URL it auto-fills.
# Keyless local endpoints work via a runtime placeholder key, so no API key is
# required for any of these. (key, label, default base URL)
_LOCAL_PRESETS = (
    ("ollama_local", "Ollama", "http://localhost:11434/v1"),
    ("lmstudio_local", "LM Studio", "http://localhost:1234/v1"),
    ("vllm_local", "vLLM", "http://localhost:8000/v1"),
    ("llamacpp_local", "llama.cpp server", "http://localhost:8080/v1"),
    ("localai_local", "LocalAI", "http://localhost:8080/v1"),
    ("jan_local", "Jan", "http://localhost:1337/v1"),
    ("custom_local", "Other (custom URL)", ""),
)
_LOCAL_PRESET_BASE_URLS = {key: url for key, _label, url in _LOCAL_PRESETS}
_DEFAULT_EXTERNAL_BASE_URL = "https://api.openai.com/v1"


def _looks_loopback(base_url: str | None) -> bool:
    if not base_url:
        return False
    return any(host in base_url for host in ("localhost", "127.0.0.1", "0.0.0.0", "::1"))


def _infer_ai_mode(provider: str, base_url: str | None, api_key: str | None) -> str:
    """Re-derive internal/external/none from stored fields for form preselection."""
    if provider == "none":
        return "none"
    # Internal = a self-hosted endpoint: a local/loopback URL, or any URL with no
    # API key (cloud needs a key, local does not).
    if base_url and (_looks_loopback(base_url) or not api_key):
        return "internal"
    return "external"


def _preset_for_base_url(base_url: str | None) -> str:
    for key, _label, url in _LOCAL_PRESETS:
        if url and url == base_url:
            return key
    return "custom_local"


class MiscViewMixin:
    """Audit and agent-settings views."""

    def _handle_update_agent_settings(self) -> None:
        fields = self._read_form_fields()
        mode = fields.get("ai_mode", "").strip()
        preset = fields.get("ai_preset", "").strip()
        ai_model = fields.get("ai_model", "").strip()
        ai_base_url = fields.get("ai_base_url", "").strip() or None
        ai_api_key = fields.get("ai_api_key", "").strip() or None

        if mode not in {"internal", "external", "none"}:
            self._send_form_error(HTTPStatus.BAD_REQUEST, "Invalid AI mode.")
            return
        if mode == "none":
            ai_provider = "none"
        else:
            # Both internal and external use the one openai_compatible backend; the
            # mode only changes defaults and what is required.
            ai_provider = "openai_compatible"
            if mode == "internal":
                if not ai_base_url:
                    ai_base_url = _LOCAL_PRESET_BASE_URLS.get(preset) or None
                if not ai_base_url:
                    self._send_form_error(
                        HTTPStatus.BAD_REQUEST,
                        "Base URL is required for a custom local endpoint.",
                    )
                    return
                # Internal: API key optional — a placeholder is used at runtime.
            else:  # external (cloud) — credentials are mandatory.
                if not ai_base_url:
                    ai_base_url = _DEFAULT_EXTERNAL_BASE_URL
                if not ai_api_key:
                    self._send_form_error(
                        HTTPStatus.BAD_REQUEST,
                        "API key is required for an external (cloud) endpoint.",
                    )
                    return
            if not ai_model:
                self._send_form_error(HTTPStatus.BAD_REQUEST, "AI model is required.")
                return
        conn = db.connect(self.server.database_path)
        try:
            conn.execute(
                """
                update agent_settings
                set ai_provider = ?,
                    ai_model = ?,
                    ai_base_url = ?,
                    ai_api_key = ?,
                    updated_at = datetime('now')
                where id = 1
                """,
                (ai_provider, ai_model, ai_base_url, ai_api_key),
            )
            conn.commit()
        finally:
            conn.close()
        self._redirect("/admin/agent-settings")

    def _send_agent_settings_page(self) -> None:
        conn = db.connect(self.server.database_path)
        try:
            row = conn.execute(
                """
                select ai_provider, ai_model, ai_base_url, ai_api_key, updated_at
                from agent_settings
                where id = 1
                """
            ).fetchone()
        finally:
            conn.close()
        settings = dict(row) if row is not None else {
            "ai_provider": "openai_compatible",
            "ai_model": "gpt-4.1-mini",
            "ai_base_url": "",
            "ai_api_key": "",
            "updated_at": "",
        }
        provider = settings["ai_provider"] or "openai_compatible"
        raw_base_url = settings["ai_base_url"] or ""
        raw_api_key = settings["ai_api_key"] or ""
        model = html.escape(settings["ai_model"] or "")
        base_url = html.escape(raw_base_url)
        api_key = html.escape(raw_api_key)
        updated_at = html.escape(settings["updated_at"] or "")

        mode = _infer_ai_mode(provider, raw_base_url or None, raw_api_key or None)
        current_preset = _preset_for_base_url(raw_base_url or None)
        preset_options = "\n".join(
            f'<option value="{key}" data-url="{html.escape(url)}"'
            f'{" selected" if key == current_preset else ""}>'
            f'{html.escape(label)}{f" — {html.escape(url)}" if url else " — custom URL"}'
            "</option>"
            for key, label, url in _LOCAL_PRESETS
        )

        def checked(value: str) -> str:
            return " checked" if value == mode else ""

        # Internal AI never pre-fills model or API key — the operator types the
        # model and leaves the key blank; only the base URL is filled per preset.
        # External keeps its saved values unchanged.
        model_value = "" if mode == "internal" else model
        key_value = "" if mode == "internal" else api_key

        body = f"""
        <h1>Agent Settings</h1>
        <p class="subtitle">Choose where the model that answers questions runs.
        This decides whether your question text and disclosed fields stay inside
        your network — see the commitment matrix at
        <code>Docs/strategy/commitment-matrix.md</code>.</p>

        <style>
        .aimode{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:14px 0 8px}}
        .aicard{{display:block;cursor:pointer;border:1.5px solid var(--border-strong);
          border-radius:var(--radius);padding:14px 15px;background:var(--surface);
          transition:border-color .15s,background .15s,box-shadow .15s}}
        .aicard .t{{display:block;font-weight:600;color:var(--ink);margin-bottom:4px}}
        .aicard .d{{display:block;font-size:12.5px;color:var(--muted);line-height:1.5}}
        .aicard input{{position:absolute;opacity:0;width:0;height:0}}
        .aicard.internal:has(input:checked){{border-color:#7CA982;background:#EDF3EC;
          box-shadow:0 0 0 3px rgba(52,101,56,.10)}}
        .aicard.external:has(input:checked){{border-color:#E3C36B;background:#FBF3DB;
          box-shadow:0 0 0 3px rgba(149,100,0,.10)}}
        .aicard.off:has(input:checked){{border-color:#9A9A93;background:var(--surface-2);
          box-shadow:0 0 0 3px rgba(0,0,0,.05)}}
        .secnote{{padding:12px 15px;border-radius:10px;font-size:13.5px;line-height:1.55;margin:6px 0 16px}}
        .secnote.safe{{background:#EDF3EC;color:#2C5530;border:1px solid #CFE0CF}}
        .secnote.warn{{background:#FBF3DB;color:#7A5200;border:1px solid #F0E2B4}}
        .secnote b{{font-weight:600}}
        #ai-form[data-mode="internal"] [data-sec="external"],
        #ai-form[data-mode="external"] [data-sec="internal"],
        #ai-form[data-mode="none"] [data-sec]{{display:none}}
        #ai-form[data-mode="none"] .credentials{{display:none}}
        </style>

        <form method="post" action="/admin/agent-settings/update" id="ai-form" data-mode="{mode}"
              data-saved-model="{model}" data-saved-key="{api_key}" data-saved-base="{base_url}">
          <div class="aimode">
            <label class="aicard internal">
              <input type="radio" name="ai_mode" value="internal"{checked("internal")}>
              <span class="t">🟢 Internal AI · 内部（自托管）</span>
              <span class="d">Model runs inside your network. Question text and
              disclosed fields never leave it.</span>
            </label>
            <label class="aicard external">
              <input type="radio" name="ai_mode" value="external"{checked("external")}>
              <span class="t">🟠 External AI · 外部（云端）</span>
              <span class="d">Question text and disclosed fields are sent to an
              outside model and may be cached or logged there.</span>
            </label>
            <label class="aicard off">
              <input type="radio" name="ai_mode" value="none"{checked("none")}>
              <span class="t">⏻ Disabled · 关闭</span>
              <span class="d">Turn off model-driven planning for agent queries.</span>
            </label>
          </div>

          <div class="secnote safe" data-sec="internal">
            <b>Internal AI keeps the whole exchange on-prem.</b> 问答全程不出内网：
            问题文本与披露字段都只到达你内网里的模型。原始数据本就不出源。
          </div>
          <div class="secnote warn" data-sec="external">
            <b>External AI sends your question and the disclosed fields off-network.</b>
            外部模型可能缓存/记录这些内容。授权裁剪仍然生效（只发送已授权字段、原始数据不出源），
            但"问答不出内网"在外部模式下<b>不成立</b>。
          </div>

          <div data-sec="internal">
            <label>Local software preset
              <select name="ai_preset" id="ai-preset">{preset_options}</select>
            </label>
          </div>

          <div class="credentials">
            <label>AI Model
              <input type="text" name="ai_model" id="ai-model" value="{model_value}" placeholder="e.g. qwen2.5 or gpt-4.1-mini">
            </label>
            <label>AI Base URL
              <input type="url" name="ai_base_url" id="ai-base-url" value="{base_url}" placeholder="auto-filled by preset / cloud endpoint">
            </label>
            <label>AI API Key
              <input type="password" name="ai_api_key" id="ai-api-key" value="{key_value}" placeholder="required for external · optional for internal">
            </label>
          </div>

          <button type="submit">Save Agent Settings</button>
        </form>
        <p class="subtitle">Updated: {updated_at or "never"}</p>

        <script>
        (function() {{
          var form = document.getElementById('ai-form');
          var preset = document.getElementById('ai-preset');
          var baseUrl = document.getElementById('ai-base-url');
          var modelInput = document.getElementById('ai-model');
          var keyInput = document.getElementById('ai-api-key');
          function selectedMode() {{
            var r = form.querySelector('input[name="ai_mode"]:checked');
            return r ? r.value : 'external';
          }}
          function syncMode() {{ form.setAttribute('data-mode', selectedMode()); }}
          form.querySelectorAll('input[name="ai_mode"]').forEach(function(r) {{
            r.addEventListener('change', function() {{
              syncMode();
              var mode = selectedMode();
              if (mode === 'internal') {{
                // Internal never pre-fills model or key; only the base URL is set.
                modelInput.value = '';
                keyInput.value = '';
                applyPreset();
              }} else if (mode === 'external') {{
                // Restore the saved cloud values (external UI unchanged).
                modelInput.value = form.getAttribute('data-saved-model') || '';
                keyInput.value = form.getAttribute('data-saved-key') || '';
                baseUrl.value = form.getAttribute('data-saved-base') || '';
              }}
            }});
          }});
          function applyPreset() {{
            if (!preset) return;
            var opt = preset.options[preset.selectedIndex];
            // 'Other' has an empty data-url, so this clears it — no default URL.
            baseUrl.value = opt ? (opt.getAttribute('data-url') || '') : '';
          }}
          if (preset) preset.addEventListener('change', applyPreset);
          syncMode();
        }})();
        </script>
        """
        self._send_html(
            HTTPStatus.OK,
            self._admin_layout("Agent Settings", "agent-settings", body),
        )

    def _send_audit_page(self, query: dict[str, list[str]]) -> None:
        conn = db.connect(self.server.database_path)
        try:
            total = count_audit_events(conn)
            total_pages = max(1, (total + _AUDIT_PAGE_SIZE - 1) // _AUDIT_PAGE_SIZE)
            page = max(1, min(self._qs_int(query, "page", 1), total_pages))
            rows = list_audit_events(
                conn,
                limit=_AUDIT_PAGE_SIZE,
                offset=(page - 1) * _AUDIT_PAGE_SIZE,
            )
        finally:
            conn.close()

        body_rows = "\n".join(
            "<tr>"
            f"<td>{html.escape(str(row['id']))}</td>"
            f"<td>{html.escape(row['timestamp'])}</td>"
            f"<td>{html.escape(str(row['user_id'] or ''))}</td>"
            f"<td>{html.escape(row['source_client'] or '')}</td>"
            f"<td>{html.escape(row['tool_name'])}</td>"
            f"<td>{html.escape(row['rationale'] or '')}</td>"
            f"<td>{html.escape(row['result_status'])}</td>"
            f"<td>{html.escape(row['error_code'] or '')}</td>"
            f"<td>{html.escape(str(row['response_record_count']))}</td>"
            f"<td><a class=\"btn-small\" href=\"/admin/audit/{int(row['id'])}\">View</a></td>"
            "</tr>"
            for row in rows
        )
        pager = self._pager(
            "/admin/audit",
            {},
            "page",
            page,
            total,
            "events",
            page_size=_AUDIT_PAGE_SIZE,
        )
        obs_link = ""
        if observability_config().enabled:
            obs_link = (
                '<p><a class="btn-small" href="/admin/observability">'
                "Open Observability (Langfuse) →</a></p>"
            )
        body = f"""
        <h1>Audit Events</h1>
        <p class="subtitle">All disclosure and tool-call events, {_AUDIT_PAGE_SIZE} per page.
        <strong>Query / Reason</strong> is what the caller asked or why;
        <strong>Records returned</strong> is how many data rows that call returned.</p>
        {obs_link}
        <table id="events">
          <thead><tr><th>ID</th><th>Timestamp</th><th>User</th><th>Client</th><th>Tool</th><th>Query / Reason</th><th>Status</th><th>Error</th><th title="Number of data rows this call returned">Records returned</th><th>Detail</th></tr></thead>
          <tbody>{body_rows}</tbody>
        </table>
        {pager}
        """
        self._send_html(
            HTTPStatus.OK,
            self._admin_layout("Admin Audit", "audit", body),
        )

    def _send_audit_detail_page(self, event_id: int) -> None:
        conn = db.connect(self.server.database_path)
        try:
            event = conn.execute(
                "select * from audit_events where id = ?", (event_id,)
            ).fetchone()
            detail = (
                get_audit_event_detail(conn, event_id)
                if event is not None
                else None
            )
        finally:
            conn.close()
        if event is None:
            self._send_html(
                HTTPStatus.NOT_FOUND,
                self._page("Not Found", "<p>Unknown audit event.</p>"),
            )
            return

        def field(label: str, value: object) -> str:
            shown = str(value) if value not in (None, "") else "—"
            return f"<tr><th>{html.escape(label)}</th><td>{html.escape(shown)}</td></tr>"

        meta = (
            '<table class="kv">'
            + field("ID", event["id"])
            + field("Timestamp", event["timestamp"])
            + field("Tool", event["tool_name"])
            + field("User", event["user_id"])
            + field("Client", event["source_client"])
            + field("Status", event["result_status"])
            + field("Error", event["error_code"])
            + field("Query / Reason", event["rationale"])
            + field("Records returned", event["response_record_count"])
            + "</table>"
        )

        if detail is None:
            payload_html = (
                '<p class="subtitle">Full request/response was not captured for '
                "this event (it predates detail logging).</p>"
            )
        else:
            trunc = (
                '<div class="flash flash-error">Payload truncated to the storage '
                "cap; showing the stored portion.</div>"
                if detail["truncated"]
                else ""
            )
            payload_html = (
                trunc
                + "<h2>Question / Input</h2>"
                + f'<pre class="detail-json">{html.escape(_pretty_json(detail["arguments_json"]))}</pre>'
                + "<h2>Answer / Server response</h2>"
                + f'<pre class="detail-json">{html.escape(_pretty_json(detail["response_json"]))}</pre>'
            )

        body = (
            "<h1>Audit Event Detail</h1>"
            '<p><a class="btn-small" href="/admin/audit">← Back to audit</a></p>'
            + meta
            + payload_html
        )
        self._send_html(
            HTTPStatus.OK,
            self._admin_layout("Audit Detail", "audit", body),
        )

    def _send_observability_page(self) -> None:
        config = observability_config()
        if not config.enabled:
            body = """
            <h1>Observability</h1>
            <p class="subtitle">Langfuse is not configured.</p>
            <p>Set <code>LANGFUSE_BASE_URL</code>, <code>LANGFUSE_INIT_USER_EMAIL</code>,
            and <code>LANGFUSE_INIT_USER_PASSWORD</code> to enable the embedded
            tracing dashboard.</p>
            """
            self._send_html(
                HTTPStatus.OK,
                self._admin_layout("Observability", "audit", body),
            )
            return

        # Build the proxy origin from the host the admin used, swapping the port
        # so the iframe target is reachable by the same browser.
        host_header = self.headers.get("Host", "")
        hostname = host_header.rsplit(":", 1)[0] if host_header else "127.0.0.1"
        iframe_src = f"http://{hostname}:{config.port}/"
        body = f"""
        <h1>Observability</h1>
        <p class="subtitle">Embedded Langfuse tracing dashboard (no separate login).</p>
        <iframe class="observability-frame" src="{html.escape(iframe_src)}"
                title="Langfuse"></iframe>
        """
        self._send_html(
            HTTPStatus.OK,
            self._admin_layout("Observability", "audit", body),
        )
