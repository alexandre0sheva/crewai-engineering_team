"""Running the answer paths of ``do``: a question, research, or a plan, none of which changes the
project.

A question about a project is answered where the project is, read-only (no branch, worktree or
copy: nothing is written to it); a request with no project runs in a workspace of its own
(``answers`` by default) that holds only the run's records. ``ANSWER.md`` or ``PLAN.md`` is
printed as Markdown when the run ends and kept in the run directory.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import typer
from rich.markdown import Markdown

from engineering_team.cli import do_plan
from engineering_team.cli.context import Globals, print_json
from engineering_team.cli.repo_mode import project_name_of
from engineering_team.cli.run_commands import present_run
from engineering_team.intake.bundle import RequestBundle
from engineering_team.modes.answer_contracts import ANSWER_FILE, PLAN_FILE
from engineering_team.modes.repo_analyzer import standalone_git
from engineering_team.pipeline.recipes import pin_recipe
from engineering_team.pipeline.state import PipelineState, RunBundle
from engineering_team.pipeline.strategies import get_strategy
from engineering_team.runtime.context import RunContext, new_run_id
from engineering_team.runtime.locks import WorkspaceLock
from engineering_team.runtime.run_index import register_workspace
from engineering_team.runtime.session import RunRecorder
from engineering_team.settings import Settings, load_settings
from engineering_team.tools.workspace import ProjectWorkspace

DEFAULT_NAME = "answers"
Hook = Callable[[Any], None]


def open_answer(settings: Settings, root: Path, plan: do_plan.DoPlan, request: str) -> Any:
    """Open a run in ``root`` itself, read-only: the controller's records go to the run
    directory. The caller releases the lock when the run is over."""

    from engineering_team.main import PreparedRun

    recipe = plan.recipe
    assert recipe is not None  # the answer routes always have one
    workspace = ProjectWorkspace.create(
        root,
        extra_commands=settings.command_allowlist,
        env_passthrough=settings.subprocess_env_allowlist,
    )
    with standalone_git(root) as git:
        if git.is_repo():  # before anything is written there
            git.exclude_controller_state()
    run_id = new_run_id()
    lock = WorkspaceLock(workspace.root).acquire(run_id)
    try:
        context = RunContext.create(settings, workspace, run_id=run_id, adopted=True)
        if recipe.source != "bundled":
            pin_recipe(context.run_dir, recipe)
        recorder = RunRecorder.begin(
            context,
            mode="answer",
            request=request,
            strategy="pipeline",
            recipe=recipe.name,
            stage_reasons={s.name: s.why for s in recipe.stages if s.why},
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


def run_answer_route(
    g: Globals,
    plan: do_plan.DoPlan,
    options: SimpleNamespace,
    config: str | None,
    request_bundle: Callable[[Settings], RequestBundle],
    ask: bool,
    *,
    opened: Hook,
    extra: Callable[[Any, dict[str, Any]], None],
) -> None:
    from engineering_team import main as engine

    printed: list[bool] = []

    def prepare() -> Any:
        label = project_name_of(plan.root, options.project_name) if plan.root else None
        overrides = {
            **engine._cli_overrides(options),
            "git.enabled": False,  # no stage commits: an answer never makes history
            "project_name": label or options.project_name or DEFAULT_NAME,
            **({"strategy": "pipeline"} if plan.root else {}),
        }
        settings = load_settings(overrides=overrides, config_file=config)
        bundle = request_bundle(settings)
        settings = settings.for_request(bundle.text)
        settings.check_ready(require_credentials=True)
        if plan.root is None:
            prepared = engine._open_run(
                settings, mode="answer", requirements=bundle.text, recipe=plan.recipe
            )
        else:
            register_workspace(settings.workspace_root, plan.root, label or plan.root.name)
            prepared = open_answer(settings, plan.root, plan, bundle.text)
        opened(prepared)
        return prepared

    def work(prepared: Any) -> int:
        code = present_run(prepared, g, ask=ask, extra=lambda data: extra(prepared, data))
        printed.append(True)
        return code

    code = engine._execute(prepare, work, lambda p: p.release(quiet=bool(printed) or g.json))
    if g.json and not printed:
        print_json({"status": "error", "exit_code": code})
    raise typer.Exit(code)


def describe_document(g: Globals, run_dir: Path, data: dict[str, Any]) -> None:
    """Add the answer (or plan) to the summary data and show it: its path as the report, how it
    was proved, and (for people) the Markdown itself."""

    state = PipelineState.load(run_dir)
    for name in (ANSWER_FILE, PLAN_FILE):
        path = run_dir / name
        if not path.is_file():
            continue
        data["report"] = data["document"] = str(path)
        check = state.answer_check if state else None
        if check is not None and state is not None and state.answer is not None:
            data["answer"] = {
                "summary": state.answer.summary,
                "confidence": state.answer.confidence,
                "verified": check.verified,
                "citations": [r.model_dump(mode="json") for r in check.results],
            }
            data["proof"] = (
                f"{check.verified} of {len(check.results)} citation(s) verified by the controller"
            )
        elif name == PLAN_FILE:
            data["proof"] = "a plan only: nothing was implemented or run"
        if not (g.json or g.quiet):
            text = path.read_text(encoding="utf-8")
            g.console().print(Markdown(text), soft_wrap=True)
        return
