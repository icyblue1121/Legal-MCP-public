#!/usr/bin/env sh
set -eu

ARCHIVE="${1:-legal-mcp-v1.4-images.tar}"
RUNTIME_COMPOSE_FILES="${COMPOSE_FILES:--f docker-compose.yml -f docker-compose.langfuse.yml}"

docker load -i "$ARCHIVE"
docker compose $RUNTIME_COMPOSE_FILES up -d
