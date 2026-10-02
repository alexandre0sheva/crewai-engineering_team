"""Group ``code_intel``: where things are defined, who uses them, and what depends on what."""

from __future__ import annotations

from crewai.tools import BaseTool, tool

from engineering_team.codeintel.dependencies import format_dependencies, inspect_dependencies
from engineering_team.codeintel.git import GitUnavailable
from engineering_team.codeintel.hotspots import compute_hotspots, format_hotspots
from engineering_team.codeintel.imports import ImportGraph
from engineering_team.codeintel.index import SourceIndex
from engineering_team.codeintel.references import find_references
from engineering_team.codeintel.related import related_tests
from engineering_team.codeintel.render import (
    render_importers,
    render_imports_of,
    render_references,
    render_related,
    render_symbol_body,
    render_symbols,
    search_definitions,
)
from engineering_team.codeintel.targets import file_or_symbol, require_file
from engineering_team.codeintel.todos import DEFAULT_TAGS, annotate, format_todos, scan_todos
from engineering_team.tools.support import ToolEnv, bounded


def make_codeintel_tools(env: ToolEnv) -> dict[str, BaseTool]:
    workspace = env.workspace

    def prefix(path: str) -> str:
        """The workspace-relative directory or file ``path`` names ("" for the whole project)."""

        name = workspace.relative_name(workspace.resolve(path, must_exist=True))
        return "" if name == "." else name

    def indexed() -> SourceIndex:
        return SourceIndex.build(workspace)

    def note(index: SourceIndex, text: str) -> str:
        if index.truncated:
            text += "\n(Only the first 5000 source files were indexed; results may be incomplete.)"
        return bounded(text, hint="narrow with path or lower max_results")

    @tool("Find Symbol")
    def find_symbol(name: str, kind: str = "", path: str = ".", max_results: int = 30) -> str:
        """Find where a class, function, method, type, or constant is defined, across the repo.

        name matches case-insensitively as a substring (exact names first); kind narrows it
        (class, function, method, interface, struct, enum, trait, type, module, const); path
        limits it to a directory. Returns file:line, kind, and the line range of each
        definition. Use Show Symbol to read one. Example: name='parse_config'.
        """

        def work() -> str:
            index = indexed()
            found = search_definitions(index, name, kind=kind, path_prefix=prefix(path))
            return note(index, render_symbols(found, name, max(1, min(int(max_results), 100))))

        return env.run(
            "Find Symbol",
            work,
            arguments={"name": name, "kind": kind, "path": path, "max_results": max_results},
        )

    @tool("Show Symbol")
    def show_symbol(name: str, file: str = "", context: int = 3) -> str:
        """Print the full definition of a symbol with numbered lines and some context.

        name is the exact name or Class.method; file picks one definition when several share
        the name (the result lists them). context adds lines before and after (max 20). Use it
        instead of reading a whole file when you only need one function or class.
        """

        def work() -> str:
            index = indexed()
            where = prefix(file) if file.strip() else ""
            found = search_definitions(index, name, path_prefix=where)
            return note(index, render_symbol_body(index, name, found, context))

        return env.run(
            "Show Symbol", work, arguments={"name": name, "file": file, "context": context}
        )

    @tool("Find References")
    def find_references_tool(symbol: str, path: str = ".", max_results: int = 60) -> str:
        """List every use of a name, classified as definition, import, call, or other.

        Whole-word and case-sensitive over source files (Python, JS/TS, Go, Java/Kotlin, C#,
        Rust, Ruby, PHP), grouped by file with line numbers. Use it before changing a
        function to see what calls it; pair it with Who Imports and Find Related Tests.
        """

        def work() -> str:
            index = indexed()
            found = find_references(index, symbol, path_prefix=prefix(path))
            return note(index, render_references(found, symbol, max(1, min(int(max_results), 300))))

        return env.run(
            "Find References",
            work,
            arguments={"symbol": symbol, "path": path, "max_results": max_results},
        )

    @tool("Who Imports")
    def who_imports(target: str, depth: int = 1, max_results: int = 60) -> str:
        """Reverse dependencies: which files import a file or module ("what could break").

        target is a file path, a dotted module (app.config), or a unique file name. depth=1
        lists direct importers; depth 2-4 also follows their importers. Static analysis for
        Python, JS/TS, Go (by package), Java/Kotlin, C#, Rust, Ruby, and PHP.
        """

        def work() -> str:
            index = indexed()
            path = require_file(index, target)
            text = render_importers(
                ImportGraph(index), path, depth, max(1, min(int(max_results), 300))
            )
            return note(index, text)

        return env.run(
            "Who Imports",
            work,
            arguments={"target": target, "depth": depth, "max_results": max_results},
        )

    @tool("Imports Of")
    def imports_of(path: str) -> str:
        """Forward dependencies: what one source file imports, split into project files and
        external packages. Use it to learn what a module relies on before changing or moving
        it. path is a file path or dotted module name; line numbers show where each import is.
        """

        def work() -> str:
            index = indexed()
            file = require_file(index, path)
            return note(index, render_imports_of(ImportGraph(index), file))

        return env.run("Imports Of", work, arguments={"path": path})

    @tool("Find Related Tests")
    def find_related_tests(target: str, max_results: int = 30) -> str:
        """Find the tests that cover a file or a symbol.

        For a file path (or module name): tests that import it, are named after it
        (test_x.py, x.spec.ts, x_test.go, XTest.java), or mention what it defines. For any
        other text: tests that mention that symbol. Each result says why it is related.
        """

        def work() -> str:
            index = indexed()
            file = file_or_symbol(index, target)
            found = related_tests(index, ImportGraph(index), file)
            return note(index, render_related(found, file, max(1, min(int(max_results), 100))))

        return env.run(
            "Find Related Tests", work, arguments={"target": target, "max_results": max_results}
        )

    @tool("Find TODOs")
    def find_todos(
        path: str = ".", tags: str = "", max_results: int = 50, with_git: bool = True
    ) -> str:
        """List TODO, FIXME, and HACK comments (tags: comma list, default TODO,FIXME,HACK).

        Searches every text file below path; with_git adds who wrote each line and how old it
        is when the project has Git history (otherwise it says those are unknown). Use it to
        find known problems and unfinished work before editing a module.
        """

        def work() -> str:
            wanted = tuple(t.strip().upper() for t in tags.split(",") if t.strip()) or DEFAULT_TAGS
            todos = scan_todos(workspace, tags=wanted, path_prefix=prefix(path))
            limit = max(1, min(int(max_results), 200))
            items, git_note = annotate(env.ctx, todos, limit=limit, use_git=with_git)
            return bounded(format_todos(items, limit=limit, git_note=git_note))

        return env.run(
            "Find TODOs",
            work,
            arguments={"path": path, "tags": tags, "max_results": max_results},
        )

    @tool("Hotspots")
    def hotspots(days: int = 365, top: int = 10, path: str = ".") -> str:
        """Rank the riskiest files: often changed in Git history and big or branchy.

        days is the history window (0 = everything), top how many files (max 50), path limits
        the directory. Score = commits x a complexity proxy. Needs Git history; without it the
        answer says so and you should use Repo Map instead. Read hotspots before refactoring.
        """

        def work() -> str:
            try:
                report = compute_hotspots(
                    env.ctx, days=max(0, int(days)), top=int(top), path_prefix=prefix(path)
                )
            except GitUnavailable as exc:
                return (
                    f"No Git history: {exc}. Hotspots need commits; use Repo Map or Project "
                    "Outline to find the central files instead."
                )
            return bounded(format_hotspots(report))

        return env.run("Hotspots", work, arguments={"days": days, "top": top, "path": path})

    @tool("Inspect Dependencies")
    def inspect_dependencies_tool(path: str = ".") -> str:
        """List the direct dependencies a project declares, with declared and locked versions.

        Reads pyproject.toml, requirements*.txt, package.json, go.mod, Cargo.toml, and pom.xml
        below path (a monorepo shows each manifest), plus uv.lock, poetry.lock, Cargo.lock,
        or package-lock.json for locked versions. Offline: it does not say what is outdated.
        """

        return env.run(
            "Inspect Dependencies",
            lambda: bounded(format_dependencies(inspect_dependencies(workspace, path))),
            arguments={"path": path},
        )

    return {
        "Find Symbol": find_symbol,
        "Show Symbol": show_symbol,
        "Find References": find_references_tool,
        "Who Imports": who_imports,
        "Imports Of": imports_of,
        "Find Related Tests": find_related_tests,
        "Find TODOs": find_todos,
        "Hotspots": hotspots,
        "Inspect Dependencies": inspect_dependencies_tool,
    }
