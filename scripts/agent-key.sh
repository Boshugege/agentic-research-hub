#!/usr/bin/env bash
# Manage per-agent API keys inside the running hub-api container.
#   scripts/agent-key.sh add <agent-name> [--projects slug-a,slug-b]
#   scripts/agent-key.sh list
#   scripts/agent-key.sh revoke <agent-name>
set -euo pipefail
exec podman exec "${HUB_API_CONTAINER:-hub-api}" python -m hub_api.keys "$@"
