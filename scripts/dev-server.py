#!/usr/bin/env python3
"""Run hub-api locally against a throwaway demo vault.

    python3 scripts/dev-server.py            # http://127.0.0.1:18090, password: dev

Nothing is read from or written to a real vault: a temporary Git repository is
created from vault-template/, seeded with a few demo notes, and deleted on exit.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORT = int(os.getenv("DEV_PORT", "18090"))


def git(vault: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(vault), *args], check=True, stdout=subprocess.DEVNULL)


def seed(client, headers: dict[str, str]) -> None:
    def post(path: str, body: dict) -> dict:
        res = client.post(path, headers=headers, json=body)
        res.raise_for_status()
        return res.json()

    def note(**fields) -> str:
        return post("/api/v1/timeline", {"project": "demo-lab", "status": "final", **fields})["path"]

    post("/api/v1/projects", {"slug": "demo-lab", "name": "Demo Lab", "description": "Sensor filtering demo project", "actor": "agent:demo"})
    idea = note(title="Try a Kalman filter for drift", kind="idea", thread="filter-design", event_date="2026-01-05",
                content="Hypothesis: a Kalman filter removes the slow drift.", author="agent:demo", tags=["filter"])
    plan = note(title="Benchmark plan", kind="plan", thread="filter-design", event_date="2026-01-06", follows=[idea],
                content="Compare against the moving-average baseline on three recordings.", author="agent:demo")
    negative = note(title="Kalman filter diverges on long runs", kind="negative", thread="filter-design", event_date="2026-01-09",
                    follows=[plan], content="Diverges after ~40 s. **Abandoned.**", author="agent:demo", tags=["filter"])
    decision = note(title="Switch to a complementary filter", kind="decision", thread="filter-design", follows=[negative],
                    supersedes=[idea], content="Cheaper and stable. Reason: divergence above.", author="human")
    note(title="Weekly sync", kind="meeting", content="Agreed to freeze the sensor firmware.", author="human")
    note(title="Calibration result", kind="result", thread="calibration", content="SNR improved by 4 dB.", author="agent:demo", tags=["exp"])
    base = client.get("/api/v1/projects/demo-lab/context", headers=headers).json()["living_commit"]
    post("/api/v1/living-doc", {
        "project": "demo-lab", "section": "Key Findings", "base_commit": base, "actor": "agent:demo",
        "summary": f"Kalman filtering was abandoned, see [[{negative[:-3]}|divergence result]].", "source_notes": [negative, plan],
    })
    assert decision


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="research-hub-dev-") as tmp:
        tmp_path = Path(tmp)
        vault = tmp_path / "vault"
        shutil.copytree(ROOT / "vault-template", vault)
        git(vault, "init", "-b", "main")
        git(vault, "config", "user.name", "Dev")
        git(vault, "config", "user.email", "dev@example.invalid")
        git(vault, "add", ".")
        git(vault, "commit", "-m", "init")
        os.environ.update(
            VAULT_PATH=str(vault), BLOBS_PATH=str(tmp_path / "blobs"), STATE_PATH=str(tmp_path / "state"),
            AGENT_API_KEY="dev-key", WEB_PASSWORD="dev", SESSION_SECRET="dev-session-secret",
            FORGEJO_TOKEN="", FORGEJO_OWNER="",
        )
        sys.path.insert(0, str(ROOT / "services/hub-api/src"))
        import uvicorn
        from fastapi.testclient import TestClient

        import hub_api.main as hub

        seed(TestClient(hub.app), {"Authorization": "Bearer dev-key"})
        print(f"Demo vault: {vault}\nOpen http://127.0.0.1:{PORT} (password: dev, API key: dev-key)")
        uvicorn.run(hub.app, host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
