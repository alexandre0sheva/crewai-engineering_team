"""The end-of-run summary: one data structure, rendered as a panel or printed as JSON."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rich import box
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from engineering_team.contracts import utc_now
from engineering_team.pipeline.state import PipelineState, RunResult
from engineering_team.runtime.context import RunContext
from engineering_team.runtime.run_store import RunStore
from engineering_team.runtime.session import format_summary
from engineering_team.runtime.snapshot import Snapshot

STATUS_STYLE = {
    "succeeded": "bold green",
    "verified": "bold green",
    "failed": "bold red",
    "cancelled": "yellow",
    "interrupted": "yellow",
    "partial": "yellow",
}
CHECK_STYLE = {"passed": "green", "failed": "red", "skipped": "yellow", "unavailable": "yellow"}
REPORT_CANDIDATES = ("docs/verification.md", "docs/release-report.md")


def file_changes(ctx: RunContext) -> dict[str, int]:
    """Files added, modified, and deleted since the run started (a snapshot comparison)."""

    before, after = ctx.baseline.files, Snapshot.take(ctx.workspace).files
    return {
        "added": len(set(after) - set(before)),
        "modified": sum(1 for n in set(before) & set(after) if before[n].sha256 != after[n].sha256),
        "deleted": len(set(before) - set(after)),
    }


def next_steps(run_id: str, status: str, verdict: str | None, resumable: bool) -> list[str]:
    steps: list[str] = []
    if status in ("failed", "cancelled", "interrupted") and resumable:
        steps.append(f"engineering-team resume {run_id}")
    if verdict in ("failed", "partial"):
        steps.append(f"engineering-team board {run_id}   (what failed)")
    steps.append(f"engineering-team status {run_id}")
    return steps


def build(ctx: RunContext, result: RunResult, exit_code: int, *, resumable: bool) -> dict[str, Any]:
    """Everything the summary shows, as JSON-ready data."""

    report = ctx.usage.report(ctx.prices)
    manifest = RunStore(ctx.workspace.root).load(ctx.run_id)
    run_summary = manifest.summary
    state = PipelineState.load(ctx.run_dir)
    checks = state.checks if state else []
    workspace = Path(result.workspace or ctx.workspace.root)
    shown_report = next((p for p in REPORT_CANDIDATES if (workspace / p).is_file()), None)
    return {
        "run_id": result.run_id,
        "project": ctx.settings.project_name,
        "status": result.status,
        "verdict": result.verdict,
        "exit_code": exit_code,
        "error": result.error or None,
        "duration_seconds": _duration(ctx),
        "usage": {
            "tokens": report.totals.total_tokens,
            "prompt_tokens": report.totals.prompt_tokens,
            "completion_tokens": report.totals.completion_tokens,
            "model_calls": report.totals.calls,
            "tool_calls": report.tool_calls,
            "estimated_cost_usd": report.estimated_cost_usd,
            "unpriced_models": report.unpriced_models,
        },
        "usage_text": format_summary(run_summary).splitlines() if run_summary else [],
        "budget": run_summary.budget_status.model_dump(mode="json") if run_summary else None,
        "files_changed": file_changes(ctx),
        "stages": [
            {"name": s.name, "status": s.status, "attempts": s.attempts} for s in result.stages
        ],
        "checks": [
            {
                "id": c.id,
                "name": c.name or c.id,
                "status": c.status,
                "required": c.required,
                "summary": c.summary or c.hint or "",
            }
            for c in checks
        ],
        "workspace": str(workspace),
        "report": str(workspace / shown_report) if shown_report else None,
        "next_steps": next_steps(result.run_id, result.status, result.verdict, resumable),
    }


def _duration(ctx: RunContext) -> float:
    """Seconds from the run's creation to its end (or to now), from its manifest."""

    manifest = RunStore(ctx.workspace.root).load(ctx.run_id)
    return round(((manifest.finished or utc_now()) - manifest.created).total_seconds(), 1)


def money(value: float | None) -> str:
    return f"${value:.4f}" if value is not None else "unknown"


def render_plain(data: dict[str, Any], console: Console) -> None:
    """The summary as unwrapped plain lines (for pipes, logs, and ``--quiet``)."""

    shown = data["verdict"] or data["status"]
    lines = [f"Run {data['run_id']}: {shown}", f"Duration: {_clock(data['duration_seconds'])}"]
    lines += data["usage_text"] or [f"Estimated cost: {money(data['usage']['estimated_cost_usd'])}"]
    files = data["files_changed"]
    lines.append(
        f"Files: {files['added']} added, {files['modified']} modified, {files['deleted']} deleted"
    )
    lines.append(f"Workspace: {data['workspace']}")
    if data["report"]:
        lines.append(f"Report: {data['report']}")
    if data["error"]:
        lines.append(f"Error: {data['error']}")
    for check in data["checks"]:
        required = "required" if check["required"] else "optional"
        lines.append(
            f"Check {check['name']}: {check['status']} ({required}) {check['summary']}".rstrip()
        )
    lines.extend(f"Next: {step}" for step in data["next_steps"])
    for line in lines:
        console.print(line, markup=False, highlight=False, soft_wrap=True)


def render(data: dict[str, Any], console: Console) -> None:
    """Print the summary panel."""

    verdict = data["verdict"]
    status = data["status"]
    shown = verdict or status
    title = Text.assemble(
        "Run ", (data["run_id"], "bold"), " ", (shown, STATUS_STYLE.get(shown, ""))
    )
    usage, files = data["usage"], data["files_changed"]
    facts = Table.grid(padding=(0, 2))
    facts.add_column(style="dim")
    facts.add_column()
    facts.add_row("Duration", _clock(data["duration_seconds"]))
    lines = data["usage_text"] or [
        f"Usage: {usage['tokens']:,} tokens in {usage['model_calls']} model call(s) and "
        f"{usage['tool_calls']} tool call(s)",
        f"Estimated cost: {money(usage['estimated_cost_usd'])}",
    ]
    facts.add_row("Usage", Text("\n".join(lines), overflow="fold"))
    facts.add_row(
        "Files",
        f"{files['added']} added · {files['modified']} modified · {files['deleted']} deleted",
    )
    facts.add_row("Workspace", Text(data["workspace"], overflow="fold"))
    if data["report"]:
        facts.add_row("Report", Text(data["report"], overflow="fold"))
    parts: list[Any] = [facts]
    if data["error"]:
        parts.append(Text(f"\n{data['error']}", style="red"))
    if data["checks"]:
        table = Table(
            box=box.SIMPLE_HEAD, pad_edge=False, title="Independent checks", title_justify="left"
        )
        for name in ("Check", "Result", "Required", "Summary"):
            table.add_column(name)
        for check in data["checks"]:
            table.add_row(
                check["name"],
                Text(check["status"], style=CHECK_STYLE.get(check["status"], "")),
                "yes" if check["required"] else "no",
                check["summary"],
            )
        parts.extend([Text(), table])
    parts.append(Text("\nNext: " + "\n      ".join(data["next_steps"]), style="dim"))
    console.print(Panel(Group(*parts), title=title, title_align="left", box=box.ROUNDED))


def _clock(seconds: float) -> str:
    minutes, rest = divmod(int(seconds), 60)
    return f"{minutes}m{rest:02d}s" if minutes else f"{rest}s"
