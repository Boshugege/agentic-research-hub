#!/usr/bin/env bash
set -euo pipefail
VAULT_PATH=${VAULT_PATH:-$HOME/research-vault}
BACKUP_PATH=${BACKUP_PATH:-/srv/backup/research-vault}
mkdir -p "$BACKUP_PATH"
git -C "$VAULT_PATH" bundle create "$BACKUP_PATH/vault-$(date +%Y%m%d-%H%M%S).bundle" --all
if [ -d "${BLOBS_PATH:-$HOME/research-blobs}" ]; then
  tar --listed-incremental="$BACKUP_PATH/blobs.snar" -czf "$BACKUP_PATH/blobs-$(date +%Y%m%d-%H%M%S).tar.gz" -C "${BLOBS_PATH:-$HOME/research-blobs}" .
fi
