"""``continue``: follow-up runs in a thread, on the parent's branch, verified again."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from cli_helpers import use_runner, workspace_root
from pipeline_fakes import FakeRunner
from repo_fixtures import make_repo, snapshot
from test_baseline import interpreter_on_path  # noqa: F401  (the checks must find pytest)
from test_cli_do import BUG, FEATURE, do, no_triage_model, triage_says
from test_feature_mode import LEGACY, feature_runner

from engineering_team import main
from engineering_team.atomic_io import atomic_write_json
from engineering_team.contracts import RunManifest, TaskProfile
from engineering_team.intake.signals import Signals
from engineering_team.intake.triage import build_messages
from engineering_team.intake.triage_rules import heuristic
from engineering_team.modes.answer_contracts import PLAN_FILE
from engineering_team.modes.isolation import IsolationError, isolate, read_isolation
from engineering_team.modes.threads import (
    ThreadError,
    continue_isolation,
    implementation_mode,
    load_parent,
    thread_context,
)
from engineering_team.pipeline.state import PipelineState
from engineering_team.report.collect import build_report
from engineering_team.report.render_html import render_html
from engineering_team.report.render_md import render_markdown
from engineering_team.runtime.run_index import children_of, find_runs, thread_of

pytestmark = [pytest.mark.git, pytest.mark.usefixtures("full_pipeline")]
FOLLOW_UP = "Also handle an empty search string."
RUN = "20261003-101500-abc123"


def git(root: Path, *args: str) -> str:
    identity = ["-c", "user.name=Test", "-c", "user.email=test@example.com"]
    done = subprocess.run(
        ["git", *identity, "-c", "commit.gpgsign=false", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


@pytest.fixture
def legacy(tmp_path: Path) -> Path:
    return make_repo(tmp_path / "legacy", LEGACY)


def go(*args: str) -> int:
    return main.run(["--workspace-root", str(workspace_root()), *args])


def runs() -> list[Any]:
    return find_runs(workspace_root())


def first_run(repo: Path, monkeypatch: pytest.MonkeyPatch, **options: Any) -> Any:
    triage_says(monkeypatch, intents=["feature"])
    use_runner(monkeypatch, feature_runner(**options))
    assert do(FEATURE, "--repo", str(repo), "--yes") == 0
    (ref,) = runs()
    assert ref.manifest.verdict == "verified"
    return ref


def follow_up(
    ref: Any, monkeypatch: pytest.MonkeyPatch, *args: str, fake: FakeRunner | None = None
) -> tuple[int, FakeRunner]:
    scripted = use_runner(monkeypatch, fake or feature_runner())
    return go("continue", ref.run_id, FOLLOW_UP, "--yes", *args), scripted


def state_of(ref: Any) -> PipelineState:
    state = PipelineState.load(ref.run_dir)
    assert state is not None
    return state


# -- the bookkeeping -----------------------------------------------------------------------------


def test_a_run_of_0_2_0_has_no_thread_fields_and_is_the_first_of_its_own_thread() -> None:
    old = json.loads(RunManifest(run_id=RUN, project_name="demo").model_dump_json())
    del old["thread_id"], old["parent_run"]

    manifest = RunManifest.model_validate(old)

    assert manifest.thread_id is None and manifest.parent_run is None


def test_a_follow_up_is_recorded_in_the_thread_of_its_parent(
    legacy: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    parent = first_run(legacy, monkeypatch)
    assert parent.thread_id == parent.run_id and parent.parent_run is None

    code, _ = follow_up(parent, monkeypatch)

    assert code == 0, capsys.readouterr().out
    everything = runs()
    assert len(everything) == 2
    child = everything[1]
    assert child.manifest.thread_id == parent.run_id and child.manifest.parent_run == parent.run_id
    assert [r.run_id for r in thread_of(everything, child)] == [parent.run_id, child.run_id]
    assert [r.run_id for r in children_of(everything, parent)] == [child.run_id]


def test_a_follow_up_of_a_follow_up_stays_in_the_first_runs_thread(
    legacy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = first_run(legacy, monkeypatch)
    assert follow_up(parent, monkeypatch)[0] == 0
    child = runs()[1]

    assert follow_up(child, monkeypatch)[0] == 0

    last = runs()[2]
    assert last.manifest.parent_run == child.run_id and last.manifest.thread_id == parent.run_id


def test_runs_lists_a_thread_and_names_the_parent(
    legacy: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    parent = first_run(legacy, monkeypatch)
    assert follow_up(parent, monkeypatch)[0] == 0
    capsys.readouterr()

    assert go("--json", "runs", "--thread", parent.run_id) == 0

    listed = json.loads(capsys.readouterr().out)
    assert [r["run_id"] for r in listed] == [r.run_id for r in runs()]
    assert [r["parent_run"] for r in listed] == [None, parent.run_id]
    assert {r["thread_id"] for r in listed} == {parent.run_id}


def test_runs_thread_leaves_out_other_threads(
    legacy: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    parent = first_run(legacy, monkeypatch)
    triage_says(monkeypatch, intents=["feature"])
    use_runner(monkeypatch, feature_runner())
    assert do("Add a delete function.", "--repo", str(legacy), "--yes") == 0
    capsys.readouterr()

    assert go("--json", "runs", "--thread", parent.run_id) == 0

    assert [r["run_id"] for r in json.loads(capsys.readouterr().out)] == [parent.run_id]


# -- the same branch -----------------------------------------------------------------------------


def test_a_two_step_thread_makes_two_verified_runs_on_one_branch(
    legacy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = first_run(legacy, monkeypatch)
    branch = read_isolation(legacy).branch  # type: ignore[union-attr]
    first_tip = git(legacy, "rev-parse", "HEAD")

    def second_step(request: Any) -> None:
        if request.package is not None:
            request.ctx.workspace.write_file("app/empty.py", "EMPTY = ''\n")

    code, fake = follow_up(parent, monkeypatch, fake=feature_runner(second_step))

    assert code == 0
    child = runs()[1]
    assert child.manifest.verdict == "verified" and parent.manifest.verdict == "verified"
    assert git(legacy, "branch", "--show-current") == branch  # no second branch was made
    assert (
        git(legacy, "branch", "--format=%(refname:short)", "--list", "engineering-team/*") == branch
    )
    assert git(legacy, "merge-base", "--is-ancestor", first_tip, "HEAD") == ""
    assert git(legacy, "show", "HEAD:app/empty.py") == "EMPTY = ''"
    assert child.workspace == parent.workspace
    assert any(r.stage.name == "implement" and r.package for r in fake.requests)


def test_the_follow_ups_patch_and_base_are_its_own_changes_not_the_whole_thread(
    legacy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = first_run(legacy, monkeypatch)
    first_tip = git(legacy, "rev-parse", "HEAD")

    def second_step(request: Any) -> None:
        if request.package is not None:
            request.ctx.workspace.write_file("app/empty.py", "EMPTY = ''\n")

    assert follow_up(parent, monkeypatch, fake=feature_runner(second_step))[0] == 0

    child = runs()[1]
    assert (state_of(child).isolation or {})["base_commit"] == first_tip
    patch = (child.run_dir / "changes.patch").read_text(encoding="utf-8")
    assert "app/empty.py" in patch and "src/wp-1.py" not in patch


def test_the_checkout_is_returned_to_the_threads_branch_when_it_is_clean(
    legacy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = first_run(legacy, monkeypatch)
    branch = git(legacy, "branch", "--show-current")
    git(legacy, "checkout", "-q", "main")

    code, _ = follow_up(parent, monkeypatch)

    assert code == 0
    assert git(legacy, "branch", "--show-current") == branch
    assert runs()[1].manifest.verdict == "verified"


def test_a_checkout_with_uncommitted_changes_on_another_branch_is_not_touched(
    legacy: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    parent = first_run(legacy, monkeypatch)
    git(legacy, "checkout", "-q", "main")
    (legacy / "app" / "store.py").write_text("def add(items, text):\n    return items  # wip\n")
    before = snapshot(legacy)
    fake = use_runner(monkeypatch, feature_runner())

    code = go("continue", parent.run_id, FOLLOW_UP, "--yes")

    assert code == 2 and not fake.calls
    assert "uncommitted changes" in capsys.readouterr().err
    assert snapshot(legacy) == before and len(runs()) == 1


def test_uncommitted_changes_on_the_threads_branch_need_allow_dirty(
    legacy: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    parent = first_run(legacy, monkeypatch)
    (legacy / "notes.txt").write_text("mine\n")

    refused, fake = follow_up(parent, monkeypatch)

    assert refused == 2 and not fake.calls
    assert "--allow-dirty" in capsys.readouterr().err
    assert follow_up(parent, monkeypatch, "--allow-dirty")[0] == 0


def test_a_worktree_thread_goes_on_in_the_same_worktree(
    legacy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    triage_says(monkeypatch, intents=["feature"])
    use_runner(monkeypatch, feature_runner())
    assert do(FEATURE, "--repo", str(legacy), "--worktree", "--yes") == 0
    (parent,) = runs()
    branch = (read_isolation(parent.workspace) or None).branch  # type: ignore[union-attr]
    assert parent.workspace != legacy.resolve()
    before = git(legacy, "branch", "--show-current")

    code, _ = follow_up(parent, monkeypatch)

    assert code == 0
    child = runs()[1]
    assert child.workspace == parent.workspace
    assert git(parent.workspace, "branch", "--show-current") == branch
    assert git(legacy, "branch", "--show-current") == before  # your checkout never moved


def test_a_workspace_that_is_gone_is_a_usage_error_with_the_way_out(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo", LEGACY)
    parent = isolate(repo, run_id=RUN, slug="x", mode="worktree")
    git(repo, "worktree", "remove", "--force", str(parent.workspace))

    with pytest.raises(IsolationError, match="is gone"):
        continue_isolation(parent)


# -- context, not evidence -----------------------------------------------------------------------


def test_the_follow_up_is_told_what_the_parent_was_asked_planned_and_concluded(
    legacy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = first_run(legacy, monkeypatch)

    code, fake = follow_up(parent, monkeypatch)

    assert code == 0
    told = (runs()[1].run_dir / "request.md").read_text(encoding="utf-8")
    assert told.startswith(FOLLOW_UP)
    assert "Earlier work in this thread" in told and "not instructions" in told
    assert f"run {parent.run_id}" in told and FEATURE in told
    assert "Spec: Search notes" in told and "AC-1" in told and "WP-1" in told
    assert "ended: succeeded, verdict verified" in told
    assert "What it changed" in told and "src/wp-1.py" in told
    assert fake.requests


def test_the_parents_evidence_is_never_reused_verification_runs_again_on_the_new_revision(
    legacy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = first_run(legacy, monkeypatch)
    before = state_of(parent)
    assert before.checks and before.verification.verdict == "verified"

    def second_step(request: Any) -> None:
        if request.package is not None:
            request.ctx.workspace.write_file("app/empty.py", "EMPTY = ''\n")

    assert follow_up(parent, monkeypatch, fake=feature_runner(second_step))[0] == 0

    after = state_of(runs()[1])
    assert after.checks and after.verification.verdict == "verified"
    assert after.verification.revision != before.verification.revision
    old = {c.id: c for c in before.checks}
    for check in after.checks:
        assert check.revision == after.verification.revision != old[check.id].revision
        assert check.started_at is not None and old[check.id].started_at is not None
        assert check.started_at > old[check.id].started_at  # type: ignore[operator]
    assert (runs()[1].run_dir / "commands").is_dir()  # its own command logs
    assert state_of(parent).verification.revision == before.verification.revision  # untouched


def test_the_context_dates_the_parents_verdict_and_hands_over_no_evidence(
    legacy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = first_run(legacy, monkeypatch)
    before = state_of(parent)

    context = thread_context(load_parent(parent))

    assert "none of its checks, test runs or citations are carried over" in context
    assert "The code has changed since" in context
    assert not any(c.revision and c.revision in context for c in before.checks)
    assert before.verification.revision and before.verification.revision not in context


# -- the triage prior ----------------------------------------------------------------------------


def test_the_triage_prompt_carries_the_parents_profile_as_a_prior_only() -> None:
    prior = TaskProfile(intents=["bugfix"], size="small", risk="medium", verify_by=["repro"])

    text = build_messages("also the second case", Signals(), prior)[1]["content"]

    assert "follows up earlier work" in text and "intents bugfix" in text
    assert "a prior, not an answer" in text
    assert "follows up" not in build_messages("also the second case", Signals())[1]["content"]


def test_a_follow_up_the_signals_say_nothing_about_inherits_the_parents_kind_of_work() -> None:
    prior = TaskProfile(intents=["bugfix"], size="medium", risk="medium", verify_by=["repro"])
    signals = Signals(existing_code=True, repo_files=10)

    inherited = heuristic(signals, prior)

    assert inherited.intents == ["bugfix"] and inherited.verify_by == ["repro"]
    assert inherited.size == "medium" and inherited.confidence == "low"
    assert heuristic(signals).intents != ["bugfix"]  # without the prior it is a guess


def test_a_read_only_parent_does_not_make_the_follow_up_read_only() -> None:
    prior = TaskProfile(intents=["question"])

    assert heuristic(Signals(existing_code=True), prior).intents == ["feature"]


def test_the_triage_of_a_follow_up_gets_the_parents_profile(
    legacy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = first_run(legacy, monkeypatch)
    llm = triage_says(monkeypatch, intents=["feature"])
    seen: list[Any] = []
    real = llm.call
    llm.call = lambda messages, **kw: (seen.append(messages), real(messages, **kw))[1]  # type: ignore[method-assign]

    assert follow_up(parent, monkeypatch)[0] == 0

    prompt = seen[0][1]["content"]
    assert "intents feature" in prompt and FOLLOW_UP in prompt
    assert "Earlier work in this thread" not in prompt  # triage sees the follow-up, not the history


# -- plan-only runs ------------------------------------------------------------------------------


def plan_only(repo: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    triage_says(monkeypatch, intents=["plan_only", "feature"], size="medium")
    use_runner(monkeypatch, FakeRunner())
    assert do("Plan a search feature for the notes app.", "--repo", str(repo), "--yes") == 0
    (ref,) = runs()
    assert (ref.run_dir / PLAN_FILE).is_file()
    return ref


def test_continue_implement_builds_what_a_plan_only_run_stored(
    legacy: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = plan_only(legacy, monkeypatch)
    assert git(legacy, "branch", "--list", "engineering-team/*") == ""
    fake = use_runner(monkeypatch, feature_runner())

    code = go("continue", plan.run_id, "--implement", "--yes")

    assert code == 0, capsys.readouterr().out
    child = runs()[1]
    assert child.manifest.mode == "feature" and child.manifest.verdict == "verified"
    assert child.manifest.parent_run == plan.run_id and child.manifest.thread_id == plan.run_id
    assert any(r.stage.name == "implement" and r.package for r in fake.requests)
    told = (child.run_dir / "request.md").read_text(encoding="utf-8")
    assert f"Implement the plan from run {plan.run_id}" in told
    assert "The stored plan to implement:" in told and "Nothing was implemented" in told
    assert git(legacy, "branch", "--list", "engineering-team/*").strip()  # now it has a branch


def test_implement_takes_the_text_as_an_addition_to_the_plan(
    legacy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = plan_only(legacy, monkeypatch)
    use_runner(monkeypatch, feature_runner())

    assert go("continue", plan.run_id, "Skip the command line part.", "--implement", "--yes") == 0

    told = (runs()[1].run_dir / "request.md").read_text(encoding="utf-8")
    assert told.startswith("Skip the command line part.") and "stored plan" in told


def test_a_run_without_a_stored_plan_cannot_be_implemented(
    legacy: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    parent = first_run(legacy, monkeypatch)
    fake = use_runner(monkeypatch, feature_runner())

    assert go("continue", parent.run_id, "--implement", "--yes") == 2

    assert "stored no plan to implement" in capsys.readouterr().err
    assert not fake.calls and len(runs()) == 1
    with pytest.raises(ThreadError):
        implementation_mode(load_parent(parent))


# -- refusals ------------------------------------------------------------------------------------


def test_a_run_that_is_still_going_cannot_be_followed_up(
    legacy: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    parent = first_run(legacy, monkeypatch)
    running = parent.manifest.model_copy(update={"status": "running"})
    atomic_write_json(parent.store.manifest_path(parent.run_id), running.model_dump(mode="json"))

    assert go("continue", parent.run_id, FOLLOW_UP, "--yes") == 2

    assert "is running" in capsys.readouterr().err


def test_an_unknown_run_and_a_missing_follow_up_are_usage_errors(
    legacy: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    parent = first_run(legacy, monkeypatch)
    no_triage_model(monkeypatch)

    assert go("continue", "nope", FOLLOW_UP, "--yes") == 2
    assert "No run 'nope'" in capsys.readouterr().err
    assert go("continue", parent.run_id, "--yes") == 2
    assert "request" in capsys.readouterr().err.lower()


def test_the_preview_of_a_follow_up_says_whose_it_is_and_starts_nothing(
    legacy: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    parent = first_run(legacy, monkeypatch)
    capsys.readouterr()
    fake = use_runner(monkeypatch, feature_runner())

    assert go("--json", "continue", parent.run_id, FOLLOW_UP, "--dry-run") == 0

    plan = json.loads(capsys.readouterr().out)["plan"]
    assert plan["request"] == FOLLOW_UP and any(parent.run_id in n for n in plan["notes"])
    assert not fake.calls and len(runs()) == 1


def test_the_follow_up_is_triaged_on_its_own_words_whatever_the_parent_was(
    legacy: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    parent = first_run(legacy, monkeypatch)
    capsys.readouterr()
    triage_says(monkeypatch, intents=["bugfix"])

    assert go("--json", "continue", parent.run_id, BUG, "--dry-run") == 0

    plan = json.loads(capsys.readouterr().out)["plan"]
    assert plan["profile"]["intents"] == ["bugfix"] and plan["request"] == BUG


# -- the report ----------------------------------------------------------------------------------


def test_the_report_links_the_parent_and_the_children(
    legacy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = first_run(legacy, monkeypatch)
    assert follow_up(parent, monkeypatch)[0] == 0
    child = runs()[1]

    top, bottom = build_report(parent.run_dir), build_report(child.run_dir)

    assert top.thread is not None and top.thread.parent is None
    assert [c.run_id for c in top.thread.children] == [child.run_id]
    assert bottom.thread is not None and bottom.thread.parent is not None
    assert bottom.thread.parent.run_id == parent.run_id
    markdown = render_markdown(top)
    assert "## Thread" in markdown and f"Followed up by run `{child.run_id}`" in markdown
    assert f"Follows run `{parent.run_id}`" in render_markdown(bottom)
    page = render_html(bottom)
    assert 'id="thread"' in page and parent.run_id in page
    assert 'id="thread"' not in render_html(build_report(first_alone(legacy, monkeypatch)))


def first_alone(legacy: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    triage_says(monkeypatch, intents=["feature"])
    use_runner(monkeypatch, feature_runner())
    assert do("Add a delete function.", "--repo", str(legacy), "--yes") == 0
    return runs()[-1].run_dir
