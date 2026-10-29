"""``bench triage``: the labelled cases, their signal expectations, scoring, the command."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import get_args

import pytest
from bench_helpers import SUITE
from typer.testing import CliRunner

from engineering_team.bench.tasks import BenchError
from engineering_team.bench.triage_eval import (
    CASES_FILE,
    CaseOutcome,
    load_cases,
    render_markdown,
    score,
)
from engineering_team.cli.app import app
from engineering_team.contracts import Intent, TaskScale
from engineering_team.intake import triage as triage_module
from engineering_team.testing.fake_llm import ScriptedLLM

runner = CliRunner()
SHIPPED = SUITE / CASES_FILE
ANSWER = {
    "intents": ["feature"], "size": "small", "risk": "low", "verify_by": ["project_tests"],
    "focus_paths": [], "needs_clarification": False, "questions": [], "assumptions": [],
    "confidence": "high", "rationale": "Adds a flag.",
}  # fmt: skip


def suite(tmp_path: Path, cases: str) -> Path:
    root = tmp_path / "suite"
    fixture = root / "tasks" / "demo" / "fixture"
    fixture.mkdir(parents=True)
    (fixture / "app.py").write_text("print('hi')\n")
    (root / "triage").mkdir()
    (root / "triage" / "cases.yaml").write_text(cases)
    (root / "budget.toml").write_text("total_usd = 1.0\n[allocations]\nT10 = 0.40\n")
    return root


CASES = """\
cases:
  - id: add-flag
    repo: demo
    git: false
    request: Add a --quiet flag to app.py.
    expect: {intents: [feature], size: small, target: existing}
    signals: {keywords: [feature], paths: [app.py]}
  - id: new-tool
    request: Build me a small CLI app for notes.
    expect: {intents: [new_project], size: small, target: new}
    signals: {keywords: [new_project]}
"""


# -- the shipped cases -----------------------------------------------------------------------


@pytest.mark.skipif(not SHIPPED.is_file(), reason="no benchmarks/ directory")
def test_the_shipped_cases_cover_every_intent_size_and_target() -> None:
    cases = load_cases(SHIPPED, SUITE / "tasks")

    assert len(cases) >= 60
    assert {c.expect.intents[0] for c in cases} == set(get_args(Intent))
    assert {c.expect.size for c in cases} == set(get_args(TaskScale))
    assert Counter(c.repo is None for c in cases)[True] >= 5
    assert Counter(c.expect.target for c in cases)["existing"] >= 30
    assert any(c.mode for c in cases) and any(not c.git for c in cases if c.repo)


@pytest.mark.skipif(not SHIPPED.is_file(), reason="no benchmarks/ directory")
def test_every_shipped_case_matches_its_signal_expectations() -> None:
    result = runner.invoke(app, ["--json", "bench", "triage", "--suite", str(SUITE)])

    report = json.loads(result.stdout)
    assert report["signal_problems"] == []
    assert result.exit_code == 0
    assert report["signals_only"]["scores"]["cases"] >= 60
    assert "live" not in report


# -- loading and scoring ---------------------------------------------------------------------


def test_bad_cases_are_reported_one_line_per_problem(tmp_path: Path) -> None:
    root = suite(
        tmp_path,
        """\
cases:
  - id: a
    repo: nowhere
    mode: cook
    request: x
    expect: {intents: [feature], size: small, target: existing}
    signals: {keywords: [cooking]}
  - id: a
    request: y
    expect: {intents: [coffee], size: small, target: new}
    signals: {}
  - id: b
    request: z
    expect: {intents: [feature], size: small, target: new}
    signals: {surprise: true}
