#!/usr/bin/env sh
set -eu

PACKAGE="${LEGAL_MCP_PACKAGE:-legal-mcp}"
COMMAND="${LEGAL_MCP_COMMAND:-legal-mcp}"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required to install Legal-MCP." >&2
  echo "Install uv first: https://docs.astral.sh/uv/getting-started/installation/" >&2
  exit 1
fi

uv tool install --upgrade "$PACKAGE"

echo
echo "Legal-MCP is installed. Starting setup..."
if [ "$#" -eq 0 ] && [ -r /dev/tty ]; then
  exec < /dev/tty
fi
exec "$COMMAND" setup "$@"
