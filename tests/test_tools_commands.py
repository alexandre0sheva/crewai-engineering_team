from __future__ import annotations

import json
import threading
from collections.abc import Callable

from conftest import Toolbox

from engineering_team.settings import load_settings


class RecordingSink:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []  # type: ignore[type-arg]

    def emit(self, type: str, **data: object) -> None:
        self.events.append((type, data))


def test_run_project_command_reports_exit_duration_output_and_log_path(toolbox: Toolbox) -> None:
    toolbox.write("t.py", "import sys\nprint('out')\nprint('err', file=sys.stderr)\nsys.exit(4)\n")

    result = toolbox("Run Project Command", command="python t.py")

    lines = result.splitlines()
    assert lines[0].startswith("Exit code: 4 (") and lines[0].endswith("s)")
    assert lines[1] == "output (stdout and stderr):"
    assert "out" in result and "err" in result
    assert lines[-1].startswith("full log: .engineering-team/runs/")


def test_a_flood_is_summarised_with_head_tail_and_a_log_hint(toolbox: Toolbox) -> None:
    toolbox.write("flood.py", "for n in range(30000):\n    print('line', n)\nprint('DONE')\n")

    result = toolbox("Run Project Command", command="python flood.py")

    assert len(result) < 20_000
    assert "head and tail of the output" in result
    assert "line 0" in result and "DONE" in result and "characters omitted" in result
    assert "Read File Range can page through it" in result
    log_path = result.rsplit("full log: ", 1)[1].split()[0]
    page = toolbox("Read File Range", path=log_path, start_line=15000, max_lines=2)
    assert "line 14999" in page


def test_a_timeout_is_reported_and_the_process_tree_is_gone(toolbox: Toolbox) -> None:
    toolbox.write("slow.py", "import time\nprint('started', flush=True)\ntime.sleep(60)\n")

    result = toolbox("Run Project Command", command="python slow.py", timeout_seconds=1)

    assert result.startswith("Command timed out after") and "process group was killed" in result
    assert "started" in result


def test_command_validation_errors_come_back_as_error_strings(toolbox: Toolbox) -> None:
    assert "not allowed" in toolbox("Run Project Command", command="curl http://x")
    assert "Shell operators" in toolbox("Run Project Command", command="python a.py | cat")
    assert "Inline code" in toolbox("Run Project Command", command="python -c 'print(1)'")


def test_list_scripts_finds_package_make_just_and_pyproject_scripts(toolbox: Toolbox) -> None:
    toolbox.write(
        "package.json",
        json.dumps({"scripts": {"test": "vitest run", "build": "tsc -p ."}}),
    )
    toolbox.write(
        "Makefile", ".PHONY: lint\nCC := gcc\nlint:\n\truff check .\n%.o: %.c\n\tcc\ncheck: lint\n"
    )
    toolbox.write(
        "justfile", "set shell := ['bash']\nfmt:\n  ruff format\nserve port='80':\n  run\n"
    )
    toolbox.write(
        "pyproject.toml", '[project]\nname="x"\n[project.scripts]\nmycli = "x.cli:main"\n'
    )

    result = toolbox("List Scripts")

    assert result.splitlines()[0] == "7 script(s) in .; run one with Run Script"
    for key in (
        "npm:test  -  vitest run",
        "npm:build  -  tsc -p .",
        "make:lint",
        "make:check",
        "just:fmt",
        "just:serve",
        "uv:mycli  -  console script -> x.cli:main",
    ):
        assert key in result
    assert "%.o" not in result and "make:CC" not in result


def test_list_scripts_with_nothing_found_says_what_to_do(toolbox: Toolbox) -> None:
    result = toolbox("List Scripts")

    assert result.startswith("No scripts found") and "Run Project Command" in result
    toolbox.write("package.json", "{broken")
    assert toolbox("List Scripts").startswith("ERROR: package.json is not valid JSON")


def test_run_script_runs_a_make_target_by_bare_or_qualified_name(
    make_toolbox: Callable[..., Toolbox],
) -> None:
    box = make_toolbox(name="proj")
    box.write("Makefile", "hello:\n\t@echo hello from make\n")

    bare = box("Run Script", name="hello")
    qualified = box("Run Script", name="make:hello")

    for result in (bare, qualified):
        assert result.startswith("Exit code: 0") and "hello from make" in result


def test_run_script_errors_name_the_alternatives(toolbox: Toolbox) -> None:
    toolbox.write("Makefile", "test:\n\t@echo make\n")
    toolbox.write("package.json", json.dumps({"scripts": {"test": "echo js"}}))

    ambiguous = toolbox("Run Script", name="test")
    missing = toolbox("Run Script", name="deploy")

    assert "ambiguous" in ambiguous and "npm:test" in ambiguous and "make:test" in ambiguous
    assert "No script named 'deploy'" in missing and "Available:" in missing


def test_run_script_still_enforces_the_command_allowlist(toolbox: Toolbox) -> None:
    toolbox.write("justfile", "hi:\n  echo hi\n")

    result = toolbox("Run Script", name="hi")

    assert result.startswith("ERROR:") and "'just' is not allowed" in result


def test_every_tool_call_emits_a_redacted_tool_call_event(
    make_context: Callable[..., object], tmp_path
) -> None:
    from engineering_team.runtime.context import RunContext
    from engineering_team.tools import build_tools
    from engineering_team.tools.workspace import ProjectWorkspace

    sink = RecordingSink()
    ctx = RunContext.create(load_settings(), ProjectWorkspace.create(tmp_path / "p"), events=sink)
    tools = {tool.name: tool for tool in build_tools(ctx)}

    tools["Write Project File"].run(path="a.txt", content="secret-content" * 100)
    tools["Read Project File"].run(path="missing.txt")

    (first, a), (second, b) = sink.events
    assert first == second == "tool.call"
    assert a["tool"] == "Write Project File" and a["ok"] is True
    assert a["args"] == {"path": "a.txt", "content": "<1400 chars>"}
    assert b["tool"] == "Read Project File" and b["ok"] is False
    assert isinstance(a["duration"], float)


def test_the_tool_gate_can_refuse_calls_for_budgets(
    make_context: Callable[..., object], tmp_path
) -> None:
    import dataclasses

    from engineering_team.runtime.context import RunContext
    from engineering_team.tools import build_tools
    from engineering_team.tools.workspace import ProjectWorkspace

    ctx = RunContext.create(load_settings(), ProjectWorkspace.create(tmp_path / "p"))
    calls: list[str] = []

    def gate(tool: str) -> str | None:
        calls.append(tool)
        return "tool-call budget exhausted" if len(calls) > 1 else None

    gated = dataclasses.replace(ctx, tool_gate=gate)
    tools = {tool.name: tool for tool in build_tools(gated)}

    assert tools["Project Tree"].run().startswith(".: 0 file(s)")
    refused = tools["Project Tree"].run()

    assert refused == "ERROR: tool-call budget exhausted"
    assert calls == ["Project Tree", "Project Tree"]


def test_concurrent_commands_are_limited_by_the_gate(
    make_toolbox: Callable[..., Toolbox],
) -> None:
    box = make_toolbox()
    box.write("t.py", "print('ok')\n")
    gate = box.ctx.command_gate
    holders = box.ctx.settings.execution.max_parallel_commands
    for _ in range(holders):
        gate.acquire()
    results: list[str] = []
    thread = threading.Thread(
        target=lambda: results.append(box("Run Project Command", command="python t.py"))
    )
    thread.start()
    thread.join(timeout=0.5)
    assert thread.is_alive()
    gate.release()
    thread.join(timeout=30)
    assert results and "ok" in results[0]
