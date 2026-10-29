"""The triage evaluation: ``benchmarks/triage/cases.yaml`` and how it is scored.

Each case is a labelled request with an optional repository (a benchmark task's ``fixture/``,
copied and, unless ``git: false``, made a Git repository) and the signals the deterministic
layer must report for it. ``bench triage`` checks every case's signals and scores the
signals-only guess offline; ``bench triage --live`` scores one cheap-tier model call per case:
primary-intent accuracy, size and target accuracy, recall of the secondary intents, how often it
asked when a case expects questions, and a confusion matrix of primary intents.
"""

from __future__ import annotations

import shutil
from collections import Counter
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from engineering_team.bench.tasks import BenchError
from engineering_team.contracts import Intent, Risk, TaskProfile, TaskScale
from engineering_team.intake.signals import Signals, collect
from engineering_team.intake.triage_rules import INTENTS, MODE_INTENTS
from engineering_team.modes.repo_analyzer import analyze_repo, standalone_git
from engineering_team.modes.repo_profile import RepoProfile

CASES_FILE = Path("triage") / "cases.yaml"
EMPTY_REPO = "@empty"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CaseExpect(_Strict):
    intents: list[Intent] = Field(min_length=1)
    size: TaskScale
    target: Literal["new", "existing"]
    risk: Risk | None = None
    clarify: bool | None = None


class SignalExpect(_Strict):
    """What the signal layer must report; absent fields mean false or empty."""

    git_repo: bool | None = None  # default: what the case's repo and git set up
    existing_code: bool | None = None
    stack_trace: bool = False
    failing_tests: list[str] = Field(default_factory=list)
    refs: list[str] = Field(default_factory=list)
    question: bool = False
    paths: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)


class TriageCase(_Strict):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    request: str = Field(min_length=1)
    repo: str | None = None
    git: bool = True
    mode: str | None = None
    expect: CaseExpect
    signals: SignalExpect


class CaseOutcome(BaseModel):
    """One case's triage, scored against its label."""

    id: str
    expected: list[str]
    predicted: list[str]
    size: tuple[str, str]  # (expected, predicted)
    target: tuple[str, str]
    asked: bool
    expect_clarify: bool | None = None
    source: str = "model"
    error: str | None = None
    cost_usd: float | None = None

    @property
    def primary_ok(self) -> bool:
        return self.predicted[:1] == self.expected[:1]


def load_cases(path: Path, tasks_dir: Path) -> list[TriageCase]:
    """The cases of ``path``; raises :class:`BenchError` with one line per problem."""

    try:
        data: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise BenchError(f"Cannot read {path}: {exc}") from exc
    raw = data.get("cases") if isinstance(data, dict) else None
    if not isinstance(raw, list) or not raw:
        raise BenchError(f"{path}: expected a non-empty `cases:` list.")
    problems: list[str] = []
    cases: list[TriageCase] = []
    for index, item in enumerate(raw, 1):
        name = item.get("id", f"#{index}") if isinstance(item, dict) else f"#{index}"
        try:
            case = TriageCase.model_validate(item)
        except ValidationError as exc:
            problems += [
                f"{name}: {'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
            ]
            continue
        if case.repo not in (None, EMPTY_REPO) and not (tasks_dir / case.repo / "fixture").is_dir():
            problems.append(f"{case.id}: repo {case.repo!r} has no {tasks_dir.name}/<id>/fixture")
        if case.mode is not None and case.mode not in MODE_INTENTS:
            modes = ", ".join(MODE_INTENTS)
            problems.append(f"{case.id}: mode {case.mode!r} is not one of {modes}")
        unknown = sorted(set(case.signals.keywords) - set(INTENTS))
        if unknown:
            problems.append(f"{case.id}: unknown keyword groups {', '.join(unknown)}")
        cases.append(case)
    counts = Counter(case.id for case in cases)
    problems += [f"{name}: the id is used {n} times" for name, n in counts.items() if n > 1]
    if problems:
        raise BenchError(f"{path}:\n" + "\n".join(f"  {p}" for p in problems))
    return cases


class Repositories:
    """The cases' repositories, made once each in ``scratch``: a copy of the fixture (or an
    empty directory), made a Git repository when the case asks for one."""

    def __init__(self, tasks_dir: Path, scratch: Path) -> None:
        self.tasks_dir, self.scratch = tasks_dir, scratch
        self._made: dict[tuple[str, bool], Path] = {}
        self._profiles: dict[Path, RepoProfile] = {}

    def path(self, case: TriageCase) -> Path | None:
        if case.repo is None:
            return None
        key = (case.repo, case.git)
        if key not in self._made:
            name = "empty" if case.repo == EMPTY_REPO else case.repo
            target = self.scratch / f"{name}{'-git' if case.git else ''}"
            if case.repo == EMPTY_REPO:
                target.mkdir(parents=True)
            else:
                shutil.copytree(self.tasks_dir / case.repo / "fixture", target)
            if case.git:
                _init_git(target)
            self._made[key] = target
        return self._made[key]

    def signals(self, case: TriageCase) -> Signals:
        """The case's signals; each repository is profiled once."""

        root = self.path(case)
        if root is None:
            return collect(case.request)
        if root not in self._profiles:
            self._profiles[root] = analyze_repo(root)
        return collect(case.request, root, self._profiles[root])


