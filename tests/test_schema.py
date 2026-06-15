import sqlite3

import pytest

from legal_mcp import db


EXPECTED_COLUMNS = {
    "projects": [
        "id",
        "project_code",
        "name",
        "stage",
        "legal_bp",
        "department",
        "release_team",
        "contact_person",
        "website",
        "notes",
        "created_at",
        "updated_at",
    ],
    "contracts": [
        "id",
        "project_id",
        "external_key",
        "title",
        "handler",
        "payment_terms",
        "currency",
        "total_amount",
        "expiry_date",
        "counterparty",
        "company_entity",
        "signed_date",
        "contract_number",
        "income_expense_type",
        "summary",
        "created_at",
        "updated_at",
    ],
    "licenses": [
        "id",
        "project_id",
        "external_key",
        "license_type",
        "identifier",
        "entity_name",
        "issuer",
        "approval_number",
        "rights_holder",
        "copyright_holder",
        "operating_entity",
        "actual_operator",
        "authorization_relation",
        "expiry_date",
        "notes",
        "created_at",
        "updated_at",
    ],
    "risks": [
        "id",
        "project_id",
        "external_key",
        "description",
        "status",
        "source",
        "created_at",
        "updated_at",
    ],
    "companies": [
        "id",
        "name",
        "unified_social_credit_code",
        "created_at",
        "updated_at",
    ],
    "company_seals": [
        "id",
        "company_id",
        "company",
        "seal_type",
        "custodian",
        "storage_location",
        "status",
        "borrower",
        "borrowed_at",
        "borrow_reason",
        "expected_return_at",
        "actual_return_at",
        "created_at",
        "updated_at",
    ],
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
    "company_access": [
        "id",
        "user_id",
        "company_id",
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
        "identity_source",
    ],
    "audit_disclosures": [
        "id",
        "audit_event_id",
        "project_id",
        "record_type",
        "record_id",
        "field_name",
        "group_id",
        "decision",
        "reason",
    ],
    "agent_runs": [
        "id",
        "thread_id",
        "question_summary",
        "status",
        "selected_tool",
        "error_code",
        "created_at",
    ],
    "agent_steps": [
        "id",
        "thread_id",
        "turn_id",
        "step_index",
        "planner_source",
        "status",
        "model",
        "reason",
        "plan_json",
        "error_code",
        "error_message",
        "created_at",
    ],
    "agent_turn_context": [
        "id",
        "conversation_id",
        "turn_id",
        "safe_context_json",
        "created_at",
    ],
    "field_semantics": [
        "id",
        "source",
        "domain",
        "field",
        "description",
        "examples",
        "synonyms",
        "origin",
        "updated_at",
    ],
    "data_sources": [
        "id",
        "name",
        "type",
        "status",
        "config_json",
        "secret_ref",
        "created_by_user_id",
        "created_at",
        "updated_at",
    ],
}

EXPECTED_INDEXES = [
    ("projects", ("project_code",), True),
    ("projects", ("stage",), False),
    ("projects", ("name",), False),
    ("projects", ("legal_bp",), False),
    ("projects", ("department",), False),
    ("projects", ("release_team",), False),
    ("contracts", ("project_id", "external_key"), True),
    ("contracts", ("counterparty",), False),
    ("contracts", ("handler",), False),
    ("contracts", ("expiry_date",), False),
    ("licenses", ("project_id", "external_key"), True),
    ("licenses", ("license_type",), False),
    ("licenses", ("expiry_date",), False),
    ("licenses", ("actual_operator",), False),
    ("licenses", ("operating_entity",), False),
    ("risks", ("project_id", "external_key"), True),
    ("risks", ("status",), False),
    ("risks", ("project_id", "status"), False),
    ("companies", ("name",), True),
    ("company_seals", ("company_id", "seal_type"), True),
    ("company_seals", ("company_id",), False),
    ("company_seals", ("status",), False),
    ("users", ("email",), True),
    ("users", ("external_subject",), False),
    ("api_keys", ("key_prefix",), False),
    ("api_keys", ("user_id",), False),
    ("project_access", ("user_id", "project_id"), True),
    ("project_access", ("project_id",), False),
    ("company_access", ("user_id", "company_id"), True),
    ("company_access", ("company_id",), False),
    ("admin_sessions", ("session_hash",), True),
    ("admin_sessions", ("user_id",), False),
    ("audit_events", ("timestamp",), False),
    ("audit_events", ("user_id",), False),
    ("audit_events", ("tool_name",), False),
    ("audit_disclosures", ("audit_event_id",), False),
    ("audit_disclosures", ("project_id",), False),
    ("agent_runs", ("thread_id",), False),
    ("agent_steps", ("thread_id",), False),
    ("agent_steps", ("status",), False),
    ("agent_turn_context", ("conversation_id",), False),
    ("field_semantics", ("source", "domain"), False),
    ("data_sources", ("status",), False),
]


