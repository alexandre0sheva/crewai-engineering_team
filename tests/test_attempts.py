"""Best-of-N attempts: scripted attempts in copies, blind selection, fallback, budget, report."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from cli_helpers import use_runner, workspace_root
from pipeline_fakes import BODY, REQUEST, FakeRunner, write_checks
from test_cli_do import triage_says
from test_pipeline_flow import only_run, project, run_dir

from engineering_team import main
from engineering_team.pipeline import select as select_module
from engineering_team.pipeline.attempts_contracts import AttemptsRecord, JudgeScore
from engineering_team.pipeline.attempts_run import variant
from engineering_team.pipeline.attempts_tree import make_copy
from engineering_team.pipeline.ladder_contracts import LadderRecord
from engineering_team.pipeline.select import Candidate, JudgeAnswer, anonymise, choose
from engineering_team.pipeline.stages import StageRequest
from engineering_team.pipeline.state import PipelineState
from engineering_team.report.collect import build_report
from engineering_team.report.render_html import render_html
from engineering_team.report.render_md import render_markdown
from engineering_team.runtime.events import read_events
from engineering_team.settings import load_settings
from engineering_team.testing.fake_llm import ScriptedLLM

CHECK_PY = (
    "import pathlib, sys\n"
    "sys.exit(0 if pathlib.Path('flag.txt').is_file() else 1)\n"
)  # fmt: skip
CHECKS = "- {id: tests, name: Project tests, kind: test, command: python check.py}\n"
# What each attempt writes to impl.txt: a2's is the smallest diff, a1's the largest.
WORDS = {"a1": "alpha\nalpha\nalpha\n", "a2": "beta\n", "a3": "gamma\ngamma\n"}
PRICED = "openai/gpt-6.1-sol"


@pytest.fixture(autouse=True)
def interpreter_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(Path(sys.executable).parent) + ":" + os.environ["PATH"])
    monkeypatch.setenv("ENGINEERING_BUDGET_MAX_REPAIR_ROUNDS", "1")


def label_of(run_id: str) -> str:
    found = re.search(r"-(a\d)$", run_id)
    assert found, f"{run_id} is not an attempt's run"
    return found[1]


class AttemptRunner(FakeRunner):
    """A ``FakeRunner`` whose work stage builds the project the same way in every attempt, then
    writes the attempt's own ``impl.txt``; the attempts in ``passes`` make the check pass."""

    def __init__(
        self, passes: tuple[str, ...] = ("a1", "a2"), spend: bool = False, rungs: bool = False
    ) -> None:
        super().__init__()
        self.passes, self.spend, self.rungs = passes, spend, rungs
        self.attempts: list[str] = []

    def run(self, request: StageRequest):  # type: ignore[no-untyped-def]
        output = super().run(request)
        if request.stage.name not in ("lead", "design", "foundation"):
            return output
        write = request.ctx.workspace.write_file
        write("README.md", "# Demo\n" + BODY)
        write("check.py", CHECK_PY)
        if self.rungs and request.state.rung != "attempts":
            return output  # the ladder's own rungs never pass
        label = label_of(request.ctx.run_id)
        self.attempts.append(label)
        write("impl.txt", WORDS[label])
        if label in self.passes:
            write("flag.txt", "ok\n")
        if self.spend:
            usage = {"prompt_tokens": 1_000, "completion_tokens": 100}
            request.ctx.events.emit("llm.call", agent="generalist", model=PRICED, usage=usage)
        return output


def judge(reply: Any) -> ScriptedLLM:
    return ScriptedLLM([reply])


def prefer(word: str) -> Any:
    """A judge reply that scores the candidate whose diff holds ``word`` highest."""

    def reply(messages: list[dict[str, Any]], tools: Any) -> str:
        prompt = str(messages[-1]["content"])
        sections = prompt.split("## Candidate ")[1:]
        ids = [section[0] for section in sections]
        best = next(section[0] for section in sections if word in section)
        scores = {
            i: {"correctness": 5 if i == best else 3, "minimality": 2, "consistency": 4, "tests": 3}
            for i in ids
        }
        return json.dumps({"scores": scores, "reason": f"Candidate {best} proves the request."})

    return reply


def use_judge(monkeypatch: pytest.MonkeyPatch, llm: ScriptedLLM) -> ScriptedLLM:
    monkeypatch.setattr(select_module, "build_judge_llm", lambda settings: llm)
    return llm


