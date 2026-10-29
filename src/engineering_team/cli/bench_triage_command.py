"""``bench triage``: check and score triage on the labelled cases (docs/BENCHMARKS.md).

Offline (the default) it checks every case's deterministic signals and scores the signals-only
guess: free, and a mismatch fails the command (exit 3). ``--live`` also makes one cheap-tier
model call per case and scores it; like every live ``bench`` command it prices the set first,
is charged to an allocation of the spend ledger, refuses to start when it cannot afford it, and
stops once ``--max-cost-usd`` is spent.
"""

from __future__ import annotations

import json
import shlex
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.table import Table

from engineering_team.bench import report as reporting
from engineering_team.bench.spend import SpendEntry, SpendLedger, now
from engineering_team.bench.tasks import BenchError
from engineering_team.bench.triage_eval import (
    CASES_FILE,
    CaseOutcome,
    Repositories,
    TriageCase,
    load_cases,
    outcome,
    render_markdown,
    score,
    signal_problems,
)
from engineering_team.cli.bench_options import SuiteOption, suite_path
from engineering_team.cli.bench_spend_command import check_spend
from engineering_team.cli.context import fail, one_of, print_json
from engineering_team.cli.context import get as get_globals
from engineering_team.intake.signals import Signals
from engineering_team.intake.triage import MAX_OUTPUT_TOKENS, build_messages, triage
from engineering_team.model_routing import PROFILE_NAMES, PROVIDERS

EXIT_FAILED = 3
CHARS_PER_TOKEN = 4
EXPECTED_OUTPUT_TOKENS = 400  # a typical answer; the cap (MAX_OUTPUT_TOKENS) bounds the range


def triage_command(
    ctx: typer.Context,
    live: Annotated[
        bool, typer.Option("--live", help="Also ask the cheap-tier model once per case (costs).")
    ] = False,
    cases: Annotated[
        str | None, typer.Option(help="The cases file (default: <suite>/triage/cases.yaml).")
    ] = None,
    case: Annotated[
        list[str] | None, typer.Option("--case", help="Only these case ids (repeatable).")
    ] = None,
    provider: Annotated[str | None, typer.Option(callback=one_of(list(PROVIDERS)))] = None,
    profile: Annotated[str | None, typer.Option(callback=one_of(list(PROFILE_NAMES)))] = None,
    config: Annotated[str | None, typer.Option(help="A settings file for the model.")] = None,
    allocation: Annotated[
        str | None, typer.Option(help="The spend allocation a live run is charged to (T10).")
    ] = None,
    max_cost_usd: Annotated[
        float, typer.Option(min=0.0, help="Stop a live run once it has spent this much.")
    ] = 0.40,
    estimate: Annotated[
        bool, typer.Option("--estimate", help="Price the live run and stop.")
    ] = False,
    out: Annotated[str | None, typer.Option(help="Write results.json and report.md here.")] = None,
    suite: SuiteOption = None,
) -> None:
    """Check triage's signals on the labelled cases and score its answers."""

    g = get_globals(ctx)
    console = g.console()
    root = suite_path(suite)
    path = Path(cases).expanduser().resolve() if cases else root / CASES_FILE
    try:
        chosen = _select(load_cases(path, root / "tasks"), case)
    except BenchError as exc:
        fail(str(exc))
    with tempfile.TemporaryDirectory(prefix="bench-triage-") as scratch:
        repos = Repositories(root / "tasks", Path(scratch))
        signals = {c.id: repos.signals(c) for c in chosen}
    problems = [p for c in chosen for p in signal_problems(c, signals[c.id])]
    offline = [outcome(c, triage(c.request, signals[c.id], mode=c.mode).profile) for c in chosen]
    result: dict[str, Any] = {
        "cases_file": str(path),
        "signal_problems": problems,
        "signals_only": {"scores": score(offline), "outcomes": _dump(offline)},
    }
    if live or estimate:
        result["live"] = _live(
            console, root, chosen, signals, provider, profile, config, allocation,
            max_cost_usd, estimate,
        )  # fmt: skip
    if out or (live and not estimate):
        _write(Path(out) if out else _default_out(root), result)
        console.print(f"Results written to {Path(out) if out else _default_out(root)}")
    if g.json:
        print_json(result)
    else:
        _show(console, len(chosen), problems, result)
    if problems:
        raise typer.Exit(EXIT_FAILED)


def _select(cases: list[TriageCase], ids: list[str] | None) -> list[TriageCase]:
    if not ids:
        return cases
    unknown = sorted(set(ids) - {c.id for c in cases})
    if unknown:
        fail(f"Unknown case: {', '.join(unknown)}.")
    return [c for c in cases if c.id in ids]


