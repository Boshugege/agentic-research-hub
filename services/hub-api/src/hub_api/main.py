from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import mimetypes
import os
import re
import subprocess
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import httpx
import yaml
from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger("hub_api")

UTC8 = timezone(timedelta(hours=8))
STATIC_DIR = Path(__file__).parent / "static"
VAULT = Path(os.getenv("VAULT_PATH", "./research-vault"))
BLOBS = Path(os.getenv("BLOBS_PATH", "./research-blobs"))
STATE = Path(os.getenv("STATE_PATH", "./service-state"))
API_KEY = os.getenv("AGENT_API_KEY", "change-me")
WEB_PASSWORD = os.getenv("WEB_PASSWORD") or API_KEY
SESSION_SECRET = os.getenv("SESSION_SECRET") or hashlib.sha256(f"rh-session:{WEB_PASSWORD}:{API_KEY}".encode()).hexdigest()
SESSION_COOKIE = "rh_session"
SESSION_TTL_SECONDS = int(os.getenv("SESSION_TTL_SECONDS", str(30 * 24 * 3600)))
FORGEJO_URL = os.getenv("FORGEJO_URL", "http://forgejo:3000").rstrip("/")
FORGEJO_PUBLIC_URL = os.getenv("FORGEJO_PUBLIC_URL", "").rstrip("/")
FORGEJO_TOKEN = os.getenv("FORGEJO_TOKEN", "")
FORGEJO_OWNER = os.getenv("FORGEJO_OWNER", "")
FORGEJO_REPO = os.getenv("FORGEJO_REPO", "research-vault")
FORGEJO_GIT_REMOTE = os.getenv("FORGEJO_GIT_REMOTE", "")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
LOCK = threading.RLock()
LOGIN_FAILURES: dict[str, list[float]] = defaultdict(list)

app = FastAPI(title="Agentic Research Hub API", version="0.3.0")


def now() -> datetime:
    return datetime.now(UTC8)


def iso() -> str:
    return now().isoformat(timespec="seconds")


def _sign_session(exp: int) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"sub": "owner", "exp": exp}).encode()).decode().rstrip("=")
    sig = hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def _verify_session(token: Optional[str]) -> bool:
    if not token or "." not in token:
        return False
    payload, sig = token.rsplit(".", 1)
    expected = hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return False
    try:
        padded = payload + "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
        return int(data.get("exp", 0)) > int(time.time())
    except Exception:
        return False


def auth(
    authorization: Optional[str] = Header(default=None),
    x_api_key: Optional[str] = Header(default=None),
    rh_session: Optional[str] = Cookie(default=None),
) -> None:
    supplied = x_api_key or (authorization[7:] if authorization and authorization.startswith("Bearer ") else None)
    if supplied and hmac.compare_digest(supplied, API_KEY):
        return
    if _verify_session(rh_session):
        return
    raise HTTPException(status_code=401, detail="authentication required")


def run_git(*args: str, cwd: Path = VAULT, check: bool = True) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=False)
    if check and result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip()


def _sync_pending_human_edits() -> None:
    """Auto-commit dirty working-tree edits before git operations."""
    if not (VAULT / ".git").exists():
        return
    if run_git("status", "--porcelain", check=False):
        run_git("add", "-A")
        if run_git("diff", "--cached", "--name-only", check=False):
            run_git(
                "-c", "user.name=Research Hub",
                "-c", "user.email=research-hub@localhost",
                "commit", "-m", "human(auto): sync pending edits",
                check=False,
            )


def _forgejo_remote_url() -> str:
    return FORGEJO_GIT_REMOTE or f"{FORGEJO_URL}/{FORGEJO_OWNER}/{FORGEJO_REPO}.git"


def _push_ref_best_effort(refspec: str) -> bool:
    if not (FORGEJO_TOKEN and FORGEJO_OWNER):
        return False
    try:
        run_git(
            "-c", "http.proxy=", "-c", "https.proxy=",
            "-c", f"http.extraHeader=Authorization: token {FORGEJO_TOKEN}",
            "push", _forgejo_remote_url(), refspec,
        )
        return True
    except Exception as exc:
        logger.warning("best-effort push failed for %s: %s", refspec, exc)
        return False


def commit(message: str, actor: str, source_notes: Optional[list[str]] = None, push_main: bool = True) -> str:
    trailer = [f"Actor: {actor}"]
    if source_notes:
        trailer.append(f"Source-Notes: {', '.join(source_notes)}")
    run_git("add", "-A")
    if not run_git("diff", "--cached", "--name-only", check=False):
        return run_git("rev-parse", "HEAD")
    run_git(
        "-c", "user.name=Research Hub",
        "-c", "user.email=research-hub@localhost",
        "commit", "-m", message, "-m", "\n".join(trailer),
    )
    sha = run_git("rev-parse", "HEAD")
    if push_main and run_git("branch", "--show-current", check=False) == "main":
        _push_ref_best_effort("HEAD:refs/heads/main")
    return sha


def projects_file() -> Path:
    return VAULT / "_meta/projects.yaml"


def projects() -> dict[str, Any]:
    path = projects_file()
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text()) or {}
    return data.get("projects", {}) or {}


def save_projects(data: dict[str, Any]) -> None:
    path = projects_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"projects": data}, allow_unicode=True, sort_keys=False))


def validate_slug(slug: str) -> str:
    cleaned = slug.strip().lower()
    if not SLUG_RE.match(cleaned):
        raise HTTPException(
            status_code=400,
            detail="invalid project slug: use lowercase letters, digits, and hyphens (e.g. 'footstep-viz')",
        )
    return cleaned


def project_path(project: str) -> Path:
    if project not in projects():
        raise HTTPException(status_code=404, detail=f"unknown project: {project}. Register it first via create_project.")
    return VAULT / "projects" / project


