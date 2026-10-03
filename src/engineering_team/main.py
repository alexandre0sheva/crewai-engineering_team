#!/usr/bin/env python
"""Command-line and CrewAI entry points.

Every entry point returns a process exit code: ``0`` success (a verifying run: verified),
``2`` usage or configuration error (one-line message, no traceback), ``1`` runtime failure,
``3`` the controller's checks failed (not verified), ``4`` verification was partial (a required
check could not run), ``130`` interrupted.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
import warnings
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from importlib import resources
from pathlib import Path
from typing import Any, TextIO, TypeVar

from engineering_team.crew import EngineeringTeam
from engineering_team.intake import TEMPLATE_MARKER, ContextScan, RequestBundle, install_context
from engineering_team.model_routing import PROFILE_NAMES, PROVIDERS
from engineering_team.pipeline.recipes import Recipe
from engineering_team.pipeline.runner import (
    execute_run,
    open_resume,
    read_request,
)
from engineering_team.pipeline.state import RunBundle, RunResult, request_hash
from engineering_team.pipeline.strategies import (
    STRATEGY_NAMES,
    Strategy,
    get_strategy,
    recipe_name_for,
)
from engineering_team.runtime.budget import BudgetExceeded
from engineering_team.runtime.context import RunContext, new_run_id
from engineering_team.runtime.locks import WorkspaceLock
from engineering_team.runtime.run_index import locate_run
from engineering_team.runtime.session import RunRecorder, format_summary
from engineering_team.settings import (
    SMOKE_PROFILE_MARKER,
    Settings,
    load_settings,
)
from engineering_team.tools import ProjectWorkspace
from engineering_team.verification.checks_file import ChecksPin, pin_checks
from engineering_team.workspaces import (
    prepare_workspace,
    slugify_project_name,
)

warnings.filterwarnings("ignore", category=SyntaxWarning, module="pysbd")

__all__ = [
    "SMOKE_PROFILE_MARKER",
    "TEMPLATE_MARKER",
    "prepare_workspace",
    "slugify_project_name",
    "load_requirements",
    "run",
    "resume",
    "train",
    "replay",
    "test",
    "run_with_trigger",
]

DEFAULT_REQUEST_FILENAME = "PROJECT_REQUEST.md"
DEFAULT_EXAMPLE = "tiny-notes"

EXIT_FAILURE = 1
EXIT_USAGE = 2
EXIT_VERIFICATION_FAILED = 3
EXIT_PARTIAL = 4
EXIT_INTERRUPTED = 130

T = TypeVar("T")


def available_examples() -> list[str]:
    """Names of the example requests bundled with the package."""

    root = resources.files("engineering_team") / "examples"
    if not root.is_dir():
        return []
    return sorted(
        entry.name
        for entry in root.iterdir()
        if entry.is_dir() and (entry / "request.md").is_file()
    )


def load_example(name: str) -> str:
    """Read a bundled example request by name."""

    examples = available_examples()
    if name not in examples:
        raise ValueError(f"Unknown example '{name}'. Available examples: {', '.join(examples)}.")
    request = resources.files("engineering_team") / "examples" / name / "request.md"
    return request.read_text(encoding="utf-8")


NO_REQUEST = (
    "No project request found. Pass --request, --request-file, or "
    f"--example {DEFAULT_EXAMPLE} (see --help), or create {DEFAULT_REQUEST_FILENAME} "
    "in the current directory."
)


def load_bundle(
    *,
    inline_request: str | None = None,
    request_files: Sequence[str | Path] = (),
    example: str | None = None,
    context_dir: str | Path | None = None,
    stdin: TextIO | None = None,
    settings: Settings | None = None,
) -> RequestBundle:
    """Load the normalised, non-placeholder request (see :class:`RequestBundle`).

    Explicit sources win over the environment: ``--request`` and ``--request-file`` (repeatable;
    ``-`` is stdin) merge in that order, ``--example`` stands alone. With none of them the
    first of ``ENGINEERING_PROJECT_REQUEST``, ``ENGINEERING_REQUEST_FILE``, and
    ``PROJECT_REQUEST.md`` in the current directory is used. A blank explicit value is an
    error rather than a silent fallback. ``ValueError`` (``IntakeError``) says what to fix.
    """

    settings = settings or load_settings()
    limits = settings.intake
    if example is not None:
        if inline_request is not None or request_files:
            raise ValueError(
                "Use only one of --example, or --request/--request-file (those can be combined)."
            )
        return RequestBundle.from_sources(
            text=load_example(example), context_dir=context_dir, limits=limits
        )
    default_file = Path.cwd() / DEFAULT_REQUEST_FILENAME
    files: Sequence[str | Path] = request_files
    if inline_request is None and not files:
        if settings.request is not None:
            inline_request = settings.request
        elif settings.request_file is not None:
            files = [settings.request_file]
        elif default_file.is_file():
            files = [default_file]
        else:
            raise ValueError(NO_REQUEST)
    return RequestBundle.from_sources(
        text=inline_request, files=files, stdin=stdin, context_dir=context_dir, limits=limits
    )


def load_requirements(
    *,
    inline_request: str | None = None,
    request_file: str | Path | None = None,
    example: str | None = None,
    settings: Settings | None = None,
) -> str:
    """The text of :func:`load_bundle` for one inline request, file, or example."""

    return load_bundle(
        inline_request=inline_request,
        request_files=[request_file] if request_file is not None else [],
        example=example,
        settings=settings,
    ).text


def build_inputs(
    *,
    project_name: str,
    requirements: str,
    workspace: ProjectWorkspace,
    run_profile: str,
) -> dict[str, str]:
    """Create the interpolation inputs shared by every agent and task.

    The request itself is recorded in the run directory by :class:`RunRecorder`.
    """

    return {
        "project_name": project_name,
        "requirements": requirements,
        "workspace_path": str(workspace.root),
        "current_date": date.today().isoformat(),
        "run_profile": run_profile,
    }


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
    parser.add_argument("--adopt", action="store_true", help=argparse.SUPPRESS)  # reserved
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
        help="How the team is orchestrated: hierarchical (default), pipeline (staged, "
        "resumable), or single (one agent, the benchmark baseline).",
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
    settings: Settings, *, reset: bool = False, force_reset: bool = False
) -> ProjectWorkspace:
    return prepare_workspace(
        settings.project_name,
        settings.workspace_root,
        reset=reset or force_reset,
        force_reset=force_reset,
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
) -> PreparedRun:
    """Prepare the workspace, take its write lock, and build the run context and inputs.

    ``context`` (the reference documents of ``--context-dir``) is copied into the workspace.

    Also creates the run's manifest (``pending``); the caller wraps the work in
    ``recorder.running()`` and releases the lock (``_execute`` does) once the run is over. A
    workspace that another run holds raises ``WorkspaceBusy``, which the CLI reports as a
    usage error.
    """

    workspace = _prepare_workspace(settings, reset=reset, force_reset=force_reset)
    run_id = new_run_id()
    lock = WorkspaceLock(workspace.root).acquire(run_id)
    # Only builds follow the strategy setting; train, test, and replay are crew commands.
    strategy_name = settings.strategy if mode == "build" else "hierarchical"
    try:
        if context is not None:
            install_context(workspace.root, context)
        ctx = RunContext.create(settings, workspace, run_id=run_id)
        pin = _pin_checks(settings, ctx, mode, strategy_name)
        recorder = RunRecorder.begin(
            ctx,
            mode=mode,
            request=requirements or "",
            strategy=strategy_name,
            recipe=recipe_name_for(strategy_name),
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
    )


def _pin_checks(settings: Settings, ctx: RunContext, mode: str, strategy: str) -> ChecksPin:
    """Validate and pin the user's checks file (a problem with it is a usage error)."""

    source = settings.verify.checks_file
    if source is None or mode != "build":
        return ChecksPin()
    if strategy != "pipeline":
        raise ValueError(
            f"Your checks file is only run by the 'pipeline' strategy, not '{strategy}'. "
            "Pass --strategy pipeline, or drop --checks."
        )
    return pin_checks(ctx, Path(source))


