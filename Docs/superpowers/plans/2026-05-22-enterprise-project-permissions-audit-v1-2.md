# Legal-MCP v1.2 Enterprise Project Permissions and Audit Console Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build v1.2 enterprise project-level permissions, named user API keys, database-backed disclosure audit, and a lightweight Admin Web UI.

**Architecture:** Execute the work in an isolated git worktree on a `codex/` branch. Add an identity layer that resolves bearer tokens to users, a project policy layer that filters all MCP tool output by visible projects, and a database audit layer that records both tool calls and project disclosure decisions. Add a minimal stdlib Admin Web server for local-user login, user/key/project-grant management, and read-only audit browsing.

**Tech Stack:** Python 3.11+, stdlib `http.server`, stdlib `sqlite3`, stdlib `hashlib`/`hmac`/`secrets`, existing Legal-MCP MCP protocol and import pipeline, pytest, Docker Compose docs.

---

## Execution Rule: Isolated Worktree Required

Do not implement v1.2 in the main checkout. Implementation must happen in a linked worktree and feature branch.

Use this branch name:

```bash
codex/enterprise-project-permissions-audit-v1-2
```

Use this worktree path if no existing project-local worktree convention is found:

```bash
/Users/haoran/workspace/Legal-MCP/.worktrees/enterprise-project-permissions-audit-v1-2
```

Before creating a worktree, run the detection commands in Task 0. If already inside a linked worktree, use the existing isolated workspace and create or switch to the branch there.

## File Structure

Create:

- `src/legal_mcp/identity.py`: local users, roles, API key hashing/verification, password hashing, session token helpers.
- `src/legal_mcp/policy.py`: `AccessContext`, role constants, visible-project authorization helpers.
- `src/legal_mcp/disclosure_audit.py`: database-backed audit event and disclosure writes plus query helpers for Admin Web.
- `src/legal_mcp/admin_server.py`: stdlib HTTP Admin Web server, session cookie handling, HTML pages, form handlers.
- `tests/test_identity.py`: user creation, API key hashing, token verification, password verification.
- `tests/test_policy.py`: legal/business/auditor/admin visible-project behavior.
- `tests/test_disclosure_audit.py`: database audit event and disclosure persistence.
- `tests/test_admin_server.py`: login, user management, key creation, project grant, audit page smoke tests.

Modify:

- `src/legal_mcp/schema.sql`: add identity, grant, session, and audit tables plus indexes.
- `src/legal_mcp/tools.py`: accept an optional `AccessContext`, filter all tool queries by visible projects, write disclosure audit rows.
- `src/legal_mcp/mcp_protocol.py`: accept an optional `AccessContext` and pass it to tools.
- `src/legal_mcp/http_server.py`: support v1.2 bearer-token identity mode while preserving v1.1 shared-token legacy mode.
- `src/legal_mcp/cli.py`: add `admin`, `serve-admin`, and identity-management bootstrap options.
- `tests/test_schema.py`: assert new tables, columns, indexes, and constraints.
- `tests/test_tools.py`: add permission-filtering tests for all MCP tools.
- `tests/test_mcp_protocol.py`: verify access context flows through protocol handling.
- `tests/test_http_server.py`: verify named user token auth, legacy token compatibility, and denied/revoked key behavior.
- `README.md`, `Docs/team-deployment.md`, `TODOS.md`: document v1.2 usage and mark enterprise permissions as planned/implemented.

Do not create a data-editing UI for projects/contracts/licenses/risks. Admin Web only manages users, keys, project grants, and audit browsing.

---

## Task 0: Create Isolated Worktree and Verify Baseline

**Files:**
- Modify only if needed: `.gitignore`
- No application code changes

- [ ] **Step 1: Detect whether the current checkout is already a linked worktree**

Run:

```bash
cd /Users/haoran/workspace/Legal-MCP
GIT_DIR=$(cd "$(git rev-parse --git-dir)" && pwd -P)
GIT_COMMON=$(cd "$(git rev-parse --git-common-dir)" && pwd -P)
BRANCH=$(git branch --show-current)
SUPERPROJECT=$(git rev-parse --show-superproject-working-tree 2>/dev/null || true)
printf 'git_dir=%s\ngit_common=%s\nbranch=%s\nsuperproject=%s\n' "$GIT_DIR" "$GIT_COMMON" "$BRANCH" "$SUPERPROJECT"
```

Expected in the main checkout:

```text
git_dir=/Users/haoran/workspace/Legal-MCP/.git
git_common=/Users/haoran/workspace/Legal-MCP/.git
branch=main
superproject=
```

If `git_dir` and `git_common` differ and `superproject` is empty, continue inside that existing worktree instead of creating another one.

- [ ] **Step 2: Confirm project-local worktrees are ignored**

Run:

```bash
cd /Users/haoran/workspace/Legal-MCP
git check-ignore -q .worktrees && echo ".worktrees ignored"
```

Expected:

```text
.worktrees ignored
```

If this fails, add `.worktrees/` to `.gitignore` and commit it before creating the worktree:

```bash
printf '\n.worktrees/\n' >> .gitignore
git add .gitignore
git commit -m "chore: ignore local worktrees"
```

- [ ] **Step 3: Create the v1.2 worktree and branch**

Run from the main checkout:

```bash
cd /Users/haoran/workspace/Legal-MCP
git worktree add .worktrees/enterprise-project-permissions-audit-v1-2 -b codex/enterprise-project-permissions-audit-v1-2 main
cd .worktrees/enterprise-project-permissions-audit-v1-2
git branch --show-current
```

Expected:

```text
codex/enterprise-project-permissions-audit-v1-2
```

- [ ] **Step 4: Run baseline tests in the worktree**

Run:

```bash
cd /Users/haoran/workspace/Legal-MCP/.worktrees/enterprise-project-permissions-audit-v1-2
python -m pytest
```

Expected:

```text
... passed ...
```

If pytest is not installed, run:

```bash
uv run pytest
```

Expected:

```text
... passed ...
```

- [ ] **Step 5: Commit only if `.gitignore` changed**

If Step 2 changed `.gitignore`, the commit already happened in the main checkout. Do not make an empty commit.

---

## Task 1: Extend SQLite Schema for Users, Keys, Grants, Sessions, and Audit

**Files:**
- Modify: `src/legal_mcp/schema.sql`
- Modify: `tests/test_schema.py`

- [ ] **Step 1: Write failing schema tests**

Modify `tests/test_schema.py` so `EXPECTED_COLUMNS` includes:

```python
    "users": [
        "id",
        "email",
        "display_name",
        "role",
        "status",
        "password_hash",
        "external_subject",
        "created_at",
        "updated_at",
    ],
    "api_keys": [
        "id",
        "user_id",
        "key_prefix",
        "key_hash",
        "label",
        "status",
        "last_used_at",
        "created_at",
        "revoked_at",
    ],
    "project_access": [
        "id",
        "user_id",
        "project_id",
        "granted_by_user_id",
        "created_at",
    ],
    "admin_sessions": [
        "id",
        "user_id",
        "session_hash",
        "expires_at",
        "created_at",
    ],
    "audit_events": [
        "id",
        "timestamp",
        "user_id",
        "api_key_id",
        "source_client",
        "tool_name",
        "rationale",
        "arguments_summary",
        "result_status",
        "error_code",
        "response_record_count",
    ],
    "audit_disclosures": [
        "id",
        "audit_event_id",
        "project_id",
        "record_type",
        "record_id",
        "decision",
        "reason",
    ],
```

Add these expected indexes to `EXPECTED_INDEXES`:

```python
    ("users", ("email",), True),
    ("users", ("external_subject",), False),
    ("api_keys", ("key_prefix",), False),
    ("api_keys", ("user_id",), False),
    ("project_access", ("user_id", "project_id"), True),
    ("project_access", ("project_id",), False),
    ("admin_sessions", ("session_hash",), True),
    ("admin_sessions", ("user_id",), False),
    ("audit_events", ("timestamp",), False),
    ("audit_events", ("user_id",), False),
    ("audit_events", ("tool_name",), False),
    ("audit_disclosures", ("audit_event_id",), False),
    ("audit_disclosures", ("project_id",), False),
```

Add a new test:

