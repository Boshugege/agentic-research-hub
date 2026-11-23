#!/usr/bin/env bash
set -euo pipefail
BASE_URL=${HUB_API_URL:-http://127.0.0.1:18083}
WEB_URL=${HUB_WEB_URL:-http://127.0.0.1:18081}

printf '%s ' "$WEB_URL/.ping"
curl --fail --silent --show-error "$WEB_URL/.ping"
printf '\n'

for path in /healthz /readyz; do
  printf '%s ' "$BASE_URL$path"
  curl --fail --silent --show-error "$BASE_URL$path"
  printf '\n'
done

if [ -n "${AGENT_API_KEY:-}" ]; then
  printf '%s ' "$BASE_URL/healthz/stack"
  curl --fail --silent --show-error -H "Authorization: Bearer $AGENT_API_KEY" "$BASE_URL/healthz/stack"
  printf '\n'
fi
