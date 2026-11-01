"""Harness tuning end to end (T21): rollouts are real team processes (the offline fake team),
judged by the tasks' hidden checks, with each candidate installed as the team's user harness."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from bench_helpers import SUITE
from test_harness_tuning import CURE, SKILL, Router, decide, edits
from typer.testing import CliRunner

from engineering_team.bench.execution import execute_run
from engineering_team.bench.plan import RunOptions, RunSpec
from engineering_team.bench.tasks import BenchTask, load_suite
from engineering_team.cli.app import app
from engineering_team.harness.domains import Domain
from engineering_team.harness.library import resolve
from engineering_team.learning.ledger import TuningLedger, identity_problems
from engineering_team.learning.tuning.loop import TuneConfig, Tuner
from engineering_team.learning.tuning.output import write_tuning
from engineering_team.learning.tuning.rollouts import (
    STRATEGY,
    BenchRollouts,
    Rollout,
    protected_texts,
)
from engineering_team.learning.tuning.tree import Candidate, Checker
from engineering_team.settings import load_settings

pytestmark = pytest.mark.skipif(not (SUITE / "tasks").is_dir(), reason="no benchmarks/ directory")

DOMAIN = Domain("bugfix", "python")


class CuredByTheHarness(BenchRollouts):
    """The fake team as a team the harness steers: on a dev task it replays the reference only
    when its harness holds the cure, else it leaves the bug (``fixture``); held-out tasks it
    solves either way."""

    def __call__(self, task: BenchTask, candidate: Candidate, name: str, repeat: int) -> Rollout:
        cured = task.split == "heldout" or any(CURE in t for t in candidate.edits.values())
        folder = name.replace(":", "-")
        options = replace(
            self.options_for(candidate, folder), fake_solution="reference" if cured else "fixture"
        )
        record = execute_run(RunSpec(task, STRATEGY, repeat), options, self.registry, self.cancel)
        return self.rollout(name, record, options.batch_dir, candidate)


def test_the_offline_loop_adopts_a_skill_the_team_needed_and_writes_a_diff_and_a_ledger(
    tmp_path: Path,
) -> None:
    tasks = {t.id: t for t in load_suite(SUITE)}
    dev, heldout = tasks["seeded-bug"], tasks["ledger-misleading-trace"]
    protected = protected_texts([dev, heldout])
    runs = tmp_path / "runs"
    options = RunOptions(batch="tune", batch_dir=runs, fake=True, acceptance_sandbox="local")
    rollouts = CuredByTheHarness(options, runs, "bugfix+python", protected)
    router = Router(
        inv=[edits()],
        dom=[edits(("skills/repro-loop/SKILL.md", SKILL))],
        compose=[decide("dom", "dom 1/1 vs base 0/1: the base never ran the reproduction")],
    )
    settings = load_settings()
    checker = Checker(DOMAIN, resolve(DOMAIN), settings, protected)
    ledger = TuningLedger(tmp_path / "tuning" / "ledger.jsonl")
    config = TuneConfig([dev], [heldout], parallel=3)

    result = Tuner(config, rollouts, router.llm(), checker, ledger, protected, settings).run()

    assert result.decision == "adopt", result.notes
    entries = {e.label: e for e in ledger.entries()}
    assert identity_problems(entries.values(), ["seeded-bug"], 1, "parallel") == []
    assert entries["rollout:base:seeded-bug:1"].outcome == "failed"
    assert entries["rollout:dom:seeded-bug:1"].outcome == "passed"
    for name in ("rollout:dom:seeded-bug:1", "rollout:composed:seeded-bug:1",
                 "eval:final:ledger-misleading-trace:1"):  # fmt: skip
        assert entries[name].detail == "candidate applied", name  # the team process resolved it
        assert (runs / entries[name].run_dir / "result.json").is_file()
    assert "hidden checks say failed" in router.seen["dom"][0][1]["content"]

    out = tmp_path / "tuning"
    data = write_tuning(out, result, config, checker.base, ledger)
    assert data["decision"] == "adopt" and data["files"] == ["skills/repro-loop/SKILL.md"]
    assert "+++ b/bugfix+python/skills/repro-loop/SKILL.md" in (out / "diff.patch").read_text()
    assert len((out / "ledger.jsonl").read_text().splitlines()) == 10


def test_harness_tune_fake_from_the_command_line_keeps_a_base_that_already_passes(
    tmp_path: Path,
) -> None:
    out = tmp_path / "tuning"
    result = CliRunner().invoke(
        app,
        ["--json", "harness", "tune", "--domain", "bugfix/python", "--fake", "--suite",
         str(SUITE), "--out", str(out), "--runs", str(tmp_path / "runs"),
         "--acceptance-sandbox", "local", "--parallel", "2"],
    )  # fmt: skip

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["decision"] == "keep_base" and data["fake"] is True
    assert data["notes"][-1] == "KEEP_BASE: no slot's choice beat the base on its own probe"
    assert data["dev_tasks"] == ["seeded-bug"] and data["identity_problems"] == []
    kinds = [json.loads(line)["kind"] for line in (out / "ledger.jsonl").read_text().splitlines()]
    assert kinds.count("rollout") == 4 and kinds.count("session") == 3
    assert not (out / "diff.patch").exists()
