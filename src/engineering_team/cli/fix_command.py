"""``fix``: fix a bug in an existing project, red then green.

Like ``feature`` it never works in your checkout blindly (a branch, worktree, or copy; see
``cli/repo_mode.py``), and it records ``CHANGE_SUMMARY.md`` and ``changes.patch`` in the run
directory. What is specific here is the input (a bug report, a stack trace, a command that shows
the bug) and the exit code 4 for a bug the team could not reproduce: it stops with questions
instead of fixing blind, unless ``--allow-unreproduced``.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Annotated, Any

import typer

from engineering_team.cli.context import fail, one_of
from engineering_team.cli.context import get as get_globals
from engineering_team.cli.repo_mode import RepoRun, run_repository_mode
from engineering_team.intake.bundle import STDIN, RequestBundle
from engineering_team.intake.errors import IntakeError
from engineering_team.model_routing import PROFILE_NAMES, PROVIDERS
from engineering_team.modes.fix_input import FixInput, compose_request, write_fix_input
from engineering_team.pipeline.state import PipelineState
from engineering_team.settings import Settings

MAX_TRACE_BYTES = 2_000_000


def _read_trace(path: str) -> str:
    target = Path(path).expanduser()
    try:
        with target.open("rb") as handle:
            data = handle.read(MAX_TRACE_BYTES + 1)
    except OSError as exc:
        fail(f"Cannot read the trace file {target}: {exc}")
    if len(data) > MAX_TRACE_BYTES:
        fail(f"{target} is larger than {MAX_TRACE_BYTES:,} bytes; pass the part with the error.")
    text = data.decode("utf-8", errors="replace").replace("\x00", "").strip()
    if not text:
        fail(f"The trace file {target} is empty.")
    return text


def fix(
    ctx: typer.Context,
    repo: Annotated[str, typer.Option(help="The project directory (default: here).")] = ".",
    request: Annotated[
        str | None, typer.Option(help="The bug report or issue text, inline ('-' reads stdin).")
    ] = None,
    request_file: Annotated[
        list[str] | None,
        typer.Option(help="Markdown or text file with the bug report or issue; repeat to merge."),
    ] = None,
    trace_file: Annotated[
        str | None,
        typer.Option(help="A stack trace or log that shows the error (Python, Node, or Java)."),
    ] = None,
    repro: Annotated[
        str | None,
        typer.Option(help="A command that shows the bug, e.g. 'python -m app.main add x'."),
    ] = None,
    allow_unreproduced: Annotated[
        bool,
        typer.Option(
            help="Fix even when the team cannot make a failing reproduction (nothing then "
            "proves the bug is gone)."
        ),
    ] = False,
    context_dir: Annotated[
        str | None,
        typer.Option(help="Directory of reference documents the team may read (copied in)."),
    ] = None,
    worktree: Annotated[
        bool,
        typer.Option(help="Work in a separate worktree (the default when the tree is dirty)."),
    ] = False,
    allow_dirty: Annotated[
        bool,
        typer.Option(
            help="Work in your checkout, on a new branch, even with uncommitted changes "
            "(they become part of the team's first commit)."
        ),
    ] = False,
    squash: Annotated[
        bool, typer.Option(help="Make the team's commits one commit when the run succeeds.")
    ] = False,
    interactive: Annotated[
        bool | None,
        typer.Option(
            "--interactive/--non-interactive",
            help="Let the team ask you questions (needs a terminal; default: no).",
        ),
    ] = None,
    project_name: Annotated[
        str | None,
        typer.Option(help="Name of the run's project, and of the copy for a non-Git directory."),
    ] = None,
    provider: Annotated[str | None, typer.Option(callback=one_of(list(PROVIDERS)))] = None,
    profile: Annotated[str | None, typer.Option(callback=one_of(list(PROFILE_NAMES)))] = None,
    allow_web: Annotated[bool, typer.Option(help="Let the team use the web tools.")] = False,
    checks: Annotated[
        str | None, typer.Option(help="YAML file of your own checks (kept outside the project).")
    ] = None,
    sandbox: Annotated[
        str | None,
        typer.Option(callback=one_of(["local", "docker"]), help="Where commands run."),
    ] = None,
    config: Annotated[str | None, typer.Option(help="Path to a config file.")] = None,
) -> None:
    """Fix a bug: reproduce it (red), fix it, and prove it is gone (green) on a branch or copy."""

    from engineering_team import main as engine

    g = get_globals(ctx)
    files = [*(request_file or [])]
    if request == STDIN:
        request, files = None, [STDIN, *files]
    if repro is not None and not repro.strip():
        fail("--repro is empty: give the command that shows the bug.")
    trace = _read_trace(trace_file) if trace_file is not None else None
    given = FixInput(
        repro=repro.strip() if repro else None, trace=trace, allow_unreproduced=allow_unreproduced
    )
    options = SimpleNamespace(
        provider=provider, profile=profile, allow_web=allow_web, checks=checks, sandbox=sandbox,
        verbose=g.verbose, workspace_root=g.workspace_root,
    )  # fmt: skip

    def load(settings: Settings) -> RequestBundle:
        has_report = request is not None or bool(files)
        if not (has_report or trace or given.repro):
            raise IntakeError(
                "Nothing to fix: pass the bug report (--request or --request-file), a "
                "--trace-file, or a --repro command."
            )
        report = (
            engine.load_bundle(inline_request=request, request_files=files, settings=settings)
            if has_report
            else None
        )
        text = compose_request(report.text if report else None, trace=trace, repro=given.repro)
        return RequestBundle.from_sources(
            text=text,
            context_dir=context_dir,
            limits=settings.intake,
        )

    def opened(prepared: Any) -> None:
        write_fix_input(prepared.ctx.run_dir, given)

    def extra(prepared: Any, data: dict[str, Any]) -> None:
        state = PipelineState.load(prepared.ctx.run_dir)
        record = state.fix if state else None
        data["fix"] = (
            {
                "reproduced": record.reproduced,
                "command": record.command or None,
                "files": record.files,
                "red": record.red.model_dump(mode="json") if record.red else None,
                "green": record.green.model_dump(mode="json") if record.green else None,
            }
            if record is not None
            else None
        )

    run_repository_mode(
        g,
        RepoRun(
            mode="fix",
            root=Path(repo).expanduser().resolve(),
            load=load,
            options=options,
            worktree=worktree,
            allow_dirty=allow_dirty,
            squash=squash,
            interactive=interactive,
            stdin_used=STDIN in files,
            project_name=project_name,
            config=config,
            slug_fallback="fix",
            opened=opened,
            extra=extra,
        ),
    )
