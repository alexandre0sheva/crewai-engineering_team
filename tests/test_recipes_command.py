"""``recipes list|show`` and running a recipe you wrote, without touching Python."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from maintain_helpers import maintain, project, run_cli, scripted
from pipeline_fakes import FakeRunner
from test_baseline import interpreter_on_path  # noqa: F401  (the checks must find pytest)
from test_feature_mode import git, latest, state_of

from engineering_team.pipeline.recipes import PINNED_RECIPE, PROJECT_RECIPES
from engineering_team.pipeline.stages import StageRequest

pytestmark = pytest.mark.git

TIDY = """\
name: tidy
description: Remove dead code and keep the tests as they are.
policies: [tests_untouched]
stages:
  - name: profile
    kind: controller
    action: repo_profile
  - name: baseline
    kind: controller
    action: baseline
  - name: sweep
    kind: agent
    teammates: [backend_engineer]
    instructions: >
      Remove code nothing calls (check with the reference tool) and change nothing else.
    retry: 1
  - name: verify
    kind: verify
    teammates: [debugger]
  - name: summary
    kind: controller
    action: change_summary
"""


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    place = tmp_path / "home"
    monkeypatch.setenv("HOME", str(place))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(place / "xdg"))
    return place / "xdg" / "engineering-team" / "recipes"


def add_recipe(repo: Path, name: str, text: str) -> Path:
    path = repo / PROJECT_RECIPES / f"{name}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# -- listing --------------------------------------------------------------------------------


def test_list_shows_the_bundled_recipes_and_yours_with_where_each_comes_from(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, home: Path
) -> None:
    repo = project(tmp_path)
    add_recipe(repo, "tidy", TIDY)
    home.mkdir(parents=True)
    (home / "tidy.yaml").write_text(
        TIDY.replace("name: tidy", "name: tidy")
    )  # hidden by the project's

    shown, _ = run_cli(monkeypatch, "recipes", "list", "--repo", str(repo))
    as_json, _ = run_cli(monkeypatch, "recipes", "list", "--repo", str(repo), before=("--json",))

    assert shown.exit_code == 0, shown.output
    for name in (
        "add-tests",
        "refactor",
        "upgrade-deps",
        "docs",
        "security-audit",
        "custom",
        "review",
    ):
        assert name in shown.stdout
    assert "tidy" in shown.stdout and "project (hides user)" in shown.stdout
    found = {item["name"]: item for item in json.loads(as_json.stdout)}
    assert found["tidy"]["source"] == "project" and found["tidy"]["hides"] == ["user"]
    assert found["tidy"]["path"].endswith("tidy.yaml") and found["fix"]["source"] == "bundled"
    assert [s["name"] for s in found["tidy"]["stages"]][-1] == "summary"


def test_a_broken_recipe_is_listed_with_its_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = project(tmp_path)
    add_recipe(
        repo, "broken", "name: broken\nstages:\n  - {name: a, kind: agent, teammates: [nobody]}\n"
    )

    shown, _ = run_cli(monkeypatch, "recipes", "list", "--repo", str(repo))
    as_json, _ = run_cli(monkeypatch, "recipes", "list", "--repo", str(repo), before=("--json",))

    assert shown.exit_code == 0 and "INVALID" in shown.stdout
    broken = next(i for i in json.loads(as_json.stdout) if i["name"] == "broken")
    assert (
        "there is no prompt 'a'" in broken["error"]
        and "unknown teammate(s) nobody" in broken["error"]
    )


def test_show_prints_the_stages_policies_and_scopes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = project(tmp_path)

    shown, _ = run_cli(monkeypatch, "recipes", "show", "add-tests", "--repo", str(repo))
    unknown, _ = run_cli(monkeypatch, "recipes", "show", "nope", "--repo", str(repo))
    as_json, _ = run_cli(
        monkeypatch, "recipes", "show", "security-audit", "--repo", str(repo), before=("--json",)
    )

    assert shown.exit_code == 0, shown.output
    assert "Policies: tests_only" in shown.stdout and "scope tests" in shown.stdout
    for stage in ("coverage_before", "tests", "verify", "coverage_after", "summary"):
        assert stage in shown.stdout
    assert unknown.exit_code == 2 and "Unknown recipe 'nope'" in unknown.stderr
    audit = json.loads(as_json.stdout)
    skip = {s["name"]: s.get("skip_if") for s in audit["stages"]}
    assert skip["fix"] == ["fixes_not_requested", "no_findings"]


# -- running a recipe you wrote -------------------------------------------------------------


def test_a_recipe_file_in_the_project_runs_with_maintain_and_nothing_else(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = project(tmp_path)
    add_recipe(repo, "tidy", TIDY)

    def sweep(request: StageRequest) -> None:
        request.ctx.workspace.write_file("app/unused.py", "x = 1\n")

    result, fake = maintain(
        repo, monkeypatch, "tidy", "--goal", "the notes package",
        fake=FakeRunner(on_call=scripted(sweep=sweep)),
    )  # fmt: skip

    assert result.exit_code == 0, result.output
    ref = latest()
    assert ref.manifest.mode == "maintain" and ref.manifest.recipe == "tidy"
    assert [s.name for s in ref.manifest.stages] == [
        "profile",
        "baseline",
        "sweep",
        "verify",
        "summary",
    ]
    request = next(r for r in fake.requests if r.stage.name == "sweep")
    assert request.teammate == "backend_engineer"
    assert request.stage.instructions and "Remove code nothing calls" in request.stage.instructions
    from engineering_team.pipeline.stages import CrewStageRunner

    assert "Remove code nothing calls" in CrewStageRunner._inputs(request)["instructions"]
    assert (ref.run_dir / PINNED_RECIPE).is_file()  # the run keeps the recipe it started with
    assert (
        next(c for c in state_of(ref).checks if c.id == "policy:tests_untouched").status == "passed"
    )


def test_a_run_of_your_own_recipe_resumes_even_from_a_worktree_that_lacks_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = project(tmp_path)
    add_recipe(repo, "tidy", TIDY)
    (repo / "notes.txt").write_text("uncommitted\n")  # a dirty tree: the team gets a worktree
    first, _ = maintain(repo, monkeypatch, "tidy", fake=FakeRunner(fail={"sweep": 9}))
    assert first.exit_code == 1, first.output
    ref = latest()
    assert ref.workspace != repo.resolve()
    assert not (ref.workspace / PROJECT_RECIPES / "tidy.yaml").exists()
    add_recipe(repo, "tidy", TIDY.replace("Remove code", "Delete code"))  # an edit does not matter
    healthy = FakeRunner()

    from cli_helpers import use_runner

    use_runner(monkeypatch, healthy)
    from maintain_helpers import runner

    from engineering_team.cli.app import app

    result = runner.invoke(app, ["--workspace-root", str(Path.cwd() / "ws"), "resume", ref.run_id])

    assert result.exit_code == 0, result.output
    assert any(r.stage.name == "sweep" for r in healthy.requests)
    request = next(r for r in healthy.requests if r.stage.name == "sweep")
    assert request.stage.instructions and "Remove code" in request.stage.instructions


def test_a_recipe_of_your_own_may_replace_a_bundled_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = project(tmp_path)
    add_recipe(
        repo,
        "custom",
        "name: custom\nstages:\n"
        "  - {name: profile, kind: controller, action: repo_profile}\n"
        "  - {name: only, kind: agent, teammates: [backend_engineer], instructions: Do it.}\n"
        "  - {name: summary, kind: controller, action: change_summary}\n",
    )

    result, _ = maintain(repo, monkeypatch, "custom", "--goal", "anything")

    assert result.exit_code == 0, result.output
    assert [s.name for s in latest().manifest.stages] == ["profile", "only", "summary"]


def test_a_recipe_in_the_users_config_directory_can_be_run_from_any_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, home: Path
) -> None:
    repo = project(tmp_path)
    home.mkdir(parents=True)
    (home / "tidy.yaml").write_text(TIDY, encoding="utf-8")

    result, fake = maintain(repo, monkeypatch, "tidy")

    assert result.exit_code == 0, result.output
    assert latest().manifest.recipe == "tidy" and any(
        r.stage.name == "sweep" for r in fake.requests
    )


def test_an_invalid_recipe_stops_the_run_before_it_starts_and_says_what_to_fix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = project(tmp_path)
    path = add_recipe(repo, "tidy", TIDY.replace("kind: verify", "kind: wizard"))
    other = add_recipe(
        repo,
        "ghosts",
        TIDY.replace("backend_engineer", "ghost_writer").replace("name: tidy", "name: ghosts"),
    )

    bad_kind, _ = maintain(repo, monkeypatch, "tidy")
    bad_team, _ = maintain(repo, monkeypatch, "ghosts")

    assert bad_kind.exit_code == 2 and str(path) in bad_kind.stderr
    assert "stages[3] ('verify').kind" in bad_kind.stderr
    assert bad_team.exit_code == 2 and str(other) in bad_team.stderr
    assert "unknown teammate(s) ghost_writer" in bad_team.stderr
    assert git(repo, "branch", "--list", "engineering-team/*") == ""
