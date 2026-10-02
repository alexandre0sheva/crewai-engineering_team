"""Shared plumbing for the developer-tool runners: locating the project, running a command."""

from __future__ import annotations

import re
import shlex
import threading
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

from engineering_team.devtools.availability import install_hint, missing_tool
from engineering_team.devtools.detect import Stack, detect_stack, find_stacks
from engineering_team.devtools.models import DevReport, TestFailure
from engineering_team.devtools.plans import Paths, Selection
from engineering_team.execution.backend import CommandRecord
from engineering_team.tools.commands import prepare_command
from engineering_team.tools.support import CANCELLED, ToolError
from engineering_team.tools.workspace import CONTROLLER_DIRECTORY, ProjectWorkspace, WorkspaceError

if TYPE_CHECKING:
    from engineering_team.runtime.context import RunContext

R = TypeVar("R", bound=DevReport)

# Tools the dev tools run that the project-wide allowlist does not cover. The agent supplies
# paths and names, never flags, so these are fixed invocations; ``tools.dev.extra_executables``
# adds more.
DEV_EXECUTABLES = frozenset(
    {
        "black", "bundle", "coverage", "eslint", "gofmt", "golangci-lint", "jest", "mvnw",
        "phpcs", "phpunit", "pip-audit", "pipenv", "poetry", "prettier", "pyright", "rake",
        "rspec", "rubocop", "rustfmt", "tsc", "vitest",
    }
)  # fmt: skip
SCRATCH_PATH = re.compile(r"\S*\.engineering-team/tmp/devtools/\S+")
MAX_LOG_CHARS = 3_000_000
TAIL_CHARS = 1_500


@dataclass
class Execution:
    """One command that ran (or could not start because its tool is not installed)."""

    argv: list[str]
    command: str
    record: CommandRecord | None
    text: str  # the combined output, up to MAX_LOG_CHARS
    log: str | None  # the full log's path relative to the project root
    tool_missing: str | None = None

    @property
    def tail(self) -> str:
        return self.text[-TAIL_CHARS:]

    @property
    def exit_code(self) -> int | None:
        return self.record.exit_code if self.record else None


def read_log(record: CommandRecord) -> str:
    try:
        with record.log_path.open(encoding="utf-8", errors="replace") as handle:
            return handle.read(MAX_LOG_CHARS)
    except OSError:
        return record.output_tail


