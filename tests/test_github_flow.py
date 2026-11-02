"""``do --issue`` and ``do --open-pr`` end to end: a fake ``gh`` on ``PATH`` and a local bare
repository standing in for ``origin``. No network, no model."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from cli_helpers import use_runner, workspace_root
from github_fakes import FakeGh, add_origin, git, install_gh
from pipeline_fakes import REQUEST
from repo_fixtures import make_repo
from test_cli_do import FEATURE, do, no_triage_model, plan_of, triage_says
from test_feature_mode import LEGACY, feature_runner

from engineering_team.cli import do_github
from engineering_team.integrations import github
from engineering_team.integrations.issue import IssueRef
from engineering_team.modes.isolation import Isolation
from engineering_team.report import write_report
from engineering_team.runtime.run_index import find_runs

BRANCH_HEADS = ("for-each-ref", "--format=%(refname:short)", "refs/heads")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A repository whose ``origin`` is a GitHub address backed by ``tmp_path/origin.git``."""

    repo = make_repo(tmp_path / "legacy", LEGACY)
    add_origin(repo, tmp_path / "origin.git")
    return repo


@pytest.fixture
def gh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeGh:
    return install_gh(tmp_path / "bin", monkeypatch)


def pushed(tmp_path: Path) -> list[str]:
    return git(tmp_path / "origin.git", *BRANCH_HEADS).split()


def run_json(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, dict[str, Any]]:
    code = do(*args, "--json")
    out = capsys.readouterr().out
    return code, json.loads(out)


# -- the issue is the request --------------------------------------------------------------------