def test_every_table_is_classified_governance_or_demo_source(tmp_path) -> None:
    """Pivot §4: each table is either gateway governance data or demo-source
    legal facts. A new unclassified table (or a stale classification entry) must
    fail here — this is the scope-drift guard for the governance/demo split.
    """
    db_path = tmp_path / "legal.db"
    db.initialize_database(db_path)

    conn = db.connect(db_path)
    try:
        tables = {
            row["name"]
            for row in conn.execute(
                "select name from sqlite_master "
                "where type = 'table' and name not like 'sqlite_%'"
            )
        }
    finally:
        conn.close()

    assert db.GOVERNANCE_TABLES.isdisjoint(db.DEMO_SOURCE_TABLES)
    # Every live table is classified, and no classification names a missing table.
    assert tables == db.GOVERNANCE_TABLES | db.DEMO_SOURCE_TABLES
    # Spot-check the boundary that matters: business facts are demo-source, the
    # governance core is not.
    assert "projects" in db.DEMO_SOURCE_TABLES
    assert "contracts" in db.DEMO_SOURCE_TABLES
    assert "companies" in db.DEMO_SOURCE_TABLES
    assert "users" in db.GOVERNANCE_TABLES
    assert "permission_grants" in db.GOVERNANCE_TABLES
    assert "audit_disclosures" in db.GOVERNANCE_TABLES


def test_connect_enables_foreign_keys(tmp_path) -> None:
    conn = db.connect(tmp_path / "legal.db")
    try:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        conn.close()


def test_initialize_database_records_schema_version(tmp_path) -> None:
    database_path = tmp_path / "legal.db"

    db.initialize_database(database_path)

    conn = db.connect(database_path)
    try:
        row = conn.execute(
            "select version from schema_version where id = 1"
        ).fetchone()
    finally:
        conn.close()

    assert row["version"] == 25


def test_audit_events_adds_identity_source_column_to_pre_v21_db(tmp_path) -> None:
    # v0.4.5 Phase 2: an audit_events predating identity_source (no such column)
    # gains it via a non-destructive ADD COLUMN on re-init, idempotently, with
    # existing rows preserved (the new column reads NULL for them).
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        conn.execute("alter table audit_events drop column identity_source")
        conn.execute(
            "insert into audit_events "
            "(tool_name, arguments_summary, result_status) "
            "values ('list_projects', '{}', 'success')"
        )
        conn.commit()
        columns = {row["name"] for row in conn.execute("pragma table_info(audit_events)")}
        assert "identity_source" not in columns
    finally:
        conn.close()

    db.initialize_database(database_path)  # migrate
    db.initialize_database(database_path)  # second run is a no-op

    conn = db.connect(database_path)
    try:
        row = conn.execute(
            "select tool_name, identity_source from audit_events"
        ).fetchone()
        assert row["tool_name"] == "list_projects"
        assert row["identity_source"] is None
    finally:
        conn.close()


