"""Read-through data connectors (pivot 阶段3).

A connector is the gateway's only door to a data source. The gateway holds
policy + audit; the data stays in its source and is reached through a connector.
Only the lightweight interface types are re-exported here; concrete connectors
(e.g. ``sqlite_demo``) are imported explicitly to avoid import cycles.
"""

from legal_mcp.connectors.base import (
    ConnectorDomain,
    ConnectorField,
    ConnectorQuery,
    DataConnector,
)

__all__ = [
    "ConnectorDomain",
    "ConnectorField",
    "ConnectorQuery",
    "DataConnector",
]
