"""Read-only access to a project's SQLite database: queries and schema.

The connection is opened ``mode=ro`` (the file cannot be written), is switched to
``query_only``, and runs under an authorizer that allows only reading: an ``INSERT``,
``DELETE``, ``DROP``, ``ATTACH`` or a write ``PRAGMA`` fails before it does anything. Queries are
parameterised, run under a time limit, and return a bounded number of rows and characters. Other
databases are out of scope for 0.2.0.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from engineering_team.tools.support import ToolError

DEFAULT_ROWS = 100
MAX_ROWS = 1000
MAX_CELL_CHARS = 80
MAX_OUTPUT_CHARS = 30_000
QUERY_SECONDS = 5.0
MAX_TABLES = 50
READ_PRAGMAS = frozenset(
    {
        "table_info",
        "table_xinfo",
        "table_list",
        "index_list",
        "index_info",
        "index_xinfo",
        "foreign_key_list",
        "database_list",
        "user_version",
        "schema_version",
        "page_count",
        "page_size",
        "encoding",
        "journal_mode",
        "foreign_keys",
        "collation_list",
    }
)
ALLOWED_ACTIONS = frozenset(
    {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}
)


def _authorize(action: int, arg1: str | None, *_rest: Any) -> int:
    if action in ALLOWED_ACTIONS:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_PRAGMA and (arg1 or "").lower() in READ_PRAGMAS:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def open_readonly(path: Path) -> sqlite3.Connection:
    """Open ``path`` for reading only, with a time limit on every statement."""

    if not path.is_file():
        raise ToolError(f"Database file not found: {path.name}. Use Find Files to locate it.")
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=2.0)
        connection.execute("PRAGMA query_only = ON")
        # Opening is lazy: without a read of the schema, `SELECT 1` "works" on a text file.
        connection.execute("SELECT count(*) FROM sqlite_master").fetchone()
        connection.set_authorizer(_authorize)
        deadline = time.monotonic() + QUERY_SECONDS
        connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 10_000)
    except sqlite3.Error as exc:
        if connection is not None:
            connection.close()
        raise ToolError(f"Cannot open {path.name} read-only: {exc}") from exc
    return connection


def _cell(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bytes):
        return f"<blob {len(value)} bytes>"
    text = str(value).replace("\n", "\\n")
    return text if len(text) <= MAX_CELL_CHARS else text[: MAX_CELL_CHARS - 3] + "..."


def _params(text: str) -> list[Any] | dict[str, Any]:
    if not text.strip():
        return []
    try:
        value = json.loads(text)
    except ValueError as exc:
        raise ToolError(
            f"params must be JSON: a list ([1, 'a']) or an object ({{'id': 1}}): {exc}"
        ) from exc
    if not isinstance(value, list | dict):
        raise ToolError(
            "params must be a JSON list for ? placeholders or an object for :name ones."
        )
    return value


def run_query(path: Path, sql: str, params: str = "", max_rows: int = DEFAULT_ROWS) -> str:
    """Run one read-only statement; rows as a table, capped in rows, cell width, and total size."""

    if not sql.strip():
        raise ToolError("Pass a SELECT statement. Use Inspect Database Schema to see the tables.")
    limit = max(1, min(int(max_rows), MAX_ROWS))
    connection = open_readonly(path)
    try:
        try:
            cursor = connection.execute(sql, _params(params))
            rows = cursor.fetchmany(limit + 1)
        except sqlite3.Error as exc:
            raise ToolError(_explain(exc)) from exc
        columns = [column[0] for column in cursor.description or []]
    finally:
        connection.close()
    more = len(rows) > limit
    shown = [[_cell(value) for value in row] for row in rows[:limit]]
    lines = [
        f"{len(shown)} row(s)" + (f" (limited to {limit}; the query returns more)" if more else "")
    ]
    if columns:
        lines += [" | ".join(columns), "-+-".join("-" * max(len(c), 3) for c in columns)]
        lines += [" | ".join(row) for row in shown]
    text = "\n".join(lines)
    if len(text) > MAX_OUTPUT_CHARS:
        text = text[:MAX_OUTPUT_CHARS] + "\n... output truncated; select fewer columns or rows."
    return text


def _explain(exc: sqlite3.Error) -> str:
    message = str(exc)
    if "not authorized" in message or "readonly" in message.lower():
        return f"{message}. This tool is read-only: only SELECT (and read PRAGMAs) are allowed."
    if "interrupted" in message:
        return (
            f"The query ran longer than {QUERY_SECONDS:g}s and was stopped; add a WHERE or LIMIT."
        )
    return f"SQL error: {message}"


def quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def describe_schema(path: Path) -> str:
    """Tables and views with columns, indexes, and row counts."""

    connection = open_readonly(path)
    try:
        objects = connection.execute(
            "SELECT type, name FROM sqlite_master WHERE type IN ('table', 'view') "
            "AND name NOT LIKE 'sqlite_%' ORDER BY type, name"
        ).fetchall()
        version = connection.execute("SELECT sqlite_version()").fetchone()[0]
        lines = [
            f"{path.name}: {path.stat().st_size:,} bytes, SQLite {version}, "
            f"{sum(kind == 'table' for kind, _ in objects)} table(s), "
            f"{sum(kind == 'view' for kind, _ in objects)} view(s)"
        ]
        for kind, name in objects[:MAX_TABLES]:
            quoted = quote_identifier(name)
            count = ""
            if kind == "table":
                total = connection.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0]
                count = f" ({total:,} rows)"
            lines += ["", f"{kind} {name}{count}"]
            for _, column, kind_name, notnull, default, pk in connection.execute(
                f"PRAGMA table_info({quoted})"
            ):
                flags = " PK" if pk else ""
                flags += " NOT NULL" if notnull else ""
                flags += f" DEFAULT {default}" if default is not None else ""
                lines.append(f"  {column} {kind_name or 'ANY'}{flags}")
            if kind == "table":
                for _, index, unique, *_ in connection.execute(f"PRAGMA index_list({quoted})"):
                    cols = [
                        row[2]
                        for row in connection.execute(
                            f"PRAGMA index_info({quote_identifier(index)})"
                        )
                    ]
                    unique_text = " UNIQUE" if unique else ""
                    names = ", ".join(c or "?" for c in cols)
                    lines.append(f"  index {index}{unique_text} ({names})")
        if len(objects) > MAX_TABLES:
            lines.append(f"\n... {len(objects) - MAX_TABLES} more objects not shown.")
    except sqlite3.Error as exc:
        raise ToolError(_explain(exc)) from exc
    finally:
        connection.close()
    return "\n".join(lines)
