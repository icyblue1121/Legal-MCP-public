"""Shared constants, styles, and pure helpers for the admin web UI."""
from __future__ import annotations

from datetime import datetime, timezone
import html

from legal_mcp.identity import ROLE_ADMIN, ROLE_AUDITOR, ROLE_BUSINESS, ROLE_LEGAL


_SESSION_COOKIE = "lmcp_admin"

_SESSION_HOURS = 8

_AUDIT_EVENT_LIMIT = 100
_AUDIT_PAGE_SIZE = 100

_ALLOWED_ROLES = {ROLE_ADMIN, ROLE_AUDITOR, ROLE_BUSINESS, ROLE_LEGAL}

MODE_LOCAL = "local"

MODE_TEAM = "team"

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}

_LOCAL_OWNER_EMAIL = "local-owner@localhost"

def _is_loopback_host(host: str) -> bool:
    return host.strip().lower() in _LOOPBACK_HOSTS


def read_deployment_mode(conn) -> str | None:
    """Return the persisted deployment mode, or None if not yet seeded."""
    row = conn.execute(
        "select mode from deployment_settings where id = 1"
    ).fetchone()
    if row is None:
        return None
    mode = row["mode"]
    return mode if mode in (MODE_LOCAL, MODE_TEAM) else None


def write_deployment_mode(conn, mode: str) -> None:
    """Persist the deployment mode (single-row upsert)."""
    if mode not in (MODE_LOCAL, MODE_TEAM):
        raise ValueError(f"Unknown deployment mode: {mode!r}")
    conn.execute(
        """
        insert into deployment_settings (id, mode) values (1, ?)
        on conflict(id) do update set mode = excluded.mode
        """,
        (mode,),
    )
    conn.commit()

_NAV_ITEMS = (
    ("/admin/users", "Users", "users"),
    ("/admin/database", "Data Sources", "database"),
    ("/admin/audit", "Audit", "audit"),
    ("/admin/agent-settings", "Agent Settings", "agent-settings"),
)

_PAGE_SIZE = 10

_MANAGE_TABS = (
    ("users", "Users", "users"),
    ("groups", "Groups & Members", "groups"),
    ("permissions", "Permissions", "permissions"),
    ("keys", "API Keys", "keys"),
)

