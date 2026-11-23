# Cloudflare Zero Trust integration

The host `cloudflared.service` remains the tunnel process. Add routes for `notes.<domain>`, `git.<domain>`, and `mcp.<domain>` to `http://127.0.0.1:18081`, `http://127.0.0.1:18082`, and `http://127.0.0.1:18083` respectively. Keep Cloudflare Access OAuth for the human surfaces and Service Auth for MCP. `hub-api` also requires its own Bearer token.

The repository ships only `cloudflared/config.example.yml`. Tunnel tokens, Access client secrets, and personal domains stay in the host configuration.

If you prefer to expose the services through a named tunnel ingress, keep the host cloudflared systemd unit and route to the loopback ports. The tunnel token itself is already managed by the host credential loader. The remaining values needed for a first production route are the chosen hostnames and Cloudflare Access policy assignments.
