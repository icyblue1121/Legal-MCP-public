FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir ".[agent]"

RUN mkdir -p /data

EXPOSE 8765

CMD ["sh", "-c", "legal-mcp serve-http --host 0.0.0.0 --port 8765 --db /data/legal.db --audit-log /data/audit.jsonl --agent-public-only"]
