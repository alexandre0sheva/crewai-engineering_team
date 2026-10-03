"""Group ``git_read``: read a project's Git history. Read-only; the port builds every command."""

from __future__ import annotations

from collections.abc import Callable

from crewai.tools import BaseTool, tool

from engineering_team.tools.support import ToolEnv, ToolError, bounded

ACTIONS = ("status", "diff", "log", "show", "blame")
HINT = "narrow with path, ref, or max_count"


def make_git_tools(env: ToolEnv) -> dict[str, BaseTool]:
    from engineering_team.git.port import GitError  # the port imports the tools package

    git = env.ctx.git

    def reading(work: Callable[[], str]) -> str:
        """Run ``work``, turning a Git problem into the ``ERROR:`` the agent can act on."""

        try:
            return work()
        except GitError as exc:
            raise ToolError(str(exc)) from exc

    @tool("Git Info")
    def git_info(
        action: str,
        path: str = "",
        ref: str = "HEAD",
        max_count: int = 20,
        start_line: int = 0,
        end_line: int = 0,
    ) -> str:
        """Read the project's Git state or history (read-only; nothing is ever changed).

        action: status (branch and changed files), diff (uncommitted work against HEAD, new
        files included), log (recent commits, newest first; with path, that file's history),
        show (one commit's message and diff, or ref with a path), blame (who last changed each
        line of a file; start_line and end_line pick a range). ref is a branch, tag, sha, or
        HEAD~N. max_count limits log (default 20). Example: action='blame', path='src/app.py'.
        """

        def work() -> str:
            name = action.strip().lower()
            if name not in ACTIONS:
                raise ToolError(f"action must be one of: {', '.join(ACTIONS)}.")
            if name == "status":
                changes = git.status().rstrip()
                return f"Branch: {git.current_branch()}\n{changes or 'Working tree clean.'}"
            if name == "diff":
                diff = git.diff(None, (path,) if path.strip() else ())
                return bounded(diff or "No uncommitted changes.", hint=HINT)
            if name == "log":
                text = git.log(max_count=max_count, path=path, ref=ref)
                count = len(text.splitlines())
                return bounded(f"{count} commit(s)\n{text}" if count else "No commits.", hint=HINT)
            if name == "show":
                return bounded(git.show(ref, path), hint=HINT)
            if not path.strip():
                raise ToolError("blame needs a file path (path='src/app.py').")
            return bounded(git.blame(path, start=start_line, end=end_line), hint=HINT)

        return env.run(
            "Git Info",
            lambda: reading(work),
            arguments={
                "action": action,
                "path": path,
                "ref": ref,
                "max_count": max_count,
                "start_line": start_line,
                "end_line": end_line,
            },  # fmt: skip
        )

    @tool("Git History Search")
    def git_history_search(
        pattern: str = "", mode: str = "string", path: str = "", max_count: int = 20
    ) -> str:
        """Find the commits that added or removed some text, or list a file's history.

        With pattern, finds commits whose change added or removed it (mode='string' matches
        the text exactly, mode='regex' a basic regular expression; path limits it to a file or
        directory). With only path, shows that file's history and who changed it last. Use it
        to learn why code is the way it is. Example: pattern='retry_limit', path='src/'.
        """

        def work() -> str:
            if not pattern.strip() and not path.strip():
                raise ToolError(
                    "Pass a pattern to search for, a path whose history to show, or both."
                )
            if not pattern.strip():
                text = git.log(max_count=max_count, path=path)
                lines = text.splitlines()
                if not lines:
                    return f"No history for {path}."
                return bounded(
                    f"History of {path} ({len(lines)} commit(s)). Last changed: {lines[0]}\n{text}",
                    hint=HINT,
                )
            text = git.search(pattern, mode=mode, path=path, max_count=max_count)
            lines = text.splitlines()
            if not lines:
                return f"No commit adds or removes {pattern!r}" + (f" in {path}." if path else ".")
            return bounded(f"{len(lines)} commit(s) changed {pattern!r}:\n{text}", hint=HINT)

        return env.run(
            "Git History Search",
            lambda: reading(work),
            arguments={"pattern": pattern, "mode": mode, "path": path, "max_count": max_count},
        )

    @tool("Git Diff Between Refs")
    def git_diff_between_refs(first: str, second: str, path: str = "", stat: bool = False) -> str:
        """Show what changed between two commits, branches, or tags (not the work tree).

        first is the older ref and second the newer one (branch, tag, sha, HEAD~N). path
        limits the diff to a file or directory; stat=true returns only a per-file summary,
        which is the right first call for a big range. Example: first='HEAD~5', second='HEAD'.
        """

        def work() -> str:
            text = git.diff_refs(first, second, path=path, stat=stat)
            return bounded(text or "No differences.", hint="pass stat=true or a path")

        return env.run(
            "Git Diff Between Refs",
            lambda: reading(work),
            arguments={"first": first, "second": second, "path": path, "stat": stat},
        )

    return {
        "Git Info": git_info,
        "Git History Search": git_history_search,
        "Git Diff Between Refs": git_diff_between_refs,
    }