def resolve_under(base: Path, rel_path: str) -> Path:
    root = base.resolve()
    target = (root / rel_path.lstrip("/")).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="path escapes root directory") from exc
    return target


def resolve_note(rel_path: str) -> Path:
    """Resolve a vault-relative note path, refusing Git internals."""
    target = resolve_under(VAULT, rel_path)
    if ".git" in target.relative_to(VAULT.resolve()).parts:
        raise HTTPException(status_code=400, detail="path points into Git internals")
    return target


def resolve_inbox_item(rel_path: str) -> Path:
    """Resolve a vault-relative path that must be a file inside _inbox/."""
    target = resolve_note(rel_path)
    try:
        target.relative_to((VAULT / "_inbox").resolve())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="item_path must be inside _inbox/") from exc
    return target


def note_event_datetime(meta: dict[str, Any], path: Path) -> datetime:
    """When the noted event happened: event_date, then created_at, then file mtime."""
    for key in ("event_date", "created_at"):
        value = meta.get(key)
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=UTC8)
        if value:
            try:
                return parse_event_datetime(str(value))
            except HTTPException:
                continue
    return datetime.fromtimestamp(path.stat().st_mtime, UTC8)


def safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+", "-", value).strip("-")
    return cleaned[:80] or "note"


def frontmatter(data: dict[str, Any]) -> str:
    return "---\n" + yaml.safe_dump(data, allow_unicode=True, sort_keys=False).strip() + "\n---\n"


def parse_frontmatter(text: str) -> dict[str, Any]:
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    try:
        parsed = yaml.safe_load(parts[1])
        return parsed if isinstance(parsed, dict) else {}
    except yaml.YAMLError:
        return {}


def strip_frontmatter(text: str) -> str:
    if not text.startswith("---"):
        return text
    parts = text.split("---", 2)
    return parts[2].lstrip("\n") if len(parts) >= 3 else text


def update_frontmatter_fields(text: str, updates: dict[str, Any]) -> str:
    if not text.startswith("---"):
        return text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return text
    meta = parse_frontmatter(text)
    meta.update(updates)
    body = parts[2].lstrip("\n")
    return frontmatter(meta) + "\n" + body


def render_project_index(slug: str, name: str, description: str = "") -> str:
    meta = {
        "id": f"{slug}-index",
        "project": slug,
        "type": "project_index",
        "title": name,
        "updated_at": iso(),
    }
    desc_line = f"\n> {description}\n" if description else ""
    return (
        frontmatter(meta)
        + f"\n# {name} (`{slug}`)\n"
        + desc_line
        + "\n"
        + f"- [[projects/{slug}/LIVING_DOC|LIVING_DOC]]\n"
    )


def render_living_doc(slug: str, name: str, description: str = "", author: str = "human") -> str:
    meta = {
        "id": f"{slug}-living-doc",
        "project": slug,
        "type": "living_doc",
        "title": name,
        "description": description,
        "updated_at": iso(),
        "author": author,
        "status": "draft",
        "tags": [],
    }
    summary_body = f"{description}\n" if description else "_待补充：当前一句话状态与核心结论。_\n"
    return (
        frontmatter(meta)
        + f"\n# {name}\n\n"
        + f"## 1. Executive Summary & Current Status\n\n{summary_body}\n"
        + "## 2. Problem Definition & Architecture / Methodology\n\n"
        + "## 3. Key Findings & Experimental Evidence\n\n"
        + "## 4. Literature & References\n\n"
        + "## 5. Open Questions, Risks & Next Actions\n\n"
        + "## 6. Changelog Highlights\n"
    )


class LoginRequest(BaseModel):
    password: str


class CreateProjectRequest(BaseModel):
    slug: str = Field(..., description="Lowercase kebab-case project ID, e.g. 'footstep-viz'")
    name: Optional[str] = Field(default=None, description="Human-readable project title")
    description: str = Field(default="", description="One-sentence summary of the project")
    status: str = Field(default="active", description="Project status: active | paused | archived")
    actor: str = Field(default="agent:hub-api", description="Agent or human actor identifier")


def parse_event_datetime(value: Optional[str]) -> datetime:
    if not value or not value.strip():
        return now()
    raw = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        return datetime.strptime(raw, "%Y-%m-%d").replace(hour=12, minute=0, second=0, tzinfo=UTC8)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}", raw):
        return datetime.strptime(raw.replace("T", " "), "%Y-%m-%d %H:%M").replace(tzinfo=UTC8)
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC8)
        return dt.astimezone(UTC8)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"invalid event_date format: {value} (use YYYY-MM-DD or ISO-8601)") from exc


class TimelineRequest(BaseModel):
    project: str
    title: str
    content: str
    event_date: Optional[str] = Field(default=None, description="Actual event date/time (YYYY-MM-DD or ISO-8601). Defaults to now.")
    created_at: Optional[str] = Field(default=None, description="Alias for event_date")
    tags: list[str] = Field(default_factory=list)
    author: str = "agent:hub-api"
    status: str = "draft"
    artifacts: list[str] = Field(default_factory=list)


class UpdateNoteRequest(BaseModel):
    path: str
    content: str
    commit_message: Optional[str] = None
    actor: str = "human"


class LivingDocRequest(BaseModel):
    project: str
    summary: str
    section: Optional[str] = None
    patch: Optional[str] = None
    source_notes: list[str] = Field(default_factory=list)
    mode: str = "direct"
    actor: str = "agent:hub-api"


class RawArtifactRequest(BaseModel):
    content: str
    filename: str
    project: str = "inbox"
    summary: str = ""


class SearchRequest(BaseModel):
    query: str
    project: Optional[str] = None
    type: Optional[str] = None
    limit: int = 20


