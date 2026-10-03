"""The per-run context that replaces process-wide globals."""

from __future__ import annotations

import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from engineering_team.execution.backend import ExecutionBackend
from engineering_team.execution.factory import create_backend
from engineering_team.pricing import PriceTable
from engineering_team.runtime.browsers import BrowserDriver, BrowserRegistry
from engineering_team.runtime.budget import Budget, BudgetGuard
from engineering_team.runtime.events import (
    EventSink,
    FanoutSink,
    JsonlSink,
    Scrubber,
    read_events,
)
from engineering_team.runtime.interaction import HumanChannel
from engineering_team.runtime.processes import ProcessRegistry
from engineering_team.runtime.reports import Reports
from engineering_team.runtime.requests import RateLimiter, RequestLimiter
from engineering_team.runtime.run_store import EVENTS_FILENAME
from engineering_team.runtime.snapshot import Snapshot
from engineering_team.runtime.usage import UsageTracker
from engineering_team.settings import Settings, secret_values
from engineering_team.tools.workspace import CONTROLLER_DIRECTORY, ProjectWorkspace

if TYPE_CHECKING:  # imported when a context is created: ``board`` itself imports ``runtime``
    from engineering_team.board.notes import NoteStore
    from engineering_team.board.store import BoardStore
    from engineering_team.git.port import GitPort
    from engineering_team.team import Roster

# Called with a tool's name before it runs; returning a message refuses the call (the tool
# returns it as an ``ERROR:``). The budget guard installs one to stop runaway tool use.
ToolGate = Callable[[str], str | None]


def new_run_id() -> str:
    """A unique id that sorts by creation time: ``YYYYMMDD-HHMMSS-<6 hex>``."""

    return f"{datetime.now(UTC):%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"


def _browser_driver(
    settings: Settings, processes: ProcessRegistry, events: EventSink, run_dir: Path
) -> BrowserDriver:
    """Start the Playwright driver (imported here so a run without the extra never loads it)."""

    from engineering_team.browsertools.driver import create_driver

    return create_driver(settings, processes, events, run_dir)


