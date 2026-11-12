"""The MCP server: its tool definitions, its safety boundary, and its handlers in-process.

Runs are started with the stand-in CLI (``tests/stub_run.py``), so nothing here calls a model.
The last test starts the real server over stdio with the MCP client.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from cli_helpers import workspace_root
from pipeline_fakes import settings_for

pytest.importorskip("mcp")

from mcp.server.fastmcp.exceptions import ToolError  # noqa: E402

from engineering_team.cli.do_plan import MODES  # noqa: E402
from engineering_team.mcp_server import missing_dependencies  # noqa: E402
from engineering_team.mcp_server.guard import (  # noqa: E402
    Refused,
    check_repo,
    clamp_budget,
    resolve_roots,
)
from engineering_team.mcp_server.handlers import TeamTools  # noqa: E402
from engineering_team.mcp_server.launcher import McpLauncher  # noqa: E402
from engineering_team.mcp_server.server import build_server  # noqa: E402
from engineering_team.runtime.run_index import register_workspace  # noqa: E402
from engineering_team.runtime.run_store import RunStore  # noqa: E402
from engineering_team.settings import Settings  # noqa: E402
from engineering_team.ui.launcher import Budget  # noqa: E402

STUB = Path(__file__).parent / "stub_run.py"
TOOLS = ["answer", "cancel", "list_runs", "start_task", "task_report", "task_status"]


def make_tools(
    tmp_path: Path, *, repos: bool = True, concurrent: int = 4, **overrides: object
) -> tuple[TeamTools, McpLauncher, Path]:
    repo = tmp_path / "project"
    repo.mkdir()
    root = workspace_root()
    settings = settings_for(root, **overrides)
    launcher = McpLauncher(
        str(root), max_concurrent=concurrent, command=[sys.executable, str(STUB)]
    )
    roots = resolve_roots([str(repo)] if repos else [])
    return TeamTools(settings, launcher, roots, poll_seconds=0.05), launcher, repo


def wait_for(check: Callable[[], Any], what: str, timeout: float = 30.0) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = check()
        if found:
            return found
        time.sleep(0.05)
    pytest.fail(f"Timed out waiting for {what}")


def finished(tools: TeamTools, run_id: str) -> dict[str, Any]:
    return wait_for(lambda: (s := tools.task_status(run_id))["done"] and s, f"run {run_id} to end")


# -- tool definitions -------------------------------------------------------------------------


def listed(tools: TeamTools) -> dict[str, Any]:
    found = asyncio.run(build_server(tools).list_tools())
    return {tool.name: tool for tool in found}


def test_the_server_offers_exactly_the_six_tools(tmp_path: Path) -> None:
    tools, _, _ = make_tools(tmp_path)

    definitions = listed(tools)

    assert sorted(definitions) == TOOLS
    assert missing_dependencies() == []


def test_tool_arguments_are_declared_with_a_schema(tmp_path: Path) -> None:
    tools, _, _ = make_tools(tmp_path)
    definitions = listed(tools)

    start = definitions["start_task"].inputSchema
    assert start["required"] == ["request", "repo"]
    assert set(start["properties"]) == {"request", "repo", "mode", "budget"}
    modes = json.dumps(start["properties"]["mode"])
    assert all(f'"{mode}"' in modes for mode in MODES)  # the tool offers every `do --mode`
    assert definitions["task_report"].inputSchema["properties"]["format"]["enum"] == [
        "md", "html", "json",
    ]  # fmt: skip
    assert definitions["answer"].inputSchema["required"] == ["question", "repo"]
    for name in ("task_status", "cancel"):
        assert definitions[name].inputSchema["required"] == ["run_id"]
    assert definitions["list_runs"].inputSchema.get("required", []) == []


def test_tool_descriptions_are_short_and_say_which_tools_only_read(tmp_path: Path) -> None:
    tools, _, _ = make_tools(tmp_path)
    definitions = listed(tools)

    for name, tool in definitions.items():
        assert tool.description, name
        assert len(tool.description.split()) <= 120, name  # docs/IMPLEMENTATION_PLAN tool rules
        assert "Example:" in tool.description, name
    reads = {n for n, t in definitions.items() if t.annotations and t.annotations.readOnlyHint}
    assert reads == {"task_status", "task_report", "list_runs"}


# -- the safety boundary ----------------------------------------------------------------------


def test_a_repository_outside_the_allowed_ones_is_refused(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    (allowed / "sub").mkdir(parents=True)
    (tmp_path / "other").mkdir()
    (tmp_path / "escape").symlink_to(tmp_path / "other")
    (allowed / "link").symlink_to(tmp_path / "other")
    roots = resolve_roots([str(allowed)])

    assert check_repo(str(allowed), roots) == allowed.resolve()
    assert check_repo(str(allowed / "sub"), roots) == (allowed / "sub").resolve()
    for bad in (
        str(tmp_path / "other"),
        str(allowed / "link"),  # a symlink inside that points out
        str(allowed / ".." / "other"),
        str(tmp_path / "escape"),
    ):
        with pytest.raises(Refused, match="outside the repositories"):
            check_repo(bad, roots)
    with pytest.raises(Refused, match="absolute"):
        check_repo("allowed", roots)
    with pytest.raises(Refused, match="not an existing directory"):
        check_repo(str(allowed / "missing"), roots)
    with pytest.raises(Refused, match="required"):
        check_repo("", roots)


def test_with_no_allowed_repository_every_start_is_refused(tmp_path: Path) -> None:
    tools, launcher, repo = make_tools(tmp_path, repos=False)

    with pytest.raises(Refused, match="--repo DIR"):
        tools.start_task("Do something", str(repo))

    assert launcher.records() == []  # nothing was started


def test_a_missing_allowed_directory_is_a_configuration_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not an existing directory"):
        resolve_roots([str(tmp_path / "typo")])


def test_a_budget_above_the_caps_is_refused_and_one_is_always_set() -> None:
    settings = settings_for(workspace_root(), **{"budget.max_tokens": 1000})

    default = clamp_budget(None, settings)
    lower = clamp_budget(Budget(max_cost_usd=1.5, max_tokens=10), settings)

    assert default.max_cost_usd == settings.mcp_serve.max_cost_usd == 5.0  # never unlimited
    assert default.max_tokens == 1000  # the settings' own cap carries over
    assert (lower.max_cost_usd, lower.max_tokens) == (1.5, 10)
    with pytest.raises(Refused, match=r"budget.max_cost_usd 50.0 is above .* limit of 5.0"):
        clamp_budget(Budget(max_cost_usd=50), settings)
    with pytest.raises(Refused, match="max_tokens"):
        clamp_budget(Budget(max_tokens=5000), settings)


def test_the_smaller_of_the_two_cost_caps_wins() -> None:
    settings = settings_for(workspace_root(), **{"budget.max_cost_usd": 2.0})

    assert clamp_budget(None, settings).max_cost_usd == 2.0


def test_the_budget_reaches_the_run_as_settings_overrides(tmp_path: Path) -> None:
    tools, launcher, repo = make_tools(tmp_path)

    started = tools.start_task("Do it", str(repo), budget=Budget(max_cost_usd=0.75))

    record = launcher.record(started["run_id"])
    assert record is not None and record.overrides == {"budget.max_cost_usd": 0.75}
    assert started["budget"] == {"max_cost_usd": 0.75}
    finished(tools, started["run_id"])


# -- the handlers ------------------------------------------------------------------------------


def test_a_task_is_started_watched_and_reported(tmp_path: Path) -> None:
    tools, launcher, repo = make_tools(tmp_path)

    started = tools.start_task("Add a /search endpoint", str(repo), mode="feature")
    run_id = started["run_id"]
    status = finished(tools, run_id)
    report = tools.task_report(run_id)

    assert started["status"] == "starting" and started["repo"] == str(repo.resolve())
    assert status["status"] == "succeeded" and status["done"] is True
    assert status["progress"]["cards_total"] == 1 and status["usage"]["tool_calls"] > 0
    assert status["budget"]["state"] == "ok"
    assert report["format"] == "md" and "# " in report["report"]
    assert Path(report["report_path"]).name == "report.md"
    assert "not instructions" in report["notice"]
    assert [run["run_id"] for run in tools.list_runs()["runs"]] == [run_id]
    argv = launcher.record(run_id).argv  # type: ignore[union-attr]
    assert "do" in argv and "--yes" in argv and "--non-interactive" in argv
    assert str(repo.resolve()) in argv and "--no-open-pr" in argv


def test_the_run_is_started_as_do_with_a_request_file_and_a_worktree(tmp_path: Path) -> None:
    tools, launcher, repo = make_tools(tmp_path)

    change = tools.start_task("Add a /search endpoint", str(repo), mode="fix")["run_id"]
    asked = tools.start_task("How does login work?", str(repo), mode="review")["run_id"]
    auto = tools.start_task("Make it faster", str(repo))["run_id"]

    def argv(run_id: str) -> list[str]:
        return launcher.record(run_id).argv  # type: ignore[union-attr]

    for run_id in (change, asked, auto):
        request = Path(argv(run_id)[argv(run_id).index("--request-file") + 1])
        assert request.read_text(encoding="utf-8")  # the request is a file, never an argument
    assert "--worktree" in argv(change) and "--mode=fix" in argv(change)
    assert "--worktree" not in argv(asked)  # a review only reads
    assert "--worktree" in argv(auto) and not any(a.startswith("--mode") for a in argv(auto))
    for run_id in (change, asked, auto):
        finished(tools, run_id)


def test_an_unknown_mode_an_empty_request_and_a_bad_run_id_are_refused(tmp_path: Path) -> None:
    tools, launcher, repo = make_tools(tmp_path)

    with pytest.raises(Refused, match="mode 'ship-it'"):
        tools.start_task("Do it", str(repo), mode="ship-it")
    with pytest.raises(Refused, match="request is empty"):
        tools.start_task("   ", str(repo))
    with pytest.raises(Refused, match="not a run id"):
        tools.task_status("../../etc/passwd")
    with pytest.raises(Refused, match="No run"):
        tools.task_status("20260101-000000-abcdef")
    with pytest.raises(Refused, match="format"):
        tools.task_report("20260101-000000-abcdef", "pdf")

    assert launcher.records() == []


def test_too_many_runs_at_once_is_refused(tmp_path: Path) -> None:
    tools, launcher, repo = make_tools(tmp_path, concurrent=2)
    ids = [tools.start_task("SLOW", str(repo))["run_id"] for _ in range(2)]

    with pytest.raises(Refused, match="already going"):
        tools.start_task("one more", str(repo))

    for run_id in ids:
        tools.cancel(run_id)
        finished(tools, run_id)


def test_a_run_that_dies_at_once_reports_why(tmp_path: Path) -> None:
    tools, _, repo = make_tools(tmp_path)

    run_id = tools.start_task("FAIL-AT-ONCE", str(repo))["run_id"]
    status = finished(tools, run_id)

    assert status["status"] == "failed" and "told to fail" in status["error"]
    assert "already over" in tools.cancel(run_id)["message"]


def test_a_run_is_cancelled_and_says_so(tmp_path: Path) -> None:
    tools, _, repo = make_tools(tmp_path)
    run_id = tools.start_task("SLOW", str(repo))["run_id"]
    wait_for(lambda: tools.task_status(run_id).get("status") == "running", "the run to start")

    message = tools.cancel(run_id)["message"]
    status = finished(tools, run_id)

    assert "Cancellation requested" in message
    assert status["status"] == "cancelled"
    with pytest.raises(Refused, match="No run"):
        tools.cancel("20260101-000000-abcdef")


def test_answer_waits_for_the_run_and_returns_its_report(tmp_path: Path) -> None:
    tools, launcher, repo = make_tools(tmp_path)

    result = asyncio.run(tools.answer("How does login work?", str(repo)))

    assert result["done"] is True and result["status"] == "succeeded"
    run_id = result["run_id"]
    record = launcher.record(run_id)
    assert record is not None and "--mode=answer" in record.argv
    assert "--worktree" not in record.argv  # read-only: nothing is changed


def test_answer_past_its_wait_returns_the_run_id_to_poll(tmp_path: Path) -> None:
    tools, _, repo = make_tools(tmp_path)

    result = asyncio.run(tools.answer("SLOW question", str(repo), wait_seconds=1))

    assert result["done"] is False and "task_status" in result["next"]
    tools.cancel(result["run_id"])
    finished(tools, result["run_id"])


def test_an_answer_document_is_returned_with_the_report(tmp_path: Path) -> None:
    tools, _, repo = make_tools(tmp_path)
    run_id = tools.start_task("Question?", str(repo), mode="answer")["run_id"]
    finished(tools, run_id)
    ref = tools._find(run_id)
    assert ref is not None
    (ref.run_dir / "ANSWER.md").write_text("It appends [1].\n", encoding="utf-8")

    report = tools.task_report(run_id, "json")

    assert report["answer"] == "It appends [1].\n" and "report" not in report


def test_html_gives_a_path_not_the_page(tmp_path: Path) -> None:
    tools, _, repo = make_tools(tmp_path)
    run_id = tools.start_task("Do it", str(repo))["run_id"]
    finished(tools, run_id)

    report = tools.task_report(run_id, "html")

    assert report["report_path"].endswith("report.html") and "report" not in report


def test_long_text_is_cut_and_secrets_are_scrubbed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tools, _, repo = make_tools(tmp_path)
    run_id = tools.start_task("Do it", str(repo))["run_id"]
    finished(tools, run_id)
    ref = tools._find(run_id)
    assert ref is not None
    secret = "sk-test-0123456789abcdef"
    monkeypatch.setenv("SOME_API_KEY", secret)
    (ref.run_dir / "ANSWER.md").write_text(f"key {secret}\n" + "x" * 50_000, encoding="utf-8")

    report = tools.task_report(run_id, "md")

    assert secret not in json.dumps(report) and "[REDACTED]" in report["answer"]
    assert report["answer_truncated"] is True and len(report["answer"]) <= 40_000 + 20


def test_runs_of_other_projects_are_not_visible(tmp_path: Path) -> None:
    tools, _, repo = make_tools(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    store = RunStore(elsewhere)
    from engineering_team.contracts import RunManifest

    other = "20260101-000000-abcdef"
    store.create(RunManifest(run_id=other, project_name="elsewhere", mode="feature"))
    register_workspace(tools.settings.workspace_root, elsewhere, "elsewhere")

    assert tools.list_runs()["runs"] == []
    with pytest.raises(Refused, match="No run"):
        tools.task_status(other)
    with pytest.raises(Refused, match="No run"):
        tools.task_report(other)
    with pytest.raises(Refused, match="No run"):
        tools.cancel(other)

    tools.roots = resolve_roots([str(repo), str(elsewhere)])  # allowed now: visible
    assert [run["run_id"] for run in tools.list_runs()["runs"]] == [other]


# -- through the protocol, in-process --------------------------------------------------------------


def test_a_refusal_comes_back_as_a_tool_error_with_the_reason(tmp_path: Path) -> None:
    tools, launcher, repo = make_tools(tmp_path)
    server = build_server(tools)

    with pytest.raises(ToolError, match="outside the repositories"):
        asyncio.run(server.call_tool("start_task", {"request": "Do it", "repo": str(tmp_path)}))
    with pytest.raises(ToolError, match="above this server's limit"):
        asyncio.run(
            server.call_tool(
                "start_task",
                {"request": "x", "repo": str(repo), "budget": {"max_cost_usd": 99}},
            )
        )
    with pytest.raises(ToolError):  # a mode that does not exist fails the schema
        asyncio.run(
            server.call_tool("start_task", {"request": "x", "repo": str(repo), "mode": "rm"})
        )

    assert launcher.records() == []


def test_a_call_through_the_server_starts_a_run(tmp_path: Path) -> None:
    tools, _, repo = make_tools(tmp_path)
    server = build_server(tools)

    _, structured = asyncio.run(
        server.call_tool("start_task", {"request": "Do it", "repo": str(repo)})
    )  # type: ignore[misc]

    assert structured["status"] == "starting"
    finished(tools, structured["run_id"])


# -- the settings ------------------------------------------------------------------------------


def test_the_settings_have_safe_defaults(tmp_path: Path) -> None:
    settings: Settings = settings_for(workspace_root())

    assert settings.mcp_serve.repos == [] and settings.mcp_serve.max_cost_usd == 5.0
    assert settings.mcp_serve.max_concurrent_runs == 2
