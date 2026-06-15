"""Composite read-through connector (pivot v0.3).

Routes each gateway domain to the connector that owns it, so one deployment can
mix sources — e.g. ``project`` from a real Feishu Bitable and ``contract`` /
``license`` from the local SQLite demo. This is what makes the north-star
scenario real: the gateway answers a question with fields drawn from whichever
source holds them, while authorization and audit stay uniform in the gateway.

The routing map (``domain -> connector``) is built by ``connector_config`` from a
reviewable config file; the composite itself is dumb plumbing with no policy.
"""

from __future__ import annotations

from typing import Any

from legal_mcp.connectors.base import ConnectorDomain, ConnectorQuery, DataConnector


class CompositeConnector:
    """A connector that dispatches by domain to one of several sub-connectors.

    A domain may be served by *several* sources (v0.4.9): the route value is an
    ordered list whose first entry is the primary source and the rest are
    fallbacks queried only when an earlier source returns no rows. ``query`` /
    ``catalog`` always use the primary, so single-source behavior is unchanged;
    multi-source fallback is orchestrated by the caller via
    :meth:`sources_for_domain`.
    """

    name = "composite"

    def __init__(
        self, routes: dict[str, DataConnector | list[DataConnector] | tuple[DataConnector, ...]]
    ) -> None:
        if not routes:
            raise ValueError("composite connector requires at least one route")
        self._routes: dict[str, tuple[DataConnector, ...]] = {}
        for domain, connectors in routes.items():
            ordered = (
                tuple(connectors) if isinstance(connectors, (list, tuple)) else (connectors,)
            )
            if not ordered:
                raise ValueError(f"domain {domain!r} routes to no connector")
            self._routes[domain] = ordered

    def sources_for_domain(self, domain: str) -> tuple[DataConnector, ...]:
        """All connectors serving a domain, primary first."""
        return self._routes.get(domain, ())

    def routes(self) -> dict[str, tuple[DataConnector, ...]]:
        """A copy of the routing table (v0.5.6), so runtime-registered DB sources
        can be merged into a fresh composite without mutating this one."""
        return dict(self._routes)

    def domain_sources(self) -> dict[str, str]:
        """Map each served domain to the name of the sub-connector that owns it.

        Lets the admin Data Sources view group domains by their real source
        (e.g. ``sqlite_demo`` vs ``feishu_bitable``) without reaching into the
        routing table. A multi-source domain reports its primary source.
        """
        return {domain: connectors[0].name for domain, connectors in self._routes.items()}

    def catalog(self) -> tuple[ConnectorDomain, ...]:
        domains: list[ConnectorDomain] = []
        for domain_name in sorted(self._routes):
            for connector in self._routes[domain_name]:
                domain = next(
                    (d for d in connector.catalog() if d.name == domain_name), None
                )
                if domain is None:
                    raise ValueError(
                        f"connector {connector.name!r} does not expose routed domain {domain_name!r}"
                    )
            primary = self._routes[domain_name][0]
            domains.append(
                next(d for d in primary.catalog() if d.name == domain_name)
            )
        return tuple(domains)

    def query(self, query: ConnectorQuery) -> list[dict[str, Any]]:
        connectors = self._routes.get(query.domain)
        if not connectors:
            raise ValueError(f"no connector routes domain: {query.domain}")
        return connectors[0].query(query)
