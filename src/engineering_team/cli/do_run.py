"""Running a ``do`` plan: the route it takes and the executor behind each route.

A change to an existing project goes through ``cli/repo_mode.py`` with the recipe composed for
it, a new project through the ``new`` path, a review and a question about a project through the
``review`` and ``analyze`` commands (the CLI is entered again with the same options).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import typer

from engineering_team.atomic_io import atomic_write_json
from engineering_team.cli import do_plan
from engineering_team.cli.context import Globals, print_json
from engineering_team.cli.repo_mode import RepoRun, project_name_of, run_repository_mode
from engineering_team.cli.run_commands import present_run
from engineering_team.intake.bundle import RequestBundle
from engineering_team.pipeline.ladder_contracts import LadderRecord
from engineering_team.pipeline.strategies import RECIPE_STRATEGIES
from engineering_team.settings import Settings, load_settings

PROFILE_FILE = "task_profile.json"
MAX_FOCUS_CHARS = 2_000


def forwarded(options: SimpleNamespace, config: str | None) -> list[str]:
    out: list[str] = []
    for flag, value in (("--provider", options.provider), ("--profile", options.profile),
                        ("--config", config)):  # fmt: skip
        if value:
            out += [flag, value]
    return out


def execute(
    ctx: typer.Context,
    g: Globals,
    plan: do_plan.DoPlan,
    *,
    options: SimpleNamespace,
    config: str | None,
    ask: bool,
    context_dir: str | None,
    worktree: bool,
    allow_dirty: bool,
    squash: bool,
    reset: bool,
    force_reset: bool,
    fix: bool,
    stdin_used: bool,
) -> None:
    from engineering_team.cli.app import _forward_globals, main

    def request_bundle(settings: Settings) -> RequestBundle:
        return RequestBundle.from_sources(
            text=do_plan.with_notes(plan.request, plan.profile),
            context_dir=context_dir,
            limits=settings.intake,
        )

    if plan.route in ("review", "analyze") and plan.root is not None:
        common = [*_forward_globals(ctx), "--repo", str(plan.root), *forwarded(options, config)]
        if plan.route == "review":
            argv = ["review", *common, "--focus", plan.request[:MAX_FOCUS_CHARS]]
        else:
            argv = ["analyze", *common, "--deep"]
        raise typer.Exit(main(argv))
    if plan.route == "new":
        run_new(g, plan, options, config, request_bundle, reset, force_reset, ask)
        return
    root = plan.root
    assert root is not None  # the other routes have a project
    options.project_name = project_name_of(root, options.project_name)
    task = do_plan.TASK_OF.get(plan.profile.primary)
    run_repository_mode(
        g,
        RepoRun(
            mode=plan.run_mode,
            root=root,
            load=request_bundle,
            options=options,
            composed=plan.recipe,
            worktree=worktree,
            allow_dirty=allow_dirty,
            squash=squash,
            interactive=ask,
            stdin_used=stdin_used,
            project_name=options.project_name,
            config=config,
            slug_fallback=plan.profile.primary.replace("_", "-"),
            run_options={"task": task, "fix_findings": fix} if task else {},
            opened=lambda prepared: _record(prepared, plan),
            extra=lambda prepared, data: _describe(prepared, data, plan),
        ),
    )


def run_new(
    g: Globals,
    plan: do_plan.DoPlan,
    options: SimpleNamespace,
    config: str | None,
    request_bundle: Any,
    reset: bool,
    force_reset: bool,
    ask: bool,
) -> None:
    from engineering_team import main as engine

    printed: list[bool] = []

    def prepare() -> Any:
        settings = load_settings(overrides=engine._cli_overrides(options), config_file=config)
        bundle = request_bundle(settings)
        settings = settings.for_request(bundle.text)
        settings.check_ready(require_credentials=True)
        prepared = engine._open_run(
            settings,
            mode="build",
            requirements=bundle.text,
            context=bundle.context,
            reset=reset,
            force_reset=force_reset,
            recipe=plan.recipe if settings.strategy in RECIPE_STRATEGIES else None,
        )
        _record(prepared, plan)
        return prepared

    def work(prepared: Any) -> int:
        code = present_run(prepared, g, ask=ask, extra=lambda d: _describe(prepared, d, plan))
        printed.append(True)
        return code

    code = engine._execute(prepare, work, lambda p: p.release(quiet=bool(printed) or g.json))
    if g.json and not printed:
        print_json({"status": "error", "exit_code": code})
    raise typer.Exit(code)


def _record(prepared: Any, plan: do_plan.DoPlan) -> None:
    """Keep the plan with the run (what was understood, composed and estimated)."""

    atomic_write_json(prepared.ctx.run_dir / PROFILE_FILE, do_plan.plan_document(plan))


def _describe(prepared: Any, data: dict[str, Any], plan: do_plan.DoPlan) -> None:
    """What the run was, for the summary: the intent, size, route and the rungs it climbed."""

    profile = plan.profile
    ladder = LadderRecord.load(prepared.ctx.run_dir)
    data["task"] = {
        "intents": profile.intents,
        "size": profile.size,
        "risk": profile.risk,
        "route": plan.route,
        "recipe": plan.recipe.name if plan.recipe else None,
        "rungs": [r.rung for r in ladder.rungs] if ladder else [],
    }
