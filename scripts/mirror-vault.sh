#!/usr/bin/env bash
set -euo pipefail
VAULT_PATH=${VAULT_PATH:-$HOME/research-vault}
REMOTE=${MIRROR_REMOTE:-github}
ASKPASS=$(mktemp)
cleanup() { rm -f "$ASKPASS"; }
trap cleanup EXIT
cat > "$ASKPASS" <<'ASKPASS_SCRIPT'
#!/usr/bin/env bash
case "$1" in
  *Username*) printf '%s\n' 'x-oauth-basic' ;;
  *Password*) gh auth token ;;
  *) printf '\n' ;;
esac
ASKPASS_SCRIPT
chmod 700 "$ASKPASS"
proxy_args=()
if [ -n "${MIRROR_HTTP_PROXY:-}" ]; then
  proxy_args=(-c "http.proxy=$MIRROR_HTTP_PROXY" -c "https.proxy=$MIRROR_HTTP_PROXY")
else
  proxy_args=(-c http.proxy= -c https.proxy=)
fi
GIT_ASKPASS="$ASKPASS" GIT_TERMINAL_PROMPT=0 git -C "$VAULT_PATH" "${proxy_args[@]}" push --prune "$REMOTE" "refs/heads/*:refs/heads/*" "refs/tags/*:refs/tags/*"
