"""Files, artifacts, the diff and report, and the read-only information endpoints."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from git_helpers import commit, init_repo  # noqa: E402
from ui_helpers import API, make_client, start, wait_status  # noqa: E402

from engineering_team.modes.isolation import RECORD  # noqa: E402
from engineering_team.runtime.run_store import RunStore  # noqa: E402
from engineering_team.ui.routes_files import is_secret  # noqa: E402

PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452")


@pytest.fixture
def finished():  # noqa: ANN201
    client, _ = make_client()
    run_id = start(client)
    wait_status(client, run_id, "succeeded")
    project = Path.cwd() / "ws" / "demo"
    run_dir = RunStore(project).run_dir(run_id)
    return client, run_id, project, run_dir


# -- files -----------------------------------------------------------------------------------


def test_project_files_can_be_listed_and_read(finished) -> None:  # noqa: ANN001
    client, run_id, project, _ = finished
    (project / "src").mkdir()
    (project / "src" / "app.py").write_text("print('hi')\n")
    (project / "README.md").write_text("# Demo\n")

    root = client.get(f"{API}/runs/{run_id}/files").json()
    sub = client.get(f"{API}/runs/{run_id}/files?path=src").json()
    read = client.get(f"{API}/runs/{run_id}/files/src/app.py").json()

    assert [(e["name"], e["type"]) for e in root] == [("src", "directory"), ("README.md", "file")]
    assert sub == [{"name": "app.py", "path": "src/app.py", "type": "file", "size": 12}]
    assert read == {
        "path": "src/app.py",
        "size": 12,
        "content": "print('hi')\n",
        "truncated": False,
    }


def test_the_controller_directory_and_git_are_never_served(finished) -> None:  # noqa: ANN001
    client, run_id, project, _ = finished
    (project / ".git").mkdir()
    (project / ".git" / "config").write_text("[core]\n")

    names = {e["name"] for e in client.get(f"{API}/runs/{run_id}/files").json()}

    assert ".git" not in names and ".engineering-team" not in names
    for path in (".git/config", ".engineering-team/runs", ".engineering-team/owner.json",
                 ".Engineering-Team/lock"):  # fmt: skip
        assert client.get(f"{API}/runs/{run_id}/files/{path}").status_code in (403, 404), path
    assert client.get(f"{API}/runs/{run_id}/files?path=.git").status_code == 403
    assert client.get(f"{API}/runs/{run_id}/files?path=.engineering-team").status_code == 403


@pytest.mark.parametrize(
    "name",
    [".env", ".env.local", ".env.production", "id_rsa", "id_ed25519.pub", "server.pem",
     "private.key", ".netrc", ".npmrc", ".pypirc", "credentials", "credentials.json",
     "prod-secrets.yaml", "store.kdbx", ".git-credentials"],
)  # fmt: skip
def test_files_that_look_like_secrets_are_neither_listed_nor_returned(finished, name: str) -> None:  # noqa: ANN001
    client, run_id, project, _ = finished
    (project / name).write_text("SECRET=hunter2-do-not-leak\n")
    (project / "notes.txt").write_text("fine\n")

    listing = client.get(f"{API}/runs/{run_id}/files")
    read = client.get(f"{API}/runs/{run_id}/files/{name}")

    assert [e["name"] for e in listing.json()] == ["notes.txt"]
    assert read.status_code == 403 and "hunter2" not in read.text
    assert "hunter2" not in listing.text


def test_secret_directories_and_example_env_files() -> None:
    assert is_secret((".ssh", "config")) and is_secret((".aws", "credentials"))
    assert is_secret(("deploy", ".env")) and is_secret((".kube",))
    assert not is_secret((".env.example",)) and not is_secret((".env.sample",))
    assert not is_secret(("src", "keys.py")) and not is_secret(("docs", "secrets-policy.md"))


def test_paths_cannot_leave_the_project(finished) -> None:  # noqa: ANN001
    client, run_id, project, _ = finished
    (project.parent / "outside.txt").write_text("not yours\n")
    (project / "link").symlink_to(project.parent / "outside.txt")
    (project / "dirlink").symlink_to(project.parent)

    attempts = [
        "../outside.txt", "..%2foutside.txt", "%2e%2e/outside.txt", "/etc/passwd",
        "src/../../outside.txt", "link", "dirlink/outside.txt", "....//outside.txt",
    ]  # fmt: skip
    for path in attempts:
        response = client.get(f"{API}/runs/{run_id}/files/{path}")
        assert response.status_code in (403, 404, 422), (path, response.status_code)
        assert "not yours" not in response.text, path
    assert client.get(f"{API}/runs/{run_id}/files?path=..").status_code in (403, 404)
    assert client.get(f"{API}/runs/{run_id}/files?path=dirlink").status_code in (403, 404)
    assert "link" not in {e["name"] for e in client.get(f"{API}/runs/{run_id}/files").json()}


def test_binary_and_large_files(finished) -> None:  # noqa: ANN001
    client, run_id, project, _ = finished
    (project / "image.png").write_bytes(PNG)
    (project / "big.txt").write_text("x" * 400_000)

    binary = client.get(f"{API}/runs/{run_id}/files/image.png")
    big = client.get(f"{API}/runs/{run_id}/files/big.txt").json()

    assert binary.status_code == 415
    assert big["truncated"] is True and big["size"] == 400_000 and len(big["content"]) == 250_000
    assert client.get(f"{API}/runs/{run_id}/files/missing.txt").status_code == 404
    (project / "src").mkdir()
    assert client.get(f"{API}/runs/{run_id}/files/src").status_code == 422  # a directory


# -- artifacts -------------------------------------------------------------------------------------


def test_artifacts_list_what_a_person_looks_at(finished) -> None:  # noqa: ANN001
    client, run_id, _, run_dir = finished
    (run_dir / "screenshots").mkdir()
    (run_dir / "screenshots" / "qa-001-home.png").write_bytes(PNG)
    (run_dir / "commands").mkdir()
    (run_dir / "commands" / "1.log").write_text("pytest output\n")
    (run_dir / "usage.json").write_text("{}")
    (run_dir / "reports").mkdir()
    (run_dir / "reports" / "verification.md").write_text("# Verification\n")

    listed = {a["path"]: a for a in client.get(f"{API}/runs/{run_id}/artifacts").json()}

    assert listed["screenshots/qa-001-home.png"]["kind"] == "screenshot"
    assert listed["commands/1.log"]["kind"] == "log" and listed["usage.json"]["kind"] == "data"
    assert "reports/verification.md" in listed and "board.md" in listed
    assert not any(p in listed for p in ("manifest.json", "request.md", "settings.json", "inbox"))
    fetched = client.get(f"{API}/runs/{run_id}/artifacts/commands/1.log")
    assert fetched.text == "pytest output\n" and fetched.headers["content-type"].startswith(
        "text/plain"
    )
    image = client.get(f"{API}/runs/{run_id}/artifacts/screenshots/qa-001-home.png")
    assert image.headers["content-type"] == "image/png" and image.content == PNG
    assert fetched.headers["x-content-type-options"] == "nosniff"


def test_only_listed_artifacts_can_be_fetched(finished) -> None:  # noqa: ANN001
    client, run_id, project, run_dir = finished
    (project.parent / "outside.png").write_bytes(PNG)
    (run_dir / "screenshots").mkdir()
    (run_dir / "screenshots" / "link.png").symlink_to(project.parent / "outside.png")

    for path in ("manifest.json", "settings.json", "events.jsonl", "../manifest.json",
                 "screenshots/../manifest.json", "screenshots/link.png", "..%2f..%2fowner.json",
                 "commands", "screenshots", "/etc/passwd"):  # fmt: skip
        assert client.get(f"{API}/runs/{run_id}/artifacts/{path}").status_code == 404, path
    assert "screenshots/link.png" not in {
        a["path"] for a in client.get(f"{API}/runs/{run_id}/artifacts").json()
    }


# -- the report and the diff -----------------------------------------------------------------------


def test_the_report_is_served_inert(finished) -> None:  # noqa: ANN001
    client, run_id, *_ = finished

    page = client.get(f"{API}/runs/{run_id}/report")
    markdown = client.get(f"{API}/runs/{run_id}/report?format=md")

    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
    assert run_id in page.text and "sandbox" in page.headers["content-security-policy"]
    assert page.headers["x-content-type-options"] == "nosniff"
    assert (
        markdown.text.startswith(f"# Run {run_id}")
        and "markdown" in markdown.headers["content-type"]
    )
    assert client.get(f"{API}/runs/{run_id}/report?format=pdf").status_code == 422


def test_a_run_without_a_starting_commit_has_no_diff(finished) -> None:  # noqa: ANN001
    client, run_id, *_ = finished

    response = client.get(f"{API}/runs/{run_id}/diff")

    assert response.status_code == 404 and "feature, fix and maintain" in response.json()["error"]


def test_the_diff_is_the_work_against_the_commit_it_started_from(finished) -> None:  # noqa: ANN001
    client, run_id, project, _ = finished
    init_repo(project)
    commit(project, {"app.py": "one\n"}, "start", "Test", 1)
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=project, capture_output=True,
                          text=True, check=True).stdout.strip()  # fmt: skip
    record = project / RECORD
    record.parent.mkdir(parents=True, exist_ok=True)
    where = str(project)
    record.write_text(
        json.dumps({"mode": "branch", "source": where, "workspace": where, "base_commit": base})
    )
    (project / "app.py").write_text("one\ntwo\n")

    full = client.get(f"{API}/runs/{run_id}/diff").json()
    stat = client.get(f"{API}/runs/{run_id}/diff?stat=true").json()

    assert full["base"] == base and "+two" in full["diff"] and full["truncated"] is False
    assert "app.py" in stat["diff"] and stat["stat"] is True


# -- information ---------------------------------------------------------------------------------


def test_config_is_masked_and_says_where_values_came_from() -> None:
    client, _ = make_client()

    response = client.get(f"{API}/config")
    data = response.json()

    assert response.status_code == 200
    rows = {row["key"]: row for row in data["settings"]}
    assert rows["project_name"]["value"] == "demo" and rows["project_name"]["source"]
    assert rows["ui.max_concurrent_runs"]["value"] == "2"
    key = rows["credentials.OPENAI_API_KEY"]
    assert key["value"] == "set"  # whether it is set, never what it is
    assert "test-key-not-real" not in response.text


def test_config_never_returns_a_secret_set_in_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-live-very-secret-123456")
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-also-secret-654321")
    client, _ = make_client()

    for path in ("config", "doctor", "team", "recipes"):
        text = client.get(f"{API}/{path}").text
        assert "very-secret" not in text and "also-secret" not in text, path


def test_doctor_reports_checks_and_the_ui_extra() -> None:
    client, _ = make_client()

    data = client.get(f"{API}/doctor").json()

    assert {"ok", "checks", "ui_missing"} <= set(data) and data["ui_missing"] == []
    names = {check["name"] for check in data["checks"]}
    assert {"Python", "Web UI", "Team"} <= names
    assert all({"name", "status", "detail", "hint"} <= set(check) for check in data["checks"])


def test_team_lists_every_teammate() -> None:
    client, _ = make_client()

    team = client.get(f"{API}/team").json()

    keys = {member["key"] for member in team}
    assert {"product_analyst", "backend_engineer", "quality_engineer"} <= keys
    assert all({"role", "model", "tool_groups", "enabled", "stages"} <= set(m) for m in team)


def test_recipes_include_bundled_ones_and_the_projects_own(tmp_path: Path) -> None:
    client, _ = make_client()
    project = tmp_path / "proj"
    (project / ".engineering-team" / "recipes").mkdir(parents=True)
    (project / ".engineering-team" / "recipes" / "mine.yaml").write_text(
        "name: mine\ndescription: Mine.\nstages:\n  - name: look\n    instructions: Look around.\n"
        "    teammate: code_reviewer\n    readonly: true\n"
    )

    bundled = {r["name"] for r in client.get(f"{API}/recipes").json()}
    with_mine = {r["name"]: r for r in client.get(f"{API}/recipes?repo={project}").json()}

    assert {"new", "feature", "fix"} <= bundled and "mine" not in bundled
    assert with_mine["mine"]["source"] != "bundled" or with_mine["mine"]["error"]
    assert client.get(f"{API}/recipes?repo=relative").status_code == 422


def test_repo_inspect_validates_the_folder_and_describes_it(tmp_path: Path) -> None:
    client, _ = make_client()
    repo = tmp_path / "service"
    repo.mkdir()
    (repo / "pyproject.toml").write_text("[project]\nname = 'service'\nversion = '1'\n")
    (repo / "app.py").write_text("print(1)\n")
    init_repo(repo)
    commit(repo, {"app.py": "print(1)\n"}, "start", "Test", 1)

    data = client.get(f"{API}/repo/inspect", params={"path": str(repo)}).json()

    assert data["path"] == str(repo.resolve())
    assert data["profile"]["git"]["is_repo"] is True and data["profile"]["git"]["dirty"] is False
    assert data["profile"]["languages"][0]["language"] == "Python"
    assert any(stack["language"] == "python" for stack in data["profile"]["stacks"])
    (repo / "dirty.txt").write_text("x")
    assert (
        client.get(f"{API}/repo/inspect", params={"path": str(repo)}).json()["profile"]["git"][
            "dirty"
        ]
        is True
    )


@pytest.mark.parametrize("path", ["", "relative/dir", "/no/such/dir/anywhere", "/etc/hosts"])
def test_repo_inspect_rejects_what_is_not_a_directory(path: str) -> None:
    client, _ = make_client()

    response = client.get(f"{API}/repo/inspect", params={"path": path})

    assert response.status_code == 422 and "error" in response.json()
    assert os.path.exists("/etc/hosts")  # (the last case is a file, not a missing path)