class TriageRequest(BaseModel):
    item_path: str
    target_project: str
    distilled_content: str


# -------------------- Web UI & Auth --------------------

@app.get("/", response_class=HTMLResponse)
def web_ui() -> HTMLResponse:
    index_file = STATIC_DIR / "index.html"
    if not index_file.exists():
        return HTMLResponse("<h1>Research Hub UI missing</h1>", status_code=500)
    return HTMLResponse(index_file.read_text(encoding="utf-8"), headers={"Cache-Control": "no-cache"})


@app.get("/.ping")
def ping() -> dict[str, str]:
    return {"status": "ok"}


SW_KILL_JS = """
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.map((k) => caches.delete(k)));
    await self.registration.unregister();
    const clients = await self.clients.matchAll({ type: "window" });
    for (const client of clients) {
      client.navigate(client.url);
    }
  })());
});
""".strip()


@app.get("/service_worker.js")
@app.get("/.service_worker.js")
@app.get("/sw.js")
def kill_legacy_service_worker() -> Response:
    return Response(
        content=SW_KILL_JS,
        media_type="application/javascript",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )


@app.get("/.config")
def clear_legacy_silverbullet_cache() -> Response:
    return Response(
        content="{}",
        status_code=410,
        media_type="application/json",
        headers={
            "Clear-Site-Data": '"cache", "storage"',
            "Cache-Control": "no-store",
        },
    )


@app.post("/api/v1/auth/login")
def login(payload: LoginRequest, request: Request, response: Response) -> dict[str, Any]:
    client_ip = request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for") or (request.client.host if request.client else "unknown")
    now_ts = time.time()
    recent_fails = [t for t in LOGIN_FAILURES[client_ip] if now_ts - t < 300]
    LOGIN_FAILURES[client_ip] = recent_fails
    if len(recent_fails) >= 10:
        raise HTTPException(status_code=429, detail="too many failed login attempts, retry in 5 minutes")

    if not hmac.compare_digest(payload.password, WEB_PASSWORD):
        LOGIN_FAILURES[client_ip].append(now_ts)
        raise HTTPException(status_code=401, detail="invalid password")

    LOGIN_FAILURES.pop(client_ip, None)
    exp = int(now_ts) + SESSION_TTL_SECONDS
    token = _sign_session(exp)
    is_https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        httponly=True,
        samesite="lax",
        secure=is_https,
        max_age=SESSION_TTL_SECONDS,
        path="/",
    )
    return {"authenticated": True, "expires_at": exp}


@app.post("/api/v1/auth/logout")
def logout(response: Response) -> dict[str, bool]:
    response.delete_cookie(key=SESSION_COOKIE, path="/")
    return {"authenticated": False}


@app.get("/api/v1/auth/me", dependencies=[Depends(auth)])
def auth_me() -> dict[str, Any]:
    return {
        "authenticated": True,
        "forgejo_public_url": FORGEJO_PUBLIC_URL or None,
        "forgejo_repo": f"{FORGEJO_OWNER}/{FORGEJO_REPO}" if FORGEJO_OWNER else None,
    }


# -------------------- Health --------------------

@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
def readyz() -> JSONResponse:
    checks: dict[str, Any] = {"vault_git": False, "index_state": False, "forgejo": False}
    try:
        checks["vault_git"] = (VAULT / ".git").exists() and (run_git("status", "--porcelain", check=False) is not None)
    except Exception:
        pass
    checks["index_state"] = STATE.exists() or STATE.parent.exists()
    try:
        response = httpx.get(f"{FORGEJO_URL}/api/healthz", timeout=2)
        checks["forgejo"] = response.is_success
    except Exception:
        checks["forgejo"] = False
    status = "ready" if all(checks.values()) else "degraded"
    return JSONResponse({"status": status, "checks": checks}, status_code=200 if status == "ready" else 503)


@app.get("/healthz/stack", dependencies=[Depends(auth)])
def stack_health() -> dict[str, Any]:
    return {"status": "ok", "services": {"hub-api": "ok", "vault": (VAULT / ".git").exists(), "forgejo": FORGEJO_URL}}


# -------------------- Projects & Overview --------------------

def _collect_timeline_items(slug: str, include_content: bool = False) -> list[dict[str, Any]]:
    root = VAULT / "projects" / slug
    items: list[dict[str, Any]] = []
    if (root / "timeline").exists():
        for path in (root / "timeline").rglob("*.md"):
            text = path.read_text(errors="ignore")
            meta = parse_frontmatter(text)
            body = strip_frontmatter(text)
            created = str(meta.get("event_date") or meta.get("created_at") or "")
            sort_key = created or path.name
            entry: dict[str, Any] = {
                "sort_key": sort_key,
                "project": slug,
                "path": str(path.relative_to(VAULT)),
                "title": meta.get("title") or path.stem,
                "event_date": created or datetime.fromtimestamp(path.stat().st_mtime, UTC8).isoformat(timespec="seconds"),
                "created_at": created or datetime.fromtimestamp(path.stat().st_mtime, UTC8).isoformat(timespec="seconds"),
                "recorded_at": str(meta.get("recorded_at") or ""),
                "updated_at": meta.get("updated_at") or created,
                "author": meta.get("author") or "unknown",
                "status": meta.get("status") or "draft",
                "tags": meta.get("tags") or [],
                "artifacts": meta.get("artifacts") or [],
            }
            if include_content:
                entry["body"] = body
                entry["raw"] = text
            else:
                preview_lines = [ln.strip() for ln in body.splitlines() if ln.strip() and not ln.strip().startswith("#") and not ln.strip().startswith(">")]
                entry["excerpt"] = " ".join(preview_lines)[:220]
            items.append(entry)
    items.sort(key=lambda item: item["sort_key"], reverse=True)
    for item in items:
        item.pop("sort_key", None)
    return items