def _prepare_from_args(args: Any, mode: str = "build") -> PreparedRun:
    if args.adopt:
        raise ValueError("--adopt is not implemented yet.")
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
    )


def _execute(
    prepare: Callable[[], T],
    work: Callable[[T], int | None],
    release: Callable[[T], None] | None = None,
) -> int:
    """Run an entry point in two phases and map failures to exit codes.

    ``prepare`` validates input and sets up the workspace; a ``ValueError`` there is a usage
    or configuration problem (exit 2, one line). ``work`` calls the crew; any failure there
    is a runtime failure (exit 1, with traceback), and it may return an exit code itself (a run
    that ended failed or cancelled without raising). ``release`` always runs after a successful
    ``prepare`` so the workspace lock is dropped however ``work`` ends.
    """

    try:
        prepared = prepare()
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return EXIT_INTERRUPTED
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    try:
        code = work(prepared)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return EXIT_INTERRUPTED
    except BudgetExceeded as exc:
        print(f"Engineering team run stopped: {exc}", file=sys.stderr)
        return EXIT_FAILURE
    except Exception as exc:
        traceback.print_exc()
        print(f"Engineering team run failed: {exc}", file=sys.stderr)
        return EXIT_FAILURE
    finally:
        if release is not None:
            release(prepared)
    return code or 0