```python
def test_identity_schema_enforces_unique_email_and_project_grants(tmp_path) -> None:
    db_path = tmp_path / "legal.db"
    db.initialize_database(db_path)

    conn = db.connect(db_path)
    try:
        conn.execute(
            """
            insert into users (email, display_name, role, status)
            values (?, ?, ?, ?)
            """,
            ("legal@example.test", "Legal User", "legal", "active"),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                insert into users (email, display_name, role, status)
                values (?, ?, ?, ?)
                """,
                ("legal@example.test", "Duplicate", "business", "active"),
            )

        user_id = conn.execute("select id from users").fetchone()["id"]
        conn.execute(
            "insert into projects (project_code, name, stage) values (?, ?, ?)",
            ("GAME-001", "Project One", "live"),
        )
        project_id = conn.execute("select id from projects").fetchone()["id"]
        conn.execute(
            """
            insert into project_access (user_id, project_id, granted_by_user_id)
            values (?, ?, ?)
            """,
            (user_id, project_id, user_id),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                insert into project_access (user_id, project_id, granted_by_user_id)
                values (?, ?, ?)
                """,
                (user_id, project_id, user_id),
            )
    finally:
        conn.close()
```

- [ ] **Step 2: Run schema tests to verify they fail**

Run:

```bash
python -m pytest tests/test_schema.py -v
```

Expected: failure showing missing tables such as `users` or missing columns.

- [ ] **Step 3: Add schema tables and indexes**

Append to `src/legal_mcp/schema.sql`:

```sql
create table if not exists users (
  id integer primary key,
  email text not null unique,
  display_name text not null,
  role text not null check (role in ('admin', 'legal', 'business', 'auditor')),
  status text not null check (status in ('active', 'disabled')) default 'active',
  password_hash text,
  external_subject text,
  created_at text not null default (datetime('now')),
  updated_at text not null default (datetime('now'))
);

create table if not exists api_keys (
  id integer primary key,
  user_id integer not null references users(id),
  key_prefix text not null,
  key_hash text not null,
  label text not null,
  status text not null check (status in ('active', 'revoked')) default 'active',
  last_used_at text,
  created_at text not null default (datetime('now')),
  revoked_at text
);

create table if not exists project_access (
  id integer primary key,
  user_id integer not null references users(id),
  project_id integer not null references projects(id),
  granted_by_user_id integer not null references users(id),
  created_at text not null default (datetime('now')),
  unique(user_id, project_id)
);

create table if not exists admin_sessions (
  id integer primary key,
  user_id integer not null references users(id),
  session_hash text not null unique,
  expires_at text not null,
  created_at text not null default (datetime('now'))
);

create table if not exists audit_events (
  id integer primary key,
  timestamp text not null default (datetime('now')),
  user_id integer references users(id),
  api_key_id integer references api_keys(id),
  source_client text,
  tool_name text not null,
  rationale text,
  arguments_summary text not null,
  result_status text not null,
  error_code text,
  response_record_count integer not null default 0
);

create table if not exists audit_disclosures (
  id integer primary key,
  audit_event_id integer not null references audit_events(id),
  project_id integer references projects(id),
  record_type text not null,
  record_id integer,
  decision text not null check (decision in ('allowed', 'denied')),
  reason text not null
);

create index if not exists idx_users_external_subject on users(external_subject);
create index if not exists idx_api_keys_prefix on api_keys(key_prefix);
create index if not exists idx_api_keys_user on api_keys(user_id);
create index if not exists idx_project_access_project on project_access(project_id);
create index if not exists idx_admin_sessions_user on admin_sessions(user_id);
create index if not exists idx_audit_events_timestamp on audit_events(timestamp);
create index if not exists idx_audit_events_user on audit_events(user_id);
create index if not exists idx_audit_events_tool on audit_events(tool_name);
create index if not exists idx_audit_disclosures_event on audit_disclosures(audit_event_id);
create index if not exists idx_audit_disclosures_project on audit_disclosures(project_id);
```

- [ ] **Step 4: Run schema tests**

Run:

```bash
python -m pytest tests/test_schema.py -v
```

Expected: all schema tests pass.

- [ ] **Step 5: Commit schema changes**

Run:

```bash
git add src/legal_mcp/schema.sql tests/test_schema.py
git commit -m "feat: add v1.2 identity and audit schema"
```

---

## Task 2: Add Local Identity and API Key Helpers

**Files:**
- Create: `src/legal_mcp/identity.py`
- Create: `tests/test_identity.py`

- [ ] **Step 1: Write failing identity tests**

Create `tests/test_identity.py`:

```python
from __future__ import annotations

from legal_mcp import db
from legal_mcp.identity import (
    ROLE_ADMIN,
    ROLE_BUSINESS,
    ROLE_LEGAL,
    create_api_key,
    create_user,
    get_user,
    hash_password,
    verify_api_key,
    verify_password,
)


def test_create_user_and_password_verification(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        password_hash = hash_password("correct horse battery staple")
        user = create_user(
            conn,
            email="admin@example.test",
            display_name="Admin",
            role=ROLE_ADMIN,
            password_hash=password_hash,
        )

        loaded = get_user(conn, user["id"])
        assert loaded["email"] == "admin@example.test"
        assert loaded["role"] == ROLE_ADMIN
        assert verify_password("correct horse battery staple", loaded["password_hash"])
        assert not verify_password("wrong", loaded["password_hash"])
    finally:
        conn.close()


def test_create_api_key_returns_plaintext_once_and_verifies_hash(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        user = create_user(
            conn,
            email="legal@example.test",
            display_name="Legal",
            role=ROLE_LEGAL,
        )
        created = create_api_key(conn, user_id=user["id"], label="Claude Desktop")

        assert created.plaintext.startswith("lmcp_")
        assert created.prefix == created.plaintext[:12]
        verified = verify_api_key(conn, created.plaintext)
        assert verified is not None
        assert verified.user["email"] == "legal@example.test"
        assert verified.api_key["label"] == "Claude Desktop"
        assert verified.user["role"] == ROLE_LEGAL
    finally:
        conn.close()


def test_revoked_or_disabled_credentials_do_not_verify(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        user = create_user(
            conn,
            email="business@example.test",
            display_name="Business",
            role=ROLE_BUSINESS,
        )
        created = create_api_key(conn, user_id=user["id"], label="Cursor")
        conn.execute("update api_keys set status = 'revoked' where id = ?", (created.api_key_id,))
        conn.commit()
        assert verify_api_key(conn, created.plaintext) is None

        second = create_api_key(conn, user_id=user["id"], label="Codex")
        conn.execute("update users set status = 'disabled' where id = ?", (user["id"],))
        conn.commit()
        assert verify_api_key(conn, second.plaintext) is None
    finally:
        conn.close()
```

- [ ] **Step 2: Run identity tests to verify they fail**

Run:

```bash
python -m pytest tests/test_identity.py -v
```

Expected: import failure for `legal_mcp.identity`.

- [ ] **Step 3: Implement identity helpers**

Create `src/legal_mcp/identity.py`:

