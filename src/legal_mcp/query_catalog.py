from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from legal_mcp.connectors.base import DataConnector

from legal_mcp.connectors.base import RecordScope
from legal_mcp.query_plan import (
    SUPPORTED_OPERATIONS,
    SUPPORTED_OPERATORS,
    VIRTUAL_IDENTITY_FIELD,
    PlanValidationResult,
    QueryPlan,
    validate_query_plan,
)
from legal_mcp.tool_catalog import CONTRACT_FIELDS, LICENSE_FIELDS, PROJECT_FIELDS, SEAL_FIELDS

# Fields a cross-domain search may return. The cross_domain executor builds its
# own per-domain return lists, but model plans must still declare return fields
# from this whitelist so validation can approve them.
CROSS_DOMAIN_RETURN_FIELDS = frozenset(
    {
        "project_code",
        "name",
        "contract_number",
        "title",
        "counterparty",
        "license_type",
        "actual_operator",
        "operating_entity",
    }
)
# A cross-domain plan filters by a single free-text term.
CROSS_DOMAIN_FILTER_FIELDS = frozenset({"q", "query", "term"})


@dataclass(frozen=True)
class FieldSemantics:
    """Semantic metadata for one field (v0.5.2).

    ``description`` and ``examples`` are injected into the planner prompt so an
    oddly-named column is understandable; ``synonyms`` (recall terms) are *also*
    folded into the domain's alias map so a near-synonym resolves to the canonical
    field. Synonyms never widen field authorization — they are only a name handle;
    the field gate still applies and no row value is ever carried here.
    """

    description: str | None = None
    examples: tuple[str, ...] = ()
    synonyms: tuple[str, ...] = ()


@dataclass(frozen=True)
class DomainCatalog:
    domain: str
    table: str
    fields: set[str]
    identity_fields: set[str]
    field_aliases: dict[str, str] = field(default_factory=dict)
    relationship_filter_fields: set[str] = field(default_factory=set)
    # How this domain's rows are scoped. Drives record-level authorization and,
    # for ``mode == "none"``, tells the field gate to evaluate global grants
    # (no project dimension) instead of the requester's visible projects.
    record_scope: RecordScope = field(default_factory=RecordScope)
    # Per-field semantic metadata (v0.5.2), keyed by canonical field name. Empty
    # unless the deployment populated ``field_semantics`` for this source/domain.
    field_semantics: dict[str, FieldSemantics] = field(default_factory=dict)


@dataclass(frozen=True)
class QueryCatalog:
    domains: dict[str, DomainCatalog]

    def validate_plan(self, plan: QueryPlan) -> PlanValidationResult:
        # Structure and enum checks live in one place (query_plan), so the graph
        # validate_plan node and classify_question agree with this validator.
        structural = validate_query_plan(plan, set(self.domains))
        if not structural.ok:
            return structural

        domain = self.domains.get(plan.domain)
        if domain is None:
            return PlanValidationResult(
                False, "unsupported_domain", "query domain is not registered"
            )

        unknown_returns = sorted(set(plan.return_fields) - domain.fields)
        if unknown_returns:
            return PlanValidationResult(
                False,
                "unknown_return_field",
                f"return fields are not registered: {', '.join(unknown_returns)}",
            )

        allowed_filter_fields = domain.fields | domain.relationship_filter_fields
        if domain.identity_fields:
            # The virtual ``identity`` filter (v0.4.8) is legal on any domain that
            # declares identity fields; retrieval expands it to an OR over them.
            allowed_filter_fields = allowed_filter_fields | {VIRTUAL_IDENTITY_FIELD}
        unknown_filters = sorted(
            {query_filter.field for query_filter in plan.filters} - allowed_filter_fields
        )
        if unknown_filters:
            return PlanValidationResult(
                False,
                "unknown_filter_field",
                f"filter fields are not registered: {', '.join(unknown_filters)}",
            )
        return PlanValidationResult(True)

    def resolve_field(self, domain: str, name: str) -> str | None:
        """Resolve an alias or canonical field name to a registered field."""
        domain_catalog = self.domains.get(domain)
        if domain_catalog is None:
            return None
        if name == VIRTUAL_IDENTITY_FIELD and domain_catalog.identity_fields:
            # ``identity`` is a virtual filter field (v0.4.8): canonical as-is on any
            # domain with identity fields, expanded to an OR over them at retrieval.
            return VIRTUAL_IDENTITY_FIELD
        if name in domain_catalog.fields:
            return name
        if name in domain_catalog.relationship_filter_fields:
            return name
        alias_target = domain_catalog.field_aliases.get(name)
        if alias_target is not None:
            return alias_target
        return None


