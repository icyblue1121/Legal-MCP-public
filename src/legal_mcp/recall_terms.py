"""Field recall-term generation + governance (v0.5.3).

When a data source is onboarded (or its catalog is refreshed), this module asks
the deployment's single model seam (:class:`legal_mcp.ai_provider.AIProvider`) to
generate, per field, a batch of **recall terms** — semantic synonyms, colloquial
phrasings, Chinese/English variants, industry jargon — for the field *name and
concept*. They are written into ``field_semantics`` with ``origin='generated'``
and, at query time, are folded into the planner's catalog (v0.5.2) so a user's
near-synonym resolves to the canonical field. Generation happens at onboarding
time; the query path never calls the model, staying deterministic and low-latency.

Governance is the point, not just generation:

* **Reviewable / versionable / auditable.** Terms are an inspectable row, not a
  hidden weight: ``origin`` distinguishes generated from hand-edited (``manual``),
  ``updated_at`` versions them, and persisting writes an audit record.
* **A name handle only — never an authorization change.** A recall term maps a
  near-synonym to a *canonical field*; the field gate still decides whether that
  field may be disclosed. Terms carry no row values (the prompt asks for synonyms
  of the field, never example data).
* **Fail-closed, never fail-open.** If the model is unavailable, a field degrades
  to *empty* recall terms (a feature loss), never to a relaxed gate or an error
  that blocks the catalog. ``manual`` rows are never clobbered by generation.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from legal_mcp.ai_provider import AIMessage, AIProvider, AIProviderError
from legal_mcp.query_catalog import QueryCatalog

# Cap per field so the generated prior stays a tight, reviewable handle and the
# injected prompt does not balloon.
MAX_TERMS_PER_FIELD = 12

_SYSTEM_PROMPT = (
    "You generate RECALL TERMS for one database field so a natural-language question "
    "can be mapped to it. Recall terms are semantic synonyms of the field's NAME and "
    "CONCEPT: synonyms, colloquial phrasings, Chinese/English variants, and industry "
    "jargon a user might type to mean this field.\n"
    "Return ONLY a JSON object: {\"synonyms\": [\"term\", ...]}.\n"
    "Rules: terms must be SEMANTIC synonyms (not character variants like spacing or "
    "case of the field name); do NOT include the field's own canonical name; do NOT "
    "invent or include any example data VALUES; no duplicates; "
    f"at most {MAX_TERMS_PER_FIELD} terms. If you cannot produce good terms, return "
    "{\"synonyms\": []}."
)


@dataclass(frozen=True)
class RecallTermProposal:
    """Generated recall terms for one field, before persistence/review."""

    domain: str
    field: str
    terms: tuple[str, ...]


@dataclass(frozen=True)
class PersistSummary:
    """Outcome of writing proposals: counts by disposition."""

    written: int = 0
    skipped_manual: int = 0
    skipped_existing_generated: int = 0
    skipped_empty: int = 0


def _build_messages(domain: str, field: str, aliases: tuple[str, ...]) -> list[AIMessage]:
    context = {
        "domain": domain,
        "field": field,
        "known_aliases": list(aliases),
    }
    return [
        AIMessage(role="system", content=_SYSTEM_PROMPT),
        AIMessage(
            role="user",
            content=(
                "Generate recall terms for this field.\n"
                + json.dumps(context, ensure_ascii=False, sort_keys=True)
            ),
        ),
    ]


def _parse_terms(content: str) -> list[str]:
    """Parse the model reply into a clean term list, tolerating shape drift.

    Accepts ``{"synonyms": [...]}`` or a bare JSON array. Anything unparseable or
    of the wrong shape yields ``[]`` — degrade, never raise."""
    try:
        parsed = json.loads(content)
    except (TypeError, ValueError):
        return []
    if isinstance(parsed, dict):
        parsed = parsed.get("synonyms", [])
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if isinstance(item, str | int | float)]


def _sanitize(
    terms: list[str], *, field: str, aliases: tuple[str, ...]
) -> tuple[str, ...]:
    """Trim, de-dupe (case-insensitive), and drop redundant/empty terms.

    Drops the canonical field name and any already-known alias (those add nothing),
    blanks, and anything past :data:`MAX_TERMS_PER_FIELD`."""
    seen: set[str] = set()
    known = {field.casefold(), *(alias.casefold() for alias in aliases)}
    cleaned: list[str] = []
    for term in terms:
        value = term.strip()
        key = value.casefold()
        if not value or key in seen or key in known:
            continue
        seen.add(key)
        cleaned.append(value)
        if len(cleaned) >= MAX_TERMS_PER_FIELD:
            break
    return tuple(cleaned)


def generate_recall_terms(
    catalog: QueryCatalog,
    provider: AIProvider,
    *,
    domains: "frozenset[str] | set[str] | None" = None,
) -> list[RecallTermProposal]:
    """Generate recall-term proposals for every catalog field (optionally filtered).

    Each field is generated independently; a model failure on one field degrades
    that field to empty terms (recorded as an empty proposal) rather than aborting
    the run or raising — the fail-closed contract. ``cross_domain`` (demo glue) is
    skipped. The aliases already known for a field are passed as context and
    excluded from the result so generation only adds *new* handles."""
    proposals: list[RecallTermProposal] = []
    for domain_name, domain_catalog in sorted(catalog.domains.items()):
        if domain_name == "cross_domain":
            continue
        if domains is not None and domain_name not in domains:
            continue
        # alias map is synonym -> field; invert to field -> its known aliases.
        aliases_by_field: dict[str, list[str]] = {}
        for alias, target in domain_catalog.field_aliases.items():
            aliases_by_field.setdefault(target, []).append(alias)
        for field_name in sorted(domain_catalog.fields):
            aliases = tuple(sorted(aliases_by_field.get(field_name, [])))
            try:
                reply = provider.complete(_build_messages(domain_name, field_name, aliases))
                terms = _sanitize(_parse_terms(reply.content), field=field_name, aliases=aliases)
            except AIProviderError:
                terms = ()  # degrade to empty; never fail-open or abort the catalog.
            proposals.append(
                RecallTermProposal(domain=domain_name, field=field_name, terms=terms)
            )
    return proposals


def _as_source_resolver(source: "str | Callable[[str], str]") -> Callable[[str], str]:
    """Accept either a single source name or a per-domain resolver."""
    if callable(source):
        return source
    return lambda _domain: source


def persist_recall_terms(
    conn: sqlite3.Connection,
    source: "str | Callable[[str], str]",
    proposals: list[RecallTermProposal],
    *,
    recompute: bool = False,
) -> PersistSummary:
    """Write generated proposals into ``field_semantics`` under ``origin='generated'``.

    ``source`` is a single source name or a per-domain resolver — the latter so a
    composite/multi-source catalog writes each domain's terms under the sub-source
    that serves it, matching how the live catalog reads them back.

    Governance rules:
    * a field with an existing ``manual`` row is never overwritten (human edits win);
    * a field with an existing ``generated`` row is overwritten only when
      ``recompute`` is set, so a re-run is explicit, not silent;
    * an empty proposal is skipped (we don't store an empty generated row).

    Only the ``synonyms`` column is touched, preserving any hand-authored
    description/examples on the same row. Idempotent within a transaction.
    """
    resolve = _as_source_resolver(source)
    written = skipped_manual = skipped_existing = skipped_empty = 0
    for proposal in proposals:
        if not proposal.terms:
            skipped_empty += 1
            continue
        domain_source = resolve(proposal.domain)
        existing = conn.execute(
            "select origin from field_semantics where source = ? and domain = ? and field = ?",
            (domain_source, proposal.domain, proposal.field),
        ).fetchone()
        if existing is not None:
            if existing["origin"] == "manual":
                skipped_manual += 1
                continue
            if not recompute:
                skipped_existing += 1
                continue
            conn.execute(
                "update field_semantics set synonyms = ?, origin = 'generated', "
                "updated_at = datetime('now') where source = ? and domain = ? and field = ?",
                (json.dumps(list(proposal.terms)), domain_source, proposal.domain, proposal.field),
            )
        else:
            conn.execute(
                "insert into field_semantics (source, domain, field, synonyms, origin) "
                "values (?, ?, ?, ?, 'generated')",
                (domain_source, proposal.domain, proposal.field, json.dumps(list(proposal.terms))),
            )
        written += 1
    conn.commit()
    return PersistSummary(
        written=written,
        skipped_manual=skipped_manual,
        skipped_existing_generated=skipped_existing,
        skipped_empty=skipped_empty,
    )


def proposals_to_json(
    source: "str | Callable[[str], str]", proposals: list[RecallTermProposal]
) -> str:
    """Serialize proposals for review/export (the governance artifact)."""
    resolve = _as_source_resolver(source)
    payload = {
        "fields": [
            {
                "source": resolve(p.domain),
                "domain": p.domain,
                "field": p.field,
                "synonyms": list(p.terms),
            }
            for p in proposals
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