```python
"""Local Legal-MCP identity and credential helpers."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

ROLE_ADMIN = "admin"
ROLE_LEGAL = "legal"
ROLE_BUSINESS = "business"
ROLE_AUDITOR = "auditor"

ACTIVE = "active"
DISABLED = "disabled"
REVOKED = "revoked"

_PBKDF2_ITERATIONS = 200_000


@dataclass(frozen=True)
class CreatedAPIKey:
    api_key_id: int
    plaintext: str
    prefix: str


@dataclass(frozen=True)
class VerifiedAPIKey:
    user: dict[str, Any]
    api_key: dict[str, Any]


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        _PBKDF2_ITERATIONS,
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        return False
    try:
        algorithm, iterations_text, salt_text, digest_text = password_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iterations_text)
        salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_text.encode("ascii"))
    except (ValueError, TypeError):
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_user(
    conn: sqlite3.Connection,
    *,
    email: str,
    display_name: str,
    role: str,
    password_hash: str | None = None,
    external_subject: str | None = None,
) -> dict[str, Any]:
    cursor = conn.execute(
        """
        insert into users (email, display_name, role, status, password_hash, external_subject)
        values (?, ?, ?, ?, ?, ?)
        """,
        (email, display_name, role, ACTIVE, password_hash, external_subject),
    )
    conn.commit()
    return get_user(conn, int(cursor.lastrowid))


def get_user(conn: sqlite3.Connection, user_id: int) -> dict[str, Any]:
    row = conn.execute("select * from users where id = ?", (user_id,)).fetchone()
    if row is None:
        raise LookupError(f"user not found: {user_id}")
    return dict(row)


def create_api_key(conn: sqlite3.Connection, *, user_id: int, label: str) -> CreatedAPIKey:
    plaintext = "lmcp_" + secrets.token_urlsafe(32)
    prefix = plaintext[:12]
    cursor = conn.execute(
        """
        insert into api_keys (user_id, key_prefix, key_hash, label, status)
        values (?, ?, ?, ?, ?)
        """,
        (user_id, prefix, hash_token(plaintext), label, ACTIVE),
    )
    conn.commit()
    return CreatedAPIKey(api_key_id=int(cursor.lastrowid), plaintext=plaintext, prefix=prefix)


def verify_api_key(conn: sqlite3.Connection, token: str) -> VerifiedAPIKey | None:
    prefix = token[:12]
    token_hash = hash_token(token)
    rows = conn.execute(
        """
        select
          api_keys.id as api_key_id,
          api_keys.user_id,
          api_keys.key_prefix,
          api_keys.key_hash,
          api_keys.label,
          api_keys.status as api_key_status,
          users.email,
          users.display_name,
          users.role,
          users.status as user_status,
          users.external_subject
        from api_keys
        join users on users.id = api_keys.user_id
        where api_keys.key_prefix = ?
        """,
        (prefix,),
    ).fetchall()
    for row in rows:
        item = dict(row)
        if not hmac.compare_digest(item["key_hash"], token_hash):
            continue
        if item["api_key_status"] != ACTIVE or item["user_status"] != ACTIVE:
            return None
        conn.execute(
            "update api_keys set last_used_at = ? where id = ?",
            (datetime.now(timezone.utc).isoformat(), item["api_key_id"]),
        )
        conn.commit()
        return VerifiedAPIKey(
            user={
                "id": item["user_id"],
                "email": item["email"],
                "display_name": item["display_name"],
                "role": item["role"],
                "status": item["user_status"],
                "external_subject": item["external_subject"],
            },
            api_key={
                "id": item["api_key_id"],
                "user_id": item["user_id"],
                "key_prefix": item["key_prefix"],
                "label": item["label"],
                "status": item["api_key_status"],
            },
        )
    return None
```

- [ ] **Step 4: Run identity tests**

Run:

```bash
python -m pytest tests/test_identity.py -v
```

Expected: all identity tests pass.

- [ ] **Step 5: Commit identity helpers**

Run:

```bash
git add src/legal_mcp/identity.py tests/test_identity.py
git commit -m "feat: add local identity helpers"
```

---

## Task 3: Add Project Policy Access Context

**Files:**
- Create: `src/legal_mcp/policy.py`
- Create: `tests/test_policy.py`

- [ ] **Step 1: Write failing policy tests**

Create `tests/test_policy.py`:

```python
from __future__ import annotations

from legal_mcp import db
from legal_mcp.identity import ROLE_ADMIN, ROLE_AUDITOR, ROLE_BUSINESS, ROLE_LEGAL, create_user
from legal_mcp.policy import AccessContext, can_query_content, visible_project_ids


def _project(conn, code: str) -> int:
    cursor = conn.execute(
        "insert into projects (project_code, name, stage) values (?, ?, ?)",
        (code, f"{code} Name", "live"),
    )
    conn.commit()
    return int(cursor.lastrowid)


def test_legal_and_admin_can_see_all_projects(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        first = _project(conn, "GAME-001")
        second = _project(conn, "GAME-002")
        admin = create_user(conn, email="admin@example.test", display_name="Admin", role=ROLE_ADMIN)
        legal = create_user(conn, email="legal@example.test", display_name="Legal", role=ROLE_LEGAL)

        assert visible_project_ids(conn, AccessContext.from_user(admin)) == {first, second}
        assert visible_project_ids(conn, AccessContext.from_user(legal)) == {first, second}
        assert can_query_content(AccessContext.from_user(admin))
        assert can_query_content(AccessContext.from_user(legal))
    finally:
        conn.close()


def test_business_can_see_only_granted_projects(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        first = _project(conn, "GAME-001")
        _project(conn, "GAME-002")
        admin = create_user(conn, email="admin@example.test", display_name="Admin", role=ROLE_ADMIN)
        business = create_user(conn, email="biz@example.test", display_name="Biz", role=ROLE_BUSINESS)
        conn.execute(
            "insert into project_access (user_id, project_id, granted_by_user_id) values (?, ?, ?)",
            (business["id"], first, admin["id"]),
        )
        conn.commit()

        assert visible_project_ids(conn, AccessContext.from_user(business)) == {first}
        assert can_query_content(AccessContext.from_user(business))
    finally:
        conn.close()


def test_auditor_can_not_query_content(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        _project(conn, "GAME-001")
        auditor = create_user(conn, email="audit@example.test", display_name="Audit", role=ROLE_AUDITOR)

        assert visible_project_ids(conn, AccessContext.from_user(auditor)) == set()
        assert not can_query_content(AccessContext.from_user(auditor))
    finally:
        conn.close()
```

- [ ] **Step 2: Run policy tests to verify they fail**

Run:

```bash
python -m pytest tests/test_policy.py -v
```

Expected: import failure for `legal_mcp.policy`.

- [ ] **Step 3: Implement policy module**

Create `src/legal_mcp/policy.py`:

```python
"""Project-level access policy for Legal-MCP."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from legal_mcp.identity import ROLE_ADMIN, ROLE_AUDITOR, ROLE_BUSINESS, ROLE_LEGAL


@dataclass(frozen=True)
class AccessContext:
    user_id: int | None
    role: str
    email: str | None = None
    api_key_id: int | None = None
    legacy_shared_token: bool = False

    @classmethod
    def from_user(cls, user: dict[str, Any], *, api_key_id: int | None = None) -> "AccessContext":
        return cls(
            user_id=int(user["id"]),
            role=str(user["role"]),
            email=user.get("email"),
            api_key_id=api_key_id,
        )

    @classmethod
    def legacy(cls) -> "AccessContext":
        return cls(user_id=None, role=ROLE_LEGAL, legacy_shared_token=True)


def can_query_content(context: AccessContext | None) -> bool:
    if context is None:
        return True
    return context.role in {ROLE_ADMIN, ROLE_LEGAL, ROLE_BUSINESS}


def visible_project_ids(conn: sqlite3.Connection, context: AccessContext | None) -> set[int] | None:
    if context is None or context.legacy_shared_token:
        return None
    if context.role in {ROLE_ADMIN, ROLE_LEGAL}:
        rows = conn.execute("select id from projects").fetchall()
        return {int(row["id"]) for row in rows}
    if context.role == ROLE_BUSINESS and context.user_id is not None:
        rows = conn.execute(
            "select project_id from project_access where user_id = ?",
            (context.user_id,),
        ).fetchall()
        return {int(row["project_id"]) for row in rows}
    if context.role == ROLE_AUDITOR:
        return set()
    return set()


def project_is_visible(conn: sqlite3.Connection, context: AccessContext | None, project_id: int) -> bool:
    visible = visible_project_ids(conn, context)
    return visible is None or project_id in visible
```

- [ ] **Step 4: Run policy tests**

Run:

```bash
python -m pytest tests/test_policy.py -v
```

Expected: all policy tests pass.

- [ ] **Step 5: Commit policy module**

Run:

```bash
git add src/legal_mcp/policy.py tests/test_policy.py
git commit -m "feat: add project access policy"
```

---

## Task 4: Add Database-Backed Disclosure Audit

**Files:**
- Create: `src/legal_mcp/disclosure_audit.py`
- Create: `tests/test_disclosure_audit.py`

- [ ] **Step 1: Write failing audit tests**

Create `tests/test_disclosure_audit.py`:

```python
from __future__ import annotations

from legal_mcp import db
from legal_mcp.disclosure_audit import (
    Disclosure,
    list_audit_events,
    write_audit_event,
)
from legal_mcp.identity import ROLE_BUSINESS, create_api_key, create_user
from legal_mcp.policy import AccessContext


def test_write_audit_event_persists_event_and_disclosures(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        user = create_user(conn, email="biz@example.test", display_name="Biz", role=ROLE_BUSINESS)
        key = create_api_key(conn, user_id=user["id"], label="Cursor")
        project_id = conn.execute(
            "insert into projects (project_code, name, stage) values (?, ?, ?) returning id",
            ("GAME-001", "Project One", "live"),
        ).fetchone()["id"]
        conn.commit()

        event_id = write_audit_event(
            conn,
            context=AccessContext.from_user(user, api_key_id=key.api_key_id),
            tool_name="get_project_context",
            rationale="contract review",
            source_client="pytest",
            arguments={"project_id_or_name": "GAME-001", "rationale": "contract review"},
            result={"project": {"id": project_id}},
            disclosures=[
                Disclosure(
                    project_id=project_id,
                    record_type="project",
                    record_id=project_id,
                    decision="allowed",
                    reason="project_visible",
                )
            ],
        )

        rows = conn.execute("select * from audit_events").fetchall()
        disclosure_rows = conn.execute("select * from audit_disclosures").fetchall()
        assert rows[0]["id"] == event_id
        assert rows[0]["user_id"] == user["id"]
        assert rows[0]["api_key_id"] == key.api_key_id
        assert rows[0]["tool_name"] == "get_project_context"
        assert rows[0]["result_status"] == "success"
        assert rows[0]["response_record_count"] == 1
        assert disclosure_rows[0]["decision"] == "allowed"
        assert disclosure_rows[0]["reason"] == "project_visible"
    finally:
        conn.close()


def test_list_audit_events_filters_by_project(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        user = create_user(conn, email="biz@example.test", display_name="Biz", role=ROLE_BUSINESS)
        project_id = conn.execute(
            "insert into projects (project_code, name, stage) values (?, ?, ?) returning id",
            ("GAME-001", "Project One", "live"),
        ).fetchone()["id"]
        conn.commit()
        write_audit_event(
            conn,
            context=AccessContext.from_user(user),
            tool_name="list_projects",
            rationale="status",
            source_client=None,
            arguments={"rationale": "status"},
            result={"projects": []},
            disclosures=[
                Disclosure(project_id=project_id, record_type="project", record_id=project_id, decision="allowed", reason="project_visible")
            ],
        )

        events = list_audit_events(conn, project_id=project_id)
        assert len(events) == 1
        assert events[0]["email"] == "biz@example.test"
        assert events[0]["tool_name"] == "list_projects"
    finally:
        conn.close()
```

- [ ] **Step 2: Run audit tests to verify they fail**

Run:

```bash
python -m pytest tests/test_disclosure_audit.py -v
```

Expected: import failure for `legal_mcp.disclosure_audit`.

- [ ] **Step 3: Implement disclosure audit module**

Create `src/legal_mcp/disclosure_audit.py`:

```python
"""Database-backed audit records for MCP disclosures."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from legal_mcp.audit import summarize_arguments
from legal_mcp.policy import AccessContext


@dataclass(frozen=True)
class Disclosure:
    project_id: int | None
    record_type: str
    record_id: int | None
    decision: str
    reason: str


def write_audit_event(
    conn: sqlite3.Connection,
    *,
    context: AccessContext | None,
    tool_name: str,
    rationale: str | None,
    source_client: str | None,
    arguments: dict[str, Any],
    result: dict[str, Any],
    disclosures: list[Disclosure],
) -> int:
    error = result.get("error")
    result_status = "error" if error else "success"
    error_code = error.get("code") if isinstance(error, dict) else None
    cursor = conn.execute(
        """
        insert into audit_events (
          user_id, api_key_id, source_client, tool_name, rationale,
          arguments_summary, result_status, error_code, response_record_count
        )
        values (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            context.user_id if context else None,
            context.api_key_id if context else None,
            source_client,
            tool_name,
            rationale,
            summarize_arguments(arguments),
            result_status,
            error_code,
            _count_records(result),
        ),
    )
    event_id = int(cursor.lastrowid)
    conn.executemany(
        """
        insert into audit_disclosures (
          audit_event_id, project_id, record_type, record_id, decision, reason
        )
        values (?, ?, ?, ?, ?, ?)
        """,
        [
            (
                event_id,
                disclosure.project_id,
                disclosure.record_type,
                disclosure.record_id,
                disclosure.decision,
                disclosure.reason,
            )
            for disclosure in disclosures
        ],
    )
    conn.commit()
    return event_id


def list_audit_events(
    conn: sqlite3.Connection,
    *,
    user_id: int | None = None,
    project_id: int | None = None,
    tool_name: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    clauses = []
    params: list[Any] = []
    if user_id is not None:
        clauses.append("audit_events.user_id = ?")
        params.append(user_id)
    if tool_name:
        clauses.append("audit_events.tool_name = ?")
        params.append(tool_name)
    if project_id is not None:
        clauses.append(
            "exists (select 1 from audit_disclosures where audit_disclosures.audit_event_id = audit_events.id and audit_disclosures.project_id = ?)"
        )
        params.append(project_id)
    where = " where " + " and ".join(clauses) if clauses else ""
    params.append(limit)
    rows = conn.execute(
        f"""
        select audit_events.*, users.email, users.display_name
        from audit_events
        left join users on users.id = audit_events.user_id
        {where}
        order by audit_events.timestamp desc, audit_events.id desc
        limit ?
        """,
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def _count_records(result: dict[str, Any]) -> int:
    if "error" in result:
        return 0
    total = 0
    for value in result.values():
        if isinstance(value, list):
            total += len(value)
        elif isinstance(value, dict):
            total += 1
    return total
```

- [ ] **Step 4: Run disclosure audit tests**

Run:

```bash
python -m pytest tests/test_disclosure_audit.py -v
```

Expected: all disclosure audit tests pass.

- [ ] **Step 5: Commit disclosure audit module**

Run:

```bash
git add src/legal_mcp/disclosure_audit.py tests/test_disclosure_audit.py
git commit -m "feat: add disclosure audit persistence"
```

---

## Task 5: Apply Project Permissions to MCP Tools

**Files:**
- Modify: `src/legal_mcp/tools.py`
- Modify: `tests/test_tools.py`

- [ ] **Step 1: Write failing tool permission tests**

Append to `tests/test_tools.py`:

```python
from legal_mcp.identity import ROLE_AUDITOR, ROLE_BUSINESS, ROLE_LEGAL, create_user
from legal_mcp.policy import AccessContext


def test_list_projects_filters_to_business_user_grants(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        visible_id = seed_project(conn, code="GAME-001", name="Visible")
        seed_project(conn, code="GAME-002", name="Hidden")
        admin = create_user(conn, email="admin@example.test", display_name="Admin", role="admin")
        business = create_user(conn, email="biz@example.test", display_name="Biz", role=ROLE_BUSINESS)
        conn.execute(
            "insert into project_access (user_id, project_id, granted_by_user_id) values (?, ?, ?)",
            (business["id"], visible_id, admin["id"]),
        )
        conn.commit()
    finally:
        conn.close()

    result = call_tool(
        "list_projects",
        {"rationale": "status"},
        database_path=database_path,
        access_context=AccessContext.from_user(business),
    )

    assert [project["project_code"] for project in result["projects"]] == ["GAME-001"]


def test_get_project_context_returns_not_found_for_hidden_project(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        seed_project(conn, code="GAME-001", name="Hidden")
        business = create_user(conn, email="biz@example.test", display_name="Biz", role=ROLE_BUSINESS)
    finally:
        conn.close()

    result = call_tool(
        "get_project_context",
        {"project_id_or_name": "GAME-001", "rationale": "status"},
        database_path=database_path,
        access_context=AccessContext.from_user(business),
    )

    assert result["error"]["code"] == "not_found"


def test_legal_user_sees_all_projects_without_grants(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        seed_project(conn, code="GAME-001", name="One")
        seed_project(conn, code="GAME-002", name="Two")
        legal = create_user(conn, email="legal@example.test", display_name="Legal", role=ROLE_LEGAL)
    finally:
        conn.close()

    result = call_tool(
        "list_projects",
        {"rationale": "status"},
        database_path=database_path,
        access_context=AccessContext.from_user(legal),
    )

    assert [project["project_code"] for project in result["projects"]] == ["GAME-001", "GAME-002"]


def test_auditor_cannot_call_content_tools(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        auditor = create_user(conn, email="audit@example.test", display_name="Audit", role=ROLE_AUDITOR)
    finally:
        conn.close()

    result = call_tool(
        "list_projects",
        {"rationale": "audit review"},
        database_path=database_path,
        access_context=AccessContext.from_user(auditor),
    )

    assert result["error"]["code"] == "access_denied"
```