def test_the_issue_is_read_through_gh_and_is_the_request(
    project: Path, gh: FakeGh, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    no_triage_model(monkeypatch)

    assert do("--issue", "acme/notes#7", "--repo", str(project), "--dry-run", "--json") == 0

    plan = plan_of(capsys)
    assert plan["request"].startswith("# Search should find notes by text")
    assert "Add a search function over note text." in plan["request"]
    assert plan["github"] == {
        "issue": {
            "ref": "acme/notes#7",
            "title": "Search should find notes by text",
            "url": "https://github.com/acme/notes/issues/7",
        },
        "open_pr": False,
        "remote": None,
    }
    assert gh.verbs() == ["auth status", "issue view"]


def test_an_issue_url_works_and_the_preview_names_the_issue(
    project: Path, gh: FakeGh, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    no_triage_model(monkeypatch)

    code = do(
        "--issue", "https://github.com/acme/notes/issues/7", "--repo", str(project), "--dry-run"
    )

    assert code == 0
    assert "Issue: acme/notes#7 Search should find notes by text" in capsys.readouterr().out


def test_the_issue_and_the_users_own_words_are_both_the_request(
    project: Path, gh: FakeGh, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    no_triage_model(monkeypatch)

    code = do(
        "Keep it small.", "--issue", "acme/notes#7", "--repo", str(project), "--dry-run", "--json"
    )

    request = plan_of(capsys)["request"]
    assert code == 0
    assert request.startswith("## Request: inline request\n\nKeep it small.")
    assert "## Request: GitHub issue acme/notes#7" in request


@pytest.mark.parametrize(
    ("issue", "message"),
    [("seven", "owner/repo#123 or an issue URL"), ("acme/notes#0", "owner/repo#123")],
)
def test_a_bad_issue_reference_is_a_usage_error(
    issue: str, message: str, project: Path, gh: FakeGh, capsys: pytest.CaptureFixture[str]
) -> None:
    assert do("--issue", issue, "--repo", str(project), "--dry-run") == 2

    assert message in capsys.readouterr().err and not gh.calls()


def test_without_gh_the_issue_is_refused_with_the_fix(
    project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path / "nothing"))

    assert do("--issue", "acme/notes#7", "--repo", str(project), "--dry-run") == 2

    err = capsys.readouterr().err
    assert "`gh` is not installed" in err and "never handles a token" in err


def test_a_signed_out_gh_is_refused_before_anything_is_read(
    project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = install_gh(tmp_path / "bin", monkeypatch, signed_in=False)

    assert do("--issue", "acme/notes#7", "--repo", str(project), "--dry-run") == 2

    assert "gh auth login" in capsys.readouterr().err and fake.verbs() == ["auth status"]


# -- without --open-pr nothing is pushed ---------------------------------------------------------


@pytest.mark.git
@pytest.mark.usefixtures("full_pipeline")
def test_a_run_from_an_issue_pushes_nothing_without_open_pr_and_the_report_cites_the_issue(
    project: Path,
    tmp_path: Path,
    gh: FakeGh,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    triage_says(monkeypatch, intents=["feature"])
    use_runner(monkeypatch, feature_runner())

    code, data = run_json(capsys, "--issue", "acme/notes#7", "--repo", str(project))

    assert code == 0 and data["verdict"] == "verified"
    assert "pull_request" not in data
    assert pushed(tmp_path) == [] and "pr create" not in gh.verbs()
    (ref,) = find_runs(workspace_root())
    assert "Search should find notes by text" in (ref.run_dir / "request.md").read_text()
    assert "Issue: acme/notes#7 Search should find notes by text" in write_report(
        ref.run_dir, "md"
    ).read_text(encoding="utf-8")
    assert "acme/notes#7" in write_report(ref.run_dir, "html").read_text(encoding="utf-8")


@pytest.mark.git
@pytest.mark.usefixtures("full_pipeline")
def test_a_plain_run_never_calls_gh(
    project: Path,
    tmp_path: Path,
    gh: FakeGh,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    triage_says(monkeypatch, intents=["feature"])
    use_runner(monkeypatch, feature_runner())

    code, data = run_json(capsys, FEATURE, "--repo", str(project))

    assert code == 0 and "pull_request" not in data
    assert gh.calls() == [] and pushed(tmp_path) == []


# -- --open-pr -----------------------------------------------------------------------------------


@pytest.mark.git
@pytest.mark.usefixtures("full_pipeline")
def test_open_pr_pushes_the_branch_and_opens_a_draft_with_the_report_summary(
    project: Path,
    tmp_path: Path,
    gh: FakeGh,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    git(project, "push", "-q", "origin", "main")  # so origin/main is a known base
    triage_says(monkeypatch, intents=["feature"])
    use_runner(monkeypatch, feature_runner())

    code, data = run_json(
        capsys, "--issue", "acme/notes#7", "--repo", str(project), "--open-pr", "--yes"
    )

    branch = data["isolation"]["branch"]
    assert code == 0, data
    assert data["pull_request"] == {
        "status": "opened",
        "url": "https://github.com/acme/notes/pull/9",
        "branch": branch,
        "base": "main",
        "draft": True,
    }
    assert branch in pushed(tmp_path)
    assert (
        git(tmp_path / "origin.git", "rev-parse", branch).strip()
        == git(project, "rev-parse", branch).strip()
    )
    create = gh.calls()[-1]
    assert create["args"][:2] == ["pr", "create"] and "--draft" in create["args"]
    for flag, value in (
        ("--repo", "acme/notes"),
        ("--head", branch),
        ("--base", "main"),
        ("--title", "Search should find notes by text"),
    ):
        assert create["args"][create["args"].index(flag) + 1] == value
    body = create["body"]
    assert body.startswith("Closes acme/notes#7\n")
    assert "**Verification: verified**" in body and "### Checks" in body
    assert "### Changes" in body and "### Cost" in body and "### Full report" in body
    assert "report.html" in body
    (ref,) = find_runs(workspace_root())
    assert (ref.run_dir / "pull_request.md").read_text(encoding="utf-8") == body


@pytest.mark.git
@pytest.mark.usefixtures("full_pipeline")
def test_a_base_branch_origin_does_not_know_is_left_to_the_repositorys_default(
    project: Path,
    gh: FakeGh,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    triage_says(monkeypatch, intents=["feature"])
    use_runner(monkeypatch, feature_runner())

    code, data = run_json(capsys, FEATURE, "--repo", str(project), "--open-pr", "--yes")

    assert code == 0 and data["pull_request"]["status"] == "opened"
    assert data["pull_request"]["base"] is None and "--base" not in gh.calls()[-1]["args"]


@pytest.mark.git
@pytest.mark.usefixtures("full_pipeline")
def test_without_yes_and_without_a_person_to_ask_nothing_is_pushed(
    project: Path,
    tmp_path: Path,
    gh: FakeGh,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    triage_says(monkeypatch, intents=["feature"])
    use_runner(monkeypatch, feature_runner())

    code, data = run_json(capsys, FEATURE, "--repo", str(project), "--open-pr")

    assert code == 0 and data["verdict"] == "verified"
    assert data["pull_request"]["status"] == "skipped"
    assert "--yes" in data["pull_request"]["reason"]
    assert pushed(tmp_path) == [] and "pr create" not in gh.verbs()


@pytest.mark.git
@pytest.mark.usefixtures("full_pipeline")
def test_a_failed_pull_request_is_reported_and_the_exit_code_says_so(
    project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    install_gh(tmp_path / "bin", monkeypatch, pr_error="GraphQL: not allowed (createPullRequest)")
    triage_says(monkeypatch, intents=["feature"])
    use_runner(monkeypatch, feature_runner())

    code, data = run_json(capsys, FEATURE, "--repo", str(project), "--open-pr", "--yes")

    assert code == 1 and data["exit_code"] == 1 and data["verdict"] == "verified"
    assert data["pull_request"]["status"] == "failed"
    assert "createPullRequest" in data["pull_request"]["error"]


# -- planning ------------------------------------------------------------------------------------


def test_the_preview_says_what_would_be_pushed_and_where(
    project: Path, gh: FakeGh, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    triage_says(monkeypatch, intents=["feature"])

    assert do(FEATURE, "--repo", str(project), "--open-pr", "--dry-run") == 0

    out = capsys.readouterr().out
    assert "push the branch to origin (github.com/acme/notes)" in out
    assert "open a draft pull request (you are asked first)." in out
    assert "pr create" not in gh.verbs()


def test_the_setting_is_the_standing_form_of_the_flag_and_the_flag_can_turn_it_off(
    project: Path,
    tmp_path: Path,
    gh: FakeGh,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    triage_says(monkeypatch, intents=["feature"])
    config = tmp_path / "team.toml"
    config.write_text("[github]\nopen_pr = true\n", encoding="utf-8")
    base = [FEATURE, "--repo", str(project), "--config", str(config), "--dry-run", "--json"]

    assert do(*base) == 0 and plan_of(capsys)["github"]["open_pr"] is True
    assert do(*base, "--no-open-pr") == 0 and plan_of(capsys)["github"]["open_pr"] is False


def test_open_pr_for_something_that_is_not_a_change_to_a_repository_is_refused(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    triage_says(monkeypatch, intents=["new_project"])

    assert do(REQUEST, "--open-pr", "--dry-run") == 2

    assert "--open-pr opens a pull request for a change to an existing Git repository" in (
        capsys.readouterr().err
    )


def test_the_setting_alone_is_ignored_where_there_is_nothing_to_push(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    triage_says(monkeypatch, intents=["new_project"])
    config = tmp_path / "team.toml"
    config.write_text("[github]\nopen_pr = true\n", encoding="utf-8")

    code = do(REQUEST, "--config", str(config), "--dry-run", "--json")

    assert code == 0 and plan_of(capsys)["github"]["open_pr"] is False


def test_a_repository_without_a_github_origin_is_refused_before_anything_is_spent(
    tmp_path: Path, gh: FakeGh, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    triage_says(monkeypatch, intents=["feature"])
    bare = make_repo(tmp_path / "plain", LEGACY)

    assert do(FEATURE, "--repo", str(bare), "--open-pr", "--dry-run") == 2
    assert "has no remote called 'origin'" in capsys.readouterr().err

    git(bare, "remote", "add", "origin", str(tmp_path / "elsewhere.git"))
    assert do(FEATURE, "--repo", str(bare), "--open-pr", "--dry-run") == 2
    assert "not a GitHub repository address" in capsys.readouterr().err


def test_the_setting_alone_does_not_block_a_repository_that_cannot_open_one(
    tmp_path: Path, gh: FakeGh, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    triage_says(monkeypatch, intents=["feature"])
    plain = make_repo(tmp_path / "plain", LEGACY)  # no origin
    config = tmp_path / "team.toml"
    config.write_text("[github]\nopen_pr = true\n", encoding="utf-8")
    base = [FEATURE, "--repo", str(plain), "--config", str(config), "--dry-run"]

    assert do(*base) == 0

    out = capsys.readouterr().out
    assert "No pull request will be opened (github.open_pr is set)" in out
    assert "has no remote called 'origin'" in out and "After a passing run" not in out
    assert do(*base, "--open-pr") == 2  # asked for on the command line, it is an error


# -- publishing, unit ----------------------------------------------------------------------------


def waiting(remote: str = "acme/notes") -> do_github.GithubRun:
    run = do_github.GithubRun(open_pr=True, issue_ref=IssueRef("acme/notes", 7))
    run.remote = github.Remote("github.com", remote)
    return run


def isolation(tmp_path: Path) -> Isolation:
    return Isolation("branch", tmp_path, tmp_path, "engineering-team/r-x", "main", "abc")


@pytest.mark.parametrize(
    "data",
    [
        {"exit_code": 3, "verdict": "failed"},
        {"exit_code": 4, "verdict": "partial"},
        {"exit_code": 0, "verdict": None},
        {"exit_code": 130, "verdict": None},
    ],
)
def test_only_a_passing_run_is_published(data: dict[str, Any], tmp_path: Path, gh: FakeGh) -> None:
    waiting().publish(SimpleNamespace(json=True, quiet=True), None, isolation(tmp_path), data)

    assert data["pull_request"] == {"status": "skipped", "reason": "the run did not pass"}
    assert gh.calls() == []


class Terminal:
    def isatty(self) -> bool:
        return True


def test_the_user_is_asked_unless_yes_and_a_no_pushes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked: list[str] = []
    answers = [False, True]
    monkeypatch.setattr(
        do_github.typer, "confirm", lambda text, **_: asked.append(text) or answers.pop(0)
    )
    monkeypatch.setattr(sys, "stdin", Terminal())
    monkeypatch.setattr(sys, "stderr", Terminal())
    terminal = SimpleNamespace(json=False, quiet=False)
    remote = github.Remote("github.com", "acme/notes")
    run = waiting()

    assert run._agreed(terminal, "engineering-team/r-x", remote) is False
    assert run._agreed(terminal, "engineering-team/r-x", remote) is True
    assert (
        asked
        == [
            "Push branch 'engineering-team/r-x' to origin (github.com/acme/notes) and open a draft "
            "pull request?"
        ]
        * 2
    )
    run.yes = True
    assert run._agreed(SimpleNamespace(json=True, quiet=True), "b", remote) is True
    assert len(asked) == 2  # --yes asks nothing, even with nobody to answer


def test_without_a_terminal_or_with_json_an_unconfirmed_push_is_not_made(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(*args: object, **kwargs: object) -> bool:
        raise AssertionError("must not ask")

    monkeypatch.setattr(do_github.typer, "confirm", refuse)
    remote = github.Remote("github.com", "acme/notes")

    assert waiting()._agreed(SimpleNamespace(json=False, quiet=False), "b", remote) is False
    monkeypatch.setattr(sys, "stdin", Terminal())
    monkeypatch.setattr(sys, "stderr", Terminal())
    assert waiting()._agreed(SimpleNamespace(json=True, quiet=False), "b", remote) is False
    assert waiting()._agreed(SimpleNamespace(json=False, quiet=True), "b", remote) is False
