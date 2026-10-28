"""Where a run's request comes from: the bundled examples, files, stdin, and the environment."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from importlib import resources
from pathlib import Path
from typing import TextIO

from engineering_team.intake import RequestBundle
from engineering_team.settings import (
    Settings,
    load_settings,
)
from engineering_team.tools import ProjectWorkspace

DEFAULT_REQUEST_FILENAME = "PROJECT_REQUEST.md"
DEFAULT_EXAMPLE = "tiny-notes"


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
