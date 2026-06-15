"""Field recall-term generation + governance (v0.5.3).

Recall terms are generated at onboarding time from the single model seam and
written into ``field_semantics`` (origin='generated'), then folded into the
planner catalog at query time. These tests pin: generation/sanitization, the
governance rules (manual never clobbered, recompute explicit), the fail-closed
degrade when the model is unavailable, and the end-to-end resolution of a
generated near-synonym to its canonical field.
"""

from __future__ import annotations

import json
from pathlib import Path

from legal_mcp import db
from legal_mcp.ai_provider import AIMessage, AIProviderUnavailableError
from legal_mcp.query_catalog import build_query_catalog
from legal_mcp.recall_terms import (
    MAX_TERMS_PER_FIELD,
    generate_recall_terms,
    persist_recall_terms,
    proposals_to_json,
)

DEMO_SOURCE = "sqlite_demo"


def _seed(tmp_path: Path) -> Path:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    return database_path


class _FakeProvider:
    """Returns canned synonyms per field, parsed from the user message JSON."""

    def __init__(self, by_field: dict[str, list[str]], *, raise_for: set[str] | None = None):
        self._by_field = by_field
        self._raise_for = raise_for or set()
        self.calls: list[str] = []

    def complete(self, messages: list[AIMessage]) -> AIMessage:
        context = json.loads(messages[-1].content.split("\n", 1)[1])
        field = context["field"]
        self.calls.append(field)
        if field in self._raise_for:
            raise AIProviderUnavailableError("backend down")
        return AIMessage(
            role="assistant",
            content=json.dumps({"synonyms": self._by_field.get(field, [])}),
        )


def test_generate_sanitizes_and_persists_then_resolves(tmp_path: Path) -> None:
    database_path = _seed(tmp_path)
    provider = _FakeProvider({"legal_bp": ["法务对接人", "法务负责人", "legal partner"]})
    conn = db.connect(database_path)
    try:
        catalog = build_query_catalog(conn)
        proposals = generate_recall_terms(catalog, provider, domains={"project"})
        summary = persist_recall_terms(conn, DEMO_SOURCE, proposals)
        # Rebuild the catalog so the freshly-written terms are loaded.
        refreshed = build_query_catalog(conn)
    finally:
        conn.close()
    assert summary.written >= 1
    # A generated near-synonym now resolves to the canonical field.
    assert refreshed.resolve_field("project", "法务对接人") == "legal_bp"
    assert refreshed.resolve_field("project", "legal partner") == "legal_bp"


def test_sanitize_drops_field_name_aliases_dupes_and_caps(tmp_path: Path) -> None:
    database_path = _seed(tmp_path)
    # legal_bp already has alias 法务BP; include it, the field name, a dupe, and a flood.
    flood = [f"term{i}" for i in range(MAX_TERMS_PER_FIELD + 5)]
    provider = _FakeProvider({"legal_bp": ["legal_bp", "法务BP", "同义", "同义", *flood]})
    conn = db.connect(database_path)
    try:
        catalog = build_query_catalog(conn)
        proposals = generate_recall_terms(catalog, provider, domains={"project"})
    finally:
        conn.close()
    legal_bp = next(p for p in proposals if p.field == "legal_bp")
    assert "legal_bp" not in legal_bp.terms  # canonical name dropped
    assert "法务BP" not in legal_bp.terms  # existing alias dropped
    assert legal_bp.terms.count("同义") == 1  # de-duped
    assert len(legal_bp.terms) <= MAX_TERMS_PER_FIELD  # capped


def test_model_unavailable_degrades_to_empty_not_fail_open(tmp_path: Path) -> None:
    database_path = _seed(tmp_path)
    provider = _FakeProvider({"legal_bp": ["x"]}, raise_for={"legal_bp"})
    conn = db.connect(database_path)
    try:
        catalog = build_query_catalog(conn)
        proposals = generate_recall_terms(catalog, provider, domains={"project"})
        summary = persist_recall_terms(conn, DEMO_SOURCE, proposals)
    finally:
        conn.close()
    legal_bp = next(p for p in proposals if p.field == "legal_bp")
    assert legal_bp.terms == ()  # degraded to empty, never raised
    # Empty proposals are not written as rows.
    assert summary.written == 0 or all(p.terms for p in proposals if p.field != "legal_bp")


def test_persist_never_clobbers_manual(tmp_path: Path) -> None:
    database_path = _seed(tmp_path)
    conn = db.connect(database_path)
    try:
        conn.execute(
            "insert into field_semantics (source, domain, field, synonyms, origin) "
            "values (?, ?, ?, ?, 'manual')",
            (DEMO_SOURCE, "project", "legal_bp", json.dumps(["手填词"])),
        )
        conn.commit()
        catalog = build_query_catalog(conn)
        provider = _FakeProvider({"legal_bp": ["生成词"]})
        proposals = generate_recall_terms(catalog, provider, domains={"project"})
        summary = persist_recall_terms(conn, DEMO_SOURCE, proposals, recompute=True)
        row = conn.execute(
            "select synonyms, origin from field_semantics "
            "where source = ? and domain = 'project' and field = 'legal_bp'",
            (DEMO_SOURCE,),
        ).fetchone()
    finally:
        conn.close()
    assert summary.skipped_manual == 1
    assert json.loads(row["synonyms"]) == ["手填词"]  # untouched
    assert row["origin"] == "manual"


def test_recompute_required_to_overwrite_generated(tmp_path: Path) -> None:
    database_path = _seed(tmp_path)
    conn = db.connect(database_path)
    try:
        catalog = build_query_catalog(conn)
        first = generate_recall_terms(
            catalog, _FakeProvider({"legal_bp": ["第一版"]}), domains={"project"}
        )
        persist_recall_terms(conn, DEMO_SOURCE, first)
        second = generate_recall_terms(
            catalog, _FakeProvider({"legal_bp": ["第二版"]}), domains={"project"}
        )
        # Without recompute, the existing generated row is left in place.
        no_overwrite = persist_recall_terms(conn, DEMO_SOURCE, second)
        kept = conn.execute(
            "select synonyms from field_semantics "
            "where source = ? and domain = 'project' and field = 'legal_bp'",
            (DEMO_SOURCE,),
        ).fetchone()
        # With recompute, it is replaced.
        overwrite = persist_recall_terms(conn, DEMO_SOURCE, second, recompute=True)
        replaced = conn.execute(
            "select synonyms from field_semantics "
            "where source = ? and domain = 'project' and field = 'legal_bp'",
            (DEMO_SOURCE,),
        ).fetchone()
    finally:
        conn.close()
    assert no_overwrite.skipped_existing_generated == 1
    assert json.loads(kept["synonyms"]) == ["第一版"]
    assert overwrite.written == 1
    assert json.loads(replaced["synonyms"]) == ["第二版"]


def test_proposals_to_json_is_reviewable(tmp_path: Path) -> None:
    database_path = _seed(tmp_path)
    conn = db.connect(database_path)
    try:
        catalog = build_query_catalog(conn)
        proposals = generate_recall_terms(
            catalog, _FakeProvider({"legal_bp": ["对接人"]}), domains={"project"}
        )
    finally:
        conn.close()
    payload = json.loads(proposals_to_json(DEMO_SOURCE, proposals))
    entry = next(f for f in payload["fields"] if f["field"] == "legal_bp")
    assert entry == {
        "source": DEMO_SOURCE,
        "domain": "project",
        "field": "legal_bp",
        "synonyms": ["对接人"],
    }
