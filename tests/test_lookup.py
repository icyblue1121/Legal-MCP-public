from legal_mcp import db
from legal_mcp.lookup import ProjectLookupResult, lookup_project


def insert_project(conn, code: str, name: str, stage: str = "live") -> None:
    conn.execute(
        "insert into projects (project_code, name, stage) values (?, ?, ?)",
        (code, name, stage),
    )
    conn.commit()


def test_lookup_exact_project_code_wins_over_matching_name(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        insert_project(conn, "GAME-001", "Shared Name")
        insert_project(conn, "SHARED NAME", "Other Project")

        result = lookup_project(conn, "SHARED NAME")

        assert result.kind == ProjectLookupResult.FOUND
        assert result.project["project_code"] == "SHARED NAME"
    finally:
        conn.close()


def test_lookup_ambiguous_exact_name_returns_candidates(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        insert_project(conn, "GAME-001", "Shared Name")
        insert_project(conn, "GAME-002", "Shared Name")

        result = lookup_project(conn, "Shared Name")

        assert result.kind == ProjectLookupResult.AMBIGUOUS
        assert [candidate["project_code"] for candidate in result.candidates] == [
            "GAME-001",
            "GAME-002",
        ]
    finally:
        conn.close()


def test_lookup_exact_alias_returns_project(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        insert_project(conn, "ACME", "Acme")
        project = conn.execute(
            "select id from projects where project_code = ?",
            ("ACME",),
        ).fetchone()
        conn.execute(
            "insert into project_aliases (project_id, alias, source) values (?, ?, ?)",
            (project["id"], "ACME项目部", "test"),
        )
        conn.commit()

        result = lookup_project(conn, "ACME项目部")

        assert result.kind == ProjectLookupResult.FOUND
        assert result.project["project_code"] == "ACME"
    finally:
        conn.close()


def test_lookup_project_code_and_alias_are_case_insensitive(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        insert_project(conn, "Acme", "示例项目")
        project = conn.execute(
            "select id from projects where project_code = ?",
            ("Acme",),
        ).fetchone()
        conn.execute(
            "insert into project_aliases (project_id, alias, source) values (?, ?, ?)",
            (project["id"], "ACME项目部", "test"),
        )
        conn.commit()

        code_result = lookup_project(conn, "acme")
        alias_result = lookup_project(conn, "acme项目部")

        assert code_result.kind == ProjectLookupResult.FOUND
        assert code_result.project["project_code"] == "Acme"
        assert alias_result.kind == ProjectLookupResult.FOUND
        assert alias_result.project["project_code"] == "Acme"
    finally:
        conn.close()


def test_lookup_project_name_embedded_in_user_question_returns_project(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        insert_project(conn, "OTHER", "示例项目")

        result = lookup_project(conn, "示例项目 的官网是什么？")

        assert result.kind == ProjectLookupResult.FOUND
        assert result.project["project_code"] == "OTHER"
    finally:
        conn.close()


def test_lookup_project_alias_embedded_in_user_question_returns_project(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        insert_project(conn, "ACME", "Acme")
        project = conn.execute(
            "select id from projects where project_code = ?",
            ("ACME",),
        ).fetchone()
        conn.execute(
            "insert into project_aliases (project_id, alias, source) values (?, ?, ?)",
            (project["id"], "ACME项目部", "test"),
        )
        conn.commit()

        result = lookup_project(conn, "ACME项目部 的官网是什么？")

        assert result.kind == ProjectLookupResult.FOUND
        assert result.project["project_code"] == "ACME"
    finally:
        conn.close()


def test_lookup_not_found_when_no_safe_match_exists(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        insert_project(conn, "GAME-001", "Alpha")
        insert_project(conn, "GAME-002", "Beta")

        result = lookup_project(conn, "Zeta")

        assert result.kind == ProjectLookupResult.NOT_FOUND
        assert result.candidates == []
    finally:
        conn.close()
