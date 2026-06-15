"""Deterministic project lookup helpers."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

FUZZY_THRESHOLD = 0.82


@dataclass(frozen=True)
class ProjectLookupResult:
    FOUND = "found"
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"

    kind: str
    project: dict[str, Any] | None = None
    candidates: list[dict[str, Any]] | None = None


def lookup_project(conn: sqlite3.Connection, query: str) -> ProjectLookupResult:
    normalized_query = query.strip()
    if not normalized_query:
        return ProjectLookupResult(ProjectLookupResult.NOT_FOUND, candidates=[])

    exact_code = conn.execute(
        "select * from projects where lower(project_code) = lower(?)",
        (normalized_query,),
    ).fetchone()
    if exact_code is not None:
        return ProjectLookupResult(ProjectLookupResult.FOUND, project=dict(exact_code))

    exact_alias = conn.execute(
        """
        select projects.*
        from project_aliases
        join projects on projects.id = project_aliases.project_id
        where lower(project_aliases.alias) = lower(?)
        """,
        (normalized_query,),
    ).fetchone()
    if exact_alias is not None:
        return ProjectLookupResult(ProjectLookupResult.FOUND, project=dict(exact_alias))

    exact_name_rows = conn.execute(
        "select * from projects where lower(name) = lower(?) order by project_code",
        (normalized_query,),
    ).fetchall()
    if len(exact_name_rows) == 1:
        return ProjectLookupResult(
            ProjectLookupResult.FOUND,
            project=dict(exact_name_rows[0]),
        )
    if len(exact_name_rows) > 1:
        return ProjectLookupResult(
            ProjectLookupResult.AMBIGUOUS,
            candidates=[_candidate(row) for row in exact_name_rows],
        )

    embedded_candidates = _embedded_candidates(conn, normalized_query)
    if len(embedded_candidates) == 1:
        row = conn.execute(
            "select * from projects where id = ?",
            (embedded_candidates[0]["id"],),
        ).fetchone()
        return ProjectLookupResult(ProjectLookupResult.FOUND, project=dict(row))
    if len(embedded_candidates) > 1:
        return ProjectLookupResult(
            ProjectLookupResult.AMBIGUOUS,
            candidates=embedded_candidates,
        )

    fuzzy_candidates = _fuzzy_candidates(conn, normalized_query)
    if len(fuzzy_candidates) == 1:
        row = conn.execute(
            "select * from projects where id = ?",
            (fuzzy_candidates[0]["id"],),
        ).fetchone()
        return ProjectLookupResult(ProjectLookupResult.FOUND, project=dict(row))
    if len(fuzzy_candidates) > 1:
        return ProjectLookupResult(
            ProjectLookupResult.AMBIGUOUS,
            candidates=fuzzy_candidates,
        )

    return ProjectLookupResult(ProjectLookupResult.NOT_FOUND, candidates=[])


def _fuzzy_candidates(conn: sqlite3.Connection, query: str) -> list[dict[str, Any]]:
    rows = conn.execute("select id, project_code, name from projects").fetchall()
    candidates = []
    for row in rows:
        score = SequenceMatcher(None, query.lower(), row["name"].lower()).ratio()
        if score >= FUZZY_THRESHOLD:
            candidates.append({**_candidate(row), "score": round(score, 3)})
    return sorted(candidates, key=lambda item: (-item["score"], item["project_code"]))


def _embedded_candidates(conn: sqlite3.Connection, query: str) -> list[dict[str, Any]]:
    normalized_query = _normalize(query)
    rows = conn.execute(
        """
        select
          projects.id,
          projects.project_code,
          projects.name,
          project_aliases.alias
        from projects
        left join project_aliases on project_aliases.project_id = projects.id
        """
    ).fetchall()
    candidates: dict[int, dict[str, Any]] = {}
    for row in rows:
        identifiers = (row["project_code"], row["name"], row["alias"])
        if any(
            identifier and _normalize(identifier) in normalized_query
            for identifier in identifiers
        ):
            candidates[int(row["id"])] = _candidate(row)
    return sorted(candidates.values(), key=lambda item: item["project_code"])


def _normalize(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


def _candidate(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "project_code": row["project_code"],
        "name": row["name"],
    }