def test_permission_grants_migrates_legacy_shape(tmp_path) -> None:
    # v0.4.0 §C C3: a permission_grants table predating per-user grants
    # (group_id NOT NULL, no user_id) is rebuilt on startup — data preserved,
    # group_id relaxed to nullable — and the rebuild is idempotent/resumable.
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        conn.executescript(
            """
            drop table permission_grants;
            create table permission_grants (
              id integer primary key,
              group_id integer not null references user_groups(id),
              operation text not null,
              data_domain text not null,
              field_name text,
              project_id integer references projects(id),
              allowed integer not null default 1,
              created_at text not null default (datetime('now')),
              unique(group_id, operation, data_domain, field_name, project_id)
            );
            create index idx_permission_grants_group_id
              on permission_grants(group_id);
            """
        )
        group_id = conn.execute(
            "insert into user_groups (name) values ('Legacy')"
        ).lastrowid
        conn.execute(
            "insert into permission_grants "
            "(group_id, operation, data_domain, field_name) "
            "values (?, 'read', 'project', 'website')",
            (group_id,),
        )
        conn.commit()
    finally:
        conn.close()

    db.initialize_database(database_path)  # rebuild
    db.initialize_database(database_path)  # second run is a no-op

    conn = db.connect(database_path)
    try:
        columns = {row["name"] for row in conn.execute("pragma table_info(permission_grants)")}
        assert "user_id" in columns
        row = conn.execute(
            "select group_id, user_id, data_domain, field_name from permission_grants"
        ).fetchone()
        assert (row["data_domain"], row["field_name"]) == ("project", "website")
        assert row["user_id"] is None
        assert row["group_id"] == group_id
        # group_id is now nullable: a user-only grant inserts cleanly.
        user_id = conn.execute(
            "insert into users (email, display_name, role) "
            "values ('m@example.com', 'M', 'legal')"
        ).lastrowid
        conn.execute(
            "insert into permission_grants (user_id, operation, data_domain) "
            "values (?, 'read', 'project')",
            (user_id,),
        )
        conn.commit()
        assert not conn.execute(
            "select 1 from sqlite_master where name = 'permission_grants_legacy'"
        ).fetchone()
    finally:
        conn.close()


def test_agent_steps_schema_tracks_planning_attempts(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)

    conn = db.connect(database_path)
    try:
        columns = {
            row["name"]: row["notnull"]
            for row in conn.execute("pragma table_info(agent_steps)")
        }
        indexes = {
            row["name"]
            for row in conn.execute("pragma index_list(agent_steps)")
        }
    finally:
        conn.close()

    assert columns["thread_id"] == 1
    assert columns["turn_id"] == 1
    assert columns["step_index"] == 1
    assert columns["planner_source"] == 1
    assert columns["status"] == 1
    assert "idx_agent_steps_thread_id" in indexes


def test_agent_steps_migrates_legacy_unique_to_turn_keyed(tmp_path) -> None:
    # v0.4.6 §F: an agent_steps predating per-turn audit (no turn_id, unique
    # (thread_id, step_index)) is rebuilt — turn_id added, unique relaxed to
    # (thread_id, turn_id, step_index) — with legacy rows preserved under a
    # synthetic per-row turn id so they never collide. Rebuild is idempotent.
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        conn.executescript(
            """
            drop table agent_steps;
            create table agent_steps (
              id integer primary key,
              thread_id text not null,
              step_index integer not null,
              planner_source text not null,
              status text not null,
              model text,
              reason text,
              plan_json text,
              error_code text,
              error_message text,
              created_at text not null default (datetime('now')),
              unique(thread_id, step_index)
            );
            create index idx_agent_steps_thread_id on agent_steps(thread_id);
            """
        )
        conn.execute(
            "insert into agent_steps (thread_id, step_index, planner_source, status) "
            "values ('conv-1', 1, 'ai', 'selected')"
        )
        conn.commit()
    finally:
        conn.close()

    db.initialize_database(database_path)  # rebuild
    db.initialize_database(database_path)  # second run is a no-op

    conn = db.connect(database_path)
    try:
        columns = {row["name"] for row in conn.execute("pragma table_info(agent_steps)")}
        assert "turn_id" in columns
        row = conn.execute(
            "select thread_id, turn_id, step_index from agent_steps"
        ).fetchone()
        assert row["thread_id"] == "conv-1"
        assert row["turn_id"] == "legacy-1"
        # The new unique key is per-turn: two turns of one conversation both at
        # step_index = 1 now coexist (the bug the migration fixes).
        conn.execute(
            "insert into agent_steps (thread_id, turn_id, step_index, planner_source, status) "
            "values ('conv-1', 'turn-a', 1, 'ai', 'selected')"
        )
        conn.execute(
            "insert into agent_steps (thread_id, turn_id, step_index, planner_source, status) "
            "values ('conv-1', 'turn-b', 1, 'ai', 'selected')"
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "insert into agent_steps "
                "(thread_id, turn_id, step_index, planner_source, status) "
                "values ('conv-1', 'turn-a', 1, 'ai', 'selected')"
            )
        assert not conn.execute(
            "select 1 from sqlite_master where name = 'agent_steps_legacy'"
        ).fetchone()
    finally:
        conn.close()


