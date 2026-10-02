"""Persistent, project-scoped filesystem and command tools.

This is a safety boundary, not a virtual machine. File APIs reject traversal and
symlink escapes, and commands run without a shell from the active project root.
Commands can still execute code written inside that project, so use an OS or
container sandbox as an additional layer when running untrusted requirements.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from crewai.tools import BaseTool, tool

MAX_READ_BYTES = 250_000
MAX_WRITE_BYTES = 1_000_000
MAX_COMMAND_OUTPUT = 40_000
MAX_LIST_ENTRIES = 500
MAX_COMMAND_TIMEOUT = 300

GIT_DIRECTORY = ".git"
CONTROLLER_DIRECTORY = ".engineering-team"
# The only part of the controller directory agents may touch: scratch space that project
# commands already use as TMPDIR.
AGENT_SCRATCH_PARTS = (CONTROLLER_DIRECTORY, "tmp")

IGNORED_LIST_DIRECTORIES = {
    ".git",
    CONTROLLER_DIRECTORY,
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "target",
}

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


class WorkspaceError(ValueError):
    """Raised when an operation would leave the configured project workspace."""


@dataclass(frozen=True)
class ProjectWorkspace:
    """A persistent filesystem root used by one generated application."""

    root: Path
    # Executables allowed in addition to the defaults, and environment variable names that
    # project commands may inherit. Both come from Settings; the workspace never reads the
    # process environment configuration itself.
    extra_commands: frozenset[str] = frozenset()
    env_passthrough: frozenset[str] = frozenset()

    @classmethod
    def create(
        cls,
        root: str | Path,
        *,
        extra_commands: Iterable[str] = (),
        env_passthrough: Iterable[str] = (),
    ) -> ProjectWorkspace:
        resolved = Path(root).expanduser().resolve()
        if resolved == Path(resolved.anchor):
            raise WorkspaceError("The filesystem root cannot be used as a project workspace.")
        resolved.mkdir(parents=True, exist_ok=True)
        return cls(
            root=resolved,
            extra_commands=frozenset(
                item.strip().lower() for item in extra_commands if item.strip()
            ),
            env_passthrough=frozenset(item.strip() for item in env_passthrough if item.strip()),
        )

    def resolve(self, relative_path: str, *, must_exist: bool = False) -> Path:
        if not isinstance(relative_path, str):
            raise WorkspaceError("Paths must be strings relative to the project workspace.")

        normalized = relative_path.strip() or "."
        supplied = Path(normalized)
        if supplied.is_absolute() or ".." in supplied.parts:
            raise WorkspaceError(f"Path must stay inside the project workspace: {relative_path}")
        self._reject_protected(supplied.parts)

        resolved = (self.root / supplied).resolve(strict=False)
        try:
            relative = resolved.relative_to(self.root)
        except ValueError as exc:
            raise WorkspaceError(
                f"Path resolves outside the project workspace: {relative_path}"
            ) from exc
        # Check again after symlink resolution: an alias such as ``docs -> .git`` passes the
        # lexical check above but still lands in protected storage.
        self._reject_protected(relative.parts)

        if must_exist and not resolved.exists():
            raise WorkspaceError(f"Path does not exist: {relative_path}")
        return resolved

    @staticmethod
    def _reject_protected(parts: tuple[str, ...]) -> None:
        """Reject paths in Git metadata or in the controller-owned state directory."""

        lowered = tuple(part.lower() for part in parts)  # case-insensitive file systems
        if GIT_DIRECTORY in lowered:
            raise WorkspaceError("Direct access to .git is not allowed.")
        if lowered and lowered[0] == CONTROLLER_DIRECTORY and lowered[:2] != AGENT_SCRATCH_PARTS:
            raise WorkspaceError(
                f"{CONTROLLER_DIRECTORY}/ is managed by the orchestrator and is not accessible."
            )

    def relative_name(self, path: Path) -> str:
        relative = path.relative_to(self.root)
        return "." if str(relative) == "." else relative.as_posix()

    def list_files(self, relative_path: str = ".", max_depth: int = 4) -> str:
        base = self.resolve(relative_path, must_exist=True)
        if not base.is_dir():
            raise WorkspaceError(f"Not a directory: {relative_path}")

        depth_limit = max(1, min(int(max_depth), 8))
        entries: list[str] = []
        base_depth = len(base.parts)

        for current_root, directory_names, file_names in os.walk(base):
            current = Path(current_root)
            depth = len(current.parts) - base_depth
            directory_names[:] = sorted(
                name
                for name in directory_names
                if name not in IGNORED_LIST_DIRECTORIES and depth < depth_limit
            )

            for directory_name in directory_names:
                entries.append(f"{self.relative_name(current / directory_name)}/")
            for file_name in sorted(file_names):
                entries.append(self.relative_name(current / file_name))

            if len(entries) >= MAX_LIST_ENTRIES:
                entries = entries[:MAX_LIST_ENTRIES]
                entries.append(
                    f"... output limited to {MAX_LIST_ENTRIES} entries; narrow the path."
                )
                break

        return "\n".join(entries) if entries else "The requested directory is empty."

    def read_file(self, relative_path: str) -> str:
        path = self.resolve(relative_path, must_exist=True)
        if not path.is_file():
            raise WorkspaceError(f"Not a file: {relative_path}")
        size = path.stat().st_size
        if size > MAX_READ_BYTES:
            raise WorkspaceError(f"File is {size} bytes; the read limit is {MAX_READ_BYTES} bytes.")

        content = path.read_bytes()
        if b"\x00" in content:
            raise WorkspaceError("Binary files cannot be read with this text tool.")
        return content.decode("utf-8")

    def write_file(self, relative_path: str, content: str) -> str:
        encoded = content.encode("utf-8")
        if len(encoded) > MAX_WRITE_BYTES:
            raise WorkspaceError(
                f"Content is {len(encoded)} bytes; the write limit is {MAX_WRITE_BYTES} bytes."
            )

        path = self.resolve(relative_path)
        if path.exists() and path.is_dir():
            raise WorkspaceError(f"Cannot replace a directory with a file: {relative_path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return f"Wrote {len(encoded)} bytes to {self.relative_name(path)}."

    def replace_in_file(
        self,
        relative_path: str,
        old_text: str,
        new_text: str,
        expected_replacements: int = 1,
    ) -> str:
        if not old_text:
            raise WorkspaceError("old_text cannot be empty.")
        expected = int(expected_replacements)
        if expected < 1:
            raise WorkspaceError("expected_replacements must be at least 1.")

        current = self.read_file(relative_path)
        actual = current.count(old_text)
        if actual != expected:
            raise WorkspaceError(
                f"Expected {expected} occurrence(s) in {relative_path}, found {actual}; "
                "read the file again before editing."
            )
        updated = current.replace(old_text, new_text)
        return self.write_file(relative_path, updated)

    def delete_path(self, relative_path: str) -> str:
        supplied = Path(relative_path.strip())
        if not supplied.parts or str(supplied) == ".":
            raise WorkspaceError("The project workspace root cannot be deleted.")
        if supplied.is_absolute() or ".." in supplied.parts:
            raise WorkspaceError("Only paths inside the project workspace can be deleted.")
        self._reject_protected(supplied.parts)

        lexical_path = self.root / supplied
        if lexical_path.is_symlink():
            # Removing a link must not follow it, but its *parent* may itself be an alias for
            # protected storage, so resolve the parent and check where the link really lives.
            parent = self.resolve(supplied.parent.as_posix(), must_exist=True)
            self._reject_protected(parent.relative_to(self.root).parts)
            lexical_path.unlink()
            return f"Deleted symlink {supplied.as_posix()}."

        path = self.resolve(relative_path, must_exist=True)
        if path.is_dir():
            shutil.rmtree(path)
            kind = "directory"
        else:
            path.unlink()
            kind = "file"
        return f"Deleted {kind} {supplied.as_posix()}."

    def run_command(
        self,
        command: str,
        relative_cwd: str = ".",
        timeout_seconds: int = 120,
    ) -> str:
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
        allowed = DEFAULT_COMMAND_ALLOWLIST | self.extra_commands
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

        cwd = self.resolve(relative_cwd, must_exist=True)
        if not cwd.is_dir():
            raise WorkspaceError(f"Command working directory is not a directory: {relative_cwd}")
        self._reject_external_path_arguments(arguments[1:])

        timeout = max(1, min(int(timeout_seconds), MAX_COMMAND_TIMEOUT))
        executable_path = shutil.which(arguments[0])
        if executable_path is None and not (
            arguments[0].startswith("./") and (cwd / arguments[0]).is_file()
        ):
            raise WorkspaceError(f"Executable not found: {arguments[0]}")

        try:
            result = subprocess.run(
                arguments,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=self._command_environment(),
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = _truncate(exc.stdout or "")
            stderr = _truncate(exc.stderr or "")
            return (
                f"Command timed out after {timeout} seconds.\n"
                f"stdout:\n{stdout or '(empty)'}\n"
                f"stderr:\n{stderr or '(empty)'}"
            )

        stdout = _truncate(result.stdout)
        stderr = _truncate(result.stderr)
        return (
            f"Exit code: {result.returncode}\n"
            f"stdout:\n{stdout or '(empty)'}\n"
            f"stderr:\n{stderr or '(empty)'}"
        )

    def _reject_external_path_arguments(self, arguments: list[str]) -> None:
        for argument in arguments:
            candidate = argument.split("=", 1)[-1] if "=" in argument else argument
            if candidate.startswith(("http://", "https://")):
                continue
            path = Path(candidate).expanduser()
            if not path.is_absolute():
                continue
            try:
                relative = path.resolve(strict=False).relative_to(self.root)
            except ValueError as exc:
                raise WorkspaceError(
                    f"Absolute command argument leaves the project workspace: {candidate}"
                ) from exc
            self._reject_protected(relative.parts)

    def _command_environment(self) -> dict[str, str]:
        safe_names = {
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
        extra_names = self.env_passthrough
        environment = {
            name: value
            for name, value in os.environ.items()
            if name in safe_names or name in extra_names
        }

        tool_home = self.root / ".engineering-team" / "tool-home"
        cache_root = self.root / ".engineering-team" / "cache"
        temp_root = self.root / ".engineering-team" / "tmp"
        for directory in (tool_home, cache_root, temp_root):
            directory.mkdir(parents=True, exist_ok=True)

        environment.update(
            {
                "HOME": str(tool_home),
                "TMPDIR": str(temp_root),
                "UV_CACHE_DIR": str(cache_root / "uv"),
                "PIP_CACHE_DIR": str(cache_root / "pip"),
                "npm_config_cache": str(cache_root / "npm"),
            }
        )
        return environment


_active_workspace: ProjectWorkspace | None = None


def configure_workspace(
    root: str | Path,
    *,
    extra_commands: Iterable[str] = (),
    env_passthrough: Iterable[str] = (),
) -> ProjectWorkspace:
    """Set the active generated-project directory for all agent tools."""

    global _active_workspace
    _active_workspace = ProjectWorkspace.create(
        root, extra_commands=extra_commands, env_passthrough=env_passthrough
    )
    return _active_workspace


def get_workspace() -> ProjectWorkspace:
    """Return the active workspace, failing clearly before an unconfigured run."""

    if _active_workspace is None:
        raise WorkspaceError(
            "No project workspace is configured. Start through engineering_team.main.run()."
        )
    return _active_workspace


def _tool_result(operation) -> str:
    try:
        return operation()
    except (OSError, UnicodeError, WorkspaceError) as exc:
        return f"ERROR: {exc}"


def _truncate(value: str | bytes, limit: int = MAX_COMMAND_OUTPUT) -> str:
    text = value.decode(errors="replace") if isinstance(value, bytes) else value
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n... output truncated at {limit} characters."


@tool("List Project Files")
def list_project_files(path: str = ".", max_depth: int = 4) -> str:
    """List the project tree below a relative path.

    Heavy dependency and cache directories are omitted. Use this before edits
    and after scaffolding so work is based on the actual project structure.
    """

    return _tool_result(lambda: get_workspace().list_files(path, max_depth))


@tool("Read Project File")
def read_project_file(path: str) -> str:
    """Read one UTF-8 text file using a path relative to the project root."""

    return _tool_result(lambda: get_workspace().read_file(path))


@tool("Write Project File")
def write_project_file(path: str, content: str) -> str:
    """Create or overwrite one UTF-8 text file under the project root.

    Parent directories are created automatically, so normal nested application
    structures such as src/, tests/, apps/, and packages/ are supported.
    """

    return _tool_result(lambda: get_workspace().write_file(path, content))


@tool("Replace In Project File")
def replace_in_project_file(
    path: str,
    old_text: str,
    new_text: str,
    expected_replacements: int = 1,
) -> str:
    """Replace exact text in a project file with an occurrence-count safety check."""

    return _tool_result(
        lambda: get_workspace().replace_in_file(
            path,
            old_text,
            new_text,
            expected_replacements,
        )
    )


@tool("Delete Project Path")
def delete_project_path(path: str) -> str:
    """Delete one file or directory inside the project workspace.

    The workspace root and .git are protected. Use only when a path is obsolete
    or was created incorrectly, and inspect it first.
    """

    return _tool_result(lambda: get_workspace().delete_path(path))


@tool("Run Project Command")
def run_project_command(
    command: str,
    working_directory: str = ".",
    timeout_seconds: int = 120,
) -> str:
    """Run an allowlisted development command from inside the project.

    The command is parsed without a shell; pipes, redirection, chained commands,
    and inline code flags are rejected. Stdout, stderr, timeout information, and
    the exit code are always returned. Write a script in the project when a
    multi-step command is needed.
    """

    return _tool_result(
        lambda: get_workspace().run_command(
            command,
            relative_cwd=working_directory,
            timeout_seconds=timeout_seconds,
        )
    )


workspace_tools: list[BaseTool] = [
    list_project_files,
    read_project_file,
    write_project_file,
    replace_in_project_file,
    delete_project_path,
    run_project_command,
]


def _never_cache(*_args, **_kwargs) -> bool:
    return False


for workspace_tool in workspace_tools:
    workspace_tool.cache_function = _never_cache
