"""``review``: a read-only review of a branch or of the working diff, for people and for CI.

It runs in place, like ``analyze --deep``: nothing in your project is written except the
controller's own state directory (run record, profile), which Git is told to ignore. Reviewers
read the change against the base; the controller writes ``findings.json`` and ``findings.md`` to
the run directory (and to ``--out-dir``) and exits 3 when a finding reaches ``review.fail_on``.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated, Any

import typer

from engineering_team.cli.context import fail, one_of, print_json
from engineering_team.cli.context import get as get_globals
from engineering_team.intake.errors import IntakeError
from engineering_team.model_routing import PROFILE_NAMES, PROVIDERS
from engineering_team.modes.findings_report import FINDINGS_JSON, FINDINGS_MD, read_findings
from engineering_team.modes.repo_analyzer import standalone_git
from engineering_team.modes.run_options import write_run_options
from engineering_team.pipeline.recipes import load_recipe
from engineering_team.pipeline.state import RunBundle
from engineering_team.pipeline.strategies import get_strategy
from engineering_team.runtime.context import RunContext, new_run_id
from engineering_team.runtime.locks import WorkspaceLock
from engineering_team.runtime.run_index import register_workspace
from engineering_team.runtime.session import RunRecorder
from engineering_team.settings import Settings, load_settings
from engineering_team.team import build_roster
from engineering_team.tools.workspace import ProjectWorkspace
from engineering_team.workspaces import slugify_project_name

DEFAULT_REQUEST = "Review the change."


def open_review(settings: Settings, root: Path, base: str | None, focus: str | None) -> Any:
    """Open a run that reviews ``root`` in place. The caller releases the lock when it is over."""

    from engineering_team.main import PreparedRun

    request = (
        f"{DEFAULT_REQUEST} Focus: {focus.strip()}" if focus and focus.strip() else DEFAULT_REQUEST
    )
    recipe = load_recipe("review", root, teammates=build_roster(settings).members)
    workspace = ProjectWorkspace.create(
        root,
        extra_commands=settings.command_allowlist,
        env_passthrough=settings.subprocess_env_allowlist,
    )
    with standalone_git(root) as git:
        if not git.is_repo():
            raise ValueError(f"{root} is not the top level of a Git repository: review needs one.")
        if base and not git.resolves(base):
            raise ValueError(f"--base {base!r} is not a branch, tag, or commit in {root}.")
        git.exclude_controller_state()  # before anything is written there
    run_id = new_run_id()
    lock = WorkspaceLock(workspace.root).acquire(run_id)
    try:
        # ``adopted``: the controller's reports go to the run directory, not into your docs/.
        context = RunContext.create(settings, workspace, run_id=run_id, adopted=True)
        write_run_options(context.run_dir, {"base": base})
        recorder = RunRecorder.begin(
            context, mode="review", request=request, strategy="pipeline", recipe=recipe.name
        )
    except BaseException:
        lock.release()
        raise
    return PreparedRun(
        ctx=context,
        inputs={},
        lock=lock,
        recorder=recorder,
        bundle=RunBundle(requirements=request),
        strategy=get_strategy("pipeline"),
        recipe=recipe,
    )


def review(
    ctx: typer.Context,
    repo: Annotated[str, typer.Option(help="The project directory (default: here).")] = ".",
    base: Annotated[
        str | None,
        typer.Option(
            help="Review everything since this branch, tag, or commit (its merge base with HEAD). "
            "Default: your uncommitted changes, or else the work since main/master."
        ),
    ] = None,
    focus: Annotated[
        str | None, typer.Option(help="What the reviewers should look at hardest.")
    ] = None,
    out_dir: Annotated[
        str | None,
        typer.Option(help="Also copy findings.json and findings.md here (for a CI artifact)."),
    ] = None,
    provider: Annotated[
        str | None, typer.Option(callback=one_of(list(PROVIDERS)), help="Model provider preset.")
    ] = None,
    profile: Annotated[
        str | None,
        typer.Option(callback=one_of(list(PROFILE_NAMES)), help="Quality/cost routing."),
    ] = None,
    config: Annotated[str | None, typer.Option(help="Path to a config file.")] = None,
) -> None:
    """Review a branch or the working diff (read-only); exit 3 on a finding at review.fail_on."""

    from engineering_team import main as engine
    from engineering_team.cli.run_commands import present_run

    g = get_globals(ctx)
    root = Path(repo).expanduser().resolve()
    if not root.is_dir():
        fail(f"{root} is not a directory.")
    args = SimpleNamespace(
        provider=provider, profile=profile, verbose=g.verbose, workspace_root=g.workspace_root
    )
    printed: list[bool] = []

    def prepare() -> Any:
        label = _project_name(root)
        overrides = {
            **engine._cli_overrides(args),
            "strategy": "pipeline",
            "git.enabled": False,  # no stage commits: a review never makes history in your repo
            "project_name": label,
        }
        settings = load_settings(overrides=overrides, config_file=config)
        settings.check_ready(require_credentials=True)
        try:
            register_workspace(settings.workspace_root, root, label)  # `status` and `board` find it
            return open_review(settings, root, base, focus)
        except IntakeError as exc:
            raise ValueError(str(exc)) from exc

    def work(prepared: Any) -> int:
        run_dir = prepared.ctx.run_dir

        def extra(data: dict[str, Any]) -> None:
            path = run_dir / FINDINGS_JSON
            if path.is_file():
                found = read_findings(path)
                data["findings"] = found["findings"]
                data["counts"] = found["counts"]
                data["passed"] = found["passed"]
                data["findings_json"] = str(path)
                data["findings_md"] = str(run_dir / FINDINGS_MD)
                data["report"] = str(run_dir / FINDINGS_MD)
                if out_dir:
                    target = Path(out_dir).expanduser().resolve()
                    target.mkdir(parents=True, exist_ok=True)
                    for name in (FINDINGS_JSON, FINDINGS_MD):
                        shutil.copyfile(run_dir / name, target / name)
                    data["findings_copied_to"] = str(target)
            data["next_steps"] = [s for s in data["next_steps"] if "resume" not in s]

        code = present_run(prepared, g, extra=extra)
        printed.append(True)
        return code

    code = engine._execute(prepare, work, lambda p: p.release(quiet=bool(printed) or g.json))
    if g.json and not printed:
        print_json({"status": "error", "exit_code": code})
    raise typer.Exit(code)


def _project_name(root: Path) -> str:
    try:
        return slugify_project_name(root.name)
    except ValueError:
        return "project"
