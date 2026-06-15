"""Project matching helpers for imports."""

from __future__ import annotations

import difflib
import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class ProjectMatch:
    mode: str
    project_code: str | None
    candidates: list[dict[str, str]]


def match_project(conn: sqlite3.Connection, raw_value: str | None) -> ProjectMatch:
    value = (raw_value or "").strip()
    if not value:
        return ProjectMatch("unresolved", None, [])

    alias = conn.execute(
        """
        select projects.project_code
        from project_aliases
        join projects on projects.id = project_aliases.project_id
        where project_aliases.alias = ?
        """,
        (value,),
    ).fetchone()
    if alias is not None:
        return ProjectMatch("alias", alias["project_code"], [])

    exact = conn.execute(
        "select project_code from projects where project_code = ? or name = ?",
        (value, value),
    ).fetchone()
    if exact is not None:
        return ProjectMatch("exact", exact["project_code"], [])

    rows = conn.execute("select project_code, name from projects").fetchall()
    choices = [row["project_code"] for row in rows] + [row["name"] for row in rows]
    matches = difflib.get_close_matches(value, choices, n=5, cutoff=0.5)
    candidates = [
        {"project_code": row["project_code"], "name": row["name"]}
        for row in rows
        if row["project_code"] in matches or row["name"] in matches
    ]
    return ProjectMatch("fuzzy_candidate" if candidates else "unresolved", None, candidates)
