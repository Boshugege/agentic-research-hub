# Architecture Decision Record

## ADR-0001 Production ingress and runtime

- **Status**: accepted
- **Date**: 2026-11-23
- **Decision**: use rootless user Quadlet units under `%h/.config/containers/systemd/`. Keep the existing host-level `cloudflared.service` and expose this stack through loopback-only ports `18081` (SilverBullet), `18082` (Forgejo), and `18083` (hub-api).
- **Evidence**: the production host runs Debian 13 and Podman 5.4.2 in rootless mode (UID 1000). Existing Quadlet units use `After=network-online.target`, `Requires=<network>.service`, `Restart=always`, `RestartSec=10`, `TimeoutStartSec=180`, and `LogDriver=journald`. `cloudflared` is a host systemd service using a credential-loaded tunnel token. Existing observability uses Prometheus and Blackbox Exporter on a separate `observability` network.
- **Rationale**: the host cloudflared process cannot resolve containers on the new rootless network directly. Loopback reverse proxying preserves zero public ports and avoids changing the existing tunnel process. Blackbox probes can target the loopback services and systemd health state is visible to the existing Podman exporter.
- **Operational follow-up**: add the three loopback services to the existing cloudflared tunnel ingress and Blackbox configuration during M2. No tunnel token or Access credential is stored in this repository.

## ADR-0002 Human Web layer

- **Status**: accepted
- **Decision**: SilverBullet remains the Markdown workspace and Forgejo remains the Git history and review surface.
- **Rationale**: this preserves Git-first Markdown as the source of truth and matches the architecture document without introducing a database wiki.

## ADR-0003 LIVING_DOC commit mode

- **Status**: accepted
- **Decision**: API default is `mode=direct` for small timeline and literature updates. `mode=pr` remains available for substantial `LIVING_DOC.md` changes and creates an `agent/<project>/<timestamp>` branch. This choice favors low-friction capture while keeping review for high-impact synthesis.

## ADR-0004 Data separation

- **Status**: accepted
- **Decision**: this repository contains code, templates, and examples only. Real `footstep-viz`, `agent-deception`, `misc`, logs, papers, and blobs live in a separate private `research-vault` and blob directory.

## ADR-0006 Rootless volume permissions

- **Status**: accepted
- **Decision**: hub-api uses Quadlet `UserNS=keep-id` so it can write the user-owned vault. Forgejo keeps the image's default rootless mapping because its s6 supervisor requires a writable internal runtime directory. Its dedicated state path is owned by the image-mapped UID, while the shared vault remains readable and writable through the service's Git operations.
- **Operational note**: changing the Forgejo state mount ownership or adding `UserNS=keep-id` makes the container fail with an s6 lock permission error.

## ADR-0007 Built-in Authenticated Web Workbench Replaces Public SilverBullet

- **Status**: accepted (supersedes ADR-0002 SilverBullet portion)
- **Date**: 2026-11-23
- **Decision**: replace SilverBullet on loopback port `18081` with a purpose-built, password-authenticated single-page Research Hub Workbench served directly by `hub-api`. Keep Forgejo on `18082` for Git hosting, PR review, and mirroring.
- **Rationale**: SilverBullet lacked a persistent project sidebar, vertical timeline feed, inline `LIVING_DOC.md` Git Diff viewer, and built-in login protection by default. Serving the Workbench from `hub-api` gives human users a dedicated project/timeline/diff UI behind signed `HttpOnly` cookie auth while preserving 100% Git-first Markdown SSOT and MCP/REST compatibility. SilverBullet remains available in `compose.yml` under the optional `legacy-silverbullet` profile.

## ADR-0008 Note kinds, threads, and note-to-note links

- **Status**: accepted
- **Date**: 2026-11-24
- **Decision**: timeline notes carry `kind`, an optional `thread`, and `follows` / `supersedes` lists of note paths in their frontmatter. Threads are derived by scanning notes; there is no separate thread registry.
- **Rationale**: a reverse-chronological list records when things happened but not why one step led to the next. Free-form tags never converged on a vocabulary. A small fixed set of kinds makes decisions and negative results findable, and explicit links let a line of reasoning be read in order. Keeping everything in note frontmatter preserves Markdown-in-Git as the only source of truth.

## ADR-0009 Optimistic concurrency for LIVING_DOC.md

- **Status**: accepted
- **Date**: 2026-11-24
- **Decision**: `update_living_doc` takes `base_commit`. Whole-document replacement requires it and is refused if the document changed since. A stale section update is accepted only when that section is unchanged since `base_commit`.
- **Rationale**: the process lock serialises writes but cannot tell that a writer is working from an old read, so two agents on one project silently overwrote each other. Section-level checking lets agents working on different sections proceed without a retry.

## ADR-0010 Per-agent keys

- **Status**: accepted
- **Date**: 2026-11-24
- **Decision**: agents may be issued individual keys, stored as SHA-256 digests in the state directory. The server assigns the actor name for such keys and can limit writes to listed projects. The shared `AGENT_API_KEY` remains valid.
- **Rationale**: with one shared key the `Actor:` trailer was self-declared and no single agent could be revoked or scoped. Keys live in the state directory rather than the environment file so they can be added and revoked without restarting the service.

## ADR-0011 Distillation is derived, not stored

- **Status**: accepted
- **Date**: 2026-11-24
- **Decision**: a note counts as distilled when a commit touching the project's `LIVING_DOC.md` names it in `Source-Notes`, or the document wiki-links to it. Superseded notes are excluded from the backlog. The unused `distilled_into_commit` frontmatter field is left in place but not relied on.
- **Rationale**: deriving the state from Git history and document links needs no second write to the note and cannot drift from what the living doc actually cites.
