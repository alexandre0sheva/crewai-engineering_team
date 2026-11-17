"""Compare contenders across committed benchmark batches, per task and paired.

    uv run python scripts/compare_batches.py benchmarks/results/2026-11-16 --split heldout
    uv run python scripts/compare_batches.py DIR [DIR ...] --split heldout --exclude-intent review

Every batch under DIR (a directory with a ``batch.json``) is read; a contender is a strategy of the
team on a provider (``auto/openai``) or an external agent (``codex``). Runs are pooled per contender
over the chosen split, then each pair is compared on the tasks both ran: a contender *wins* a task
when its pass rate there is higher, and the exact two-sided sign test says how likely such a split
is if neither were better (``engineering_team.bench.compare``). "Better" is claimed only when p is
below 0.05 or the Wilson intervals do not overlap. Prints Markdown; writes nothing.

``--supersede DATE:TASK`` drops the runs of TASK from every batch under a directory dated before
DATE (``benchmarks/results/2026-11-16/...``): the task's check was amended and its runs were
repeated, so the older runs, which an amended check would judge differently, no longer count.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

from engineering_team.bench.compare import pairings
from engineering_team.bench.report import money, seconds
from engineering_team.bench.results import (
    RunRecord,
    batch_directories,
    collect_results,
    read_batch,
)
from engineering_team.bench.stats import cost_per_success, median, wilson_interval

COUNTED = ("passed", "failed", "timeout")


def variant(batch: dict[str, object]) -> str:
    """What a batch changed besides the strategy and provider: its ``--config`` file (best-of-3,
    learning off) or a shared experience library, so such runs never pool with the plain ones."""

    command = [str(part) for part in batch.get("command") or []]
    parts = []
    if "--config" in command and command.index("--config") + 1 < len(command):
        parts.append(Path(command[command.index("--config") + 1]).stem)
    if "--library" in command:
        parts.append("shared-library")
    return "".join(f"+{part}" for part in parts)


def label(record: RunRecord, extra: str) -> str:
    return (record.agent or f"{record.strategy}/{record.provider}") + extra


DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def dated(batch: Path) -> str:
    """The date directory a batch sits in (``2026-11-16``), or an empty string."""

    return next((part for part in batch.parts if DATE.match(part)), "")


def load(
    roots: list[Path],
    split: str,
    skip_intents: set[str],
    superseded: dict[str, str] | None = None,
) -> dict[str, list[RunRecord]]:
    """``superseded`` maps a task to the first date whose runs of it still count."""

    superseded = superseded or {}
    pooled: dict[str, list[RunRecord]] = defaultdict(list)
    for batch in (b for root in roots for b in batch_directories(root)):
        extra = variant(read_batch(batch))
        date = dated(batch)
        for record in collect_results(batch):
            if record.fake or record.outcome not in COUNTED:
                continue
            if record.task in superseded and date < superseded[record.task]:
                continue
            if split != "all" and record.split != split:
                continue
            if record.intent in skip_intents:
                continue
            pooled[label(record, extra)].append(record)
    return dict(sorted(pooled.items()))


def table(pooled: dict[str, list[RunRecord]]) -> list[str]:
    lines = [
        "| Contender | Passed | Pass rate (Wilson 95 %) | Cost / success | Median time |",
        "|---|---|---|---|---|",
    ]
    for name, runs in pooled.items():
        passed = sum(r.outcome == "passed" for r in runs)
        low, high = wilson_interval(passed, len(runs))
        costs = [r.cost_usd for r in runs]
        plan = any(r.billing == "subscription" for r in runs)
        per = "plan" if plan else money(cost_per_success(costs, passed))
        time = median([r.duration_seconds for r in runs if r.duration_seconds is not None])
        lines.append(
            f"| {name} | {passed}/{len(runs)} | {passed / len(runs):.0%} "
            f"({low:.0%}–{high:.0%}) | {per} | {seconds(time) if time else '–'} |"
        )
    return lines


def paired(pooled: dict[str, list[RunRecord]]) -> list[str]:
    rates: dict[str, dict[str, tuple[int, int]]] = {}
    for name, runs in pooled.items():
        per_task: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        for r in runs:
            per_task[r.task][0] += r.outcome == "passed"
            per_task[r.task][1] += 1
        rates[name] = {t: (p, n) for t, (p, n) in per_task.items()}
    lines = [
        "| Pair | Tasks | First wins | Second wins | Ties | Sign test p |",
        "|---|---|---|---|---|---|",
    ]
    for p in pairings(rates):
        lines.append(
            f"| {p.a} vs {p.b} | {p.tasks} | {p.a_wins} | {p.b_wins} | {p.ties} | {p.p_value:.3f} |"
        )
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("results", type=Path, nargs="+", help="directories of batches")
    parser.add_argument("--split", default="heldout", choices=["dev", "heldout", "all"])
    parser.add_argument("--exclude-intent", action="append", default=[], metavar="INTENT")
    parser.add_argument(
        "--supersede",
        action="append",
        default=[],
        metavar="DATE:TASK",
        help="count TASK only from batches dated DATE or later (its check was amended)",
    )
    args = parser.parse_args(argv)
    superseded: dict[str, str] = {}
    for item in args.supersede:
        date, _, task = item.partition(":")
        if not DATE.match(date) or not task:
            parser.error(f"--supersede wants DATE:TASK, got {item!r}")
        superseded[task] = date
    pooled = load(args.results, args.split, set(args.exclude_intent), superseded)
    if not pooled:
        print("no counted runs found", file=sys.stderr)
        return 1
    print("\n".join(table(pooled)))
    print()
    print("\n".join(paired(pooled)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
