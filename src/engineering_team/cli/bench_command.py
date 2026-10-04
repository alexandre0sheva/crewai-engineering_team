"""``bench``: run the benchmark suite against the team, and report on a batch of runs.

The suite (``benchmarks/``) ships in the source checkout, not the wheel. ``bench run`` makes one
subprocess per run in an isolated workspace, judges each with hidden checks, and writes a report;
``--fake`` replays the reference solutions offline (no model, no cost) to validate the harness
itself; ``--dry-run`` prices the batch without starting anything. See ``docs/BENCHMARKS.md``.
"""

from __future__ import annotations

import sys
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.table import Table

from engineering_team.bench import estimate as estimating
from engineering_team.bench import report as reporting
from engineering_team.bench.batch import BudgetLedger, run_batch, write_batch_manifest
from engineering_team.bench.plan import STRATEGIES, RunOptions, plan_runs
from engineering_team.bench.results import RunRecord, batch_directories, collect_results, read_batch
from engineering_team.bench.tasks import BenchError, load_suite, select_tasks
from engineering_team.cli.context import Globals, fail, one_of, print_json
from engineering_team.cli.context import get as get_globals
from engineering_team.model_routing import PROFILE_NAMES, PROVIDERS

EXIT_FAILED = 3
EXIT_INTERRUPTED = 130

bench_app = typer.Typer(help="Run and report the benchmark suite.", no_args_is_help=True)

SuiteOption = Annotated[
    str | None,
    typer.Option(
        help="The benchmark suite directory (default: ./benchmarks in a source checkout)."
    ),
]
OutOption = Annotated[
    str | None, typer.Option(help="Where batches are kept (default: <suite>/.runs).")
]


def default_suite() -> Path:
    """``./benchmarks``, else the ``benchmarks`` next to this source tree (an editable install)."""

    here = Path.cwd() / "benchmarks"
    if (here / "tasks").is_dir():
        return here
    checkout = Path(__file__).resolve().parents[3] / "benchmarks"
    return checkout if (checkout / "tasks").is_dir() else here


def _suite(suite: str | None) -> Path:
    return Path(suite).expanduser().resolve() if suite else default_suite().resolve()


def _strategies(values: list[str] | None) -> list[str]:
    chosen = [s.strip() for raw in values or ["pipeline"] for s in raw.split(",") if s.strip()]
    unknown = [s for s in chosen if s not in STRATEGIES]
    if unknown:
        fail(f"Unknown strategy: {', '.join(unknown)}. Choose from {', '.join(STRATEGIES)}.")
    return list(dict.fromkeys(chosen))


def _config_path(config: str | None) -> str | None:
    if config is None:
        return None
    path = Path(config).expanduser().resolve()
    if not path.is_file():
        fail(f"Config file not found: {path}")
    return str(path)


def _settings(
    provider: str | None, profile: str | None, config: str | None, *, credentials: bool = False
) -> Any:
    """The settings the team will get: the flags and ``--config``, nothing ambient (no
    ``ENGINEERING_*`` variable, user config file, or project config; see
    ``prepare.neutral_settings_environment``). With ``credentials`` the keys the team's process
    will find (the environment, or the repository's ``.env`` as CrewAI loads it) are visible."""

    from engineering_team.bench.prepare import visible_credentials
    from engineering_team.settings import SettingsError, load_settings

    env = visible_credentials() if credentials else {}
    overrides = {k: v for k, v in (("provider", provider), ("profile", profile)) if v}
    with tempfile.TemporaryDirectory(prefix="bench-settings-") as scratch:
        try:
            return load_settings(
                overrides=overrides,
                env=env,
                cwd=Path(scratch),
                home=Path(scratch),
                config_file=config,
            )
        except SettingsError as exc:
            fail(str(exc))


@bench_app.command("list")
def list_tasks(ctx: typer.Context, suite: SuiteOption = None) -> None:
    """List the benchmark tasks."""

    g = get_globals(ctx)
    try:
        tasks = load_suite(_suite(suite))
    except BenchError as exc:
        fail(str(exc))
    if g.json:
        print_json(
            [
                {
                    "id": t.id, "kind": t.kind, "mode": t.mode, "subset": t.subset,
                    "criteria": [c.id for c in t.criteria], "title": t.title,
                }
                for t in tasks
            ]
        )  # fmt: skip
        return
    table = Table("Task", "Kind", "Mode", "Subset", "Criteria", "Title", box=None)
    for t in tasks:
        table.add_row(t.id, t.kind, t.mode, t.subset, str(len(t.criteria)), t.title)
    g.console().print(table)


