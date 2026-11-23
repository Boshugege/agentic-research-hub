#!/usr/bin/env bash
set -euo pipefail
VAULT_PATH=${1:-./research-vault}
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
if [ -e "$VAULT_PATH/.git" ]; then
  echo "Vault already initialized at $VAULT_PATH"
  exit 0
fi
mkdir -p "$(dirname -- "$VAULT_PATH")"
cp -R "$REPO_ROOT/vault-template/." "$VAULT_PATH/"
( cd "$VAULT_PATH" && git init -b main && git add . && git -c user.name='Research Hub' -c user.email='research-hub@localhost' commit -m 'chore(vault): initialize research vault' )
echo "Initialized private vault at $VAULT_PATH"
