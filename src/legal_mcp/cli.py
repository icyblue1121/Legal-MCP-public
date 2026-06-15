"""Command-line interface for Legal-MCP."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from legal_mcp import __version__
from legal_mcp.audit import DEFAULT_AUDIT_PATH
from legal_mcp.import_pipeline import import_file


DEFAULT_DATABASE_PATH = Path.home() / ".legal-mcp" / "legal.db"
SETUP_CLIENTS = ("claude", "claude-code", "cursor", "windsurf", "vscode", "codex", "generic")
ADMIN_ROLES = ("admin", "legal", "business", "auditor")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="legal-mcp")
    parser.add_argument(
        "--version",
        action="version",
        version=f"legal-mcp {__version__}",
    )
    subparsers = parser.add_subparsers(dest="command")

    import_parser = subparsers.add_parser("import", help="Import CSV/XLSX data")
    import_parser.add_argument("path", type=Path)
    import_parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DATABASE_PATH,
        help=f"SQLite database path (default: {DEFAULT_DATABASE_PATH})",
    )
    serve_parser = subparsers.add_parser("serve", help="Run the stdio MCP server")
    serve_parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DATABASE_PATH,
        help=f"SQLite database path (default: {DEFAULT_DATABASE_PATH})",
    )
    serve_parser.add_argument(
        "--audit-log",
        type=Path,
        default=DEFAULT_AUDIT_PATH,
        help="Audit log JSONL path",
    )
    serve_parser.add_argument(
        "--update-check-url",
        help="Optional JSON endpoint for non-blocking startup update notices.",
    )
    serve_parser.add_argument(
        "--agent-public-only",
        action="store_true",
        help="Expose only agent_query in tools/list.",
    )
    serve_parser.add_argument(
        "--connector",
        type=Path,
        help=(
            "Optional read-through connector config YAML. When set, the declared "
            "domains are served from their real source (e.g. a Feishu Bitable) "
            "instead of the local SQLite demo; credentials come from env. A bad "
            "config fails closed (refuses to start)."
        ),
    )
    serve_http_parser = subparsers.add_parser("serve-http", help="Run the HTTP MCP server")
    serve_http_parser.add_argument("--host", default="127.0.0.1")
    serve_http_parser.add_argument("--port", type=int, default=8765)
    serve_http_parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DATABASE_PATH,
        help=f"SQLite database path (default: {DEFAULT_DATABASE_PATH})",
    )
    serve_http_parser.add_argument(
        "--audit-log",
        type=Path,
        default=DEFAULT_AUDIT_PATH,
        help="Audit log JSONL path",
    )
    serve_http_parser.add_argument(
        "--legacy-token",
        dest="legacy_token",
        help="Optional shared bearer token for legacy clients. Prefer per-user API keys.",
    )
    serve_http_parser.add_argument(
        "--token",
        dest="legacy_token",
        help=argparse.SUPPRESS,
    )
    serve_http_parser.add_argument(
        "--legacy-token-full-access",
        dest="legacy_token_full_access",
        action="store_true",
        help=(
            "Migration escape hatch: let the legacy shared token bypass field/row "
            "grants (full access). Off by default — the legacy token is fail-closed. "
            "Insecure; use only during migration, prefer per-user API keys."
        ),
    )
    serve_http_parser.add_argument(
        "--trusted-identity-header",
        dest="trusted_identity_header",
        help=(
            "Header name a trusted reverse proxy injects with the authenticated "
            "user's subject (e.g. X-Legal-MCP-User). Maps to users.external_subject. "
            "Requires --trusted-proxy; a header from an untrusted peer is rejected "
            "fail-closed."
        ),
    )
    serve_http_parser.add_argument(
        "--trusted-proxy",
        dest="trusted_proxies",
        action="append",
        default=[],
        metavar="IP_OR_CIDR",
        help=(
            "IP or CIDR of a trusted reverse-proxy peer for --trusted-identity-header. "
            "Repeat for several."
        ),
    )
    serve_http_parser.add_argument(
        "--trusted-header-email-fallback",
        dest="trusted_header_email_fallback",
        action="store_true",
        help=(
            "Let the trusted identity header match users.email when no "
            "external_subject matches. Off by default (pilot convenience only)."
        ),
    )
    serve_http_parser.add_argument(
        "--update-check-url",
        help="Optional JSON endpoint for non-blocking startup update notices.",
    )
    serve_http_parser.add_argument(
        "--allow-origin",
        dest="allowed_origins",
        action="append",
        default=[],
        help="Allowed browser Origin. Repeat for multiple origins.",
    )
    serve_http_parser.add_argument(
        "--agent-public-only",
        action="store_true",
        help="Expose only agent_query in tools/list.",
    )
    serve_http_parser.add_argument(
        "--min-client-version",
        help="Reject HTTP clients older than this Legal-MCP client/proxy version.",
    )
    serve_http_parser.add_argument(
        "--connector",
        type=Path,
        help=(
            "Optional read-through connector config YAML. When set, the declared "
            "domains are served from their real source (e.g. a Feishu Bitable) "
            "instead of the local SQLite demo; credentials come from env. A bad "
            "config fails closed (refuses to start)."
        ),
    )
    serve_admin_parser = subparsers.add_parser("serve-admin", help="Run the admin web server")
    serve_admin_parser.add_argument("--host", default="127.0.0.1")
    serve_admin_parser.add_argument("--port", type=int, default=8766)
    serve_admin_parser.add_argument(
        "--mode",
        choices=("local", "team"),
        default="team",
        help=(
            "Deployment mode. 'team' (default) always requires the admin "
            "password. 'local' skips the password for single-user use, but is "
            "only allowed when --host is a loopback address."
        ),
    )
    serve_admin_parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DATABASE_PATH,
        help=f"SQLite database path (default: {DEFAULT_DATABASE_PATH})",
    )
    serve_admin_parser.add_argument(
        "--connector",
        type=Path,
        default=None,
        help=(
            "Optional read-through connector config YAML (same file the gateway "
            "uses). When set, the Data Sources view and permissions form reflect "
            "the declared sources/domains; otherwise they show the bundled SQLite "
            "demo."
        ),
    )
    admin_parser = subparsers.add_parser("admin", help="Admin bootstrap commands")
    admin_subparsers = admin_parser.add_subparsers(dest="admin_command", required=True)
    create_user_parser = admin_subparsers.add_parser("create-user", help="Create a local user")
    create_user_parser.add_argument("--email", required=True)
    create_user_parser.add_argument("--display-name", required=True)
    create_user_parser.add_argument("--role", choices=ADMIN_ROLES, required=True)
    create_user_parser.add_argument("--password")
    create_user_parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DATABASE_PATH,
        help=f"SQLite database path (default: {DEFAULT_DATABASE_PATH})",
    )
    proxy_parser = subparsers.add_parser("proxy", help="Proxy local stdio MCP to a remote HTTP MCP server")
    proxy_parser.add_argument("--url", required=True, help="Remote HTTP MCP endpoint URL")
    proxy_parser.add_argument("--api-key", dest="api_key", help="Per-user API key for remote HTTP MCP server")
    proxy_parser.add_argument("--token", dest="api_key", help=argparse.SUPPRESS)
    proxy_parser.add_argument("--timeout", type=float, default=30)
    setup_parser = subparsers.add_parser("setup", help="Configure a local MCP client")
    setup_parser.add_argument(
        "--client",
        choices=SETUP_CLIENTS,
        help="Client configuration to write",
    )
    setup_parser.add_argument("--config", type=Path, help="Override client config path")
    setup_parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DATABASE_PATH,
        help=f"SQLite database path (default: {DEFAULT_DATABASE_PATH})",
    )
    setup_parser.add_argument(
        "--audit-log",
        type=Path,
        default=DEFAULT_AUDIT_PATH,
        help=f"Audit log path (default: {DEFAULT_AUDIT_PATH})",
    )
    setup_parser.add_argument(
        "--command",
        dest="server_command",
        default="legal-mcp",
        help="Command clients should run to start Legal-MCP",
    )
    setup_parser.add_argument("--remote-url", help="Remote HTTP MCP endpoint for team proxy mode")
    setup_parser.add_argument("--api-key", dest="api_key", help="Per-user API key for remote HTTP MCP endpoint")
    setup_parser.add_argument("--token", dest="api_key", help=argparse.SUPPRESS)
    doctor_parser = subparsers.add_parser("doctor", help="Validate local install health")
    doctor_parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DATABASE_PATH,
        help=f"SQLite database path (default: {DEFAULT_DATABASE_PATH})",
    )
    doctor_parser.add_argument("--config", type=Path, help="Optional client config path to check")
    doctor_parser.add_argument("--remote-url", help="Remote HTTP MCP endpoint to check")
    doctor_parser.add_argument(
        "--probe-ai",
        action="store_true",
        help="Probe the configured AI endpoint (makes a network request to it)",
    )

    scaffold_parser = subparsers.add_parser(
        "scaffold-connector",
        help="Draft a connector config from a Feishu Bitable's real columns",
    )
    scaffold_parser.add_argument(
        "--app-token", required=True, help="Bitable app token (the bascn… / base/<app_token> id)"
    )
    scaffold_parser.add_argument(
        "--table",
        action="append",
        required=True,
        metavar="DOMAIN:TABLE_ID",
        help="A table to scaffold as 'domain_name:table_id'. Repeatable.",
    )
    scaffold_parser.add_argument(
        "--app-id-env",
        default="FEISHU_APP_ID",
        help="Env var holding the app id (default: FEISHU_APP_ID)",
    )
    scaffold_parser.add_argument(
        "--app-secret-env",
        default="FEISHU_APP_SECRET",
        help="Env var holding the app secret (default: FEISHU_APP_SECRET)",
    )
    scaffold_parser.add_argument("--base-url", help="Override the Feishu Open Platform base URL")
    scaffold_parser.add_argument(
        "--out", type=Path, help="Write the draft here instead of stdout"
    )

    recall_parser = subparsers.add_parser(
        "recall-terms",
        help="Generate / review field recall terms (synonyms) for a data source",
    )
    recall_parser.add_argument(
        "source",
        nargs="?",
        default="sqlite_demo",
        help="Source name to generate under (default: sqlite_demo, the bundled demo)",
    )
    recall_parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DATABASE_PATH,
        help=f"SQLite database path (default: {DEFAULT_DATABASE_PATH})",
    )
    recall_parser.add_argument(
        "--connector",
        type=Path,
        help="Connector config to source the catalog from (default: bundled demo catalog)",
    )
    recall_parser.add_argument(
        "--domain",
        action="append",
        metavar="DOMAIN",
        help="Only generate for this domain. Repeatable.",
    )
    recall_parser.add_argument(
        "--write",
        action="store_true",
        help="Persist generated terms to field_semantics (origin=generated) + audit. "
        "Without it, the run is a dry-run that prints proposals for review.",
    )
    recall_parser.add_argument(
        "--recompute",
        action="store_true",
        help="Overwrite existing generated terms (manual edits are always preserved)",
    )
    recall_parser.add_argument(
        "--out", type=Path, help="Write the review JSON here instead of stdout"
    )
    recall_parser.add_argument(
        "--audit-log",
        type=Path,
        default=DEFAULT_AUDIT_PATH,
        help=f"Audit log path (default: {DEFAULT_AUDIT_PATH})",
    )
    return parser


def _load_connector_or_exit(path: Path | None, database_path: Path):
    """Load a read-through connector config for the server, or exit clearly.

    Fails closed: a missing credential or malformed config exits non-zero rather
    than silently serving every domain from the local SQLite demo."""
    if path is None:
        return None
    from legal_mcp.connector_config import load_connector_config

    try:
        return load_connector_config(path, database_path=database_path)
    except (OSError, ValueError, ImportError) as exc:
        print(f"error: could not load connector config {path}: {exc}", file=sys.stderr)
        raise SystemExit(2)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "import":
        report = import_file(args.path, database_path=args.db)
        _print_import_report(report)
        return 1 if report.errors else 0
    if args.command == "serve":
        from legal_mcp.mcp_server import serve

        serve(
            args.db,
            args.audit_log,
            sys.stdin.buffer,
            sys.stdout.buffer,
            sys.stderr,
            update_check_url=args.update_check_url,
            public_agent_only=True if args.agent_public_only else None,
            connector_setup=_load_connector_or_exit(args.connector, args.db),
        )
        return 0
    if args.command == "serve-http":
        from legal_mcp.http_server import serve_http

        serve_http(
            host=args.host,
            port=args.port,
            database_path=args.db,
            audit_path=args.audit_log,
            bearer_token=args.legacy_token,
            legacy_token_full_access=args.legacy_token_full_access,
            trusted_identity_header=args.trusted_identity_header,
            trusted_proxies=tuple(args.trusted_proxies),
            trusted_header_email_fallback=args.trusted_header_email_fallback,
            allowed_origins=tuple(args.allowed_origins),
            update_check_url=args.update_check_url,
            public_agent_only=True if args.agent_public_only else None,
            min_client_version=args.min_client_version,
            connector_setup=_load_connector_or_exit(args.connector, args.db),
        )
        return 0
    if args.command == "serve-admin":
        from legal_mcp.admin_server import build_admin_server

        try:
            server = build_admin_server(
                host=args.host,
                port=args.port,
                database_path=args.db,
                mode=args.mode,
                connector_setup=_load_connector_or_exit(args.connector, args.db),
            )
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

        from legal_mcp.admin_observability import (
            observability_config,
            start_observability_proxy,
        )

        obs_config = observability_config()
        obs_server = None
        if obs_config.enabled:
            obs_server = start_observability_proxy(
                host=args.host,
                database_path=args.db,
                mode=args.mode,
                config=obs_config,
            )
            print(
                "legal-mcp: Langfuse observability embed available on "
                f"{args.host}:{obs_config.port}",
                file=sys.stderr,
            )
        try:
            server.serve_forever()
        finally:
            server.server_close()
            if obs_server is not None:
                obs_server.shutdown()
                obs_server.server_close()
        return 0
    if args.command == "admin" and args.admin_command == "create-user":
        from legal_mcp import db
        from legal_mcp.identity import create_user, hash_password

        if args.role == "admin" and not args.password:
            print("Error: --password is required when creating an admin user", file=sys.stderr)
            return 2

        db.initialize_database(args.db)
        conn = db.connect(args.db)
        try:
            try:
                create_user(
                    conn,
                    email=args.email,
                    display_name=args.display_name,
                    role=args.role,
                    password_hash=hash_password(args.password) if args.password else None,
                )
            except sqlite3.IntegrityError:
                print(f"Error: user already exists: {args.email}", file=sys.stderr)
                return 1
        finally:
            conn.close()
        print(f"Created user {args.email} ({args.role})")
        return 0
    if args.command == "proxy":
        from legal_mcp.proxy import proxy_stdio

        if not args.api_key:
            print("Error: --api-key is required", file=sys.stderr)
            return 2
        proxy_stdio(url=args.url, token=args.api_key, timeout=args.timeout)
        return 0
    if args.command == "setup":
        from legal_mcp.setup_wizard import configure_client

        client = args.client or _prompt_setup_client()
        config_path = configure_client(
            client,
            config_path=args.config,
            database_path=args.db,
            audit_path=args.audit_log,
            command=args.server_command,
            remote_url=args.remote_url,
            api_key=args.api_key,
        )
        print(f"Configured {client}: {config_path}")
        print(f"Database ready: {args.db}")
        print("You can re-run legal-mcp setup at any time to repair or update this configuration.")
        return 0
    if args.command == "doctor":
        from legal_mcp.doctor import check_install_health

        report = check_install_health(
            database_path=args.db,
            config_path=args.config,
            remote_url=args.remote_url,
            probe_ai=args.probe_ai,
        )
        status = "healthy" if report.healthy else "unhealthy"
        print(f"Legal-MCP doctor: {status}")
        for check in report.checks:
            mark = "ok" if check.ok else "fail"
            print(f"{mark}: {check.message}")
        return 0 if report.healthy else 1

    if args.command == "scaffold-connector":
        return _scaffold_connector(args)

    if args.command == "recall-terms":
        return _recall_terms(args)

    parser.print_help()
    return 0


def _scaffold_connector(args) -> int:
    """Draft a connector config from a Feishu Bitable's real columns (v0.4.0 §D)."""
    import os

    from legal_mcp.connectors.feishu_bitable import (
        FeishuBitableConfig,
        FeishuBitableConnector,
        FeishuClient,
    )
    from legal_mcp.scaffold import scaffold_connector_config

    domains: list[dict] = []
    for spec in args.table:
        domain, _, table_id = spec.partition(":")
        if not domain or not table_id:
            print(f"error: --table must be 'domain:table_id', got {spec!r}", file=sys.stderr)
            return 2
        # Pointer-only: no fields yet — describe_schema fills them from the source.
        domains.append({"name": domain, "table_id": table_id, "fields": []})

    app_id = os.environ.get(args.app_id_env)
    app_secret = os.environ.get(args.app_secret_env)
    if not app_id or not app_secret:
        print(
            f"error: set {args.app_id_env} and {args.app_secret_env} in the environment",
            file=sys.stderr,
        )
        return 2

    client_kwargs = {"app_id": app_id, "app_secret": app_secret, "app_token": args.app_token}
    if args.base_url:
        client_kwargs["base_url"] = args.base_url
    connector = FeishuBitableConnector(
        FeishuBitableConfig.from_dict({"app_token": args.app_token, "domains": domains}),
        FeishuClient(**client_kwargs),
    )

    try:
        draft = scaffold_connector_config(
            connector,
            app_token=args.app_token,
            app_id_env=args.app_id_env,
            app_secret_env=args.app_secret_env,
        )
    except Exception as exc:  # noqa: BLE001 - surface any introspection failure clearly
        print(f"error: could not introspect the source: {exc}", file=sys.stderr)
        return 1

    if args.out:
        args.out.write_text(draft, encoding="utf-8")
        print(f"wrote draft connector config to {args.out} — review before use.")
    else:
        sys.stdout.write(draft)
    return 0


