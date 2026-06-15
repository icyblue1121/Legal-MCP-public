"""Verifies the runnable legal demo actually demonstrates its claims (阶段6)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

RUN_DEMO_PATH = (
    Path(__file__).resolve().parents[1] / "examples" / "legal-demo" / "run_demo.py"
)


def _load_run_demo():
    spec = importlib.util.spec_from_file_location("legal_demo_run", RUN_DEMO_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_demo_discloses_differently_per_role(tmp_path: Path) -> None:
    run_demo = _load_run_demo()
    results = {r["role"]: r for r in run_demo.run(tmp_path / "demo.db")}

    # Same question, different disclosure: only legal sees the sensitive field.
    assert "legal_bp" in results["legal"]["allowed_fields"]
    assert "legal_bp" not in results["business"]["allowed_fields"]
    assert "legal_bp" not in results["auditor"]["allowed_fields"]

    # Everyone still gets the non-sensitive identity field.
    for role in ("legal", "business", "auditor"):
        assert "project_code" in results[role]["allowed_fields"]


def test_demo_audit_records_allow_and_deny(tmp_path: Path) -> None:
    run_demo = _load_run_demo()
    results = {r["role"]: r for r in run_demo.run(tmp_path / "demo.db")}

    legal_bp_decision = results["business"]["decisions"]["legal_bp"]
    assert legal_bp_decision.allowed is False  # a recorded deny
    code_decision = results["business"]["decisions"]["project_code"]
    assert code_decision.allowed is True  # a recorded allow


def test_demo_never_leaks_denied_value_to_business(tmp_path: Path) -> None:
    run_demo = _load_run_demo()
    results = {r["role"]: r for r in run_demo.run(tmp_path / "demo.db")}

    legal_bp_values = {
        row.get("legal_bp")
        for row in results["legal"]["rows"]
        if row.get("legal_bp")
    }
    assert legal_bp_values  # sanity: legal actually saw the sensitive values

    business_blob = json.dumps(results["business"]["rows"], ensure_ascii=False, default=str)
    for value in legal_bp_values:
        assert value not in business_blob
