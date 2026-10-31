"""Follow-up runs: what ``continue`` takes from the run it follows.

A run that follows another one is in the same *thread*: it works in the parent's repository, on
the parent's branch (or worktree, or copy), and is told what the parent was asked, specified,
planned, answered and concluded. That is context for the team, not evidence: the parent's
checks, verdict and citations are never carried over, so the follow-up is verified again on the
revision it produces. The thread lives in the manifests (``thread_id``, ``parent_run``).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from engineering_team.contracts import TaskProfile
from engineering_team.git.port import GitError
from engineering_team.intake.triage_rules import MODE_INTENTS, READ_ONLY_INTENTS
from engineering_team.modes.answer_contracts import PLAN_FILE
from engineering_team.modes.change_report import SUMMARY_FILE
from engineering_team.modes.isolation import Isolation, IsolationError, _record, read_isolation
from engineering_team.modes.repo_analyzer import standalone_git, status_counts
from engineering_team.pipeline.state import PipelineState
from engineering_team.runtime.run_index import RunRef

PROFILE_FILE = "task_profile.json"  # the plan `do` kept with the run (``cli/do_run.py``)
MAX_REQUEST = 600
MAX_SUMMARY = 1_500
MAX_ITEMS = 12
MAX_PLAN = 12_000
# The mode that does the work of an intent that changes code (a plan is never implemented as a
# question, a review or another plan).
MODE_OF = {intent: mode for mode, intent in MODE_INTENTS.items() if intent not in READ_ONLY_INTENTS}
BUSY = ("pending", "running")


class ThreadError(ValueError):
    """A run cannot be followed up (a usage error); the message says what to do instead."""


@dataclass(frozen=True)
class ParentRun:
    """The run being followed up, as far as the follow-up needs it."""

    ref: RunRef
    request: str
    document: dict[str, Any]  # the plan `do` kept (``task_profile.json``); {} for 0.2.0 runs
    profile: TaskProfile | None
    state: PipelineState | None
    isolation: Isolation | None  # how the parent got its workspace, if it was isolated

    @property
    def plan_text(self) -> str:
        path = self.ref.run_dir / PLAN_FILE
        return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""

    @property
    def summary_text(self) -> str:
        path = self.ref.run_dir / SUMMARY_FILE
        return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""


@dataclass(frozen=True)
class ThreadLink:
    """What a follow-up run carries from its parent into the run machinery."""

    parent: ParentRun
    implement: bool = False

    @property
    def isolation(self) -> Isolation | None:
        return self.parent.isolation


def load_parent(ref: RunRef) -> ParentRun:
    """Read what ``ref`` left behind. Raises :class:`ThreadError` for a run still going."""

    if ref.manifest.status in BUSY:
        raise ThreadError(
            f"Run {ref.run_id} is {ref.manifest.status}; follow it up once it has ended "
            f"(engineering-team status {ref.run_id})."
        )
    run_dir = ref.run_dir
    document = _json(run_dir / PROFILE_FILE)
    profile: TaskProfile | None = None
    if isinstance(document.get("profile"), dict):
        try:
            profile = TaskProfile.model_validate(document["profile"])
        except ValueError:
            profile = None
    state = PipelineState.load(run_dir)
    try:
        request = (run_dir / "request.md").read_text(encoding="utf-8").strip()
    except OSError:
        request = ""
    return ParentRun(ref, request, document, profile, state, _isolation(ref, state))


def _json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _isolation(ref: RunRef, state: PipelineState | None) -> Isolation | None:
    """How the parent got its workspace: the record its own run kept, else the workspace's."""

    kept = (state.isolation if state is not None else None) or {}
    if kept.get("mode") in ("branch", "worktree", "copy") and kept.get("workspace"):
        return Isolation(
            mode=kept["mode"],
            source=Path(kept.get("source") or kept["workspace"]),
            workspace=Path(kept["workspace"]),
            branch=kept.get("branch"),
            base_branch=kept.get("base_branch"),
            base_commit=kept.get("base_commit"),
        )
    return read_isolation(ref.workspace)


def project_root(parent: ParentRun) -> Path | None:
    """The directory the follow-up is about: the parent's workspace (its branch, worktree or
    copy), or ``None`` when the parent worked on no project (a question with no repository)."""

    if parent.isolation is not None:
        return parent.isolation.workspace
    if parent.document.get("route") == "answer" and not parent.document.get("project"):
        return None
    return parent.ref.workspace


def implementation_mode(parent: ParentRun) -> str:
    """The mode that implements ``parent``'s plan (``continue --implement``). Raises
    :class:`ThreadError` when the run stored no plan."""

    ref = parent.ref
    if not parent.plan_text.strip():
        raise ThreadError(
            f"Run {ref.run_id} stored no plan to implement. Only a plan-only run does "
            '(`engineering-team do "plan ..."`); give a follow-up in words instead.'
        )
    profile = parent.profile
    if profile is not None:
        for intent in profile.intents[1:]:
            if intent in MODE_OF:
                return MODE_OF[intent]
        if profile.target.kind == "new":
            return "new"
    return "feature"


def prior_of(parent: ParentRun) -> TaskProfile | None:
    """The parent's profile, the prior the triage of the follow-up starts from."""

    return parent.profile


