"""Per-agent API keys.

Each agent gets its own key so that authorship is decided by the server
instead of being self-declared, a single agent can be revoked without
touching the others, and an agent can be limited to the projects it works on.

Only SHA-256 digests are stored. Manage keys with:

    python -m hub_api.keys add <agent-name> [--projects a,b]
    python -m hub_api.keys list
    python -m hub_api.keys revoke <agent-name>
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_CACHE: dict[str, Any] = {"path": None, "mtime": None, "by_hash": {}}


def keys_file() -> Path:
    explicit = os.getenv("AGENT_KEYS_FILE")
    if explicit:
        return Path(explicit)
    return Path(os.getenv("STATE_PATH", "./service-state")) / "agent-keys.yaml"


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _read(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text()) or {}
    agents = data.get("agents") if isinstance(data, dict) else None
    return agents if isinstance(agents, dict) else {}


def _write(path: Path, agents: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(yaml.safe_dump({"agents": agents}, allow_unicode=True, sort_keys=True))
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def lookup(key: str) -> Optional[dict[str, Any]]:
    """Return {'name', 'projects'} for a valid agent key, else None. Reloads when the file changes."""
    path = keys_file()
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        return None
    if _CACHE["path"] != str(path) or _CACHE["mtime"] != mtime:
        by_hash: dict[str, dict[str, Any]] = {}
        for name, cfg in _read(path).items():
            if not isinstance(cfg, dict) or not cfg.get("key_sha256"):
                continue
            projects = cfg.get("projects")
            by_hash[str(cfg["key_sha256"]).lower()] = {
                "name": str(name),
                "projects": [str(p) for p in projects] if isinstance(projects, list) else None,
            }
        _CACHE.update(path=str(path), mtime=mtime, by_hash=by_hash)
    return _CACHE["by_hash"].get(hash_key(key))


def add(name: str, projects: Optional[list[str]] = None) -> str:
    """Create (or rotate) an agent's key and return the plaintext key once."""
    if not NAME_RE.match(name):
        raise ValueError("agent name must be lowercase letters, digits, and hyphens")
    path = keys_file()
    agents = _read(path)
    key = "rh_" + secrets.token_urlsafe(32)
    entry: dict[str, Any] = {
        "key_sha256": hash_key(key),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if projects:
        entry["projects"] = projects
    agents[name] = entry
    _write(path, agents)
    return key


def revoke(name: str) -> bool:
    path = keys_file()
    agents = _read(path)
    if name not in agents:
        return False
    del agents[name]
    _write(path, agents)
    return True


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="hub_api.keys", description="Manage per-agent API keys")
    sub = parser.add_subparsers(dest="command", required=True)
    p_add = sub.add_parser("add", help="create or rotate an agent key")
    p_add.add_argument("name")
    p_add.add_argument("--projects", default="", help="comma-separated project slugs the agent may write to (default: all)")
    sub.add_parser("list", help="list agents")
    p_revoke = sub.add_parser("revoke", help="delete an agent key")
    p_revoke.add_argument("name")
    args = parser.parse_args(argv)

    if args.command == "add":
        projects = [p.strip() for p in args.projects.split(",") if p.strip()]
        rotated = args.name in _read(keys_file())
        try:
            key = add(args.name, projects or None)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        scope = ", ".join(projects) if projects else "all projects"
        print(f"{'Rotated' if rotated else 'Created'} key for agent:{args.name} (writes: {scope})", file=sys.stderr)
        print("Store it now; it is not recoverable:", file=sys.stderr)
        print(key)
        return 0
    if args.command == "list":
        agents = _read(keys_file())
        if not agents:
            print("no agent keys configured")
        for name, cfg in sorted(agents.items()):
            projects = cfg.get("projects") if isinstance(cfg, dict) else None
            created = cfg.get("created_at", "?") if isinstance(cfg, dict) else "?"
            print(f"agent:{name}\twrites: {', '.join(projects) if projects else 'all projects'}\tcreated: {created}")
        return 0
    if args.command == "revoke":
        if revoke(args.name):
            print(f"revoked agent:{args.name}")
            return 0
        print(f"error: no such agent: {args.name}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
