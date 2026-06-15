import json
import tomllib

from legal_mcp.doctor import check_install_health
from legal_mcp.setup_wizard import (
    build_proxy_server_config,
    build_stdio_server_config,
    configure_client,
    write_claude_config,
    write_codex_config,
    write_cursor_config,
    write_generic_stdio_config,
    write_vscode_config,
    write_windsurf_config,
)


def test_build_stdio_server_config_uses_serve_command_and_paths(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    audit_path = tmp_path / "audit.jsonl"

    config = build_stdio_server_config(database_path, audit_path, command="legal-mcp")

    assert config == {
        "type": "stdio",
        "command": "legal-mcp",
        "args": [
            "serve",
            "--db",
            str(database_path),
            "--audit-log",
            str(audit_path),
        ],
    }


def test_build_stdio_server_config_can_use_remote_proxy() -> None:
    config = build_proxy_server_config(
        remote_url="http://legal.internal:8765/mcp",
        api_key="lmcp_user-api-key",
        command="legal-mcp",
    )

    assert config == {
        "type": "stdio",
        "command": "legal-mcp",
        "args": [
            "proxy",
            "--url",
            "http://legal.internal:8765/mcp",
            "--api-key",
            "lmcp_user-api-key",
        ],
    }


def test_json_client_writers_merge_legal_mcp_server(tmp_path) -> None:
    claude_path = tmp_path / "claude_desktop_config.json"
    cursor_path = tmp_path / "mcp.json"
    windsurf_path = tmp_path / "mcp_config.json"
    claude_path.write_text(
        json.dumps({"mcpServers": {"existing": {"command": "node"}}}),
        encoding="utf-8",
    )

    write_claude_config(claude_path, tmp_path / "legal.db", tmp_path / "audit.jsonl")
    write_cursor_config(cursor_path, tmp_path / "legal.db", tmp_path / "audit.jsonl")
    write_windsurf_config(windsurf_path, tmp_path / "legal.db", tmp_path / "audit.jsonl")

    claude_config = json.loads(claude_path.read_text(encoding="utf-8"))
    cursor_config = json.loads(cursor_path.read_text(encoding="utf-8"))
    windsurf_config = json.loads(windsurf_path.read_text(encoding="utf-8"))
    assert "existing" in claude_config["mcpServers"]
    assert claude_config["mcpServers"]["legal-mcp"]["command"] == "legal-mcp"
    assert cursor_config["mcpServers"]["legal-mcp"]["args"][0] == "serve"
    assert windsurf_config["mcpServers"]["legal-mcp"]["type"] == "stdio"


def test_vscode_writer_uses_servers_convention(tmp_path) -> None:
    config_path = tmp_path / "mcp.json"
    config_path.write_text(
        json.dumps({"servers": {"existing": {"command": "node"}}, "inputs": []}),
        encoding="utf-8",
    )

    write_vscode_config(config_path, tmp_path / "legal.db", tmp_path / "audit.jsonl")

    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert "existing" in config["servers"]
    assert config["servers"]["legal-mcp"]["type"] == "stdio"
    assert config["servers"]["legal-mcp"]["args"][0] == "serve"
    assert config["inputs"] == []


def test_codex_writer_merges_toml_mcp_server(tmp_path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('model = "gpt-5.4"\n', encoding="utf-8")

    write_codex_config(config_path, tmp_path / "legal.db", tmp_path / "audit.jsonl")

    parsed = tomllib.loads(config_path.read_text(encoding="utf-8"))
    assert parsed["model"] == "gpt-5.4"
    assert parsed["mcp_servers"]["legal-mcp"]["command"] == "legal-mcp"
    assert parsed["mcp_servers"]["legal-mcp"]["args"][0] == "serve"


def test_generic_stdio_writer_outputs_server_config(tmp_path) -> None:
    config_path = tmp_path / "legal-mcp-stdio.json"

    write_generic_stdio_config(config_path, tmp_path / "legal.db", tmp_path / "audit.jsonl")

    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert config["legal-mcp"]["type"] == "stdio"


def test_configure_claude_code_uses_claude_mcp_user_scope(monkeypatch) -> None:
    commands = []

    monkeypatch.setattr("legal_mcp.setup_wizard.shutil.which", lambda command: f"C:/Tools/{command}.exe")

    def fake_run(command, **kwargs):
        commands.append((command, kwargs))
        class CompletedProcess:
            returncode = 0

        return CompletedProcess()

    monkeypatch.setattr("legal_mcp.setup_wizard.subprocess.run", fake_run)

    config_path = configure_client(
        "claude-code",
        remote_url="http://10.236.36.71:8765/mcp",
        api_key="lmcp_user-api-key",
    )

    assert str(config_path).endswith(".claude.json")
    assert commands[0][0] == ["C:/Tools/claude.exe", "mcp", "remove", "legal-mcp"]
    assert commands[0][1]["check"] is False
    assert commands[1][0] == [
        "C:/Tools/claude.exe",
        "mcp",
        "add",
        "--transport",
        "stdio",
        "--scope",
        "user",
        "legal-mcp",
        "--",
        "C:/Tools/legal-mcp.exe",
        "proxy",
        "--url",
        "http://10.236.36.71:8765/mcp",
        "--api-key",
        "lmcp_user-api-key",
    ]
    assert commands[1][1]["check"] is True


def test_doctor_reports_missing_database_and_healthy_database(tmp_path) -> None:
    database_path = tmp_path / "legal.db"

    missing = check_install_health(database_path=database_path)
    assert not missing.healthy
    assert any(check.code == "database_missing" for check in missing.checks)

    from legal_mcp import db

    db.initialize_database(database_path)
    healthy = check_install_health(database_path=database_path)
    assert healthy.healthy


def test_doctor_ai_probe_not_configured_is_benign(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("LEGAL_MCP_AI_BASE_URL", raising=False)
    monkeypatch.delenv("LEGAL_MCP_AI_API_KEY", raising=False)
    from legal_mcp import db

    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)

    report = check_install_health(database_path=database_path, probe_ai=True)

    assert report.healthy
    ai = next(check for check in report.checks if check.code == "ai_backend")
    assert ai.ok and "not configured" in ai.message


def test_doctor_ai_probe_reports_unreachable_endpoint(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    # Point at a closed port so the probe must fail to connect.
    monkeypatch.setenv("LEGAL_MCP_AI_BASE_URL", "http://127.0.0.1:1/v1")
    from legal_mcp import db

    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)

    report = check_install_health(database_path=database_path, probe_ai=True)

    ai = next(check for check in report.checks if check.code == "ai_backend")
    assert not ai.ok
    assert "unreachable" in ai.message and "127.0.0.1:1" in ai.message


def test_doctor_can_check_remote_http_health(monkeypatch, tmp_path) -> None:
    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            return json.dumps({"service": "legal-mcp", "database": "ready"}).encode("utf-8")

    def fake_urlopen(request, timeout):
        assert request.full_url == "http://legal.internal:8765/healthz"
        return FakeResponse()

    monkeypatch.setattr("legal_mcp.doctor.urllib.request.urlopen", fake_urlopen)

    report = check_install_health(
        database_path=tmp_path / "unused.db",
        config_path=None,
        remote_url="http://legal.internal:8765/mcp",
    )

    assert report.healthy is True
    assert any("remote HTTP server is healthy" in check.message for check in report.checks)


def test_doctor_validates_config_contains_legal_mcp_server(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    config_path = tmp_path / "mcp.json"
    from legal_mcp import db

    db.initialize_database(database_path)
    config_path.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")

    report = check_install_health(database_path=database_path, config_path=config_path)

    assert not report.healthy
    assert any(check.code == "config_legal_mcp" for check in report.checks)


def test_doctor_accepts_vscode_servers_config(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    config_path = tmp_path / "mcp.json"
    from legal_mcp import db

    db.initialize_database(database_path)
    write_vscode_config(config_path, database_path, tmp_path / "audit.jsonl")

    report = check_install_health(database_path=database_path, config_path=config_path)

    assert report.healthy


def test_doctor_reports_unreadable_database_without_crashing(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    database_path.write_text("not sqlite", encoding="utf-8")

    report = check_install_health(database_path=database_path)

    assert not report.healthy
    assert any(check.code == "database_readable" for check in report.checks)
