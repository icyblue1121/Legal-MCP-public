"""Declarative connector configuration (pivot v0.3).

A reviewable, git-committable YAML file selects and wires the read-through
sources, and a bad/incomplete file fails closed (the server refuses to start)
rather than silently falling back to a wider source.

Secrets are never in the file. A Feishu source names *environment variables* for
its ``app_id`` / ``app_secret``; the file holds only non-secret resource ids
(``app_token``, table ids) and the field declaration. Loading reads the env, so
the same config is safe to commit.

The result is a :class:`ConnectorSetup`:

* ``connector`` — a :class:`CompositeConnector` whose catalog is the union of the
  configured sources (Feishu-served domains + the local SQLite demo for the rest);
* ``connector_domains`` — the domains routed through the connector retrieval path
  (:mod:`legal_mcp.connector_retrieval`). Domains *not* in this set stay on the
  SQLite ``search_tools`` path, unchanged.

Example::

    version: 1
    sources:
      - type: feishu_bitable
        app_token: bascnDemoAppToken      # non-secret resource id
        app_id_env: FEISHU_APP_ID          # secret, read from env
        app_secret_env: FEISHU_APP_SECRET
        domains:
          - name: project
            table_id: tblProject
            fields:
              - {name: project_code, is_identity: true, aliases: ["项目代号"]}
              - {name: name, is_identity: true, aliases: ["项目名称"]}
              - {name: contact_person, aliases: ["对接人"]}
              - {name: legal_bp, aliases: ["法务BP", "法务bp"]}
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from legal_mcp import db
from legal_mcp.connectors.base import DataConnector
from legal_mcp.connectors.composite import CompositeConnector
from legal_mcp.connectors.feishu_bitable import (
    FeishuBitableConfig,
    FeishuBitableConnector,
    FeishuClient,
)
from legal_mcp.connectors.local_file import LocalFileConfig, LocalFileConnector
from legal_mcp.connectors.sqlite_demo import SqliteDemoConnector

_DEFAULT_APP_ID_ENV = "FEISHU_APP_ID"
_DEFAULT_APP_SECRET_ENV = "FEISHU_APP_SECRET"


@dataclass(frozen=True)
class ConnectorSetup:
    """The resolved connectors for a deployment.

    ``connector`` serves the catalog (and the connector retrieval path);
    ``connector_domains`` are the domains that route through it rather than the
    local SQLite ``search_tools`` engine.
    """

    connector: DataConnector
    connector_domains: frozenset[str]

    def sources_for(self, domain: str) -> tuple[DataConnector, ...]:
        """All connectors serving a domain, primary first (v0.4.9 fallback order).

        A non-composite connector (tests construct ``ConnectorSetup`` directly)
        serves every domain itself.
        """
        sources_for_domain = getattr(self.connector, "sources_for_domain", None)
        if sources_for_domain is None:
            return (self.connector,)
        return sources_for_domain(domain)


def load_connector_config(
    path: Path | str,
    *,
    database_path: Path | str,
    env: Mapping[str, str] | None = None,
) -> ConnectorSetup:
    """Load a YAML connector config. Requires PyYAML (imported lazily).

    Fails closed: missing credentials, an unknown source type, or a domain claimed
    by two sources raise, so the caller refuses to start instead of degrading to a
    broader or unintended source.
    """
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise ImportError(
            "loading a YAML connector config requires PyYAML (pip install pyyaml)"
        ) from exc
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError("connector config must contain a top-level mapping")
    return build_connector_setup(data, database_path=database_path, env=env)


def build_connector_setup(
    data: dict[str, Any],
    *,
    database_path: Path | str,
    env: Mapping[str, str] | None = None,
    feishu_connector_factory: Any | None = None,
) -> ConnectorSetup:
    """Build a :class:`ConnectorSetup` from parsed config.

    ``feishu_connector_factory(config) -> DataConnector`` is a test seam for
    injecting a fake-client Feishu connector; production passes ``None`` and a
    real credential-bound :class:`FeishuClient` is built from the environment.
    """
    env = os.environ if env is None else env
    sqlite_connector = SqliteDemoConnector(database_path)
    sqlite_domains = {domain.name for domain in sqlite_connector.catalog()}

    # A domain declared by several sources is served in declaration order: the
    # first source is the primary, later ones are fallbacks queried only when an
    # earlier source returns no rows (v0.4.9). Sources serving the same domain
    # must carry distinct names so the user/agent can pick one by name.
    explicit_routes: dict[str, list[DataConnector]] = {}

    for source in data.get("sources") or []:
        source = source or {}
        connector, served = _build_source(
            source, database_path, env, sqlite_domains, feishu_connector_factory
        )
        custom_name = source.get("name")
        if custom_name:
            connector.name = str(custom_name)
        for domain in served:
            siblings = explicit_routes.setdefault(domain, [])
            if any(existing.name == connector.name for existing in siblings):
                raise ValueError(
                    f"domain {domain!r} is served by two sources named "
                    f"{connector.name!r}; give each source a distinct 'name'"
                )
            siblings.append(connector)

    routes: dict[str, list[DataConnector]] = {
        domain: [sqlite_connector] for domain in sqlite_domains
    }
    routes.update(explicit_routes)

    return ConnectorSetup(
        connector=CompositeConnector(routes),
        connector_domains=frozenset(explicit_routes),
    )


def build_source_connector(
    source: dict[str, Any],
    *,
    database_path: Path | str,
    env: Mapping[str, str] | None = None,
) -> tuple[DataConnector, list[str]]:
    """Build one connector from a source declaration (public wrapper, v0.5.7).

    Used by the admin onboarding wizard to *validate* a reviewed declaration by
    actually constructing the connector (a bad declaration raises) before it is
    persisted as an active data source. Same per-type dispatch as the YAML and DB
    registry paths.
    """
    env = os.environ if env is None else env
    sqlite_domains = {domain.name for domain in SqliteDemoConnector(database_path).catalog()}
    return _build_source(source, database_path, env, sqlite_domains, None)


def _build_source(
    source: dict[str, Any],
    database_path: Path | str,
    env: Mapping[str, str],
    sqlite_domains: set[str],
    feishu_connector_factory: Any | None,
) -> tuple[DataConnector, list[str]]:
    """Build one connector from a source declaration, dispatching by ``type``.

    Shared by the YAML config path (:func:`build_connector_setup`) and the runtime
    DB-registry path (:func:`effective_connector_setup`), so a source declared in
    either place is built identically (v0.5.6)."""
    source_type = source.get("type")
    if source_type == "feishu_bitable":
        return _build_feishu_source(source, env, feishu_connector_factory)
    if source_type == "local_file":
        return _build_local_file_source(source)
    if source_type == "tencent_docs":
        return _build_tencent_docs_source(source, env)
    if source_type == "sqlite_demo":
        # A fresh instance, so a custom ``name`` never renames the implicit
        # default-route connector shared by unclaimed domains.
        return _build_sqlite_source(source, SqliteDemoConnector(database_path), sqlite_domains)
    raise ValueError(f"unknown connector source type: {source_type!r}")


def _build_sqlite_source(
    source: dict[str, Any],
    sqlite_connector: SqliteDemoConnector,
    sqlite_domains: set[str],
) -> tuple[DataConnector, list[str]]:
    """An explicitly declared local SQLite demo source (v0.4.9).

    Lets a deployment list the governance DB as a named fallback (or primary)
    for domains also served by a remote source. ``domains`` defaults to every
    domain the demo connector exposes.
    """
    declared = source.get("domains")
    if declared is None:
        served = sorted(sqlite_domains)
    else:
        served = [str(d.get("name") if isinstance(d, dict) else d) for d in declared]
        unknown = [d for d in served if d not in sqlite_domains]
        if unknown:
            raise ValueError(f"sqlite_demo source declares unknown domains: {unknown}")
    if not served:
        raise ValueError("sqlite_demo source declares no domains")
    return sqlite_connector, served


def _build_local_file_source(
    source: dict[str, Any],
) -> tuple[DataConnector, list[str]]:
    """A local-file source (CSV/XLSX/JSON/JSONL/MD), v0.5.5.

    No credentials: paths are non-secret and committed declarations name the file
    and its reviewed columns. Fails closed on an empty domain set.
    """
    config = LocalFileConfig.from_dict({"domains": source.get("domains")})
    served = [domain.name for domain in config.domains]
    if not served:
        raise ValueError("local_file source declares no domains")
    return LocalFileConnector(config), served


def _build_tencent_docs_source(
    source: dict[str, Any],
    env: Mapping[str, str],
) -> tuple[DataConnector, list[str]]:
    """A Tencent Docs smart-table online source (v0.5.9).

    The access token is read from an env var (``access_token_env``, default
    ``TENCENT_DOCS_TOKEN``) — never committed. Fails closed on a missing token or
    an empty domain set."""
    from legal_mcp.connectors.tencent_docs import (
        TencentDocsClient,
        TencentDocsConfig,
        TencentDocsConnector,
    )

    config = TencentDocsConfig.from_dict(
        {"file_id": source.get("file_id"), "domains": source.get("domains")}
    )
    served = [domain.name for domain in config.domains]
    if not served:
        raise ValueError("tencent_docs source declares no domains")
    token = _required_env(env, source.get("access_token_env", "TENCENT_DOCS_TOKEN"))
    client_kwargs: dict[str, Any] = {"access_token": token, "file_id": config.file_id}
    if source.get("base_url"):
        client_kwargs["base_url"] = source["base_url"]
    return TencentDocsConnector(config, TencentDocsClient(**client_kwargs)), served


def _build_feishu_source(
    source: dict[str, Any],
    env: Mapping[str, str],
    factory: Any | None,
) -> tuple[DataConnector, list[str]]:
    config = FeishuBitableConfig.from_dict(
        {"app_token": _app_token(source, env), "domains": source.get("domains")}
    )
    served = [domain.name for domain in config.domains]
    if not served:
        raise ValueError("feishu source declares no domains")

    if factory is not None:
        return factory(config), served

    app_id = _required_env(env, source.get("app_id_env", _DEFAULT_APP_ID_ENV))
    app_secret = _required_env(env, source.get("app_secret_env", _DEFAULT_APP_SECRET_ENV))
    client_kwargs: dict[str, Any] = {
        "app_id": app_id,
        "app_secret": app_secret,
        "app_token": config.app_token,
    }
    if source.get("base_url"):
        client_kwargs["base_url"] = source["base_url"]
    return FeishuBitableConnector(config, FeishuClient(**client_kwargs)), served


def _app_token(source: dict[str, Any], env: Mapping[str, str]) -> str | None:
    token = source.get("app_token")
    if token:
        return token
    token_env = source.get("app_token_env")
    if token_env:
        return _required_env(env, token_env)
    return None  # FeishuBitableConfig.from_dict raises a clear error if absent.


def _required_env(env: Mapping[str, str], name: str) -> str:
    value = env.get(name)
    if not value:
        raise ValueError(
            f"connector config requires environment variable {name!r} to be set"
        )
    return value


# Effective setup cache keyed by (db path, base identity, active-sources fingerprint),
# so the per-request rebuild only happens when the registry actually changed (v0.5.6).
_EFFECTIVE_CACHE: dict[tuple[str, int, tuple[int, str]], ConnectorSetup] = {}


def effective_connector_setup(
    base: ConnectorSetup | None,
    database_path: Path | str,
    *,
    env: Mapping[str, str] | None = None,
) -> ConnectorSetup | None:
    """Merge runtime-registered ``data_sources`` (active) into the static setup (v0.5.6).

    Returns ``base`` unchanged when no active DB-registered source exists. Otherwise
    it unions the DB sources into the routing table — so a domain a DB source serves
    joins the live catalog and routes through the connector path *without a restart*.
    When ``base`` is ``None`` (no YAML config), a pure-SQLite base is synthesized so
    DB sources work on their own. Result is cached on the registry fingerprint.

    Authorization is unchanged: a DB-registered domain flows through the same field
    gate + record scope as any other, so an unconfigured grant stays default-deny.
    """
    conn = db.connect(database_path)
    try:
        active = db.active_data_sources(conn)
        fingerprint = db.data_sources_fingerprint(conn)
    finally:
        conn.close()
    if not active:
        return base

    env = os.environ if env is None else env
    cache_key = (str(database_path), id(base), fingerprint)
    cached = _EFFECTIVE_CACHE.get(cache_key)
    if cached is not None:
        return cached

    if base is not None and hasattr(base.connector, "routes"):
        routes: dict[str, list[DataConnector]] = {
            domain: list(connectors) for domain, connectors in base.connector.routes().items()
        }
        base_domains = set(base.connector_domains)
    else:
        # No YAML config (or a non-composite base): synthesize a pure-SQLite base so
        # the DB sources have somewhere to merge into.
        synthesized = build_connector_setup({}, database_path=database_path, env=env)
        routes = {
            domain: list(connectors)
            for domain, connectors in synthesized.connector.routes().items()
        }
        base_domains = set()

    sqlite_domains = {domain.name for domain in SqliteDemoConnector(database_path).catalog()}
    db_domains: set[str] = set()
    for row in active:
        source = json.loads(row["config_json"])
        if not isinstance(source, dict):
            raise ValueError(f"data_source {row['name']!r} config_json is not an object")
        source.setdefault("type", row["type"])
        connector, served = _build_source(source, database_path, env, sqlite_domains, None)
        connector.name = str(row["name"])
        for domain in served:
            siblings = routes.setdefault(domain, [])
            if any(existing.name == connector.name for existing in siblings):
                raise ValueError(
                    f"domain {domain!r} is served by two sources named {connector.name!r}; "
                    "give each data source a distinct name"
                )
            siblings.append(connector)
            db_domains.add(domain)

    setup = ConnectorSetup(
        connector=CompositeConnector(routes),
        connector_domains=frozenset(base_domains | db_domains),
    )
    _EFFECTIVE_CACHE[cache_key] = setup
    return setup
