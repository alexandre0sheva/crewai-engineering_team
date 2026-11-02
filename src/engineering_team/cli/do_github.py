"""``do --issue`` and ``do --open-pr``: GitHub on either side of a run.

``--issue owner/repo#N`` (or an issue URL) makes the issue the request. ``--open-pr`` (or
``github.open_pr = true``), after a run that passed, pushes the run's branch to ``origin`` and
opens a **draft** pull request whose body is the report summary; it asks first unless ``--yes``.
Both go through ``gh`` on this machine (``integrations/github.py``). Without the flag nothing is
pushed.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import typer

from engineering_team.atomic_io import atomic_write_text
from engineering_team.cli.context import Globals
from engineering_team.integrations import github
from engineering_team.integrations.issue import (
    GithubError,
    Issue,
    IssueRef,
    parse_issue_ref,
    save_issue,
)
from engineering_team.integrations.pr_body import (
    CheckLine,
    CriterionLine,
    FileLine,
    PullRequestFacts,
    pull_request_title,
    render_pr_body,
)
from engineering_team.modes.isolation import Isolation
from engineering_team.outcome import EXIT_FAILURE
from engineering_team.pipeline.state import PipelineState
from engineering_team.settings import Settings

PR_BODY_FILE = "pull_request.md"
ISSUE_BUDGET_MARGIN = 4_000  # room for the user's own words next to the issue


@dataclass
class GithubRun:
    """What the command line asked of GitHub, and what was found out while planning."""

    issue_ref: IssueRef | None = None
    open_pr_flag: bool | None = None  # --open-pr / --no-open-pr; None: the setting decides
    yes: bool = False
    open_pr: bool = False  # resolved against the settings
    issue: Issue | None = None
    remote: github.Remote | None = None
    note: str = ""  # why a pull request the setting asked for will not be opened

    @classmethod
    def from_options(cls, issue: str | None, open_pr: bool | None, yes: bool) -> GithubRun:
        return cls(parse_issue_ref(issue) if issue else None, open_pr, yes)

    def intake(self, settings: Settings) -> list[tuple[str, str]]:
        """Read the issue (a request source) and settle whether a pull request is wanted."""

        self.open_pr = (
            self.open_pr_flag if self.open_pr_flag is not None else settings.github.open_pr
        )
        if self.issue_ref is None:
            return []
        with github.host_runner() as runner:
            self.issue = github.fetch_issue(runner, self.issue_ref)
        limit = settings.intake.max_request_chars - ISSUE_BUDGET_MARGIN
        return [(f"GitHub issue {self.issue_ref.text}", self.issue.request_text(limit))]

    def check_plan(self, route: str, root: Path | None) -> None:
        """Refuse before anything is spent when a pull request cannot be opened for this plan."""

        if not self.open_pr:
            return
        if route != "repository" or root is None:
            if self.open_pr_flag:
                raise GithubError(
                    "--open-pr opens a pull request for a change to an existing Git repository, "
                    f"and this request is a {route} one. Leave --open-pr out."
                )
            self.open_pr = False  # the setting only applies where there is a change to push
            return
        try:
            with github.host_runner() as runner:
                self.remote = github.origin_remote(runner, root)
                github.require_gh(runner, self.remote.host)
        except GithubError as exc:
            if self.open_pr_flag:
                raise
            # Only the setting asked: the request is still worth doing, without the pull request.
            self.open_pr, self.remote = False, None
            self.note = f"No pull request will be opened (github.open_pr is set): {exc}"

    def plan_lines(self) -> list[str]:
        lines = [self.note] if self.note else []
        if self.issue is not None:
            lines.append(f"Issue: {self.issue.ref.text} {self.issue.title} ({self.issue.url})")
        if self.open_pr and self.remote is not None:
            lines.append(
                f"After a passing run: push the branch to origin ({self.remote.shown}) and "
                "open a draft pull request" + ("." if self.yes else " (you are asked first).")
            )
        return lines

    def document(self) -> dict[str, Any]:
        """What ``do --dry-run --json`` says about GitHub."""

        return {
            "issue": (
                {"ref": self.issue.ref.text, "title": self.issue.title, "url": self.issue.url}
                if self.issue is not None
                else None
            ),
            "open_pr": self.open_pr,
            "remote": self.remote.shown if self.remote is not None else None,
        }

    def opened(self, prepared: Any) -> None:
        """The run exists: keep the issue it came from with it (the report cites it)."""

        if self.issue is not None:
            save_issue(prepared.ctx.run_dir, self.issue)

    def publish(
        self, g: Globals, prepared: Any, isolation: Isolation, data: dict[str, Any]
    ) -> None:
        """After the run: push and open the draft pull request when the run passed and the user
        agrees; the outcome goes into the summary data as ``pull_request``."""

        if not self.open_pr:
            return
        if data["exit_code"] != 0 or data["verdict"] != "verified":
            data["pull_request"] = {"status": "skipped", "reason": "the run did not pass"}
            return
        if isolation.branch is None or self.remote is None:
            data["pull_request"] = {"status": "skipped", "reason": "the run has no branch"}
            return
        branch, remote = isolation.branch, self.remote
        if not self._agreed(g, branch, remote):
            data["pull_request"] = {
                "status": "skipped",
                "reason": "not confirmed (pass --yes to open it without being asked)",
            }
            return
        ctx = prepared.ctx
        try:
            with github.host_runner() as runner:
                base = isolation.base_branch
                if not (base and github.remote_has_branch(runner, isolation.source, base)):
                    base = None  # the repository's default branch
                facts = self._facts(prepared, isolation, data, branch, base)
                body_file = ctx.run_dir / PR_BODY_FILE
                atomic_write_text(body_file, render_pr_body(facts))
                title = pull_request_title(self._title(prepared), self._fallback_title())
                github.push_branch(runner, isolation.workspace, branch)
                ctx.events.emit("github.push", branch=branch, remote=remote.shown)
                url = github.open_draft_pr(
                    runner, isolation.workspace, remote,
                    head=branch, base=base, title=title, body_file=body_file,
                )  # fmt: skip
                ctx.events.emit("github.pull_request", url=url, draft=True)
        except GithubError as exc:
            data["pull_request"] = {"status": "failed", "error": str(exc)}
            data["exit_code"] = EXIT_FAILURE
            sys.stderr.write(f"Pull request not opened: {exc}\n")
            return
        data["pull_request"] = {
            "status": "opened", "url": url, "branch": branch, "base": base, "draft": True,
        }  # fmt: skip

    # -- internals ---------------------------------------------------------------------

    def _agreed(self, g: Globals, branch: str, remote: github.Remote) -> bool:
        if self.yes:
            return True
        if g.json or g.quiet or not (sys.stdin.isatty() and sys.stderr.isatty()):
            return False  # nobody can answer, and a push is not done unasked
        try:
            return typer.confirm(
                f"Push branch '{branch}' to origin ({remote.shown}) and open a draft pull request?",
                default=False,
                err=True,
            )
        except typer.Abort:
            return False

    def _fallback_title(self) -> str:
        return self.issue.title if self.issue else "Change by engineering-team"

    def _title(self, prepared: Any) -> str:
        state = PipelineState.load(prepared.ctx.run_dir)
        if self.issue is not None and self.issue.title:
            return self.issue.title
        if state is not None and state.spec is not None:
            return state.spec.title
        if state is not None and state.triage is not None:
            return state.triage.title
        return ""

    def _facts(
        self, prepared: Any, isolation: Isolation, data: dict[str, Any], branch: str,
        base: str | None,
    ) -> PullRequestFacts:  # fmt: skip
        ctx = prepared.ctx
        state = PipelineState.load(ctx.run_dir)
        files: list[FileLine] = []
        if isolation.base_commit:
            try:
                files = [
                    FileLine(c.path, c.status, c.added, c.removed)
                    for c in ctx.git.changes(isolation.base_commit)
                ]
            except Exception:  # the stats are a courtesy: a pull request without them is fine
                files = []
        coverage = state.verification.coverage if state else []
        texts = {c.id: c.text for c in (state.spec.criteria if state and state.spec else [])}
        remote, issue = self.remote, self.issue
        return PullRequestFacts(
            run_id=data["run_id"],
            verdict=str(data["verdict"]),
            branch=branch,
            base_branch=base,
            issue=issue.ref if issue else None,
            closes_issue=bool(
                issue and remote and issue.ref.repo == remote.repo and issue.ref.host == remote.host
            ),  # fmt: skip
            checks=[
                CheckLine(c["name"], c["status"], bool(c["required"]), c["summary"])
                for c in data["checks"]
            ],
            criteria=[
                CriterionLine(c.id, c.text or texts.get(c.id, ""), c.status) for c in coverage
            ],
            files=files,
            cost_usd=data["usage"]["estimated_cost_usd"],
            duration_seconds=data["duration_seconds"],
            report_path=data.get("run_report") or data.get("report"),
        )