def _live(
    console: Any,
    root: Path,
    cases: list[TriageCase],
    signals: dict[str, Signals],
    provider: str | None,
    profile: str | None,
    config: str | None,
    allocation: str | None,
    cap: float,
    estimate_only: bool,
) -> dict[str, Any]:
    from engineering_team.cli.bench_command import _config_path, _settings
    from engineering_team.intake.triage import build_triage_llm, triage_model

    settings = _settings(provider, profile, _config_path(config), credentials=True)
    model = triage_model(settings).model
    price = settings.price_table().lookup(model)
    prompt_tokens = sum(
        sum(len(m["content"]) for m in build_messages(c.request, signals[c.id])) // CHARS_PER_TOKEN
        for c in cases
    )
    estimated = None
    if price is not None:
        estimated = round(
            (prompt_tokens * price.input + len(cases) * EXPECTED_OUTPUT_TOKENS * price.output)
            / 1_000_000,
            6,
        )
    console.print(
        f"Triage on {model}: {len(cases)} calls, about {prompt_tokens:,} prompt tokens, "
        f"estimate {reporting.money(estimated)} (cap ${cap:.2f}; at most {MAX_OUTPUT_TOKENS} "
        "output tokens per call).",
        markup=False,
        soft_wrap=True,
    )
    if estimate_only:
        return {"model": model, "estimate_usd": estimated, "prompt_tokens": prompt_tokens}
    missing = settings.missing_credentials()
    if missing:
        fail("; ".join(missing))
    ledger = SpendLedger(root)
    charged = check_spend(ledger, allocation, estimated, cap)
    llm = build_triage_llm(settings)
    outcomes: list[CaseOutcome] = []
    spent, unknown, stopped = 0.0, False, None
    for case in cases:
        if spent >= cap:
            stopped = f"stopped after {len(outcomes)} cases: ${spent:.4f} reached the cap"
            break
        found = triage(case.request, signals[case.id], llm=llm, mode=case.mode, settings=settings)
        cost = found.usage.cost_usd if found.usage else None
        unknown = unknown or cost is None
        spent += cost or 0.0
        outcomes.append(outcome(case, found.profile, error=found.error, cost_usd=cost))
    actual = None if unknown else round(spent, 8)
    assert allocation is not None  # check_spend refused the run otherwise
    ledger.record(
        SpendEntry(
            date=now(), task=allocation,
            command=shlex.join(["engineering-team", *sys.argv[1:]]),
            estimated_usd=round(charged, 6), actual_usd=actual,
            note=f"bench triage: {len(outcomes)} call(s) on {model}",
        )
    )  # fmt: skip
    console.print(
        f"Spend recorded in {ledger.ledger_path.name}: {allocation} {reporting.money(actual)}.",
        markup=False,
    )
    return {
        "model": model,
        "estimate_usd": estimated,
        "actual_usd": actual,
        "stopped": stopped,
        "scores": score(outcomes),
        "outcomes": _dump(outcomes),
    }


def _dump(outcomes: list[CaseOutcome]) -> list[dict[str, Any]]:
    return [o.model_dump() for o in outcomes]


def _default_out(root: Path) -> Path:
    return root / ".runs" / f"triage-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}"


def _write(directory: Path, result: dict[str, Any]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "results.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    parts = []
    if "scores" in result.get("live", {}):
        live = result["live"]
        outcomes = [CaseOutcome.model_validate(o) for o in live["outcomes"]]
        parts.append(render_markdown(f"Triage, {live['model']}", live["scores"], outcomes))
    offline = result["signals_only"]
    parts.append(
        render_markdown(
            "Triage, signals only (no model)",
            offline["scores"],
            [CaseOutcome.model_validate(o) for o in offline["outcomes"]],
        )
    )
    (directory / "report.md").write_text("\n".join(parts), encoding="utf-8")


def _show(console: Any, count: int, problems: list[str], result: dict[str, Any]) -> None:
    if problems:
        console.print(f"{len(problems)} signal mismatch(es):", style="bold red")
        for problem in problems:
            console.print(f"  {problem}", markup=False)
    else:
        console.print(f"Signals: all {count} cases match their expectations.")
    table = Table("Triage", "Primary", "Size", "Target", "Secondary", "Asked", box=None)
    rows = [("signals only", result["signals_only"]["scores"])]
    if "scores" in result.get("live", {}):
        rows.insert(0, (result["live"]["model"], result["live"]["scores"]))
    for name, scores in rows:
        table.add_row(
            name,
            *(_pct(scores[k]) for k in ("primary_accuracy", "size_accuracy", "target_accuracy")),
            _pct(scores["secondary_recall"]),
            _pct(scores["clarify_agreement"]),
        )
    console.print(table)


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.0f} %"
