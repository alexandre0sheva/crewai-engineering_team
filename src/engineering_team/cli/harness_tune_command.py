"""``harness tune`` and ``harness adopt``: improve a domain's harness on the benchmark's dev split,
judged by hidden checks, and copy an accepted result into a pack (docs/HARNESS.md#tuning)."""

from __future__ import annotations

import shlex
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, cast

import typer

from engineering_team.bench import estimate as estimating
from engineering_team.bench.fake_team import FAKE_MODEL, SOLUTIONS
from engineering_team.bench.plan import RunOptions, RunSpec
from engineering_team.bench.selection import in_domain, split_values
from engineering_team.bench.spend import SpendEntry, SpendLedger
from engineering_team.bench.tasks import BenchError, BenchTask, load_suite
from engineering_team.cli.bench_command import _config_path, _settings
from engineering_team.cli.bench_options import SuiteOption, TasksDirOption, suite_path
from engineering_team.cli.bench_options import tasks_dir_path as tasks_path
from engineering_team.cli.bench_spend_command import check_spend
from engineering_team.cli.context import fail, one_of, print_json
from engineering_team.cli.context import get as get_globals
from engineering_team.cli.harness_command import _domain
from engineering_team.harness.domains import Domain
from engineering_team.learning.ledger import LEDGER_FILE, ROLLOUTS_PER_REPEAT, Mode, TuningLedger
from engineering_team.learning.library import domain_folder
from engineering_team.learning.tuning import prompts
from engineering_team.learning.tuning.loop import TuneConfig, Tuner
from engineering_team.learning.tuning.output import (
    NOTES_FILE,
    AdoptError,
    adopt,
    bundled_root,
    default_directory,
    write_tuning,
)
from engineering_team.learning.tuning.rollouts import BenchRollouts, protected_texts
from engineering_team.learning.tuning.sessions import (
    build_tuning_llm,
    tuning_model,
    worst_case_usd,
)
from engineering_team.learning.tuning.tree import Checker, pack_of
from engineering_team.model_routing import PROFILE_NAMES, PROVIDERS

EXIT_INCOMPLETE = 3
PROMPT_CHARS = 20_000  # a session's prompt, for the estimate (traces are capped at 8,000)


def plan_counts(dev: int, heldout: int, k: int, mode: str) -> tuple[int, int]:
    """``(rollouts, sessions)`` a complete loop makes, evaluation included (at most)."""

    rollouts = dev * k * ROLLOUTS_PER_REPEAT[mode] + dev * k + 2 * heldout * k
    return rollouts, 3 * dev * k


def offline_llm(sessions: int) -> Any:
    """The proposers and composer of ``--fake``: no model, so every proposal is empty and every
    composition keeps the base (the loop's plumbing runs, nothing is invented)."""

    from engineering_team.testing import ScriptedLLM

    def answer(messages: list[dict[str, Any]], tools: Any) -> str:
        if messages[0].get("content") == prompts.COMPOSE_PROMPT:
            return '{"decision": "keep_base", "notes": []}'
        return '{"edits": [], "notes": "offline: no model was called"}'

    return ScriptedLLM([answer] * (2 * sessions + 2), model=FAKE_MODEL)


def _tasks(
    suite: Path, tasks_dir: str | None, domain: Domain, ids: list[str] | None
) -> tuple[list[BenchTask], list[BenchTask], list[BenchTask]]:
    try:
        every = load_suite(suite, tasks_path(tasks_dir))
    except BenchError as exc:
        fail(str(exc))
    wanted = set(split_values(ids))
    unknown = sorted(wanted - {t.id for t in every})
    if unknown:
        fail(f"Unknown task(s): {', '.join(unknown)}.")
    ours = [t for t in every if in_domain(t, domain) and t.applies_to("pipeline")]
    dev = [t for t in ours if t.split == "dev" and (not wanted or t.id in wanted)]
    heldout = [t for t in ours if t.split == "heldout"]
    if not dev:
        fail(f"No dev task of the domain {domain.key} to tune on (see `bench list`).")
    return every, sorted(dev, key=lambda t: t.id), sorted(heldout, key=lambda t: t.id)