def _init_git(root: Path) -> None:
    with standalone_git(root) as git:
        git.init()


def signal_problems(case: TriageCase, signals: Signals) -> list[str]:
    """How ``signals`` differ from what ``case`` expects (empty: they match)."""

    want = case.signals
    has_repo = case.repo is not None
    expected: dict[str, Any] = {
        "git_repo": want.git_repo if want.git_repo is not None else has_repo and case.git,
        "existing_code": (
            want.existing_code
            if want.existing_code is not None
            else case.repo not in (None, EMPTY_REPO)
        ),
        "stack_trace": want.stack_trace,
        "failing_tests": want.failing_tests,
        "refs": sorted(want.refs),
        "question": want.question,
        "paths": sorted(want.paths),
        "keywords": sorted(want.keywords),
    }
    actual: dict[str, Any] = {
        "git_repo": signals.git_repo,
        "existing_code": signals.existing_code,
        "stack_trace": signals.stack_trace,
        "failing_tests": signals.failing_tests,
        "refs": sorted(signals.refs),
        "question": signals.question,
        "paths": signals.paths,
        "keywords": sorted(signals.keywords),
    }
    return [
        f"{case.id}: {name} is {actual[name]!r}, expected {value!r}"
        for name, value in expected.items()
        if actual[name] != value
    ]


def outcome(case: TriageCase, profile: TaskProfile, **extra: Any) -> CaseOutcome:
    return CaseOutcome(
        id=case.id,
        expected=list(case.expect.intents),
        predicted=list(profile.intents),
        size=(case.expect.size, profile.size),
        target=(case.expect.target, profile.target.kind),
        asked=bool(profile.questions) and profile.needs_clarification,
        expect_clarify=case.expect.clarify,
        source=profile.source,
        **extra,
    )


def score(outcomes: list[CaseOutcome]) -> dict[str, Any]:
    """Accuracy figures and the confusion matrix (expected primary -> predicted -> count)."""

    total = len(outcomes) or 1
    secondary = [(o, i) for o in outcomes for i in o.expected[1:]]
    clarify = [o for o in outcomes if o.expect_clarify is not None]
    matrix: dict[str, dict[str, int]] = {}
    for o in outcomes:
        row = matrix.setdefault(o.expected[0], {})
        row[o.predicted[0]] = row.get(o.predicted[0], 0) + 1
    return {
        "cases": len(outcomes),
        "primary_accuracy": round(sum(o.primary_ok for o in outcomes) / total, 4),
        "size_accuracy": round(sum(o.size[0] == o.size[1] for o in outcomes) / total, 4),
        "target_accuracy": round(sum(o.target[0] == o.target[1] for o in outcomes) / total, 4),
        "secondary_recall": (
            round(sum(i in o.predicted for o, i in secondary) / len(secondary), 4)
            if secondary
            else None
        ),
        "clarify_agreement": (
            round(sum(o.asked == o.expect_clarify for o in clarify) / len(clarify), 4)
            if clarify
            else None
        ),
        "fallbacks": sum(o.source == "signals" for o in outcomes),
        "confusion": {k: dict(sorted(v.items())) for k, v in sorted(matrix.items())},
    }


def render_markdown(title: str, scores: dict[str, Any], outcomes: list[CaseOutcome]) -> str:
    """A short report: the figures, the confusion matrix, and every miss."""

    def pct(value: float | None) -> str:
        return "n/a" if value is None else f"{value * 100:.1f} %"

    lines = [
        f"# {title}",
        "",
        f"- Cases: {scores['cases']}",
        f"- Primary intent: {pct(scores['primary_accuracy'])}",
        f"- Size: {pct(scores['size_accuracy'])}",
        f"- Target (new/existing): {pct(scores['target_accuracy'])}",
        f"- Secondary intents found: {pct(scores['secondary_recall'])}",
        f"- Asked when expected / not when not: {pct(scores['clarify_agreement'])}",
        f"- Signals-only fallbacks: {scores['fallbacks']}",
        "",
        "## Confusion matrix (rows: expected primary intent, columns: predicted)",
        "",
    ]
    labels = sorted(
        {*scores["confusion"], *(p for row in scores["confusion"].values() for p in row)}
    )
    lines.append("| expected | " + " | ".join(labels) + " |")
    lines.append("|---|" + "---|" * len(labels))
    for row in sorted(scores["confusion"]):
        cells = [str(scores["confusion"][row].get(label, "")) for label in labels]
        lines.append(f"| {row} | " + " | ".join(cells) + " |")
    misses = [o for o in outcomes if not o.primary_ok or o.size[0] != o.size[1]]
    if misses:
        lines += ["", "## Misses", "", "| case | expected | predicted | size |"]
        lines.append("|---|---|---|---|")
        for o in misses:
            lines.append(
                f"| {o.id} | {', '.join(o.expected)} | {', '.join(o.predicted)} | "
                f"{o.size[0]} → {o.size[1]} |"
            )
    return "\n".join(lines) + "\n"
