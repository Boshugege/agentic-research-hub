# Agent integration

Use `https://mcp.<domain>/mcp` (JSON-RPC 2.0 MCP Streamable HTTP) or `https://mcp.<domain>/api/v1` (REST API) with `Authorization: Bearer <AGENT_API_KEY>` (and Cloudflare Service Auth headers if Cloudflare Access is enabled on the MCP hostname).

Health endpoints: `/healthz`, `/readyz`, and authenticated `/healthz/stack`.

## Available MCP tools (`tools/list`)

- `list_projects` — list registered projects and stats
- `create_project` — idempotently register & scaffold a new project (`projects/<slug>/INDEX.md`, `LIVING_DOC.md`, `timeline/`)
- `get_project_context` — read `LIVING_DOC.md` + recent timeline notes + literature list
- `list_timeline` — list timeline notes in reverse-chronological order
- `append_timeline_note` — append a timestamped timeline note and auto-commit
- `update_living_doc` — update `LIVING_DOC.md` (`mode="direct"` or `mode="pr"`)
- `get_living_doc_history` — inspect Git commit history of `LIVING_DOC.md`
- `read_note` — read any Markdown file in the vault
- `ingest_raw_artifact` — store raw logs in `_inbox/raw-logs/` (or `/blobs/` if > 200KB)
- `search_knowledge` — search across notes by query, project, and type
- `list_inbox_items` / `triage_inbox_item` — list and triage `_inbox/` notes into projects

For `mode=pr`, set private `FORGEJO_TOKEN`, `FORGEJO_OWNER`, and `FORGEJO_GIT_REMOTE` environment variables. The API pushes the PR branch, opens the Forgejo pull request against `main`, and immediately switches the shared working tree back to `main`.
