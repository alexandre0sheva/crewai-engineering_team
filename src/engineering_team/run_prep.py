"""Preparing a run: command-line options to settings, the workspace, and its run context."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from engineering_team.intake import ContextScan, install_context
from engineering_team.model_routing import PROFILE_NAMES, PROVIDERS
from engineering_team.pipeline.recipes import Recipe, pin_recipe
from engineering_team.pipeline.state import RunBundle
from engineering_team.pipeline.strategies import (
    RECIPE_STRATEGIES,
    STRATEGY_NAMES,
    Strategy,
    get_strategy,
    recipe_name_for,
)
from engineering_team.request_sources import DEFAULT_EXAMPLE, build_inputs, load_bundle
from engineering_team.runtime.context import RunContext, new_run_id
from engineering_team.runtime.locks import WorkspaceLock
from engineering_team.runtime.session import RunRecorder, format_summary
from engineering_team.settings import (
    Settings,
    load_settings,
)
from engineering_team.tools import ProjectWorkspace
from engineering_team.verification.checks_file import ChecksPin, pin_checks
from engineering_team.workspaces import (
    prepare_workspace,
)


def _run_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="engineering-team",
        description="Build or continue an MVP with the CrewAI engineering team.",
    )
    request_source = parser.add_mutually_exclusive_group()
    request_source.add_argument("--request", help="Inline product requirements.")
    request_source.add_argument(
        "--request-file",
        help="Markdown or text file containing product requirements.",
    )
    request_source.add_argument(
        "--example",
        help=f"Use a bundled example request (e.g. {DEFAULT_EXAMPLE}).",
    )
    parser.add_argument(
        "--project-name",
        help="Name used for the persistent workspace directory (default: mvp-app).",
    )
    parser.add_argument(
        "--workspace-root",
        help="Parent directory for generated projects (default: ./workspace; relative paths "
        "use the current directory).",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete this project's existing workspace before starting (only if this tool "
        "created it).",
    )
    parser.add_argument(
        "--force-reset",
        action="store_true",
        help="Like --reset, but also deletes a directory this tool did not create. "
        "Dangerous targets (home, cwd, filesystem root, symlinks) are still refused.",
    )
    parser.add_argument(
        "--adopt",
        action="store_true",
        help="Let the team work in an existing directory it did not create (not a Git "
        "repository; see `analyze` and docs/USAGE.md for repositories).",
    )
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="Validate inputs and prepare the workspace without calling an LLM.",
    )
    parser.add_argument(
        "--profile",
        choices=list(PROFILE_NAMES),
        help="Quality/cost routing: standard, the lower-cost smoke, or max-quality.",
    )
    parser.add_argument(
        "--provider",
        choices=list(PROVIDERS),
        help="Model provider preset (default: openai).",
    )
    parser.add_argument(
        "--strategy",
        choices=list(STRATEGY_NAMES),
        help="How the team is orchestrated: pipeline (staged, resumable; the default, see "
        "docs/BENCHMARKS.md), single (one agent, the benchmark baseline) or team (a lead "
        "agent that calls teammates on demand; see docs/ARCHITECTURE.md).",
    )
    parser.add_argument(
        "--allow-web",
        action="store_true",
        help="Let the team use the web tools (search, fetch, package info). Off by default; "
        "see docs/SAFETY.md.",
    )
    parser.add_argument(
        "--sandbox",
        choices=["local", "docker"],
        help="Where project commands run: local (on this machine, the default) or docker "
        "(a hardened container per command; needs Docker, never falls back to local). "
        "See docs/SAFETY.md.",
    )
    parser.add_argument(
        "--checks",
        help="YAML file of your own checks the controller runs to verify the result (pipeline "
        "strategy). Keep it outside the project; see docs/CONFIGURATION.md.",
    )
    parser.add_argument(
        "--no-git",
        action="store_true",
        help="Do not make a new project a Git repository or commit after each stage.",
    )
    parser.add_argument(
        "--config",
        help="Path to a config file (default: ./engineering-team.toml if present).",
    )
    return parser


def _absolute(path: str | None) -> str | None:
    """``path`` as an absolute path (relative paths mean the current directory)."""

    return str(Path(path).expanduser().resolve()) if path else None


def _cli_overrides(args: Any) -> dict[str, Any]:
    """Settings overrides from the options a command defines (each command defines some)."""

    names: dict[str, Any] = {
        "web.enabled": True if getattr(args, "allow_web", False) else None,
        "provider": getattr(args, "provider", None),
        "profile": getattr(args, "profile", None),
        "strategy": getattr(args, "strategy", None),
        "verify.checks_file": _absolute(getattr(args, "checks", None)),
        "git.enabled": False if getattr(args, "no_git", False) else None,
        "execution.backend": getattr(args, "sandbox", None),
        "verbose": getattr(args, "verbose", None),
        "project_name": getattr(args, "project_name", None),
        "workspace_root": getattr(args, "workspace_root", None),
    }
    return {key: value for key, value in names.items() if value is not None}


@dataclass
class PreparedRun:
    """A prepared workspace, its run context, what to run, and the lock held on the workspace."""

    ctx: RunContext
    inputs: dict[str, str]
    lock: WorkspaceLock
    recorder: RunRecorder
    bundle: RunBundle
    strategy: Strategy
    recipe: Recipe | None = None

    def release(self, *, quiet: bool = False) -> None:
        """Report what the run used and cost (unless ``quiet``: the CLI shows its own summary),
        then drop the workspace lock."""

        try:
            summary = self.recorder.manifest.summary
            if not quiet and summary is not None and (summary.usage.calls or summary.tool_calls):
                print(f"\n{format_summary(summary)}")
        finally:
            self.lock.release()


def _prepare_workspace(
    settings: Settings, *, reset: bool = False, force_reset: bool = False, adopt: bool = False
) -> ProjectWorkspace:
    return prepare_workspace(
        settings.project_name,
        settings.workspace_root,
        reset=reset or force_reset,
        force_reset=force_reset,
        adopt=adopt,
        command_allowlist=settings.command_allowlist,
        subprocess_env_allowlist=settings.subprocess_env_allowlist,
    )


def _open_run(
    settings: Settings,
    *,
    mode: str = "build",
    requirements: str | None = None,
    context: ContextScan | None = None,
    reset: bool = False,
    force_reset: bool = False,
    adopt: bool = False,
    workspace: ProjectWorkspace | None = None,
    run_id: str | None = None,
    recipe: Recipe | None = None,
) -> PreparedRun:
    """Prepare the workspace, take its write lock, and build the run context and inputs.

    ``context`` (the reference documents of ``--context-dir``) is copied into the workspace.
    A repository mode passes the ``workspace`` its isolation made (and the ``run_id`` that named
    the branch) and its ``recipe``: the run then uses the pipeline strategy (or the team
    strategy, when it is the configured one, which derives the lead's recipe from it), and the
    controller's reports go to the run directory instead of the project's ``docs/``.

    Also creates the run's manifest (``pending``); the caller wraps the work in
    ``recorder.running()`` and releases the lock (``_execute`` does) once the run is over. A
    workspace that another run holds raises ``WorkspaceBusy``, which the CLI reports as a
    usage error.
    """

    adopted = workspace is not None
    if workspace is None:
        workspace = _prepare_workspace(settings, reset=reset, force_reset=force_reset, adopt=adopt)
    run_id = run_id or new_run_id()
    lock = WorkspaceLock(workspace.root).acquire(run_id)
    strategy_name = settings.strategy
    if recipe is not None and strategy_name not in RECIPE_STRATEGIES:
        strategy_name = "pipeline"
    try:
        if context is not None:
            install_context(workspace.root, context)
        ctx = RunContext.create(settings, workspace, run_id=run_id, adopted=adopted)
        pin = _pin_checks(settings, ctx, mode, strategy_name)
        if recipe is not None and recipe.source != "bundled":
            pin_recipe(ctx.run_dir, recipe)
        recorder = RunRecorder.begin(
            ctx,
            mode=mode,
            request=requirements or "",
            strategy=strategy_name,
            recipe=recipe.name if recipe is not None else recipe_name_for(strategy_name),
            stage_reasons={s.name: s.why for s in recipe.stages if s.why} if recipe else None,
        )
        inputs = (
            build_inputs(
                project_name=settings.project_name,
                requirements=requirements,
                workspace=workspace,
                run_profile=settings.profile,
            )
            if requirements is not None
            else {}
        )
    except BaseException:
        lock.release()
        raise
    return PreparedRun(
        ctx=ctx,
        inputs=inputs,
        lock=lock,
        recorder=recorder,
        bundle=RunBundle(
            requirements=requirements or "",
            inputs=inputs,
            checks_digest=pin.digest,
            script_digests=pin.script_digests,
        ),
        strategy=get_strategy(strategy_name),
        recipe=recipe,
    )


def _pin_checks(settings: Settings, ctx: RunContext, mode: str, strategy: str) -> ChecksPin:
    """Validate and pin the user's checks file (a problem with it is a usage error)."""

    source = settings.verify.checks_file
    if source is None or mode not in ("build", "feature", "fix", "maintain"):
        return ChecksPin()
    if strategy not in RECIPE_STRATEGIES:
        raise ValueError(
            f"Your checks file is only run by the 'pipeline' and 'team' strategies, not "
            f"'{strategy}'. Pass --strategy pipeline, or drop --checks."
        )
    return pin_checks(ctx, Path(source))


def _prepare_from_args(args: Any, mode: str = "build") -> PreparedRun:
    settings = load_settings(overrides=_cli_overrides(args), config_file=args.config)
    files = args.request_file
    bundle = load_bundle(
        inline_request=args.request,
        request_files=[files] if isinstance(files, str) else list(files or []),
        example=args.example,
        context_dir=getattr(args, "context_dir", None),
        settings=settings,
    )
    settings = settings.for_request(bundle.text)
    settings.check_ready(require_credentials=not getattr(args, "prepare_only", False))
    return _open_run(
        settings,
        mode="prepare" if getattr(args, "prepare_only", False) else mode,
        requirements=bundle.text,
        context=bundle.context,
        reset=args.reset,
        force_reset=args.force_reset,
        adopt=getattr(args, "adopt", False),
    )