@app.get("/api/v1/overview", dependencies=[Depends(auth)])
def overview(recent_limit: int = 20) -> dict[str, Any]:
    proj_list = list_projects(include_stats=True)
    all_recent: list[dict[str, Any]] = []
    for p in proj_list["projects"]:
        all_recent.extend(_collect_timeline_items(p["slug"], include_content=False))
    all_recent.sort(key=lambda x: str(x.get("created_at") or x["path"]), reverse=True)

    inbox_entries = []
    inbox_root = VAULT / "_inbox"
    if inbox_root.exists():
        for path in sorted(inbox_root.rglob("*.md"), reverse=True):
            text = path.read_text(errors="ignore")
            meta = parse_frontmatter(text)
            inbox_entries.append({
                "path": str(path.relative_to(VAULT)),
                "title": meta.get("title") or path.stem,
                "created_at": meta.get("created_at") or datetime.fromtimestamp(path.stat().st_mtime, UTC8).isoformat(timespec="seconds"),
                "project": meta.get("project") or "inbox",
                "content": text,
            })

    return {
        "projects": proj_list["projects"],
        "recent_timeline": all_recent[:recent_limit],
        "inbox": inbox_entries,
        "inbox_count": len(inbox_entries),
        "total_timeline": len(all_recent),
        "forgejo_public_url": FORGEJO_PUBLIC_URL or None,
    }


@app.get("/api/v1/projects", dependencies=[Depends(auth)])
def list_projects(include_stats: bool = True) -> dict[str, Any]:
    data = []
    for slug, config in projects().items():
        item = {"slug": slug, **(config if isinstance(config, dict) else {})}
        if include_stats:
            root = VAULT / "projects" / slug
            timelines = _collect_timeline_items(slug, include_content=False)
            item["timeline_count"] = len(timelines)
            item["latest_timeline"] = timelines[0]["path"] if timelines else None
            item["latest_timeline_title"] = timelines[0]["title"] if timelines else None
            item["latest_timeline_at"] = timelines[0]["created_at"] if timelines else None
            item["literature_count"] = len(list((root / "literature").glob("*.md"))) if (root / "literature").exists() else 0
            living_path = root / "LIVING_DOC.md"
            if living_path.exists():
                lmeta = parse_frontmatter(living_path.read_text(errors="ignore"))
                item["living_updated_at"] = lmeta.get("updated_at")
                item["living_author"] = lmeta.get("author")
        data.append(item)
    inbox = list((VAULT / "_inbox").rglob("*.md")) if (VAULT / "_inbox").exists() else []
    return {"projects": data, "inbox_count": len(inbox)}


@app.post("/api/v1/projects", dependencies=[Depends(auth)])
def create_project(request: CreateProjectRequest) -> dict[str, Any]:
    slug = validate_slug(request.slug)
    display_name = (request.name or slug).strip()
    if request.status not in {"active", "paused", "archived"}:
        raise HTTPException(status_code=400, detail="status must be active, paused, or archived")
    with LOCK:
        _sync_pending_human_edits()
        if run_git("branch", "--show-current", check=False) != "main":
            run_git("switch", "main")
        registry = projects()
        created = slug not in registry
        existing = registry.get(slug) if isinstance(registry.get(slug), dict) else {}
        entry = {
            "name": display_name if (created or request.name) else existing.get("name", display_name),
            "status": request.status if (created or request.status) else existing.get("status", "active"),
        }
        desc = request.description.strip() or existing.get("description", "")
        if desc:
            entry["description"] = desc
        registry[slug] = entry
        save_projects(registry)

        root = VAULT / "projects" / slug
        (root / "timeline").mkdir(parents=True, exist_ok=True)
        (root / "literature").mkdir(parents=True, exist_ok=True)
        for keep in (root / "timeline/.gitkeep", root / "literature/.gitkeep"):
            if not keep.exists():
                keep.write_text("")
        (root / "project.yaml").write_text(yaml.safe_dump({"slug": slug, **entry}, allow_unicode=True, sort_keys=False))

        index_path = root / "INDEX.md"
        if not index_path.exists():
            index_path.write_text(render_project_index(slug, entry["name"], desc))

        living_path = root / "LIVING_DOC.md"
        if not living_path.exists():
            living_path.write_text(render_living_doc(slug, entry["name"], desc, author=request.actor))

        action = "initialize" if created else "update metadata for"
        sha = commit(f"feat({slug}): {action} project workspace", request.actor, [f"projects/{slug}/INDEX.md"])
    return {
        "created": created,
        "slug": slug,
        "name": entry["name"],
        "status": entry["status"],
        "paths": {
            "index": f"projects/{slug}/INDEX.md",
            "living_doc": f"projects/{slug}/LIVING_DOC.md",
            "timeline_dir": f"projects/{slug}/timeline",
        },
        "commit": sha,
    }


@app.get("/api/v1/projects/{project}/timeline", dependencies=[Depends(auth)])
def list_timeline(project: str, limit: int = 50, include_content: bool = False) -> dict[str, Any]:
    project_path(project)
    items = _collect_timeline_items(project, include_content=include_content)
    return {"project": project, "count": len(items), "items": items[:limit]}


