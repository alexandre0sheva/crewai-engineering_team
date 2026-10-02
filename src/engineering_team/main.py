#!/usr/bin/env python
"""Command-line and CrewAI entry points.

Every entry point returns a process exit code: ``0`` success, ``2`` usage or configuration
error (one-line message, no traceback), ``1`` runtime failure, ``130`` interrupted.
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
from typing import Any, TypeVar

from engineering_team.crew import EngineeringTeam
from engineering_team.model_routing import PROFILE_NAMES, PROVIDERS
from engineering_team.runtime.budget import BudgetExceeded
from engineering_team.runtime.context import RunContext, new_run_id
from engineering_team.runtime.locks import WorkspaceLock
from engineering_team.runtime.session import RunRecorder, format_summary
from engineering_team.settings import (
    SMOKE_PROFILE_MARKER,
    Settings,
    load_settings,
)
from engineering_team.tools import ProjectWorkspace
from engineering_team.workspaces import (
    prepare_workspace,
    slugify_project_name,
)

warnings.filterwarnings("ignore", category=SyntaxWarning, module="pysbd")

__all__ = [
    "SMOKE_PROFILE_MARKER",
    "prepare_workspace",
    "slugify_project_name",
    "load_requirements",
    "run",
    "train",
    "replay",
    "test",
    "run_with_trigger",
]

DEFAULT_REQUEST_FILENAME = "PROJECT_REQUEST.md"
DEFAULT_EXAMPLE = "tiny-notes"
TEMPLATE_MARKER = "<!-- ENGINEERING_TEAM_REQUEST_TEMPLATE -->"

EXIT_FAILURE = 1
EXIT_USAGE = 2
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


def _read_request_file(path: str | Path) -> str:
    configured = Path(path).expanduser()
    if not configured.is_file():
        raise ValueError(
            f"Request file not found: {configured}. Pass --request, --request-file, or --example."
        )
    return configured.read_text(encoding="utf-8")


def load_requirements(
    *,
    inline_request: str | None = None,
    request_file: str | Path | None = None,
    example: str | None = None,
    settings: Settings | None = None,
) -> str:
    """Load a non-placeholder product request.

    Precedence (first match wins): ``--request`` > ``--example`` > ``--request-file`` >
    ``ENGINEERING_PROJECT_REQUEST`` > ``ENGINEERING_REQUEST_FILE`` > ``PROJECT_REQUEST.md``
    in the current working directory. Explicit command-line input always beats the
    environment, and a blank explicit value is an error rather than a silent fallback.
    """

    settings = settings or load_settings()
    environment_request = settings.request
    environment_file = settings.request_file
    default_file = Path.cwd() / DEFAULT_REQUEST_FILENAME

    if inline_request is not None:
        requirements = inline_request
    elif example is not None:
        requirements = load_example(example)
    elif request_file is not None:
        requirements = _read_request_file(request_file)
    elif environment_request is not None:
        requirements = environment_request
    elif environment_file is not None:
        requirements = _read_request_file(environment_file)
    elif default_file.is_file():
        requirements = default_file.read_text(encoding="utf-8")
    else:
        raise ValueError(
            "No project request found. Pass --request, --request-file, or "
            f"--example {DEFAULT_EXAMPLE} (see --help), or create {DEFAULT_REQUEST_FILENAME} "
            "in the current directory."
        )

    requirements = requirements.strip()
    if not requirements:
        raise ValueError("The project request is empty.")
    if TEMPLATE_MARKER in requirements:
        raise ValueError(
            "The project request is still a template. Replace it with concrete MVP "
            "requirements, or pass --request/--request-file/--example."
        )
    return requirements


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
        "--allow-web",
        action="store_true",
        help="Let the team use the web tools (search, fetch, package info). Off by default; "
        "see docs/SAFETY.md.",
    )
    parser.add_argument(
        "--config",
        help="Path to a config file (default: ./engineering-team.toml if present).",
    )
    return parser


def _cli_overrides(args: argparse.Namespace) -> dict[str, Any]:
    names: dict[str, Any] = {
        "web.enabled": True if args.allow_web else None,
        "provider": args.provider,
        "profile": args.profile,
        "project_name": args.project_name,
        "workspace_root": args.workspace_root,
    }
    return {key: value for key, value in names.items() if value is not None}


@dataclass
class PreparedRun:
    """A prepared workspace, its run context, crew inputs, and the lock held on the workspace."""

    ctx: RunContext
    inputs: dict[str, str]
    lock: WorkspaceLock
    recorder: RunRecorder

    def release(self) -> None:
        """Report what the run used and cost, then drop the workspace lock."""

        try:
            summary = self.recorder.manifest.summary
            if summary is not None and (summary.usage.calls or summary.tool_calls):
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
    reset: bool = False,
    force_reset: bool = False,
) -> PreparedRun:
    """Prepare the workspace, take its write lock, and build the run context and inputs.

    Also creates the run's manifest (``pending``); the caller wraps the work in
    ``recorder.running()`` and releases the lock (``_execute`` does) once the run is over. A
    workspace that another run holds raises ``WorkspaceBusy``, which the CLI reports as a
    usage error.
    """

    workspace = _prepare_workspace(settings, reset=reset, force_reset=force_reset)
    run_id = new_run_id()
    lock = WorkspaceLock(workspace.root).acquire(run_id)
    try:
        ctx = RunContext.create(settings, workspace, run_id=run_id)
        recorder = RunRecorder.begin(ctx, mode=mode, request=requirements or "")
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
    return PreparedRun(ctx=ctx, inputs=inputs, lock=lock, recorder=recorder)


def _prepare_from_args(args: argparse.Namespace, mode: str = "build") -> PreparedRun:
    if args.adopt:
        raise ValueError("--adopt is not implemented yet.")
    settings = load_settings(overrides=_cli_overrides(args), config_file=args.config)
    requirements = load_requirements(
        inline_request=args.request,
        request_file=args.request_file,
        example=args.example,
        settings=settings,
    )
    settings = settings.for_request(requirements)
    settings.check_ready(require_credentials=not getattr(args, "prepare_only", False))
    return _open_run(
        settings,
        mode="prepare" if getattr(args, "prepare_only", False) else mode,
        requirements=requirements,
        reset=args.reset,
        force_reset=args.force_reset,
    )


def _execute(
    prepare: Callable[[], T],
    work: Callable[[T], object],
    release: Callable[[T], None] | None = None,
) -> int:
    """Run an entry point in two phases and map failures to exit codes.

    ``prepare`` validates input and sets up the workspace; a ``ValueError`` there is a usage
    or configuration problem (exit 2, one line). ``work`` calls the crew; any failure there
    is a runtime failure (exit 1, with traceback). ``release`` always runs after a successful
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
        work(prepared)
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
    return 0


