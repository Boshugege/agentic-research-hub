# Release checklist

The public repository can be released after the following checks.

- `compose.yml` parses with Podman Compose and exposes only loopback ports.
- Quadlet dry-run succeeds with `podman-system-generator -user -dryrun`.
- `vault-template/` contains no research notes, PDFs, logs, or personal paths.
- `rg` finds no real token, API key, Access secret, private domain, or private host path.
- `research-vault`, `research-blobs`, and `service-state` are ignored by Git.
- `git-autosync.timer` and `research-vault-backup.timer` are active on personal.
- Configure a private Forgejo repository and add its GitHub private mirror from the Forgejo administration UI or API. Store the mirror credential only in Forgejo's secret store.
- Add Cloudflare Tunnel routes and Access applications for the three loopback ports. Keep the tunnel token and Access service token outside this repository.
- `research-vault-github-mirror.timer` pushes only `heads` and `tags` to the GitHub private mirror.