def tune(
    ctx: typer.Context,
    domain: Annotated[str, typer.Option("--domain", help="intent/stack, e.g. bugfix/python.")],
    split: Annotated[
        str,
        typer.Option(callback=one_of(["dev"]), help="The split to tune on (held-out only judges)."),
    ] = "dev",
    k: Annotated[int, typer.Option("--k", min=1, help="Base rollouts per dev task.")] = 1,
    mode: Annotated[
        str,
        typer.Option(
            callback=one_of(["parallel", "sequential"]),
            help="parallel: INV and DOM propose from the same rollout; sequential: DOM builds on "
            "INV's candidate.",
        ),
    ] = "parallel",
    budget_usd: Annotated[
        float | None,
        typer.Option(min=0.01, help="Spend cap for the whole tuning (required for a live run)."),
    ] = None,
    run_budget_usd: Annotated[
        float | None, typer.Option(min=0.01, help="Cap for one rollout (default: a share).")
    ] = None,
    tasks: Annotated[
        list[str] | None, typer.Option(help="Only these dev tasks (default: the domain's).")
    ] = None,
    parallel: Annotated[int, typer.Option(min=1, help="Rollouts at the same time.")] = 1,
    provider: Annotated[str | None, typer.Option(callback=one_of(list(PROVIDERS)))] = None,
    profile: Annotated[str | None, typer.Option(callback=one_of(list(PROFILE_NAMES)))] = None,
    config: Annotated[str | None, typer.Option(help="A settings file for the team.")] = None,
    sandbox: Annotated[
        str | None, typer.Option(callback=one_of(["local", "docker"]), help="Where commands run.")
    ] = None,
    fake: Annotated[
        bool,
        typer.Option("--fake", help="Offline: the fake team, and proposers that change nothing."),
    ] = False,
    fake_solution: Annotated[
        str, typer.Option(callback=one_of(list(SOLUTIONS)), help="With --fake: as bench run.")
    ] = "reference",
    allocation: Annotated[
        str, typer.Option(help="The release budget allocation a live tuning is charged to.")
    ] = "T33",
    allow_unpriced: Annotated[
        bool, typer.Option(help="Run even if a model has no price (no cap can stop it).")
    ] = False,
    acceptance_sandbox: Annotated[
        str, typer.Option(callback=one_of(["docker", "local"]), help="Where Node/Go checks run.")
    ] = "docker",
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show the plan and its estimate; run nothing.")
    ] = False,
    out: Annotated[
        str | None,
        typer.Option(help="The tuning directory (default: <suite>/tuning/<date>/<domain>)."),
    ] = None,
    runs: Annotated[
        str | None, typer.Option(help="Where rollouts run (default: <suite>/.runs).")
    ] = None,
    suite: SuiteOption = None,
    tasks_dir: TasksDirOption = None,
) -> None:
    """Tune a domain's harness: base rollouts on the dev tasks, INV (procedure) and DOM
    (knowledge) proposals probed on the same tasks, a composed harness accepted only when it does
    better on dev and no worse on held-out (hidden checks judge). Writes a reviewable diff."""

    g = get_globals(ctx)
    suite_dir = suite_path(suite)
    chosen = _domain(domain, Path.cwd())
    every, dev, heldout = _tasks(suite_dir, tasks_dir, chosen, tasks)
    config_file = _config_path(config)
    settings = _settings(provider, profile, config_file, credentials=not fake)
    rollouts_n, sessions_n = plan_counts(len(dev), len(heldout), k, mode)
    console = g.console(stderr=True)
    model = FAKE_MODEL if fake else tuning_model(settings).model
    per_session = worst_case_usd(settings, PROMPT_CHARS, model) if not fake else 0.0
    estimate: float | None = 0.0
    if not fake:
        if not allow_unpriced and (estimating.unpriced_models(settings) or per_session is None):
            fail("A model has no price, so no cap can stop the tuning; pass --allow-unpriced.")
        per_dev = k * (ROLLOUTS_PER_REPEAT[mode] + 1)
        specs = [RunSpec(t, "pipeline", 1) for t in dev for _ in range(per_dev)]
        specs += [RunSpec(t, "pipeline", 1) for t in heldout for _ in range(2 * k)]
        team = estimating.estimate_batch(specs, settings).total_usd
        estimate = None if team is None else team + (per_session or 0.0) * sessions_n
    console.print(
        f"Tuning {chosen.key} ({mode}, k={k}): dev {', '.join(t.id for t in dev)}; held-out "
        f"{', '.join(t.id for t in heldout) or 'none'}; up to {rollouts_n} rollouts and "
        f"{sessions_n} sessions" + ("" if fake else f", estimated ${estimate or 0:.2f}") + ".",
        markup=False,
        soft_wrap=True,
    )
    if dry_run:
        return
    run_cap, charged = None, None
    spend = SpendLedger(suite_dir)
    if not fake:
        if budget_usd is None:
            fail("A live tuning needs --budget-usd (try --dry-run first).")
        settings.check_ready(require_credentials=True)
        charged = check_spend(spend, allocation, estimate, budget_usd)
        reserve = (per_session or 0.0) * sessions_n
        run_cap = run_budget_usd or round((budget_usd - reserve) / rollouts_n, 4)
        if run_cap <= 0:
            fail(f"--budget-usd ${budget_usd:.2f} does not cover the sessions (${reserve:.2f}).")
    target = Path(out).expanduser().resolve() if out else default_directory(suite_dir, chosen)
    if target.exists() and any(target.iterdir()):
        fail(f"{target} exists; pass another --out.")
    stamp = f"{datetime.now():%Y%m%d-%H%M%S}"
    runs_root = (Path(runs).expanduser().resolve() if runs else suite_dir / ".runs") / (
        f"tune-{stamp}-{str(domain_folder(chosen.key)).replace('/', '-')}"
    )
    options = RunOptions(
        batch="tune", batch_dir=runs_root, provider=provider, profile=profile, sandbox=sandbox,
        config=config_file, fake=fake, fake_solution=fake_solution, run_budget_usd=run_cap,
        acceptance_sandbox=acceptance_sandbox, repo_cache=suite_dir / ".cache" / "repos",
    )  # fmt: skip
    protected = protected_texts(every)
    from engineering_team.harness.library import resolve
    from engineering_team.settings import secret_values

    base = resolve(chosen)
    checker = Checker(chosen, base, settings, protected, tuple(secret_values()))
    runner = BenchRollouts(options, runs_root, pack_of(chosen), protected)
    ledger = TuningLedger(target / LEDGER_FILE)
    tune_config = TuneConfig(dev, heldout, k, cast(Mode, mode), budget_usd, run_cap, parallel)
    llm = offline_llm(sessions_n) if fake else build_tuning_llm(settings)

    def show(line: str) -> None:
        if not g.quiet:
            console.print(line, markup=False, highlight=False, soft_wrap=True)

    tuner = Tuner(tune_config, runner, llm, checker, ledger, protected, settings, show)
    try:
        result = tuner.run()
    except KeyboardInterrupt:
        runner.cancel.set()
        runner.registry.terminate_all()
        raise typer.Exit(130) from None
    finally:
        if charged is not None:
            _record_spend(spend, allocation, charged, ledger, target)
    data = write_tuning(
        target, result, tune_config, base, ledger, rollouts=str(runs_root), fake=fake, model=model
    )
    if g.json:
        print_json({"directory": str(target), **data})
    else:
        g.console().print(
            f"{result.notes[-1] if result.notes else data['decision']}\n"
            f"Notes: {target / NOTES_FILE}",
            markup=False,
            soft_wrap=True,
        )
    raise typer.Exit(EXIT_INCOMPLETE if result.stopped or result.identity else 0)


