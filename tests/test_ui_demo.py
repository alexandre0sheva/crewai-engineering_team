"""``ui --demo``: scripted runs through the real pipeline, so the UI can be tried without a key."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from cli_helpers import workspace_root  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from git_helpers import require_git  # noqa: E402
from pipeline_fakes import settings_for  # noqa: E402
from ui_helpers import API, wait_status  # noqa: E402

from engineering_team.ui.app import create_app  # noqa: E402
from engineering_team.ui.demo import DEMO_DIR, ensure_sample_repo  # noqa: E402
from engineering_team.ui.launcher import RunLauncher  # noqa: E402


@pytest.fixture
def demo() -> tuple[TestClient, Path]:
    require_git()
    root = workspace_root()
    repo = ensure_sample_repo(Path.cwd() / "demo-home")
    launcher = RunLauncher(
        str(root),
        max_concurrent=2,
        command=[sys.executable, "-m", "engineering_team.ui.demo", "--demo-pause", "0.01"],
        environment={"ENGINEERING_STRATEGY": "pipeline"},
    )
    app = create_app(settings_for(root), launcher=launcher, demo_repo=str(repo))
    client = TestClient(app, base_url="http://localhost")
    client.headers.update({"X-Engineering-Team": "1"})
    return client, repo


def test_the_sample_project_is_a_clean_repository_made_once() -> None:
    require_git()

    repo = ensure_sample_repo(Path.cwd() / "home")
    again = ensure_sample_repo(Path.cwd() / "home")

    assert repo == again
    assert (repo / ".git").is_dir() and (repo / "app" / "main.py").is_file()


@pytest.mark.git
def test_a_demo_feature_run_produces_a_diff_criteria_and_a_report(demo) -> None:  # noqa: ANN001
    client, repo = demo
    assert client.get(f"{API}/options").json()["demo"] == {"repo": str(repo)}

    started = client.post(
        f"{API}/runs",
        json={"mode": "feature", "repo": str(repo), "request": "Add notes. AC-1 add. AC-2 list."},
    )
    run_id = started.json()["run_id"]
    wait_status(client, run_id, "succeeded", "failed", timeout=120)
    results = client.get(f"{API}/runs/{run_id}/results").json()

    assert results["status"] == "succeeded"
    assert {c["id"] for c in results["coverage"]} == {"AC-1", "AC-2", "AC-3"}
    assert {a["agent"] for a in results["agents"]} >= {"backend_engineer"}
    changed = [f["path"] for f in results["diff"]["files"]]
    assert changed and all(path.startswith(f"{DEMO_DIR}/") for path in changed)
    assert results["merge"]["branch"]
    # Everything the screens need is there to read: the board, the files, the report.
    board = client.get(f"{API}/runs/{run_id}/board").json()
    assert board["progress"]["cards_done"] == board["progress"]["cards_total"] > 0
    assert client.get(f"{API}/runs/{run_id}/files?path={DEMO_DIR}").json()
    assert "<html" in client.get(f"{API}/runs/{run_id}/report").text
    assert client.get(f"{API}/runs/{run_id}/diff").json()["diff"]


@pytest.mark.git
@pytest.mark.parametrize("strategy", [None, "hierarchical"])
def test_a_demo_new_project_run_succeeds_whatever_strategy_was_asked(
    demo,  # noqa: ANN001
    strategy: str | None,
) -> None:
    client, _ = demo
    options = {"strategy": strategy} if strategy else {}

    started = client.post(
        f"{API}/runs", json={"mode": "new", "request": "Build a notes CLI.", "options": options}
    )
    run_id = started.json()["run_id"]
    wait_status(client, run_id, "succeeded", "failed", timeout=120)
    results = client.get(f"{API}/runs/{run_id}/results").json()

    assert results["status"] == "succeeded", results["banner"]
    assert results["verdict"] == "verified"
    assert results["merge"] is None