- [ ] **Step 2: Run tool tests to verify they fail**

Run:

```bash
python -m pytest tests/test_tools.py -v
```

Expected: failure because `call_tool` does not accept `access_context`.

- [ ] **Step 3: Modify `call_tool` signature and early auditor denial**

In `src/legal_mcp/tools.py`, add imports:

```python
from legal_mcp.disclosure_audit import Disclosure, write_audit_event
from legal_mcp.policy import AccessContext, can_query_content, project_is_visible, visible_project_ids
```

Change `call_tool` signature:

```python
def call_tool(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    database_path: str | Path,
    audit_path: str | Path = DEFAULT_AUDIT_PATH,
    access_context: AccessContext | None = None,
) -> dict[str, Any]:
```

After rationale validation and before dispatch, add:

```python
    if not can_query_content(access_context):
        result = _error("access_denied", "user is not allowed to query project content")
        _audit(tool_name, rationale, source_client, arguments, result, audit_path)
        return result
```

- [ ] **Step 4: Filter list queries by visible project ids**

Update helper signatures:

```python
def _list_projects(conn: sqlite3.Connection, arguments: dict[str, Any], access_context: AccessContext | None) -> dict[str, Any]:
def _get_project_context(conn: sqlite3.Connection, arguments: dict[str, Any], access_context: AccessContext | None) -> dict[str, Any]:
def _list_expiring_licenses(conn: sqlite3.Connection, arguments: dict[str, Any], access_context: AccessContext | None) -> dict[str, Any]:
def _list_open_risks(conn: sqlite3.Connection, arguments: dict[str, Any], access_context: AccessContext | None) -> dict[str, Any]:
```

Call them with `access_context` in the dispatch block.

For `list_projects`, use:

```python
    visible = visible_project_ids(conn, access_context)
    clauses = []
    params: list[Any] = []
    stage = arguments.get("stage")
    if stage:
        clauses.append("stage = ?")
        params.append(stage)
    if visible is not None:
        if not visible:
            return {"projects": []}
        placeholders = ",".join("?" for _ in visible)
        clauses.append(f"id in ({placeholders})")
        params.extend(sorted(visible))
    where = " where " + " and ".join(clauses) if clauses else ""
    rows = conn.execute(f"select * from projects{where} order by project_code", params).fetchall()
    return {"projects": [dict(row) for row in rows]}
```

For `get_project_context`, after lookup succeeds:

```python
    if not project_is_visible(conn, access_context, int(project_id)):
        return _error("not_found", "project not found")
```

For `list_expiring_licenses` and `list_open_risks`, add visible-project filtering using `projects.id in (...)`; return empty lists when visible set is empty.

- [ ] **Step 5: Run tool tests**

Run:

```bash
python -m pytest tests/test_tools.py -v
```

Expected: all tool tests pass.

- [ ] **Step 6: Run related existing tests**

Run:

```bash
python -m pytest tests/test_lookup.py tests/test_mcp_protocol.py tests/test_audit.py -v
```

Expected: all tests pass.

- [ ] **Step 7: Commit policy-filtered tools**

Run:

```bash
git add src/legal_mcp/tools.py tests/test_tools.py
git commit -m "feat: enforce project permissions in MCP tools"
```

---

## Task 6: Wire Named User Tokens into HTTP MCP Server

**Files:**
- Modify: `src/legal_mcp/http_server.py`
- Modify: `src/legal_mcp/mcp_protocol.py`
- Modify: `tests/test_http_server.py`
- Modify: `tests/test_mcp_protocol.py`

- [ ] **Step 1: Write failing HTTP auth tests**

Append to `tests/test_http_server.py`:

```python
from legal_mcp.identity import ROLE_BUSINESS, create_api_key, create_user


def test_http_mcp_accepts_named_user_api_key(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    audit_path = tmp_path / "audit.jsonl"
    _database_with_project(database_path)
    conn = db.connect(database_path)
    try:
        user = create_user(conn, email="biz@example.test", display_name="Biz", role=ROLE_BUSINESS)
        project_id = conn.execute("select id from projects where project_code = ?", ("Acme",)).fetchone()["id"]
        conn.execute(
            "insert into project_access (user_id, project_id, granted_by_user_id) values (?, ?, ?)",
            (user["id"], project_id, user["id"]),
        )
        key = create_api_key(conn, user_id=user["id"], label="pytest")
    finally:
        conn.close()

    server = build_http_server(
        host="127.0.0.1",
        port=0,
        database_path=database_path,
        audit_path=audit_path,
        bearer_token="legacy-token",
        allowed_origins=(),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, payload = _post_json(
            f"http://127.0.0.1:{server.server_port}/mcp",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "get_project_context",
                    "arguments": {"project_id_or_name": "Acme", "rationale": "named user query"},
                },
            },
            token=key.plaintext,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    tool_payload = json.loads(payload["result"]["content"][0]["text"])
    assert status == 200
    assert tool_payload["project"]["project_code"] == "Acme"
```

- [ ] **Step 2: Update MCP protocol tests for access context pass-through**

In `tests/test_mcp_protocol.py`, add a test that creates a business user with no grants and calls `handle_message(..., access_context=AccessContext.from_user(user))`; assert hidden project returns `not_found`.

Use this body:

```python
def test_tools_call_uses_access_context(tmp_path: Path) -> None:
    database_path = tmp_path / "legal.db"
    _database_with_project(database_path)
    conn = db.connect(database_path)
    try:
        user = create_user(conn, email="biz@example.test", display_name="Biz", role=ROLE_BUSINESS)
    finally:
        conn.close()

    response = handle_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "get_project_context",
                "arguments": {"project_id_or_name": "Acme", "rationale": "business query"},
            },
        },
        database_path=database_path,
        audit_path=tmp_path / "audit.jsonl",
        access_context=AccessContext.from_user(user),
    )

    tool_payload = json.loads(response["result"]["content"][0]["text"])
    assert tool_payload["error"]["code"] == "not_found"
```

Add imports:

```python
from legal_mcp.identity import ROLE_BUSINESS, create_user
from legal_mcp.policy import AccessContext
```

- [ ] **Step 3: Run HTTP/protocol tests to verify they fail**

Run:

```bash
python -m pytest tests/test_http_server.py tests/test_mcp_protocol.py -v
```

Expected: failures because protocol does not accept `access_context` and HTTP server treats named key as unauthorized.

- [ ] **Step 4: Add `access_context` to protocol handler**

Modify `src/legal_mcp/mcp_protocol.py`:

```python
from legal_mcp.policy import AccessContext
```

Change signature:

```python
def handle_message(
    message: dict[str, Any],
    *,
    database_path: str | Path,
    audit_path: str | Path,
    access_context: AccessContext | None = None,
) -> dict[str, Any] | None:
```

Pass it to `call_tool`:

```python
        result = call_tool(
            params.get("name", ""),
            params.get("arguments") or {},
            database_path=database_path,
            audit_path=audit_path,
            access_context=access_context,
        )
```

- [ ] **Step 5: Resolve access context in HTTP server**

Modify `src/legal_mcp/http_server.py`:

```python
from legal_mcp.identity import verify_api_key
from legal_mcp.policy import AccessContext
```

Replace `_is_authorized` with:

```python
    def _resolve_access_context(self) -> AccessContext | None:
        header = self.headers.get("Authorization", "")
        prefix = "Bearer "
        if not header.startswith(prefix):
            return None
        token = header[len(prefix):]
        expected = self.server.bearer_token
        if expected and token == expected:
            return AccessContext.legacy()
        conn = db.connect(self.server.database_path)
        try:
            verified = verify_api_key(conn, token)
        finally:
            conn.close()
        if verified is None:
            return None
        return AccessContext.from_user(verified.user, api_key_id=verified.api_key["id"])
```

