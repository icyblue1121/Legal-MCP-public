"""SQLite database helpers for Legal-MCP."""

from __future__ import annotations

import sqlite3
from importlib import resources
from pathlib import Path
from typing import TypeAlias

DatabasePath: TypeAlias = str | Path

# Schema table groups (pivot 阶段4, plan §4.1/§4.2). The gateway legitimately
# holds GOVERNANCE tables. DEMO_SOURCE tables are reference legal facts that a
# real deployment serves from its own system through a connector — they are not
# canonical business data owned here. Kept in sync with schema.sql and asserted
# in tests/test_schema.py, so a new unclassified table fails CI.
GOVERNANCE_TABLES: frozenset[str] = frozenset(
    {
        "schema_version",
        "users",
        "api_keys",
        "user_groups",
        "user_group_memberships",
        "permission_grants",
        "admin_sessions",
        "audit_events",
        "audit_disclosures",
        "audit_event_details",
        "agent_runs",
        "agent_steps",
        "agent_turn_context",
        "agent_settings",
        "deployment_settings",
        "data_source_state",
        "field_semantics",
        "data_sources",
    }
)

# `project_access` and `project_aliases` are demo-source today because they bind
# to legal projects; the plan (§6 阶段4) migrates project_access into the generic
# policy/grant system later so authorization stops depending on demo facts.
DEMO_SOURCE_TABLES: frozenset[str] = frozenset(
    {
        "projects",
        "contracts",
        "licenses",
        "risks",
        "companies",
        "company_seals",
        "project_access",
        "company_access",
        "project_aliases",
    }
)


