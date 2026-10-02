"""Group ``search``: finding text, files, and symbols without reading whole files."""

from __future__ import annotations

from crewai.tools import BaseTool, tool

from engineering_team.tools.navigation import project_outline, repo_map
from engineering_team.tools.search import find_files, search_files
from engineering_team.tools.support import ToolEnv, bounded


def make_search_tools(env: ToolEnv) -> dict[str, BaseTool]:
    workspace = env.workspace

    @tool("Search Project Files")
    def search_project_files(
        pattern: str,
        path: str = ".",
        regex: bool = False,
        case: str = "smart",
        include: list[str] | None = None,
        exclude: list[str] | None = None,
        context: int = 0,
        max_results: int = 50,
    ) -> str:
        """Search file contents for text (literal by default, regex=true for a regex).

        Returns 'file:line: text' matches, counts first. include/exclude are globs such as
        ['*.py'] or ['tests/**']; context adds surrounding lines (max 5); case is smart
        (insensitive unless the pattern has capitals), sensitive, or insensitive. Respects
        .gitignore and skips binaries. Then use Read File Range on a hit.
        """

        return env.run(
            "Search Project Files",
            lambda: bounded(
                search_files(
                    workspace,
                    pattern,
                    path=path,
                    regex=regex,
                    case=case,
                    include=include,
                    exclude=exclude,
                    context=context,
                    max_results=max_results,
                ),
                hint="narrow with path/include or lower context",
            ),
            arguments={
                "pattern": pattern,
                "path": path,
                "regex": regex,
                "include": include,
                "exclude": exclude,
            },
        )

    @tool("Find Files")
    def find_files_tool(
        pattern: str, path: str = ".", sort: str = "name", max_results: int = 100
    ) -> str:
        """Find files by glob (e.g. '*.py', 'src/**/test_*.ts'); sort='name' or 'recent'.

        A pattern without '/' matches names at any depth. Use it to locate a file whose
        content you do not need to search, or to see what changed recently.
        """

        return env.run(
            "Find Files",
            lambda: find_files(workspace, pattern, path=path, sort=sort, max_results=max_results),
            arguments={"pattern": pattern, "path": path, "sort": sort},
        )

    @tool("Project Outline")
    def project_outline_tool(path: str = ".", max_files: int = 40) -> str:
        """List top-level symbols (classes, functions, methods, types) per source file.

        Works for Python, JS/TS, Go, Java/Kotlin, and Rust. Pass a file or a directory.
        Use it to learn a module's shape, then Read File Range at the line numbers shown.
        """

        return env.run(
            "Project Outline",
            lambda: bounded(
                project_outline(workspace, path, max_files), hint="pass a narrower path"
            ),
            arguments={"path": path, "max_files": max_files},
        )

    @tool("Repo Map")
    def repo_map_tool(path: str = ".", size: int = 1500) -> str:
        """Overview of the most important source files and their key symbols (size = token budget).

        Files are ranked by how often other files reference what they define, so the core
        modules come first. Call it once when starting on an existing codebase, then use
        Search Project Files and Read File Range for details.
        """

        return env.run(
            "Repo Map",
            lambda: repo_map(workspace, path, size),
            arguments={"path": path, "size": size},
        )

    return {
        "Search Project Files": search_project_files,
        "Find Files": find_files_tool,
        "Project Outline": project_outline_tool,
        "Repo Map": repo_map_tool,
    }
