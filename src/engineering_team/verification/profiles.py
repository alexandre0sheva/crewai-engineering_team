"""Which checks a verification runs.

Precedence, per kind of check (setup, tests, lint, type check, build, smoke): the user's
checks (``--checks FILE``) > the commands the architect's plan declares > what is detected from
the project's own files (``devtools.detect``, the one stack detector). Custom user checks are
added to the rest. A plan command the controller may not run (it is not on the allowlist, or
needs a shell) is dropped with a note, and the detected default is used instead.

A required ``tests`` check always exists, even for a project with nothing to run, so an empty
or untestable project can never look verified.
"""

from __future__ import annotations

import shlex
from collections.abc import Sequence
from dataclasses import dataclass, field

from engineering_team.contracts import CheckKind, CheckSpec, Plan
from engineering_team.devtools.detect import Stack, find_stacks
from engineering_team.devtools.runner import DevRunner
from engineering_team.runtime.context import RunContext
from engineering_team.tools.commands import prepare_command
from engineering_team.tools.workspace import WorkspaceError

KIND_ORDER: tuple[CheckKind, ...] = ("setup", "test", "lint", "typecheck", "build", "smoke")
BASE_IDS: dict[CheckKind, str] = {
    "setup": "setup",
    "test": "tests",
    "lint": "lint",
    "typecheck": "typecheck",
    "build": "build",
    "smoke": "smoke",
}
LABELS: dict[CheckKind, str] = {
    "setup": "setup",
    "test": "tests",
    "lint": "lint",
    "typecheck": "type check",
    "build": "build",
    "smoke": "run",
}


@dataclass
class ChecksPlan:
    """The checks to run, in order, and what was decided on the way (shown in the report)."""

    checks: list[CheckSpec] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _check_id(kind: CheckKind, directory: str = ".", index: int = 0) -> str:
    check_id = BASE_IDS[kind]
    if directory != ".":
        check_id += f":{directory}"
    return f"{check_id}:{index + 1}" if index else check_id


def build_checks(ctx: RunContext, plan: Plan | None, user: Sequence[CheckSpec]) -> ChecksPlan:
    """The checks for ``ctx``'s workspace (see the module docstring)."""

    verify = ctx.settings.verify
    dev = ctx.settings.tools.dev
    workspace = DevRunner(ctx).workspace
    result = ChecksPlan()
    stacks = project_roots(find_stacks(ctx.workspace.root))
    commands = plan.commands if plan is not None else None
    declared: dict[CheckKind, list[str]] = (
        {
            "setup": commands.setup,
            "test": commands.test,
            "lint": commands.lint,
            "build": commands.build,
            "smoke": commands.run if verify.smoke else [],
        }
        if commands is not None
        else {}
    )
    required: dict[CheckKind, bool] = {
        "setup": False,
        "test": True,
        "lint": verify.static_required,
        "typecheck": verify.static_required,
        "build": True,
        "smoke": verify.smoke_required,
    }
    timeouts: dict[CheckKind, int] = {
        "setup": dev.install_timeout,
        "test": dev.test_timeout,
        "lint": dev.lint_timeout,
        "typecheck": dev.typecheck_timeout,
        "build": dev.build_timeout,
        "smoke": verify.smoke_seconds,
    }

    def detected(kind: CheckKind, stack: Stack | None) -> CheckSpec:
        directory = stack.directory if stack else "."
        return CheckSpec(
            id=_check_id(kind, directory),
            name=f"{LABELS[kind].capitalize()}" + (f" ({directory})" if directory != "." else ""),
            required=required[kind],
            timeout=float(timeouts[kind]),
            kind=kind,
            source="detected",
            cwd=directory,
        )

    def from_plan(kind: CheckKind) -> list[CheckSpec]:
        found: list[CheckSpec] = []
        for line in declared.get(kind, []):
            reason = _refusal(workspace, line)
            if reason is not None:
                result.notes.append(
                    f"The plan's {LABELS[kind]} command `{line}` cannot be run by the controller "
                    f"({reason}); the detected default is used instead."
                )
                continue
            found.append(
                CheckSpec(
                    id=_check_id(kind, index=len(found)),
                    name=f"{LABELS[kind].capitalize()}: {line}"[:80],
                    argv=shlex.split(line),
                    required=required[kind],
                    timeout=float(verify.timeout if kind != "smoke" else verify.smoke_seconds),
                    kind=kind,
                    source="plan",
                )
            )
        return found

    for kind in KIND_ORDER:
        mine = [check for check in user if check.kind == kind]
        if mine:
            result.checks.extend(mine)
            continue
        if planned := from_plan(kind):
            result.checks.extend(planned)
            continue
        if kind == "test":  # a project with no stack still gets one (it will fail, not pass)
            projects: list[Stack | None] = [*stacks] or [None]
            result.checks.extend(detected(kind, stack) for stack in projects)
        elif kind in ("lint", "typecheck", "build"):
            result.checks.extend(
                detected(kind, stack) for stack in stacks if getattr(stack, kind) is not None
            )
    result.checks.extend(check for check in user if check.kind == "custom")
    return result


def project_roots(stacks: list[Stack]) -> list[Stack]:
    """The project roots: a directory of the same language inside another project (a ``tests/``
    or ``src/`` folder of loose Python files) is part of that project, not a second one."""

    roots: list[Stack] = []
    for stack in stacks:  # path order: parents come before their children
        inside = any(
            root.language == stack.language
            and (root.directory == "." or stack.directory.startswith(root.directory + "/"))
            and stack.directory != root.directory
            for root in roots
        )
        if not inside:
            roots.append(stack)
    return roots


def _refusal(workspace: object, line: str) -> str | None:
    """Why the controller may not run ``line``; ``None`` when it may (or only lacks the tool)."""

    try:
        prepare_command(workspace, line)  # type: ignore[arg-type]
    except WorkspaceError as exc:
        message = str(exc)
        return None if message.startswith("Executable not found") else message.rstrip(".")
    return None
