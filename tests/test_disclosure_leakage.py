"""Permanent leakage red-team gate (pivot 阶段2, see plan §6 阶段2 #7, §12.4).

Invariant under test: an unauthorized field's *value* must never appear in the
output, whether the attacker over-requests it, asks for it alone, or relies on
field projection. A single regression here destroys the project's credibility, so
this file is a CI gate, not an optional extra.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from legal_mcp import db
from legal_mcp.connector_retrieval import execute_connector_plan
from legal_mcp.connectors.base import (
    ConnectorDomain,
    ConnectorField,
    ConnectorQuery,
    RecordScope,
)
from legal_mcp.connectors.sqlite_demo import SqliteDemoConnector
from legal_mcp.identity import ROLE_BUSINESS, create_user
from legal_mcp.policy import AccessContext
from legal_mcp.query_plan import QueryFilter, QueryPlan
from legal_mcp.search_tools import execute_search_plan

SECRET_WEBSITE = "https://SECRET-RED-TEAM.example"
SECRET_BP = "SECRET-BP-9999"
SECRET_SALARY = "SECRET-SALARY-424242"
SECRET_PEER_ENTRY = "SECRET-PEER-JOURNAL-7777"


def _seed_business_user_without_sensitive_fields(tmp_path: Path):
    """Business user has project access and may read only project_code + name.

    The project stores secrets in `website` and `legal_bp`, which the user must
    never be able to extract.
    """
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    project_id = conn.execute(
        "insert into projects (project_code, name, stage, legal_bp, website) "
        "values (?, ?, ?, ?, ?)",
        ("ACME", "Demo Project", "live", SECRET_BP, SECRET_WEBSITE),
    ).lastrowid
    user = create_user(
        conn, email="redteam@example.com", display_name="Business", role=ROLE_BUSINESS
    )
    conn.execute(
        "insert into project_access (user_id, project_id, granted_by_user_id) "
        "values (?, ?, ?)",
        (user["id"], project_id, user["id"]),
    )
    group_id = conn.execute(
        "insert into user_groups (name) values (?)", ("redteam-group",)
    ).lastrowid
    conn.execute(
        "insert into user_group_memberships (user_id, group_id) values (?, ?)",
        (user["id"], group_id),
    )
    for field in ("project_code", "name"):
        conn.execute(
            "insert into permission_grants "
            "(group_id, operation, data_domain, field_name, project_id) "
            "values (?, 'read', 'project', ?, ?)",
            (group_id, field, project_id),
        )
    conn.commit()
    return conn, AccessContext.from_user(user)


def _blob(*responses: object) -> str:
    return "".join(json.dumps(r, ensure_ascii=False, default=str) for r in responses)


def test_unauthorized_field_value_never_leaks(tmp_path: Path) -> None:
    conn, context = _seed_business_user_without_sensitive_fields(tmp_path)
    try:
        # Attack A: over-request the denied field alongside allowed ones.
        over_request = execute_search_plan(
            conn,
            QueryPlan(
                domain="project",
                operation="search",
                filters=[QueryFilter(field="project_code", operator="eq", value="ACME")],
                return_fields=["project_code", "name", "website"],
                limit=20,
            ),
            access_context=context,
        )
        # Attack B: request the denied field alone.
        denied_only = execute_search_plan(
            conn,
            QueryPlan(
                domain="project",
                operation="search",
                filters=[QueryFilter(field="project_code", operator="eq", value="ACME")],
                return_fields=["legal_bp"],
                limit=20,
            ),
            access_context=context,
        )
        # Attack C: request only allowed fields — projection must drop secrets.
        allowed_only = execute_search_plan(
            conn,
            QueryPlan(
                domain="project",
                operation="search",
                filters=[QueryFilter(field="project_code", operator="eq", value="ACME")],
                return_fields=["project_code", "name"],
                limit=20,
            ),
            access_context=context,
        )
    finally:
        conn.close()

    blob = _blob(over_request, denied_only, allowed_only)
    assert SECRET_WEBSITE not in blob
    assert SECRET_BP not in blob

    # Over-requesting / requesting a denied field must be refused, not silently
    # dropped — a silent drop would hide that the client asked for forbidden data.
    assert "error" in over_request
    assert "error" in denied_only

    # The allowed query still works and returns the non-secret identity fields.
    assert allowed_only.get("projects") == [{"project_code": "ACME", "name": "Demo Project"}]


def test_identity_candidate_list_never_leaks_secret_field(tmp_path: Path) -> None:
    """v0.4.8: an identity query that produces a candidate list must not become a
    side channel for an ungranted field. Requesting a denied field is refused on
    both paths exactly as a normal query is, so no candidate ever carries it."""
    conn, context = _seed_business_user_without_sensitive_fields(tmp_path)
    # A second project so a bare token could form a multi-row candidate list.
    conn.execute(
        "insert into projects (project_code, name, stage, legal_bp, website) "
        "values (?, ?, ?, ?, ?)",
        ("ACE2", "Demo Project Two", "live", SECRET_BP, SECRET_WEBSITE),
    )
    conn.commit()
    plan = QueryPlan(
        domain="project",
        operation="search",
        filters=[QueryFilter(field="identity", operator="contains", value="Demo")],
        return_fields=["legal_bp"],  # denied
        limit=20,
    )
    try:
        sql_result = execute_search_plan(conn, plan, access_context=context)
        connector_result = execute_connector_plan(
            SqliteDemoConnector(tmp_path / "legal.db"), plan, conn=conn, access_context=context
        )
    finally:
        conn.close()
    blob = _blob(sql_result, connector_result)
    assert SECRET_BP not in blob
    assert SECRET_WEBSITE not in blob
    # Refused, not silently downgraded to a code+name candidate list.
    assert sql_result["error"]["code"] == "return_field_access_denied"
    assert connector_result["error"]["code"] == "return_field_access_denied"


# --- Security gate (v0.4.0 §C/§D): the same invariant on an ARBITRARY, ----------
# non-project domain served through a connector with record_scope.mode = none.
# The leakage gate must hold for any domain, not just the legacy legal tables.

_STAFFING_DOMAIN = ConnectorDomain(
    name="staffing",
    table="tblStaffing",
    fields=(
        ConnectorField(domain="staffing", name="member", is_identity=True),
        ConnectorField(domain="staffing", name="task"),
        ConnectorField(domain="staffing", name="salary"),  # the secret
    ),
    record_scope=RecordScope(mode="none"),
)


class _FakeStaffingConnector:
    """A non-project source whose rows carry a secret in an ungranted field."""

    name = "fake_staffing"

    def __init__(self) -> None:
        self.last_query: ConnectorQuery | None = None

    def catalog(self) -> tuple[ConnectorDomain, ...]:
        return (_STAFFING_DOMAIN,)

    def query(self, query: ConnectorQuery) -> list[dict[str, Any]]:
        self.last_query = query
        row = {"member": "Alice", "task": "drafting", "salary": SECRET_SALARY}
        return [{k: row[k] for k in query.fields if k in row}]


def _seed_staffing_user_without_salary(tmp_path: Path):
    """A business user with a global (project_id NULL) grant on staffing.task only
    — and no grant for staffing.salary, the secret field."""
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    user = create_user(
        conn, email="staffing-redteam@example.com", display_name="Business", role=ROLE_BUSINESS
    )
    group_id = conn.execute(
        "insert into user_groups (name) values (?)", ("staffing-redteam-group",)
    ).lastrowid
    conn.execute(
        "insert into user_group_memberships (user_id, group_id) values (?, ?)",
        (user["id"], group_id),
    )
    conn.execute(
        "insert into permission_grants "
        "(group_id, operation, data_domain, field_name, project_id) "
        "values (?, 'read', 'staffing', 'task', null)",  # task only, NOT salary
        (group_id,),
    )
    conn.commit()
    return conn, AccessContext.from_user(user)


def _staffing_plan(return_fields: list[str]) -> QueryPlan:
    return QueryPlan(
        domain="staffing",
        operation="search",
        filters=[QueryFilter(field="member", operator="eq", value="Alice")],
        return_fields=return_fields,
        limit=20,
    )


def test_unauthorized_field_value_never_leaks_for_arbitrary_domain(tmp_path: Path) -> None:
    conn, context = _seed_staffing_user_without_salary(tmp_path)
    connector = _FakeStaffingConnector()
    try:
        # Attack A: over-request the denied field (salary) alongside the allowed one.
        over_request = execute_connector_plan(
            connector, _staffing_plan(["task", "salary"]), conn=conn, access_context=context
        )
        # Attack B: request the denied field alone.
        denied_only = execute_connector_plan(
            connector, _staffing_plan(["salary"]), conn=conn, access_context=context
        )
        # Attack C: request only the allowed field — projection must drop the secret.
        allowed_only = execute_connector_plan(
            connector, _staffing_plan(["task"]), conn=conn, access_context=context
        )
    finally:
        conn.close()

    blob = _blob(over_request, denied_only, allowed_only)
    assert SECRET_SALARY not in blob

    # Denied requests are refused, not silently dropped.
    assert "error" in over_request
    assert "error" in denied_only
    # The allowed query works and returns only the non-secret field.
    assert allowed_only == {"staffing": [{"task": "drafting"}]}
    # The connector was never asked to fetch the secret field for the allowed query.
    assert connector.last_query is not None
    assert "salary" not in connector.last_query.fields


# --- Security gate (v0.4.5 Phase 4): a by_owner domain must never leak a PEER's ---
# row value, even when the requester over-requests, filters for the peer, or relies
# on the connector returning broad rows.

_JOURNAL_DOMAIN = ConnectorDomain(
    name="journal",
    table="tblJournal",
    fields=(
        ConnectorField(domain="journal", name="owner", is_identity=True),
        ConnectorField(domain="journal", name="entry"),
    ),
    record_scope=RecordScope(mode="by_owner", field="owner", subject="external_subject"),
)


class _BroadJournalConnector:
    """A source whose service credentials return *everyone's* rows — the gateway,
    not the source, must keep a requester to their own."""

    name = "fake_journal"

    def __init__(self) -> None:
        self.last_query: ConnectorQuery | None = None
        self._rows = [
            {"owner": "oidc|alice", "entry": "alice-note"},
            {"owner": "oidc|bob", "entry": SECRET_PEER_ENTRY},
        ]

    def catalog(self) -> tuple[ConnectorDomain, ...]:
        return (_JOURNAL_DOMAIN,)

    def query(self, query: ConnectorQuery) -> list[dict[str, Any]]:
        self.last_query = query
        matched = [
            row
            for row in self._rows
            if all(
                str(row.get(f.field, "")) == str(f.value)
                for f in query.filters
                if f.operator == "eq"
            )
        ]
        return [{k: row[k] for k in query.fields if k in row} for row in matched[: query.limit]]


def _seed_journal_owner(tmp_path: Path):
    """alice, granted the journal.entry field, carrying her external_subject."""
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    user = create_user(
        conn,
        email="alice-journal@example.com",
        display_name="Alice",
        role=ROLE_BUSINESS,
        external_subject="oidc|alice",
    )
    group_id = conn.execute(
        "insert into user_groups (name) values (?)", ("journal-group",)
    ).lastrowid
    conn.execute(
        "insert into user_group_memberships (user_id, group_id) values (?, ?)",
        (user["id"], group_id),
    )
    conn.execute(
        "insert into permission_grants "
        "(group_id, operation, data_domain, field_name, project_id) "
        "values (?, 'read', 'journal', 'entry', null)",
        (group_id,),
    )
    conn.commit()
    return conn, AccessContext.from_user(user)


def test_by_owner_never_leaks_a_peers_row_value(tmp_path: Path) -> None:
    conn, context = _seed_journal_owner(tmp_path)
    connector = _BroadJournalConnector()
    try:
        # Attack A: plain query — must return only alice's own row.
        own = execute_connector_plan(
            connector,
            QueryPlan(domain="journal", operation="search", filters=[],
                      return_fields=["entry"], limit=20),
            conn=conn, access_context=context,
        )
        # Attack B: filter for the peer's subject — the pushdown override wins.
        spoof_filter = execute_connector_plan(
            connector,
            QueryPlan(
                domain="journal", operation="search",
                filters=[QueryFilter(field="owner", operator="eq", value="oidc|bob")],
                return_fields=["entry"], limit=20,
            ),
            conn=conn, access_context=context,
        )
        # Attack C: also request the owner column itself.
        with_owner = execute_connector_plan(
            connector,
            QueryPlan(domain="journal", operation="search",
                      filters=[QueryFilter(field="owner", operator="eq", value="oidc|bob")],
                      return_fields=["owner", "entry"], limit=20),
            conn=conn, access_context=context,
        )
    finally:
        conn.close()

    blob = _blob(own, spoof_filter, with_owner)
    # The peer's row value must never appear, by any of the three routes.
    assert SECRET_PEER_ENTRY not in blob
    assert "oidc|bob" not in blob
    # alice still sees her own row on the plain query.
    assert own == {"journal": [{"entry": "alice-note"}]}