@app.get("/api/v1/projects/{project}/context", dependencies=[Depends(auth)])
def get_project_context(project: str, recent_days: int = 14, include_lit: bool = True) -> dict[str, Any]:
    root = project_path(project)
    living = (root / "LIVING_DOC.md").read_text() if (root / "LIVING_DOC.md").exists() else ""
    cutoff = now() - timedelta(days=recent_days)
    timeline = []
    for path in sorted((root / "timeline").rglob("*.md"), reverse=True) if (root / "timeline").exists() else []:
        text = path.read_text(errors="ignore")
        meta = parse_frontmatter(text)
        if note_event_datetime(meta, path) >= cutoff:
            timeline.append({"path": str(path.relative_to(VAULT)), "frontmatter": meta, "content": text})
    literature = []
    if include_lit and (root / "literature").exists():
        literature = [str(path.relative_to(VAULT)) for path in sorted((root / "literature").glob("*.md"))]
    return {
        "project": project,
        "meta": projects().get(project, {}),
        "living_doc": living,
        "living_frontmatter": parse_frontmatter(living),
        "living_body": strip_frontmatter(living),
        "timeline": timeline,
        "literature": literature,
    }


@app.get("/api/v1/notes", dependencies=[Depends(auth)])
def read_note(path: str) -> dict[str, Any]:
    target = resolve_note(path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail=f"note not found: {path}")
    text = target.read_text(errors="ignore")
    return {
        "path": str(target.relative_to(VAULT.resolve())),
        "frontmatter": parse_frontmatter(text),
        "body": strip_frontmatter(text),
        "content": text,
    }


@app.put("/api/v1/notes", dependencies=[Depends(auth)])
def update_note(request: UpdateNoteRequest) -> dict[str, Any]:
    target = resolve_note(request.path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail=f"note not found: {request.path}")
    rel = str(target.relative_to(VAULT.resolve()))
    with LOCK:
        _sync_pending_human_edits()
        if run_git("branch", "--show-current", check=False) != "main":
            run_git("switch", "main")
        updated = update_frontmatter_fields(request.content, {"updated_at": iso()})
        target.write_text(updated)
        msg = request.commit_message or f"edit(note): update {rel}"
        sha = commit(msg, request.actor, [rel], push_main=True)
    return {"path": rel, "commit": sha}


@app.post("/api/v1/timeline", dependencies=[Depends(auth)])
def append_timeline_note(request: TimelineRequest) -> dict[str, str]:
    root = project_path(request.project)
    recorded_iso = iso()
    ts = parse_event_datetime(request.event_date or request.created_at)
    event_iso = ts.isoformat(timespec="seconds")
    stamp = ts.strftime("%Y-%m-%d_%H%M")
    slug_title = safe_name(request.title)
    note_id = ts.strftime("%Y%m%d-%H%M") + "-" + slug_title
    path = root / "timeline" / ts.strftime("%Y-%m") / f"{stamp}_{slug_title}.md"
    with LOCK:
        _sync_pending_human_edits()
        if run_git("branch", "--show-current", check=False) != "main":
            run_git("switch", "main")
        path.parent.mkdir(parents=True, exist_ok=True)
        counter = 1
        while path.exists():
            path = root / "timeline" / ts.strftime("%Y-%m") / f"{stamp}_{slug_title}-{counter}.md"
            counter += 1
        metadata = {
            "id": note_id,
            "project": request.project,
            "type": "timeline",
            "title": request.title,
            "event_date": event_iso,
            "created_at": event_iso,
            "recorded_at": recorded_iso,
            "updated_at": recorded_iso,
            "author": request.author,
            "status": request.status,
            "tags": request.tags,
            "artifacts": request.artifacts,
            "distilled_into_commit": None,
        }
        path.write_text(frontmatter(metadata) + f"\n# {request.title}\n\n{request.content.strip()}\n")
        rel = str(path.relative_to(VAULT))
        sha = commit(f"log({request.project}): append {request.title}", request.author, [rel])
    return {"path": rel, "commit": sha, "event_date": event_iso}


def _replace_section(original: str, section: str, new_body: str) -> str:
    exact_marker = f"## {section}"
    start = original.find(exact_marker)
    matched_header = exact_marker
    if start == -1:
        pattern = re.compile(rf"^## [^\n]*{re.escape(section)}[^\n]*$", re.MULTILINE | re.IGNORECASE)
        match = pattern.search(original)
        if not match:
            raise HTTPException(status_code=400, detail=f"section not found: {section}")
        start = match.start()
        matched_header = match.group(0)
    header_end = start + len(matched_header)
    next_match = re.search(r"\n## ", original[header_end:])
    end = header_end + next_match.start() if next_match else len(original)
    return original[:start] + matched_header + "\n\n" + new_body.strip() + "\n" + original[end:]


