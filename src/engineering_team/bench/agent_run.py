"""Run one benchmark run by an external agent (Claude Code, Codex, ...) and judge what it left.

The agent gets what our team gets: a fresh copy of the same fixture (an empty project directory, or
a clean Git repository), the same request text, the same wall-clock limit, and the same hidden
checks, which are never copied into the run. Its command goes through an ``ExecutionBackend``; only
the checks decide the verdict.
"""

from __future__ import annotations

import shutil
import subprocess
import threading
from datetime import UTC, datetime
from pathlib import Path

from engineering_team.bench.agents import build_agent
from engineering_team.bench.execution import ProcessRegistry, base_record, judge
from engineering_team.bench.plan import RunOptions, RunSpec
from engineering_team.bench.prepare import PROJECT, make_repository
from engineering_team.bench.results import RunRecord, write_result
from engineering_team.bench.tasks import BenchError, BenchTask
from engineering_team.execution.local import LocalBackend


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def agent_request(task: BenchTask) -> str:
    """The text the agent is asked: the request, plus what the team is handed beside it (the stack
    trace and the command that shows the bug, for a fix) as plain text."""

    text = task.request_path.read_text(encoding="utf-8").strip()
    if task.trace_file:
        trace = (task.root / task.trace_file).read_text(encoding="utf-8").strip()
        text += f"\n\nThe failure's stack trace:\n\n```\n{trace}\n```"
    if task.repro:
        text += f"\n\nA command that shows the bug: `{task.repro}`"
    return text


def prepare_workspace(spec: RunSpec, run_dir: Path, repo_cache: Path | None = None) -> Path:
    """The run directory and the workspace the agent starts in (nothing is started)."""

    task = spec.task
    run_dir.mkdir(parents=True)
    shutil.copyfile(task.request_path, run_dir / "request.md")
    if task.kind == "greenfield":
        workspace = run_dir / "ws" / PROJECT
        workspace.mkdir(parents=True)
    else:
        workspace = run_dir / "repo"
        make_repository(task, workspace, repo_cache)
    return workspace


def execute_agent_run(
    spec: RunSpec, options: RunOptions, registry: ProcessRegistry, cancel: threading.Event
) -> RunRecord:
    """Make one run by an external agent and judge it; always returns a record."""

    del registry  # the backend ends the agent's process group itself when ``cancel`` is set
    run_dir = options.batch_dir / spec.task.id / f"{spec.strategy}-{spec.repeat}"
    relative = run_dir.relative_to(options.batch_dir).as_posix()
    started = now()
    if run_dir.exists():
        shutil.rmtree(run_dir)
    try:
        workspace = prepare_workspace(spec, run_dir, options.repo_cache)
        request = agent_request(spec.task)
    except (OSError, BenchError, subprocess.CalledProcessError) as exc:
        record = base_record(
            spec, options, outcome="error", reason=f"setup failed: {exc}", started=started,
            finished=now(), run_dir=relative,
        )  # fmt: skip
        run_dir.mkdir(parents=True, exist_ok=True)
        write_result(run_dir, record)
        return record

    name = spec.strategy
    cap = options.cap_for(name)
    agent = build_agent(
        name, LocalBackend(run_dir / "agent-logs", cancel), model=options.agent_models.get(name),
        max_cost_usd=cap, billing=options.agent_billing.get(name),  # type: ignore[arg-type]
    )  # fmt: skip
    version = options.agent_versions.get(name) or agent.version()
    outcome = agent.run(workspace, request, spec.task.timeout_seconds)
    if outcome.exit_code is None and not outcome.timed_out and not outcome.cancelled:
        record = base_record(
            spec, options, outcome="error", reason=f"could not run {name}: {outcome.error}",
            agent_version=version, billing=outcome.billing, started=started, finished=now(),
            run_dir=relative,
        )  # fmt: skip
        write_result(run_dir, record)
        return record

    report = outcome.report
    criteria, verdict_outcome, reason = judge(
        spec, options, workspace, timed_out=outcome.timed_out, interrupted=outcome.cancelled,
        exit_code=outcome.exit_code, ended=report.error or f"exit code {outcome.exit_code}",
        who=name,
    )  # fmt: skip
    plan_billed = outcome.billing == "subscription"
    pinned = options.agent_models.get(name)
    record = base_record(
        spec, options, outcome=verdict_outcome, reason=reason, agent_version=version,
        exit_code=outcome.exit_code, duration_seconds=outcome.wall_s,
        tokens=report.prompt_tokens + report.completion_tokens,
        prompt_tokens=report.prompt_tokens, completion_tokens=report.completion_tokens,
        cached_prompt_tokens=report.cached_prompt_tokens, sessions=1, model_calls=report.turns,
        cost_usd=None if plan_billed else outcome.cost_usd,
        equivalent_cost_usd=outcome.cost_usd if plan_billed else None,
        billing=outcome.billing, models=list(report.models) or ([pinned] if pinned else []),
        criteria=criteria, started=started, finished=now(), run_dir=relative,
        workspace=workspace.relative_to(options.batch_dir).as_posix(),
    )  # fmt: skip
    write_result(run_dir, record)
    return record
