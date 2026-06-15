"""End-to-end identity resolution through the live agent path (v0.4.8).

Drives the real ``agent_query`` tool entrypoint — classify → plan → validate →
authorize → connector retrieval — with the ``project`` domain served by a
(fake-client) Feishu connector that honours the pushed-down filter, exactly as the
real Bitable would. Planning is stubbed to emit the virtual ``identity`` filter the
planner prompt now teaches the model to produce, so the test isolates the identity
resolver and OR pushdown rather than a live LLM.

Acceptance (the canonical cases): a bare token resolves whether it is a code
("MOON"), a unique name fragment ("月之子"), or a case-folded code ("nova"); an
ambiguous token ("山海") returns a code+name candidate list for the agent to
disambiguate. Record scope and the field gate stay enforced around the connector.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

from legal_mcp import db
from legal_mcp.connector_config import build_connector_setup
from legal_mcp.connectors.feishu_bitable import FeishuBitableConnector
from legal_mcp.identity import ROLE_LEGAL
from legal_mcp.policy import AccessContext
from legal_mcp.tools import call_tool

REPO_ROOT = Path(__file__).resolve().parents[1]
SEED_PATH = REPO_ROOT / "examples" / "legal-demo" / "seed_server_db.py"

_spec = importlib.util.spec_from_file_location("legal_demo_seed_identity", SEED_PATH)
seed_mod = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(seed_mod)

# Rows that live ONLY in the Feishu source. The legal_bp values are distinct so a
# disclosure proves which project the resolver actually landed on.
_FEISHU_ROWS = [
    {"project_code": "MOON", "name": "Project Moon 月之子", "contact_person": "C1", "legal_bp": "BP-MOON"},
    {"project_code": "NOVA", "name": "Project Nova 新星", "contact_person": "C2", "legal_bp": "BP-NOVA"},
    {"project_code": "SH1", "name": "指间山海", "contact_person": "C3", "legal_bp": "BP-SH1"},
    {"project_code": "SH2", "name": "山海经", "contact_person": "C4", "legal_bp": "BP-SH2"},
]

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
                {"name": "legal_bp", "aliases": ["法务BP"]},
            ],
        }
    ],
}


class _FilteringFeishuClient:
    """A fake Bitable client that actually applies the pushed filter, so the OR
    pushdown selects rows the same way the live source would."""

    def search_records(self, *, table_id, field_names, filter, page_size):
        rows = [row for row in _FEISHU_ROWS if _matches(row, filter)]
        return [{k: row[k] for k in field_names if k in row} for row in rows[:page_size]]


def _matches(row: dict[str, Any], filter: dict[str, Any] | None) -> bool:
    if not filter:
        return True
    if "children" in filter:
        return all(_matches_group(row, child) for child in filter["children"])
    return _matches_group(row, filter)


def _matches_group(row: dict[str, Any], group: dict[str, Any]) -> bool:
    results = [_matches_condition(row, cond) for cond in group.get("conditions", [])]
    if not results:
        return True
    return any(results) if group.get("conjunction") == "or" else all(results)


def _matches_condition(row: dict[str, Any], cond: dict[str, Any]) -> bool:
    value = str(row.get(cond["field_name"], ""))
    target = str(cond["value"][0])
    if cond["operator"] == "contains":
        return target.casefold() in value.casefold()
    return value.casefold() == target.casefold()  # "is"


class _IdentityStubPlanner:
    """Plans the identity filter the prompt teaches: a bare token → identity+contains.

    The token is the question text before '的' (so 'MOON的法务BP是谁' → 'MOON'), else
    the whole question ('月之子' / 'nova' / '山海')."""

    def __init__(self, *_args, **_kwargs) -> None:
        pass

    def complete(self, messages):
        from legal_mcp.ai_provider import AIMessage

        question = messages[-1].content
        token = question.split("的", 1)[0] if "的" in question else question
        return AIMessage(
            role="assistant",
            content=json.dumps(
                {
                    "domain": "project",
                    "operation": "search",
                    "filters": [
                        {"field": "identity", "operator": "contains", "value": token}
                    ],
                    "return_fields": ["legal_bp"],
                    "limit": 10,
                }
            ),
        )


@pytest.fixture
def identity_setup(tmp_path, monkeypatch):
    database_path = tmp_path / "legal-demo-server.db"
    seed_mod.seed_server_db(database_path)
    _grant_legal_access_to_feishu_projects(database_path)
    setup = build_connector_setup(
        {"sources": [_FEISHU_SOURCE]},
        database_path=database_path,
        feishu_connector_factory=lambda c: FeishuBitableConnector(c, _FilteringFeishuClient()),
    )
    monkeypatch.setattr("legal_mcp.ai_provider.ConfiguredAIProvider", _IdentityStubPlanner)
    return database_path, tmp_path / "audit.jsonl", setup


def _grant_legal_access_to_feishu_projects(database_path: Path) -> None:
    """Mirror the Feishu rows into governance so the legal user is in record scope
    for them and may read legal_bp (record scope + field gate stay live)."""
    conn = db.connect(database_path)
    try:
        legal_id = conn.execute(
            "select id from users where role = ? order by id limit 1", (ROLE_LEGAL,)
        ).fetchone()["id"]
        group_id = conn.execute(
            "insert into user_groups (name) values (?)", ("feishu-legal",)
        ).lastrowid
        conn.execute(
            "insert into user_group_memberships (user_id, group_id) values (?, ?)",
            (legal_id, group_id),
        )
        conn.execute(
            "insert into permission_grants "
            "(group_id, operation, data_domain, field_name, project_id) "
            "values (?, 'read', 'project', 'legal_bp', NULL)",
            (group_id,),
        )
        for row in _FEISHU_ROWS:
            existing = conn.execute(
                "select id from projects where project_code = ?", (row["project_code"],)
            ).fetchone()
            if existing is None:
                project_id = conn.execute(
                    "insert into projects (project_code, name, stage) values (?, ?, 'live')",
                    (row["project_code"], row["name"]),
                ).lastrowid
            else:
                project_id = existing["id"]
            already = conn.execute(
                "select 1 from project_access where user_id = ? and project_id = ?",
                (legal_id, project_id),
            ).fetchone()
            if already is None:
                conn.execute(
                    "insert into project_access (user_id, project_id, granted_by_user_id) "
                    "values (?, ?, ?)",
                    (legal_id, project_id, legal_id),
                )
        conn.commit()
    finally:
        conn.close()


def _legal_context(database_path: Path) -> AccessContext:
    conn = db.connect(database_path)
    try:
        row = conn.execute(
            "select id from users where role = ? order by id limit 1", (ROLE_LEGAL,)
        ).fetchone()
    finally:
        conn.close()
    return AccessContext(user_id=int(row["id"]), role=ROLE_LEGAL)


def _ask(identity_setup, question: str) -> dict[str, Any]:
    database_path, audit_path, setup = identity_setup
    return call_tool(
        "agent_query",
        {"rationale": "identity e2e", "question": question},
        database_path=database_path,
        audit_path=audit_path,
        access_context=_legal_context(database_path),
        connector_setup=setup,
    )


def _payload(result: dict[str, Any]) -> dict[str, Any]:
    return json.loads(result["answer"])


def test_bare_code_token_resolves(identity_setup) -> None:
    result = _ask(identity_setup, "MOON的法务BP是谁")
    assert result["status"] == "success"
    rows = _payload(result)["projects"]
    assert rows == [{"legal_bp": "BP-MOON", "name": "Project Moon 月之子", "project_code": "MOON"}]


def test_unique_name_fragment_resolves(identity_setup) -> None:
    result = _ask(identity_setup, "月之子的法务BP是谁")
    assert result["status"] == "success"
    rows = _payload(result)["projects"]
    assert [r["project_code"] for r in rows] == ["MOON"]
    assert rows[0]["legal_bp"] == "BP-MOON"


def test_case_folded_code_resolves(identity_setup) -> None:
    result = _ask(identity_setup, "nova的法务BP是谁")
    assert result["status"] == "success"
    rows = _payload(result)["projects"]
    assert [r["project_code"] for r in rows] == ["NOVA"]
    assert rows[0]["legal_bp"] == "BP-NOVA"


def test_ambiguous_token_lists_candidates(identity_setup) -> None:
    result = _ask(identity_setup, "山海")
    assert result["status"] == "success"
    # v0.5.4: an ambiguous identity match renders a human-readable "did you mean"
    # list (the candidates are already record-scoped + field-gated identity values).
    answer = result["answer"]
    assert "山海" in answer
    assert "匹配到多个" in answer
    assert "SH1" in answer and "SH2" in answer