@app.post("/api/v1/living-doc", dependencies=[Depends(auth)])
def update_living_doc(request: LivingDocRequest) -> dict[str, Any]:
    root = project_path(request.project)
    path = root / "LIVING_DOC.md"
    with LOCK:
        if request.mode not in {"direct", "pr"}:
            raise HTTPException(status_code=400, detail="mode must be direct or pr")
        _sync_pending_human_edits()
        if run_git("branch", "--show-current", check=False) != "main":
            run_git("switch", "main")

        branch = None
        try:
            if request.mode == "pr":
                branch = f"agent/{request.project}/{now().strftime('%Y%m%d%H%M%S')}"
                run_git("switch", "-c", branch)

            original = path.read_text() if path.exists() else ""
            orig_meta = parse_frontmatter(original)
            if request.patch is not None:
                updated = request.patch
                if not updated.startswith("---"):
                    proj_cfg = projects().get(request.project, {})
                    preserved_meta = {
                        "id": orig_meta.get("id") or f"{request.project}-living-doc",
                        "project": request.project,
                        "type": "living_doc",
                        "title": orig_meta.get("title") or (proj_cfg.get("name") if isinstance(proj_cfg, dict) else request.project) or request.project,
                        "updated_at": iso(),
                        "author": request.actor,
                        "status": orig_meta.get("status") or "active",
                        "tags": orig_meta.get("tags") or [],
                    }
                    updated = frontmatter(preserved_meta) + "\n" + updated.lstrip("\n")
            elif request.section:
                updated = _replace_section(original, request.section, request.summary)
            else:
                updated = original.rstrip() + "\n\n" + request.summary.strip() + "\n"

            updated = update_frontmatter_fields(updated, {"updated_at": iso(), "author": request.actor})
            path.write_text(updated)

            commit_msg = f"docs({request.project}): {request.summary.splitlines()[0][:72]}" if request.patch is not None and request.summary.strip() else f"docs({request.project}): update LIVING_DOC"
            if request.section:
                commit_msg = f"docs({request.project}): update {request.section} in LIVING_DOC"

            if request.mode == "direct":
                sha = commit(commit_msg, request.actor, request.source_notes, push_main=True)
                return {"mode": "direct", "commit": sha, "path": str(path.relative_to(VAULT))}

            sha = commit(commit_msg, request.actor, request.source_notes, push_main=False)
            result: dict[str, Any] = {"mode": "pr", "branch": branch, "commit": sha, "path": str(path.relative_to(VAULT))}
            if FORGEJO_TOKEN and FORGEJO_OWNER:
                run_git(
                    "-c", "http.proxy=", "-c", "https.proxy=",
                    "-c", f"http.extraHeader=Authorization: token {FORGEJO_TOKEN}",
                    "push", _forgejo_remote_url(), f"HEAD:refs/heads/{branch}",
                )
                response = httpx.post(
                    f"{FORGEJO_URL}/api/v1/repos/{FORGEJO_OWNER}/{FORGEJO_REPO}/pulls",
                    headers={"Authorization": f"token {FORGEJO_TOKEN}"},
                    json={"title": commit_msg, "head": branch, "base": "main", "body": request.summary},
                    timeout=10,
                )
                response.raise_for_status()
                pr_data = response.json()
                result["pull_request"] = {
                    "number": pr_data.get("number"),
                    "html_url": pr_data.get("html_url"),
                    "state": pr_data.get("state"),
                }
            else:
                result["forgejo"] = "PR branch created locally. Configure FORGEJO_TOKEN and FORGEJO_OWNER to create the remote PR."
            return result
        finally:
            if run_git("branch", "--show-current", check=False) != "main":
                run_git("switch", "main", check=False)


@app.get("/api/v1/living-doc/{project}/history", dependencies=[Depends(auth)])
def living_doc_history(project: str, limit: int = 30) -> dict[str, Any]:
    project_path(project)
    rel = f"projects/{project}/LIVING_DOC.md"
    raw = run_git("log", f"-{limit}", "--format=%H%x1f%ad%x1f%s%x1f%b%x1e", "--date=iso", "--", rel, check=False)
    history = []
    for record in raw.split("\x1e"):
        record = record.strip()
        if not record:
            continue
        parts = record.split("\x1f")
        sha = parts[0] if len(parts) > 0 else ""
        date = parts[1] if len(parts) > 1 else ""
        subject = parts[2] if len(parts) > 2 else ""
        body = parts[3].strip() if len(parts) > 3 else ""
        actor_match = re.search(r"^Actor:\s*(.+)$", body, re.MULTILINE)
        sources_match = re.search(r"^Source-Notes:\s*(.+)$", body, re.MULTILINE)
        history.append({
            "commit": sha,
            "short_commit": sha[:7],
            "date": date,
            "subject": subject,
            "actor": actor_match.group(1).strip() if actor_match else "human",
            "source_notes": [s.strip() for s in sources_match.group(1).split(",")] if sources_match else [],
        })
    return {"project": project, "history": history}


@app.get("/api/v1/living-doc/{project}/diff", dependencies=[Depends(auth)])
def living_doc_diff(project: str, commit_sha: str) -> dict[str, Any]:
    project_path(project)
    if not SHA_RE.match(commit_sha):
        raise HTTPException(status_code=400, detail="invalid commit sha")
    rel = f"projects/{project}/LIVING_DOC.md"
    diff_text = run_git("show", "--no-color", "--format=", "--unified=3", commit_sha, "--", rel, check=False)
    snapshot = run_git("show", f"{commit_sha}:{rel}", check=False)
    meta_raw = run_git("show", "-s", "--format=%H%x1f%ad%x1f%s%x1f%b", "--date=iso", commit_sha, check=False)
    parts = meta_raw.split("\x1f")
    return {
        "commit": parts[0] if parts else commit_sha,
        "date": parts[1] if len(parts) > 1 else "",
        "subject": parts[2] if len(parts) > 2 else "",
        "body": parts[3].strip() if len(parts) > 3 else "",
        "diff": diff_text,
        "snapshot": snapshot,
    }


@app.post("/api/v1/artifacts", dependencies=[Depends(auth)])
def ingest_raw_artifact(request: RawArtifactRequest) -> dict[str, Any]:
    data = request.content.encode()
    digest = hashlib.sha256(data).hexdigest()
    if len(data) > 200 * 1024:
        target = BLOBS / safe_name(request.project) / now().strftime("%Y-%m") / f"{digest[:12]}_{safe_name(request.filename)}"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return {"storage": "blob", "path": f"/blobs/{target.relative_to(BLOBS)}", "sha256": digest}
    with LOCK:
        _sync_pending_human_edits()
        if run_git("branch", "--show-current", check=False) != "main":
            run_git("switch", "main")
        target = VAULT / "_inbox" / "raw-logs" / f"{now().strftime('%Y%m%d-%H%M%S')}_{safe_name(request.filename)}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            frontmatter({
                "id": digest[:16],
                "project": request.project or "inbox",
                "type": "raw_log",
                "title": request.filename,
                "created_at": iso(),
                "updated_at": iso(),
                "author": "agent:hub-api",
                "status": "draft",
                "tags": [],
            })
            + f"\n{request.summary}\n\n{request.content}\n"
        )
        rel = str(target.relative_to(VAULT))
        sha = commit("chore(inbox): ingest raw artifact", "agent:hub-api", [rel])
    return {"storage": "vault", "path": rel, "commit": sha}