def connect(database_path: DatabasePath) -> sqlite3.Connection:
    """Open a SQLite connection with project-required defaults."""
    conn = sqlite3.connect(database_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def initialize_schema(conn: sqlite3.Connection) -> None:
    """Apply the canonical SQLite schema to an open connection."""
    conn.execute("PRAGMA foreign_keys = ON")
    schema_sql = (
        resources.files("legal_mcp")
        .joinpath("schema.sql")
        .read_text(encoding="utf-8")
    )
    # SQLite cannot relax a column's NOT NULL in place, so the per-user-grant
    # change to permission_grants (v0.4.0 §C C3: group_id becomes nullable, add
    # user_id) needs a table rebuild on already-deployed databases. Rename the
    # legacy table out of the way *before* executescript so the script's
    # create-if-not-exists builds the new shape (no duplicated DDL here), then
    # copy the rows back. The new-table create is the single source of truth.
    pending_grant_rebuild = _begin_permission_grants_rebuild(conn)
    pending_steps_rebuild = _begin_agent_steps_rebuild(conn)
    conn.executescript(schema_sql)
    if pending_grant_rebuild:
        _finish_permission_grants_rebuild(conn)
    if pending_steps_rebuild:
        _finish_agent_steps_rebuild(conn)
    _ensure_audit_identity_source_column(conn)
    conn.commit()


def _ensure_audit_identity_source_column(conn: sqlite3.Connection) -> None:
    """Add ``audit_events.identity_source`` to a pre-v21 database (v0.4.5 Phase 2).

    ``create table if not exists`` cannot add a column to an existing table, so an
    already-deployed audit_events needs an explicit ADD COLUMN. ADD COLUMN is the
    safe, non-destructive SQLite migration (unlike the permission_grants NOT-NULL
    relax that forced a rebuild); it appends the column, matching schema.sql's
    column order. Idempotent: a fresh DB already has the column and is skipped.
    """
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(audit_events)")}
    if "identity_source" not in columns:
        conn.execute("alter table audit_events add column identity_source text")


def _begin_permission_grants_rebuild(conn: sqlite3.Connection) -> bool:
    """Stage a permission_grants rebuild if the table predates per-user grants.

    Idempotent and resumable: a leftover ``permission_grants_legacy`` (an
    interrupted earlier run) resumes the copy; an already-migrated table (it has
    a ``user_id`` column) is left untouched. Returns whether a copy is pending.
    """
    legacy = conn.execute(
        "select 1 from sqlite_master where type='table' "
        "and name='permission_grants_legacy'"
    ).fetchone()
    if legacy is not None:
        return True
    table = conn.execute(
        "select 1 from sqlite_master where type='table' "
        "and name='permission_grants'"
    ).fetchone()
    if table is None:
        return False  # fresh database — executescript creates the new shape
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(permission_grants)")}
    if "user_id" in columns:
        return False  # already migrated
    conn.execute("alter table permission_grants rename to permission_grants_legacy")
    # Free the index name so executescript can recreate it on the new table.
    conn.execute("drop index if exists idx_permission_grants_group_id")
    return True


def _finish_permission_grants_rebuild(conn: sqlite3.Connection) -> None:
    """Copy legacy grant rows into the rebuilt table and drop the legacy table."""
    conn.execute(
        """
        insert or ignore into permission_grants
          (id, group_id, user_id, operation, data_domain, field_name,
           project_id, allowed, created_at)
        select id, group_id, null, operation, data_domain, field_name,
               project_id, allowed, created_at
        from permission_grants_legacy
        """
    )
    conn.execute("drop table permission_grants_legacy")


def _begin_agent_steps_rebuild(conn: sqlite3.Connection) -> bool:
    """Stage an agent_steps rebuild if the table predates per-turn audit (v0.4.6 §F).

    The unique key changed from ``(thread_id, step_index)`` to
    ``(thread_id, turn_id, step_index)`` and a non-null ``turn_id`` was added.
    SQLite cannot alter a unique constraint in place, so an already-deployed
    agent_steps is renamed out of the way and rebuilt by executescript, then rows
    are copied with a synthetic per-row ``turn_id`` (so legacy rows never collide).
    Idempotent and resumable, mirroring the permission_grants rebuild.
    """
    legacy = conn.execute(
        "select 1 from sqlite_master where type='table' and name='agent_steps_legacy'"
    ).fetchone()
    if legacy is not None:
        return True
    table = conn.execute(
        "select 1 from sqlite_master where type='table' and name='agent_steps'"
    ).fetchone()
    if table is None:
        return False  # fresh database — executescript creates the new shape
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(agent_steps)")}
    if "turn_id" in columns:
        return False  # already migrated
    conn.execute("alter table agent_steps rename to agent_steps_legacy")
    # Free the index names so executescript can recreate them on the new table.
    conn.execute("drop index if exists idx_agent_steps_thread_id")
    conn.execute("drop index if exists idx_agent_steps_status")
    return True


def _finish_agent_steps_rebuild(conn: sqlite3.Connection) -> None:
    """Copy legacy step rows into the rebuilt table, backfilling per-row turn ids."""
    conn.execute(
        """
        insert or ignore into agent_steps
          (id, thread_id, turn_id, step_index, planner_source, status, model,
           reason, plan_json, error_code, error_message, created_at)
        select id, thread_id, 'legacy-' || id, step_index, planner_source, status,
               model, reason, plan_json, error_code, error_message, created_at
        from agent_steps_legacy
        """
    )
    conn.execute("drop table agent_steps_legacy")


def disabled_data_sources(conn: sqlite3.Connection) -> set[str]:
    """Names of declared data sources an operator has disconnected (v0.4.0 §C C5).

    A disabled source's domains are dropped from the live catalog so queries
    against them fail closed. Absent from this set = enabled (the default).
    """
    try:
        rows = conn.execute(
            "select source_name from data_source_state where disabled = 1"
        ).fetchall()
    except sqlite3.OperationalError:
        return set()  # table predates C5; treat everything as enabled
    return {str(row["source_name"]) for row in rows}


def set_data_source_disabled(
    conn: sqlite3.Connection,
    source_name: str,
    *,
    disabled: bool,
    updated_by: str | None = None,
) -> None:
    """Connect (``disabled=False``) or disconnect (``disabled=True``) a source."""
    conn.execute(
        """
        insert into data_source_state (source_name, disabled, updated_at, updated_by)
        values (?, ?, datetime('now'), ?)
        on conflict(source_name) do update set
          disabled = excluded.disabled,
          updated_at = excluded.updated_at,
          updated_by = excluded.updated_by
        """,
        (source_name, 1 if disabled else 0, updated_by),
    )
    conn.commit()


def active_data_sources(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Runtime-registered sources that are live (v0.5.6).

    Only ``status = 'active'`` rows join the live catalog; ``draft`` / ``disabled``
    rows are excluded so a newly added source is off until explicitly enabled.
    Returns rows in insertion order (declaration order = primary-first fallback).
    An older DB without the table yields an empty list.
    """
    try:
        return conn.execute(
            "select name, type, config_json, secret_ref, updated_at "
            "from data_sources where status = 'active' order by id"
        ).fetchall()
    except sqlite3.OperationalError:
        return []


_DATA_SOURCE_STATUSES = frozenset({"draft", "active", "disabled"})


def list_data_sources(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """All runtime-registered sources, any status, for the admin CRUD view (v0.5.8)."""
    try:
        return conn.execute(
            "select id, name, type, status, config_json, secret_ref, updated_at "
            "from data_sources order by name"
        ).fetchall()
    except sqlite3.OperationalError:
        return []


def set_data_source_status(conn: sqlite3.Connection, name: str, *, status: str) -> bool:
    """Enable/disable/draft a registered source (v0.5.8). Returns False if absent.

    Only ``active`` sources join the live catalog, so flipping to ``disabled``
    takes a domain out without deleting its reviewed declaration."""
    if status not in _DATA_SOURCE_STATUSES:
        raise ValueError(f"unknown data_source status: {status!r}")
    cursor = conn.execute(
        "update data_sources set status = ?, updated_at = datetime('now') where name = ?",
        (status, name),
    )
    conn.commit()
    return cursor.rowcount > 0


def delete_data_source(conn: sqlite3.Connection, name: str) -> bool:
    """Remove a registered source entirely (v0.5.8). Returns False if absent.

    Its domain leaves the live catalog on the next request (fail-closed). The
    reviewed declaration is gone — re-add via the wizard to bring it back."""
    cursor = conn.execute("delete from data_sources where name = ?", (name,))
    conn.commit()
    return cursor.rowcount > 0


def data_sources_fingerprint(conn: sqlite3.Connection) -> tuple[int, str]:
    """A cheap change key over the active sources, for hot-reload caching (v0.5.6).

    ``(count, max(updated_at))`` changes whenever a source is added, removed,
    enabled/disabled, or edited (its ``updated_at`` bumped), so a cached effective
    setup can be invalidated without rebuilding connectors every request.
    """
    try:
        row = conn.execute(
            "select count(*) as c, coalesce(max(updated_at), '') as m "
            "from data_sources where status = 'active'"
        ).fetchone()
    except sqlite3.OperationalError:
        return (0, "")
    return (int(row["c"]), str(row["m"]))


def initialize_database(database_path: DatabasePath) -> None:
    """Create or update a database file with the canonical schema."""
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = connect(path)
    try:
        initialize_schema(conn)
    finally:
        conn.close()
