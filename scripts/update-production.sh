#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
IMAGE=${HUB_API_IMAGE:-ghcr.io/example/agentic-research-hub/hub-api:latest}

cd "$REPO_ROOT"
if [ -d .git ] && git remote get-url origin >/dev/null 2>&1; then
  echo "==> Pulling latest stack commits..."
  git pull --ff-only
fi

echo "==> Building $IMAGE..."
podman build -t "$IMAGE" -f "$REPO_ROOT/services/hub-api/Containerfile" "$REPO_ROOT/services/hub-api"

echo "==> Syncing Quadlet units and helper scripts..."
"$REPO_ROOT/scripts/install-quadlet.sh"

echo "==> Restarting hub-api.service..."
systemctl --user restart hub-api.service
sleep 2

echo "==> Verifying health..."
"$REPO_ROOT/scripts/check-health.sh"
podman ps --filter "name=^(forgejo|hub-api)$" --format "{{.Names}} | {{.Status}} | {{.Ports}}"