# Domains backed by a real table. Must stay aligned with the domains
# search_tools.execute_search_plan can execute. (risk is intentionally not
# registered yet: there is no risk executor.)
DOMAIN_FIELDS = {
    "project": frozenset(PROJECT_FIELDS),
    "contract": frozenset(CONTRACT_FIELDS),
    "license": frozenset(LICENSE_FIELDS),
    "seal": frozenset(SEAL_FIELDS),
}

DOMAIN_TABLES = {
    "project": "projects",
    "contract": "contracts",
    "license": "licenses",
    "seal": "company_seals",
}

IDENTITY_FIELDS = {
    "project": {"project_code", "name"},
    "contract": {"contract_number", "title"},
    "license": {"license_type", "identifier"},
    "seal": {"company", "seal_type"},
}

FIELD_ALIASES = {
    "project": {
        "项目代号": "project_code",
        "项目名称": "name",
        "游戏名称": "name",
        "官网": "website",
        "法务BP": "legal_bp",
        "发行团队": "release_team",
        "所属部门": "department",
        "联系人": "contact_person",
        "对接人": "contact_person",
        "上线状态": "stage",
        "阶段": "stage",
        "备注": "notes",
    },
    "contract": {
        "合同号": "contract_number",
        "合同主题": "title",
        "相对方": "counterparty",
        "我方签约公司": "company_entity",
        "金额": "total_amount",
        "经办人": "handler",
    },
    "license": {
        "资质类型": "license_type",
        "商标": "license_type",
        "商标权利人": "rights_holder",
        "在哪家公司": "rights_holder",
        "著作权人": "copyright_holder",
        "实际运营方": "actual_operator",
        "实际运营主体": "actual_operator",
        "运营方": "actual_operator",
        "运营单位": "operating_entity",
        "运营主体": "operating_entity",
    },
    "seal": {
        "公司": "company",
        "公司名称": "company",
        "印章类型": "seal_type",
        "章类型": "seal_type",
        "保管人": "custodian",
        "保管地点": "storage_location",
        "存放地点": "storage_location",
        "现在状态": "status",
        "当前状态": "status",
        "印章状态": "status",
        "外借人": "borrower",
        "借用人": "borrower",
        "外借时间": "borrowed_at",
        "借出时间": "borrowed_at",
        "外借原因": "borrow_reason",
        "借用原因": "borrow_reason",
        "预计归还时间": "expected_return_at",
        "实际归还时间": "actual_return_at",
    },
}


# The source name the built-in (non-connector) demo catalog uses to look up
# field_semantics — matches ``SqliteDemoConnector.name`` so both paths share rows.
DEMO_SOURCE_NAME = "sqlite_demo"