def _recall_terms(args) -> int:
    """Generate / review field recall terms for a source (v0.5.3).

    Dry-run by default (prints proposals for governance review); ``--write``
    persists them under ``origin='generated'`` and records an audit event. Manual
    edits are never overwritten; existing generated rows need ``--recompute``."""
    from legal_mcp import db
    from legal_mcp.ai_provider import ConfiguredAIProvider
    from legal_mcp.audit import write_audit_record
    from legal_mcp.query_catalog import (
        build_query_catalog,
        build_query_catalog_from_connector,
        source_of_domain_for_connector,
    )
    from legal_mcp.recall_terms import (
        generate_recall_terms,
        persist_recall_terms,
        proposals_to_json,
    )

    db.initialize_database(args.db)
    conn = db.connect(args.db)
    try:
        if args.connector is not None:
            setup = _load_connector_or_exit(args.connector, args.db)
            if setup is None:
                print("error: --connector did not yield a usable connector", file=sys.stderr)
                return 2
            catalog = build_query_catalog_from_connector(setup.connector, conn)
            # Write each domain's terms under the sub-source that serves it, so the
            # live catalog (which resolves per domain) reads them back.
            source = source_of_domain_for_connector(setup.connector)
        else:
            catalog = build_query_catalog(conn)
            source = args.source

        domains = frozenset(args.domain) if args.domain else None
        provider = ConfiguredAIProvider(args.db)
        proposals = generate_recall_terms(catalog, provider, domains=domains)

        review_json = proposals_to_json(source, proposals)
        if args.out:
            args.out.write_text(review_json, encoding="utf-8")
            print(f"wrote recall-term review to {args.out}")
        else:
            sys.stdout.write(review_json + "\n")

        non_empty = sum(1 for proposal in proposals if proposal.terms)
        if non_empty == 0:
            print(
                "warning: no recall terms generated — is an AI provider configured? "
                "(see `legal-mcp doctor --probe-ai`)",
                file=sys.stderr,
            )

        if not args.write:
            print(
                f"dry run: {non_empty}/{len(proposals)} fields produced terms. "
                "Re-run with --write to persist.",
                file=sys.stderr,
            )
            return 0

        summary = persist_recall_terms(
            conn, source, proposals, recompute=args.recompute
        )
        write_audit_record(
            tool_name="recall_terms.generate",
            rationale="onboarding-time recall-term generation",
            source_client="cli",
            arguments={
                "source": args.source if args.connector is None else "<connector>",
                "recompute": args.recompute,
                "written": summary.written,
                "skipped_manual": summary.skipped_manual,
                "skipped_existing_generated": summary.skipped_existing_generated,
            },
            result_status="ok",
            error_code=None,
            audit_path=args.audit_log,
        )
        print(
            f"wrote {summary.written} field(s); "
            f"kept {summary.skipped_manual} manual, "
            f"skipped {summary.skipped_existing_generated} existing generated "
            "(use --recompute to overwrite)."
        )
        return 0
    finally:
        conn.close()