def run(argv: Sequence[str] | None = None) -> int:
    """The ``engineering-team`` command: ``new``, ``resume``, ``status``, ``runs``, ``board``, ...

    The 0.1.0 form (``engineering-team --request-file FILE``) still works: it runs ``new`` and
    prints a deprecation notice. See :mod:`engineering_team.cli.app`.
    """

    from engineering_team.cli.app import main

    return main(list(sys.argv[1:] if argv is None else argv))


def exit_code_for(result: RunResult) -> int:
    """The process exit code for how a run ended (see the module docstring)."""

    if result.status == "succeeded":
        return 0
    if result.status == "cancelled":
        return EXIT_INTERRUPTED
    if result.verdict == "failed":
        return EXIT_VERIFICATION_FAILED
    if result.verdict == "partial":
        return EXIT_PARTIAL
    return EXIT_FAILURE


def failure_line(result: RunResult, *, resumable: bool) -> str | None:
    """One line saying how a run that did not succeed ended (``None`` for a success)."""

    hint = f" Continue it with: engineering-team resume {result.run_id}" if resumable else ""
    if result.status == "succeeded":
        return None
    if result.status == "cancelled":
        return f"Run {result.run_id} was cancelled.{hint}"
    if result.verdict in ("failed", "partial"):
        return f"Run {result.run_id} is not verified: {result.error.rstrip('.')}.{hint}"
    return f"Engineering team run failed: {result.error.rstrip('.')}.{hint}"


def _report(result: RunResult, *, resumable: bool) -> int:
    """Say how a run ended (one line each) and return the matching exit code."""

    line = failure_line(result, resumable=resumable)
    if line is None:
        print(f"\nProject workspace: {result.workspace}")
    else:
        print(f"\n{line}", file=sys.stderr)
    return exit_code_for(result)


def _run_prepared(prepared: PreparedRun) -> int:
    """Run the prepared run under its strategy and report how it ended."""

    result = execute_run(
        prepared.ctx, prepared.bundle, strategy=prepared.strategy, recipe=prepared.recipe
    )
    return _report(result, resumable=prepared.strategy.resumable)


def _prepare_resume(settings: Settings, run_id: str, request: str | None) -> PreparedRun:
    """Reopen ``run_id``, or, when ``request`` differs from the one it was started with, start a
    new run for that request (old evidence is never reused for a different request)."""

    settings.check_ready(require_credentials=True)
    _, manifest, _ = read_request(settings, run_id)
    if request is not None and request_hash(request) != manifest.request_hash:
        print(
            f"The request changed since run {run_id}; starting a new run instead of resuming it.",
            file=sys.stderr,
        )
        fresh = settings.for_request(request).with_overrides(
            {"strategy": manifest.strategy}, source=f"changed request (was run {run_id})"
        )
        return _open_run(fresh, mode="build", requirements=request)
    opened = open_resume(settings, run_id)
    return PreparedRun(
        ctx=opened.ctx,
        inputs={},
        lock=opened.lock,
        recorder=RunRecorder.attach(opened.ctx),
        bundle=opened.bundle,
        strategy=opened.strategy,
        recipe=opened.recipe,
    )


def resume(
    run_id: str, *, settings: Settings | None = None, request: str | None = None
) -> RunResult:
    """Continue an unfinished or failed pipeline run and return how it ended.

    Stages that finished (and whose workspace state still matches) are not run again. Passing a
    ``request`` that differs from the run's own starts a new run for it instead. Raises
    ``ValueError`` when the run cannot be resumed (unknown, already succeeded, hierarchical,
    recipe changed) and ``WorkspaceBusy`` when another run holds the workspace.
    """

    prepared = _prepare_resume(settings or load_settings(), run_id, request)
    try:
        return execute_run(
            prepared.ctx, prepared.bundle, strategy=prepared.strategy, recipe=prepared.recipe
        )
    finally:
        prepared.lock.release()


