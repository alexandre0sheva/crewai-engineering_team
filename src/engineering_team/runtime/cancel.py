"""Cancelling a run: the stop signal, SIGINT/SIGTERM handling, and the ``cancel`` flag file.

Cancellation is cooperative. Anything that wants the run to stop sets ``ctx.cancel_event``;
tools then refuse to work (telling the agent to wrap up) and the controller stops at its next
safe point (between and after stages). Three things set it: a first SIGINT or a SIGTERM (a
second SIGINT raises ``KeyboardInterrupt`` for an immediate stop), the file
``runs/<run_id>/cancel`` that ``engineering-team cancel`` writes for a run in another
process, and the budget guard.
"""

from __future__ import annotations

import contextlib
import logging
import signal
import sys
import threading
from collections.abc import Iterator
from pathlib import Path
from types import FrameType
from typing import TYPE_CHECKING, Any

from engineering_team.runtime.run_store import RunStore

if TYPE_CHECKING:
    from engineering_team.runtime.context import RunContext

log = logging.getLogger(__name__)

CANCEL_FLAG = "cancel"
POLL_SECONDS = 0.5


class RunCancelled(Exception):
    """Raised at a safe point when the run was asked to stop (not for a budget stop)."""


def cancel_flag_path(workspace_root: Path, run_id: str) -> Path:
    return RunStore(workspace_root).run_dir(run_id) / CANCEL_FLAG


def request_cancel(workspace_root: Path, run_id: str) -> Path:
    """Ask the run (in whichever process) to stop at its next safe point.

    Raises :class:`~engineering_team.runtime.run_store.RunNotFound` for an unknown run.
    """

    store = RunStore(workspace_root)
    store.load(run_id)
    flag = cancel_flag_path(workspace_root, run_id)
    flag.write_text("cancel requested\n", encoding="utf-8")
    return flag


def clear_cancel_flag(workspace_root: Path, run_id: str) -> None:
    """Forget an earlier request (a resumed run must not stop at once)."""

    with contextlib.suppress(OSError):
        cancel_flag_path(workspace_root, run_id).unlink()


def check_cancelled(ctx: RunContext) -> None:
    """Raise :class:`RunCancelled` if the run was told to stop (a budget stop raises
    ``BudgetExceeded`` instead, through ``ctx.budget.check()``)."""

    if ctx.cancel_event.is_set():
        ctx.budget.check()
        raise RunCancelled("The run was cancelled.")


def _watch(ctx: RunContext, stop: threading.Event) -> None:
    flag = cancel_flag_path(ctx.workspace.root, ctx.run_id)
    while not stop.wait(POLL_SECONDS):
        if flag.exists():
            ctx.events.emit("run.cancel_requested", via="cancel flag")
            ctx.cancel_event.set()
            return


@contextlib.contextmanager
def cancellation(ctx: RunContext) -> Iterator[None]:
    """Make SIGINT/SIGTERM and the cancel flag stop ``ctx``'s run for the duration.

    Signal handlers can only be installed from the main thread; elsewhere only the flag file
    (and ``ctx.cancel_event`` itself) work. Previous handlers are restored on exit.
    """

    stop = threading.Event()
    watcher = threading.Thread(
        target=_watch, args=(ctx, stop), name=f"cancel-watch-{ctx.run_id}", daemon=True
    )
    clear_cancel_flag(ctx.workspace.root, ctx.run_id)
    previous: dict[signal.Signals, Any] = {}

    def handle(number: int, frame: FrameType | None) -> None:
        if ctx.cancel_event.is_set() and number == signal.SIGINT:
            raise KeyboardInterrupt  # the second Ctrl-C: stop now
        ctx.events.emit("run.cancel_requested", via=signal.Signals(number).name)
        ctx.cancel_event.set()
        if number == signal.SIGINT:
            print(
                "\nCancelling at the next safe point; press Ctrl-C again to stop immediately.",
                file=sys.stderr,
            )

    if threading.current_thread() is threading.main_thread():
        for installed in (signal.SIGINT, signal.SIGTERM):
            previous[installed] = signal.signal(installed, handle)
    watcher.start()
    try:
        yield
    finally:
        stop.set()
        watcher.join(timeout=2)
        for restored, handler in previous.items():
            signal.signal(restored, handler)
