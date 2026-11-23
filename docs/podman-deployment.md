# Podman deployment

Production runs rootless user Quadlet (`systemd --user`). Install the units with `scripts/install-quadlet.sh`, initialize `%h/research-vault`, and set secrets in `%h/.config/agentic-research-hub/research.env` with mode `0600`.

```sh
./scripts/init-vault.sh "$HOME/research-vault"
./scripts/install-quadlet.sh
podman build -t ghcr.io/example/agentic-research-hub/hub-api:latest services/hub-api
systemctl --user enable --now research-net-network.service forgejo.service hub-api.service
systemctl --user enable --now git-autosync.timer research-vault-backup.timer research-vault-github-mirror.timer
```

## Updating After GitHub PRs or Commits

Once this repository is cloned on the host (e.g. `~/agentic-research-hub`), deploy updates from `main` with one command:

```sh
~/agentic-research-hub/scripts/update-production.sh
```

`update-production.sh` pulls `main`, rebuilds `hub-api`, refreshes Quadlet units and helper scripts, restarts `hub-api.service`, and runs health checks without modifying `~/.config/agentic-research-hub/research.env` or `~/research-vault`.

The only host listeners are loopback ports `18081` (Web Workbench), `18082` (Forgejo), and `18083` (MCP/REST API). Route them through host `cloudflared` + Cloudflare Access and do not bind `0.0.0.0`.
