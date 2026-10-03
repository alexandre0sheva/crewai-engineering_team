"""Showing a run: the live display, the plain log, and the pieces they share.

All three read the run through :class:`~engineering_team.cli.snapshot.RunWatcher`, so the same
code shows a run in this process and ``board --watch`` shows one in another.
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from engineering_team.cli.kanban import render_kanban
from engineering_team.cli.snapshot import FeedLine, RunWatcher, ViewState
from engineering_team.pricing import PriceTable
from engineering_team.runtime.budget import Budget

FEED_ROWS = 8
BAR_WIDTH = 24
LEVEL_STYLE = {"info": "", "good": "green", "warn": "yellow", "bad": "red"}


@dataclass(frozen=True)
class RunInfo:
    """What the display needs that is not in the run directory's live files."""

    project: str
    strategy: str
    budget: Budget
    prices: PriceTable


def clock(seconds: float) -> str:
    minutes, rest = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    return f"{minutes}m{rest:02d}s" if minutes else f"{rest}s"


def progress_bar(percent: float, width: int = BAR_WIDTH) -> str:
    filled = round(width * max(0.0, min(100.0, percent)) / 100)
    return "█" * filled + "░" * (width - filled)


def budget_parts(budget: Budget, state: ViewState) -> list[str]:
    """``tokens 41% of 30,000``-style fragments for every limit that is set."""

    usage = state.usage
    parts: list[str] = []
    if budget.max_tokens:
        used = usage.totals.total_tokens / budget.max_tokens
        parts.append(f"tokens {used:.0%} of {budget.max_tokens:,}")
    if budget.max_cost_usd and usage.estimated_cost_usd is not None:
        used = usage.estimated_cost_usd / budget.max_cost_usd
        parts.append(f"cost {used:.0%} of ${budget.max_cost_usd:g}")
    if budget.max_tool_calls:
        used = usage.tool_calls / budget.max_tool_calls
        parts.append(f"tool calls {used:.0%} of {budget.max_tool_calls:,}")
    if budget.max_wall_seconds:
        used = state.elapsed_seconds / budget.max_wall_seconds
        parts.append(f"time {used:.0%} of {clock(budget.max_wall_seconds)}")
    return parts


def header(state: ViewState, info: RunInfo) -> RenderableType:
    status = state.status + (" · paused" if state.paused else "")
    status += " · cancelling" if state.cancel_requested and state.running else ""
    title = Text.assemble(
        ("Run ", "dim"),
        (state.run_id, "bold"),
        f"  {info.project} · {info.strategy} · ",
        (status, "bold"),
    )
    progress = state.progress
    if progress.cards_total:
        bar = (
            f"{progress_bar(progress.overall_percent)} {progress.overall_percent:.0f}%  "
            f"{progress.cards_done}/{progress.cards_total} cards"
        )
    else:
        done = sum(
            1 for s in (state.manifest.stages if state.manifest else []) if s.status == "succeeded"
        )
        bar = f"{done} stage(s) finished"
    stage = f"  ·  stage {state.stage}" if state.stage and state.running else ""
    usage = state.usage
    cost = (
        f"${usage.estimated_cost_usd:.4f}"
        if usage.estimated_cost_usd is not None
        else "cost unknown"
    )
    facts = [
        f"elapsed {clock(state.elapsed_seconds)}",
        f"{usage.totals.total_tokens:,} tokens",
        cost,
        *budget_parts(info.budget, state),
    ]
    return Panel(
        Group(title, Text(bar + stage), Text(" · ".join(facts), style="dim")), padding=(0, 1)
    )


def lanes_table(state: ViewState) -> RenderableType | None:
    if not state.lanes:
        return None
    table = Table.grid(padding=(0, 2))
    for lane in state.lanes:
        table.add_row(
            Text(f"lane {lane.lane}", style="bold"),
            lane.unit,
            Text(lane.agent, style="dim"),
            Text(lane.last_tool or "starting…", style="cyan"),
        )
    return Group(Text("Lanes", style="bold"), table)


def feed_view(lines: list[FeedLine], rows: int = FEED_ROWS) -> RenderableType:
    out = Text()
    for index, line in enumerate(lines[-rows:]):
        if index:
            out.append("\n")
        out.append(line.ts.astimezone().strftime("%H:%M:%S "), style="dim")
        out.append(line.text, style=LEVEL_STYLE.get(line.level, ""))
    return Group(Text("Activity", style="bold"), out) if lines else Text("")


def render_run(
    state: ViewState, info: RunInfo, *, width: int, rows: int = FEED_ROWS
) -> RenderableType:
    """The whole live screen for one moment."""

    parts: list[RenderableType] = [
        header(state, info),
        render_kanban(state.cards, width=width, now=state.now),
    ]
    lanes = lanes_table(state)
    if lanes is not None:
        parts.append(lanes)
    parts.append(feed_view(state.feed, rows))
    return Group(*parts)


# -- the displays ---------------------------------------------------------------------------


class LiveDisplay:
    """A Rich ``Live`` screen refreshed from the run directory until the block exits."""

    def __init__(self, console: Console, info: RunInfo, run_dir: Path) -> None:
        self._console = console
        self._info = info
        self.watcher = RunWatcher(run_dir, info.prices)

    def _render(self) -> RenderableType:
        return render_run(self.watcher.poll(), self._info, width=self._console.width)

    @contextlib.contextmanager
    def __call__(self) -> Iterator[None]:
        with Live(
            get_renderable=self._render,
            console=self._console,
            refresh_per_second=4,
            transient=False,
        ):
            yield


class PlainDisplay:
    """Timestamped log lines, one per notable event: for pipes, CI, and ``--quiet``."""

    def __init__(self, stream: Console, run_dir: Path, prices: PriceTable, *, quiet: bool) -> None:
        self._out = stream
        self._quiet = quiet
        self.watcher = RunWatcher(run_dir, prices)

    def _flush(self) -> None:
        self.watcher.poll()
        for line in self.watcher.new_lines:
            if self._quiet and line.level == "info":
                continue
            stamp = line.ts.astimezone().strftime("%Y-%m-%d %H:%M:%S")
            self._out.print(f"{stamp} {line.text}", markup=False, highlight=False, soft_wrap=True)

    @contextlib.contextmanager
    def __call__(self) -> Iterator[None]:
        stop = threading.Event()

        def loop() -> None:
            while not stop.wait(0.5):
                self._flush()

        thread = threading.Thread(target=loop, name="plain-log", daemon=True)
        thread.start()
        try:
            yield
        finally:
            stop.set()
            thread.join(timeout=2)
            self._flush()
