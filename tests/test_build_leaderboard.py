"""``scripts/build_leaderboard.py``: rows from committed batches (a ``results.json`` or, for 0.2.0,
only the run records), their order, intervals, small-sample marking, links and the README rows."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest
from bench_helpers import record

from engineering_team.bench.export import results_document
from engineering_team.bench.results import RunRecord, collect_results, write_result

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "build_leaderboard.py"


@pytest.fixture(scope="module")
def board() -> ModuleType:
    spec = importlib.util.spec_from_file_location("build_leaderboard", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_leaderboard"] = module
    spec.loader.exec_module(module)
    return module


def make_batch(
    results: Path,
    path: str,
    records: list[RunRecord],
    *,
    with_results_json: bool,
    fake: bool = False,
    versions: dict[str, str] | None = None,
) -> Path:
    batch_dir = results / path
    batch_dir.mkdir(parents=True)
    manifest = {
        "batch": batch_dir.name, "created": "2026-10-04T10:00:00+00:00", "fake": fake,
        "versions": versions or {"engineering_team": "0.2.0", "crewai": "1.15.23"},
    }  # fmt: skip
    (batch_dir / "batch.json").write_text(json.dumps(manifest), encoding="utf-8")
    for r in records:
        run_dir = batch_dir / r.task / f"{r.strategy}-{r.repeat}"
        run_dir.mkdir(parents=True, exist_ok=True)
        write_result(run_dir, r)
    if with_results_json:
        document = results_document(manifest, collect_results(batch_dir))
        (batch_dir / "results.json").write_text(json.dumps(document), encoding="utf-8")
    return batch_dir


def runs(strategy: str, passed: int, total: int, *, cost: float = 0.1, **fields: object):
    return [
        record(
            task=f"task-{n % 5}", strategy=strategy, repeat=n // 5 + 1, cost_usd=cost,
            outcome="passed" if n < passed else "failed", models=["openai/m"], provider="openai",
            **fields,
        )
        for n in range(total)
    ]  # fmt: skip


@pytest.fixture
def results(tmp_path: Path) -> Path:
    root = tmp_path / "results"
    # 0.2.0 style: run records only. 10 runs, 7 passed.
    make_batch(root, "2026-10-04/old", runs("single", 7, 10), with_results_json=False)
    # a newer batch with a results.json: 10 runs, 9 passed, dearer per success.
    make_batch(
        root, "2026-11-20/new", runs("pipeline", 9, 10, cost=0.3), with_results_json=True,
        versions={"engineering_team": "0.3.0", "crewai": "1.15.31"},
    )  # fmt: skip
    # a screen: 3 of 3 passes must not outrank 9 of 10.
    make_batch(root, "2026-11-01/model-screen/probe", runs("single", 3, 3), with_results_json=True)
    # offline batches measure the harness, not a model.
    make_batch(root, "2026-11-02/fake", runs("single", 10, 10), with_results_json=False, fake=True)
    return root


def test_rows_come_from_results_json_or_from_the_run_records(board: ModuleType, results: Path):
    rows = {row.batch: row for row in board.collect(results)}

    assert set(rows) == {
        "2026-10-04/old", "2026-11-20/new", "2026-11-01/model-screen/probe",
    }  # fmt: skip
    old, new = rows["2026-10-04/old"], rows["2026-11-20/new"]
    assert (old.passed, old.runs, old.version, old.date) == (7, 10, "0.2.0", "2026-10-04")
    assert (new.passed, new.runs, new.version, new.date) == (9, 10, "0.3.0", "2026-11-20")
    assert new.tasks == 5
    assert 0 < old.low < old.rate < old.high < 1  # a Wilson interval, not 70 % ± 0
    assert old.cost_per_success == pytest.approx(10 * 0.1 / 7)


def test_enough_runs_first_then_the_interval_floor_then_cost(board: ModuleType, results: Path):
    rows = board.collect(results)

    assert [row.batch for row in rows] == [
        "2026-11-20/new",  # 9/10
        "2026-10-04/old",  # 7/10
        "2026-11-01/model-screen/probe",  # 3/3 is below both: it has n < 10
    ]
    assert [row.small for row in rows] == [False, False, True]


def test_the_order_does_not_depend_on_the_disk(board: ModuleType, results: Path):
    rows = board.collect(results)
    assert board.ordered(reversed(rows)) == rows


def test_a_small_sample_is_greyed_and_marked(board: ModuleType, results: Path):
    page = board.page(board.collect(results))

    greyed = [line for line in page.splitlines() if 'class="lb-small"' in line]
    assert len(greyed) == 1
    assert "n &lt; 10" in greyed[0]
    assert "model-screen/probe" in greyed[0]
    assert page.count('class="lb-row"') == 2


def test_every_row_links_to_its_batch_on_github(board: ModuleType, results: Path):
    page = board.page(board.collect(results))

    base = "https://github.com/alexandre0sheva/crewai-engineering_team/tree/main"
    assert f'href="{base}/benchmarks/results/2026-10-04/old"' in page
    assert f'href="{base}/benchmarks/results/2026-11-01/model-screen/probe"' in page


def test_a_plan_run_shows_plan_never_zero(board: ModuleType, tmp_path: Path):
    root = tmp_path / "results"
    own = [
        record(
            task=f"t{n}", strategy="claude-code", agent="claude-code", agent_version="2.1.287",
            cost_usd=None, billing="subscription", equivalent_cost_usd=0.5,
        )
        for n in range(10)
    ]  # fmt: skip
    make_batch(root, "2026-11-10/agents", own, with_results_json=True)

    (row,) = board.collect(root)

    assert row.kind == "agent"
    assert "claude-code 2.1.287" in row.version
    assert board.cost_text(row) == "plan (≈$0.50/run)"
    assert "$0" not in board.cost_text(row).replace("$0.50", "")


def test_the_readme_summary_is_three_rows_between_markers(board: ModuleType, results: Path):
    rows = board.collect(results)
    base = "# Title\n\nbefore\n\n"
    text = f"{base}{board.README_START}\nstale\n{board.README_END}\n\nafter\n"

    out = board.replace_block(text, board.readme_block(rows, "https://example.org/site/"))

    assert out.startswith(base) and out.endswith("\n\nafter\n")
    block = out[out.index(board.README_START) : out.index(board.README_END)]
    assert block.count("\n| `") == min(3, len(rows))
    assert "(n < 10)" in block  # the third row is a small sample and says so
    assert "https://example.org/site/leaderboard/" in block
    assert "stale" not in block


def test_main_writes_then_checks(
    board: ModuleType, results: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    docs_root = tmp_path / "root"
    (docs_root / "docs").mkdir(parents=True)
    readme = docs_root / "README.md"
    readme.write_text(f"intro\n{board.README_START}\n{board.README_END}\n", encoding="utf-8")
    monkeypatch.setattr(board, "ROOT", docs_root)
    argv = ["--results", str(results), "--readme", str(readme)]

    assert board.main(["--check", *argv]) == 1  # nothing generated yet
    assert board.main(argv) == 0
    assert board.main(["--check", *argv]) == 0

    data = json.loads((docs_root / "docs" / "assets" / "leaderboard.json").read_text("utf-8"))
    assert data["min_runs"] == 10
    assert [row["batch"] for row in data["rows"]][0] == "2026-11-20/new"
    assert "# Leaderboard" in (docs_root / "docs" / "leaderboard.md").read_text("utf-8")


def test_the_committed_results_render(board: ModuleType):
    """The 0.2.0 batches in the repository appear, labelled with their version."""

    rows = board.collect(board.ROOT / "benchmarks" / "results")

    old = [row for row in rows if row.batch == "2026-10-04/screen-default"]
    assert {row.contender for row in old} == {"single", "pipeline"}
    assert all(row.version == "0.2.0" and not row.small for row in old)
