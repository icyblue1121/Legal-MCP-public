"""Guided local setup helpers for Legal-MCP clients."""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any

from legal_mcp.audit import DEFAULT_AUDIT_PATH
from legal_mcp.cli import DEFAULT_DATABASE_PATH
from legal_mcp.db import initialize_database

SERVER_NAME = "legal-mcp"


def default_config_path(client: str) -> Path:
    home = Path.home()
    if client == "claude-code":
        return home / ".claude.json"
    if client == "claude":
        if platform.system() == "Darwin":
            return home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
        if platform.system() == "Windows":
            return home / "AppData" / "Roaming" / "Claude" / "claude_desktop_config.json"
        return home / ".config" / "Claude" / "claude_desktop_config.json"
    if client == "cursor":
        return home / ".cursor" / "mcp.json"
    if client == "windsurf":
        return home / ".codeium" / "windsurf" / "mcp_config.json"
    if client == "vscode":
        system = platform.system()
        if system == "Darwin":
            return home / "Library" / "Application Support" / "Code" / "User" / "mcp.json"
        if system == "Windows":
            return home / "AppData" / "Roaming" / "Code" / "User" / "mcp.json"
        return home / ".config" / "Code" / "User" / "mcp.json"
    if client == "codex":
        return home / ".codex" / "config.toml"
    if client == "generic":
        return home / ".legal-mcp" / "legal-mcp-stdio.json"
    raise ValueError(f"unknown client: {client}")


def build_stdio_server_config(
    database_path: str | Path = DEFAULT_DATABASE_PATH,
    audit_path: str | Path = DEFAULT_AUDIT_PATH,
    *,
    command: str = SERVER_NAME,
) -> dict[str, Any]:
    return {
        "type": "stdio",
        "command": command,
        "args": [
            "serve",
            "--db",
            str(database_path),
            "--audit-log",
            str(audit_path),
        ],
    }


def build_proxy_server_config(
    *,
    remote_url: str,
    api_key: str,
    command: str = SERVER_NAME,
) -> dict[str, Any]:
    return {
        "type": "stdio",
        "command": command,
        "args": [
            "proxy",
            "--url",
            remote_url,
            "--api-key",
            api_key,
        ],
    }


def configure_client(
    client: str,
    *,
    config_path: str | Path | None = None,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
    audit_path: str | Path = DEFAULT_AUDIT_PATH,
    command: str = SERVER_NAME,
    remote_url: str | None = None,
    api_key: str | None = None,
    token: str | None = None,
) -> Path:
    resolved_api_key = api_key or token
    if remote_url:
        if not resolved_api_key:
            raise ValueError("api_key is required when remote_url is provided")
        server_config = build_proxy_server_config(
            remote_url=remote_url,
            api_key=resolved_api_key,
            command=command,
        )
    else:
        initialize_database(database_path)
        server_config = build_stdio_server_config(database_path, audit_path, command=command)
    path = Path(config_path) if config_path is not None else default_config_path(client)
    if client == "claude":
        write_claude_config(path, database_path, audit_path, command=command, server_config=server_config)
    elif client == "claude-code":
        configure_claude_code(server_config)
    elif client == "cursor":
        write_cursor_config(path, database_path, audit_path, command=command, server_config=server_config)
    elif client == "windsurf":
        write_windsurf_config(path, database_path, audit_path, command=command, server_config=server_config)
    elif client == "vscode":
        write_vscode_config(path, database_path, audit_path, command=command, server_config=server_config)
    elif client == "codex":
        write_codex_config(path, database_path, audit_path, command=command, server_config=server_config)
    elif client == "generic":
        write_generic_stdio_config(path, database_path, audit_path, command=command, server_config=server_config)
    else:
        raise ValueError(f"unknown client: {client}")
    return path