@bench_app.command("run")
def run(
    ctx: typer.Context,
    tasks: Annotated[
        list[str] | None, typer.Option(help="Task ids, comma-separated or repeated (default: all).")
    ] = None,
    subset: Annotated[
        str, typer.Option(callback=one_of(["all", "dev", "heldout"]), help="dev, heldout, or all.")
    ] = "all",
    strategy: Annotated[
        list[str] | None,
        typer.Option(help="pipeline, hierarchical, or single; repeat or comma-separate."),
    ] = None,
    repeat: Annotated[int, typer.Option(min=1, help="Runs per task and strategy.")] = 1,
    parallel: Annotated[int, typer.Option(min=1, help="Runs at the same time.")] = 1,
    budget_usd: Annotated[
        float | None,
        typer.Option(min=0.01, help="Spend cap for the whole batch (required for a live run)."),
    ] = None,
    run_budget_usd: Annotated[
        float | None,
        typer.Option(
            min=0.01, help="Cap for one run (default: the batch budget / number of runs)."
        ),
    ] = None,
    provider: Annotated[str | None, typer.Option(callback=one_of(list(PROVIDERS)))] = None,
    profile: Annotated[str | None, typer.Option(callback=one_of(list(PROFILE_NAMES)))] = None,
    sandbox: Annotated[
        str | None, typer.Option(callback=one_of(["local", "docker"]), help="Where commands run.")
    ] = None,
    config: Annotated[
        str | None,
        typer.Option(
            help="A settings file for the team (models, budgets, ...). A run's conditions come "
            "from the flags and this file only; ENGINEERING_* variables and your user config "
            "are not read."
        ),
    ] = None,
    allow_unpriced: Annotated[
        bool,
        typer.Option(help="Run even if a model has no price (then no cost cap can stop a run)."),
    ] = False,
    fake: Annotated[
        bool, typer.Option("--fake", help="Replay the reference solutions offline (no model).")
    ] = False,
    fake_solution: Annotated[
        str,
        typer.Option(
            callback=one_of(["reference", "broken"]),
            help="With --fake: the reference, or the reference with its sabotage applied.",
        ),
    ] = "reference",
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Estimate the cost from the price table; run nothing.")
    ] = False,
    batch: Annotated[
        str | None, typer.Option(help="Name of the batch (default: a timestamp).")
    ] = None,
    resume: Annotated[
        bool, typer.Option(help="Continue the named --batch: keep its finished runs.")
    ] = False,
    allow_failures: Annotated[
        bool, typer.Option(help="Exit 0 even when runs fail (they are the data).")
    ] = False,
    suite: SuiteOption = None,
    out: OutOption = None,
) -> None:
    """Run benchmark tasks through the team, judge each with hidden checks, and write a report."""

    g = get_globals(ctx)
    suite_dir = _suite(suite)
    strategies = _strategies(strategy)
    try:
        chosen = select_tasks(load_suite(suite_dir), tasks, subset)
    except BenchError as exc:
        fail(str(exc))
    specs, not_applicable = plan_runs(chosen, strategies, repeat)
    if not specs:
        fail("Nothing to run: the selected strategies do not apply to the selected tasks.")
    console = g.console(stderr=True)

    config_file = _config_path(config)
    if dry_run:
        _dry_run(g, specs, not_applicable, _settings(provider, profile, config_file), budget_usd)
        return

    if not fake:
        if budget_usd is None:
            fail("A live run needs --budget-usd (a spend cap for the batch); try --dry-run first.")
        settings = _settings(provider, profile, config_file, credentials=True)
        settings.check_ready(require_credentials=True)
        try:
            unpriced = estimating.unpriced_models(settings)
        except ValueError as exc:  # a settings problem (an Azure deployment name, ...)
            fail(str(exc))
        if unpriced and not allow_unpriced:
            fail(
                f"No price for {', '.join(unpriced)}: the cost cap cannot stop a run it cannot "
                'price. Add a [pricing."MODEL"] override to a --config file '
                "(docs/CONFIGURATION.md), or pass --allow-unpriced."
            )
    if resume and not batch:
        fail("--resume needs --batch NAME.")
    out_dir = Path(out).expanduser().resolve() if out else suite_dir / ".runs"
    name = batch or f"{datetime.now():%Y%m%d-%H%M%S}{'-fake' if fake else ''}"
    batch_dir = out_dir / name
    if batch_dir.exists() and not resume:
        fail(f"{batch_dir} exists. Choose another --batch name, or pass --resume to continue it.")
    run_cap = run_budget_usd or (round(budget_usd / len(specs), 4) if budget_usd else None)
    options = RunOptions(
        batch=name, batch_dir=batch_dir, provider=provider, profile=profile, sandbox=sandbox,
        config=config_file, fake=fake, fake_solution=fake_solution,
        run_budget_usd=None if fake else run_cap, resume=resume,
    )  # fmt: skip
    batch_dir.mkdir(parents=True, exist_ok=True)
    write_batch_manifest(
        options, chosen, strategies, repeat, planned=len(specs), not_applicable=not_applicable,
        budget_usd=budget_usd, parallel=parallel,
    )  # fmt: skip

    cancel = threading.Event()
    counter = [0]

    def show(record: RunRecord) -> None:
        counter[0] += 1
        if g.quiet and record.outcome == "passed":
            return
        console.print(
            f"[{counter[0]}/{len(specs)}] {record.task} {record.strategy}#{record.repeat}: "
            f"{record.outcome} · {reporting.money(record.cost_usd)} · "
            f"{reporting.seconds(record.duration_seconds)}"
            + (f" · {record.reason}" if record.reason else ""),
            markup=False,
            highlight=False,
            soft_wrap=True,
        )

    ledger = BudgetLedger(None if fake else budget_usd)
    if not g.quiet:
        console.print(
            f"Batch {name}: {len(specs)} run(s), {parallel} at a time"
            + (" (offline, scripted)" if fake else f", budget ${budget_usd:.2f}")
            + f". Results: {batch_dir}",
            markup=False,
            soft_wrap=True,
        )
    try:
        run_batch(specs, options, parallel=parallel, ledger=ledger, on_result=show, cancel=cancel)
    except KeyboardInterrupt:
        console.print(
            f"Interrupted. Finished runs are kept: continue with `bench run ... --batch {name} "
            "--resume`.",
            markup=False,
            soft_wrap=True,
        )
        raise typer.Exit(EXIT_INTERRUPTED) from None

    records = collect_results(batch_dir)
    meta = read_batch(batch_dir)
    (batch_dir / "report.md").write_text(reporting.render_markdown(meta, records), encoding="utf-8")
    (batch_dir / "results.csv").write_text(reporting.render_csv(records), encoding="utf-8")
    passed = sum(1 for r in records if r.outcome == "passed")
    clean = passed == len(specs)
    if g.json:
        print_json(
            {
                "batch": name, "directory": str(batch_dir), "planned": len(specs),
                "passed": passed, "report": str(batch_dir / "report.md"),
                "csv": str(batch_dir / "results.csv"), "spent_usd": ledger.spent,
                "runs": [
                    {"task": r.task, "strategy": r.strategy, "repeat": r.repeat,
                     "outcome": r.outcome, "cost_usd": r.cost_usd, "reason": r.reason}
                    for r in records
                ],
            }
        )  # fmt: skip
    else:
        _print_groups(g, records)
        console.print(f"Report: {batch_dir / 'report.md'}", markup=False, soft_wrap=True)
    raise typer.Exit(0 if clean or allow_failures else EXIT_FAILED)


