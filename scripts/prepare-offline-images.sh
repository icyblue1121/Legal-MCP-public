#!/usr/bin/env sh
set -eu

ARCHIVE="${1:-legal-mcp-v1.4-images.tar}"
RUNTIME_COMPOSE_FILES="${COMPOSE_FILES:--f docker-compose.yml -f docker-compose.langfuse.yml}"
BUILD_COMPOSE_FILES="${BUILD_COMPOSE_FILES:--f docker-compose.yml -f docker-compose.langfuse.yml -f docker-compose.build.yml}"

docker compose $BUILD_COMPOSE_FILES build legal-mcp
docker compose $RUNTIME_COMPOSE_FILES pull

IMAGES="$(docker compose $BUILD_COMPOSE_FILES config --images && docker compose $RUNTIME_COMPOSE_FILES config --images)"
UNIQUE_IMAGES="$(
  printf '%s\n' "$IMAGES" | awk '
    NF && !seen[$0]++ {
      n = split($0, parts, "/")
      if (parts[n] !~ /:/ && $0 !~ /@/) {
        print $0 ":latest"
      } else {
        print $0
      }
    }
  '
)"

docker save -o "$ARCHIVE" $UNIQUE_IMAGES
printf 'Saved Docker images to %s\n' "$ARCHIVE"