In `do_POST`, replace:

```python
        if not self._is_authorized():
            self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
            return
```

with:

```python
        access_context = self._resolve_access_context()
        if access_context is None:
            self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
            return
```

Pass the context to `handle_message`:

```python
        response = handle_message(
            message,
            database_path=self.server.database_path,
            audit_path=self.server.audit_path,
            access_context=access_context,
        )
```

- [ ] **Step 6: Run HTTP/protocol tests**

Run:

```bash
python -m pytest tests/test_http_server.py tests/test_mcp_protocol.py -v
```

Expected: all tests pass.

- [ ] **Step 7: Commit HTTP identity wiring**

Run:

```bash
git add src/legal_mcp/http_server.py src/legal_mcp/mcp_protocol.py tests/test_http_server.py tests/test_mcp_protocol.py
git commit -m "feat: authenticate MCP requests as users"
```

---

## Task 7: Record Disclosure Audit from MCP Tool Calls

**Files:**
- Modify: `src/legal_mcp/tools.py`
- Modify: `tests/test_audit.py`
- Modify: `tests/test_tools.py`

- [ ] **Step 1: Write failing database audit integration test**

Append to `tests/test_audit.py`:

```python
from legal_mcp.identity import ROLE_BUSINESS, create_user
from legal_mcp.policy import AccessContext


def test_tool_call_writes_database_audit_event_and_disclosure(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        cursor = conn.execute(
            "insert into projects (project_code, name, stage) values (?, ?, ?)",
            ("GAME-001", "Project One", "live"),
        )
        project_id = int(cursor.lastrowid)
        user = create_user(conn, email="biz@example.test", display_name="Biz", role=ROLE_BUSINESS)
        conn.execute(
            "insert into project_access (user_id, project_id, granted_by_user_id) values (?, ?, ?)",
            (user["id"], project_id, user["id"]),
        )
        conn.commit()
    finally:
        conn.close()

    call_tool(
        "get_project_context",
        {"project_id_or_name": "GAME-001", "rationale": "status", "source_client": "pytest"},
        database_path=database_path,
        access_context=AccessContext.from_user(user),
    )

    conn = db.connect(database_path)
    try:
        event = conn.execute("select * from audit_events").fetchone()
        disclosure = conn.execute("select * from audit_disclosures").fetchone()
        assert event["user_id"] == user["id"]
        assert event["tool_name"] == "get_project_context"
        assert event["rationale"] == "status"
        assert event["source_client"] == "pytest"
        assert disclosure["project_id"] == project_id
        assert disclosure["record_type"] == "project"
        assert disclosure["decision"] == "allowed"
    finally:
        conn.close()
```

- [ ] **Step 2: Run audit tests to verify they fail**

Run:

```bash
python -m pytest tests/test_audit.py -v
```

Expected: failure because no database audit rows are written.

- [ ] **Step 3: Add disclosure collection and database audit write in `tools.py`**

In `call_tool`, initialize disclosures:

```python
    disclosures: list[Disclosure] = []
```

Change each tool helper to return both result and disclosures, or keep helper return values and derive disclosures in `call_tool` after result. For v1.2, derive in `call_tool` with:

```python
def _disclosures_from_result(result: dict[str, Any]) -> list[Disclosure]:
    disclosures: list[Disclosure] = []
    if "project" in result and isinstance(result["project"], dict):
        project_id = result["project"].get("id")
        disclosures.append(Disclosure(project_id=project_id, record_type="project", record_id=project_id, decision="allowed", reason="project_visible"))
    for key, record_type in [
        ("projects", "project"),
        ("licenses", "license"),
        ("contracts", "contract"),
        ("risks", "risk"),
    ]:
        for item in result.get(key, []) if isinstance(result.get(key), list) else []:
            if record_type == "project":
                project_id = item.get("id")
            else:
                project_id = item.get("project_id")
            record_id = item.get("id")
            disclosures.append(Disclosure(project_id=project_id, record_type=record_type, record_id=record_id, decision="allowed", reason="project_visible"))
    return disclosures
```

After `_audit(...)`, write database audit inside the same database connection path before closing, or open a short second connection after result construction:

```python
    try:
        audit_conn = db.connect(database_path)
        try:
            write_audit_event(
                audit_conn,
                context=access_context,
                tool_name=tool_name,
                rationale=rationale,
                source_client=source_client,
                arguments=arguments,
                result=result,
                disclosures=_disclosures_from_result(result),
            )
        finally:
            audit_conn.close()
    except sqlite3.Error:
        pass
```

Keep JSONL audit behavior unchanged.

- [ ] **Step 4: Run audit and tool tests**

Run:

```bash
python -m pytest tests/test_audit.py tests/test_tools.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit database audit integration**

Run:

```bash
git add src/legal_mcp/tools.py tests/test_audit.py tests/test_tools.py
git commit -m "feat: audit MCP project disclosures"
```

---

## Task 8: Add Admin Web Server

**Files:**
- Create: `src/legal_mcp/admin_server.py`
- Create: `tests/test_admin_server.py`

- [ ] **Step 1: Write failing Admin Web smoke tests**

Create `tests/test_admin_server.py`:

```python
from __future__ import annotations

import http.cookiejar
import threading
import urllib.parse
import urllib.request
from pathlib import Path

from legal_mcp import db
from legal_mcp.admin_server import build_admin_server
from legal_mcp.identity import ROLE_ADMIN, hash_password


def _opener():
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
    )


def _post(opener, url: str, data: dict[str, str]):
    encoded = urllib.parse.urlencode(data).encode("utf-8")
    request = urllib.request.Request(url, data=encoded, method="POST")
    return opener.open(request, timeout=5)


def _admin_db(path: Path) -> None:
    db.initialize_database(path)
    conn = db.connect(path)
    try:
        conn.execute(
            """
            insert into users (email, display_name, role, status, password_hash)
            values (?, ?, ?, ?, ?)
            """,
            ("admin@example.test", "Admin", ROLE_ADMIN, "active", hash_password("secret")),
        )
        conn.execute(
            "insert into projects (project_code, name, stage) values (?, ?, ?)",
            ("GAME-001", "Project One", "live"),
        )
        conn.commit()
    finally:
        conn.close()