def _prompt_setup_client() -> str:
    print("Choose an MCP client to configure:")
    for index, client in enumerate(SETUP_CLIENTS, start=1):
        print(f"  {index}. {client}")
    while True:
        answer = input("Client [cursor]: ").strip().lower()
        if not answer:
            return "cursor"
        if answer in SETUP_CLIENTS:
            return answer
        if answer.isdigit():
            selected = int(answer)
            if 1 <= selected <= len(SETUP_CLIENTS):
                return SETUP_CLIENTS[selected - 1]
        print("Please enter a client name or number.")


def _print_import_report(report) -> None:
    print(f"Import complete: {report.source_rows} source rows processed")
    for entity, counts in report.counts.items():
        if any(counts.values()):
            print(
                f"{entity}: "
                f"{counts['created']} created, "
                f"{counts['updated']} updated, "
                f"{counts['skipped']} skipped, "
                f"{counts['failed']} failed"
            )
    if report.warnings:
        print("Warnings:")
        for warning in report.warnings:
            print(
                f"- {warning.file_name} row {warning.row_number} "
                f"field {warning.field_name}: {warning.error_code} - {warning.message}"
            )
    if report.errors:
        print("Errors:")
        for error in report.errors:
            print(
                f"- {error.file_name} row {error.row_number} "
                f"field {error.field_name}: {error.error_code} - {error.message}"
            )