def _record_spend(
    spend: SpendLedger, allocation: str, charged: float, ledger: TuningLedger, target: Path
) -> None:
    cost, unknown = ledger.cost()
    spend.record(
        SpendEntry(
            date=datetime.now(UTC).isoformat(timespec="seconds"),
            task=allocation,
            command=shlex.join(["engineering-team", *sys.argv[1:]]),
            estimated_usd=round(charged, 6),
            actual_usd=None if unknown else cost,
            batch=target.name,
            note=f"harness tune: {len(ledger.entries())} rollouts and sessions",
        )
    )


def adopt_command(
    ctx: typer.Context,
    directory: Annotated[str, typer.Argument(help="A tuning directory (with decision.json).")],
    to: Annotated[
        str,
        typer.Option(
            callback=one_of(["user", "bundled"]),
            help="user: ~/.config/engineering-team/harness; bundled: this checkout's library.",
        ),
    ] = "user",
    force: Annotated[
        bool, typer.Option("--force", help="Adopt a KEEP_BASE result, or over a changed base.")
    ] = False,
) -> None:
    """Copy a tuning's composed harness into a pack (it must have decided to adopt)."""

    from engineering_team.settings.loading import user_config_directory

    g = get_globals(ctx)
    folder = Path(directory).expanduser().resolve()
    try:
        if to == "bundled":
            root = bundled_root()
        else:
            user = user_config_directory()
            if user is None:
                fail("There is no home directory for the user's harness; use --to bundled.")
            root = user / "harness"
        written = adopt(folder, root, force=force)
    except AdoptError as exc:
        fail(str(exc))
    if g.json:
        print_json({"target": str(root), "files": [str(p) for p in written]})
        return
    console = g.console()
    for path in written:
        console.print(f"wrote {path}", markup=False, soft_wrap=True)
