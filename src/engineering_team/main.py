#!/usr/bin/env python
"""Command-line and CrewAI entry points.

Every entry point returns a process exit code: ``0`` success (a verifying run: verified),
``2`` usage or configuration error (one-line message, no traceback), ``1`` runtime failure,
``3`` the controller's checks failed (not verified), ``4`` verification was partial (a required
check could not run) or ``fix`` could not reproduce the bug and needs more information, ``130``
interrupted.
"""

from __future__ import annotations

import sys
import traceback
import warnings
from collections.abc import Callable, Sequence
from typing import TypeVar

from engineering_team.api import (
    _prepare_resume,  # noqa: F401  (the CLI reaches these as ``engine._x``)
    resume,
)
from engineering_team.intake import TEMPLATE_MARKER
from engineering_team.outcome import (
    EXIT_FAILURE,
    EXIT_INTERRUPTED,
    EXIT_USAGE,
    exit_code_for,
    failure_line,
)
from engineering_team.request_sources import (
    available_examples,
    build_inputs,
    load_bundle,
    load_example,
    load_requirements,
)
from engineering_team.run_prep import (
    PreparedRun,
    _cli_overrides,  # noqa: F401
    _open_run,  # noqa: F401
    _prepare_from_args,  # noqa: F401
    _run_parser,  # noqa: F401
)
from engineering_team.runtime.budget import BudgetExceeded
from engineering_team.settings import SMOKE_PROFILE_MARKER
from engineering_team.workspaces import prepare_workspace, slugify_project_name

warnings.filterwarnings("ignore", category=SyntaxWarning, module="pysbd")

warnings.filterwarnings("ignore", category=SyntaxWarning, module="pysbd")


__all__ = [
    "SMOKE_PROFILE_MARKER",
    "TEMPLATE_MARKER",
    "PreparedRun",
    "available_examples",
    "build_inputs",
    "exit_code_for",
    "failure_line",
    "load_bundle",
    "load_example",
    "load_requirements",
    "prepare_workspace",
    "resume",
    "run",
    "run_crew",
    "slugify_project_name",
]


T = TypeVar("T")


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

    The 0.1.0 form without a command (``engineering-team --request-file FILE``) is gone: it
    prints a pointer to ``new`` and exits 2. See :mod:`engineering_team.cli.app`.
    """

    from engineering_team.cli.app import main

    return main(list(sys.argv[1:] if argv is None else argv))


def run_crew() -> int:
    """The ``run_crew`` script that ``crewai run`` calls: ``engineering-team new`` with the
    command-line options (the request comes from ``PROJECT_REQUEST.md`` when none is given)."""

    from engineering_team.cli.app import main

    return main(["new", *sys.argv[1:]])
