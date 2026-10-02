"""Group ``command``: running project commands and the project's own scripts."""

from __future__ import annotations

import shlex

from crewai.tools import BaseTool, tool

from engineering_team.tools.commands import run_command
from engineering_team.tools.scripts import list_scripts, resolve_script
from engineering_team.tools.support import ToolEnv, ToolError


def make_command_tools(env: ToolEnv) -> dict[str, BaseTool]:
    workspace = env.workspace
    ctx = env.ctx

    def execute(command: str, working_directory: str, timeout_seconds: int) -> str:
        return run_command(
            workspace,
            command,
            relative_cwd=working_directory,
            timeout_seconds=timeout_seconds,
            backend=ctx.backend,
            gate=ctx.command_gate,
        )

    @tool("Run Project Command")
    def run_project_command(
        command: str, working_directory: str = ".", timeout_seconds: int = 120
    ) -> str:
        """Run an allowlisted development command from inside the project.

        Parsed without a shell: no pipes, redirection, chaining, or inline code flags. Returns
        the exit code, duration, the output (head and tail if long), and 'full log: <path>'
        which Read File Range can page through. Timeouts kill the whole process tree. Prefer
        Run Script when the project defines the script. Example: command='pytest -q tests'.
        """

        return env.run(
            "Run Project Command",
            lambda: execute(command, working_directory, timeout_seconds),
            arguments={
                "command": command,
                "working_directory": working_directory,
                "timeout_seconds": timeout_seconds,
            },
        )

    @tool("List Scripts")
    def list_scripts_tool(path: str = ".") -> str:
        """List the project's own scripts: package.json scripts, Makefile targets, justfile
        recipes, and pyproject [project.scripts]. Call before Run Script or guessing a command.
        """

        def operation() -> str:
            directory = workspace.resolve(path, must_exist=True)
            if not directory.is_dir():
                raise ToolError(f"{path} is a file; pass the project directory.")
            return list_scripts(directory, path)

        return env.run("List Scripts", operation, arguments={"path": path})

    @tool("Run Script")
    def run_script(
        name: str, args: str = "", working_directory: str = ".", timeout_seconds: int = 300
    ) -> str:
        """Run a script from List Scripts by name ('test' or 'npm:test'), with optional extra args.

        Same output and limits as Run Project Command; the underlying tool (npm, make, just,
        uv) must be allowed in the command allowlist. Example: name='test', args='-k login'.
        """

        def operation() -> str:
            directory = workspace.resolve(working_directory, must_exist=True)
            if not directory.is_dir():
                raise ToolError(f"{working_directory} is a file; pass the project directory.")
            script = resolve_script(directory, name, working_directory)
            try:
                extra = shlex.split(args)
            except ValueError as exc:
                raise ToolError(f"Could not parse args ({exc}).") from exc
            return execute(script.command(extra), working_directory, timeout_seconds)

        return env.run(
            "Run Script",
            operation,
            arguments={"name": name, "args": args, "working_directory": working_directory},
        )

    return {
        "Run Project Command": run_project_command,
        "List Scripts": list_scripts_tool,
        "Run Script": run_script,
    }
