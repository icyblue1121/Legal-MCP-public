"""Deterministic classification for schema-independent agent intents.

v0.4.6 §A removed the global business fast path that parsed project-code grammar
and project/license field names out of natural language. That heuristic assumed
legal-demo phrasing (e.g. "the text before the first 的 is the project") and broke
for verbose questions and for arbitrary connector-backed domains, silently
producing a wrong-filter plan that searched zero rows. Field/domain planning is now
always the catalog-bound planner's job.

What remains here is only intent classification that does **not** depend on the
source schema: recognizing an access-scope question ("what can I see / what are my
permissions") so it can bypass the model and call ``describe_my_access`` directly.
"""

from __future__ import annotations

from dataclasses import dataclass

from legal_mcp.query_plan import QueryPlan


@dataclass(frozen=True)
class FastPathDecision:
    intent: str
    reason: str
    plan: QueryPlan | None = None


# Phrases that ask for the requester's own access scope rather than business data.
ACCESS_TERMS = (
    "我能访问哪些项目",
    "我能看到哪些项目",
    "我的权限",
    "我有什么权限",
    "有什么权限",
    "什么权限",
    "权限范围",
    "用户权限",
)

SEAL_STATUS_TERMS = (
    "我管理的公司印章状态",
    "我负责的公司印章状态",
    "我保管的公司印章状态",
)


def is_access_question(question: str) -> bool:
    """True if the question asks for the requester's own permission scope."""
    return any(term in question for term in ACCESS_TERMS)


def plan_fast_path(question: str) -> FastPathDecision | None:
    """Schema-independent fast intent, or ``None`` to defer to the planner.

    Only access-scope questions are handled deterministically now; project and
    license field questions go to the catalog-bound planner (v0.4.6 §A).
    """
    normalized = question.strip()
    if is_access_question(normalized):
        return FastPathDecision(intent="access", reason="common access-scope question")
    if any(term in normalized for term in SEAL_STATUS_TERMS):
        return FastPathDecision(
            intent="search",
            reason="managed company seal status demo question",
            plan=QueryPlan(
                domain="seal",
                operation="list",
                filters=[],
                return_fields=[
                    "company",
                    "seal_type",
                    "status",
                    "storage_location",
                    "borrower",
                    "borrowed_at",
                    "borrow_reason",
                    "expected_return_at",
                    "actual_return_at",
                ],
                limit=100,
            ),
        )
    return None
