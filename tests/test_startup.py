from __future__ import annotations

from legal_mcp import db
from legal_mcp.startup import (
    EXPECTED_SCHEMA_VERSION,
    StartupCheckResult,
    check_local_compatibility,
    run_startup_checks,
)


def test_check_local_compatibility_accepts_current_schema(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)

    result = check_local_compatibility(database_path)

    assert result.ok is True
    assert result.schema_version == EXPECTED_SCHEMA_VERSION
    assert result.error is None


def test_check_local_compatibility_rejects_old_schema(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        conn.execute("update schema_version set version = 12 where id = 1")
        conn.commit()
    finally:
        conn.close()

    result = check_local_compatibility(database_path)

    assert result.ok is False
    assert "schema version 12" in result.error


def test_run_startup_checks_does_not_require_remote_check(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)

    results = run_startup_checks(database_path, remote_url=None)

    assert all(isinstance(result, StartupCheckResult) for result in results)
    assert [result.name for result in results] == ["local_schema"]
    assert results[0].ok is True
