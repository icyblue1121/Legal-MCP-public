from pathlib import Path
import stat


ROOT = Path(__file__).resolve().parents[1]


def test_install_script_bootstraps_uv_tool_install_and_setup() -> None:
    script = ROOT / "install.sh"

    content = script.read_text(encoding="utf-8")

    assert content.startswith("#!/usr/bin/env sh")
    assert script.stat().st_mode & stat.S_IXUSR
    assert "uv tool install --upgrade" in content
    assert "legal-mcp" in content
    assert 'exec "$COMMAND" setup "$@"' in content


def test_readme_documents_phase_6_commands() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "uv tool install --upgrade legal-mcp" in readme
    assert "./install.sh --client cursor" in readme
    assert "--client claude-code" in readme
    assert "legal-mcp setup" in readme
    assert "legal-mcp import" in readme
    assert "legal-mcp doctor" in readme
    assert "legal-mcp serve" in readme


def test_readme_documents_real_data_trial_entrypoint() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "Keep real client data" in readme
    assert "outside Git" in readme
    assert "empty data directories only" in readme
