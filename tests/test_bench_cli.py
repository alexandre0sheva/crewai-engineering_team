"""The ``bench`` command: listing, dry runs, usage errors, and reporting on a batch."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from bench_helpers import SUITE, failing, record
from typer.testing import CliRunner

from engineering_team.bench.batch import write_batch_manifest
from engineering_team.bench.execution import RunOptions
from engineering_team.bench.results import write_result
from engineering_team.cli.app import COMMANDS, app

pytestmark = pytest.mark.skipif(not (SUITE / "tasks").is_dir(), reason="no benchmarks/ directory")
runner = CliRunner()


def bench(*args: str):  # noqa: ANN201
    return runner.invoke(app, ["bench", *args, "--suite", str(SUITE)])


def test_bench_is_a_known_command() -> None:
    assert "bench" in COMMANDS


def test_list_shows_every_task() -> None:
    result = runner.invoke(app, ["--json", "bench", "list", "--suite", str(SUITE)])

    assert result.exit_code == 0
    tasks = json.loads(result.stdout)
    assert len(tasks) == 10
    assert {t["kind"] for t in tasks} == {"greenfield", "brownfield"}
    assert all(t["criteria"] for t in tasks)


def test_a_dry_run_prices_the_batch_and_runs_nothing(tmp_path: Path) -> None:
    out = tmp_path / "out"
    result = runner.invoke(
        app,
        ["--json", "bench", "run", "--dry-run", "--suite", str(SUITE), "--out", str(out),
         "--tasks", "notes-cli,seeded-bug", "--strategy", "pipeline,single", "--repeat", "3",
         "--provider", "openai"],
    )  # fmt: skip

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["runs"] == 9  # notes-cli x 2 strategies x 3, seeded-bug x pipeline x 3
    assert data["estimated_usd"] > 0 and data["range_usd"][0] < data["estimated_usd"]
    assert {row["strategy"] for row in data["rows"]} == {"pipeline", "single"}
    assert not out.exists()  # nothing was created


def test_a_human_dry_run_says_the_numbers_are_priors() -> None:
    result = bench("run", "--dry-run", "--tasks", "notes-cli", "--provider", "ollama")

    assert result.exit_code == 0
    assert "priors" in result.output and "nothing was run" in result.output


def test_a_live_run_without_a_budget_is_refused() -> None:
    result = bench("run", "--tasks", "notes-cli")

    assert result.exit_code == 2
    assert "--budget-usd" in result.output


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--strategy", "magic"], "Unknown strategy"),
        (["--tasks", "nope"], "Unknown task"),
        (["--tasks", "seeded-bug", "--strategy", "single"], "Nothing to run"),
        (["--resume", "--fake"], "--resume needs --batch"),
        (["--subset", "weird"], "weird"),
    ],
)
def test_usage_errors_exit_2_with_a_message(args: list[str], message: str) -> None:
    result = bench("run", "--fake", *args) if "--fake" not in args else bench("run", *args)

    assert result.exit_code == 2
    assert message in result.output


def test_an_existing_batch_is_not_overwritten(tmp_path: Path) -> None:
    (tmp_path / "taken").mkdir()

    result = bench(
        "run", "--fake", "--tasks", "notes-cli", "--batch", "taken", "--out", str(tmp_path)
    )

    assert result.exit_code == 2 and "--resume" in result.output


def test_a_live_run_with_an_unpriced_model_is_refused_before_anything_starts(
    tmp_path: Path,
) -> None:
    config = tmp_path / "bench.toml"
    config.write_text('[models.tiers.worker]\nmodel = "openai/not-priced-yet"\n')

    result = bench(
        "run", "--tasks", "notes-cli", "--budget-usd", "1", "--config", str(config),
        "--out", str(tmp_path / "out"),
    )  # fmt: skip

    assert result.exit_code == 2
    assert "No price for openai/not-priced-yet" in result.output
    assert "--allow-unpriced" in result.output
    assert not (tmp_path / "out").exists()


def test_a_missing_config_file_is_a_usage_error(tmp_path: Path) -> None:
    result = bench("run", "--fake", "--config", str(tmp_path / "nope.toml"))

    assert result.exit_code == 2 and "Config file not found" in result.output


def test_the_shipped_models_all_have_prices() -> None:
    from engineering_team.bench.estimate import unpriced_models
    from engineering_team.settings import load_settings

    for provider in ("openai", "anthropic", "google", "ollama"):
        assert unpriced_models(load_settings(overrides={"provider": provider})) == []


def make_batch(directory: Path, name: str = "demo") -> Path:
    batch_dir = directory / name
    options = RunOptions(batch=name, batch_dir=batch_dir, provider="openai")
    batch_dir.mkdir(parents=True)
    write_batch_manifest(
        options, [], ["pipeline"], 2, planned=2, not_applicable=[], budget_usd=5.0, parallel=1
    )
    write_result(batch_dir / "notes-cli" / "pipeline-1", record(batch=name))
    write_result(
        batch_dir / "notes-cli" / "pipeline-2",
        record(
            batch=name, repeat=2, outcome="failed", criteria=failing("ids-not-reused"), reason="x"
        ),
    )
    return batch_dir


def test_report_summarises_a_batch_and_writes_the_files(tmp_path: Path) -> None:
    batch_dir = make_batch(tmp_path)

    result = runner.invoke(app, ["bench", "report", str(batch_dir)])

    assert result.exit_code == 0
    assert "| pipeline | 1/2 | 50% (9%–91%) |" in result.stdout
    assert "ids-not-reused" in result.stdout
    assert (batch_dir / "report.md").read_text() == result.stdout
    assert "notes-cli,pipeline,1" in (batch_dir / "results.csv").read_text()


def test_report_without_a_name_uses_the_latest_batch(tmp_path: Path) -> None:
    make_batch(tmp_path, "older")
    make_batch(tmp_path, "newer")

    result = runner.invoke(app, ["--json", "bench", "report", "--out", str(tmp_path)])

    assert json.loads(result.stdout)["batch"] == "newer"


def test_report_json_carries_the_intervals(tmp_path: Path) -> None:
    make_batch(tmp_path)

    result = runner.invoke(app, ["--json", "bench", "report", "demo", "--out", str(tmp_path)])

    group = json.loads(result.stdout)["groups"][0]
    assert (group["passed"], group["counted"]) == (1, 2)
    assert 0 < group["wilson_low"] < 0.5 < group["wilson_high"] < 1


def test_report_with_no_batches_says_what_to_do(tmp_path: Path) -> None:
    result = runner.invoke(app, ["bench", "report", "--out", str(tmp_path)])

    assert result.exit_code == 2 and "bench run" in result.output
