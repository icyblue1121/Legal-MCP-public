"""Connector-backed retrieval gateway (pivot v0.3).

These tests pin the connector retrieval path: it must apply the *same* field gate
and the *same* record scope as the SQLite path, but source rows from a connector.
The crown jewel is the parity test — on the bundled SQLite demo, the connector
path and ``execute_search_plan`` must return identical rows for the same plan and
access context. That equivalence is what lets a real source (Feishu) swap in
without changing the authorization model.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from legal_mcp import db
from legal_mcp.connector_retrieval import execute_connector_plan
from legal_mcp.connectors.base import (
    ConnectorDomain,
    ConnectorField,
    ConnectorFilter,
    ConnectorQuery,
    RecordScope,
)
from legal_mcp.connectors.sqlite_demo import SqliteDemoConnector
from legal_mcp.identity import ROLE_BUSINESS, ROLE_LEGAL, create_api_key, create_user
from legal_mcp.policy import AccessContext
from legal_mcp.query_plan import QueryFilter, QueryPlan
from legal_mcp.search_tools import execute_search_plan


# --- governance seed (mirrors examples/legal-demo/seed_server_db.py) ---------


def _seed(tmp_path: Path) -> Path:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    return database_path


def _insert_project(conn: sqlite3.Connection, code: str, name: str, legal_bp: str) -> int:
    cursor = conn.execute(
        "insert into projects (project_code, name, stage, contact_person, legal_bp) "
        "values (?, ?, ?, ?, ?)",
        (code, name, "live", f"contact-{code}", legal_bp),
    )
    return int(cursor.lastrowid)


def _grant_fields(conn: sqlite3.Connection, user_id: int, domain: str, fields: tuple[str, ...]) -> None:
    group_id = conn.execute(
        "insert into user_groups (name) values (?)", (f"grp-{user_id}-{domain}",)
    ).lastrowid
    conn.execute(
        "insert into user_group_memberships (user_id, group_id) values (?, ?)",
        (user_id, group_id),
    )
    for field in fields:
        conn.execute(
            "insert into permission_grants "
            "(group_id, operation, data_domain, field_name, project_id) "
            "values (?, ?, ?, ?, ?)",
            (group_id, "read", domain, field, None),
        )


def _grant_projects(conn: sqlite3.Connection, user_id: int, project_ids: list[int]) -> None:
    for project_id in project_ids:
        conn.execute(
            "insert into project_access (user_id, project_id, granted_by_user_id) "
            "values (?, ?, ?)",
            (user_id, project_id, user_id),
        )


def _legal_user_with_all(tmp_path: Path) -> tuple[Path, AccessContext]:
    """Legal user granted every project + the legal_bp/contact_person fields."""
    database_path = _seed(tmp_path)
    conn = db.connect(database_path)
    try:
        ids = [
            _insert_project(conn, "MOON", "Moon Project", "BP-Morgan"),
            _insert_project(conn, "STAR", "Star Project", "BP-Stark"),
        ]
        user = create_user(conn, email="l@x.local", display_name="L", role=ROLE_LEGAL)
        _grant_projects(conn, user["id"], ids)
        _grant_fields(conn, user["id"], "project", ("contact_person", "legal_bp"))
        conn.commit()
        context = AccessContext(user_id=int(user["id"]), role=ROLE_LEGAL)
    finally:
        conn.close()
    return database_path, context


def _plan(value: str = "MOON", fields: tuple[str, ...] = ("legal_bp",)) -> QueryPlan:
    return QueryPlan(
        domain="project",
        operation="search",
        filters=[QueryFilter(field="project_code", operator="eq", value=value)],
        return_fields=list(fields),
        limit=20,
    )


# --- fake connector for scope/projection unit tests --------------------------


class _FakeConnector:
    name = "fake"

    def __init__(
        self,
        rows: list[dict[str, Any]],
        domain: ConnectorDomain | None = None,
    ) -> None:
        self._rows = rows
        # The retrieval path now resolves a domain's record scope from the
        # connector catalog, so a fake must declare its domain. Default to a
        # ``project`` domain with today's by_governed_code scope.
        self._domain = domain or ConnectorDomain(
            name="project",
            table="projects",
            fields=(
                ConnectorField(domain="project", name="project_code", is_identity=True),
                ConnectorField(domain="project", name="legal_bp"),
            ),
        )
        self.last_query: ConnectorQuery | None = None

    def catalog(self) -> tuple[ConnectorDomain, ...]:
        return (self._domain,)

    def query(self, query: ConnectorQuery) -> list[dict[str, Any]]:
        self.last_query = query
        # Project canned rows to the requested fields, like a real connector.
        return [{k: row[k] for k in query.fields if k in row} for row in self._rows]


# --- parity: connector path == SQLite path on the demo source ----------------


def test_parity_with_search_tools_for_visible_project(tmp_path: Path) -> None:
    database_path, context = _legal_user_with_all(tmp_path)
    plan = _plan("MOON", ("legal_bp", "contact_person"))
    conn = db.connect(database_path)
    try:
        sql_result = execute_search_plan(conn, plan, access_context=context)
        connector_result = execute_connector_plan(
            SqliteDemoConnector(database_path), plan, conn=conn, access_context=context
        )
    finally:
        conn.close()
    assert "error" not in sql_result
    assert sql_result["projects"] == [{"legal_bp": "BP-Morgan", "contact_person": "contact-MOON"}]
    assert connector_result == sql_result


def test_parity_denies_out_of_scope_project(tmp_path: Path) -> None:
    """A business user with no project access sees nothing on either path."""
    database_path = _seed(tmp_path)
    conn = db.connect(database_path)
    try:
        _insert_project(conn, "MOON", "Moon Project", "BP-Morgan")
        user = create_user(conn, email="b@x.local", display_name="B", role=ROLE_BUSINESS)
        _grant_fields(conn, user["id"], "project", ("contact_person",))
        conn.commit()
        context = AccessContext(user_id=int(user["id"]), role=ROLE_BUSINESS)
        plan = _plan("MOON", ("contact_person",))

        sql_result = execute_search_plan(conn, plan, access_context=context)
        connector_result = execute_connector_plan(
            SqliteDemoConnector(database_path), plan, conn=conn, access_context=context
        )
    finally:
        conn.close()
    assert sql_result == {"projects": []}
    assert connector_result == {"projects": []}


# --- field gate --------------------------------------------------------------


def test_denies_ungranted_return_field(tmp_path: Path) -> None:
    database_path = _seed(tmp_path)
    conn = db.connect(database_path)
    try:
        _insert_project(conn, "MOON", "Moon Project", "BP-Morgan")
        user = create_user(conn, email="b@x.local", display_name="B", role=ROLE_BUSINESS)
        _grant_projects(conn, user["id"], [1])
        _grant_fields(conn, user["id"], "project", ("contact_person",))  # NOT legal_bp
        conn.commit()
        context = AccessContext(user_id=int(user["id"]), role=ROLE_BUSINESS)
        result = execute_connector_plan(
            SqliteDemoConnector(database_path), _plan("MOON", ("legal_bp",)),
            conn=conn, access_context=context,
        )
    finally:
        conn.close()
    assert result["error"]["code"] == "return_field_access_denied"


# --- record scope + projection (fake connector) ------------------------------


def test_record_scope_post_filters_connector_rows(tmp_path: Path) -> None:
    """The connector returns rows from two projects; scope drops the unauthorized
    one even though the connector (broad source creds) returned both."""
    database_path, context = _legal_user_with_all(tmp_path)
    # Revoke STAR so only MOON is in scope.
    conn = db.connect(database_path)
    try:
        star_id = conn.execute(
            "select id from projects where project_code = 'STAR'"
        ).fetchone()["id"]
        conn.execute("delete from project_access where project_id = ?", (star_id,))
        conn.commit()
    finally:
        conn.close()

    connector = _FakeConnector(
        [
            {"project_code": "MOON", "legal_bp": "BP-Morgan"},
            {"project_code": "STAR", "legal_bp": "BP-Stark"},  # out of scope
        ]
    )
    plan = QueryPlan(
        domain="project", operation="search", filters=[], return_fields=["legal_bp"], limit=20
    )
    conn = db.connect(database_path)
    try:
        result = execute_connector_plan(
            connector, plan, conn=conn, access_context=context
        )
    finally:
        conn.close()

    # Scope field was fetched so post-filtering could see it...
    assert "project_code" in connector.last_query.fields
    # ...but projected out, leaving only the authorized field of the in-scope row.
    assert result == {"projects": [{"legal_bp": "BP-Morgan"}]}


def test_contains_filter_is_pushed_down_and_matches_fuzzily(tmp_path: Path) -> None:
    """v0.4.7: a ``contains`` search now resolves through the connector path instead
    of erroring — the regression that made a connector-served source (Feishu) drop
    every fuzzy name search to ``unsupported_operator`` and forced exact guessing."""
    database_path, context = _legal_user_with_all(tmp_path)
    plan = QueryPlan(
        domain="project",
        operation="search",
        # lowercase substring of "Moon Project" — neither an exact nor case match.
        filters=[QueryFilter(field="name", operator="contains", value="moon")],
        return_fields=["legal_bp"],
        limit=20,
    )
    conn = db.connect(database_path)
    try:
        result = execute_connector_plan(
            SqliteDemoConnector(database_path), plan, conn=conn, access_context=context
        )
    finally:
        conn.close()
    assert result == {"projects": [{"legal_bp": "BP-Morgan"}]}


def test_in_filter_is_pushed_down(tmp_path: Path) -> None:
    """An ``in`` multi-value filter resolves through the connector path."""
    database_path, context = _legal_user_with_all(tmp_path)
    plan = QueryPlan(
        domain="project",
        operation="search",
        filters=[QueryFilter(field="project_code", operator="in", value=["MOON", "STAR"])],
        return_fields=["legal_bp"],
        limit=20,
    )
    conn = db.connect(database_path)
    try:
        result = execute_connector_plan(
            SqliteDemoConnector(database_path), plan, conn=conn, access_context=context
        )
    finally:
        conn.close()
    assert result["projects"] == [{"legal_bp": "BP-Morgan"}, {"legal_bp": "BP-Stark"}]


def test_eq_filter_is_case_insensitive(tmp_path: Path) -> None:
    """v0.4.7: ``eq`` matches case-insensitively, so a differently-cased identity
    value still finds its row instead of a false-empty."""
    database_path, context = _legal_user_with_all(tmp_path)
    plan = _plan("moon", ("legal_bp",))  # stored code is "MOON"
    conn = db.connect(database_path)
    try:
        result = execute_connector_plan(
            SqliteDemoConnector(database_path), plan, conn=conn, access_context=context
        )
    finally:
        conn.close()
    assert result == {"projects": [{"legal_bp": "BP-Morgan"}]}


def test_is_empty_filter_is_pushed_down_end_to_end(tmp_path: Path) -> None:
    """v0.5.1: is_empty now pushes down through the connector path (was reported
    ``unsupported_operator``). A project with an empty granted field is found."""
    database_path = _seed(tmp_path)
    conn = db.connect(database_path)
    try:
        ids = [
            _insert_project(conn, "MOON", "Moon Project", "BP-Morgan"),
            _insert_project(conn, "STAR", "Star Project", ""),  # empty legal_bp
        ]
        user = create_user(conn, email="l@x.local", display_name="L", role=ROLE_LEGAL)
        _grant_projects(conn, user["id"], ids)
        _grant_fields(conn, user["id"], "project", ("project_code", "legal_bp"))
        conn.commit()
        context = AccessContext(user_id=int(user["id"]), role=ROLE_LEGAL)
        plan = QueryPlan(
            domain="project",
            operation="search",
            filters=[QueryFilter(field="legal_bp", operator="is_empty")],
            return_fields=["project_code"],
            limit=20,
        )
        result = execute_connector_plan(
            SqliteDemoConnector(database_path), plan, conn=conn, access_context=context
        )
    finally:
        conn.close()
    assert result == {"projects": [{"project_code": "STAR"}]}


def test_date_between_filter_is_pushed_down_end_to_end(tmp_path: Path) -> None:
    """v0.5.1: a date_between range pushes down and filters at the source."""
    database_path = _seed(tmp_path)
    conn = db.connect(database_path)
    try:
        pid = _insert_project(conn, "MOON", "Moon Project", "BP-Morgan")
        for key, number, signed in (
            ("EK-1", "C-2024", "2024-06-01"),
            ("EK-2", "C-2025", "2025-06-01"),
        ):
            conn.execute(
                "insert into contracts "
                "(project_id, external_key, title, contract_number, signed_date) "
                "values (?, ?, ?, ?, ?)",
                (pid, key, f"Contract {number}", number, signed),
            )
        user = create_user(conn, email="l@x.local", display_name="L", role=ROLE_LEGAL)
        _grant_fields(conn, user["id"], "contract", ("contract_number", "signed_date"))
        conn.commit()
        # Unrestricted record scope so the date filter is the sole constraint (the
        # sqlite_demo contract domain scopes by the *relational* project_code, which
        # it cannot SELECT to post-filter; real contract sources declare it as an
        # own field — see test_record_scope_post_filters_connector_rows).
        context = AccessContext(user_id=int(user["id"]), role=ROLE_LEGAL, unrestricted=True)
        plan = QueryPlan(
            domain="contract",
            operation="search",
            filters=[
                QueryFilter(
                    field="signed_date",
                    operator="date_between",
                    value=("2024-01-01", "2024-12-31"),
                )
            ],
            return_fields=["contract_number"],
            limit=20,
        )
        result = execute_connector_plan(
            SqliteDemoConnector(database_path), plan, conn=conn, access_context=context
        )
    finally:
        conn.close()
    assert result == {"contracts": [{"contract_number": "C-2024"}]}


# --- v0.4.8: virtual identity filter (code-or-name, precision + candidates) ---


def _identity_seed(tmp_path: Path) -> tuple[Path, AccessContext]:
    """Legal user granted every project + legal_bp, over a richer project set so a
    bare token can match a code, a name fragment, or several names ambiguously."""
    database_path = _seed(tmp_path)
    conn = db.connect(database_path)
    try:
        ids = [
            _insert_project(conn, "MOON", "Project Moon 月之子", "BP-Morgan"),
            _insert_project(conn, "NOVA", "Project Nova 新星", "BP-Nova"),
            _insert_project(conn, "SH1", "指间山海", "BP-Shanhai-A"),
            _insert_project(conn, "SH2", "山海经", "BP-Shanhai-B"),
        ]
        user = create_user(conn, email="l@x.local", display_name="L", role=ROLE_LEGAL)
        _grant_projects(conn, user["id"], ids)
        _grant_fields(conn, user["id"], "project", ("legal_bp",))
        conn.commit()
        context = AccessContext(user_id=int(user["id"]), role=ROLE_LEGAL)
    finally:
        conn.close()
    return database_path, context


def _identity_plan(token: str, fields: tuple[str, ...] = ("legal_bp",)) -> QueryPlan:
    return QueryPlan(
        domain="project",
        operation="search",
        filters=[QueryFilter(field="identity", operator="contains", value=token)],
        return_fields=list(fields),
        limit=20,
    )


def test_identity_exact_code_hit_returns_only_that_project(tmp_path: Path) -> None:
    """A bare 'MOON' is the project_code of one row and a substring of nothing else;
    the exact (nocase) hit wins, so the answer is that single project."""
    database_path, context = _identity_seed(tmp_path)
    result = _run(
        SqliteDemoConnector(database_path), _identity_plan("MOON"), database_path, context
    )
    assert "identity_disambiguation" not in result
    assert result["projects"] == [
        {"project_code": "MOON", "name": "Project Moon 月之子", "legal_bp": "BP-Morgan"}
    ]


def test_identity_case_insensitive_code_hit(tmp_path: Path) -> None:
    """'nova' (lowercase) exactly matches the NOVA code case-insensitively."""
    database_path, context = _identity_seed(tmp_path)
    result = _run(
        SqliteDemoConnector(database_path), _identity_plan("nova"), database_path, context
    )
    assert "identity_disambiguation" not in result
    assert result["projects"] == [
        {"project_code": "NOVA", "name": "Project Nova 新星", "legal_bp": "BP-Nova"}
    ]


def test_identity_unique_name_fragment_resolves(tmp_path: Path) -> None:
    """'月之子' matches one name as a substring (no exact hit) → that single project,
    not flagged ambiguous, so the agent can answer directly."""
    database_path, context = _identity_seed(tmp_path)
    result = _run(
        SqliteDemoConnector(database_path), _identity_plan("月之子"), database_path, context
    )
    assert "identity_disambiguation" not in result
    assert result["projects"] == [
        {"project_code": "MOON", "name": "Project Moon 月之子", "legal_bp": "BP-Morgan"}
    ]


def test_identity_ambiguous_token_returns_candidates(tmp_path: Path) -> None:
    """'山海' is a substring of two names and an exact match of neither → a candidate
    list carrying code+name so the agent can disambiguate, flagged ambiguous."""
    database_path, context = _identity_seed(tmp_path)
    result = _run(
        SqliteDemoConnector(database_path), _identity_plan("山海"), database_path, context
    )
    assert result["identity_disambiguation"] == {"token": "山海", "candidate_count": 2}
    codes = sorted(row["project_code"] for row in result["projects"])
    assert codes == ["SH1", "SH2"]
    # Each candidate carries the identity fields (code + name) for disambiguation.
    for row in result["projects"]:
        assert {"project_code", "name", "legal_bp"} <= set(row)


def test_identity_filter_is_pushed_down_as_or_over_identity_fields(tmp_path: Path) -> None:
    """The identity token is pushed to the source as one OR filter over the domain's
    identity fields — not fetched-broadly-then-filtered in the gateway."""
    database_path, context = _identity_seed(tmp_path)
    connector = _FakeConnector(
        [{"project_code": "MOON", "name": "Project Moon 月之子", "legal_bp": "BP-Morgan"}],
        domain=ConnectorDomain(
            name="project",
            table="projects",
            fields=(
                ConnectorField(domain="project", name="project_code", is_identity=True),
                ConnectorField(domain="project", name="name", is_identity=True),
                ConnectorField(domain="project", name="legal_bp"),
            ),
        ),
    )
    _run(connector, _identity_plan("MOON"), database_path, context)
    pushed = [f for f in connector.last_query.filters if f.or_fields]
    assert len(pushed) == 1
    assert pushed[0].operator == "contains"
    assert pushed[0].value == "MOON"
    assert set(pushed[0].or_fields) == {"project_code", "name"}


def test_identity_candidates_respect_record_scope(tmp_path: Path) -> None:
    """An out-of-scope project that matches the token must never appear in the
    candidate list — record scope is applied before ranking."""
    database_path, context = _identity_seed(tmp_path)
    conn = db.connect(database_path)
    try:
        sh2_id = conn.execute(
            "select id from projects where project_code = 'SH2'"
        ).fetchone()["id"]
        conn.execute("delete from project_access where project_id = ?", (sh2_id,))
        conn.commit()
    finally:
        conn.close()
    result = _run(
        SqliteDemoConnector(database_path), _identity_plan("山海"), database_path, context
    )
    # Only the in-scope match remains; with a single match it is no longer ambiguous.
    assert "identity_disambiguation" not in result
    assert [row["project_code"] for row in result["projects"]] == ["SH1"]


# --- v0.4.0 §A/§B: arbitrary non-project domain ------------------------------

# A staffing roster that maps onto no governance project: it has no project_code,
# so it cannot be scoped by_governed_code. ``member`` is its row identity.
_STAFFING_DOMAIN = ConnectorDomain(
    name="staffing",
    table="tblStaffing",
    fields=(
        ConnectorField(domain="staffing", name="member", is_identity=True),
        ConnectorField(domain="staffing", name="task"),
        ConnectorField(domain="staffing", name="salary"),
    ),
    record_scope=RecordScope(mode="none"),
)


def _staffing_user(tmp_path: Path, granted: tuple[str, ...]) -> tuple[Path, AccessContext]:
    """A business user with global (project_id NULL) grants on the staffing domain
    and *no* project access at all — the non-project case."""
    database_path = _seed(tmp_path)
    conn = db.connect(database_path)
    try:
        user = create_user(conn, email="s@x.local", display_name="S", role=ROLE_BUSINESS)
        _grant_fields(conn, user["id"], "staffing", granted)
        conn.commit()
        context = AccessContext(user_id=int(user["id"]), role=ROLE_BUSINESS)
    finally:
        conn.close()
    return database_path, context


def test_none_scope_domain_serves_rows_without_project_access(tmp_path: Path) -> None:
    """A1: a record_scope=none, non-project domain is queryable end-to-end with the
    field gate enforced and no project assumption — the user holds zero project
    access yet still sees rows, because there is no row scope to satisfy."""
    database_path, context = _staffing_user(tmp_path, granted=("task",))
    connector = _FakeConnector(
        [{"member": "Alice", "task": "drafting"}], domain=_STAFFING_DOMAIN
    )
    plan = QueryPlan(
        domain="staffing",
        operation="search",
        filters=[QueryFilter(field="member", operator="eq", value="Alice")],
        return_fields=["task"],
        limit=20,
    )
    conn = db.connect(database_path)
    try:
        result = execute_connector_plan(connector, plan, conn=conn, access_context=context)
    finally:
        conn.close()
    # Served under the domain's own key; no project_code was fetched (none scope).
    assert result == {"staffing": [{"task": "drafting"}]}
    assert "project_code" not in (connector.last_query.fields if connector.last_query else ())


def test_none_scope_domain_field_gate_denies_ungranted(tmp_path: Path) -> None:
    """A1: the global-grant field gate still bites on a none-scope domain — an
    ungranted field is denied (not vacuously allowed for lack of a project)."""
    database_path, context = _staffing_user(tmp_path, granted=("task",))  # NOT salary
    connector = _FakeConnector(
        [{"member": "Alice", "salary": "99"}], domain=_STAFFING_DOMAIN
    )
    plan = QueryPlan(
        domain="staffing", operation="search", filters=[],
        return_fields=["salary"], limit=20,
    )
    conn = db.connect(database_path)
    try:
        result = execute_connector_plan(connector, plan, conn=conn, access_context=context)
    finally:
        conn.close()
    assert result["error"]["code"] == "return_field_access_denied"


def test_new_domain_identity_field_exempt_from_grant_gate(tmp_path: Path) -> None:
    """B1: the new domain's identity field (declared is_identity in the catalog) is
    exempt from the grant gate — proving identity comes from the catalog flags, not
    a hard-coded branch in query_authorization. ``member`` is returned though it was
    never granted, exactly as project_code is for the project domain."""
    database_path, context = _staffing_user(tmp_path, granted=("task",))  # member NOT granted
    connector = _FakeConnector(
        [{"member": "Alice", "task": "drafting"}], domain=_STAFFING_DOMAIN
    )
    plan = QueryPlan(
        domain="staffing", operation="search", filters=[],
        return_fields=["member"], limit=20,
    )
    conn = db.connect(database_path)
    try:
        result = execute_connector_plan(connector, plan, conn=conn, access_context=context)
    finally:
        conn.close()
    assert result == {"staffing": [{"member": "Alice"}]}


# --- v0.4.5 Phase 4: record_scope: by_owner ----------------------------------

# A personal journal: each row's ``owner`` column holds a federated subject. The
# owner field is is_identity so a user *may* filter on it — which lets the override
# test prove the pushdown wins over a client-supplied owner filter.
_JOURNAL_DOMAIN = ConnectorDomain(
    name="journal",
    table="tblJournal",
    fields=(
        ConnectorField(domain="journal", name="owner", is_identity=True),
        ConnectorField(domain="journal", name="entry"),
    ),
    record_scope=RecordScope(mode="by_owner", field="owner", subject="external_subject"),
)


class _OwnerConnector:
    """A by_owner source that honors filter-then-limit, like a real source would.

    Crucially it applies ``filters`` *before* ``limit`` — so a test can prove the
    gateway pushes the owner predicate down (no false-empty), not post-filters.
    """

    name = "fake_owner"

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.last_query: ConnectorQuery | None = None

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
        limited = matched[: query.limit]
        return [{k: row[k] for k in query.fields if k in row} for row in limited]


def _owner_user(
    tmp_path: Path, *, external_subject: str | None, granted: tuple[str, ...] = ("entry",)
) -> tuple[Path, AccessContext]:
    """A business user granted journal fields, carrying ``external_subject`` (or not)."""
    database_path = _seed(tmp_path)
    conn = db.connect(database_path)
    try:
        user = create_user(
            conn,
            email=f"{external_subject or 'anon'}@x.local",
            display_name="O",
            role=ROLE_BUSINESS,
            external_subject=external_subject,
        )
        _grant_fields(conn, user["id"], "journal", granted)
        conn.commit()
        context = AccessContext.from_user(user)
    finally:
        conn.close()
    return database_path, context


def _journal_plan(
    return_fields: tuple[str, ...] = ("entry",),
    filters: list[QueryFilter] | None = None,
    limit: int = 20,
) -> QueryPlan:
    return QueryPlan(
        domain="journal",
        operation="search",
        filters=filters or [],
        return_fields=list(return_fields),
        limit=limit,
    )


def _run(connector: Any, plan: QueryPlan, database_path: Path, context: AccessContext):
    conn = db.connect(database_path)
    try:
        return execute_connector_plan(connector, plan, conn=conn, access_context=context)
    finally:
        conn.close()


def test_by_owner_returns_only_requesters_rows(tmp_path: Path) -> None:
    database_path, context = _owner_user(tmp_path, external_subject="oidc|alice")
    connector = _OwnerConnector(
        [
            {"owner": "oidc|alice", "entry": "mine"},
            {"owner": "oidc|bob", "entry": "theirs"},
        ]
    )
    result = _run(connector, _journal_plan(), database_path, context)
    assert result == {"journal": [{"entry": "mine"}]}
    # The owner equality was pushed down to the source (not post-filtered).
    assert connector.last_query.filters == (
        ConnectorFilter(field="owner", operator="eq", value="oidc|alice"),
    )


def test_by_owner_peer_sees_only_theirs(tmp_path: Path) -> None:
    database_path, context = _owner_user(tmp_path, external_subject="oidc|bob")
    connector = _OwnerConnector(
        [
            {"owner": "oidc|alice", "entry": "mine"},
            {"owner": "oidc|bob", "entry": "theirs"},
        ]
    )
    result = _run(connector, _journal_plan(), database_path, context)
    assert result == {"journal": [{"entry": "theirs"}]}


def test_by_owner_granted_user_without_subject_sees_no_rows(tmp_path: Path) -> None:
    """The deny is the by_owner resolver, not the field gate: the user *is* granted
    the field, but has no external_subject → zero rows, and the source is never even
    queried."""
    database_path, context = _owner_user(tmp_path, external_subject=None)
    connector = _OwnerConnector([{"owner": "oidc|alice", "entry": "x"}])
    result = _run(connector, _journal_plan(), database_path, context)
    assert result == {"journal": []}
    assert connector.last_query is None


def test_by_owner_does_not_fall_through_to_all_for_unrestricted(tmp_path: Path) -> None:
    """Fail-closed red line: an unrestricted (local-operator) context must NOT see
    every owner's rows. external_subject is None → zero rows, never None=all."""
    database_path, _ = _owner_user(tmp_path, external_subject="oidc|alice")
    context = AccessContext.local_operator()  # unrestricted, no external_subject
    connector = _OwnerConnector([{"owner": "oidc|alice", "entry": "x"}])
    result = _run(connector, _journal_plan(), database_path, context)
    assert result == {"journal": []}
    assert connector.last_query is None


