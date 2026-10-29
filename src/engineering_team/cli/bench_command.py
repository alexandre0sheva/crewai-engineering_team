"""``bench``: run the benchmark suite against the team, and report on a batch of runs.

The suite (``benchmarks/``) ships in the source checkout, not the wheel. ``bench run`` makes one
subprocess per run in an isolated workspace, judges each with hidden checks, and writes a report;
``--fake`` replays the reference solutions offline (no model, no cost) to validate the harness
itself; ``--dry-run`` prices the batch without starting anything. A live batch is charged to the
release's spend ledger (``bench spend``) and refused when it cannot afford it. See
``docs/BENCHMARKS.md``.
"""

from __future__ import annotations

import tempfile
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.table import Table

from engineering_team.bench import estimate as estimating
from engineering_team.bench import report as reporting
from engineering_team.bench.agents import BILLINGS
from engineering_team.bench.batch import BudgetLedger, run_batch, write_batch_manifest
from engineering_team.bench.export import write_results_json
from engineering_team.bench.fake_team import SOLUTIONS
from engineering_team.bench.plan import STRATEGIES, RunOptions, is_agent, plan_runs
from engineering_team.bench.results import RunRecord, collect_results, read_batch
from engineering_team.bench.selection import TaskFilter, select_tasks, split_values
from engineering_team.bench.spend import SpendLedger
from engineering_team.bench.tasks import BenchError, load_suite
from engineering_team.cli.bench_agents import agent_names, agent_pairs, probe_agents
from engineering_team.cli.bench_options import OutOption, SuiteOption, suite_path
from engineering_team.cli.bench_report_command import dry_run as show_dry_run
from engineering_team.cli.bench_report_command import print_groups, report
from engineering_team.cli.bench_spend_command import check_spend, record_batch_spend, spend
from engineering_team.cli.context import fail, one_of, print_json
from engineering_team.cli.context import get as get_globals
from engineering_team.model_routing import PROFILE_NAMES, PROVIDERS

EXIT_FAILED = 3
EXIT_INTERRUPTED = 130

bench_app = typer.Typer(help="Run and report the benchmark suite.", no_args_is_help=True)
bench_app.command("report")(report)
bench_app.command("spend")(spend)


def _strategies(values: list[str] | None, *, agents: bool = False) -> list[str]:
    """The team's strategies to run: ``pipeline`` unless told otherwise, or none when only
    external agents were asked for."""

    default = [] if agents else ["pipeline"]
    chosen = [s.strip() for raw in values or default for s in raw.split(",") if s.strip()]
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
        tasks = load_suite(suite_path(suite))
    except BenchError as exc:
        fail(str(exc))
    if g.json:
        print_json(
            [
                {
                    "id": t.id, "kind": t.kind, "mode": t.mode, "intent": t.intent,
                    "split": t.split, "size": t.size, "difficulty": t.difficulty,
                    "languages": t.languages, "toolchains": t.toolchains,
                    "criteria": [c.id for c in t.criteria], "title": t.title,
                }
                for t in tasks
            ]
        )  # fmt: skip
        return
    table = Table("Task", "Intent", "Split", "Languages", "Size", "Difficulty", "Title", box=None)
    for t in tasks:
        languages = ", ".join(t.languages) + (" (Docker)" if t.toolchains else "")
        table.add_row(t.id, t.intent, t.split, languages, t.size, t.difficulty, t.title)
    g.console().print(table)


