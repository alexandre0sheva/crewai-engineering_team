"""``bench report``: a batch's results as Markdown and CSV.

Pass rate is passed over runs that counted (passed, failed, timeout), with a Wilson 95 % interval;
runs the harness could not make (``error``) or did not start (``skipped``) are listed, not counted.
*Cost per successful task* is all spend, failed runs included, divided by the successes. Every
failure is listed with the criteria it missed. Order is stable: task, strategy, repeat.
"""

from __future__ import annotations

import csv
import io
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from engineering_team.bench.results import RunRecord
from engineering_team.bench.stats import cost_per_success, mean, median, total_cost, wilson_interval

SMALL_SAMPLE = 20  # below this many counted runs the interval is wide enough to need a warning
CSV_COLUMNS = (
    "batch", "task", "strategy", "repeat", "kind", "mode", "subset", "provider", "profile",
    "fake", "outcome", "reason", "failed_criteria", "exit_code", "team_status", "team_verdict",
    "duration_seconds", "tokens", "prompt_tokens", "completion_tokens", "model_calls",
    "tool_calls", "cost_usd", "repair_rounds", "tool_failures", "setup_failures", "run_id",
    "run_dir",
)  # fmt: skip


@dataclass(frozen=True)
class Group:
    """Aggregate of the runs that share a label."""

    label: str
    runs: int  # every run, whatever its outcome
    counted: int
    passed: int
    low: float
    high: float
    cost_total: float | None
    cost_mean: float | None
    cost_per_success: float | None
    duration_median: float | None
    repair_mean: float | None
    tool_failures: int
    setup_failures: int

    @property
    def rate(self) -> float | None:
        return self.passed / self.counted if self.counted else None


def aggregate(label: str, records: Iterable[RunRecord]) -> Group:
    items = list(records)
    counted = [r for r in items if r.counted]
    passed = sum(1 for r in counted if r.outcome == "passed")
    low, high = wilson_interval(passed, len(counted))
    costs = [r.cost_usd for r in counted]
    spent = total_cost(costs)
    return Group(
        label=label,
        runs=len(items),
        counted=len(counted),
        passed=passed,
        low=low,
        high=high,
        cost_total=spent,
        cost_mean=None if spent is None or not counted else spent / len(counted),
        cost_per_success=cost_per_success(costs, passed),
        duration_median=median([r.duration_seconds for r in counted]),
        repair_mean=mean([float(r.repair_rounds) for r in counted]),
        tool_failures=sum(r.tool_failures for r in counted),
        setup_failures=sum(r.setup_failures for r in counted),
    )


def group_by(records: Iterable[RunRecord], key: Callable[[RunRecord], str]) -> list[Group]:
    buckets: dict[str, list[RunRecord]] = defaultdict(list)
    for record in records:
        buckets[key(record)].append(record)
    return [aggregate(label, buckets[label]) for label in sorted(buckets)]


def money(value: float | None) -> str:
    if value is None:
        return "unknown"
    return f"${value:.4f}" if value < 0.1 else f"${value:.2f}"


def percent(group: Group) -> str:
    if group.rate is None:
        return "–"
    return f"{group.rate:.0%} ({group.low:.0%}–{group.high:.0%})"


def seconds(value: float | None) -> str:
    if value is None:
        return "–"
    return f"{value:.0f}s" if value < 120 else f"{value / 60:.1f}min"


