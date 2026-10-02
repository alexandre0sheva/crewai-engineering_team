"""Group ``fs_write``: creating, editing, moving, and deleting files, and reporting changes."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from crewai.tools import BaseTool, tool

from engineering_team.tools import patching
from engineering_team.tools.changes import workspace_changes
from engineering_team.tools.support import ToolEnv, ToolError, bounded
from engineering_team.tools.workspace import ProjectWorkspace


def _link_or_resolve(workspace: ProjectWorkspace, path: str) -> Path:
    """The path itself, not where a final symlink points (so moving a link moves the link)."""

    supplied = Path(path.strip() or ".")
    if not supplied.parts or str(supplied) == ".":
        raise ToolError("The workspace root cannot be moved, copied, or replaced.")
    if (workspace.root / supplied).is_symlink():
        parent = workspace.resolve(supplied.parent.as_posix(), must_exist=True)
        return parent / supplied.name
    return workspace.resolve(path)


def _move(workspace: ProjectWorkspace, source: str, destination: str, overwrite: bool) -> str:
    src = _link_or_resolve(workspace, source)
    dst = _link_or_resolve(workspace, destination)
    if not (src.exists() or src.is_symlink()):
        raise ToolError(f"{source} does not exist. Use Find Files to locate it.")
    if src == dst:
        raise ToolError("Source and destination are the same path.")
    if src.is_dir() and dst.is_relative_to(src):
        raise ToolError(f"Cannot move {source} into itself ({destination}).")
    _prepare_destination(dst, destination, overwrite)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return f"Moved {source} to {destination}."


def _prepare_destination(dst: Path, destination: str, overwrite: bool) -> None:
    if dst.exists() or dst.is_symlink():
        if not overwrite:
            raise ToolError(
                f"{destination} already exists. Choose another destination or pass overwrite=true."
            )
        if dst.is_dir() and not dst.is_symlink():
            raise ToolError(
                f"{destination} is a directory; delete it first with Delete Project Path."
            )
        dst.unlink()


def _copy(workspace: ProjectWorkspace, source: str, destination: str, overwrite: bool) -> str:
    src = _link_or_resolve(workspace, source)
    dst = _link_or_resolve(workspace, destination)
    if not src.exists():
        raise ToolError(f"{source} does not exist. Use Find Files to locate it.")
    if src.is_dir() and dst.is_relative_to(src):
        raise ToolError(f"Cannot copy {source} into itself ({destination}).")
    _prepare_destination(dst, destination, overwrite)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, dst, symlinks=True, ignore=shutil.ignore_patterns(".git"))
    else:
        shutil.copy2(src, dst)
    return f"Copied {source} to {destination}."


def _apply_patch(env: ToolEnv, patch: str, edits: list[dict[str, Any]] | None) -> str:
    workspace = env.workspace
    if bool(patch.strip()) == bool(edits):
        raise ToolError(
            "Pass exactly one of: patch (a unified diff) or edits (a list of "
            "{path, old, new, expected_replacements})."
        )
    changes = (
        patching.plan_unified_diff(workspace, patch)
        if patch.strip()
        else patching.plan_edits(workspace, edits or [])
    )
    for change in changes:  # every file must be in scope before any is written
        env.ensure_writable([change.path], deleting=change.new_text is None)
    patching.commit(workspace, changes)
    return patching.summarize(changes)


def make_write_tools(env: ToolEnv) -> dict[str, BaseTool]:
    workspace = env.workspace
    ctx = env.ctx

    @tool("Write Project File")
    def write_project_file(path: str, content: str) -> str:
        """Create or overwrite one UTF-8 text file under the project root.

        Parent directories are created automatically, so normal nested application
        structures such as src/, tests/, apps/, and packages/ are supported. For edits to
        an existing file prefer Apply Patch or Replace In Project File.
        """

        return env.run(
            "Write Project File",
            lambda: workspace.write_file(path, content),
            arguments={"path": path, "content": content},
            changes=[path],
        )

    @tool("Replace In Project File")
    def replace_in_project_file(
        path: str, old_text: str, new_text: str, expected_replacements: int = 1
    ) -> str:
        """Replace exact text in one file; fails unless old_text occurs expected_replacements times.

        Make old_text unique by including neighbouring lines. For several edits at once use
        Apply Patch.
        """

        return env.run(
            "Replace In Project File",
            lambda: workspace.replace_in_file(path, old_text, new_text, expected_replacements),
            arguments={"path": path, "old_text": old_text, "new_text": new_text},
            changes=[path],
        )

    @tool("Delete Project Path")
    def delete_project_path(path: str) -> str:
        """Delete one file or directory inside the project workspace.

        The workspace root and .git are protected. Use only when a path is obsolete
        or was created incorrectly, and inspect it first.
        """

        return env.run(
            "Delete Project Path",
            lambda: workspace.delete_path(path),
            arguments={"path": path},
            deletes=[path],
        )

    @tool("Apply Patch")
    def apply_patch(patch: str = "", edits: list[dict[str, Any]] | None = None) -> str:
        """Change several places or files atomically; all edits are validated before any is written.

        Give either patch (a unified diff: '--- a/f', '+++ b/f', '@@ -a,b +c,d @@' hunks;
        '--- /dev/null' creates, '+++ /dev/null' deletes) or edits (a list of
        {path, old, new, expected_replacements}). Returns per-file counts and new line ranges.
        """

        return env.run(
            "Apply Patch",
            lambda: _apply_patch(env, patch, edits),
            arguments={"patch": patch, "edits": edits},
        )

    @tool("Move Path")
    def move_path(source: str, destination: str, overwrite: bool = False) -> str:
        """Move or rename a file or directory inside the project (parents are created).

        Refuses to replace an existing destination unless overwrite=true (files only).
        """

        return env.run(
            "Move Path",
            lambda: _move(workspace, source, destination, overwrite),
            arguments={"source": source, "destination": destination},
            changes=[destination],
            deletes=[source],
        )

    @tool("Copy Path")
    def copy_path(source: str, destination: str, overwrite: bool = False) -> str:
        """Copy a file or a whole directory inside the project (parents are created).

        Refuses to replace an existing destination unless overwrite=true (files only).
        """

        return env.run(
            "Copy Path",
            lambda: _copy(workspace, source, destination, overwrite),
            arguments={"source": source, "destination": destination},
            changes=[destination],
        )

    @tool("Make Directory")
    def make_directory(path: str) -> str:
        """Create a directory, including missing parents. Succeeds if it already exists."""

        def operation() -> str:
            target = workspace.resolve(path)
            if target.exists() and not target.is_dir():
                raise ToolError(f"{path} exists and is a file.")
            target.mkdir(parents=True, exist_ok=True)
            return f"Directory ready: {workspace.relative_name(target)}"

        return env.run(
            "Make Directory", operation, arguments={"path": path}, changes=[f"{path}/.keep"]
        )

    @tool("Workspace Changes")
    def workspace_changes_tool(path: str = ".", max_diff_lines: int = 200, context: int = 2) -> str:
        """Show files added, modified, or deleted since this run started, with a compact diff.

        Works without Git. Use it to review your own work before finishing. Pass a path to
        limit the report and max_diff_lines to cap the diff.
        """

        return env.run(
            "Workspace Changes",
            lambda: bounded(
                workspace_changes(
                    workspace,
                    ctx.baseline,
                    path=path,
                    max_diff_lines=max_diff_lines,
                    context=context,
                ),
                hint="pass a narrower path",
            ),
            arguments={"path": path},
        )

    return {
        "Write Project File": write_project_file,
        "Replace In Project File": replace_in_project_file,
        "Delete Project Path": delete_project_path,
        "Apply Patch": apply_patch,
        "Move Path": move_path,
        "Copy Path": copy_path,
        "Make Directory": make_directory,
        "Workspace Changes": workspace_changes_tool,
    }