def configure_claude_code(server_config: dict[str, Any]) -> None:
    claude_command = _resolve_command("claude")
    server_command = _resolve_command(str(server_config["command"]))
    args = [str(arg) for arg in server_config.get("args", [])]

    subprocess.run(
        [claude_command, "mcp", "remove", SERVER_NAME],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.run(
        [
            claude_command,
            "mcp",
            "add",
            "--transport",
            "stdio",
            "--scope",
            "user",
            SERVER_NAME,
            "--",
            server_command,
            *args,
        ],
        check=True,
    )


def write_claude_config(
    config_path: str | Path,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
    audit_path: str | Path = DEFAULT_AUDIT_PATH,
    *,
    command: str = SERVER_NAME,
    server_config: dict[str, Any] | None = None,
) -> None:
    _write_json_mcp_config(
        config_path, database_path, audit_path, command=command, server_config=server_config
    )


def write_cursor_config(
    config_path: str | Path,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
    audit_path: str | Path = DEFAULT_AUDIT_PATH,
    *,
    command: str = SERVER_NAME,
    server_config: dict[str, Any] | None = None,
) -> None:
    _write_json_mcp_config(
        config_path, database_path, audit_path, command=command, server_config=server_config
    )


def write_windsurf_config(
    config_path: str | Path,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
    audit_path: str | Path = DEFAULT_AUDIT_PATH,
    *,
    command: str = SERVER_NAME,
    server_config: dict[str, Any] | None = None,
) -> None:
    _write_json_mcp_config(
        config_path, database_path, audit_path, command=command, server_config=server_config
    )


def write_vscode_config(
    config_path: str | Path,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
    audit_path: str | Path = DEFAULT_AUDIT_PATH,
    *,
    command: str = SERVER_NAME,
    server_config: dict[str, Any] | None = None,
) -> None:
    path = Path(config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding="utf-8").strip():
        config = json.loads(path.read_text(encoding="utf-8"))
    else:
        config = {}
    servers = config.setdefault("servers", {})
    servers[SERVER_NAME] = server_config or build_stdio_server_config(
        database_path, audit_path, command=command
    )
    path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_generic_stdio_config(
    config_path: str | Path,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
    audit_path: str | Path = DEFAULT_AUDIT_PATH,
    *,
    command: str = SERVER_NAME,
    server_config: dict[str, Any] | None = None,
) -> None:
    path = Path(config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    config = {SERVER_NAME: server_config or build_stdio_server_config(database_path, audit_path, command=command)}
    path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_codex_config(
    config_path: str | Path,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
    audit_path: str | Path = DEFAULT_AUDIT_PATH,
    *,
    command: str = SERVER_NAME,
    server_config: dict[str, Any] | None = None,
) -> None:
    path = Path(config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    cleaned = _remove_toml_table(existing, f"mcp_servers.{SERVER_NAME}").rstrip()
    server_config = server_config or build_stdio_server_config(database_path, audit_path, command=command)
    block = [
        f'[mcp_servers."{SERVER_NAME}"]',
        f'command = "{_toml_escape(server_config["command"])}"',
        "args = [",
        *[f'  "{_toml_escape(arg)}",' for arg in server_config["args"]],
        "]",
        "",
    ]
    content = "\n".join(line for line in [cleaned, "\n".join(block)] if line)
    path.write_text(content + "\n", encoding="utf-8")


def _write_json_mcp_config(
    config_path: str | Path,
    database_path: str | Path,
    audit_path: str | Path,
    *,
    command: str,
    server_config: dict[str, Any] | None = None,
) -> None:
    path = Path(config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding="utf-8").strip():
        config = json.loads(path.read_text(encoding="utf-8"))
    else:
        config = {}
    mcp_servers = config.setdefault("mcpServers", {})
    mcp_servers[SERVER_NAME] = server_config or build_stdio_server_config(
        database_path, audit_path, command=command
    )
    path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _remove_toml_table(content: str, table_name: str) -> str:
    target_headers = {f"[{table_name}]", f'[{table_name.split(".", 1)[0]}."{SERVER_NAME}"]'}
    lines = content.splitlines()
    kept: list[str] = []
    skipping = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            skipping = stripped in target_headers
            if skipping:
                continue
        if not skipping:
            kept.append(line)
    return "\n".join(kept)


def _toml_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _resolve_command(command: str) -> str:
    return shutil.which(command) or command
