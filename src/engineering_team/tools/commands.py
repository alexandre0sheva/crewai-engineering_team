"""Validating project commands, then running them through the execution backend.

Commands can still execute code written inside the project (package scripts, tests), so this
is an allowlist plus argument checks, not isolation. Use an OS or container sandbox when the
requirements or dependencies are untrusted. Spawning, timeouts, and output capture belong to
the :class:`~engineering_team.execution.backend.ExecutionBackend`.
"""

from __future__ import annotations

import os
import shlex
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path

from engineering_team.execution.backend import (
    CommandRecord,
    CommandSpec,
    ExecutionBackend,
    has_executable,
)
from engineering_team.tools.workspace import CONTROLLER_DIRECTORY, ProjectWorkspace, WorkspaceError

MAX_COMMAND_TIMEOUT = 300

DEFAULT_COMMAND_ALLOWLIST = {
    "bun",
    "cargo",
    "composer",
    "deno",
    "dotnet",
    "go",
    "gradle",
    "gradlew",
    "java",
    "javac",
    "make",
    "mvn",
    "mvnw",
    "mypy",
    "node",
    "npm",
    "npx",
    "php",
    "pnpm",
    "pytest",
    "python",
    "python3",
    "rails",
    "ruby",
    "ruff",
    "rustc",
    "swift",
    "swiftc",
    "uv",
    "yarn",
}

SHELL_CONTROL_TOKENS = {"&&", "||", "|", ";", ">", ">>", "<", "2>", "2>>"}
BLOCKED_INLINE_EXECUTION = {
    "node": {"-e", "--eval", "-p", "--print"},
    "python": {"-c"},
    "python3": {"-c"},
    "ruby": {"-e"},
}

SAFE_ENVIRONMENT_NAMES = frozenset(
    {
        "LANG",
        "LC_ALL",
        "NO_PROXY",
        "PATH",
        "SSL_CERT_DIR",
        "SSL_CERT_FILE",
        "SYSTEMROOT",
        "TMPDIR",
        "http_proxy",
        "https_proxy",
        "no_proxy",
    }
)


def prepare_command(
    workspace: ProjectWorkspace,
    command: str,
    relative_cwd: str = ".",
    timeout_seconds: int = 120,
    *,
    max_timeout: int = MAX_COMMAND_TIMEOUT,
    backend: ExecutionBackend | None = None,
) -> CommandSpec:
    """Validate ``command`` (allowlist, no shell, no inline code, no outside paths).

    ``max_timeout`` caps ``timeout_seconds`` (the developer tools allow longer runs). With a
    ``backend`` that resolves programs itself (the Docker sandbox), the executable is not
    looked up on the host's ``PATH``.

    Returns the spec to run, or raises :class:`WorkspaceError` with a message saying how to
    fix the call.
    """

    if not command.strip():
        raise WorkspaceError("Command cannot be empty.")
    if "\n" in command or "\r" in command:
        raise WorkspaceError("Run one command at a time without embedded newlines.")

    try:
        arguments = shlex.split(command, posix=os.name != "nt")
    except ValueError as exc:
        raise WorkspaceError(f"Invalid command syntax: {exc}") from exc
    if not arguments:
        raise WorkspaceError("Command cannot be empty.")
    if any(token in SHELL_CONTROL_TOKENS for token in arguments):
        raise WorkspaceError(
            "Shell operators are disabled. Run commands separately or write a project script."
        )

    executable = Path(arguments[0]).name.removesuffix(".exe").lower()
    allowed = DEFAULT_COMMAND_ALLOWLIST | workspace.extra_commands
    if executable not in allowed:
        raise WorkspaceError(
            f"Command '{executable}' is not allowed. Add it explicitly with "
            "command_allowlist (ENGINEERING_COMMAND_ALLOWLIST) if this project requires it."
        )

    blocked_flags = BLOCKED_INLINE_EXECUTION.get(executable, set())
    if any(argument in blocked_flags for argument in arguments[1:]):
        raise WorkspaceError(
            "Inline code execution is disabled. Write the code inside the workspace, "
            "then run that file."
        )

    cwd = workspace.resolve(relative_cwd, must_exist=True)
    if not cwd.is_dir():
        raise WorkspaceError(f"Command working directory is not a directory: {relative_cwd}")
    _reject_external_path_arguments(workspace, arguments[1:])

    timeout = max(1, min(int(timeout_seconds), max_timeout))
    if not has_executable(backend, arguments[0]) and not (
        arguments[0].startswith("./") and (cwd / arguments[0]).is_file()
    ):
        raise WorkspaceError(f"Executable not found: {arguments[0]}")

    return CommandSpec(
        argv=tuple(arguments),
        cwd=cwd,
        env=command_environment(workspace),
        timeout=float(timeout),
        label=executable,
    )


