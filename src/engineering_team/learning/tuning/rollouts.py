"""Rollouts: one team run of a benchmark task under a candidate harness, judged by hidden checks.

:class:`BenchRollouts` makes a rollout the way ``bench run`` makes a run (a team process of its
own, an isolated workspace, the hidden checks after it), with the candidate installed as the
team's user harness and learning off, so the harness alone is measured. What a proposer is
later shown is the rollout's outcome and a summary of its trajectory, and nothing else of the
task: every line that quotes a protected file (the hidden checks and references of every task,
less what the requests say) is withheld, and the request is the text the team itself was given.
"""

from __future__ import annotations

import contextlib
import json
import threading
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from engineering_team.bench.execution import ProcessRegistry, execute_run
from engineering_team.bench.metrics import parse_summary
from engineering_team.bench.plan import RunOptions, RunSpec
from engineering_team.bench.results import RunRecord
from engineering_team.bench.tasks import BenchTask
from engineering_team.learning.evidence import MAX_REQUEST_CHARS, trace_summary
from engineering_team.learning.guard import Protected, leak_in
from engineering_team.learning.tuning.tree import Candidate, write_overlay
from engineering_team.runtime.run_store import TRAJECTORY_FILENAME
from engineering_team.runtime.trajectory import read_trajectory

STRATEGY = "pipeline"
WITHHELD = "[withheld: it quotes a benchmark's hidden checks or reference]"
TRACE_CHARS = 8_000
OVERRIDES: dict[str, Any] = {"learning.enabled": False}  # lessons would confound the comparison


@dataclass(frozen=True)
class Rollout:
    """What the loop keeps of one rollout."""

    label: str
    task: str
    repeat: int
    outcome: str  # passed, failed, timeout, error, skipped (bench's outcomes)
    cost_usd: float | None = None
    tokens: int = 0
    run_dir: str = ""
    team_verdict: str | None = None
    trace: str = ""  # redacted
    seqs: frozenset[int] = frozenset()
    teammates: tuple[str, ...] = ()
    overlay_applied: bool | None = None  # the team's harness had the candidate's pack (or not)

    @property
    def passed(self) -> bool:
        return self.outcome == "passed"

    @property
    def counted(self) -> bool:
        return self.outcome in ("passed", "failed", "timeout")

    def evidence(self) -> str:
        """The rollout as a proposer sees it (data, already redacted)."""

        return "\n".join(
            [
                f"Rollout {self.label}: hidden checks say {self.outcome}; the team's own verdict "
                f"was {self.team_verdict or 'none'}.",
                "Trajectory (#seq teammate tool(args) -> result; agents' words are theirs):",
                self.trace or "(nothing recorded)",
            ]
        )


RolloutRunner = Callable[[BenchTask, Candidate, str, int], Rollout]


def redact(text: str, protected: Protected) -> str:
    """``text`` with every line that quotes a protected file withheld; if a quotation spans
    lines, the whole text is withheld."""

    if not protected:
        return text
    lines = [WITHHELD if leak_in(line, protected) else line for line in text.splitlines()]
    kept = "\n".join(lines)
    return kept if leak_in(kept, protected) is None else WITHHELD


def request_text(task: BenchTask, protected: Protected) -> str:
    """The request the team was given (``request.md``), capped (and redacted, should a
    ``protected`` set not allow it)."""

    try:
        text = task.request_path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return redact(text[:MAX_REQUEST_CHARS], protected)


def protected_texts(tasks: list[BenchTask]) -> Protected:
    """The hidden checks and references of ``tasks``: what no proposer may see or write (less
    what their requests say, which the team is shown anyway)."""

    paths: list[Path] = []
    for task in tasks:
        paths += [task.checks_file.parent, task.reference_dir]
    protected = Protected.from_paths(paths)
    for task in tasks:
        with contextlib.suppress(OSError):
            protected.allow(task.request_path.read_text(encoding="utf-8"))
    return protected


@dataclass
class BenchRollouts:
    """Rollouts through the benchmark harness, each in ``<root>/<label>/``."""

    options: RunOptions
    root: Path
    pack: str
    protected: Protected
    trace_chars: int = TRACE_CHARS
    registry: ProcessRegistry = field(default_factory=ProcessRegistry)
    cancel: threading.Event = field(default_factory=threading.Event)

    def options_for(self, candidate: Candidate, name: str) -> RunOptions:
        folder = self.root / name
        overlay = None
        if not candidate.empty:
            overlay = write_overlay(candidate, self.pack, folder / "harness-overlay")
        return replace(
            self.options,
            batch=name,
            batch_dir=folder,
            harness_overlay=overlay,
            settings_overrides={**self.options.settings_overrides, **OVERRIDES},
        )

    def __call__(self, task: BenchTask, candidate: Candidate, name: str, repeat: int) -> Rollout:
        folder = name.replace(":", "-")
        options = self.options_for(candidate, folder)
        record = execute_run(RunSpec(task, STRATEGY, repeat), options, self.registry, self.cancel)
        return self.rollout(name, record, options.batch_dir, candidate)

    def rollout(
        self, name: str, record: RunRecord, batch_dir: Path, candidate: Candidate
    ) -> Rollout:
        run_dir = batch_dir / record.run_dir
        team_dir = _team_run_dir(batch_dir, record)
        entries = list(read_trajectory(team_dir / TRAJECTORY_FILENAME)) if team_dir else []
        packs = _harness_packs(run_dir, team_dir)
        applied = None
        if not candidate.empty and packs is not None:
            applied = (self.pack, "user") in packs
        return Rollout(
            label=name,
            task=record.task,
            repeat=record.repeat,
            outcome=record.outcome,
            cost_usd=record.cost_usd,
            tokens=record.tokens,
            run_dir=str(run_dir.relative_to(self.root)),
            team_verdict=record.team_verdict,
            trace=redact(trace_summary(entries, self.trace_chars), self.protected),
            seqs=frozenset(entry.seq for entry in entries),
            teammates=tuple(sorted({e.teammate for e in entries if e.teammate})),
            overlay_applied=applied,
        )


def _team_run_dir(batch_dir: Path, record: RunRecord) -> Path | None:
    if not record.workspace or not record.run_id:
        return None
    workspace = Path(record.workspace)
    workspace = workspace if workspace.is_absolute() else batch_dir / workspace
    folder = workspace / ".engineering-team" / "runs" / record.run_id
    return folder if folder.is_dir() else None


def _harness_packs(run_dir: Path, team_dir: Path | None) -> set[tuple[str, str]] | None:
    """The ``(pack, source)`` pairs the team's harness had: from its manifest, or the summary a
    fake team prints (``None``: not recorded)."""

    harness: Any = None
    if team_dir is not None:
        with contextlib.suppress(OSError, ValueError):
            harness = json.loads((team_dir / "manifest.json").read_text("utf-8")).get("harness")
    if not isinstance(harness, dict):
        harness = parse_summary(run_dir / "stdout.log").get("harness")
    if not isinstance(harness, dict) or not isinstance(harness.get("packs"), list):
        return None
    return {
        (str(p.get("name")), str(p.get("source"))) for p in harness["packs"] if isinstance(p, dict)
    }
