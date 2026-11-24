# Agentic Research Hub

Git-first Markdown research workbench for human + AI agent collaboration, powered by **FastAPI + MCP + built-in authenticated Web Workbench** and **Forgejo**.

## Key Features

- **Project-scoped dual-track notes**:
  - **Timeline (`projects/<slug>/timeline/YYYY-MM/`)**: append-only timestamped notes, each with a kind (decision, result, negative result, plan, idea, meeting), an optional thread, and links to the notes it follows or supersedes, so a line of reasoning can be read end to end.
  - **Living Doc (`projects/<slug>/LIVING_DOC.md`)**: one canonical document per project evolved via repeated Git commits (and optional Forgejo PRs).
  - **Inbox (`_inbox/raw-logs/`)**: staging queue for unsorted LLM dumps (files >200KB automatically stored in `/blobs/` outside Git).
- **Authenticated Web Workbench** (`http://127.0.0.1:18081` -> `notes.<domain>`):
  - Password-gated login (`HttpOnly` signed session cookie + rate limiting).
  - Persistent project sidebar, vertical reverse-chronological timeline stream, Markdown Living Doc reader/editor, and inline Git Commit & Diff history viewer.
- **AI-First MCP & REST Gateway** (`http://127.0.0.1:18083` -> `mcp.<domain>`):
  - Standards-compliant JSON-RPC 2.0 Streamable HTTP MCP (`/mcp`, 15 tools including `get_project_context`, `append_timeline_note`, `list_threads`, `update_living_doc`, `list_undistilled_notes`, `get_digest`).
  - Built for several agents at once: per-agent keys with server-assigned identity and project-scoped writes, and stale-read protection on `LIVING_DOC.md`.
  - Equivalent REST endpoints under `/api/v1` secured by `Authorization: Bearer <key>`.
- **Git Forge & Backup**:
  - Self-hosted **Forgejo** (`127.0.0.1:18082` -> `git.<domain>`) for commit history, PR review, and off-site private GitHub mirroring.
  - Strict two-repo architecture: this repository holds public infrastructure/code/templates only; real research notes and secrets live in a separate private vault.

## Quick Start (Compose — Community Entry Point)

```sh
cp .env.example .env
# Edit .env and set strong AGENT_API_KEY and WEB_PASSWORD
mkdir -p research-vault research-blobs service-state
./scripts/init-vault.sh research-vault
podman compose --env-file .env up -d --build
./scripts/check-health.sh
```

- Web Workbench: `http://127.0.0.1:18081` (login with `WEB_PASSWORD`)
- Forgejo Git UI: `http://127.0.0.1:18082`
- MCP / REST API: `http://127.0.0.1:18083`

## Production Deployment (Rootless Podman Quadlet)

Production hosts run rootless Podman Quadlet (`systemd --user`) with loopback-only ports behind `cloudflared` + Cloudflare Access:

```sh
./scripts/init-vault.sh "$HOME/research-vault"
./scripts/install-quadlet.sh
podman build -t ghcr.io/example/agentic-research-hub/hub-api:latest services/hub-api
systemctl --user enable --now research-net-network.service forgejo.service hub-api.service
```

### Updating Production After Commits / PRs

Pull the latest stack changes, rebuild `hub-api`, refresh Quadlet units, and verify health in one command (never touches private `research.env` or `research-vault`):

```sh
./scripts/update-production.sh
```

## Local Preview

```sh
python3 scripts/dev-server.py   # http://127.0.0.1:18090 against a throwaway demo vault
python3 tests/smoke_test.py
```

See [docs/podman-deployment.md](docs/podman-deployment.md), [docs/agent-integration.md](docs/agent-integration.md), [docs/agent-playbook.md](docs/agent-playbook.md), [docs/cloudflare-zero-trust.md](docs/cloudflare-zero-trust.md), and [docs/adr.md](docs/adr.md).