@dataclass(frozen=True, eq=False)
class RunContext:
    """Everything one run needs: settings, workspace, state directory, and shared controls.

    Tools, crews, and (later) strategies receive this explicitly; nothing reads a module
    global. ``command_gate`` caps concurrent project commands, and ``cancel_event`` asks
    every worker of the run to stop at its next safe point. ``backend`` executes commands,
    ``baseline`` is the workspace as it was when the run started (for change reports),
    ``events`` receives structured events such as ``tool.call`` and also feeds ``usage`` (token
    and tool-call accounting) and ``budget`` (the guard that stops an overspending run);
    ``tool_gate`` is how the guard refuses tool calls. ``board`` is the run's task board
    (controller-owned; see ``docs/ARCHITECTURE.md``), ``notes`` its shared notes and decision
    log, ``human`` the line to the person running it, and ``processes`` the run's background
    processes and ports (all stopped when their stage or the run ends), ``browsers`` the run's
    headless browser (closed with its stage or the run), and ``web_requests`` the run's shared
    cap on outbound web requests, ``git`` the controller's Git (checkpoints, patches, history;
    see ``docs/ARCHITECTURE.md``), and ``llm_rate`` (set when ``parallel.max_rpm`` is) the cap on
    model calls per minute that parallel agents share, and ``team`` the roster of teammates
    (the built-ins plus the project's changes and additions; see ``docs/TEAM.md``), and ``reports``
    where the controller's own write-ups go (the project's ``docs/``, or the run directory for a
    project the team did not create).
    """

    run_id: str
    settings: Settings
    workspace: ProjectWorkspace
    run_dir: Path
    command_gate: threading.BoundedSemaphore
    cancel_event: threading.Event
    backend: ExecutionBackend
    baseline: Snapshot
    events: EventSink
    usage: UsageTracker
    budget: BudgetGuard
    prices: PriceTable
    board: BoardStore
    notes: NoteStore
    human: HumanChannel
    processes: ProcessRegistry
    browsers: BrowserRegistry
    git: GitPort
    team: Roster
    reports: Reports
    tool_gate: ToolGate | None = None
    web_requests: RequestLimiter = field(default_factory=lambda: RequestLimiter(40))
    llm_rate: RateLimiter | None = None

    @classmethod
    def create(
        cls,
        settings: Settings,
        workspace: ProjectWorkspace,
        *,
        run_id: str | None = None,
        backend: ExecutionBackend | None = None,
        events: EventSink | None = None,
        resume: bool = False,
        adopted: bool = False,
    ) -> RunContext:
        """Create the context and its run directory (controller-owned, hidden from agents).

        Snapshots the workspace as the baseline for change reports. The default backend runs
        commands as ``settings.execution.backend`` says (locally by default, or in the Docker
        sandbox; an unavailable Docker is an ``ExecutionUnavailable``), logging to
        ``run_dir/commands/``. ``resume=True`` continues an
        existing run directory: usage (and so the cost and token budgets) starts from what
        ``events.jsonl`` already recorded, the board and notes load from disk, and the event
        log is appended to. The baseline is then the workspace as it is now. ``adopted`` keeps the
        controller's reports in the run directory instead of the project's ``docs/``.
        """

        from engineering_team.team import build_roster

        team = build_roster(settings)  # a bad team definition is a usage error, before any state
        from engineering_team.board.notes import NoteStore
        from engineering_team.board.store import BoardStore
        from engineering_team.git.port import GitPort

        run_id = run_id or new_run_id()
        run_dir = workspace.root / CONTROLLER_DIRECTORY / "runs" / run_id
        cancel_event = threading.Event()
        # Before the run directory exists: a backend that cannot start (Docker missing) must
        # leave no empty run behind.
        run_backend = backend or create_backend(
            settings, workspace.root, run_dir / "commands", run_id, cancel_event
        )
        run_dir.mkdir(parents=True, exist_ok=True)
        usage = (
            UsageTracker.from_events(read_events(run_dir / EVENTS_FILENAME))
            if resume
            else UsageTracker()
        )
        prices = settings.price_table()
        guard = BudgetGuard(Budget.from_settings(settings.budget), usage, prices, cancel_event)
        # The usage tracker must see an event before the guard evaluates it.
        sink = FanoutSink(
            events
            or JsonlSink(run_dir / EVENTS_FILENAME, run_id, scrubber=Scrubber(secret_values())),
            usage,
            guard,
        )
        guard.attach(sink)
        controller_dir = workspace.root / CONTROLLER_DIRECTORY
        processes = ProcessRegistry(
            run_backend,
            cancel_event,
            sink,
            max_processes=settings.runtime.max_background_processes,
            max_lifetime=settings.runtime.process_lifetime_seconds,
        )
        return cls(
            run_id=run_id,
            settings=settings,
            workspace=workspace,
            run_dir=run_dir,
            command_gate=threading.BoundedSemaphore(settings.execution.max_parallel_commands),
            cancel_event=cancel_event,
            backend=run_backend,
            baseline=Snapshot.take(workspace),
            events=sink,
            usage=usage,
            budget=guard,
            prices=prices,
            board=BoardStore(
                run_dir,
                sink,
                run_id=run_id,
                max_in_progress=settings.parallel.max_parallel_agents,
            ),
            notes=NoteStore(run_dir / "notes", controller_dir / "decisions.md", sink),
            human=HumanChannel(sink),
            processes=processes,
            browsers=BrowserRegistry(
                max_contexts=settings.browser.max_contexts,
                driver_factory=lambda: _browser_driver(settings, processes, sink, run_dir),
                cancel_event=cancel_event,
                events=sink,
                name=run_id,
            ),
            git=GitPort(workspace, run_backend, settings.git, sink),
            team=team,
            reports=(Reports.in_run_dir(workspace, run_dir) if adopted else Reports(workspace)),
            tool_gate=guard.tool_gate,
            web_requests=RequestLimiter(settings.web.max_requests_per_run),
            llm_rate=(
                RateLimiter(settings.parallel.max_rpm, cancel_event)
                if settings.parallel.max_rpm
                else None
            ),
        )