@app.get("/blobs/{rel_path:path}", dependencies=[Depends(auth)])
def get_blob(rel_path: str) -> FileResponse:
    target = resolve_under(BLOBS, rel_path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="blob not found")
    media_type, _ = mimetypes.guess_type(str(target))
    return FileResponse(target, media_type=media_type or "application/octet-stream")


@app.post("/api/v1/search", dependencies=[Depends(auth)])
def search_knowledge(request: SearchRequest) -> dict[str, Any]:
    root = project_path(request.project) if request.project else VAULT
    results = []
    query = request.query.lower().strip()
    if not query:
        return {"results": []}
    for path in sorted(root.rglob("*.md")) if root.exists() else []:
        if ".git" in path.parts or "Library" in path.parts:
            continue
        text = path.read_text(errors="ignore")
        metadata = parse_frontmatter(text)
        if request.type and metadata.get("type") != request.type:
            continue
        for line_no, line in enumerate(text.splitlines(), 1):
            if query in line.lower():
                results.append({"path": str(path.relative_to(VAULT)), "line": line_no, "snippet": line.strip(), "frontmatter": metadata})
                if len(results) >= request.limit:
                    return {"results": results}
    return {"results": results}


@app.get("/api/v1/inbox", dependencies=[Depends(auth)])
def list_inbox_items(category: str = "all") -> list[str]:
    base = VAULT / "_inbox"
    dirs = {"logs": base / "raw-logs"}
    roots = dirs.values() if category == "all" else [dirs.get(category, base)]
    return [str(path.relative_to(VAULT)) for root in roots if root.exists() for path in sorted(root.glob("*.md"))]


@app.post("/api/v1/inbox/triage", dependencies=[Depends(auth)])
def triage_inbox_item(request: TriageRequest) -> dict[str, Any]:
    source = resolve_inbox_item(request.item_path)
    target_root = project_path(request.target_project)
    if not source.is_file():
        raise HTTPException(status_code=404, detail="inbox item not found")
    target = target_root / "timeline" / now().strftime("%Y-%m") / source.name
    with LOCK:
        _sync_pending_human_edits()
        if run_git("branch", "--show-current", check=False) != "main":
            run_git("switch", "main")
        target.parent.mkdir(parents=True, exist_ok=True)
        counter = 1
        while target.exists():
            target = target.with_name(f"{source.stem}-{counter}{source.suffix}")
            counter += 1
        content = request.distilled_content
        if not content.startswith("---"):
            meta = parse_frontmatter(source.read_text(errors="ignore"))
            meta.update({"project": request.target_project, "type": "timeline", "updated_at": iso()})
            content = frontmatter(meta) + "\n" + content
        target.write_text(content)
        source.unlink()
        rel = str(target.relative_to(VAULT))
        sha = commit(f"triage({request.target_project}): move {source.name}", "agent:hub-api", [request.item_path, rel])
    return {"path": rel, "commit": sha}


# -------------------- MCP Streamable HTTP --------------------

