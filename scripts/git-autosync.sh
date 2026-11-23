#!/usr/bin/env bash
set -euo pipefail
VAULT_PATH=${VAULT_PATH:-$HOME/research-vault}
ENV_FILE=${RESEARCH_ENV_FILE:-$HOME/.config/agentic-research-hub/research.env}
if [ ! -d "$VAULT_PATH/.git" ]; then
  exit 0
fi
if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi
git -C "$VAULT_PATH" add -A
if ! git -C "$VAULT_PATH" diff --cached --quiet; then
  git -C "$VAULT_PATH" -c user.name="Research Hub" -c user.email="research-hub@localhost" commit -m "human(auto): sync local edits"
fi
if [ -n "${FORGEJO_TOKEN:-}" ] && git -C "$VAULT_PATH" remote get-url origin >/dev/null 2>&1; then
  cur_branch=$(git -C "$VAULT_PATH" branch --show-current || true)
  if [ "$cur_branch" = "main" ]; then
    git -C "$VAULT_PATH" -c http.proxy= -c https.proxy= \
      -c "http.extraHeader=Authorization: token ${FORGEJO_TOKEN}" \
      pull --rebase --autostash origin main >/dev/null 2>&1 || true
    git -C "$VAULT_PATH" -c http.proxy= -c https.proxy= \
      -c "http.extraHeader=Authorization: token ${FORGEJO_TOKEN}" \
      push origin main >/dev/null 2>&1 || true
  fi
fi
