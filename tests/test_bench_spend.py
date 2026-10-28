"""The release spend ledger: the cap, refusals with the numbers, unknown and subscription costs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from bench_helpers import record

from engineering_team.bench.spend import SpendEntry, SpendLedger, SpendRefused
from engineering_team.bench.tasks import BenchError
from engineering_team.cli.bench_spend_command import actual_cost, record_spend, runs_since


def ledger(tmp_path: Path, allocations: str = "T25 = 2.80\nT33 = 9.70", total: float = 15.0):
    (tmp_path / "budget.toml").write_text(f"total_usd = {total}\n[allocations]\n{allocations}\n")
    return SpendLedger(tmp_path)


def entry(task: str = "T25", estimate: float = 0.5, actual: float | None = 0.5, **fields: object):
    values: dict[str, object] = {
        "date": "2026-10-28T00:00:00+00:00", "task": task, "command": "bench run",
        "estimated_usd": estimate, "actual_usd": actual,
    }  # fmt: skip
    values.update(fields)
    return SpendEntry.model_validate(values)


def test_a_command_that_fits_its_allocation_is_allowed(tmp_path: Path) -> None:
    book = ledger(tmp_path)
    book.record(entry(actual=2.0))

    book.check("T25", 0.80, cap=0.80)  # exactly what is left


@pytest.mark.parametrize(
    ("estimate", "cap", "message"),
    [
        (
            0.81,
            None,
            r"T25 has spent \$2.00 of its \$2.80 \(\$0.80 left\); this is estimated at \$0.81",
        ),
        (0.10, 1.00, r"The cap \$1.00 is more than the \$0.80 left in T25"),
    ],
)
def test_a_command_over_its_allocation_is_refused_with_the_numbers(
    tmp_path: Path, estimate: float, cap: float | None, message: str
) -> None:
    book = ledger(tmp_path)
    book.record(entry(actual=2.0))

    with pytest.raises(SpendRefused, match=message):
        book.check("T25", estimate, cap)


def test_the_release_total_caps_every_allocation(tmp_path: Path) -> None:
    book = ledger(tmp_path, "T25 = 2.0", total=3.0)
    book.record(entry("T6", actual=2.0))  # spend outside today's allocations still counts

    with pytest.raises(SpendRefused, match=r"The release has \$1.00 of \$3.00 left"):
        book.check("T25", 1.5)


def test_an_unknown_cost_is_charged_as_its_estimate(tmp_path: Path) -> None:
    book = ledger(tmp_path)
    book.record(entry(estimate=1.25, actual=None))

    assert book.spent("T25") == pytest.approx(1.25)
    with pytest.raises(SpendRefused):
        book.check("T25", 1.60)


def test_subscription_runs_are_recorded_but_not_charged(tmp_path: Path) -> None:
    book = ledger(tmp_path)
    book.record(entry(estimate=5, actual=4, billing="subscription"))

    assert book.spent() == 0
    assert len(book.entries()) == 1
    book.check("T25", 2.80)


def test_the_ledger_is_append_only(tmp_path: Path) -> None:
    book = ledger(tmp_path)
    book.record(entry(actual=0.1))
    first = (tmp_path / "spend.jsonl").read_text()
    book.record(entry(actual=0.2))

    text = (tmp_path / "spend.jsonl").read_text()
    assert text.startswith(first) and len(text.splitlines()) == 2
    assert json.loads(text.splitlines()[1])["actual_usd"] == 0.2


def test_a_damaged_ledger_or_budget_says_which_line(tmp_path: Path) -> None:
    book = ledger(tmp_path)
    (tmp_path / "spend.jsonl").write_text(entry().model_dump_json() + "\n{not json}\n")

    with pytest.raises(BenchError, match="spend.jsonl:2"):
        book.entries()
    (tmp_path / "budget.toml").write_text("total_usd = 1.0\n[allocations]\nT25 = 2.0\n")
    with pytest.raises(BenchError, match="more than total_usd"):
        book.budget()
    with pytest.raises(BenchError, match="No .*budget.toml"):
        SpendLedger(tmp_path / "elsewhere").budget()


def test_a_batch_costs_the_sum_of_its_runs_or_unknown(tmp_path: Path) -> None:
    priced = [record(cost_usd=0.1), record(repeat=2, cost_usd=0.2)]
    not_started = record(repeat=3, outcome="skipped", cost_usd=None)

    assert actual_cost([*priced, not_started]) == pytest.approx(0.3)
    assert actual_cost([*priced, record(repeat=4, cost_usd=None, tokens=10)]) is None


def test_a_finished_batch_is_recorded_with_its_estimate_and_cost(tmp_path: Path) -> None:
    book = ledger(tmp_path)

    written = record_spend(book, "T25", 0.4, [record(cost_usd=0.15)], "b1")

    assert (written.task, written.estimated_usd, written.actual_usd, written.batch) == (
        "T25", 0.4, 0.15, "b1"
    )  # fmt: skip
    assert book.spent("T25") == pytest.approx(0.15)


def test_a_resumed_batch_charges_only_the_runs_it_started() -> None:
    kept = record(started="2026-10-28T10:00:00+00:00", cost_usd=0.4)
    rerun = record(repeat=2, started="2026-10-28T12:00:05+00:00", cost_usd=0.1)
    skipped = record(repeat=3, outcome="skipped", started="", cost_usd=None)

    assert runs_since([kept, rerun, skipped], "2026-10-28T12:00:00+00:00") == [rerun]