def _print_groups(g: Globals, records: list[RunRecord]) -> None:
    table = Table(*["Strategy", *reporting.GROUP_HEADERS[1:]], box=None)
    for row in reporting.group_rows(reporting.group_by(records, lambda r: r.strategy)):
        table.add_row(*row)
    g.console(stderr=True).print(table)


def _dry_run(
    g: Globals,
    specs: list[Any],
    not_applicable: list[tuple[str, str]],
    settings: Any,
    budget_usd: float | None,
) -> None:
    found = estimating.estimate_batch(specs, settings)
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
    if found.unpriced_models:
        console.print(
            f"No price for {', '.join(found.unpriced_models)}: the cost is unknown. Add a "
            "[prices] override (docs/CONFIGURATION.md) or rely on --budget-usd.",
            markup=False,
            soft_wrap=True,
        )
    for task, strategy in not_applicable:
        console.print(
            f"  not applicable: {task} × {strategy} (repository modes use the pipeline)",
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


@bench_app.command("report")
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
    out_dir = Path(out).expanduser().resolve() if out else _suite(suite) / ".runs"
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
    if g.json:
        print_json(
            {
                "batch": batch_dir.name,
                "groups": [
                    {"strategy": grp.label, "passed": grp.passed, "counted": grp.counted,
                     "rate": grp.rate, "wilson_low": grp.low, "wilson_high": grp.high,
                     "cost_per_success_usd": grp.cost_per_success}
                    for grp in reporting.group_by(records, lambda r: r.strategy)
                ],
            }
        )  # fmt: skip
    else:
        sys.stdout.write(text)