def _table(headers: list[str], rows: list[list[str]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(" --- " for _ in headers) + "|"]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return lines


def group_rows(groups: list[Group]) -> list[list[str]]:
    return [
        [
            g.label,
            f"{g.passed}/{g.counted}",
            percent(g),
            money(g.cost_mean),
            money(g.cost_per_success) if g.passed else "–",
            seconds(g.duration_median),
            "–" if g.repair_mean is None else f"{g.repair_mean:.1f}",
            str(g.tool_failures),
            str(g.setup_failures),
        ]
        for g in groups
    ]


GROUP_HEADERS = [
    "", "Passed", "Pass rate (Wilson 95 %)", "Mean cost / run", "Cost / success",
    "Median time", "Repairs / run", "Tool failures", "Setup failures",
]  # fmt: skip


def render_markdown(batch: dict[str, Any], records: list[RunRecord]) -> str:
    """The report: header, results by strategy, kind and subset, by task, and every failure."""

    name = batch.get("batch") or (records[0].batch if records else "batch")
    versions = batch.get("versions") or {}
    lines = [f"# Benchmark report: {name}", ""]
    if batch.get("fake") or any(r.fake for r in records):
        lines += [
            "> **Offline run (`--fake`)**: scripted reference solutions, no model. This validates "
            "the harness; it says nothing about the team's quality.",
            "",
        ]
    facts = [
        (
            "Provider / profile",
            f"{batch.get('provider') or 'default'} / {batch.get('profile') or 'default'}",
        ),
        ("Strategies", ", ".join(batch.get("strategies") or sorted({r.strategy for r in records}))),
        ("Repeats per task", str(batch.get("repeat", "?"))),
        ("Created", str(batch.get("created", "?"))),
        ("Versions", ", ".join(f"{k} {v}" for k, v in versions.items() if v) or "?"),
    ]
    lines += [f"- **{label}:** {value}" for label, value in facts] + [""]

    counted = [r for r in records if r.counted]
    other = [r for r in records if not r.counted]
    if not records:
        return "\n".join([*lines, "No results found in this batch.", ""])
    if 0 < len(counted) < SMALL_SAMPLE:
        lines += [
            f"> **Small sample:** {len(counted)} counted run(s). Pass-rate intervals this wide "
            "cannot separate strategies that differ by less than their width; read them as such.",
            "",
        ]

    lines += ["## Results by strategy", ""]
    lines += _table(
        ["Strategy", *GROUP_HEADERS[1:]], group_rows(group_by(records, lambda r: r.strategy))
    )
    lines += ["", "## By task kind and subset", ""]
    lines += _table(
        ["Strategy / kind / subset", *GROUP_HEADERS[1:]],
        group_rows(group_by(records, lambda r: f"{r.strategy} · {r.kind} · {r.subset}")),
    )
    lines += ["", "## By task", ""]
    lines += _table(
        ["Task / strategy", *GROUP_HEADERS[1:]],
        group_rows(group_by(records, lambda r: f"{r.task} · {r.strategy}")),
    )

    failures = [r for r in records if r.outcome != "passed"]
    lines += ["", f"## Failures ({len(failures)})", ""]
    if failures:
        lines += _table(
            ["Task", "Strategy", "Run", "Outcome", "Missed criteria", "Why"],
            [
                [
                    r.task, r.strategy, str(r.repeat), r.outcome,
                    ", ".join(r.failed_criteria) or "–", r.reason.replace("|", "\\|") or "–",
                ]
                for r in failures
            ],
        )  # fmt: skip
    else:
        lines.append("None: every run passed.")
    if other:
        lines += [
            "",
            f"{len(other)} run(s) did not count towards pass rates (harness error or not started).",
        ]
    skipped = batch.get("not_applicable") or []
    if skipped:
        pairs = ", ".join(f"{s['task']} × {s['strategy']}" for s in skipped)
        lines += ["", f"Not applicable (repository modes use the pipeline only): {pairs}."]
    lines += [
        "",
        "Pass rate is passed over counted runs (passed, failed, timeout). Cost per success is all "
        "spend, failed runs included, over the successes; `unknown` means a model had no price.",
        "",
    ]
    return "\n".join(lines)


def render_csv(records: list[RunRecord]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for record in records:
        row: dict[str, Any] = record.model_dump()
        row["failed_criteria"] = ";".join(record.failed_criteria)
        row["cost_usd"] = "" if record.cost_usd is None else f"{record.cost_usd:.6f}"
        writer.writerow(["" if row[c] is None else row[c] for c in CSV_COLUMNS])
    return buffer.getvalue()
