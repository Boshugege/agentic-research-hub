#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
TARGET_DIR=${QUADLET_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/containers/systemd}
ENV_FILE=${RESEARCH_ENV_FILE:-$HOME/.config/agentic-research-hub/research.env}
mkdir -p "$TARGET_DIR" "$(dirname -- "$ENV_FILE")"
if [ ! -f "$ENV_FILE" ]; then
  cat > "$ENV_FILE" <<ENV
VAULT_HOST_PATH=$HOME/research-vault
BLOBS_HOST_PATH=$HOME/research-blobs
STATE_HOST_PATH=$HOME/research-state
AGENT_API_KEY=change-me
WEB_PASSWORD=change-me-web
SESSION_SECRET=
FORGEJO_PUBLIC_URL=
FORGEJO_TOKEN=
FORGEJO_OWNER=
FORGEJO_REPO=research-vault
FORGEJO_GIT_REMOTE=
ENV
  chmod 600 "$ENV_FILE"
  echo "Created $ENV_FILE. Set production secrets before starting services." >&2
fi
SYSTEMD_USER_DIR=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user
mkdir -p "$SYSTEMD_USER_DIR" "$HOME/.local/bin"
for file in "$REPO_ROOT"/quadlet/*; do
  case "$file" in
    *.container|*.network) install -m 0644 "$file" "$TARGET_DIR/$(basename "$file")" ;;
    *.service|*.timer) install -m 0644 "$file" "$SYSTEMD_USER_DIR/$(basename "$file")" ;;
  esac
done
install -m 0755 "$REPO_ROOT/scripts/git-autosync.sh" "$HOME/.local/bin/research-git-autosync"
install -m 0755 "$REPO_ROOT/scripts/research-vault-backup.sh" "$HOME/.local/bin/research-vault-backup"
install -m 0755 "$REPO_ROOT/scripts/mirror-vault.sh" "$HOME/.local/bin/research-vault-mirror"
systemctl --user daemon-reload
systemctl --user enable git-autosync.timer research-vault-backup.timer
echo "Installed Quadlet units in $TARGET_DIR"
echo "Start with: systemctl --user start research-net-network.service forgejo.service hub-api.service"
echo "Enable timers with: systemctl --user enable --now git-autosync.timer research-vault-github-mirror.timer"