def _config_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="engineering-team config",
        description="Inspect the effective configuration.",
    )
    parser.add_argument("action", choices=["show"], help="Print every setting with its source.")
    parser.add_argument("--provider", choices=list(PROVIDERS))
    parser.add_argument("--profile", choices=list(PROFILE_NAMES))
    parser.add_argument("--config", help="Path to a config file.")
    parser.add_argument("--json", action="store_true", help="Machine-readable output.")
    return parser


def _config_command(argv: Sequence[str]) -> int:
    args = _config_parser().parse_args(argv)

    def prepare() -> Settings:
        overrides = {k: v for k, v in (("provider", args.provider), ("profile", args.profile)) if v}
        return load_settings(overrides=overrides, config_file=args.config)

    def work(settings: Settings) -> None:
        rows = settings.describe()
        if args.json:
            print(json.dumps([row.__dict__ for row in rows], indent=2))
            return
        width = max(len(row.key) for row in rows)
        value_width = min(max(len(row.value) for row in rows), 70)
        for row in rows:
            print(f"{row.key:<{width}}  {row.value:<{value_width}}  [{row.source}]")
        for note in [*settings.sdk_problems(), *settings.missing_credentials()]:
            print(f"Note: {note}.", file=sys.stderr)

    return _execute(prepare, work)


def run(argv: Sequence[str] | None = None) -> int:
    """Build or resume an MVP from command-line inputs."""

    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == "config":
        return _config_command(arguments[1:])
    args = _run_parser().parse_args(arguments)

    def work(prepared: PreparedRun) -> None:
        workspace = prepared.ctx.workspace
        with prepared.recorder.running():
            if args.prepare_only:
                print(f"Prepared project workspace: {workspace.root}")
                return
            EngineeringTeam(prepared.ctx).crew().kickoff(inputs=prepared.inputs)
            print(f"\nProject workspace: {workspace.root}")

    return _execute(lambda: _prepare_from_args(args), work, PreparedRun.release)


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


def replay() -> int:
    """Replay the latest crew run from a task ID."""

    def prepare() -> tuple[str, PreparedRun]:
        if len(sys.argv) < 2:
            raise ValueError("Usage: replay <task-id>")
        settings = load_settings()
        settings.check_ready(require_credentials=True)
        return sys.argv[1], _open_run(settings, mode="replay")

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

    def work(prepared: PreparedRun) -> None:
        with prepared.recorder.running():
            EngineeringTeam(prepared.ctx).crew().kickoff(inputs=prepared.inputs)

    return _execute(prepare, work, PreparedRun.release)


if __name__ == "__main__":
    sys.exit(run())
