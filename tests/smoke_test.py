import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

root = Path(__file__).parents[1]
with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    vault = td / "vault"
    shutil.copytree(root / "vault-template", vault)
    subprocess.run(["git", "-C", str(vault), "init", "-b", "main"], check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "-C", str(vault), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(vault), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(vault), "add", "."], check=True)
    subprocess.run(["git", "-C", str(vault), "commit", "-m", "init"], check=True, stdout=subprocess.DEVNULL)

    os.environ.update(
        VAULT_PATH=str(vault),
        BLOBS_PATH=str(td / "blobs"),
        STATE_PATH=str(td / "state"),
        AGENT_API_KEY="test-key",
        WEB_PASSWORD="web-secret-password",
        SESSION_SECRET="test-session-secret",
        FORGEJO_TOKEN="",
        FORGEJO_OWNER="",
    )
    sys.path.insert(0, str(root / "services/hub-api/src"))
    import hub_api.main as hub
    importlib.reload(hub)
    client = TestClient(hub.app)
    headers = {"Authorization": "Bearer test-key"}

    # 0. Web UI & Authentication Gate
    assert client.get("/.ping").status_code == 200
    ui_res = client.get("/")
    assert ui_res.status_code == 200 and "登录科研工作台" in ui_res.text
    assert client.get("/api/v1/overview").status_code == 401
    assert client.post("/api/v1/auth/login", json={"password": "wrong"}).status_code == 401

    login_res = client.post("/api/v1/auth/login", json={"password": "web-secret-password"})
    assert login_res.status_code == 200 and login_res.json()["authenticated"] is True
    assert client.get("/api/v1/overview").status_code == 200
    assert client.post("/api/v1/auth/logout").status_code == 200
    assert client.get("/api/v1/overview").status_code == 401

    # 1. Create a brand new project (dynamic project naming by agent)
    r = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"slug": "new-sensor-lab", "name": "New Sensor Lab", "description": "Dynamic test project", "actor": "agent:sensor"},
    )
    assert r.status_code == 200 and r.json()["created"] is True, r.text
    assert (vault / "projects/new-sensor-lab/LIVING_DOC.md").exists()

    # 2. Append timeline note with historical event_date & edit note in place
    r = client.post(
        "/api/v1/timeline",
        headers=headers,
        json={
            "project": "new-sensor-lab",
            "title": "Initial Calibration",
            "content": "SNR improved by 4dB.",
            "event_date": "2026-10-29",
            "tags": ["exp"],
            "status": "final",
        },
    )
    assert r.status_code == 200, r.text
    note_path = r.json()["path"]
    assert "timeline/2026-10/2026-10-29_" in note_path, note_path

    r = client.get("/api/v1/projects/new-sensor-lab/timeline", headers=headers, params={"include_content": True})
    assert r.status_code == 200 and r.json()["count"] == 1 and r.json()["items"][0]["created_at"].startswith("2026-10-29"), r.text

    # 2b. Patch LIVING_DOC without frontmatter must preserve YAML frontmatter
    full_doc = "# New Sensor Lab\n\n## 1. Executive Summary\n\nBody\n\n## 3. Key Findings\n\nInitial\n"
    patch_req = {"project": "new-sensor-lab", "summary": "full rewrite", "patch": full_doc, "actor": "agent:sensor"}
    r = client.post("/api/v1/living-doc", headers=headers, json=patch_req)
    assert r.status_code == 400 and "base_commit is required" in r.text, r.text
    base0 = client.get("/api/v1/projects/new-sensor-lab/context", headers=headers).json()["living_commit"]
    r = client.post("/api/v1/living-doc", headers=headers, json={**patch_req, "base_commit": base0})
    assert r.status_code == 200, r.text
    base1 = r.json()["commit"]
    ctx_after = client.get("/api/v1/projects/new-sensor-lab/context", headers=headers).json()
    assert ctx_after["living_commit"] == base1, ctx_after
    assert ctx_after["living_frontmatter"].get("project") == "new-sensor-lab" and ctx_after["living_frontmatter"].get("author") == "agent:sensor", ctx_after

    # 2c. Optimistic concurrency: a stale whole-document patch is refused, and so is a
    #     stale edit of a section someone else changed; untouched sections still merge.
    r = client.post("/api/v1/living-doc", headers=headers, json={**patch_req, "base_commit": base0})
    assert r.status_code == 409 and base1[:12] in r.text, r.text
    r = client.post("/api/v1/living-doc", headers=headers, json={"project": "new-sensor-lab", "section": "Key Findings", "summary": "Agent B finding", "base_commit": base1, "actor": "agent:b"})
    assert r.status_code == 200, r.text
    r = client.post("/api/v1/living-doc", headers=headers, json={"project": "new-sensor-lab", "section": "Executive Summary", "summary": "Agent A summary", "base_commit": base1, "actor": "agent:a"})
    assert r.status_code == 200, r.text
    r = client.post("/api/v1/living-doc", headers=headers, json={"project": "new-sensor-lab", "section": "Key Findings", "summary": "Agent A stale overwrite", "base_commit": base1, "actor": "agent:a"})
    assert r.status_code == 409, r.text
    merged = (vault / "projects/new-sensor-lab/LIVING_DOC.md").read_text()
    assert "Agent B finding" in merged and "Agent A summary" in merged and "stale overwrite" not in merged, merged
    assert subprocess.check_output(["git", "-C", str(vault), "branch", "--list", "agent/*"], text=True).strip() == ""

    r = client.put("/api/v1/notes", headers=headers, json={"path": note_path, "content": (vault / note_path).read_text() + "\nVerified.\n"})
    assert r.status_code == 200, r.text

    # 3. Update LIVING_DOC & inspect Commit Diff API + PR branch reset
    r = client.post(
        "/api/v1/living-doc",
        headers=headers,
        json={
            "project": "new-sensor-lab",
            "section": "Executive Summary",
            "summary": "Calibration complete; pipeline ready.",
            "source_notes": [note_path],
            "mode": "direct",
            "actor": "agent:sensor",
        },
    )
    assert r.status_code == 200, r.text
    living_sha = r.json()["commit"]

    diff_res = client.get("/api/v1/living-doc/new-sensor-lab/diff", headers=headers, params={"commit_sha": living_sha})
    assert diff_res.status_code == 200 and "+Calibration complete; pipeline ready." in diff_res.json()["diff"], diff_res.text

    r = client.post(
        "/api/v1/living-doc",
        headers=headers,
        json={"project": "new-sensor-lab", "section": "Key Findings", "summary": "PR proposal body", "mode": "pr", "actor": "agent:sensor"},
    )
    assert r.status_code == 200 and r.json()["mode"] == "pr", r.text
    cur_branch = subprocess.check_output(["git", "-C", str(vault), "branch", "--show-current"], text=True).strip()
    assert cur_branch == "main", f"expected main, got {cur_branch}"

    # 3b. Project context filters timeline by event_date, not file mtime
    r = client.post("/api/v1/timeline", headers=headers, json={"project": "new-sensor-lab", "title": "Fresh Run", "content": "Today."})
    assert r.status_code == 200, r.text
    fresh_path = r.json()["path"]
    recent = [n["path"] for n in client.get("/api/v1/projects/new-sensor-lab/context", headers=headers, params={"recent_days": 14}).json()["timeline"]]
    assert recent == [fresh_path], recent
    everything = [n["path"] for n in client.get("/api/v1/projects/new-sensor-lab/context", headers=headers, params={"recent_days": 36500}).json()["timeline"]]
    assert set(everything) == {fresh_path, note_path}, everything

    # 3c. Path guards: Git internals are off limits, triage only moves _inbox items
    assert client.get("/api/v1/notes", headers=headers, params={"path": ".git/config"}).status_code == 400
    git_config_before = (vault / ".git/config").read_text()
    r = client.put("/api/v1/notes", headers=headers, json={"path": ".git/config", "content": "[core]\n"})
    assert r.status_code == 400 and (vault / ".git/config").read_text() == git_config_before, r.text
    assert client.get("/api/v1/notes", headers=headers, params={"path": "../outside.md"}).status_code == 400

    living_rel = "projects/new-sensor-lab/LIVING_DOC.md"
    r = client.post("/api/v1/inbox/triage", headers=headers, json={"item_path": living_rel, "target_project": "new-sensor-lab", "distilled_content": "x"})
    assert r.status_code == 400 and (vault / living_rel).exists(), r.text

    r = client.post("/api/v1/artifacts", headers=headers, json={"content": "raw log line", "filename": "run.log", "project": "new-sensor-lab"})
    assert r.status_code == 200 and r.json()["storage"] == "vault", r.text
    inbox_item = r.json()["path"]
    r = client.post("/api/v1/inbox/triage", headers=headers, json={"item_path": inbox_item, "target_project": "new-sensor-lab", "distilled_content": "Distilled run log."})
    assert r.status_code == 200 and not (vault / inbox_item).exists() and (vault / r.json()["path"]).exists(), r.text

    r = client.post("/api/v1/search", headers=headers, json={"query": "a", "project": "../.."})
    assert r.status_code == 404, r.text
    r = client.post("/api/v1/search", headers=headers, json={"query": "Calibration", "project": "new-sensor-lab"})
    assert r.status_code == 200 and r.json()["results"], r.text

    # 3d. Note kinds, threads, follows / supersedes
    def add_note(**fields):
        res = client.post("/api/v1/timeline", headers=headers, json={"project": "new-sensor-lab", "content": "body", **fields})
        assert res.status_code == 200, res.text
        return res.json()

    idea = add_note(title="Try Kalman filter", kind="idea", thread="Filter Design", event_date="2026-11-01")
    assert idea["thread"] == "filter-design" and idea["kind"] == "idea", idea
    result = add_note(title="Kalman drifts", kind="negative", thread="filter-design", event_date="2026-11-03", follows=[idea["path"]])
    decision = add_note(title="Switch to complementary filter", kind="decision", thread="filter-design", event_date="2026-11-05",
                        follows=[result["path"].removesuffix(".md")], supersedes=[idea["path"]])
    assert client.post("/api/v1/timeline", headers=headers, json={"project": "new-sensor-lab", "title": "x", "content": "y", "kind": "bogus"}).status_code == 400
    r = client.post("/api/v1/timeline", headers=headers, json={"project": "new-sensor-lab", "title": "x", "content": "y", "follows": ["projects/new-sensor-lab/timeline/nope.md"]})
    assert r.status_code == 400 and "note not found" in r.text, r.text

    r = client.get("/api/v1/projects/new-sensor-lab/timeline", headers=headers, params={"thread": "filter-design", "order": "asc"})
    thread_items = r.json()["items"]
    assert [i["kind"] for i in thread_items] == ["idea", "negative", "decision"], thread_items
    assert thread_items[0]["superseded_by"] == [decision["path"]] and thread_items[2]["follows"] == [result["path"]], thread_items
    r = client.get("/api/v1/projects/new-sensor-lab/timeline", headers=headers, params={"kind": "decision"})
    assert [i["path"] for i in r.json()["items"]] == [decision["path"]], r.text

    threads = client.get("/api/v1/projects/new-sensor-lab/threads", headers=headers).json()
    assert threads["threads"][0]["thread"] == "filter-design" and threads["threads"][0]["count"] == 3, threads
    assert threads["threads"][0]["latest_kind"] == "decision" and threads["threads"][0]["first_at"].startswith("2026-11-01"), threads
    assert client.get("/api/v1/projects/new-sensor-lab/context", headers=headers).json()["threads"][0]["thread"] == "filter-design"
    r = client.post("/api/v1/search", headers=headers, json={"query": "body", "project": "new-sensor-lab", "kind": "negative"})
    assert {x["path"] for x in r.json()["results"]} == {result["path"]}, r.text

    # 3e. Per-agent keys: server-assigned identity, project-scoped writes, revocation
    from hub_api import keys as agent_keys
    bot_key = agent_keys.add("sensor-bot", ["new-sensor-lab"])
    bot = {"Authorization": f"Bearer {bot_key}"}
    me = client.get("/api/v1/auth/me", headers=bot).json()["principal"]
    assert me == {"kind": "agent", "name": "sensor-bot", "write_projects": ["new-sensor-lab"]}, me
    assert client.get("/api/v1/auth/me", headers=headers).json()["principal"]["kind"] == "shared-key"

    r = client.post("/api/v1/timeline", headers=bot, json={"project": "new-sensor-lab", "title": "Signed note", "content": "x", "author": "agent:impostor"})
    assert r.status_code == 200, r.text
    signed = client.get("/api/v1/notes", headers=bot, params={"path": r.json()["path"]}).json()
    assert signed["frontmatter"]["author"] == "agent:sensor-bot", signed["frontmatter"]
    last_commit = subprocess.check_output(["git", "-C", str(vault), "log", "-1", "--format=%b"], text=True)
    assert "Actor: agent:sensor-bot" in last_commit, last_commit

    assert client.post("/api/v1/timeline", headers=bot, json={"project": "misc", "title": "t", "content": "c"}).status_code == 403
    assert client.post("/api/v1/living-doc", headers=bot, json={"project": "misc", "section": "Executive Summary", "summary": "s"}).status_code == 403
    assert client.post("/api/v1/projects", headers=bot, json={"slug": "other-lab"}).status_code == 403
    assert client.put("/api/v1/notes", headers=bot, json={"path": "_meta/projects.yaml", "content": "projects: {}"}).status_code == 403
    assert client.get("/api/v1/projects/misc/context", headers=bot).status_code == 200
    r = client.post("/mcp", headers=bot, json={"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {"name": "append_timeline_note", "arguments": {"project": "misc", "title": "t", "content": "c"}}})
    assert r.json()["result"]["isError"] is True and "may only write" in r.json()["result"]["content"][0]["text"], r.text

    assert agent_keys.main(["revoke", "sensor-bot"]) == 0
    assert client.get("/api/v1/auth/me", headers=bot).status_code == 401
    assert client.get("/api/v1/auth/me", headers={"Authorization": "Bearer rh_not-a-real-key"}).status_code == 401

    # 3f. An empty WEB_PASSWORD disables web login instead of accepting an empty password
    saved_password, hub.WEB_PASSWORD = hub.WEB_PASSWORD, ""
    assert client.post("/api/v1/auth/login", json={"password": ""}).status_code == 503
    hub.WEB_PASSWORD = saved_password

    # 4. Standards-compliant MCP handshake + tools/list + tools/call
    r = client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}})
    assert r.status_code == 200 and r.json()["result"]["serverInfo"]["name"] == "agentic-research-hub", r.text
    r = client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    tool_names = [t["name"] for t in r.json()["result"]["tools"]]
    assert len(tool_names) == 13 and "list_threads" in tool_names, tool_names
    r = client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "list_threads", "arguments": {"project": "new-sensor-lab"}}})
    assert r.json()["result"]["isError"] is False and "filter-design" in r.json()["result"]["content"][0]["text"], r.text

print("all smoke tests passed")