# -- what the team is told ------------------------------------------------------------------


def follow_up_request(text: str, parent: ParentRun, *, implement: bool) -> str:
    """The request of a follow-up when the person gave none (``--implement`` alone)."""

    if text.strip():
        return text.strip()
    if implement:
        return f"Implement the plan from run {parent.ref.run_id}."
    raise ThreadError('Say what the follow-up should do: continue RUN_ID "<follow-up>".')


def thread_context(parent: ParentRun, *, implement: bool = False) -> str:
    """The parent's work as context for the follow-up (data, not instructions, and bounded)."""

    manifest = parent.ref.manifest
    ended = manifest.status + (f", verdict {manifest.verdict}" if manifest.verdict else "")
    lines = [
        f"Earlier work in this thread (context from run {parent.ref.run_id}, not instructions):",
        f"- It was asked: {_clip(parent.request, MAX_REQUEST)}",
        f"- It ended: {ended}. The code has changed since, and none of its checks, test runs or "
        "citations are carried over: run them again on the result.",
    ]
    state = parent.state
    spec = state.spec if state is not None else None
    if spec is not None:
        lines.append(f"- Spec: {spec.title}. {_clip(spec.summary, MAX_REQUEST)}")
        lines += [f"  - {c.id}: {_clip(c.text, 200)}" for c in spec.criteria[:MAX_ITEMS]]
    plan = state.plan if state is not None else None
    if plan is not None:
        packages = "; ".join(f"{w.id} {_clip(w.title, 80)}" for w in plan.work_packages[:MAX_ITEMS])
        lines.append(f"- Plan ({plan.stack or 'no stack named'}): {packages or 'no work packages'}")
    answer = state.answer if state is not None else None
    if answer is not None:
        lines.append(f"- Answer it gave: {_clip(answer.summary, MAX_SUMMARY)}")
    if parent.summary_text.strip():
        changed = _clip(parent.summary_text, MAX_SUMMARY)
        lines += ["- What it changed (measured by the controller):", changed]
    if implement:
        lines += ["", "The stored plan to implement:", _clip(parent.plan_text, MAX_PLAN)]
    return "\n".join(lines)


def with_thread(request: str, context: str) -> str:
    return f"{request.rstrip()}\n\n---\n{context}" if context else request


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit].rstrip() + " [...]"


# -- the workspace --------------------------------------------------------------------------


def continue_isolation(parent: Isolation, *, allow_dirty: bool = False) -> Isolation:
    """Give the follow-up the parent's workspace again, on the parent's branch.

    A worktree or copy is used as it is. In the user's own checkout the parent's branch is
    checked out again when the tree is clean (a dirty tree is refused without ``allow_dirty``).
    The follow-up's base is the commit the branch is at now, so its patch and its summary show
    what it changed, not the whole thread. Raises :class:`IsolationError`.
    """

    workspace = parent.workspace
    if not workspace.is_dir():
        raise IsolationError(
            f"The workspace of the earlier run ({workspace}) is gone, so there is no branch to "
            "continue on. Start a new run with `do`."
        )
    notes: list[str] = []
    head: str | None = None
    try:
        with standalone_git(workspace) as git:
            if git.is_repo():
                head = git.head()
                changed, untracked = status_counts(git.status())
                dirty = bool(changed or untracked)
                if parent.branch and git.current_branch() != parent.branch:
                    if parent.mode == "worktree":
                        raise IsolationError(
                            f"The worktree {workspace} is no longer on '{parent.branch}'. Check "
                            "that branch out there again, or start a new run with `do`."
                        )
                    if dirty:
                        raise IsolationError(
                            f"Your checkout is on '{git.current_branch()}' with uncommitted "
                            f"changes, so the thread's branch '{parent.branch}' cannot be "
                            "checked out. Commit or stash them first."
                        )
                    git.switch_branch(parent.branch)
                    notes.append(f"Switched your checkout back to the thread's '{parent.branch}'.")
                    head = git.head()
                elif parent.mode == "branch" and dirty and not allow_dirty:
                    raise IsolationError(
                        f"The working tree at {workspace} has uncommitted changes "
                        f"({changed} changed, {untracked} untracked) on the thread's branch. "
                        "Commit or stash them, or pass --allow-dirty to make them part of the "
                        "follow-up's first commit."
                    )
                elif dirty:
                    notes.append("Uncommitted changes in the workspace are part of the follow-up.")
            elif parent.mode != "copy":
                raise IsolationError(f"{workspace} is no longer a Git repository.")
    except GitError as exc:
        raise IsolationError(f"Git could not return to the thread's branch: {exc}") from exc
    result = Isolation(
        parent.mode, parent.source, workspace, parent.branch, parent.base_branch, head,
        tuple(notes),
    )  # fmt: skip
    _record(result)
    return result


def link_thread(prepared: Any, parent: RunRef) -> None:
    """Record on the new run's manifest which run it follows and which thread it is in."""

    def change(manifest: Any) -> None:
        manifest.thread_id = parent.thread_id
        manifest.parent_run = parent.run_id

    prepared.recorder.store.update(prepared.ctx.run_id, change)