def run_command(
    workspace: ProjectWorkspace,
    command: str,
    relative_cwd: str = ".",
    timeout_seconds: int = 120,
    *,
    backend: ExecutionBackend,
    gate: AbstractContextManager[object] | None = None,
) -> str:
    """Validate and run one command through ``backend``, returning a compact report.

    ``gate`` (normally the run's ``command_gate`` semaphore) is held only while the command
    runs, so validation errors never wait for a slot.
    """

    spec = prepare_command(workspace, command, relative_cwd, timeout_seconds, backend=backend)
    with gate if gate is not None else nullcontext():
        record = backend.run(spec)
    return format_record(workspace, record)


def format_record(workspace: ProjectWorkspace, record: CommandRecord) -> str:
    """Exit status and duration first, then the output window, then where the full log is."""

    if record.timed_out:
        status = f"Command timed out after {record.duration:.0f}s and its process group was killed."
    elif record.cancelled:
        status = "Command was cancelled because the run was cancelled."
    else:
        status = f"Exit code: {record.exit_code} ({record.duration:.1f}s)"
    scope = "head and tail of the output" if record.truncated else "output (stdout and stderr)"
    try:
        log = record.log_path.relative_to(workspace.root).as_posix()
    except ValueError:
        log = str(record.log_path)
    hint = " (Read File Range can page through it)" if record.truncated else ""
    return f"{status}\n{scope}:\n{record.output_tail or '(empty)'}\nfull log: {log}{hint}"


def _reject_external_path_arguments(workspace: ProjectWorkspace, arguments: list[str]) -> None:
    for argument in arguments:
        candidate = argument.split("=", 1)[-1] if "=" in argument else argument
        if candidate.startswith(("http://", "https://")):
            continue
        path = Path(candidate).expanduser()
        if not path.is_absolute():
            continue
        try:
            relative = path.resolve(strict=False).relative_to(workspace.root)
        except ValueError as exc:
            raise WorkspaceError(
                f"Absolute command argument leaves the project workspace: {candidate}"
            ) from exc
        workspace.reject_protected(relative.parts)


def command_environment(workspace: ProjectWorkspace) -> dict[str, str]:
    """The environment project commands get: a small safe subset plus an isolated home."""

    environment = {
        name: value
        for name, value in os.environ.items()
        if name in SAFE_ENVIRONMENT_NAMES or name in workspace.env_passthrough
    }

    state = workspace.root / CONTROLLER_DIRECTORY
    tool_home, cache_root, temp_root = state / "tool-home", state / "cache", state / "tmp"
    for directory in (tool_home, cache_root, temp_root):
        directory.mkdir(parents=True, exist_ok=True)

    environment.update(
        {
            "HOME": str(tool_home),
            "TMPDIR": str(temp_root),
            # An edit and a re-run within the same second would otherwise load stale bytecode
            # (the cache is keyed on mtime in whole seconds and the size).
            "PYTHONDONTWRITEBYTECODE": "1",
            "UV_CACHE_DIR": str(cache_root / "uv"),
            "PIP_CACHE_DIR": str(cache_root / "pip"),
            "npm_config_cache": str(cache_root / "npm"),
        }
    )
    return environment
