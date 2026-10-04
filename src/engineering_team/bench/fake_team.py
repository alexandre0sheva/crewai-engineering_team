"""The offline "team": replays a task's reference solution through scripted tools.

``bench run --fake`` starts ``python -m engineering_team.bench.fake_team`` instead of the real
CLI. It builds a real CrewAI ``Agent`` on a :class:`ScriptedLLM` whose script is one
``Write Project File`` call per reference file, with the real tools bound to a real ``RunContext``,
so the tool layer (path rules, protected paths, events, usage) is exercised without a model.
``--solution broken`` applies the task's ``sabotage`` to the reference first. The output is a JSON
summary shaped like ``new --json``, so the harness treats both teams the same way.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from engineering_team.bench.tasks import BenchError, BenchTask, load_task

FAKE_MODEL = "ollama/qwen3.8:27b"  # a priced (free) model name, so the cost reads $0.00
WRITE_TOOL = "Write Project File"
SKIP_SUFFIXES = (".pyc",)


def reference_files(task: BenchTask, solution: str = "reference") -> dict[str, str]:
    """The files the fake team writes: the reference, or the reference after the task's sabotage."""

    if solution not in ("reference", "broken"):
        raise BenchError(f"solution must be 'reference' or 'broken', not {solution!r}.")
    files: dict[str, str] = {}
    root = task.reference_dir
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if (
            path.is_file()
            and "__pycache__" not in relative.parts
            and path.suffix not in SKIP_SUFFIXES
        ):
            files[relative.as_posix()] = path.read_text(encoding="utf-8")
    if solution == "broken":
        for edit in task.sabotage.edits:
            if edit.path not in files:
                raise BenchError(f"Task {task.id}: sabotage edits {edit.path}, not in reference/.")
            found = files[edit.path].count(edit.old)
            if found != 1:
                raise BenchError(
                    f"Task {task.id}: sabotage text must occur once in {edit.path}, found {found}."
                )
            files[edit.path] = files[edit.path].replace(edit.old, edit.new)
    return files


def _quiet_dotenv() -> None:
    """CrewAI loads a ``.env`` at import time; an offline run must not read the developer's."""

    import dotenv

    dotenv.load_dotenv = lambda *args, **kwargs: False


def replay(files: dict[str, str], workspace: Path, run_id: str) -> dict[str, Any]:
    """Write ``files`` into ``workspace`` with an agent on a scripted model; returns a summary."""

    _quiet_dotenv()
    from engineering_team.runtime.bridge import bind_run, flush_bridge
    from engineering_team.runtime.context import RunContext
    from engineering_team.settings import load_settings
    from engineering_team.testing import ScriptedLLM, ToolCall, Turn, run_agent_task
    from engineering_team.tools.workspace import ProjectWorkspace

    workspace.mkdir(parents=True, exist_ok=True)
    ctx = RunContext.create(load_settings(), ProjectWorkspace.create(workspace), run_id=run_id)
    ctx.events.emit("run.started", mode="bench-fake")
    script: list[Any] = [
        Turn(ToolCall(WRITE_TOOL, {"path": path, "content": content}), 400, 120)
        for path, content in files.items()
    ]
    script.append(Turn("Wrote the reference solution.", 300, 20))
    llm = ScriptedLLM(script, model=FAKE_MODEL)
    error = ""
    try:
        with bind_run(run_id, ctx.events):
            run_agent_task(
                ctx,
                llm,
                "Write the project files.",
                max_iter=len(files) + 5,
                role="Reference Engineer",
            )
            flush_bridge()
    except Exception as exc:  # the harness records a team that fails; it does not crash with it
        error = f"{type(exc).__name__}: {exc}"
    missing = [
        path
        for path, content in files.items()
        if not (workspace / path).is_file()
        or (workspace / path).read_text(encoding="utf-8") != content
    ]
    if missing and not error:
        error = f"{len(missing)} file(s) were not written as scripted: {', '.join(missing[:5])}"
    ok = not error
    ctx.events.emit("run.finished", status="succeeded" if ok else "failed")
    report = ctx.usage.report(ctx.prices)
    return {
        "run_id": run_id,
        "status": "succeeded" if ok else "failed",
        "verdict": "verified" if ok else "failed",
        "exit_code": 0 if ok else 1,
        "error": error or None,
        "fake": True,
        "usage": {
            "tokens": report.totals.total_tokens,
            "prompt_tokens": report.totals.prompt_tokens,
            "completion_tokens": report.totals.completion_tokens,
            "model_calls": report.totals.calls,
            "tool_calls": report.tool_calls,
            "estimated_cost_usd": report.estimated_cost_usd,
        },
        "workspace": str(workspace),
    }


def main(argv: list[str] | None = None) -> int:
    # CrewAI prints console panels to stdout, some of them late. Keep a private handle on the real
    # stdout for the JSON summary and send everything else on descriptor 1 to stderr.
    summary_out = os.fdopen(os.dup(1), "w", encoding="utf-8")
    os.dup2(2, 1)
    parser = argparse.ArgumentParser(prog="engineering_team.bench.fake_team")
    parser.add_argument("--task-dir", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--solution", choices=("reference", "broken"), default="reference")
    args = parser.parse_args(argv)
    try:
        files = reference_files(load_task(args.task_dir), args.solution)
    except BenchError as exc:
        summary_out.write(json.dumps({"status": "error", "error": str(exc), "exit_code": 2}) + "\n")
        summary_out.flush()
        return 2
    summary = replay(files, args.workspace, args.run_id)
    summary_out.write("\n" + json.dumps(summary) + "\n")
    summary_out.flush()
    return int(summary["exit_code"])


if __name__ == "__main__":
    sys.exit(main())
