"""Read-through data connector interface (pivot 阶段3).

A connector describes the queryable domains/fields of a data source (``catalog``)
and answers constrained queries against it (``query``). It returns *raw source
rows*: field-level and record-scope authorization are the gateway's job, applied
to the query plan and results *around* the connector, never inside it.

v0.2 keeps this minimal. Reserved slots (documented, not yet enforced) so v0.3
real sources don't force a breaking change — see plan §6 阶段3:

1. record-scope predicates distinct from user filters, so row-level auth can be
   pushed down instead of fetching everything and trimming in the gateway;
2. capability declaration (which filters/fields a connector can push down);
3. distinguishing "field absent" from "field present but unauthorized" (the
   latter must not leak schema to the LLM);
4. pagination/cursor and a structured error model.

Deviation from the plan's illustrative sketch: ``catalog()`` returns
``ConnectorDomain`` objects (not bare ``ConnectorField`` tuples) because the
gateway catalog needs per-domain table / identity / relationship metadata; and
``query()`` takes no connection because a connector owns access to its own
source.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

# How a domain's rows are scoped for record-level authorization. Declared per
# domain so a non-project source is not forced onto the legacy ``project_code``
# assumption (v0.4.0 §A).
#   none             — no row post-filter; the domain/field grant is the only gate.
#   by_governed_code — today's behavior: ``field`` carries a governed project code.
#   by_owner         — v0.4.5 Phase 4: ``field`` is the source's owner column and
#                      ``subject`` is the gateway identity attribute it is matched
#                      against; "your own rows". No identifiable subject → zero rows.
RECORD_SCOPE_MODES = frozenset({"none", "by_governed_code", "by_owner"})

# Which ``AccessContext`` attribute a ``by_owner`` domain matches its owner column
# against. ``external_subject`` (the federated subject) is the safe default; ``email``
# is a local-pilot convenience; ``user_id`` is meaningful only for local/demo sources.
OWNER_SUBJECT_ATTRS = frozenset({"external_subject", "email", "user_id"})


@dataclass(frozen=True)
class RecordScope:
    """How a domain's rows are scoped. Default = today's project-code behavior."""

    mode: str = "by_governed_code"
    field: str = "project_code"
    # ``by_owner`` only: which ``AccessContext`` attribute identifies the requester
    # as a row owner (one of :data:`OWNER_SUBJECT_ATTRS`). Unused by other modes.
    subject: str = "external_subject"


def record_scope_from_dict(raw: Any) -> RecordScope:
    """Parse a domain's ``record_scope`` config block, failing closed on a bad mode.

    Shared by config-driven connectors (Feishu, local_file). ``by_owner`` requires
    an explicit ``field`` — the source's owner column; there is no safe default for
    it, unlike ``by_governed_code``'s ``project_code``. ``subject`` (the gateway
    identity attribute matched against it) defaults to ``external_subject`` and must
    be one of :data:`OWNER_SUBJECT_ATTRS`.
    """
    if not raw:
        return RecordScope()
    if not isinstance(raw, Mapping):
        raise ValueError("'record_scope' must be a mapping")
    mode = raw.get("mode", "by_governed_code")
    if mode not in RECORD_SCOPE_MODES:
        raise ValueError(
            f"unknown record_scope mode {mode!r}; expected one of {sorted(RECORD_SCOPE_MODES)}"
        )
    if mode == "by_owner":
        field = raw.get("field")
        if not field:
            raise ValueError(
                "record_scope mode 'by_owner' requires an explicit 'field' "
                "(the source's owner column)"
            )
        subject = raw.get("subject", "external_subject")
        if subject not in OWNER_SUBJECT_ATTRS:
            raise ValueError(
                f"record_scope 'subject' must be one of {sorted(OWNER_SUBJECT_ATTRS)}; "
                f"got {subject!r}"
            )
        return RecordScope(mode="by_owner", field=field, subject=subject)
    field = raw.get("field", "project_code")
    return RecordScope(mode=mode, field=field)


@dataclass(frozen=True)
class ConnectorField:
    """One queryable field in a domain."""

    domain: str
    name: str
    is_identity: bool = False
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class ConnectorDomain:
    """A queryable domain (one logical record type) exposed by a connector."""

    name: str
    table: str
    fields: tuple[ConnectorField, ...]
    # Identity fields of a *related* record that may be used as a filter (e.g.
    # filtering contracts by their project's code).
    relationship_filter_fields: tuple[str, ...] = ()
    record_scope: RecordScope = RecordScope()


@dataclass(frozen=True)
class ConnectorFilter:
    """One pushed-down filter predicate.

    ``operator`` is one of the gateway's plan operators that the connector path
    translates for push-down (``eq`` / ``contains`` / ``in`` as of v0.4.7). It
    carries the operator through the connector boundary so a source can serve a
    *fuzzy* or *multi-value* search natively, not just exact equality — the
    case the equality-only ``dict`` shape silently could not express, which made
    a real source (Feishu) drop ``contains`` searches to ``unsupported_operator``.
    A connector raises ``ValueError`` for an operator it cannot push down.

    ``or_fields`` (v0.4.8) turns one predicate into an OR across *several* fields:
    when non-empty, the connector applies ``operator value`` to each named field
    and ORs them (``field`` is then only the virtual label, e.g. ``identity``).
    This is how a virtual ``identity`` filter pushes a single token down as
    ``project_code contains ? OR name contains ?`` — to the source, in one query,
    rather than fetching broadly and OR-ing in the gateway. Every ``or_fields``
    name must be a real, catalog-declared field.
    """

    field: str
    operator: str
    value: Any = None
    or_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class ConnectorQuery:
    """A constrained read request. Field/filter names must be catalog fields.

    ``filters`` is operator-aware (each is a :class:`ConnectorFilter`), so the
    gateway can push fuzzy/multi-value predicates down to the source rather than
    fetching broadly and trimming in-process. Multiple filters are AND-ed.
    """

    domain: str
    filters: tuple[ConnectorFilter, ...]
    fields: tuple[str, ...]
    limit: int = 20


@dataclass(frozen=True)
class SourceTable:
    """A raw table discoverable on a source, for config scaffolding (v0.4.0 §D).

    ``describe_schema`` returns these so ``legal-mcp scaffold-connector`` can emit
    a *draft* connector config from real columns. It carries no values — only the
    table identity and its column names, in source order — so introspection never
    discloses data. A column is queryable only after it survives human review into
    the committed config.
    """

    domain: str  # suggested gateway domain name (operator may rename)
    table: str  # the source's table id / name
    fields: tuple[str, ...]  # real column names, in source order


@runtime_checkable
class DataConnector(Protocol):
    name: str

    def catalog(self) -> tuple[ConnectorDomain, ...]:
        """Describe the domains and fields this connector can serve."""
        ...

    def query(self, query: ConnectorQuery) -> list[dict[str, Any]]:
        """Return raw source rows for a constrained query (no authorization)."""
        ...

    # Optional. A connector that ships a deterministic fast path for common
    # questions implements ``fast_intents``; it bypasses LLM planning, but the
    # resulting query still flows through gateway authorization. Returning None
    # falls back to the LLM planner. Kept out of the Protocol so connectors are
    # not forced to implement it.
    #
    #     def fast_intents(self, question: str) -> ConnectorQuery | None: ...
    #
    # Optional. A connector that can introspect its source implements
    # ``describe_schema`` (v0.4.0 §D): list each table's real columns so
    # ``scaffold-connector`` can emit a draft config. Read-only, values-free.
    # Kept out of the Protocol so connectors are not forced to implement it.
    #
    #     def describe_schema(self) -> tuple[SourceTable, ...]: ...
