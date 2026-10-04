"""The web UI's app shell: the static files are served safely, every file they refer to exists,
nothing needs a CDN or an inline script, and the endpoints the screens call have the shape the
JavaScript reads."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from git_helpers import commit, init_repo  # noqa: E402
from ui_helpers import API, make_client, start, wait_status  # noqa: E402

from engineering_team.modes.isolation import RECORD, Isolation  # noqa: E402
from engineering_team.ui import app as ui_app  # noqa: E402
from engineering_team.ui.launcher import RunOptions, overrides_for  # noqa: E402
from engineering_team.ui.routes_app import merge_guide  # noqa: E402
from engineering_team.ui.security import Security  # noqa: E402

STATIC = Path(ui_app.__file__).parent / "static"
STATIC_FILES = sorted(p for p in STATIC.rglob("*") if p.is_file())
TEXT_FILES = [p for p in STATIC_FILES if p.suffix in (".js", ".css", ".html", ".svg")]
IMPORT = re.compile(r"""(?:from\s+|import\s*\(\s*|import\s+)["'](\.[^"']+)["']""")


def test_the_page_is_served_with_a_strict_content_security_policy() -> None:
    client, _ = make_client()

    page = client.get("/")

    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    assert "<title>engineering-team</title>" in page.text
    policy = page.headers["content-security-policy"]
    assert "default-src 'none'" in policy
    assert "script-src 'self'" in policy
    assert "unsafe-inline" not in policy and "unsafe-eval" not in policy
    assert "frame-ancestors 'none'" in policy
    assert page.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("path", STATIC_FILES, ids=lambda p: p.relative_to(STATIC).as_posix())
def test_every_static_file_is_served_as_itself(path: Path) -> None:
    client, _ = make_client()
    relative = path.relative_to(STATIC).as_posix()

    response = client.get(f"/static/{relative}")

    assert response.status_code == 200
    assert response.content == path.read_bytes()
    assert response.headers["x-content-type-options"] == "nosniff"
    kind = response.headers["content-type"]
    expected = {".js": "javascript", ".css": "text/css", ".html": "text/html", ".svg": "image/svg"}
    assert expected[path.suffix] in kind


def test_the_files_the_page_and_modules_import_all_exist() -> None:
    index = (STATIC / "index.html").read_text(encoding="utf-8")
    wanted = {Path(STATIC / ref.removeprefix("/static/")) for ref in re.findall(
        r'(?:src|href)="(/static/[^"]+)"', index)}  # fmt: skip
    for path in STATIC.glob("js/**/*.js"):
        for ref in IMPORT.findall(path.read_text(encoding="utf-8")):
            wanted.add((path.parent / ref).resolve())

    missing = [str(p) for p in wanted if not p.is_file()]

    assert wanted and not missing, f"referenced but not shipped: {missing}"


def test_nothing_is_fetched_from_elsewhere_or_written_inline() -> None:
    offenders: list[str] = []
    for path in TEXT_FILES:
        text = path.read_text(encoding="utf-8")
        name = path.relative_to(STATIC).as_posix()
        for url in re.findall(r"https?://[^\s\"')]+", text):
            if "www.w3.org" not in url:  # the SVG namespace is an identifier, not a request
                offenders.append(f"{name}: {url}")
        inline = re.search(r"<script(?![^>]*\bsrc=)", text) or re.search(r"\sstyle=|<style", text)
        if path.suffix == ".html" and inline:
            offenders.append(f"{name}: inline script or style")
        styled = re.search(r"""\bstyle:\s*["'`]|setAttribute\(\s*["']style""", text)
        if path.suffix == ".js" and styled:
            offenders.append(f"{name}: a style attribute (blocked by the policy)")
        markup = re.search(r"\binnerHTML\b|\binsertAdjacentHTML\b|eval\(", text)
        if path.suffix == ".js" and markup:
            offenders.append(f"{name}: builds markup from strings")

    assert not offenders, offenders


@pytest.mark.parametrize(
    "path",
    ["/static/../ui/app.py", "/static/%2e%2e/app.py", "/static/js/../../app.py", "/static/nope.js"],
)
def test_static_paths_cannot_leave_the_static_folder(path: str) -> None:
    client, _ = make_client()

    response = client.get(path)

    assert response.status_code == 404
    assert "create_app" not in response.text


def test_the_shell_is_open_on_a_remote_server_but_the_api_still_needs_the_token() -> None:
    client, _ = make_client(security=Security(remote=True, token="s3cret"), headers={})
    client.headers.pop("authorization", None)

    assert client.get("/").status_code == 200
    assert client.get("/static/app.css").status_code == 200
    assert client.get(f"{API}/runs").status_code == 401
    assert client.get(f"{API}/options").status_code == 401
    assert client.post("/static/app.css").status_code in (401, 403, 405)


# -- what the screens read ---------------------------------------------------------------------


def test_options_describe_the_start_form() -> None:
    client, _ = make_client()

    options = client.get(f"{API}/options").json()

    assert [m["key"] for m in options["modes"]] == ["new", "feature", "fix", "maintain", "review"]
    assert {m["key"] for m in options["modes"] if m["needs_repo"]} == {
        "feature",
        "fix",
        "maintain",
        "review",  # fmt: skip
    }
    assert set(options["templates"]) == {"new", "feature", "fix", "maintain"}
    assert options["defaults"]["strategy"] == "pipeline"
    assert options["defaults"]["team_profile"] in options["team_profiles"]
    assert {"lead", "worker"} == {m["slot"] for m in options["models"]}
    assert options["limits"]["max_request_chars"] == 200_000
    assert set(options["tools"]) == {"web", "browser", "docker"}
    assert options["demo"] is None


def test_the_models_follow_the_chosen_provider_and_profile() -> None:
    client, _ = make_client()

    anthropic = client.get(f"{API}/options?provider=anthropic&profile=smoke").json()["models"]
    openai = client.get(f"{API}/options?provider=openai&profile=standard").json()["models"]

    assert anthropic != openai
    assert all(m["model"].startswith("anthropic/") for m in anthropic)
    assert all(m["input_per_million"] is not None for m in openai)  # the cost hint needs prices
    assert client.get(f"{API}/options?provider=nope").status_code == 422
    assert client.get(f"{API}/options?profile=nope").status_code == 422


def test_options_say_when_the_server_is_in_demo_mode() -> None:
    client, _ = make_client()
    client.app.state.ui.demo_repo = "/somewhere/sample"

    assert client.get(f"{API}/options").json()["demo"] == {"repo": "/somewhere/sample"}


def test_inspecting_a_folder_recommends_how_the_team_is_isolated() -> None:
    client, _ = make_client()
    clean = Path.cwd() / "clean"
    clean.mkdir()
    init_repo(clean)
    commit(clean, {"app.py": "print(1)\n"}, "first", "Dev", 1)
    dirty = Path.cwd() / "dirty"
    dirty.mkdir()
    init_repo(dirty)
    commit(dirty, {"app.py": "print(1)\n"}, "first", "Dev", 1)
    (dirty / "app.py").write_text("print(2)\n")
    plain = Path.cwd() / "plain"
    plain.mkdir()
    (plain / "a.txt").write_text("x")

    modes = {
        name: client.get(f"{API}/repo/inspect", params={"path": str(path)}).json()["isolation"]
        for name, path in (("clean", clean), ("dirty", dirty), ("plain", plain))
    }

    assert modes["clean"]["mode"] == "branch"
    assert modes["dirty"]["mode"] == "worktree" and "1 uncommitted" in modes["dirty"]["why"]
    assert modes["plain"]["mode"] == "copy"


def test_team_preset_and_roster_toggles_reach_the_run_as_settings() -> None:
    options = RunOptions(team_profile="minimal", disabled_teammates=["docs_writer"])

    assert overrides_for(options) == {"team_profile": "minimal", "team.docs_writer.enabled": False}
    assert overrides_for(RunOptions()) == {}


def test_a_start_with_the_team_preset_is_accepted() -> None:
    client, _ = make_client()

    run_id = start(client, options={"team_profile": "minimal"})

    assert wait_status(client, run_id, "succeeded") == "succeeded"


def test_the_results_are_the_report_as_json() -> None:
    client, _ = make_client()
    run_id = start(client)
    wait_status(client, run_id, "succeeded")

    results = client.get(f"{API}/runs/{run_id}/results").json()

    assert results["run_id"] == run_id
    assert results["status"] == "succeeded"
    assert set(results["banner"]) == {"label", "tone", "reasons"}
    for key in ("coverage", "checks", "findings", "screenshots", "warnings", "agents", "summaries"):
        assert key in results
    assert results["merge"] is None  # a new project has nothing to merge
    assert client.get(f"{API}/runs/20250101-000000-abcdef/results").status_code == 404


def test_the_results_of_a_project_run_say_how_to_take_the_change() -> None:
    client, _ = make_client()
    run_id = start(client)
    wait_status(client, run_id, "succeeded")
    project = Path.cwd() / "ws" / "demo"
    record = project / RECORD
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(
        '{"mode": "worktree", "source": "/src/my app", "workspace": "/work/tree", '
        '"branch": "engineering-team/x", "base_branch": "main", "base_commit": "abc123", '
        '"notes": []}'
    )

    merge = client.get(f"{API}/runs/{run_id}/results").json()["merge"]

    commands = [c for step in merge["steps"] for c in step["commands"]]
    assert merge["branch"] == "engineering-team/x"
    assert f"engineering-team diff {run_id}" in commands
    assert "cd '/src/my app'" in commands  # quoted: the path has a space
    assert "git merge engineering-team/x" in commands
    assert "git worktree remove /work/tree" in commands


def test_a_copy_has_no_branch_to_merge_only_a_patch() -> None:
    copy = Isolation(mode="copy", source=Path("/a"), workspace=Path("/b"))

    guide = merge_guide("20261004-101500-abc123", copy)

    titles = [step["title"] for step in guide["steps"]]
    assert titles == ["Look at the change", "Take the change as a patch"]