def test_by_owner_pushdown_before_pagination_fixes_false_empty(tmp_path: Path) -> None:
    """alice's only row sits past the source's first ``limit`` rows. A naive
    limit-before-filter would return [] (false-empty); the pushdown returns her row."""
    database_path, context = _owner_user(tmp_path, external_subject="oidc|alice")
    connector = _OwnerConnector(
        [
            {"owner": "oidc|bob", "entry": "b1"},
            {"owner": "oidc|bob", "entry": "b2"},
            {"owner": "oidc|alice", "entry": "a1"},
        ]
    )
    result = _run(connector, _journal_plan(limit=2), database_path, context)
    assert result == {"journal": [{"entry": "a1"}]}


def test_by_owner_owner_field_not_projected_unless_requested(tmp_path: Path) -> None:
    database_path, context = _owner_user(tmp_path, external_subject="oidc|alice")
    connector = _OwnerConnector([{"owner": "oidc|alice", "entry": "mine"}])
    result = _run(connector, _journal_plan(("entry",)), database_path, context)
    # owner is fetched for the defense-in-depth safety net...
    assert "owner" in connector.last_query.fields
    # ...but stripped from the disclosed rows (only the requested field remains).
    assert result == {"journal": [{"entry": "mine"}]}


def test_by_owner_cannot_query_peers_rows_via_filter(tmp_path: Path) -> None:
    """alice adds her own ``owner == bob`` filter to read his rows; the pushdown
    overrides it, so she still only ever sees her own."""
    database_path, context = _owner_user(tmp_path, external_subject="oidc|alice")
    connector = _OwnerConnector(
        [
            {"owner": "oidc|alice", "entry": "mine"},
            {"owner": "oidc|bob", "entry": "theirs"},
        ]
    )
    plan = _journal_plan(
        filters=[QueryFilter(field="owner", operator="eq", value="oidc|bob")]
    )
    result = _run(connector, plan, database_path, context)
    assert result == {"journal": [{"entry": "mine"}]}
    assert connector.last_query.filters == (
        ConnectorFilter(field="owner", operator="eq", value="oidc|alice"),
    )