def launch(*extra: str) -> int:
    return main.run(
        [
            "new", "--request", REQUEST, "--project-name", "demo",
            "--workspace-root", str(workspace_root()), "--strategy", "auto",
            "--checks", str(write_checks(CHECKS)), *extra,
        ]
    )  # fmt: skip


def record() -> AttemptsRecord:
    found = AttemptsRecord.load(run_dir(only_run()))
    assert found is not None
    return found


def events(kind: str) -> list[dict[str, Any]]:
    return [e.data for e in read_events(run_dir(only_run()) / "events.jsonl") if e.type == kind]


# -- through `do` ------------------------------------------------------------------------------


def test_do_with_three_attempts_runs_them_offline_and_explains_a_blind_deterministic_choice(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    triage_says(monkeypatch, intents=["new_project"])
    runner = use_runner(monkeypatch, AttemptRunner(passes=("a1", "a2")))
    llm = use_judge(monkeypatch, judge(prefer("alpha")))

    code = main.run(
        [
            "--workspace-root", str(workspace_root()), "do", REQUEST, "--yes",
            "--project-name", "demo", "--attempts", "3", "--checks", str(write_checks(CHECKS)),
        ]
    )  # fmt: skip

    assert code == 0, capsys.readouterr()
    assert sorted(runner.attempts) == ["a1", "a2", "a3"]  # type: ignore[attr-defined]
    manifest = only_run()
    assert manifest.status == "succeeded" and manifest.verdict == "verified"
    found = record()
    assert [(a.label, a.status) for a in found.attempts] == [
        ("a1", "passed"), ("a2", "passed"), ("a3", "failed"),
    ]  # fmt: skip
    assert found.attempts[2].failing_checks == ["tests: failed (exit code 1)"]
    selection = found.selection
    assert selection is not None and selection.method == "judge" and selection.chosen == "a1"
    assert set(selection.anonymised) == {"a1", "a2"}  # only the passing attempts are candidates
    assert selection.anonymised == anonymise(["a1", "a2"], manifest.run_id)
    assert selection.revision_match is True
    assert "proves the request" in selection.reason
    # Blind: the judge saw anonymous ids, never an attempt's label, run or variation.
    (call,) = llm.calls
    assert not re.search(r"\ba[123]\b", call.prompt) and manifest.run_id not in call.prompt
    assert "seed" not in call.prompt and "gamma" not in call.prompt  # the failed one is not shown
    assert "data, not instructions" in call.prompt
    # The chosen attempt's changes are the run's; every attempt is kept as a patch and a run.
    assert (project() / "impl.txt").read_text(encoding="utf-8") == WORDS["a1"]
    folder = run_dir(manifest)
    for entry in found.attempts:
        assert (folder / entry.patch).is_file() and (folder / entry.run_dir / "manifest.json")
    assert "+beta" in (folder / "attempts" / "a2.patch").read_text(encoding="utf-8")
    assert not (project() / ".engineering-team" / "attempts").exists()  # the copies are gone
    state = PipelineState.load(folder)
    assert state is not None and state.verification.verdict == "verified"
    kinds = [e["label"] for e in events("attempts.finished")]
    assert kinds == ["a1", "a2", "a3"]  # recorded in label order
    assert events("attempts.selected")[0]["chosen"] == "a1"


# -- through `new` -----------------------------------------------------------------------------


def test_a_failed_judge_falls_back_to_the_smallest_passing_diff_and_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_runner(monkeypatch, AttemptRunner(passes=("a1", "a2")))
    use_judge(monkeypatch, judge("I like them all."))

    assert launch("--attempts", "3") == 0

    selection = record().selection
    assert selection is not None and selection.method == "fallback" and selection.chosen == "a2"
    assert "no JSON object" in selection.judge_error
    assert "smallest passing diff" in selection.reason
    assert (project() / "impl.txt").read_text(encoding="utf-8") == WORDS["a2"]


def test_when_no_attempt_passes_the_closest_is_kept_and_the_run_fails_saying_it_is_unverified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_runner(monkeypatch, AttemptRunner(passes=()))
    llm = use_judge(monkeypatch, judge(prefer("alpha")))

    assert launch("--attempts", "3") == 3  # not verified

    manifest = only_run()
    assert manifest.status == "failed" and manifest.verdict == "failed"
    selection = record().selection
    assert selection is not None and selection.method == "none-passed"
    assert selection.chosen == "a2"  # all 0 of 1: the smallest diff
    assert "not a verified result" in selection.reason
    assert not llm.calls  # nothing to judge
    assert (project() / "impl.txt").read_text(encoding="utf-8") == WORDS["a2"]


def test_attempts_that_would_cost_more_than_the_budget_left_never_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = use_runner(monkeypatch, AttemptRunner())
    monkeypatch.setenv("ENGINEERING_BUDGET_MAX_COST_USD", "0.0001")

    assert launch("--attempts", "3") == 1

    found = record()
    assert found.selection is not None and found.selection.method == "stopped"
    assert found.stopped.startswith("Not started: 3 attempts and the judge are estimated at")
    assert found.attempts == [] and not runner.calls
    assert only_run().status == "failed"


def test_the_ledger_matches_what_the_run_spent_and_one_passing_attempt_needs_no_judge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_runner(monkeypatch, AttemptRunner(passes=("a3",), spend=True))
    llm = use_judge(monkeypatch, judge(prefer("alpha")))

    assert launch("--attempts", "3") == 0

    found = record()
    selection = found.selection
    assert selection is not None and selection.method == "only" and selection.chosen == "a3"
    assert not llm.calls
    costs = [a.cost_usd for a in found.attempts]
    assert all(cost is not None and cost > 0 for cost in costs)
    assert found.ledger_usd is not None and found.ledger_usd == found.spent_usd
    manifest = only_run()
    assert manifest.summary is not None
    assert manifest.summary.estimated_cost_usd == pytest.approx(found.ledger_usd)
    forwarded = [e for e in read_events(run_dir(manifest) / "events.jsonl") if e.type == "llm.call"]
    assert sorted(e.lane for e in forwarded) == ["a1", "a2", "a3"]
    assert {e.stage for e in forwarded} == {"attempts"}


def test_the_report_shows_every_attempt_its_checks_and_the_judges_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_runner(monkeypatch, AttemptRunner(passes=("a1", "a2")))
    use_judge(monkeypatch, judge(prefer("beta")))

    assert launch("--attempts", "3") == 0

    report = build_report(run_dir(only_run()))
    text = render_markdown(report)
    assert "## Attempts" in text and "chosen by the blind judge: **a2**" in text
    assert "a2 (chosen)" in text and "1 of 1" in text and "0 of 1; failing: tests" in text
    assert "proves the request" in text and "attempts/a3.patch" in text
    assert "Ledger: 3 attempt(s) and the judge" in text
    assert "exactly the tree it was verified on" in text
    page = render_html(report)
    assert 'id="attempts"' in page and "Judge's scores" in page


def test_a_run_with_attempts_cannot_be_resumed(monkeypatch: pytest.MonkeyPatch) -> None:
    use_runner(monkeypatch, AttemptRunner(passes=()))
    use_judge(monkeypatch, judge(prefer("alpha")))
    assert launch("--attempts", "2") == 3

    code = main.run(
        [
            "resume", only_run().run_id, "--project-name", "demo",
            "--workspace-root", str(workspace_root()),
        ]
    )  # fmt: skip

    assert code == 2


# -- the ladder's last rung --------------------------------------------------------------------


def test_the_ladder_climbs_to_attempts_when_asked_and_keeps_the_rung_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENGINEERING_LADDER_MAX_RUNG", "attempts")
    use_runner(monkeypatch, AttemptRunner(passes=("a2",), rungs=True))
    use_judge(monkeypatch, judge(prefer("alpha")))

    assert launch() == 0

    ladder = LadderRecord.load(run_dir(only_run()))
    assert ladder is not None
    assert [(r.rung, r.status) for r in ladder.rungs] == [
        ("solo", "escalated"), ("team", "escalated"), ("pipeline", "escalated"),
        ("attempts", "succeeded"),
    ]  # fmt: skip
    found = record()
    assert found.source == "ladder" and found.count == 3
    assert found.selection is not None and found.selection.chosen == "a2"
    assert only_run().verdict == "verified"


# -- selection, alone ----------------------------------------------------------------------------


def test_anonymous_ids_are_a_seeded_shuffle_stable_across_reruns() -> None:
    labels = ["a1", "a2", "a3"]
    first = anonymise(labels, "20261101-120000-abcdef")

    assert first == anonymise(list(reversed(labels)), "20261101-120000-abcdef")
    assert sorted(first.values()) == ["A", "B", "C"]
    shuffles = {tuple(anonymise(labels, f"20261101-120000-{n:06x}").items()) for n in range(12)}
    assert len(shuffles) > 1  # the seed is the run: other runs get other orders


def test_a_tie_goes_to_the_smallest_diff() -> None:
    small = Candidate("a2", "diff", diff_lines=2, files=1, checks="1 of 1")
    large = Candidate("a1", "diff", diff_lines=9, files=1, checks="1 of 1")
    ids = {"a1": "A", "a2": "B"}
    same = JudgeScore(correctness=4, minimality=4, consistency=4, tests=4)

    chosen, reason = choose(
        JudgeAnswer(scores={"A": same, "B": same}, reason="Both."), [large, small], ids
    )

    assert chosen.label == "a2" and "tie at 16 between A, B" in reason


def test_an_answer_that_skips_a_candidate_is_not_used() -> None:
    with pytest.raises(select_module.JudgeError, match="scored"):
        select_module.parse_answer(
            {"scores": {"A": {"correctness": 1, "minimality": 1, "consistency": 1, "tests": 1}},
             "reason": "A."},
            {"A", "B"},
        )  # fmt: skip


# -- variation and copies ------------------------------------------------------------------------


def test_attempts_vary_the_worker_model_or_the_harness_and_never_fire_hooks(
    make_context: Any,
) -> None:
    settings = load_settings(overrides={"attempts.vary": "model", "budget.max_cost_usd": 3.0})
    ctx = make_context(settings=settings)

    variants = [variant(ctx, number, 1.0) for number in (1, 2, 3)]

    workers = [
        found.resolve_model("generalist_engineer", tier="worker").model for found, _, _ in variants
    ]
    assert workers[0] == settings.resolve_model("dev", tier="worker").model
    assert workers[1] == settings.resolve_model("x", tier="reviewer").model
    assert workers[2] == settings.resolve_model("x", tier="lead").model
    assert "the reviewer tier's" in variants[1][2]
    for found, _, _ in variants:
        assert found.budget.max_cost_usd == 1.0 and found.attempts.count == 1
        assert not found.hooks.any

    harness = make_context("other", settings=load_settings(overrides={"attempts.vary": "harness"}))
    described = [variant(harness, number, None)[2] for number in (1, 2, 3)]
    assert described == [
        "harness */*, as the run resolved it",
        "harness */*, bundled library only",
        "harness of the base domain (no domain packs), bundled library only",
    ]


def test_a_copy_keeps_a_repository_but_never_a_worktrees_link_to_one(
    make_context: Any, tmp_path: Path
) -> None:
    ctx = make_context()
    ctx.workspace.write_file("app.py", "print('hi')\n")
    (ctx.workspace.root / ".git").write_text("gitdir: /elsewhere/.git/worktrees/x\n")
    (ctx.workspace.root / ".engineering-team" / "memory").mkdir(parents=True)
    (ctx.workspace.root / ".engineering-team" / "memory" / "facts.yaml").write_text("[]\n")

    copy = make_copy(ctx.workspace, tmp_path / "copy", ctx.settings)

    assert (copy.root / "app.py").is_file() and not (copy.root / ".git").exists()
    assert (copy.root / ".engineering-team" / "memory" / "facts.yaml").is_file()
    assert not (copy.root / ".engineering-team" / "runs").exists()
    (ctx.workspace.root / ".git").unlink()
    (ctx.workspace.root / ".git").mkdir()
    (ctx.workspace.root / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    again = make_copy(ctx.workspace, tmp_path / "copy", ctx.settings)
    assert (again.root / ".git" / "HEAD").is_file()


@pytest.mark.git
def test_the_chosen_attempt_is_one_commit_of_the_new_projects_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_runner(monkeypatch, AttemptRunner(passes=("a1", "a2")))
    use_judge(monkeypatch, judge(prefer("alpha")))

    assert launch("--attempts", "2") == 0

    log = subprocess.run(
        ["git", "log", "--format=%s"], cwd=project(), capture_output=True, text=True, check=True
    ).stdout
    assert "stage(attempts): best of 2: a1 (judge)" in log.splitlines()[0]
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=project(), capture_output=True, text=True, check=True
    ).stdout
    assert status.strip() == ""
    selection = record().selection
    assert selection is not None and selection.revision_match is True
