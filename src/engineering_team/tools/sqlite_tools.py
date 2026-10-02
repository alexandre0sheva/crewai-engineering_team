"""Group ``runtime``: read-only SQLite inspection."""

from __future__ import annotations

from pathlib import Path

from crewai.tools import BaseTool, tool

from engineering_team.tools.sqlite_inspect import DEFAULT_ROWS, describe_schema, run_query
from engineering_team.tools.support import ToolEnv, ToolError


def make_sqlite_tools(env: ToolEnv) -> dict[str, BaseTool]:
    workspace = env.workspace

    def database(path: str) -> Path:
        resolved = workspace.resolve(path, must_exist=True)
        if not resolved.is_file():
            raise ToolError(f"{path} is not a file; pass the path of the .db / .sqlite file.")
        return resolved

    @tool("Query SQLite")
    def query_sqlite(path: str, sql: str, params: str = "", max_rows: int = DEFAULT_ROWS) -> str:
        """Run one read-only SELECT against a project's SQLite file and get the rows as a table.

        The file is opened read-only; writes, ATTACH, and write PRAGMAs fail. Use ? or :name
        placeholders with params as JSON ('[1, "a"]' or '{"id": 1}'). Rows (default 100), cell
        width, and run time are capped. Example: sql='SELECT * FROM users WHERE id = ?',
        params='[1]'.
        """

        return env.run(
            "Query SQLite",
            lambda: run_query(database(path), sql, params, max_rows),
            arguments={"path": path, "sql": sql, "max_rows": max_rows},
        )

    @tool("Inspect Database Schema")
    def inspect_database_schema(path: str) -> str:
        """Show a SQLite file's tables and views with columns (type, primary key, not null,
        default), indexes, and row counts. Read-only. Call it before writing queries or code
        against an existing database; other database engines are not supported.
        """

        return env.run(
            "Inspect Database Schema",
            lambda: describe_schema(database(path)),
            arguments={"path": path},
        )

    return {"Query SQLite": query_sqlite, "Inspect Database Schema": inspect_database_schema}
