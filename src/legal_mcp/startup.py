"""Startup compatibility checks for Legal-MCP transports."""

from __future__ import annotations

import json
import sqlite3
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from legal_mcp import __version__, db
from legal_mcp.db import DatabasePath

EXPECTED_SCHEMA_VERSION = 25


@dataclass(frozen=True)
class StartupCheckResult:
    name: str
    ok: bool
    message: str
    schema_version: int | None = None
    latest_version: str | None = None
    error: str | None = None


class StartupCheckError(RuntimeError):
    """Raised when a required startup check fails."""


def check_local_compatibility(database_path: DatabasePath) -> StartupCheckResult:
    if not Path(database_path).exists():
        db.initialize_database(database_path)

    conn = db.connect(database_path)
    try:
        try:
            row = conn.execute(
                "select version from schema_version where id = 1"
            ).fetchone()
        except sqlite3.OperationalError:
            row = None
    finally:
        conn.close()

    version = int(row["version"]) if row is not None else 0
    if version != EXPECTED_SCHEMA_VERSION:
        error = (
            f"database schema version {version} is not compatible with "
            f"legal-mcp {__version__}; expected {EXPECTED_SCHEMA_VERSION}"
        )
        return StartupCheckResult(
            name="local_schema",
            ok=False,
            message=error,
            schema_version=version,
            error=error,
        )

    return StartupCheckResult(
        name="local_schema",
        ok=True,
        message=f"schema version {version} is compatible",
        schema_version=version,
    )


def check_remote_version(remote_url: str, timeout: float = 1.0) -> StartupCheckResult:
    try:
        with urllib.request.urlopen(remote_url, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        return StartupCheckResult(
            name="remote_version",
            ok=True,
            message="remote version check skipped after error",
            error=str(exc),
        )

    latest = str(payload.get("version", ""))
    return StartupCheckResult(
        name="remote_version",
        ok=True,
        message=f"latest remote version: {latest}" if latest else "remote version unavailable",
        latest_version=latest or None,
    )


def run_startup_checks(
    database_path: DatabasePath,
    *,
    remote_url: str | None = None,
) -> list[StartupCheckResult]:
    results = [check_local_compatibility(database_path)]
    if remote_url:
        results.append(check_remote_version(remote_url))
    return results


def require_startup_checks(
    database_path: DatabasePath,
    *,
    remote_url: str | None = None,
) -> list[StartupCheckResult]:
    results = run_startup_checks(database_path, remote_url=remote_url)
    failures = [
        result
        for result in results
        if result.name == "local_schema" and not result.ok
    ]
    if failures:
        raise StartupCheckError(failures[0].message)
    return results
