"""Identity-token matching: precision ranking + ambiguity candidates (v0.4.8).

Both retrieval paths — the SQLite *direct* path (:mod:`legal_mcp.search_tools`)
and the connector path (:mod:`legal_mcp.connector_retrieval`) — push a virtual
``identity`` filter down as an OR over the domain's identity fields (a code *or* a
name). That OR is deliberately lenient (substring / case-insensitive), so it can
over-match: "山海" hits several projects, "月之子" exactly one, "MOON" the code.

This module turns those raw matches into an answer, identically for both paths:

* if any identity field of a row matches the token **exactly** (case-insensitive),
  only the exact matches are returned — "MOON" / "nova" resolve to one project,
  never buried under substring noise;
* otherwise the substring matches are returned as **candidates**, ranked
  most-precise first (prefix before mid-string) and capped, each carrying the
  domain's identity fields so the agent can confirm or ask which one was meant.

The rows handed in are already record-scoped and field-gated by the caller, so a
candidate list cannot disclose a row the requester may not see, nor a field they
were not granted. Identity fields themselves are exempt from the field gate, so
echoing ``project_code`` / ``name`` for disambiguation never widens disclosure.
"""

from __future__ import annotations

from typing import Any

from legal_mcp.query_plan import VIRTUAL_IDENTITY_FIELD, QueryPlan

# How many substring candidates to surface when nothing matches exactly. Enough to
# let the agent disambiguate, few enough to stay a "did you mean…" list.
CANDIDATE_LIMIT = 10


def identity_filter(plan: QueryPlan) -> tuple[str, str] | None:
    """The plan's identity filter as ``(operator, token)``, or ``None`` if absent.

    A ``None``/empty value normalizes to ``""`` so callers can rely on a string.
    Only the first identity filter is honoured (a plan carries at most one).
    """
    for query_filter in plan.filters:
        if query_filter.field == VIRTUAL_IDENTITY_FIELD:
            value = query_filter.value
            return query_filter.operator, "" if value is None else str(value)
    return None


def rank_identity_rows(
    rows: list[dict[str, Any]],
    *,
    token: str,
    identity_fields: tuple[str, ...],
    return_fields: list[str],
    candidate_limit: int = CANDIDATE_LIMIT,
) -> tuple[list[dict[str, Any]], bool]:
    """Resolve identity matches to an answer. Returns ``(rows_out, ambiguous)``.

    ``rows`` are already record-scoped and field-gated and each carry the identity
    fields plus the requested return fields. Output rows are projected to exactly
    ``identity_fields`` ∪ ``return_fields`` (identity fields first, so the agent can
    always name the entity). ``ambiguous`` is true only when several distinct
    substring candidates remain — the signal to disambiguate rather than answer.
    """
    output_fields = list(dict.fromkeys([*identity_fields, *return_fields]))

    def project(row: dict[str, Any]) -> dict[str, Any]:
        return {field: row[field] for field in output_fields if field in row}

    def values(row: dict[str, Any]) -> list[str]:
        return [
            str(row[field])
            for field in identity_fields
            if field in row and row[field] is not None
        ]

    folded = token.casefold()
    exact = [row for row in rows if any(value.casefold() == folded for value in values(row))]
    if exact:
        return [project(row) for row in exact], False

    # Substring candidates. The rows are already the source's identity-OR match set,
    # but filter defensively so a stray non-matching row can never become a candidate.
    substring = [
        row for row in rows if any(folded in value.casefold() for value in values(row))
    ]

    def precision(row: dict[str, Any]) -> tuple[int, str]:
        folded_values = [value.casefold() for value in values(row)]
        # Prefix matches are more likely the intended entity than mid-string ones;
        # the second key is the first identity value, for a stable deterministic order.
        rank = 0 if any(value.startswith(folded) for value in folded_values) else 1
        first = values(row)[0] if values(row) else ""
        return rank, first

    ranked = sorted(substring, key=precision)
    candidates = [project(row) for row in ranked[:candidate_limit]]
    return candidates, len(candidates) > 1
