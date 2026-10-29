"""``bench report`` and ``bench run --dry-run``: reading a batch, and pricing one before it runs."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.table import Table

from engineering_team.bench import estimate as estimating
from engineering_team.bench import report as reporting
from engineering_team.bench.export import write_results_json
from engineering_team.bench.plan import is_agent
from engineering_team.bench.results import RunRecord, batch_directories, collect_results, read_batch
from engineering_team.cli.bench_options import OutOption, SuiteOption, suite_path
from engineering_team.cli.context import Globals, fail, print_json
from engineering_team.cli.context import get as get_globals


def print_groups(g: Globals, records: list[RunRecord]) -> None:
    table = Table(*["Strategy / agent", *reporting.GROUP_HEADERS[1:]], box=None)
    for row in reporting.group_rows(reporting.group_by(records, lambda r: r.contender)):
        table.add_row(*row)
    g.console(stderr=True).print(table)


def dry_run(
    g: Globals,
    specs: list[Any],
    not_applicable: list[tuple[str, str]],
    settings: Any,
    budget_usd: float | None,
) -> None:
    agent_runs = sum(1 for s in specs if is_agent(s.strategy))
    found = estimating.estimate_batch([s for s in specs if not is_agent(s.strategy)], settings)
    total, span = found.total_usd, found.range_usd
    if g.json:
        print_json(
            {
                "runs": found.runs, "estimated_usd": total,
                "range_usd": list(span) if span else None,
                "unpriced_models": found.unpriced_models,
                "rows": [
                    {"task": r.task, "strategy": r.strategy, "runs": r.runs,
                     "per_run_usd": r.per_run_usd}
                    for r in found.rows
                ],
            }
        )  # fmt: skip
        return
    console = g.console()
    table = Table("Task", "Strategy", "Runs", "Per run", "Subtotal", box=None)
    for r in found.rows:
        subtotal = None if r.per_run_usd is None else r.per_run_usd * r.runs
        table.add_row(
            r.task,
            r.strategy,
            str(r.runs),
            reporting.money(r.per_run_usd),
            reporting.money(subtotal),
        )
    console.print(table)
    summary = f"{found.runs} run(s): estimated {reporting.money(total)}"
    if span:
        summary += f" (plausibly {reporting.money(span[0])} to {reporting.money(span[1])})"
    console.print(summary, markup=False, soft_wrap=True)
    if agent_runs:
        console.print(
            f"{agent_runs} run(s) by external agents are not in that figure: their cost is only "
            "known after the run (--run-budget-usd caps Claude Code; a plan costs no money).",
            markup=False,
            soft_wrap=True,
        )
    if found.unpriced_models:
        console.print(
            f"No price for {', '.join(found.unpriced_models)}: the cost is unknown. Add a "
            "[prices] override (docs/CONFIGURATION.md) or rely on --budget-usd.",
            markup=False,
            soft_wrap=True,
        )
    for task, strategy in not_applicable:
        console.print(
            f"  not applicable: {task} × {strategy} (repository modes use the pipeline; "
            "external agents skip reviews)",
            markup=False,
        )
    console.print(
        "The token counts behind this are priors, not measurements. Set --budget-usd as the cap; "
        "nothing was run.",
        markup=False,
        soft_wrap=True,
    )
    if budget_usd is not None and total is not None and total > budget_usd:
        console.print(
            f"Warning: the estimate is above --budget-usd {budget_usd:g}; some runs may not start.",
            markup=False,
            soft_wrap=True,
        )


def report(
    ctx: typer.Context,
    batch: Annotated[
        str | None,
        typer.Argument(help="A batch directory, or a name under --out (default: latest)."),
    ] = None,
    write: Annotated[
        bool, typer.Option(help="Write report.md and results.csv into the batch.")
    ] = True,
    suite: SuiteOption = None,
    out: OutOption = None,
) -> None:
    """Summarise a batch: pass rates with Wilson 95 % intervals, cost per success, every failure."""

    g = get_globals(ctx)
    out_dir = Path(out).expanduser().resolve() if out else suite_path(suite) / ".runs"
    if batch and Path(batch).expanduser().is_dir():
        batch_dir = Path(batch).expanduser().resolve()
    elif batch:
        batch_dir = out_dir / batch
    else:
        found = list(batch_directories(out_dir))
        if not found:
            fail(f"No batches under {out_dir}. Run `bench run` first.")
        batch_dir = found[-1]
    records = collect_results(batch_dir)
    if not records:
        fail(f"No results in {batch_dir}.", code=1)
    text = reporting.render_markdown(read_batch(batch_dir), records)
    if write:
        (batch_dir / "report.md").write_text(text, encoding="utf-8")
        (batch_dir / "results.csv").write_text(reporting.render_csv(records), encoding="utf-8")
        write_results_json(batch_dir, read_batch(batch_dir), records)
    if g.json:
        print_json(
            {
                "batch": batch_dir.name,
                "groups": [
                    {"strategy": grp.label, "passed": grp.passed, "counted": grp.counted,
                     "rate": grp.rate, "wilson_low": grp.low, "wilson_high": grp.high,
                     "cost_per_success_usd": grp.cost_per_success}
                    for grp in reporting.group_by(records, lambda r: r.contender)
                ],
            }
        )  # fmt: skip
    else:
        sys.stdout.write(text)