def train() -> int:
    """Train the crew using the current request and workspace."""

    def prepare() -> tuple[int, str, PreparedRun]:
        if len(sys.argv) < 3:
            raise ValueError("Usage: train <iterations> <training-file>")
        iterations = int(sys.argv[1])
        args = _run_parser().parse_args(sys.argv[3:])
        return iterations, sys.argv[2], _prepare_from_args(args, "train")

    def work(prepared: tuple[int, str, PreparedRun]) -> None:
        iterations, filename, run_state = prepared
        with run_state.recorder.running():
            EngineeringTeam(run_state.ctx).crew().train(
                n_iterations=iterations, filename=filename, inputs=run_state.inputs
            )

    return _execute(prepare, work, lambda prepared: prepared[2].release())


def _replay_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="replay",
        description="Replay a crew run from a task id. The project is named explicitly (a run "
        "id or a project name), not guessed from the environment.",
    )
    parser.add_argument("task_id", help="The CrewAI task id to replay from.")
    parser.add_argument("--run", help="A run id (or unambiguous prefix); its project is used.")
    parser.add_argument("--project-name", help="The project to replay in.")
    parser.add_argument("--workspace-root", help="Parent directory of generated projects.")
    parser.add_argument("--config", help="Path to a config file.")
    return parser


def replay() -> int:
    """Replay a crew run: ``replay <task-id> [--run RUN_ID | --project-name NAME]``."""

    def prepare() -> tuple[str, PreparedRun]:
        if len(sys.argv) < 2:
            raise ValueError("Usage: replay <task-id> [--run RUN_ID | --project-name NAME]")
        args = _replay_parser().parse_args(sys.argv[1:])
        settings = load_settings(overrides=_cli_overrides(args), config_file=args.config)
        if args.run:
            ref = locate_run(settings.workspace_root, args.run, args.project_name)
            settings = settings.with_overrides(
                {"project_name": ref.project}, source=f"run {ref.run_id}"
            )
        settings.check_ready(require_credentials=True)
        return args.task_id, _open_run(settings, mode="replay")

    def work(prepared: tuple[str, PreparedRun]) -> None:
        task_id, run_state = prepared
        with run_state.recorder.running():
            EngineeringTeam(run_state.ctx).crew().replay(task_id=task_id)

    return _execute(prepare, work, lambda prepared: prepared[1].release())


def test() -> int:
    """Run CrewAI's iterative crew evaluation command."""

    def prepare() -> tuple[int, str, PreparedRun]:
        if len(sys.argv) < 3:
            raise ValueError("Usage: test <iterations> <evaluation-model>")
        iterations = int(sys.argv[1])
        args = _run_parser().parse_args(sys.argv[3:])
        return iterations, sys.argv[2], _prepare_from_args(args, "test")

    def work(prepared: tuple[int, str, PreparedRun]) -> None:
        iterations, eval_llm, run_state = prepared
        with run_state.recorder.running():
            EngineeringTeam(run_state.ctx).crew().test(
                n_iterations=iterations, eval_llm=eval_llm, inputs=run_state.inputs
            )

    return _execute(prepare, work, lambda prepared: prepared[2].release())


def run_with_trigger() -> int:
    """Run from a CrewAI trigger payload containing requirements and project_name."""

    def prepare() -> PreparedRun:
        if len(sys.argv) < 2:
            raise ValueError("No trigger JSON payload provided.")
        try:
            payload = json.loads(sys.argv[1])
        except json.JSONDecodeError as exc:
            raise ValueError("Trigger payload must be valid JSON.") from exc
        if not isinstance(payload, dict):
            raise ValueError("Trigger payload must be a JSON object.")

        requirements = payload.get("requirements") or payload.get("project_request")
        if not isinstance(requirements, str) or not requirements.strip():
            requirements = json.dumps(payload, indent=2)
        payload_profile = payload.get("run_profile")
        if payload_profile is not None and not isinstance(payload_profile, str):
            raise ValueError("Trigger run_profile must be a string.")

        overrides = {"project_name": str(payload.get("project_name", "triggered-mvp"))}
        if payload_profile is not None:
            overrides["profile"] = payload_profile.strip().lower()
        settings = load_settings().with_overrides(overrides, source="trigger payload")
        settings = settings.for_request(requirements)
        settings.check_ready(require_credentials=True)
        prepared = _open_run(settings, requirements=requirements)
        prepared.inputs["crewai_trigger_payload"] = json.dumps(payload)
        return prepared

    def work(prepared: PreparedRun) -> int:
        return _run_prepared(prepared)

    return _execute(prepare, work, PreparedRun.release)


if __name__ == "__main__":
    sys.exit(run())