@bench_app.command("run")
def run(
    ctx: typer.Context,
    tasks: Annotated[
        list[str] | None, typer.Option(help="Task ids, comma-separated or repeated (default: all).")
    ] = None,
    split: Annotated[
        str,
        typer.Option(
            "--split",
            "--subset",
            callback=one_of(["all", "dev", "heldout"]),
            help="dev, heldout, or all (--subset is the 0.2.0 name).",
        ),
    ] = "all",
    intent: Annotated[
        list[str] | None, typer.Option(help="Only these intents (bugfix, review, ...).")
    ] = None,
    language: Annotated[
        list[str] | None, typer.Option(help="Only tasks in these languages (python, go, ...).")
    ] = None,
    size: Annotated[
        list[str] | None, typer.Option(help="Only these sizes (trivial, small, medium, large).")
    ] = None,
    difficulty: Annotated[
        list[str] | None, typer.Option(help="Only these difficulties (easy, medium, hard).")
    ] = None,
    strategy: Annotated[
        list[str] | None,
        typer.Option(help="pipeline or single; repeat or comma-separate."),
    ] = None,
    agent: Annotated[
        list[str] | None,
        typer.Option(
            help="External agents to run on the same tasks and checks: claude-code, codex, "
            "opencode, aider; repeat or comma-separate. Alone, they replace the team."
        ),
    ] = None,
    agent_model: Annotated[
        list[str] | None,
        typer.Option(help="Pin an agent's model, AGENT=MODEL (repeatable); default: its own."),
    ] = None,
    agent_billing: Annotated[
        list[str] | None,
        typer.Option(
            help="Override how an agent is paid for, AGENT=api|subscription (default: api when "
            "an API key is in the environment, else a plan for Claude Code and Codex)."
        ),
    ] = None,
    repeat: Annotated[int, typer.Option(min=1, help="Runs per task and strategy or agent.")] = 1,
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
            callback=one_of(list(SOLUTIONS)),
            help="With --fake: the reference, the reference with its sabotage applied (broken), "
            "or nothing written (fixture).",
        ),
    ] = "reference",
    acceptance_sandbox: Annotated[
        str,
        typer.Option(
            callback=one_of(["docker", "local"]),
            help="Where the checks of Node/Go tasks run: their Docker image, or this machine.",
        ),
    ] = "docker",
    node_modules: Annotated[
        str | None,
        typer.Option(
            envvar="BENCH_NODE_MODULES",
            help="With --acceptance-sandbox local: a node_modules directory holding the Node "
            "tasks' packages (benchmarks/images/node/package.json).",
        ),
    ] = None,
    allocation: Annotated[
        str | None,
        typer.Option(
            help="The release budget allocation a live batch is charged to (a plan task such as "
            "T25; see `bench spend`). Required for a live run."
        ),
    ] = None,
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
    suite_dir = suite_path(suite)
    agents = agent_names(agent)
    strategies = _strategies(strategy, agents=bool(agents))
    models = agent_pairs(agent_model, "--agent-model", agents)
    billing_override = agent_pairs(agent_billing, "--agent-billing", agents, BILLINGS[:2])
    task_filter = TaskFilter(
        split=split,
        intents=tuple(split_values(intent)),
        languages=tuple(split_values(language)),
        sizes=tuple(split_values(size)),
        difficulties=tuple(split_values(difficulty)),
    )
    try:
        chosen = select_tasks(load_suite(suite_dir), tasks, task_filter)
    except BenchError as exc:
        fail(str(exc))
    specs, not_applicable = plan_runs(chosen, [*strategies, *agents], repeat)
    if not specs:
        fail("Nothing to run: the selected strategies do not apply to the selected tasks.")
    agents = [a for a in agents if any(s.strategy == a for s in specs)]
    console = g.console(stderr=True)

    config_file = _config_path(config)
    ledger = SpendLedger(suite_dir)
    if dry_run:
        show_dry_run(
            g, specs, not_applicable, _settings(provider, profile, config_file), budget_usd
        )
        return

    infos = probe_agents(agents, billing_override)
    agent_billing_of = {name: info.billing for name, info in infos.items()}
    team_specs = [s for s in specs if not is_agent(s.strategy)]
    paid_agent_runs = sum(1 for s in specs if agent_billing_of.get(s.strategy) != "subscription"
                          and is_agent(s.strategy))  # fmt: skip
    billed_runs = (0 if fake else len(team_specs)) + paid_agent_runs
    charged: float | None = None
    if agents and not fake and not allocation:
        fail(
            "Runs of external agents are recorded in the release ledger, even on a plan: pass "
            "--allocation TASK (see `bench spend`)."
        )
    if billed_runs:
        if budget_usd is None:
            fail("A live run needs --budget-usd (a spend cap for the batch); try --dry-run first.")
        run_cap = run_budget_usd or round(budget_usd / billed_runs, 4)
        team_estimate: float | None = 0.0
        if not fake and team_specs:
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
            team_estimate = estimating.estimate_batch(team_specs, settings).total_usd
        # An agent's cost is only known afterwards: its estimate is the cap of each run.
        estimate = None if team_estimate is None else team_estimate + run_cap * paid_agent_runs
        charged = check_spend(ledger, allocation, estimate, budget_usd)
        g.console(stderr=True).print(
            f"Estimated {reporting.money(estimate)} for {billed_runs} billed run(s) (cap "
            f"${budget_usd:.2f}); {allocation} has spent ${ledger.spent(allocation):.4f}.",
            markup=False,
            soft_wrap=True,
        )
    else:
        run_cap = None
    if resume and not batch:
        fail("--resume needs --batch NAME.")
    out_dir = Path(out).expanduser().resolve() if out else suite_dir / ".runs"
    name = batch or f"{datetime.now():%Y%m%d-%H%M%S}{'-fake' if fake else ''}"
    batch_dir = out_dir / name
    if batch_dir.exists() and not resume:
        fail(f"{batch_dir} exists. Choose another --batch name, or pass --resume to continue it.")
    options = RunOptions(
        batch=name, batch_dir=batch_dir, provider=provider, profile=profile, sandbox=sandbox,
        config=config_file, fake=fake, fake_solution=fake_solution,
        run_budget_usd=run_cap, resume=resume, agent_models=models, agent_billing=agent_billing_of,
        agent_versions={n: i.version for n, i in infos.items() if i.version},
        acceptance_sandbox=acceptance_sandbox, node_modules=node_modules,
    )  # fmt: skip
    batch_dir.mkdir(parents=True, exist_ok=True)
    write_batch_manifest(
        options, chosen, [*strategies, *agents], repeat, planned=len(specs),
        not_applicable=not_applicable, budget_usd=budget_usd, parallel=parallel,
        agents={
            n: {"version": i.version, "model": models.get(n), "billing": i.billing}
            for n, i in infos.items()
        },
    )  # fmt: skip

    began = datetime.now(UTC).isoformat(timespec="seconds")
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

    budget = BudgetLedger(budget_usd if billed_runs else None)
    if not g.quiet:
        console.print(
            f"Batch {name}: {len(specs)} run(s), {parallel} at a time"
            + (" (offline, scripted)" if fake and not agents else "")
            + (f", budget ${budget_usd:.2f}" if budget_usd is not None else "")
            + f". Results: {batch_dir}",
            markup=False,
            soft_wrap=True,
        )
    try:
        run_batch(specs, options, parallel=parallel, ledger=budget, on_result=show, cancel=cancel)
    except KeyboardInterrupt:
        console.print(
            f"Interrupted. Finished runs are kept: continue with `bench run ... --batch {name} "
            "--resume`.",
            markup=False,
            soft_wrap=True,
        )
        raise typer.Exit(EXIT_INTERRUPTED) from None
    finally:
        if allocation and not (fake and not agents):  # live: what it spent goes in the ledger
            record_batch_spend(
                console, ledger, allocation, charged, collect_results(batch_dir), began, name
            )

    records = collect_results(batch_dir)
    meta = read_batch(batch_dir)
    (batch_dir / "report.md").write_text(reporting.render_markdown(meta, records), encoding="utf-8")
    (batch_dir / "results.csv").write_text(reporting.render_csv(records), encoding="utf-8")
    write_results_json(batch_dir, meta, records)
    passed = sum(1 for r in records if r.outcome == "passed")
    clean = passed == len(specs)
    if g.json:
        print_json(
            {
                "batch": name, "directory": str(batch_dir), "planned": len(specs),
                "passed": passed, "report": str(batch_dir / "report.md"),
                "csv": str(batch_dir / "results.csv"),
                "results": str(batch_dir / "results.json"), "spent_usd": budget.spent,
                "runs": [
                    {"task": r.task, "strategy": r.strategy, "agent": r.agent, "repeat": r.repeat,
                     "billing": r.billing,
                     "outcome": r.outcome, "cost_usd": r.cost_usd, "reason": r.reason}
                    for r in records
                ],
            }
        )  # fmt: skip
    else:
        print_groups(g, records)
        console.print(f"Report: {batch_dir / 'report.md'}", markup=False, soft_wrap=True)
    raise typer.Exit(0 if clean or allow_failures else EXIT_FAILED)
