"""Prepare one run: its directory, workspace, and the command line that starts the team.

The team always runs as ``python -m engineering_team ...`` in a process of its own; with ``fake``
it is :mod:`engineering_team.bench.fake_team` instead, which replays the task's reference solution.
Nothing here starts a process except the ``git`` commands that make a repository fixture.
"""

from __future__ import annotations

import os
import secrets
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from engineering_team.bench.acceptance import clean_environment, source_root
from engineering_team.bench.plan import RunOptions, RunSpec
from engineering_team.bench.tasks import BenchTask, copy_tree

PROJECT = "app"  # the directory name of a new project and the project name of a repository run
GIT_IDENTITY = {
    "GIT_AUTHOR_NAME": "bench",
    "GIT_AUTHOR_EMAIL": "bench@example.invalid",
    "GIT_COMMITTER_NAME": "bench",
    "GIT_COMMITTER_EMAIL": "bench@example.invalid",
}


@dataclass
class Prepared:
    """Where a run works and the command that starts the team."""

    run_dir: Path
    workspace: Path
    argv: list[str]
    env: dict[str, str]
    run_id: str


def new_run_id() -> str:
    return f"{datetime.now(UTC):%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        env={**os.environ, **GIT_IDENTITY},
    )


def _make_repository(task: BenchTask, repo: Path) -> None:
    """The fixture as a clean Git repository on ``main`` with one commit."""

    copy_tree(task.fixture_dir, repo)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "fixture")


def neutral_settings_environment(run_dir: Path) -> dict[str, str]:
    """Variables that make the team's settings come from the command line and ``--config`` only.

    Every ``ENGINEERING_*`` setting is set blank (a blank value counts as unset, and CrewAI's
    ``.env`` loading never overrides a variable that already exists, so neither the shell nor the
    repository's ``.env`` can change the benchmark's conditions), and the user's config file
    (``$XDG_CONFIG_HOME/engineering-team/``) is pointed at an empty directory. API keys are left
    alone: the team finds them as it always does.
    """

    from engineering_team.settings import CONFIG_FILE_ENV, ENV_SETTINGS, OVERRIDES_ENV

    names = {*ENV_SETTINGS, CONFIG_FILE_ENV, OVERRIDES_ENV}
    names |= {name for name in os.environ if name.startswith("ENGINEERING_")}
    return {**dict.fromkeys(sorted(names), ""), "XDG_CONFIG_HOME": str(run_dir / "xdg-config")}


def visible_credentials() -> dict[str, str]:
    """The provider API keys the team's process will find: the environment's, and the
    repository's ``.env`` (importing CrewAI loads it, as it does in the team's own process)."""

    import crewai  # noqa: F401

    from engineering_team.model_routing import CREDENTIAL_ENV

    return {
        name: os.environ[name]
        for names in CREDENTIAL_ENV.values()
        for name in names
        if os.environ.get(name)
    }


def _live_environment(options: RunOptions, run_dir: Path, task: BenchTask) -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [source_root(), env.get("PYTHONPATH")]))
    env.update(neutral_settings_environment(run_dir))
    env.update(
        CREWAI_STORAGE_DIR=str(run_dir / "crewai-storage"),
        CREWAI_DISABLE_TELEMETRY="true",
        OTEL_SDK_DISABLED="true",
        # Stop gracefully a little before the harness's own timeout kills the process.
        ENGINEERING_BUDGET_MAX_WALL_SECONDS=str(max(30, int(task.timeout_seconds * 0.9))),
    )
    if options.run_budget_usd is not None:
        env["ENGINEERING_BUDGET_MAX_COST_USD"] = f"{options.run_budget_usd:.4f}"
    return env


def prepare(spec: RunSpec, options: RunOptions, run_dir: Path) -> Prepared:
    """Create the run directory, the workspace, and the command line (nothing is started)."""

    task = spec.task
    run_dir.mkdir(parents=True)
    request = run_dir / "request.md"
    shutil.copyfile(task.request_path, request)
    run_id = new_run_id()
    work_root = run_dir / "ws"  # parent of generated projects; also holds isolation copies
    work_root.mkdir()
    if task.kind == "greenfield":
        workspace = work_root / PROJECT
    else:
        workspace = run_dir / "repo"
        _make_repository(task, workspace)

    if options.fake:
        home = run_dir / "home"
        home.mkdir()
        argv = [
            options.python, "-m", "engineering_team.bench.fake_team",
            "--task-dir", str(task.root), "--workspace", str(workspace),
            "--run-id", run_id, "--solution", options.fake_solution,
        ]  # fmt: skip
        return Prepared(run_dir, workspace, argv, clean_environment(home), run_id)

    argv = [options.python, "-m", "engineering_team", "--json", "--workspace-root", str(work_root)]
    argv += ["--run-id", run_id]
    if task.mode == "new":
        argv += ["new", "--request-file", str(request), "--project-name", PROJECT]
        argv += ["--strategy", spec.strategy]
    else:
        argv += [task.mode, "--repo", str(workspace), "--project-name", PROJECT]
        if task.mode == "maintain":
            argv += ["--task", str(task.maintain_task), "--goal-file", str(request)]
        else:
            argv += ["--request-file", str(request)]
        if task.trace_file:  # a copy in the run directory: the team must not learn the task's path
            shutil.copyfile(task.root / task.trace_file, run_dir / "trace.txt")
            argv += ["--trace-file", str(run_dir / "trace.txt")]
        if task.repro:
            argv += ["--repro", task.repro]
    for flag, value in (
        ("--provider", options.provider),
        ("--profile", options.profile),
        ("--sandbox", options.sandbox),
        ("--config", options.config),
    ):
        if value:
            argv += [flag, value]
    argv.append("--non-interactive")
    return Prepared(run_dir, workspace, argv, _live_environment(options, run_dir, task), run_id)
