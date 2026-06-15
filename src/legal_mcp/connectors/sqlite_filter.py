"""Shared SQLite filter translation for connector push-down (v0.5.5).

Both the bundled SQLite demo connector and the ``local_file`` connector (which
loads a flat file into an in-memory SQLite table) translate the gateway's
operator-aware :class:`~legal_mcp.connectors.base.ConnectorFilter` predicates into
parameterized SQL the same way. Keeping that translation here is the single source
of truth, so a local-file query finds rows by exactly the same operator semantics
(case-insensitive ``eq``, ``LIKE`` ``contains``, ``in``, ``is_empty``, the date
comparisons, and the ``or_fields`` OR-group) as the demo source.

Columns are never taken from user input: the caller supplies a ``column_for``
resolver that maps a catalog-declared field to a safe SQL column reference; values
are always parameterized.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from legal_mcp.connectors.base import ConnectorFilter


def condition(column: str, operator: str, value: Any, params: list[Any]) -> str:
    """One pushed-down filter as a parameterized SQL predicate.

    ``eq`` is case-insensitive (``collate nocase``) and ``contains`` uses ``LIKE``
    (case-insensitive for ASCII in SQLite), matching the fuzzy/lenient matching the
    SQLite *direct* path (``search_tools``) gives. ``in`` is a parameterized
    membership test. ``is_empty`` and the ``date_*`` comparisons (v0.5.1) mirror
    ``search_tools._condition``; ``date_between`` carries a ``[start, end]`` pair.
    """
    if operator == "eq":
        params.append(value)
        return f"{column} = ? collate nocase"
    if operator == "contains":
        params.append(f"%{value}%")
        return f"{column} like ?"
    if operator == "in":
        values = list(value) if isinstance(value, list | tuple | set) else [value]
        if not values:
            return "0 = 1"  # an empty IN matches nothing — never the whole table.
        params.extend(values)
        return f"{column} in ({', '.join('?' for _ in values)})"
    if operator == "is_empty":
        return f"({column} is null or {column} = '')"
    if operator == "date_before":
        params.append(value)
        return f"{column} < ?"
    if operator == "date_after":
        params.append(value)
        return f"{column} > ?"
    if operator == "date_between":
        start, end = value
        params.extend([start, end])
        return f"{column} between ? and ?"
    raise ValueError(f"sqlite connector cannot push down operator: {operator}")


def build_where(
    filters: tuple[ConnectorFilter, ...],
    column_for: Callable[[str], str],
    params: list[Any],
) -> list[str]:
    """Translate operator-aware filters into a list of AND-ed SQL predicates.

    ``column_for(field)`` resolves a field name to a safe column reference (and may
    have side effects, e.g. adding a JOIN). An ``or_fields`` filter becomes one
    parenthesized OR group so it AND-composes safely with any other filter — the
    virtual ``identity`` token over (project_code, name) shape (v0.4.8).
    """
    where: list[str] = []
    for query_filter in filters:
        if query_filter.or_fields:
            ors = [
                condition(column_for(field), query_filter.operator, query_filter.value, params)
                for field in query_filter.or_fields
            ]
            where.append("(" + " or ".join(ors) + ")")
        else:
            where.append(
                condition(
                    column_for(query_filter.field),
                    query_filter.operator,
                    query_filter.value,
                    params,
                )
            )
    return where