def test_admin_login_and_users_page(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    _admin_db(database_path)
    server = build_admin_server(host="127.0.0.1", port=0, database_path=database_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    opener = _opener()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        response = _post(opener, f"{base}/login", {"email": "admin@example.test", "password": "secret"})
        assert response.status in {200, 303}
        users = opener.open(f"{base}/admin/users", timeout=5).read().decode("utf-8")
        assert "admin@example.test" in users
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
```

- [ ] **Step 2: Run Admin Web tests to verify they fail**

Run:

```bash
python -m pytest tests/test_admin_server.py -v
```

Expected: import failure for `legal_mcp.admin_server`.

- [ ] **Step 3: Implement minimal Admin Web server**

Create `src/legal_mcp/admin_server.py` with:

```python
"""Lightweight Admin Web UI for Legal-MCP v1.2."""

from __future__ import annotations

import html
import sqlite3
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from legal_mcp import db
from legal_mcp.identity import ROLE_ADMIN, hash_token, verify_password


class LegalMCPAdminServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        RequestHandlerClass: type[BaseHTTPRequestHandler],
        *,
        database_path: str | Path,
    ) -> None:
        super().__init__(server_address, RequestHandlerClass)
        self.database_path = Path(database_path)


class LegalMCPAdminHandler(BaseHTTPRequestHandler):
    server: LegalMCPAdminServer

    def do_GET(self) -> None:
        if self.path == "/" or self.path == "/login":
            self._send_html(HTTPStatus.OK, _page("Login", _login_form()))
            return
        user = self._current_admin()
        if user is None:
            self._redirect("/login")
            return
        if self.path == "/admin/users":
            self._send_html(HTTPStatus.OK, _page("Users", self._users_html()))
            return
        if self.path == "/admin/audit":
            self._send_html(HTTPStatus.OK, _page("Audit", self._audit_html()))
            return
        self._send_html(HTTPStatus.NOT_FOUND, _page("Not found", "<p>Not found</p>"))

    def do_POST(self) -> None:
        form = self._read_form()
        if self.path == "/login":
            session_hash = self._login(form.get("email", ""), form.get("password", ""))
            if session_hash:
                self._redirect_with_cookie("/admin/users", session_hash)
            else:
                self._send_html(HTTPStatus.UNAUTHORIZED, _page("Login", _login_form("Invalid login")))
            return
        self._send_html(HTTPStatus.NOT_FOUND, _page("Not found", "<p>Not found</p>"))

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _connect(self) -> sqlite3.Connection:
        return db.connect(self.server.database_path)

    def _read_form(self) -> dict[str, str]:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        parsed = urllib.parse.parse_qs(body)
        return {key: values[0] for key, values in parsed.items() if values}

    def _login(self, email: str, password: str) -> str | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "select * from users where email = ? and role = ? and status = 'active'",
                (email, ROLE_ADMIN),
            ).fetchone()
            if row is None or not verify_password(password, row["password_hash"]):
                return None
            token = f"session-{row['id']}-{row['email']}"
            session_hash = hash_token(token)
            conn.execute(
                """
                insert into admin_sessions (user_id, session_hash, expires_at)
                values (?, ?, datetime('now', '+8 hours'))
                """,
                (row["id"], session_hash),
            )
            conn.commit()
            return session_hash
        finally:
            conn.close()

    def _current_admin(self) -> dict[str, Any] | None:
        cookie = self.headers.get("Cookie", "")
        if "lmcp_admin=" not in cookie:
            return None
        session_hash = cookie.split("lmcp_admin=", 1)[1].split(";", 1)[0]
        conn = self._connect()
        try:
            row = conn.execute(
                """
                select users.*
                from admin_sessions
                join users on users.id = admin_sessions.user_id
                where admin_sessions.session_hash = ?
                  and admin_sessions.expires_at > datetime('now')
                  and users.role = ?
                  and users.status = 'active'
                """,
                (session_hash, ROLE_ADMIN),
            ).fetchone()
            return dict(row) if row is not None else None
        finally:
            conn.close()

    def _users_html(self) -> str:
        conn = self._connect()
        try:
            rows = conn.execute("select email, display_name, role, status from users order by email").fetchall()
        finally:
            conn.close()
        items = "".join(
            f"<tr><td>{html.escape(row['email'])}</td><td>{html.escape(row['display_name'])}</td><td>{html.escape(row['role'])}</td><td>{html.escape(row['status'])}</td></tr>"
            for row in rows
        )
        return "<h1>Users</h1><table><tbody>" + items + "</tbody></table>"

    def _audit_html(self) -> str:
        conn = self._connect()
        try:
            rows = conn.execute("select tool_name, result_status, rationale from audit_events order by id desc limit 100").fetchall()
        finally:
            conn.close()
        items = "".join(
            f"<tr><td>{html.escape(row['tool_name'])}</td><td>{html.escape(row['result_status'])}</td><td>{html.escape(row['rationale'] or '')}</td></tr>"
            for row in rows
        )
        return "<h1>Audit</h1><table><tbody>" + items + "</tbody></table>"

    def _send_html(self, status: HTTPStatus, body: str) -> None:
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _redirect(self, path: str) -> None:
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", path)
        self.end_headers()

    def _redirect_with_cookie(self, path: str, session_hash: str) -> None:
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", path)
        self.send_header("Set-Cookie", f"lmcp_admin={session_hash}; HttpOnly; SameSite=Lax; Path=/")
        self.end_headers()


def _login_form(error: str = "") -> str:
    message = f"<p>{html.escape(error)}</p>" if error else ""
    return message + """
    <form method="post" action="/login">
      <input name="email" type="email" />
      <input name="password" type="password" />
      <button type="submit">Login</button>
    </form>
    """


def _page(title: str, body: str) -> str:
    return f"<!doctype html><html><head><title>{html.escape(title)}</title></head><body>{body}</body></html>"


def build_admin_server(*, host: str, port: int, database_path: str | Path) -> LegalMCPAdminServer:
    db.initialize_database(database_path)
    return LegalMCPAdminServer((host, port), LegalMCPAdminHandler, database_path=database_path)
```

- [ ] **Step 4: Run Admin Web tests**

Run:

```bash
python -m pytest tests/test_admin_server.py -v
```

Expected: tests pass.

- [ ] **Step 5: Commit Admin Web foundation**

Run:

```bash
git add src/legal_mcp/admin_server.py tests/test_admin_server.py
git commit -m "feat: add admin web foundation"
```

---

## Task 9: Add Admin Web User, Key, Grant, and Audit Handlers

**Files:**
- Modify: `src/legal_mcp/admin_server.py`
- Modify: `tests/test_admin_server.py`

- [ ] **Step 1: Add failing tests for creating user, key, and grant**

Extend `tests/test_admin_server.py` with a test that:

```python
def test_admin_can_create_business_user_and_grant_project(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    _admin_db(database_path)
    server = build_admin_server(host="127.0.0.1", port=0, database_path=database_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    opener = _opener()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        _post(opener, f"{base}/login", {"email": "admin@example.test", "password": "secret"})
        _post(
            opener,
            f"{base}/admin/users/create",
            {"email": "biz@example.test", "display_name": "Biz", "role": "business"},
        )
        users_html = opener.open(f"{base}/admin/users", timeout=5).read().decode("utf-8")
        assert "biz@example.test" in users_html

        conn = db.connect(database_path)
        try:
            user_id = conn.execute("select id from users where email = ?", ("biz@example.test",)).fetchone()["id"]
            project_id = conn.execute("select id from projects where project_code = ?", ("GAME-001",)).fetchone()["id"]
        finally:
            conn.close()
        _post(
            opener,
            f"{base}/admin/grants/create",
            {"user_id": str(user_id), "project_id": str(project_id)},
        )
        conn = db.connect(database_path)
        try:
            grant = conn.execute(
                "select * from project_access where user_id = ? and project_id = ?",
                (user_id, project_id),
            ).fetchone()
            assert grant is not None
        finally:
            conn.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
```

- [ ] **Step 2: Run Admin tests to verify they fail**

Run:

```bash
python -m pytest tests/test_admin_server.py -v
```

Expected: 404 for `/admin/users/create` or `/admin/grants/create`.

- [ ] **Step 3: Implement form handlers**

In `admin_server.py`, add `do_POST` branches after login:

```python
        user = self._current_admin()
        if user is None:
            self._redirect("/login")
            return
        if self.path == "/admin/users/create":
            self._create_user(form)
            self._redirect("/admin/users")
            return
        if self.path == "/admin/grants/create":
            self._create_grant(user, form)
            self._redirect("/admin/users")
            return
```

Add methods:

```python
    def _create_user(self, form: dict[str, str]) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """
                insert into users (email, display_name, role, status)
                values (?, ?, ?, 'active')
                """,
                (form["email"], form["display_name"], form["role"]),
            )
            conn.commit()
        finally:
            conn.close()

    def _create_grant(self, admin_user: dict[str, Any], form: dict[str, str]) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """
                insert or ignore into project_access (user_id, project_id, granted_by_user_id)
                values (?, ?, ?)
                """,
                (int(form["user_id"]), int(form["project_id"]), admin_user["id"]),
            )
            conn.commit()
        finally:
            conn.close()
```

Add simple forms to `_users_html` so manual testing can use the page:

```python
        form = """
        <form method="post" action="/admin/users/create">
          <input name="email" />
          <input name="display_name" />
          <select name="role">
            <option value="legal">legal</option>
            <option value="business">business</option>
            <option value="auditor">auditor</option>
            <option value="admin">admin</option>
          </select>
          <button type="submit">Create user</button>
        </form>
        """
```

- [ ] **Step 4: Run Admin tests**

Run:

```bash
python -m pytest tests/test_admin_server.py -v
```

Expected: all Admin tests pass.

- [ ] **Step 5: Commit Admin management handlers**

Run:

```bash
git add src/legal_mcp/admin_server.py tests/test_admin_server.py
git commit -m "feat: add admin user and grant management"
```

---

## Task 10: Add CLI Commands for Admin Bootstrap and Admin Server

**Files:**
- Modify: `src/legal_mcp/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing CLI parser tests**

