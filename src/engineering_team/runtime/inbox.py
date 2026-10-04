"""Commands for a run that lives in another process: steering notes, pause, unpause.

``engineering-team note|pause|unpause`` cannot reach the running controller's memory, so they drop
a small JSON file into ``runs/<run_id>/inbox/``. The run polls that directory (like the cancel
flag), applies each command to its board in file-name order, and deletes the file. A command
posted for a run nobody is working on waits there and is applied when the run is resumed. Every
file is validated when it is read: the inbox is a trust boundary like any other input.
"""

from __future__ import annotations

import contextlib
import json
import secrets
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

from engineering_team.atomic_io import atomic_write_json
from engineering_team.board.rules import BoardError

if TYPE_CHECKING:
    from engineering_team.runtime.context import RunContext

INBOX_DIRECTORY = "inbox"
POLL_SECONDS = 0.5
KINDS = ("note", "pause", "unpause", "answer")
MAX_FILE_BYTES = 20_000
USER = "user"


def inbox_dir(run_dir: Path) -> Path:
    return run_dir / INBOX_DIRECTORY


def post_command(
    run_dir: Path,
    kind: str,
    *,
    text: str = "",
    card: str | None = None,
    question: str | None = None,
) -> Path:
    """Queue a command for the run in ``run_dir``; returns the file written. An ``answer``
    names the ``question`` id and carries the ``text`` (empty: decline, the team assumes)."""

    if kind not in KINDS:
        raise ValueError(f"Unknown command {kind!r}; expected one of {', '.join(KINDS)}.")
    if kind == "note" and not text.strip():
        raise ValueError("A note needs some text.")
    payload: dict[str, Any] = {"kind": kind}
    if kind == "note":
        payload["text"] = text.strip()
        if card:
            payload["card"] = card.strip()
    if kind == "answer":
        if not question or not question.strip():
            raise ValueError("An answer names the question it answers.")
        payload["question"] = question.strip()
        payload["text"] = text.strip()
    # Time-ordered names, so commands apply in the order they were posted.
    path = inbox_dir(run_dir) / f"{time.time_ns():020d}-{secrets.token_hex(2)}.json"
    atomic_write_json(path, payload)
    return path


def pending_commands(run_dir: Path) -> list[Path]:
    try:
        return sorted(inbox_dir(run_dir).glob("*.json"))
    except OSError:
        return []


def apply_pending(ctx: RunContext) -> int:
    """Apply every queued command to ``ctx``'s board; returns how many were applied."""

    applied = 0
    for path in pending_commands(ctx.run_dir):
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                raise ValueError("the command file is too large")
            data = json.loads(path.read_text(encoding="utf-8"))
            _apply(ctx, data)
            applied += 1
        except (OSError, ValueError, BoardError) as exc:
            ctx.events.emit("inbox.rejected", file=path.name, reason=str(exc)[:200])
        finally:
            with contextlib.suppress(OSError):
                path.unlink()
    return applied


def _apply(ctx: RunContext, data: object) -> None:
    if not isinstance(data, dict):
        raise ValueError("a command is a JSON object")
    kind = data.get("kind")
    if kind == "pause":
        ctx.board.pause()
    elif kind == "unpause":
        ctx.board.unpause()
    elif kind == "note":
        text, card = data.get("text"), data.get("card")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("a note needs text")
        if isinstance(card, str) and card:
            ctx.board.comment(card, text, author=USER)
        else:
            ctx.board.add_user_note(text)
    elif kind == "answer":
        question, text = data.get("question"), data.get("text")
        if not isinstance(question, str) or not isinstance(text, str):
            raise ValueError("an answer needs a question id and text")
        delivered = ctx.human.answer(question, text) if text.strip() else ctx.human.skip(question)
        if not delivered:
            raise ValueError(f"no open question {question}")
    else:
        raise ValueError(f"unknown command {kind!r}")


def _watch(ctx: RunContext, stop: threading.Event) -> None:
    while not stop.wait(POLL_SECONDS):
        apply_pending(ctx)


@contextlib.contextmanager
def inbox_watch(ctx: RunContext) -> Iterator[None]:
    """Apply commands posted for ``ctx``'s run while the block runs (and any already waiting)."""

    apply_pending(ctx)
    stop = threading.Event()
    watcher = threading.Thread(
        target=_watch, args=(ctx, stop), name=f"inbox-watch-{ctx.run_id}", daemon=True
    )
    watcher.start()
    try:
        yield
    finally:
        stop.set()
        watcher.join(timeout=2)
        apply_pending(ctx)