class BaseRunner:
    """Resolves projects and runs commands for one run; subclasses add the tools."""

    def __init__(self, ctx: RunContext) -> None:
        self.ctx = ctx
        self.cfg = ctx.settings.tools.dev
        extra = {name.strip().lower() for name in self.cfg.extra_executables if name.strip()}
        self.workspace: ProjectWorkspace = replace(
            ctx.workspace, extra_commands=ctx.workspace.extra_commands | DEV_EXECUTABLES | extra
        )
        self._counter = 0
        self._lock = threading.Lock()

    # -- locating the project ----------------------------------------------------------

    def resolve(self, working_directory: str) -> tuple[Path, Stack]:
        """The directory and its detected stack, or a :class:`ToolError` saying where to look."""

        ws = self.ctx.workspace
        directory = ws.resolve(working_directory, must_exist=True)
        if not directory.is_dir():
            raise ToolError(f"{working_directory} is a file; pass the project directory.")
        relative = ws.relative_name(directory)
        stack = detect_stack(directory, relative)
        if stack is not None:
            return directory, stack
        others = find_stacks(ws.root)
        if others:
            listing = "; ".join(f"{s.directory} ({s.describe()})" for s in others[:6])
            raise ToolError(
                f"No project files in {relative!r}. Projects found: {listing}. "
                "Pass working_directory=<one of them>."
            )
        raise ToolError(
            "No project detected: found no pyproject.toml, requirements.txt, package.json, "
            "go.mod, Cargo.toml, pom.xml, build.gradle, *.csproj, Gemfile, or composer.json. "
            "Create the project files first, or use Run Project Command."
        )

    def checked_paths(self, directory: Path, paths: Sequence[str]) -> list[str]:
        """Paths relative to ``directory`` that exist inside the project and are not flags."""

        ws = self.ctx.workspace
        base = ws.relative_name(directory)
        checked = []
        for path in (p.strip() for p in paths):
            if not path:
                continue
            if path.startswith("-"):
                raise ToolError(f"Path {path!r} looks like a command-line flag; pass a file.")
            ws.resolve(f"{base}/{path}", must_exist=True)
            checked.append(path)
        return checked

    def selection(
        self,
        directory: Path,
        paths: Sequence[str] = (),
        *,
        filter: str = "",
        markers: str = "",
        fail_fast: bool = False,
        failures: Sequence[TestFailure] = (),
    ) -> Selection:
        """Validate agent-supplied names: paths stay in the project, nothing looks like a flag."""

        for label, value in (("filter", filter), ("markers", markers)):
            if value.strip().startswith("-"):
                raise ToolError(f"The {label} {value!r} must not start with '-'.")
        return Selection(
            tuple(self.checked_paths(directory, paths)),
            filter.strip(),
            markers.strip(),
            fail_fast,
            tuple(failures),
        )

    # -- running -----------------------------------------------------------------------

    def scratch(self) -> Paths:
        """A fresh directory under the project's scratch space for a tool's report files."""

        with self._lock:
            self._counter += 1
            name = f"{self.ctx.run_id}-{self._counter}"
        directory = self.ctx.workspace.root / CONTROLLER_DIRECTORY / "tmp" / "devtools" / name
        directory.mkdir(parents=True, exist_ok=True)
        return Paths(report=directory / "report", directory=directory)

    def timeout(self, configured: int, requested: int | None) -> int:
        """A call may ask for less time than configured, never more."""

        return max(1, min(requested, configured)) if requested else configured

    def execute(
        self, argv: Sequence[str], directory: Path, timeout: int, *, network: bool = False
    ) -> Execution:
        """Run one command through the backend. A missing tool comes back as ``tool_missing``."""

        command = shlex.join(argv)
        ws = self.workspace
        try:
            spec = prepare_command(
                ws, command, ws.relative_name(directory), timeout, max_timeout=timeout
            )
        except WorkspaceError as exc:
            if str(exc).startswith("Executable not found"):
                return Execution(list(argv), command, None, "", None, tool_missing=argv[0])
            raise
        if network:
            spec = replace(spec, network=True)
        with self.ctx.command_gate:
            record = self.ctx.backend.run(spec)
        if record.cancelled:
            raise ToolError(CANCELLED.removeprefix("ERROR: "))
        text = read_log(record)
        try:
            log = record.log_path.relative_to(ws.root).as_posix()
        except ValueError:
            log = str(record.log_path)
        return Execution(
            list(argv), command, record, text, log, missing_tool(argv, text, record.exit_code)
        )

    def finish(self, report: R, ex: Execution, stack: Stack | None = None) -> R:
        """Fill the fields every report shares. A tool that is not installed is ``unavailable``
        and a timeout is an ``error``; neither can look like a pass."""

        update: dict[str, Any] = {"command": self.display(ex.command), "log_path": ex.log}
        record = ex.record
        if record is not None:
            update.update(exit_code=record.exit_code, duration=round(record.duration, 2))
            if record.timed_out:
                update.update(
                    status="error",
                    hint=(
                        f"Timed out after {record.duration:.0f}s; narrow the selection or "
                        "raise the matching tools.dev.*_timeout."
                    ),
                )
        if ex.tool_missing:
            update.update(status="unavailable", hint=install_hint(ex.tool_missing, stack))
        return report.model_copy(update=update)

    def display(self, command: str) -> str:
        """The command as an agent should see it: project-relative, scratch report paths named."""

        shown = command.replace(str(self.ctx.workspace.root) + "/", "")
        return SCRATCH_PATH.sub("<report>", shown)

    @staticmethod
    def under(directory: str, path: str | None) -> str | None:
        """``path`` (relative to a tool's working directory) relative to the project root."""

        if not path or directory == "." or path.startswith("/"):
            return path
        return f"{directory}/{path}"
