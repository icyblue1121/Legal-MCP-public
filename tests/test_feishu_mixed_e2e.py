"""Mixed-source crown jewel: Feishu project domain + SQLite governance (v0.3).

One question, different disclosure per role — with the **DB permission grants as
the sole authorization gate** (v0.4.0 §C: no policy file) — but the ``project``
domain is now served by a (fake-client) Feishu connector while the gateway and
audit are unchanged. The sensitive value is returned *only* by the Feishu source,
so a successful disclosure proves the live path actually read through the
connector, and the leakage assertions prove authorization still wraps a non-SQL
source. The legal-vs-business difference comes entirely from the seed's DB grants
(legal is granted ``legal_bp``, business is not).

Runs in-process through ``call_tool`` (the real tool entrypoint) so the
``can_query_content`` gate, the DB-grant field gate, and connector retrieval are
all exercised together. Planning is made deterministic by stubbing the server-side
planner (v0.4.6 §A removed the global business fast path), so the test isolates
authorization + connector retrieval, not LLM planning.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from legal_mcp import db
from legal_mcp.connector_config import build_connector_setup
from legal_mcp.connectors.feishu_bitable import FeishuBitableConnector
from legal_mcp.identity import ROLE_AUDITOR, ROLE_BUSINESS, ROLE_LEGAL
from legal_mcp.policy import AccessContext
from legal_mcp.tools import call_tool

REPO_ROOT = Path(__file__).resolve().parents[1]
SEED_PATH = REPO_ROOT / "examples" / "legal-demo" / "seed_server_db.py"

_spec = importlib.util.spec_from_file_location("legal_demo_seed", SEED_PATH)
seed_mod = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(seed_mod)

QUESTION = "MOON的法务BP是谁"
# Distinct from demo-data.csv's value: it exists ONLY in the Feishu source, so
# seeing it proves the answer was read through the connector, not from SQLite.
FEISHU_LEGAL_BP = "BP-FROM-FEISHU"

_FEISHU_SOURCE = {
    "type": "feishu_bitable",
    "app_token": "bascnDemo",
    "domains": [
        {
            "name": "project",
            "table_id": "tblProject",
            "fields": [
                {"name": "project_code", "is_identity": True, "aliases": ["项目代号"]},
                {"name": "name", "is_identity": True, "aliases": ["项目名称"]},
                {"name": "contact_person", "aliases": ["对接人"]},
                {"name": "legal_bp", "aliases": ["法务BP", "法务bp"]},
            ],
        }
    ],
}


class _FakeFeishuClient:
    """Returns the MOON row as if it lived in a Feishu Bitable."""

    def search_records(self, *, table_id, field_names, filter, page_size):
        return [
            {
                "project_code": "MOON",
                "name": "Moon Project",
                "contact_person": "Contact-Moon",
                "legal_bp": FEISHU_LEGAL_BP,
            }
        ]


class _StubPlanner:
    """A deterministic stand-in for ConfiguredAIProvider: always plans the MOON
    legal_bp query, so the test exercises authorization + connector retrieval
    without a live model (v0.4.6 §A removed the deterministic fast path)."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    def complete(self, messages):
        from legal_mcp.ai_provider import AIMessage

        return AIMessage(
            role="assistant",
            content=json.dumps(
                {
                    "domain": "project",
                    "operation": "search",
                    "filters": [
                        {"field": "project_code", "operator": "eq", "value": "MOON"}
                    ],
                    "return_fields": ["legal_bp"],
                    "limit": 1,
                }
            ),
        )


def _context_for(database_path: Path, role: str) -> AccessContext:
    conn = db.connect(database_path)
    try:
        row = conn.execute(
            "select id from users where role = ? order by id limit 1", (role,)
        ).fetchone()
    finally:
        conn.close()
    return AccessContext(user_id=int(row["id"]), role=role)


@pytest.fixture
def mixed_setup(tmp_path, monkeypatch):
    database_path = tmp_path / "legal-demo-server.db"
    seed_mod.seed_server_db(database_path)
    setup = build_connector_setup(
        {"sources": [_FEISHU_SOURCE]},
        database_path=database_path,
        feishu_connector_factory=lambda c: FeishuBitableConnector(c, _FakeFeishuClient()),
    )
    # Stub the planner the agent_query entrypoint constructs, so the query plans
    # deterministically (the old fast path used to do this).
    monkeypatch.setattr("legal_mcp.ai_provider.ConfiguredAIProvider", _StubPlanner)
    return database_path, tmp_path / "audit.jsonl", setup


def _agent_query(database_path, audit_path, setup, role: str) -> dict[str, Any]:
    return call_tool(
        "agent_query",
        {"rationale": "mixed-source demo", "question": QUESTION},
        database_path=database_path,
        audit_path=audit_path,
        access_context=_context_for(database_path, role),
        connector_setup=setup,
    )


def test_legal_sees_feishu_sourced_field(mixed_setup) -> None:
    database_path, audit_path, setup = mixed_setup
    result = _agent_query(database_path, audit_path, setup, ROLE_LEGAL)
    assert result["status"] == "success"
    # The disclosed value came from the Feishu connector, end to end.
    assert FEISHU_LEGAL_BP in json.dumps(result, ensure_ascii=False)


def test_business_denied_by_db_grant_no_leak(mixed_setup) -> None:
    database_path, audit_path, setup = mixed_setup
    result = _agent_query(database_path, audit_path, setup, ROLE_BUSINESS)
    # Business has no DB grant for legal_bp, so the same query legal runs is denied
    # — the DB grant is the sole gate now (v0.4.0 §C).
    assert result["error"]["code"] == "return_field_access_denied"
    # Leakage gate over the connector path: the Feishu value never appears.
    assert FEISHU_LEGAL_BP not in json.dumps(result, ensure_ascii=False)


def test_auditor_cannot_query_content_no_leak(mixed_setup) -> None:
    database_path, audit_path, setup = mixed_setup
    result = _agent_query(database_path, audit_path, setup, ROLE_AUDITOR)
    assert result["error"]["code"] == "access_denied"
    assert FEISHU_LEGAL_BP not in json.dumps(result, ensure_ascii=False)
