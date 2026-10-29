"""Side-by-side comparison of the contenders in a batch: our strategies and external agents.

Paired by task: each task gives every contender a pass rate over its counted runs, and a contender
*wins* a task when its rate is higher. Only tasks both have counted runs for are compared.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from itertools import combinations

from engineering_team.bench.results import RunRecord

Rates = dict[str, dict[str, tuple[int, int]]]  # contender -> task -> (passed, counted)


@dataclass(frozen=True)
class Pairing:
    a: str
    b: str
    tasks: int  # tasks both have counted runs for
    a_wins: int
    b_wins: int
    ties: int


def task_rates(records: Iterable[RunRecord]) -> Rates:
    tally: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for record in records:
        if record.counted:
            cell = tally[record.contender][record.task]
            cell[0] += record.outcome == "passed"
            cell[1] += 1
    return {
        c: {t: (p, n) for t, (p, n) in sorted(tasks.items())} for c, tasks in sorted(tally.items())
    }


def pairings(rates: Rates) -> list[Pairing]:
    found = []
    for a, b in combinations(rates, 2):
        shared = sorted(rates[a].keys() & rates[b].keys())
        a_wins = b_wins = 0
        for task in shared:
            (pa, na), (pb, nb) = rates[a][task], rates[b][task]
            left, right = pa * nb, pb * na  # compare pa/na with pb/nb without dividing
            a_wins += left > right
            b_wins += right > left
        found.append(Pairing(a, b, len(shared), a_wins, b_wins, len(shared) - a_wins - b_wins))
    return found


def _table(headers: list[str], rows: list[list[str]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(" --- " for _ in headers) + "|"]
    return lines + ["| " + " | ".join(row) + " |" for row in rows]


def render_comparison(records: list[RunRecord]) -> list[str]:
    """Markdown lines for the paired comparison (empty when there is only one contender)."""

    rates = task_rates(records)
    if len(rates) < 2:
        return []
    lines = ["## Paired comparison, per task", ""]
    lines += _table(
        ["Pair", "Tasks compared", "First wins", "Second wins", "Ties"],
        [
            [f"{p.a} vs {p.b}", str(p.tasks), str(p.a_wins), str(p.b_wins), str(p.ties)]
            for p in pairings(rates)
        ],
    )
    tasks = sorted({task for per_task in rates.values() for task in per_task})
    lines += ["", "Passed / counted runs per task:", ""]
    lines += _table(
        ["Task", *rates],
        [
            [task, *(f"{rates[c][task][0]}/{rates[c][task][1]}" if task in rates[c] else "–"
                     for c in rates)]
            for task in tasks
        ],
    )  # fmt: skip
    lines += [
        "",
        "A contender wins a task when its pass rate there is higher. With one run per task a win "
        "is one pass against one failure; read the counts, not the order.",
        "",
    ]
    return lines