def test_initialize_database_creates_agent_settings(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)

    conn = db.connect(database_path)
    try:
        row = conn.execute(
            """
            select ai_provider, ai_model, ai_base_url
            from agent_settings
            where id = 1
            """
        ).fetchone()
    finally:
        conn.close()

    assert row is not None
    assert row["ai_provider"] == "openai_compatible"
    assert row["ai_model"] == "gpt-4.1-mini"
    assert row["ai_base_url"] is None


def test_initialize_database_creates_group_permission_and_alias_tables(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        tables = {
            row["name"]
            for row in conn.execute(
                "select name from sqlite_master where type = 'table'"
            )
        }
    finally:
        conn.close()

    assert "user_groups" in tables
    assert "user_group_memberships" in tables
    assert "permission_grants" in tables
    assert "project_aliases" in tables


def test_contracts_table_has_contract_information_columns(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        columns = {
            row["name"]
            for row in conn.execute("pragma table_info(contracts)")
        }
    finally:
        conn.close()

    assert {
        "handler",
        "payment_terms",
        "currency",
        "total_amount",
        "expiry_date",
        "company_entity",
        "contract_number",
        "income_expense_type",
    }.issubset(columns)


def test_audit_disclosures_tracks_fields_and_group_reason(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        columns = {
            row["name"]
            for row in conn.execute("pragma table_info(audit_disclosures)")
        }
    finally:
        conn.close()

    assert "field_name" in columns
    assert "group_id" in columns


def test_initialize_database_creates_required_tables_and_columns(tmp_path) -> None:
    db_path = tmp_path / "legal.db"

    db.initialize_database(db_path)

    conn = db.connect(db_path)
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "select name from sqlite_master where type = 'table'"
            )
        }
        assert set(EXPECTED_COLUMNS).issubset(tables)

        for table_name, expected_columns in EXPECTED_COLUMNS.items():
            columns = [
                row["name"]
                for row in conn.execute(f"PRAGMA table_info({table_name})")
            ]
            assert columns == expected_columns
    finally:
        conn.close()


def test_initialize_database_creates_required_indexes(tmp_path) -> None:
    db_path = tmp_path / "legal.db"
    db.initialize_database(db_path)

    conn = db.connect(db_path)
    try:
        actual_indexes = []
        for table_name in EXPECTED_COLUMNS:
            for index_row in conn.execute(f"PRAGMA index_list({table_name})"):
                index_name = index_row["name"]
                columns = tuple(
                    row["name"] for row in conn.execute(f"PRAGMA index_info({index_name})")
                )
                actual_indexes.append((table_name, columns, bool(index_row["unique"])))

        for expected in EXPECTED_INDEXES:
            assert expected in actual_indexes
    finally:
        conn.close()


def test_schema_enforces_project_identity_and_allows_duplicate_names(tmp_path) -> None:
    db_path = tmp_path / "legal.db"
    db.initialize_database(db_path)

    conn = db.connect(db_path)
    try:
        conn.execute(
            "insert into projects (project_code, name, stage) values (?, ?, ?)",
            ("GAME-001", "Same Name", "live"),
        )
        conn.execute(
            "insert into projects (project_code, name, stage) values (?, ?, ?)",
            ("GAME-002", "Same Name", "planning"),
        )

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "insert into projects (project_code, name, stage) values (?, ?, ?)",
                ("GAME-001", "Renamed Later", "live"),
            )
    finally:
        conn.close()


def test_identity_schema_enforces_unique_email_and_project_grants(tmp_path) -> None:
    db_path = tmp_path / "legal.db"
    db.initialize_database(db_path)

    conn = db.connect(db_path)
    try:
        conn.execute(
            "insert into projects (project_code, name, stage) values (?, ?, ?)",
            ("GAME-001", "Project One", "live"),
        )
        project_id = conn.execute(
            "select id from projects where project_code = ?", ("GAME-001",)
        ).fetchone()["id"]

        conn.execute(
            "insert into users (email, display_name, role) values (?, ?, ?)",
            ("admin@example.com", "Admin User", "admin"),
        )
        user_id = conn.execute(
            "select id from users where email = ?", ("admin@example.com",)
        ).fetchone()["id"]

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "insert into users (email, display_name, role) values (?, ?, ?)",
                ("admin@example.com", "Duplicate User", "legal"),
            )

        conn.execute(
            "insert into project_access "
            "(user_id, project_id, granted_by_user_id) values (?, ?, ?)",
            (user_id, project_id, user_id),
        )

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "insert into project_access "
                "(user_id, project_id, granted_by_user_id) values (?, ?, ?)",
                (user_id, project_id, user_id),
            )
    finally:
        conn.close()


