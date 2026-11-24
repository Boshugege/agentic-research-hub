#!/usr/bin/env python3
"""Set kind / thread / follows / supersedes on an existing timeline note.

Only the note's frontmatter is touched; the script re-reads the note afterwards
and fails loudly if the body changed. Standard library only.

    annotate-note.py projects/<slug>/timeline/2026-11/<file>.md --kind decision --thread bem-solver
    annotate-note.py <path> --follows <path-a>,<path-b> --supersedes <path-c>
    annotate-note.py <path> --kind result --dry-run

Credentials come from RESEARCH_HUB_URL and RESEARCH_HUB_KEY. If the key is not
set, the script looks for a Claude Code MCP server named "research-hub"
configured for the current directory in ~/.claude.json.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

KINDS = ("note", "idea", "plan", "result", "negative", "decision", "meeting")
THREAD_RE = re.compile(r"^[a-z0-9一-鿿]+(-[a-z0-9一-鿿]+)*$")
USER_AGENT = "Mozilla/5.0 (research-hub annotate-note)"


def credentials() -> tuple[str, str]:
    url, key = os.getenv("RESEARCH_HUB_URL", ""), os.getenv("RESEARCH_HUB_KEY", "")
    if not key:
        try:
            config = json.load(open(os.path.expanduser("~/.claude.json")))
            server = config["projects"][os.getcwd()]["mcpServers"]["research-hub"]
            key = server["headers"]["Authorization"].split(None, 1)[1]
            url = url or server["url"].rsplit("/mcp", 1)[0]
        except (OSError, KeyError, IndexError, ValueError):
            pass
    if not url or not key:
        sys.exit("error: set RESEARCH_HUB_URL and RESEARCH_HUB_KEY (or run from a project with a research-hub MCP server)")
    return url.rstrip("/"), key


def call(method: str, url: str, key: str, body: dict | None = None) -> dict:
    request = urllib.request.Request(
        url,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", "User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        sys.exit(f"error: {method} {url.split('?')[0]} -> HTTP {exc.code}: {exc.read().decode(errors='replace')[:300]}")


def set_fields(content: str, fields: dict[str, object]) -> str:
    """Replace or add top-level frontmatter keys, leaving every other line as it is."""
    lines = content.split("\n")
    if not lines or lines[0].strip() != "---":
        sys.exit("error: note has no frontmatter")
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        sys.exit("error: unterminated frontmatter")
    head = lines[1:end]
    for name, value in fields.items():
        rendered = f"{name}: {json.dumps(value, ensure_ascii=False)}"
        kept, skipping, replaced = [], False, False
        for line in head:
            if re.match(rf"^{re.escape(name)}\s*:", line):
                kept.append(rendered)
                skipping, replaced = True, True
                continue
            if skipping and (line.startswith((" ", "\t", "- ")) or line.strip() == ""):
                continue  # continuation of the old multi-line value
            skipping = False
            kept.append(line)
        if not replaced:
            kept.append(rendered)
        head = kept
    return "\n".join(["---", *head, *lines[end:]])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("path", help="vault-relative note path")
    parser.add_argument("--kind", choices=KINDS)
    parser.add_argument("--thread", help="kebab-case thread ID; pass '' to clear")
    parser.add_argument("--follows", help="comma-separated note paths; pass '' to clear")
    parser.add_argument("--supersedes", help="comma-separated note paths; pass '' to clear")
    parser.add_argument("--dry-run", action="store_true", help="show the new frontmatter without saving")
    args = parser.parse_args()

    if "/timeline/" not in args.path or not args.path.endswith(".md"):
        sys.exit("error: path must be a timeline note (projects/<slug>/timeline/.../*.md)")
    url, key = credentials()
    note_url = f"{url}/api/v1/notes?path="

    fields: dict[str, object] = {}
    if args.kind:
        fields["kind"] = args.kind
    if args.thread is not None:
        if args.thread and not THREAD_RE.match(args.thread):
            sys.exit("error: thread must be lowercase kebab-case, e.g. bem-solver")
        fields["thread"] = args.thread or None
    for name in ("follows", "supersedes"):
        raw = getattr(args, name)
        if raw is None:
            continue
        refs = [ref.strip() for ref in raw.split(",") if ref.strip()]
        for ref in refs:
            if ref == args.path:
                sys.exit(f"error: a note cannot list itself in {name}")
            call("GET", note_url + urllib.parse.quote(ref), key)  # exits if the referenced note is missing
        fields[name] = refs
    if not fields:
        sys.exit("error: nothing to change; pass --kind, --thread, --follows or --supersedes")

    before = call("GET", note_url + urllib.parse.quote(args.path), key)
    updated = set_fields(before["content"], fields)
    if args.dry_run:
        print(updated.split("\n---", 1)[0] + "\n---")
        return 0
    project = args.path.split("/")[1]
    summary = ", ".join(f"{k}={v}" for k, v in fields.items())
    call("PUT", f"{url}/api/v1/notes", key, {
        "path": args.path,
        "content": updated,
        "commit_message": f"chore({project}): annotate {os.path.basename(args.path)} ({summary})"[:200],
    })
    after = call("GET", note_url + urllib.parse.quote(args.path), key)
    if after["body"] != before["body"]:
        sys.exit("error: BODY CHANGED after update; inspect the note and its Git history immediately")
    saved = {name: after["frontmatter"].get(name) for name in fields}
    if saved != fields:
        sys.exit(f"error: saved frontmatter {saved} does not match requested {fields}")
    print(f"ok {args.path}: {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
