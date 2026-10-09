#!/usr/bin/env bash
# Launches Sarvam's API-tools MCP server (sarvam-mcp) with the key from .env,
# so the key never lands in .mcp.json or git.
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -f .env ]; then set -a; source .env; set +a; fi
: "${SARVAM_API_KEY:?SARVAM_API_KEY missing: put it in .env (see .env.example)}"
export SARVAM_MCP_BASE_PATH="${SARVAM_MCP_BASE_PATH:-$PWD/.sarvam-mcp-out}"
mkdir -p "$SARVAM_MCP_BASE_PATH"
exec /opt/homebrew/bin/uvx --python 3.12 sarvam-mcp