def test_identity_schema_enforces_required_api_key_and_grant_fields(tmp_path) -> None:
    db_path = tmp_path / "legal.db"
    db.initialize_database(db_path)

    conn = db.connect(db_path)
    try:
        conn.execute(
            "insert into projects (project_code, name, stage) values (?, ?, ?)",
            ("GAME-001", "Project One", "live"),
        )
        project_id = conn.execute(
            "select id from projects where project_code = ?", ("GAME-001",)
        ).fetchone()["id"]

        conn.execute(
            "insert into users (email, display_name, role) values (?, ?, ?)",
            ("admin@example.com", "Admin User", "admin"),
        )
        user_id = conn.execute(
            "select id from users where email = ?", ("admin@example.com",)
        ).fetchone()["id"]

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "insert into api_keys (user_id, key_prefix, key_hash) values (?, ?, ?)",
                (user_id, "lk_test", "hashed-secret"),
            )

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "insert into project_access (user_id, project_id) values (?, ?)",
                (user_id, project_id),
            )
    finally:
        conn.close()


def test_audit_schema_enforces_required_fields_defaults_and_decisions(tmp_path) -> None:
    db_path = tmp_path / "legal.db"
    db.initialize_database(db_path)

    conn = db.connect(db_path)
    try:
        conn.execute(
            "insert into audit_events (tool_name, arguments_summary, result_status) "
            "values (?, ?, ?)",
            ("search_contracts", "{}", "success"),
        )
        audit_event_id = conn.execute("select id from audit_events").fetchone()["id"]
        response_record_count = conn.execute(
            "select response_record_count from audit_events where id = ?",
            (audit_event_id,),
        ).fetchone()["response_record_count"]
        assert response_record_count == 0

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "insert into audit_events (tool_name, result_status) values (?, ?)",
                ("search_contracts", "success"),
            )

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "insert into audit_disclosures "
                "(audit_event_id, record_type, record_id, decision, reason) "
                "values (?, ?, ?, ?, ?)",
                (audit_event_id, "contract", 1, "maybe", "Invalid decision"),
            )

        conn.execute(
            "insert into audit_disclosures "
            "(audit_event_id, record_type, record_id, decision, reason) "
            "values (?, ?, ?, ?, ?)",
            (audit_event_id, "summary", None, "allowed", "Aggregate disclosure"),
        )
        disclosure = conn.execute(
            "select record_id from audit_disclosures where record_type = ?",
            ("summary",),
        ).fetchone()
        assert disclosure["record_id"] is None

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "insert into audit_disclosures "
                "(audit_event_id, record_type, record_id, decision) "
                "values (?, ?, ?, ?)",
                (audit_event_id, "contract", 1, "allowed"),
            )
    finally:
        conn.close()


def test_schema_enforces_child_foreign_keys_and_unique_external_keys(tmp_path) -> None:
    db_path = tmp_path / "legal.db"
    db.initialize_database(db_path)

    conn = db.connect(db_path)
    try:
        conn.execute(
            "insert into projects (project_code, name, stage) values (?, ?, ?)",
            ("GAME-001", "Project One", "live"),
        )
        conn.execute(
            "insert into projects (project_code, name, stage) values (?, ?, ?)",
            ("GAME-002", "Project Two", "live"),
        )
        first_project_id = conn.execute(
            "select id from projects where project_code = ?", ("GAME-001",)
        ).fetchone()["id"]
        second_project_id = conn.execute(
            "select id from projects where project_code = ?", ("GAME-002",)
        ).fetchone()["id"]

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "insert into risks (project_id, external_key, description, status) "
                "values (?, ?, ?, ?)",
                (999, "risk-1", "Missing parent", "open"),
            )

        conn.execute(
            "insert into contracts (project_id, external_key, title) values (?, ?, ?)",
            (first_project_id, "contract-1", "Publishing Agreement"),
        )
        conn.execute(
            "insert into contracts (project_id, external_key, title) values (?, ?, ?)",
            (second_project_id, "contract-1", "Another Agreement"),
        )

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "insert into contracts (project_id, external_key, title) values (?, ?, ?)",
                (first_project_id, "contract-1", "Duplicate Agreement"),
            )
    finally:
        conn.close()
