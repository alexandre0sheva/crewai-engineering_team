"""The per-run context that replaces process-wide globals."""

from __future__ import annotations

import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from engineering_team.execution.backend import ExecutionBackend
from engineering_team.execution.local import LocalBackend
from engineering_team.pricing import PriceTable
from engineering_team.runtime.budget import Budget, BudgetGuard
from engineering_team.runtime.events import EventSink, FanoutSink, JsonlSink, Scrubber
from engineering_team.runtime.run_store import EVENTS_FILENAME
from engineering_team.runtime.snapshot import Snapshot
from engineering_team.runtime.usage import UsageTracker
from engineering_team.settings import Settings, secret_values
from engineering_team.tools.workspace import CONTROLLER_DIRECTORY, ProjectWorkspace

# Called with a tool's name before it runs; returning a message refuses the call (the tool
# returns it as an ``ERROR:``). The budget guard installs one to stop runaway tool use.
ToolGate = Callable[[str], str | None]


def new_run_id() -> str:
    """A unique id that sorts by creation time: ``YYYYMMDD-HHMMSS-<6 hex>``."""

    return f"{datetime.now(UTC):%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"


@dataclass(frozen=True, eq=False)
class RunContext:
    """Everything one run needs: settings, workspace, state directory, and shared controls.

    Tools, crews, and (later) strategies receive this explicitly; nothing reads a module
    global. ``command_gate`` caps concurrent project commands, and ``cancel_event`` asks
    every worker of the run to stop at its next safe point. ``backend`` executes commands,
    ``baseline`` is the workspace as it was when the run started (for change reports),
    ``events`` receives structured events such as ``tool.call`` and also feeds ``usage`` (token
    and tool-call accounting) and ``budget`` (the guard that stops an overspending run);
    ``tool_gate`` is how the guard refuses tool calls.
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
    tool_gate: ToolGate | None = None

    @classmethod
    def create(
        cls,
        settings: Settings,
        workspace: ProjectWorkspace,
        *,
        run_id: str | None = None,
        backend: ExecutionBackend | None = None,
        events: EventSink | None = None,
    ) -> RunContext:
        """Create the context and its run directory (controller-owned, hidden from agents).

        Snapshots the workspace as the baseline for change reports. The default backend runs
        commands locally, logging to ``run_dir/commands/``.
        """

        run_id = run_id or new_run_id()
        run_dir = workspace.root / CONTROLLER_DIRECTORY / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        cancel_event = threading.Event()
        usage = UsageTracker()
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
        return cls(
            run_id=run_id,
            settings=settings,
            workspace=workspace,
            run_dir=run_dir,
            command_gate=threading.BoundedSemaphore(settings.execution.max_parallel_commands),
            cancel_event=cancel_event,
            backend=backend or LocalBackend(run_dir / "commands", cancel_event),
            baseline=Snapshot.take(workspace),
            events=sink,
            usage=usage,
            budget=guard,
            prices=prices,
            tool_gate=guard.tool_gate,
        )
