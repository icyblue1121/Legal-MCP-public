from pathlib import Path


def test_dockerfile_runs_http_server() -> None:
    content = Path("Dockerfile").read_text()

    assert "FROM python:3.11-slim" in content
    assert '".[agent]"' in content
    assert "serve-http" in content
    assert "--agent-public-only" in content


def test_compose_mounts_data_and_sets_http_command() -> None:
    content = Path("docker-compose.yml").read_text()

    assert "legal-mcp:" in content
    assert "image: *legal-mcp-image" in content
    assert "pull_policy: never" in content
    assert "build:" not in content
    assert "8765:8765" in content
    assert "./data:/data" in content
    assert "LEGAL_MCP_AGENT_PUBLIC_ONLY" in content
    assert "serve-http" in content
    assert "--agent-public-only" in content


def test_build_compose_builds_reusable_image() -> None:
    content = Path("docker-compose.build.yml").read_text()

    assert "build: ." in content
    from legal_mcp import __version__

    assert f"image: legal-mcp:v{__version__}" in content


def test_langfuse_compose_runs_self_hosted_stack() -> None:
    content = Path("docker-compose.langfuse.yml").read_text()

    assert "langfuse-web:" in content
    assert "ghcr.io/langfuse/langfuse:3" in content
    assert "langfuse-worker:" in content
    assert "ghcr.io/langfuse/langfuse-worker:3" in content
    assert "127.0.0.1:3000:3000" in content
    assert "LANGFUSE_BASE_URL" in content
    assert "http://langfuse-web:3000" in content
