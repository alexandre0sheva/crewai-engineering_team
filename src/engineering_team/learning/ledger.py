"""The tuning ledger: every rollout and proposer session of ``harness tune``, with its cost.

``ledger.jsonl`` in the tuning directory has one line per entry, written as the entry ends and
never rewritten. A label names one entry (``rollout:base:seeded-bug:1``,
``session:inv:seeded-bug:1``, ``eval:final:ledger-misleading-trace:1``); recording a label twice is
a bug in the loop and raises :class:`LedgerError` at once. When a loop is complete, the
**identity check** holds for every tuned task with ``k`` repeats: ``4k`` rollouts (base, INV
probe, DOM probe, composed probe) and ``3k`` sessions (INV, DOM, composer) in parallel mode;
``3k`` rollouts and ``3k`` sessions in sequential mode, where the composer picks among variants
already probed. Evaluation runs (``eval:*``) are counted apart. See docs/HARNESS.md#tuning.
"""

from __future__ import annotations

import json
import os
import threading
from collections import Counter
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

LEDGER_FILE = "ledger.jsonl"
Kind = Literal["rollout", "session", "eval"]
Mode = Literal["parallel", "sequential"]
ROLLOUTS_PER_REPEAT: dict[str, int] = {"parallel": 4, "sequential": 3}
SESSIONS_PER_REPEAT = 3


class LedgerError(RuntimeError):
    """A duplicate label, or a finished loop whose counts break the identity."""


class LedgerEntry(BaseModel):
    """One rollout (a team run judged by hidden checks) or one proposer or composer session."""

    model_config = ConfigDict(extra="forbid")

    label: str
    kind: Kind
    role: str  # rollouts: base, inv, dom, composed; sessions: inv, dom, compose; eval: base, final
    task: str
    repeat: int
    outcome: str = ""  # a rollout's outcome; a session's: proposed, no_change, invalid, failed
    cost_usd: float | None = None  # None: unknown (an unpriced model), never free
    tokens: int = 0
    model: str = ""
    attempts: int = 0  # a session: 1, or 2 after a validation retry
    run_dir: str = ""  # a rollout: its bench run directory, relative to the rollouts directory
    detail: str = ""
    date: str = ""


def label(kind: Kind, role: str, task: str, repeat: int) -> str:
    return f"{kind}:{role}:{task}:{repeat}"


class TuningLedger:
    """``ledger.jsonl``: append-only, thread-safe, one entry per label."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._entries = list(read_ledger(path))
        self._labels = {entry.label for entry in self._entries}

    def record(self, entry: LedgerEntry) -> LedgerEntry:
        with self._lock:
            if entry.label in self._labels:
                raise LedgerError(
                    f"The tuning ledger already has {entry.label!r}: every rollout and session "
                    "is recorded once (a bug in the loop, not in the run)."
                )
            if not entry.date:
                entry = entry.model_copy(
                    update={"date": datetime.now(UTC).isoformat(timespec="seconds")}
                )
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry.model_dump(mode="json")) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            self._labels.add(entry.label)
            self._entries.append(entry)
            return entry

    def entries(self) -> list[LedgerEntry]:
        with self._lock:
            return list(self._entries)

    def cost(self) -> tuple[float, int]:
        """``(known cost in USD, entries whose cost is unknown)``."""

        entries = self.entries()
        known = sum(e.cost_usd for e in entries if e.cost_usd is not None)
        unknown = sum(1 for e in entries if e.cost_usd is None and e.tokens > 0)
        return round(known, 8), unknown


def read_ledger(path: Path) -> Iterable[LedgerEntry]:
    if not path.is_file():
        return
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            yield LedgerEntry.model_validate_json(line)
        except ValueError as exc:
            raise LedgerError(f"{path}:{number} is not a ledger entry: {exc}") from None


def identity_problems(
    entries: Iterable[LedgerEntry], tasks: Iterable[str], k: int, mode: Mode
) -> list[str]:
    """Where the counts of a finished loop differ from the identity (empty: they hold)."""

    entries = list(entries)
    rollouts = Counter(e.task for e in entries if e.kind == "rollout")
    sessions = Counter(e.task for e in entries if e.kind == "session")
    want_rollouts, want_sessions = ROLLOUTS_PER_REPEAT[mode] * k, SESSIONS_PER_REPEAT * k
    problems: list[str] = []
    tuned = list(tasks)
    for task in tuned:
        if rollouts[task] != want_rollouts:
            problems.append(f"{task}: {rollouts[task]} rollouts, the identity says {want_rollouts}")
        if sessions[task] != want_sessions:
            problems.append(f"{task}: {sessions[task]} sessions, the identity says {want_sessions}")
    stray = sorted((set(rollouts) | set(sessions)) - set(tuned))
    if stray:
        problems.append(f"rollouts or sessions for tasks that were not tuned: {', '.join(stray)}")
    return problems
