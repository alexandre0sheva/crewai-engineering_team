"""GitHub integration, the parts that need no run: issue references, the issue as a request, remote
addresses, the pull request body, and the settings."""

from __future__ import annotations

from pathlib import Path

import pytest
from github_fakes import ISSUE, install_gh

from engineering_team.integrations import github
from engineering_team.integrations.issue import (
    GithubError,
    Issue,
    IssueRef,
    load_cite,
    parse_issue_ref,
    save_issue,
)
from engineering_team.integrations.pr_body import (
    CheckLine,
    CriterionLine,
    FileLine,
    PullRequestFacts,
    defuse,
    pull_request_title,
    render_pr_body,
)
from engineering_team.settings import load_settings

# -- issue references ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "ref"),
    [
        ("acme/notes#7", IssueRef("acme/notes", 7)),
        (" acme/my-notes.v2#123 ", IssueRef("acme/my-notes.v2", 123)),
        ("https://github.com/acme/notes/issues/7", IssueRef("acme/notes", 7)),
        ("https://github.com/acme/notes/issues/7/", IssueRef("acme/notes", 7)),
        ("https://github.com/acme/notes/issues/7#issuecomment-1", IssueRef("acme/notes", 7)),
        (
            "https://ghe.example.com/acme/notes/issues/7",
            IssueRef("acme/notes", 7, "ghe.example.com"),
        ),
    ],
)
def test_an_issue_is_named_by_owner_repo_number_or_by_its_url(text: str, ref: IssueRef) -> None:
    assert parse_issue_ref(text) == ref


@pytest.mark.parametrize(
    "text",
    ["7", "#7", "acme/notes", "acme/notes#0", "acme/notes#x", "-x/notes#1", "a/b/c#1",
     "https://github.com/acme/notes/pull/7", "acme/notes#7; rm -rf /"],
)  # fmt: skip
def test_anything_else_is_a_usage_error_that_says_what_is_accepted(text: str) -> None:
    with pytest.raises(GithubError, match=r"owner/repo#123 or an issue URL"):
        parse_issue_ref(text)


def test_the_reference_prints_the_way_it_was_given_and_names_the_gh_repository() -> None:
    assert IssueRef("acme/notes", 7).text == "acme/notes#7"
    assert IssueRef("acme/notes", 7).gh_repo == "acme/notes"
    ghe = IssueRef("acme/notes", 7, "ghe.example.com")
    assert (
        ghe.text == "ghe.example.com/acme/notes#7" and ghe.gh_repo == "ghe.example.com/acme/notes"
    )


# -- the issue as a request ----------------------------------------------------------------------


def test_the_issue_becomes_a_request_with_its_title_body_and_comments() -> None:
    issue = Issue.from_json(IssueRef("acme/notes", 7), ISSUE)

    text = issue.request_text(10_000)

    assert text.startswith("# Search should find notes by text")
    assert "Issue acme/notes#7 · open · opened by reporter · labels: enhancement" in text
    assert "https://github.com/acme/notes/issues/7" in text
    assert "Add a search function over note text." in text
    assert "### maintainer commented\n\nCase-insensitive please." in text


def test_a_long_issue_is_cut_to_the_limit_keeping_the_newest_comments() -> None:
    data = {
        **ISSUE,
        "body": "b" * 5_000,
        "comments": [
            {"author": {"login": f"user{n}"}, "body": f"comment {n} " + "c" * 500}
            for n in range(1, 21)
        ],
    }
    issue = Issue.from_json(IssueRef("acme/notes", 7), data)

    text = issue.request_text(3_000)

    assert len(text) <= 3_000
    assert "omitted" in text
    assert "comment 20 " in text and "comment 1 " not in text
    assert text.index("comment 19 ") < text.index("comment 20 ")  # oldest shown first
    assert "older not shown" in text


def test_a_sparse_issue_still_reads_as_a_request() -> None:
    issue = Issue.from_json(IssueRef("acme/notes", 7), {"title": "Fix it", "body": None})

    assert "(the issue has no description)" in issue.request_text(2_000)
    assert "## Comments" not in issue.request_text(2_000)


def test_the_run_keeps_the_issue_it_came_from(tmp_path: Path) -> None:
    issue = Issue.from_json(IssueRef("acme/notes", 7), ISSUE)

    assert load_cite(tmp_path) is None
    save_issue(tmp_path, issue)
    cite = load_cite(tmp_path)

    assert cite is not None
    assert cite.line == (
        "acme/notes#7 Search should find notes by text (https://github.com/acme/notes/issues/7)"
    )
    (tmp_path / "github_issue.json").write_text("{not json", encoding="utf-8")
    assert load_cite(tmp_path) is None


# -- remotes -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "shown"),
    [
        ("https://github.com/acme/notes.git", "github.com/acme/notes"),
        ("https://github.com/acme/notes", "github.com/acme/notes"),
        ("https://x-access-token:SECRET@github.com/acme/notes.git", "github.com/acme/notes"),
        ("git@github.com:acme/notes.git", "github.com/acme/notes"),
        ("ssh://git@ghe.example.com:2222/acme/notes.git", "ghe.example.com/acme/notes"),
    ],
)
def test_a_remote_address_gives_host_and_repository_and_never_a_credential(
    url: str, shown: str
) -> None:
    remote = github.parse_remote(url)

    assert remote is not None and remote.shown == shown
    assert "SECRET" not in repr(remote)


