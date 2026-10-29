"""``results.json``: one document for a whole batch, the same schema for the team and the agents.

The site's leaderboard reads this file. ``contenders`` is the summary (one row per strategy or
external agent), ``paired`` the per-task wins and losses, and ``runs`` every run record as
``result.json`` holds it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from engineering_team.atomic_io import atomic_write_json
from engineering_team.bench.compare import pairings, task_rates
from engineering_team.bench.report import group_by
from engineering_team.bench.results import RunRecord

RESULTS_FILE = "results.json"
EXPORT_VERSION = 1


def results_document(batch: dict[str, Any], records: list[RunRecord]) -> dict[str, Any]:
    agents = {r.agent: r.agent_version for r in records if r.agent}
    contenders = []
    for group in group_by(records, lambda r: r.contender):
        own = [r for r in records if r.contender == group.label]
        sample = own[0]
        contenders.append(
            {
                "name": group.label,
                "kind": "agent" if sample.agent else "strategy",
                "version": sample.agent_version,
                "billing": group.billing,
                "models": sorted({m for r in own for m in r.models}),
                "runs": group.runs, "counted": group.counted, "passed": group.passed,
                "pass_rate": group.rate, "wilson_low": group.low, "wilson_high": group.high,
                "cost_total_usd": group.cost_total, "cost_per_success_usd": group.cost_per_success,
                "equivalent_cost_total_usd": group.equivalent_total,
                "median_duration_seconds": group.duration_median,
            }
        )  # fmt: skip
    return {
        "export_version": EXPORT_VERSION,
        "batch": batch.get("batch") or (records[0].batch if records else ""),
        "created": batch.get("created"),
        "fake": bool(batch.get("fake")) and not any(r.agent for r in records),
        "versions": batch.get("versions") or {},
        "agents": {name: {"version": version} for name, version in sorted(agents.items())},
        "contenders": contenders,
        "paired": [
            {"a": p.a, "b": p.b, "tasks": p.tasks, "a_wins": p.a_wins, "b_wins": p.b_wins,
             "ties": p.ties}
            for p in pairings(task_rates(records))
        ],
        "runs": [r.model_dump(mode="json") for r in records],
    }  # fmt: skip


def write_results_json(batch_dir: Path, batch: dict[str, Any], records: list[RunRecord]) -> Path:
    path = batch_dir / RESULTS_FILE
    atomic_write_json(path, results_document(batch, records))
    return path