def load_field_semantics(
    conn: sqlite3.Connection | None, source: str
) -> dict[str, dict[str, FieldSemantics]]:
    """Load semantic metadata for a source as ``{domain: {field: FieldSemantics}}``.

    Returns an empty map when there is no connection, no ``field_semantics`` table
    (an older DB), or no rows. A JSON column that fails to parse degrades to an
    empty list, never an error — semantics are an enhancement, never a gate.
    """
    if conn is None:
        return {}
    try:
        rows = conn.execute(
            "select domain, field, description, examples, synonyms "
            "from field_semantics where source = ?",
            (source,),
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    result: dict[str, dict[str, FieldSemantics]] = {}
    for row in rows:
        result.setdefault(row["domain"], {})[row["field"]] = FieldSemantics(
            description=row["description"],
            examples=_json_str_tuple(row["examples"]),
            synonyms=_json_str_tuple(row["synonyms"]),
        )
    return result


def _json_str_tuple(raw: object) -> tuple[str, ...]:
    if not raw:
        return ()
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return ()
    if not isinstance(parsed, list):
        return ()
    return tuple(str(item) for item in parsed if isinstance(item, str | int | float))


def _apply_field_semantics(
    domain_catalog: DomainCatalog, semantics: dict[str, FieldSemantics]
) -> DomainCatalog:
    """Merge a domain's semantics: fold synonyms into the alias map (synonym ->
    canonical field) and attach the per-field metadata for prompt injection. Only
    fields the catalog actually exposes are kept, so a stale row can't introduce a
    field. A synonym never overrides an existing alias or shadows a real field."""
    relevant = {f: s for f, s in semantics.items() if f in domain_catalog.fields}
    if not relevant:
        return domain_catalog
    merged_aliases = dict(domain_catalog.field_aliases)
    for canonical_field, sem in relevant.items():
        for synonym in sem.synonyms:
            if synonym and synonym not in domain_catalog.fields:
                merged_aliases.setdefault(synonym, canonical_field)
    return replace(domain_catalog, field_aliases=merged_aliases, field_semantics=relevant)


def _with_field_semantics(
    domains: dict[str, DomainCatalog],
    conn: sqlite3.Connection | None,
    source_of_domain: "Callable[[str], str]",
) -> dict[str, DomainCatalog]:
    """Apply ``field_semantics`` to each domain, keyed by *its owning source*.

    A domain's source is resolved per domain (``source_of_domain``), not globally,
    so a composite/multi-source catalog still finds each domain's rows under the
    sub-source that actually serves it — matching how the recall-term generator
    writes them. Each distinct source's rows are loaded once."""
    if conn is None:
        return domains
    cache: dict[str, dict[str, dict[str, FieldSemantics]]] = {}
    result: dict[str, DomainCatalog] = {}
    for name, domain_catalog in domains.items():
        source = source_of_domain(name)
        if source not in cache:
            cache[source] = load_field_semantics(conn, source)
        result[name] = _apply_field_semantics(domain_catalog, cache[source].get(name, {}))
    return result


def source_of_domain_for_connector(connector: "DataConnector") -> "Callable[[str], str]":
    """A per-domain source resolver for a connector's catalog.

    Uses the connector's ``domain_sources()`` map (the sub-source that owns each
    domain, e.g. through a CompositeConnector) when available, falling back to the
    connector's own ``name``. This is the source key under which recall terms are
    generated, so generation and the live catalog agree."""
    domain_sources = getattr(connector, "domain_sources", None)
    mapping = domain_sources() if callable(domain_sources) else {}

    def resolve(domain_name: str) -> str:
        return mapping.get(domain_name, connector.name)

    return resolve


def build_query_catalog(conn: sqlite3.Connection) -> QueryCatalog:
    # Fields are taken from tool_catalog (the same source the executor's column
    # maps use) so a plan that validates can always execute. The connection is
    # accepted for API compatibility and to optionally intersect with live
    # columns.
    live_columns = {
        domain: _table_fields(conn, table) for domain, table in DOMAIN_TABLES.items()
    }
    domains: dict[str, DomainCatalog] = {}
    for domain, canonical_fields in DOMAIN_FIELDS.items():
        fields = set(canonical_fields) & live_columns.get(domain, set(canonical_fields))
        if not fields:
            # If introspection found nothing (e.g. a stub db), trust the catalog.
            fields = set(canonical_fields)
        relationship_filter_fields = (
            {"project_code", "name"} if domain in {"contract", "license"} else set()
        )
        record_scope = (
            RecordScope(mode="none") if domain == "seal" else RecordScope()
        )
        domains[domain] = DomainCatalog(
            domain=domain,
            table=DOMAIN_TABLES[domain],
            fields=fields,
            identity_fields=IDENTITY_FIELDS.get(domain, set()) & fields,
            field_aliases={
                alias: target
                for alias, target in FIELD_ALIASES.get(domain, {}).items()
                if target in fields
            },
            relationship_filter_fields=relationship_filter_fields,
            record_scope=record_scope,
        )

    domains["cross_domain"] = _cross_domain_catalog()
    domains = _with_field_semantics(domains, conn, lambda _name: DEMO_SOURCE_NAME)
    return QueryCatalog(domains=domains)


def _cross_domain_catalog() -> DomainCatalog:
    # Demo glue: the cross_domain pseudo-domain spans the legal demo tables. It
    # will move into the connector layer in 阶段4.
    return DomainCatalog(
        domain="cross_domain",
        table="",
        fields=set(CROSS_DOMAIN_RETURN_FIELDS),
        identity_fields=set(),
        field_aliases={},
        relationship_filter_fields=set(CROSS_DOMAIN_FILTER_FIELDS),
    )


def build_query_catalog_from_connector(
    connector: "DataConnector",
    conn: sqlite3.Connection | None = None,
    *,
    exclude_domains: "frozenset[str] | set[str] | None" = None,
) -> QueryCatalog:
    """Build a gateway ``QueryCatalog`` from a connector's declared catalog.

    Proves the gateway's field catalog can be sourced from a read-through
    connector instead of hard-coded legal tables. When ``conn`` is given, the
    declared fields are intersected with live columns exactly as
    ``build_query_catalog`` does, so a connector backed by the same SQLite demo
    produces an equivalent catalog. The ``cross_domain`` pseudo-domain is still
    demo glue added here (see 阶段4).

    ``exclude_domains`` drops the named domains before building — the live
    catalog filter for a console-disconnected source (v0.4.0 §C C5). Excluded
    domains never reach the planner, validation, or authorization, so a query
    against a disconnected source fails closed with ``unsupported_domain``.
    """
    excluded = exclude_domains or frozenset()
    domains: dict[str, DomainCatalog] = {}
    for connector_domain in connector.catalog():
        if connector_domain.name in excluded:
            continue
        declared = {connector_field.name for connector_field in connector_domain.fields}
        if conn is not None:
            live = _table_fields(conn, connector_domain.table)
            fields = (declared & live) or declared
        else:
            fields = set(declared)
        identity = {
            connector_field.name
            for connector_field in connector_domain.fields
            if connector_field.is_identity
        } & fields
        field_aliases = {
            alias: connector_field.name
            for connector_field in connector_domain.fields
            for alias in connector_field.aliases
            if connector_field.name in fields
        }
        domains[connector_domain.name] = DomainCatalog(
            domain=connector_domain.name,
            table=connector_domain.table,
            fields=fields,
            identity_fields=identity,
            field_aliases=field_aliases,
            relationship_filter_fields=set(connector_domain.relationship_filter_fields),
            record_scope=connector_domain.record_scope,
        )
    domains["cross_domain"] = _cross_domain_catalog()
    domains = _with_field_semantics(domains, conn, source_of_domain_for_connector(connector))
    return QueryCatalog(domains=domains)


def catalog_context_for_prompt(catalog: QueryCatalog) -> str:
    payload: dict[str, object] = {
        "supported_operations": sorted(SUPPORTED_OPERATIONS),
        "supported_operators": sorted(SUPPORTED_OPERATORS),
        "filter_shape": {"field": "<field>", "operator": "<operator>", "value": "<value>"},
        "cross_domain_usage": (
            "Use domain 'cross_domain' for free-text searches that span projects, "
            "contracts, and licenses. Provide exactly one filter with field 'q', "
            "operator 'contains', and the search term as value."
        ),
        "identity_usage": (
            "For a bare entity token whose kind the user did NOT pin down — a code or "
            "a name, a full name or a fragment (e.g. 'MOON', '月之子', 'nova', '山海') — "
            "use a single filter {field:'identity', operator:'contains', value:<token>} "
            "on a domain that lists identity_fields. The server matches it against all "
            "identity fields and disambiguates. Only when the user explicitly says it is "
            "the code/name (项目代号/项目名称) should you filter the specific identity field "
            "with operator 'eq'."
        ),
        "domains": {
            domain: {
                "fields": sorted(domain_catalog.fields),
                "identity_fields": sorted(domain_catalog.identity_fields),
                "field_aliases": domain_catalog.field_aliases,
                "relationship_filter_fields": sorted(domain_catalog.relationship_filter_fields),
                "virtual_filter_fields": (
                    [VIRTUAL_IDENTITY_FIELD] if domain_catalog.identity_fields else []
                ),
                **(
                    {"field_semantics": _field_semantics_payload(domain_catalog.field_semantics)}
                    if domain_catalog.field_semantics
                    else {}
                ),
            }
            for domain, domain_catalog in sorted(catalog.domains.items())
        },
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _field_semantics_payload(
    field_semantics: dict[str, FieldSemantics],
) -> dict[str, dict[str, object]]:
    """The per-field semantic metadata as a prompt-injectable dict (v0.5.2).

    Only the non-empty parts of each field's entry are emitted, so a description-
    only field doesn't carry empty synonym/example arrays into the prompt.
    """
    payload: dict[str, dict[str, object]] = {}
    for name, semantics in sorted(field_semantics.items()):
        entry: dict[str, object] = {}
        if semantics.description:
            entry["description"] = semantics.description
        if semantics.examples:
            entry["examples"] = list(semantics.examples)
        if semantics.synonyms:
            entry["synonyms"] = list(semantics.synonyms)
        if entry:
            payload[name] = entry
    return payload


def _table_fields(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        rows = conn.execute(f"pragma table_info({table})").fetchall()
    except sqlite3.Error:
        return set()
    excluded = {"id", "project_id", "created_at", "updated_at"}
    return {str(row["name"]) for row in rows if str(row["name"]) not in excluded}