@pytest.mark.parametrize("url", ["", "/srv/git/notes.git", "https://github.com/acme", "notes"])
def test_a_remote_that_is_not_a_repository_address_is_not_one(url: str) -> None:
    assert github.parse_remote(url) is None


# -- gh --------------------------------------------------------------------------------------------


def test_gh_missing_or_signed_out_is_a_usage_error_that_says_how_to_fix_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    with github.host_runner() as runner, pytest.raises(GithubError, match="gh auth login"):
        github.require_gh(runner)

    fake = install_gh(tmp_path / "bin", monkeypatch, signed_in=False)
    with github.host_runner() as runner, pytest.raises(GithubError, match="not signed in"):
        github.fetch_issue(runner, IssueRef("acme/notes", 7))
    assert fake.verbs() == ["auth status"]  # it stopped before reading anything


def test_the_issue_is_read_with_one_read_only_gh_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = install_gh(tmp_path / "bin", monkeypatch)

    with github.host_runner() as runner:
        issue = github.fetch_issue(runner, IssueRef("acme/notes", 7))

    assert issue.title == "Search should find notes by text" and issue.comments
    assert fake.verbs() == ["auth status", "issue view"]
    argv = fake.calls()[1]["args"]
    assert argv[:5] == ["issue", "view", "7", "--repo", "acme/notes"] and "--json" in argv


# -- the pull request body -----------------------------------------------------------------------


def facts(**changes: object) -> PullRequestFacts:
    base = PullRequestFacts(
        run_id="run-1",
        verdict="verified",
        branch="engineering-team/run-1-search",
        base_branch="main",
        issue=IssueRef("acme/notes", 7),
        closes_issue=True,
        checks=[
            CheckLine("pytest", "passed", True, "12 passed"),
            CheckLine("ruff", "failed", False, "1 problem | in app.py"),
        ],
        criteria=[CriterionLine("AC-1", "search returns matching notes", "verified")],
        files=[FileLine("app/store.py", "M", 10, 2), FileLine("logo.png", "A", None, None)],
        cost_usd=0.1234,
        duration_seconds=125,
        report_path="/work/ws/.engineering-team/runs/run-1/report.html",
    )
    return base.__class__(**{**base.__dict__, **changes})


def test_the_body_has_the_verdict_checks_criteria_diff_stats_cost_and_the_report_path() -> None:
    body = render_pr_body(facts())

    assert body.startswith("Closes acme/notes#7\n")
    assert (
        "**Verification: verified** (run `run-1`, branch `engineering-team/run-1-search` → `main`)"
        in body
    )
    assert "| pytest | passed | yes | 12 passed |" in body
    assert "| ruff | failed | no | 1 problem \\| in app.py |" in body
    assert "- AC-1 (verified): search returns matching notes" in body
    assert "2 file(s), +10 −2." in body and "| `logo.png` | A | binary |" in body
    assert "$0.1234 estimated, 2m05s." in body
    assert "/work/ws/.engineering-team/runs/run-1/report.html" in body
    assert "draft" in body and "not evidence" in body


def test_an_issue_of_another_repository_is_referred_to_not_closed() -> None:
    body = render_pr_body(facts(closes_issue=False, issue=IssueRef("other/repo", 3)))

    assert body.startswith("Refs other/repo#3\n") and "Closes" not in body


def test_a_body_without_checks_files_or_cost_says_so_instead_of_leaving_holes() -> None:
    body = render_pr_body(
        facts(
            checks=[],
            criteria=[],
            files=[],
            cost_usd=None,
            duration_seconds=None,
            report_path=None,
            issue=None,
        )  # fmt: skip
    )

    assert "No file changes were measured." in body and "cost unknown." in body
    assert "### Checks" not in body and "Closes" not in body and "Refs" not in body


def test_text_from_the_project_cannot_notify_anyone_or_break_the_table() -> None:
    body = render_pr_body(
        facts(
            checks=[CheckLine("lint", "failed", True, "ask @octocat <b>x</b>\nline two")],
            criteria=[CriterionLine("AC-2", "ping @team-lead", "unverified")],
        )
    )

    assert "@octocat" not in body and "@team-lead" not in body
    assert "@​octocat" in body and "&lt;b&gt;" in body
    assert defuse("a@b.com and @me") == "a@​b.com and @​me"


def test_a_huge_body_is_cut_before_githubs_limit_and_the_title_is_short() -> None:
    body = render_pr_body(facts(files=[FileLine(f"f{n}.py", "A", 1, 0) for n in range(5_000)]))

    assert len(body) < 65_000 and "... and 4975 more file(s)." in body
    title = pull_request_title("x " * 200, "fallback")
    assert len(title) <= 100 and title.endswith("...")
    assert pull_request_title("", "fallback") == "fallback"


# -- settings ------------------------------------------------------------------------------------


def test_opening_a_pull_request_is_off_unless_the_settings_say_otherwise() -> None:
    assert load_settings().github.open_pr is False
    assert load_settings(overrides={"github": {"open_pr": True}}).github.open_pr is True
