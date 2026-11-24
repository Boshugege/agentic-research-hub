# Agent integration

Use `https://mcp.<domain>/mcp` (JSON-RPC 2.0 MCP Streamable HTTP) or `https://mcp.<domain>/api/v1` (REST API) with `Authorization: Bearer <key>` (and Cloudflare Service Auth headers if Cloudflare Access is enabled on the MCP hostname).

Health endpoints: `/healthz`, `/readyz`, and authenticated `/healthz/stack`. `GET /api/v1/auth/me` reports who the key authenticates as.

For how to instruct and maintain the note-taking agents themselves, see [agent-playbook.md](agent-playbook.md).

## Keys and identity

- **Per-agent keys (recommended).** `scripts/agent-key.sh add <name> [--projects a,b]` prints a key once and stores only its SHA-256 digest in `<STATE_PATH>/agent-keys.yaml` (override with `AGENT_KEYS_FILE`). Writes made with that key are always recorded as `agent:<name>`, whatever `actor` / `author` the request declares, and are refused with 403 outside the listed projects. Reads are not restricted. `list` and `revoke <name>` manage existing keys; changes apply without a restart.
- **Shared key.** `AGENT_API_KEY` keeps working and trusts the declared `actor`. Treat it as an owner credential.

## Available MCP tools (`tools/list`)

- `get_project_context` — start here: `LIVING_DOC.md` with its `living_commit`, notes whose event date is within `recent_days`, thread summaries, undistilled backlog, literature list
- `list_projects` / `create_project` — list projects with stats; idempotently register and scaffold one
- `append_timeline_note` — append one note and auto-commit. Fields beyond title/content: `kind` (`note`, `idea`, `plan`, `result`, `negative`, `decision`, `meeting`), `thread`, `follows`, `supersedes`, `event_date`
- `list_timeline` — notes newest first; filter by `kind` / `thread`, use `order="asc"` to read a thread from its start
- `list_threads` — a project's lines of thought with counts and latest activity
- `list_undistilled_notes` — notes not yet reflected in `LIVING_DOC.md`
- `update_living_doc` — replace one section (preferred), replace the whole document (`patch`, requires `base_commit`), or append; `mode="direct"` or `mode="pr"`
- `get_living_doc_history` — Git history of `LIVING_DOC.md`
- `get_digest` — cross-project summary of the last N days
- `read_note` / `search_knowledge` — read a note; search by query, project, type, kind, thread
- `ingest_raw_artifact` — store raw logs in `_inbox/raw-logs/` (or `/blobs/` if > 200KB)
- `list_inbox_items` / `triage_inbox_item` — list and move `_inbox/` notes into a project timeline

## Concurrent edits to LIVING_DOC.md

`get_project_context` returns `living_commit`. Pass it back as `base_commit`:

- Whole-document `patch` must carry `base_commit` and gets `409` if the document changed since.
- A `section` update with a stale `base_commit` still succeeds when nobody else changed that section, and gets `409` when someone did.
- Every successful update returns `commit`, which is the next `living_commit`.

To correct the kind, thread, or links of an existing note without touching its body, use `scripts/annotate-note.py <path> --kind … --thread … [--follows a,b] [--supersedes c]`; it edits only those frontmatter keys and verifies the body is unchanged.

A note stops being "undistilled" once an update lists it in `source_notes` or the document links to it with `[[path|label]]`.

For `mode=pr`, set private `FORGEJO_TOKEN`, `FORGEJO_OWNER`, and `FORGEJO_GIT_REMOTE` environment variables. The API pushes the PR branch, opens the Forgejo pull request against `main`, and immediately switches the shared working tree back to `main`.
