from pathlib import Path


def test_readme_documents_team_deployment() -> None:
    content = Path("README.md").read_text()

    assert "Team Deployment" in content
    assert "legal-mcp serve-http" in content
    assert "legal-mcp proxy" in content


def test_readme_keeps_deployment_notes_outside_git() -> None:
    content = Path("README.md").read_text()

    assert "Keep deployment notes" in content
    assert "outside Git" in content


def test_team_deployment_docs_describe_v13_minimum_disclosure() -> None:
    content = Path("Docs/team-deployment.md").read_text(encoding="utf-8")

    assert "1.3" in content
    assert "minimum disclosure" in content
    assert "get_project_context" in content
    assert "startup checks" in content


def test_docs_describe_v14_agent_observability_runbook() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")
    deployment = Path("Docs/team-deployment.md").read_text(encoding="utf-8")
    observability = Path("Docs/agent-observability.md").read_text(encoding="utf-8")
    combined = "\n".join([readme, deployment, observability])

    assert "agent_query" in combined
    assert "LEGAL_MCP_AGENT_MODEL" in combined
    assert "OPENAI_BASE_URL" in combined
    assert "LANGFUSE_PUBLIC_KEY" in combined
    assert "LANGFUSE_BASE_URL=http://127.0.0.1:3000" in combined
    assert "LEGAL_MCP_AGENT_PUBLIC_ONLY" in combined
    assert "Langfuse Cloud is not the production default" in combined
    assert "self-hosted Langfuse" in combined
    assert "Langflow is prototype-only" in combined
    assert "must not be connected to production Legal-MCP data" in combined
    assert "structured_query" in combined
    assert "LEGAL_MCP_AI_PROVIDER" in combined
    assert "External AI clients cannot directly access database tools" in combined
    assert "normal\ncatalog exposes `agent_query`, `agent_write`, `describe_my_access`, and\n`structured_query`" in combined
    assert "快路径" in combined
    assert "有界重试" in combined
    assert "agent_steps" in combined
    assert "不硬编码具体数据库条目" in combined
    assert "新增数据库字段" in combined