_STYLES = """
:root{
  --bg:#F7F6F3; --surface:#FFFFFF; --surface-2:#FBFBFA;
  --border:#EAEAEA; --border-strong:#E0DFDB;
  --text:#2F3437; --muted:#787774; --ink:#1A1A1A; --radius:12px;
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{
  margin:0; background:var(--bg); color:var(--text);
  font-family:'SF Pro Display','Geist Sans','Helvetica Neue',Arial,sans-serif;
  font-size:15px; line-height:1.6; padding:0 0 96px;
  -webkit-font-smoothing:antialiased;
}
.container{max-width:1080px; margin:0 auto; padding:0 24px}
nav.topnav{
  position:sticky; top:0; z-index:10;
  background:rgba(247,246,243,.86); backdrop-filter:saturate(180%) blur(10px);
  border-bottom:1px solid var(--border);
  display:flex; align-items:center; gap:4px;
  padding:13px 24px; margin-bottom:40px;
}
nav.topnav .brand{
  font-weight:600; letter-spacing:-.02em; margin-right:18px; color:var(--ink);
  font-size:15px;
}
nav.topnav a{
  text-decoration:none; color:var(--muted); font-size:14px;
  padding:6px 12px; border-radius:8px; transition:color .15s, background .15s;
}
nav.topnav a:hover{color:var(--ink); background:rgba(0,0,0,.04)}
nav.topnav a.active{
  color:var(--ink); background:var(--surface); border:1px solid var(--border);
}
nav.topnav .spacer{flex:1}
nav.topnav .mode{
  font-size:11px; text-transform:uppercase; letter-spacing:.06em; font-weight:600;
  color:#346538; background:#EDF3EC; padding:4px 11px; border-radius:9999px;
}
h1{
  font-size:28px; letter-spacing:-.03em; line-height:1.15;
  margin:0 0 6px; color:var(--ink); font-weight:600;
}
.subtitle{color:var(--muted); margin:0 0 4px; font-size:15px}
h2{
  font-size:12px; text-transform:uppercase; letter-spacing:.07em;
  color:var(--muted); margin:44px 0 12px; font-weight:600;
}
form{
  background:var(--surface); border:1px solid var(--border); border-radius:var(--radius);
  padding:20px; display:flex; flex-wrap:wrap; gap:16px; align-items:flex-end;
}
label{
  display:flex; flex-direction:column; gap:6px;
  font-size:13px; color:var(--muted); font-weight:500;
}
input,select{
  font:inherit; font-size:14px; color:var(--text);
  background:#fff; border:1px solid var(--border-strong); border-radius:8px;
  padding:9px 11px; min-width:210px; transition:border-color .15s, box-shadow .15s;
}
input:focus,select:focus{
  outline:none; border-color:#9A9A93; box-shadow:0 0 0 3px rgba(0,0,0,.04);
}
button{
  font:inherit; font-size:14px; font-weight:500;
  background:var(--ink); color:#fff; border:0; border-radius:8px;
  padding:10px 18px; cursor:pointer; transition:transform .08s, background .15s;
}
button:hover{background:#333}
button:active{transform:scale(.985)}
table{
  width:100%; border-collapse:collapse; background:var(--surface);
  border:1px solid var(--border); border-radius:var(--radius); overflow:hidden;
  margin-top:12px; font-size:14px;
}
thead th{
  text-align:left; font-size:11px; text-transform:uppercase; letter-spacing:.05em;
  color:var(--muted); font-weight:600; padding:11px 16px; background:var(--surface-2);
  border-bottom:1px solid var(--border); white-space:nowrap;
}
tbody td{padding:11px 16px; border-bottom:1px solid var(--border); color:var(--text)}
tbody tr:last-child td{border-bottom:0}
tbody tr:hover{background:var(--surface-2)}
.flash{
  background:#FBF3DB; color:#956400; border:1px solid #F2E4B8;
  padding:12px 16px; border-radius:8px; font-size:14px; margin:0 0 20px;
}
.flash code{font-family:'Geist Mono','SF Mono','JetBrains Mono',monospace; font-size:13px}
.flash-error{background:#FDEBEC; color:#9F2F2D; border-color:#F3CFD0}
.login-wrap{min-height:100dvh; display:flex; align-items:center; justify-content:center; padding:24px}
.login-card{width:100%; max-width:380px}
.login-card .brand{
  font-weight:600; letter-spacing:-.02em; color:var(--ink); font-size:18px;
  margin:0 0 4px;
}
.login-card form{flex-direction:column; align-items:stretch; gap:14px; margin-top:18px}
.login-card label{font-size:13px}
.login-card input{width:100%; min-width:0}
.login-card button{width:100%; padding:12px}
a.back{
  display:inline-block; color:var(--muted); text-decoration:none; font-size:13px;
  margin-bottom:14px;
}
a.back:hover{color:var(--ink)}
.actionbar{display:flex; align-items:center; gap:12px; margin:6px 0 8px}
.actionbar .spacer{flex:1}
a.btn{
  display:inline-flex; align-items:center; text-decoration:none;
  font-size:14px; font-weight:500; padding:10px 18px; border-radius:8px;
  background:var(--ink); color:#fff; transition:background .15s;
}
a.btn:hover{background:#333}
a.btn-secondary{background:transparent; color:var(--muted); border:1px solid var(--border-strong)}
a.btn-secondary:hover{background:rgba(0,0,0,.04); color:var(--ink)}
/* Guided provisioning form: structured panels instead of inline row. */
form.provision{display:block; padding:0; background:transparent; border:0}
form.provision .panel{
  background:var(--surface); border:1px solid var(--border); border-radius:var(--radius);
  padding:22px 22px 24px; margin-bottom:20px;
}
form.provision .panel h2{margin:0 0 4px}
form.provision .panel .hint{margin:0 0 16px; font-size:13px; color:var(--muted)}
form.provision .grid{display:flex; flex-wrap:wrap; gap:16px}
form.provision .grid label{flex:1; min-width:220px}
.check{
  flex-direction:row !important; align-items:center; gap:9px;
  font-size:14px; color:var(--text); font-weight:500; cursor:pointer;
}
.check input{min-width:0; width:16px; height:16px; accent-color:#1A1A1A}
.checklist{
  display:grid; grid-template-columns:repeat(auto-fill,minmax(280px,1fr)); gap:8px;
  max-height:320px; overflow:auto; padding:2px;
}
.checkrow{
  display:flex; align-items:flex-start; gap:10px;
  border:1px solid var(--border); border-radius:10px; padding:11px 13px;
  cursor:pointer; transition:border-color .15s, background .15s;
}
.checkrow:hover{border-color:var(--border-strong); background:var(--surface-2)}
.checkrow input{margin-top:2px; min-width:0; width:16px; height:16px; accent-color:#1A1A1A}
.checkrow .meta{display:flex; flex-direction:column; gap:2px; min-width:0}
.checkrow .meta .title{font-weight:600; font-size:14px; color:var(--ink)}
.checkrow .meta .sub{font-size:12px; color:var(--muted); line-height:1.45}
.checkrow .meta .perms{
  font-family:'Geist Mono','SF Mono','JetBrains Mono',monospace;
  font-size:11px; color:#346538;
}
.empty{font-size:13px; color:var(--muted); padding:8px 2px}
.keypanel{
  background:var(--surface); border:1px solid var(--border); border-radius:var(--radius);
  padding:20px 22px; margin:8px 0 24px;
}
.keypanel .label{font-size:12px; text-transform:uppercase; letter-spacing:.06em; color:var(--muted); margin:0 0 6px}
.keypanel code{
  display:block; font-family:'Geist Mono','SF Mono','JetBrains Mono',monospace;
  font-size:14px; color:var(--ink); background:var(--surface-2);
  border:1px solid var(--border); border-radius:8px; padding:12px 14px;
  word-break:break-all; user-select:all;
}
.keypanel .warn{font-size:13px; color:#9F2F2D; margin:12px 0 0}
.summary-list{list-style:none; padding:0; margin:0 0 8px}
.summary-list li{
  padding:10px 0; border-bottom:1px solid var(--border);
  font-size:14px; color:var(--text); display:flex; gap:10px;
}
.summary-list li:last-child{border-bottom:0}
.summary-list .k{color:var(--muted); min-width:140px}
/* Sub-tabs inside the manage page. */
.tabs{display:flex; gap:4px; border-bottom:1px solid var(--border); margin:18px 0 8px}
.tabs a{
  text-decoration:none; color:var(--muted); font-size:14px; font-weight:500;
  padding:9px 14px; border-radius:8px 8px 0 0; border:1px solid transparent;
  border-bottom:0; margin-bottom:-1px; transition:color .15s, background .15s;
}
.tabs a:hover{color:var(--ink); background:rgba(0,0,0,.03)}
.tabs a.active{
  color:var(--ink); background:var(--surface);
  border-color:var(--border); border-bottom:1px solid var(--surface);
}
/* Search + toolbar row above a table. */
.toolbar{display:flex; align-items:center; gap:10px; margin:14px 0 4px}
.toolbar form.search{
  background:transparent; border:0; padding:0; display:flex; gap:8px; align-items:center;
}
.toolbar input[type=search]{min-width:240px}
.toolbar .count{font-size:13px; color:var(--muted)}
.toolbar .spacer{flex:1}
/* Pager. */
.pager{display:flex; align-items:center; gap:8px; margin:12px 0 4px; font-size:13px}
.pager a,.pager span.cur{
  text-decoration:none; padding:6px 11px; border-radius:8px;
  border:1px solid var(--border-strong); color:var(--muted);
}
.pager a:hover{color:var(--ink); background:rgba(0,0,0,.04)}
.pager span.cur{background:var(--ink); color:#fff; border-color:var(--ink)}
.pager .info{border:0; color:var(--muted); padding:6px 4px}
/* Inline row-action forms in tables. */
td .rowacts{display:flex; flex-wrap:wrap; gap:6px; align-items:center}
td form.inline{background:transparent; border:0; padding:0; display:inline-flex; gap:6px; align-items:center}
button.small,a.btn-small{
  font-size:12px; font-weight:500; padding:5px 10px; border-radius:7px;
  text-decoration:none; cursor:pointer;
}
a.btn-small{background:transparent; color:var(--muted); border:1px solid var(--border-strong)}
a.btn-small:hover{background:rgba(0,0,0,.04); color:var(--ink)}
.observability-frame{width:100%; height:78vh; border:1px solid var(--border-strong);
  border-radius:10px; background:#fff}
table.kv{width:auto; margin:0 0 20px}
table.kv th{text-align:left; padding:4px 16px 4px 0; white-space:nowrap; color:var(--muted); font-weight:500}
.detail-json{max-height:46vh; overflow:auto; background:#FAFAF8;
  border:1px solid var(--border-strong); border-radius:8px; padding:12px;
  font-size:12px; line-height:1.5; white-space:pre-wrap; word-break:break-word}
button.small{background:transparent; color:var(--muted); border:1px solid var(--border-strong)}
button.small:hover{background:rgba(0,0,0,.04); color:var(--ink)}
button.small.danger{color:#9F2F2D; border-color:#F3CFD0}
button.small.danger:hover{background:#FDEBEC}
.pill{font-size:11px; padding:2px 9px; border-radius:9999px; font-weight:600;
  text-transform:uppercase; letter-spacing:.04em}
.pill.active{background:#EDF3EC; color:#346538}
.pill.disabled,.pill.revoked{background:#FDEBEC; color:#9F2F2D}
/* Data Sources page: one block per domain, field-name chips. Metadata only. */
.ds-source{margin:22px 0}
.ds-source-h{display:flex; align-items:center; justify-content:space-between; gap:12px}
.ds-source-h h2{margin:0}
.ds-source-h form{margin:0}
.ds-disconnected{opacity:.62}
.ds-domain{
  background:var(--surface); border:1px solid var(--border); border-radius:var(--radius);
  padding:14px 16px; margin:10px 0;
}
.ds-domain-h{display:flex; align-items:baseline; gap:12px; flex-wrap:wrap; margin-bottom:10px}
.ds-name{font-weight:600; color:var(--ink); font-size:15px}
.ds-scope{font-size:12px; color:var(--muted)}
.ds-scope code{font-family:'Geist Mono','SF Mono','JetBrains Mono',monospace; font-size:11px}
.ds-fields{display:flex; flex-wrap:wrap; gap:6px}
.field-chip{
  display:inline-flex; align-items:center; gap:6px; font-size:13px; color:var(--text);
  background:var(--surface-2); border:1px solid var(--border); border-radius:8px; padding:4px 10px;
}
.field-chip .tag{
  font-size:10px; text-transform:uppercase; letter-spacing:.04em;
  color:#346538; background:#EDF3EC; border-radius:6px; padding:1px 6px;
}
.cards{display:grid; grid-template-columns:repeat(auto-fill,minmax(150px,1fr)); gap:12px; margin:12px 0}
.card{
  background:var(--surface); border:1px solid var(--border); border-radius:var(--radius);
  padding:16px 18px;
}
.card .n{font-size:26px; font-weight:600; color:var(--ink); letter-spacing:-.02em}
.card .t{font-size:12px; text-transform:uppercase; letter-spacing:.06em; color:var(--muted); margin-top:2px}
"""

def _parse_session_expires_at(value: str) -> datetime | None:
    try:
        expires_at = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if expires_at.tzinfo is None:
        return expires_at.replace(tzinfo=timezone.utc)
    return expires_at.astimezone(timezone.utc)