MCP_TOOLS: list[dict[str, Any]] = [
    {
        "name": "list_projects",
        "description": "List all registered research projects, timeline note counts, latest timeline note, and inbox count.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "include_stats": {"type": "boolean", "default": True, "description": "Include timeline and literature counts"},
            },
        },
    },
    {
        "name": "create_project",
        "description": "Register and scaffold a new research project workspace (idempotent). Creates projects/<slug>/INDEX.md, LIVING_DOC.md, timeline/, and updates _meta/projects.yaml.",
        "inputSchema": {
            "type": "object",
            "required": ["slug"],
            "properties": {
                "slug": {"type": "string", "description": "Lowercase kebab-case slug, e.g. 'footstep-viz' or 'agent-deception'"},
                "name": {"type": "string", "description": "Human-readable project title"},
                "description": {"type": "string", "description": "One-sentence project overview"},
                "status": {"type": "string", "enum": ["active", "paused", "archived"], "default": "active"},
                "actor": {"type": "string", "default": "agent:hub-api", "description": "Actor name, e.g. 'agent:footstep-viz'"},
            },
        },
    },
    {
        "name": "get_project_context",
        "description": "Fetch a project's current LIVING_DOC.md plus recent timeline notes and literature list in one call.",
        "inputSchema": {
            "type": "object",
            "required": ["project"],
            "properties": {
                "project": {"type": "string", "description": "Project slug"},
                "recent_days": {"type": "integer", "default": 14},
                "include_lit": {"type": "boolean", "default": True},
            },
        },
    },
    {
        "name": "list_timeline",
        "description": "List timeline notes of a project in reverse-chronological order (metadata only).",
        "inputSchema": {
            "type": "object",
            "required": ["project"],
            "properties": {
                "project": {"type": "string", "description": "Project slug"},
                "limit": {"type": "integer", "default": 30},
            },
        },
    },
    {
        "name": "append_timeline_note",
        "description": "Append a new timestamped timeline note under projects/<project>/timeline/YYYY-MM/ and auto-commit to Git. Pass event_date (YYYY-MM-DD or ISO-8601) when backfilling historical notes so timeline ordering matches actual event dates.",
        "inputSchema": {
            "type": "object",
            "required": ["project", "title", "content"],
            "properties": {
                "project": {"type": "string", "description": "Registered project slug"},
                "title": {"type": "string", "description": "Concise title of this timeline entry"},
                "content": {"type": "string", "description": "Markdown body of the note"},
                "event_date": {"type": "string", "description": "Actual event date/time (e.g. '2026-10-29' or '2026-11-21T15:00:00+08:00'). Defaults to current time."},
                "tags": {"type": "array", "items": {"type": "string"}, "default": []},
                "author": {"type": "string", "default": "agent:hub-api"},
                "status": {"type": "string", "default": "draft", "enum": ["draft", "verified", "final", "distilled", "archived"]},
                "artifacts": {"type": "array", "items": {"type": "string"}, "default": []},
            },
        },
    },
    {
        "name": "update_living_doc",
        "description": "Update a project's canonical LIVING_DOC.md (replace a section, replace full markdown via patch, or append summary) and commit via direct or Forgejo PR mode.",
        "inputSchema": {
            "type": "object",
            "required": ["project", "summary"],
            "properties": {
                "project": {"type": "string", "description": "Registered project slug"},
                "summary": {"type": "string", "description": "New section content (when section is set) or change summary / PR body"},
                "section": {"type": "string", "description": "Target H2 heading or substring, e.g. '1. Executive Summary'"},
                "patch": {"type": "string", "description": "Optional full Markdown content replacing LIVING_DOC.md"},
                "source_notes": {"type": "array", "items": {"type": "string"}, "default": [], "description": "Timeline note paths distilled into this update"},
                "mode": {"type": "string", "enum": ["direct", "pr"], "default": "direct"},
                "actor": {"type": "string", "default": "agent:hub-api"},
            },
        },
    },
    {
        "name": "get_living_doc_history",
        "description": "Get Git commit history of a project's LIVING_DOC.md.",
        "inputSchema": {
            "type": "object",
            "required": ["project"],
            "properties": {
                "project": {"type": "string"},
                "limit": {"type": "integer", "default": 20},
            },
        },
    },
    {
        "name": "read_note",
        "description": "Read any Markdown note inside the research vault by relative path.",
        "inputSchema": {
            "type": "object",
            "required": ["path"],
            "properties": {
                "path": {"type": "string", "description": "Relative path inside vault, e.g. 'projects/footstep-viz/LIVING_DOC.md'"},
            },
        },
    },
    {
        "name": "ingest_raw_artifact",
        "description": "Store raw logs or artifacts. Files <=200KB go into _inbox/raw-logs/; larger files are stored in /blobs/ outside Git.",
        "inputSchema": {
            "type": "object",
            "required": ["content", "filename"],
            "properties": {
                "content": {"type": "string"},
                "filename": {"type": "string"},
                "project": {"type": "string", "default": "inbox"},
                "summary": {"type": "string", "default": ""},
            },
        },
    },
    {
        "name": "search_knowledge",
        "description": "Search Markdown notes across the vault or within a specific project and note type.",
        "inputSchema": {
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "string"},
                "project": {"type": "string"},
                "type": {"type": "string", "enum": ["living_doc", "timeline", "literature", "raw_log"]},
                "limit": {"type": "integer", "default": 10},
            },
        },
    },
    {
        "name": "list_inbox_items",
        "description": "List unsorted raw log items currently waiting in _inbox/.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "default": "all"},
            },
        },
    },
    {
        "name": "triage_inbox_item",
        "description": "Move and distill an _inbox note into a project's timeline/ directory.",
        "inputSchema": {
            "type": "object",
            "required": ["item_path", "target_project", "distilled_content"],
            "properties": {
                "item_path": {"type": "string"},
                "target_project": {"type": "string"},
                "distilled_content": {"type": "string"},
            },
        },
    },
]


@app.get("/mcp", dependencies=[Depends(auth)])
def mcp_info() -> dict[str, Any]:
    return {
        "name": "agentic-research-hub",
        "version": "0.3.0",
        "transport": "streamable-http-jsonrpc",
        "tools_count": len(MCP_TOOLS),
    }


@app.post("/mcp", dependencies=[Depends(auth)])
def mcp(request: dict[str, Any]) -> Any:
    method = request.get("method")
    req_id = request.get("id")
    params = request.get("params") or {}

    if method == "initialize":
        client_version = params.get("protocolVersion") or "2024-11-05"
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": client_version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "agentic-research-hub", "version": "0.3.0"},
            },
        }

    if method == "notifications/initialized":
        if req_id is None:
            return Response(status_code=202)
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}

    if method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": MCP_TOOLS}}

    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        try:
            if name == "list_projects":
                result = list_projects(**arguments)
            elif name == "create_project":
                result = create_project(CreateProjectRequest.model_validate(arguments))
            elif name == "get_project_context":
                result = get_project_context(**arguments)
            elif name == "list_timeline":
                result = list_timeline(**arguments)
            elif name == "append_timeline_note":
                result = append_timeline_note(TimelineRequest.model_validate(arguments))
            elif name == "update_living_doc":
                result = update_living_doc(LivingDocRequest.model_validate(arguments))
            elif name == "get_living_doc_history":
                result = living_doc_history(**arguments)
            elif name == "read_note":
                result = read_note(**arguments)
            elif name == "ingest_raw_artifact":
                result = ingest_raw_artifact(RawArtifactRequest.model_validate(arguments))
            elif name == "search_knowledge":
                result = search_knowledge(SearchRequest.model_validate(arguments))
            elif name == "list_inbox_items":
                result = list_inbox_items(**arguments)
            elif name == "triage_inbox_item":
                result = triage_inbox_item(TriageRequest.model_validate(arguments))
            else:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32602, "message": f"unknown tool: {name}"},
                }
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
                    "isError": False,
                },
            }
        except (HTTPException, ValidationError, TypeError, ValueError, RuntimeError) as exc:
            detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps({"error": detail}, ensure_ascii=False)}],
                    "isError": True,
                },
            }

    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"method not found: {method}"}}


def run() -> None:
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