""",
    )

    with pytest.raises(BenchError) as error:
        load_cases(root / CASES_FILE, root / "tasks")

    lines = str(error.value).splitlines()[1:]
    assert any("a: repo 'nowhere'" in line for line in lines)
    assert any("a: mode 'cook'" in line for line in lines)
    assert any("a: unknown keyword groups cooking" in line for line in lines)
    assert any("a: expect.intents.0" in line for line in lines)
    assert any("b: signals.surprise" in line for line in lines)


def test_scores_and_the_confusion_matrix() -> None:
    outcomes = [
        CaseOutcome(id="1", expected=["bugfix"], predicted=["bugfix", "tests"],
                    size=("small", "small"), target=("existing", "existing"), asked=False),
        CaseOutcome(id="2", expected=["bugfix", "tests"], predicted=["feature"],
                    size=("small", "medium"), target=("existing", "existing"), asked=True,
                    expect_clarify=True, source="signals"),
    ]  # fmt: skip

    scores = score(outcomes)

    assert scores["primary_accuracy"] == 0.5
    assert scores["size_accuracy"] == 0.5
    assert scores["target_accuracy"] == 1.0
    assert scores["secondary_recall"] == 0.0
    assert scores["clarify_agreement"] == 1.0
    assert scores["fallbacks"] == 1
    assert scores["confusion"] == {"bugfix": {"bugfix": 1, "feature": 1}}
    report = render_markdown("Triage", scores, outcomes)
    assert "| bugfix | 1 | 1 |" in report
    assert "| 2 | bugfix, tests | feature | small → medium |" in report


# -- the command -----------------------------------------------------------------------------


def test_a_signal_mismatch_fails_the_command(tmp_path: Path) -> None:
    root = suite(tmp_path, CASES.replace("paths: [app.py]", "paths: [main.py]"))

    result = runner.invoke(app, ["bench", "triage", "--suite", str(root)])

    assert result.exit_code == 3
    assert "add-flag: paths is ['app.py'], expected ['main.py']" in result.stdout


def test_an_estimate_prices_the_set_and_calls_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = suite(tmp_path, CASES)
    monkeypatch.setattr(triage_module, "build_triage_llm", lambda settings: pytest.fail("call"))

    result = runner.invoke(
        app,
        ["--json", "bench", "triage", "--estimate", "--provider", "openai", "--suite", str(root)],
    )

    assert result.exit_code == 0, result.output
    live = json.loads(result.stdout)["live"]
    assert live["model"] == "openai/gpt-6-luna"
    assert live["estimate_usd"] > 0
    assert not (root / "spend.jsonl").exists()


def test_a_live_run_must_name_its_allocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = suite(tmp_path, CASES)
    monkeypatch.setattr(triage_module, "build_triage_llm", lambda settings: pytest.fail("call"))

    result = runner.invoke(
        app, ["bench", "triage", "--live", "--provider", "ollama", "--suite", str(root)]
    )

    assert result.exit_code == 2
    assert "--allocation" in result.output


def test_a_live_run_scores_the_model_and_records_its_spend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = suite(tmp_path, CASES)
    llm = ScriptedLLM([json.dumps(ANSWER), "not an answer"], model="ollama/qwen3.8:27b")
    monkeypatch.setattr(triage_module, "build_triage_llm", lambda settings: llm)
    out = tmp_path / "out"

    result = runner.invoke(
        app,
        ["--json", "bench", "triage", "--live", "--provider", "ollama", "--allocation", "T10",
         "--out", str(out), "--suite", str(root)],
    )  # fmt: skip

    assert result.exit_code == 0, result.output
    llm.assert_exhausted()
    live = json.loads(result.stdout)["live"]
    assert live["scores"]["primary_accuracy"] == 1.0  # the second case fell back to the signals
    assert live["scores"]["fallbacks"] == 1
    assert live["actual_usd"] == 0.0
    ledger = [json.loads(line) for line in (root / "spend.jsonl").read_text().splitlines()]
    assert [(e["task"], e["actual_usd"]) for e in ledger] == [("T10", 0.0)]
    assert "bench triage: 2 call(s)" in ledger[0]["note"]
    assert json.loads((out / "results.json").read_text())["live"]["model"] == "ollama/qwen3.8:27b"
    assert "## Confusion matrix" in (out / "report.md").read_text()
