"""Field semantic metadata layer (v0.5.2).

``field_semantics`` carries per-field description / examples / synonyms (the
recall-terms layer v0.5.3 will populate). These tests pin the *carrying layer*:
synonyms fold into the alias map so a near-synonym resolves to the canonical
field, and the metadata is injected into the planner prompt. Authorization is
unchanged — synonyms are only a name handle, never a grant.
"""

from __future__ import annotations

import json
from pathlib import Path

from legal_mcp import db
from legal_mcp.connectors.sqlite_demo import SqliteDemoConnector
from legal_mcp.query_catalog import (
    DEMO_SOURCE_NAME,
    build_query_catalog,
    build_query_catalog_from_connector,
    catalog_context_for_prompt,
    load_field_semantics,
)


def _seed(tmp_path: Path) -> Path:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    return database_path


def _add_semantics(
    database_path: Path,
    *,
    source: str = DEMO_SOURCE_NAME,
    domain: str = "project",
    field: str = "legal_bp",
    description: str | None = None,
    examples: list[str] | None = None,
    synonyms: list[str] | None = None,
    origin: str = "manual",
) -> None:
    conn = db.connect(database_path)
    try:
        conn.execute(
            "insert into field_semantics "
            "(source, domain, field, description, examples, synonyms, origin) "
            "values (?, ?, ?, ?, ?, ?, ?)",
            (
                source,
                domain,
                field,
                description,
                json.dumps(examples) if examples is not None else None,
                json.dumps(synonyms) if synonyms is not None else None,
                origin,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def test_synonym_resolves_to_canonical_field(tmp_path: Path) -> None:
    database_path = _seed(tmp_path)
    _add_semantics(database_path, field="legal_bp", synonyms=["法务对接人", "法律BP"])
    conn = db.connect(database_path)
    try:
        catalog = build_query_catalog(conn)
    finally:
        conn.close()
    # A natural-language synonym now resolves to the canonical field.
    assert catalog.resolve_field("project", "法务对接人") == "legal_bp"
    assert catalog.resolve_field("project", "法律BP") == "legal_bp"


def test_semantics_injected_into_prompt(tmp_path: Path) -> None:
    database_path = _seed(tmp_path)
    _add_semantics(
        database_path,
        field="legal_bp",
        description="法务业务伙伴",
        examples=["BP-Morgan"],
        synonyms=["法务对接人"],
    )
    conn = db.connect(database_path)
    try:
        catalog = build_query_catalog(conn)
    finally:
        conn.close()
    payload = json.loads(catalog_context_for_prompt(catalog))
    semantics = payload["domains"]["project"]["field_semantics"]["legal_bp"]
    assert semantics == {
        "description": "法务业务伙伴",
        "examples": ["BP-Morgan"],
        "synonyms": ["法务对接人"],
    }


def test_semantics_apply_on_connector_path(tmp_path: Path) -> None:
    database_path = _seed(tmp_path)
    # The sqlite demo connector's name is the source key the demo catalog shares.
    _add_semantics(database_path, source="sqlite_demo", field="legal_bp", synonyms=["法务对接人"])
    conn = db.connect(database_path)
    try:
        catalog = build_query_catalog_from_connector(
            SqliteDemoConnector(database_path), conn
        )
    finally:
        conn.close()
    assert catalog.resolve_field("project", "法务对接人") == "legal_bp"


def test_synonym_does_not_shadow_real_field_or_existing_alias(tmp_path: Path) -> None:
    database_path = _seed(tmp_path)
    # Try to point an existing alias and a real field name at the wrong target.
    _add_semantics(
        database_path,
        field="legal_bp",
        synonyms=["法务BP", "name"],  # 法务BP already aliases legal_bp; name is a real field
    )
    conn = db.connect(database_path)
    try:
        catalog = build_query_catalog(conn)
    finally:
        conn.close()
    # The real field still resolves to itself, never redirected by a synonym.
    assert catalog.resolve_field("project", "name") == "name"
    # The pre-existing alias mapping is preserved (法务BP -> legal_bp either way here,
    # but the setdefault must not clobber an existing entry).
    assert catalog.resolve_field("project", "法务BP") == "legal_bp"


def test_stale_field_is_ignored(tmp_path: Path) -> None:
    database_path = _seed(tmp_path)
    _add_semantics(database_path, field="not_a_real_field", synonyms=["whatever"])
    conn = db.connect(database_path)
    try:
        catalog = build_query_catalog(conn)
    finally:
        conn.close()
    # A semantics row for a field the catalog does not expose introduces nothing.
    assert catalog.resolve_field("project", "whatever") is None
    assert "not_a_real_field" not in catalog.domains["project"].field_semantics


def test_loader_tolerates_bad_json(tmp_path: Path) -> None:
    database_path = _seed(tmp_path)
    conn = db.connect(database_path)
    try:
        conn.execute(
            "insert into field_semantics (source, domain, field, synonyms) "
            "values (?, ?, ?, ?)",
            (DEMO_SOURCE_NAME, "project", "legal_bp", "{not valid json"),
        )
        conn.commit()
        semantics = load_field_semantics(conn, DEMO_SOURCE_NAME)
    finally:
        conn.close()
    # Bad JSON degrades to empty, never raises.
    assert semantics["project"]["legal_bp"].synonyms == ()


def test_no_semantics_leaves_catalog_unchanged(tmp_path: Path) -> None:
    database_path = _seed(tmp_path)
    conn = db.connect(database_path)
    try:
        catalog = build_query_catalog(conn)
    finally:
        conn.close()
    assert catalog.domains["project"].field_semantics == {}
    payload = json.loads(catalog_context_for_prompt(catalog))
    assert "field_semantics" not in payload["domains"]["project"]
