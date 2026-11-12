"""The real ``engineering-team mcp serve`` process, spoken to over stdio with the MCP client.

Only calls that start nothing: a run would be a model-driven subprocess.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("mcp")

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

from engineering_team.cli.app import main  # noqa: E402

NO_DOTENV = "import dotenv\ndotenv.load_dotenv = lambda *args, **kwargs: False\n"


def server_parameters(tmp_path: Path, repo: Path) -> StdioServerParameters:
    """The server process with the developer's ``.env`` (which CrewAI loads at import) kept out."""

    patch = tmp_path / "patch"
    patch.mkdir()
    (patch / "sitecustomize.py").write_text(NO_DOTENV, encoding="utf-8")
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(tmp_path),
        "PYTHONPATH": str(patch),
        "CREWAI_STORAGE_DIR": str(tmp_path / "crewai"),
    }
    return StdioServerParameters(
        command=sys.executable,
        args=[
            "-m", "engineering_team", "--workspace-root", str(tmp_path / "ws"),
            "mcp", "serve", "--repo", str(repo),
        ],
        env=env,
        cwd=tmp_path,
    )  # fmt: skip


async def session_calls(params: StdioServerParameters) -> dict[str, Any]:
    out: dict[str, Any] = {}
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        init = await session.initialize()
        out["server"] = init.serverInfo.name
        out["tools"] = sorted(tool.name for tool in (await session.list_tools()).tools)
        out["runs"] = (await session.call_tool("list_runs", {})).structuredContent
        refused = await session.call_tool(
            "start_task", {"request": "Do it", "repo": str(params.cwd)}
        )
        out["refused"] = (refused.isError, refused.content[0].text)  # type: ignore[union-attr]
        missing = await session.call_tool("task_status", {"run_id": "20260101-000000-abcdef"})
        out["missing"] = (missing.isError, missing.content[0].text)  # type: ignore[union-attr]
    return out


def test_the_server_speaks_mcp_over_stdio(tmp_path: Path) -> None:
    repo = tmp_path / "project"
    repo.mkdir()

    seen = asyncio.run(asyncio.wait_for(session_calls(server_parameters(tmp_path, repo)), 120))

    assert seen["server"] == "engineering-team"
    assert seen["tools"] == [
        "answer", "cancel", "list_runs", "start_task", "task_report", "task_status",
    ]  # fmt: skip
    assert seen["runs"] == {"runs": [], "count": 0}
    assert seen["refused"][0] is True and "outside the repositories" in seen["refused"][1]
    assert seen["missing"][0] is True and "No run" in seen["missing"][1]


def test_a_missing_allowed_directory_stops_the_server_with_a_usage_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["--workspace-root", str(tmp_path / "ws"), "mcp", "serve", "--repo", "/no/such"])

    assert code != 0
    assert "not an existing directory" in capsys.readouterr().err