Append to `tests/test_cli.py`:

```python
def test_admin_create_user_parser() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "admin",
            "create-user",
            "--email",
            "admin@example.test",
            "--display-name",
            "Admin",
            "--role",
            "admin",
            "--password",
            "secret",
        ]
    )

    assert args.command == "admin"
    assert args.admin_command == "create-user"
    assert args.email == "admin@example.test"
    assert args.role == "admin"


def test_serve_admin_parser() -> None:
    parser = build_parser()
    args = parser.parse_args(["serve-admin", "--host", "0.0.0.0", "--port", "8766"])

    assert args.command == "serve-admin"
    assert args.host == "0.0.0.0"
    assert args.port == 8766
```

- [ ] **Step 2: Run CLI tests to verify they fail**

Run:

```bash
python -m pytest tests/test_cli.py -v
```

Expected: parser rejects `admin` or `serve-admin`.

- [ ] **Step 3: Add CLI subcommands**

In `build_parser`, add:

```python
    serve_admin_parser = subparsers.add_parser("serve-admin", help="Run the Legal-MCP Admin Web server")
    serve_admin_parser.add_argument("--host", default="127.0.0.1")
    serve_admin_parser.add_argument("--port", type=int, default=8766)
    serve_admin_parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DATABASE_PATH,
        help=f"SQLite database path (default: {DEFAULT_DATABASE_PATH})",
    )

    admin_parser = subparsers.add_parser("admin", help="Manage Legal-MCP local users")
    admin_subparsers = admin_parser.add_subparsers(dest="admin_command")
    create_user_parser = admin_subparsers.add_parser("create-user", help="Create a local Legal-MCP user")
    create_user_parser.add_argument("--email", required=True)
    create_user_parser.add_argument("--display-name", required=True)
    create_user_parser.add_argument("--role", required=True, choices=("admin", "legal", "business", "auditor"))
    create_user_parser.add_argument("--password")
    create_user_parser.add_argument("--db", type=Path, default=DEFAULT_DATABASE_PATH)
```

In `main`, add:

```python
    if args.command == "serve-admin":
        from legal_mcp.admin_server import build_admin_server

        server = build_admin_server(host=args.host, port=args.port, database_path=args.db)
        try:
            server.serve_forever()
        finally:
            server.server_close()
        return 0
    if args.command == "admin" and args.admin_command == "create-user":
        from legal_mcp import db
        from legal_mcp.identity import create_user, hash_password

        db.initialize_database(args.db)
        conn = db.connect(args.db)
        try:
            user = create_user(
                conn,
                email=args.email,
                display_name=args.display_name,
                role=args.role,
                password_hash=hash_password(args.password) if args.password else None,
            )
        finally:
            conn.close()
        print(f"Created user {user['email']} ({user['role']})")
        return 0
```

- [ ] **Step 4: Run CLI tests**

Run:

```bash
python -m pytest tests/test_cli.py -v
```

Expected: all CLI tests pass.

- [ ] **Step 5: Commit CLI commands**

Run:

```bash
git add src/legal_mcp/cli.py tests/test_cli.py
git commit -m "feat: add admin CLI commands"
```

---

## Task 11: Documentation and TODO Updates

**Files:**
- Modify: `README.md`
- Modify: `Docs/team-deployment.md`
- Modify: `TODOS.md`

- [ ] **Step 1: Update README team deployment section**

In `README.md`, add a v1.2 named-user setup after the existing v1.1 shared token example:

````markdown
### v1.2 named user tokens

For enterprise pilots, prefer one Legal-MCP user and API key per person or AI client instead of sharing a single `LEGAL_MCP_TOKEN`.

Create the first admin:

```sh
legal-mcp admin create-user \
  --email admin@example.com \
  --display-name "Legal MCP Admin" \
  --role admin \
  --password "replace-this-password" \
  --db /data/legal.db
```

Start the Admin Web UI:

```sh
legal-mcp serve-admin --host 0.0.0.0 --port 8766 --db /data/legal.db
```

Then use the Admin Web UI to create legal, business, and auditor users, generate API keys, and grant projects to business users.
````

- [ ] **Step 2: Update team deployment runbook**

In `Docs/team-deployment.md`, add:

```markdown
## v1.2 enterprise permissions

v1.1 shared-token mode remains available for trusted pilots. v1.2 enterprise mode uses named Legal-MCP users and per-user API keys.

Roles:

- `admin`: manages users, API keys, grants, and audit views.
- `legal`: can query all projects.
- `business`: can query only granted projects.
- `auditor`: can view audit records but cannot query project content through MCP.

Business users start with no project visibility. Grant project access in the Admin Web UI before giving them an API key.
```

- [ ] **Step 3: Update TODOs**

In `TODOS.md`, update the "Enterprise permissions and audit console" section with:

```markdown
**v1.2 status:** Planned as "Enterprise Project Permissions and Audit Console": local users, per-user API keys, project-level access grants, database-backed disclosure audit, and a lightweight Admin Web UI. Field-level permissions and SSO remain post-v1.2.
```

- [ ] **Step 4: Run documentation-sensitive tests**

Run:

```bash
python -m pytest tests/test_team_deployment_docs.py tests/test_installer_docs.py -v
```

Expected: all documentation tests pass. If they fail because expected snippets changed, update only the affected assertions.

- [ ] **Step 5: Commit docs**

Run:

```bash
git add README.md Docs/team-deployment.md TODOS.md tests/test_team_deployment_docs.py tests/test_installer_docs.py
git commit -m "docs: document v1.2 enterprise permissions"
```

---

## Task 12: Full Verification and Handoff

**Files:**
- No planned source changes unless verification exposes a defect

- [ ] **Step 1: Run full test suite**

Run:

```bash
python -m pytest
```

Expected:

```text
... passed ...
```

- [ ] **Step 2: Run manual smoke test for HTTP MCP named token**

Run:

```bash
tmpdir=$(mktemp -d)
db_path="$tmpdir/legal.db"
python - <<'PY' "$db_path"
import sys
from legal_mcp import db
from legal_mcp.identity import create_api_key, create_user

path = sys.argv[1]
db.initialize_database(path)
conn = db.connect(path)
try:
    conn.execute("insert into projects (project_code, name, stage) values (?, ?, ?)", ("GAME-001", "Project One", "live"))
    user = create_user(conn, email="legal@example.test", display_name="Legal", role="legal")
    key = create_api_key(conn, user_id=user["id"], label="smoke")
    print(key.plaintext)
finally:
    conn.close()
PY
```

Expected: a token beginning with `lmcp_`.

- [ ] **Step 3: Check git history and status**

Run:

```bash
git status --short
git log --oneline --max-count=12
```

Expected:

```text
```

for `git status --short`, meaning the worktree is clean.

- [ ] **Step 4: Prepare PR summary**

Write a concise summary:

```markdown
## Summary
- Added local users, API keys, project grants, and v1.2 schema.
- Enforced project-level MCP permissions by authenticated user.
- Added database-backed audit events/disclosures and a lightweight Admin Web UI.

## Tests
- python -m pytest
```

- [ ] **Step 5: Stop before merge**

Do not merge into `main` from the worktree. Hand off the branch name and worktree path:

```text
Branch: codex/enterprise-project-permissions-audit-v1-2
Worktree: /Users/haoran/workspace/Legal-MCP/.worktrees/enterprise-project-permissions-audit-v1-2
```

---

## Self-Review Notes

Spec coverage:

- Named users and API keys: Tasks 1, 2, 6, 10.
- Project-level authorization: Tasks 1, 3, 5.
- Legal default all-project visibility: Task 3 and Task 5 tests.
- Business explicit grants: Task 3, Task 5, Task 9.
- Auditor no MCP content access: Task 3 and Task 5 tests.
- Admin Web UI: Tasks 8 and 9.
- Database-backed audit: Tasks 1, 4, 7.
- v1.1 compatibility: Task 6 keeps legacy bearer token path.
- Documentation: Task 11.
- Worktree and branch isolation: Task 0.

Scope exclusions preserved:

- No field-level permissions.
- No SSO/OIDC implementation.
- No project/contract/license/risk data editing UI.
- No OCR/PDF or external connectors.
