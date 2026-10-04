"""``maintain --task upgrade-deps``, ``docs``, ``security-audit`` and ``custom``."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from maintain_helpers import check, event_types, git, latest, maintain, project, scripted, state_of
from pipeline_fakes import FakeRunner
from test_baseline import interpreter_on_path  # noqa: F401  (the checks must find pytest)

from engineering_team.contracts import Finding, ReviewReport
from engineering_team.devtools.models import AuditReport, Vulnerability
from engineering_team.modes import maintain as maintain_actions
from engineering_team.modes import upgrade as upgrade_module
from engineering_team.modes.change_report import SUMMARY_FILE
from engineering_team.modes.maintain_contracts import Upgrade, UpgradePlan
from engineering_team.pipeline.stages import StageRequest
from engineering_team.tools.support import ToolError

pytestmark = pytest.mark.git

PINS = "alpha==1\nbeta==1\ngamma==1\ndelta==1\n"
# The project's own test: it fails once gamma is moved to 2 (a breaking release).
PIN_TEST = (
    "from pathlib import Path\n\n\ndef test_gamma_two_breaks_us():\n"
    "    assert 'gamma==2' not in Path('requirements.txt').read_text()\n"
)
PLAN = UpgradePlan(
    upgrades=[
        Upgrade(package=name, manifest="requirements.txt", current="1", target="2", reason="newer")
        for name in ("alpha", "beta", "gamma", "delta")
    ],
    skipped=["epsilon: pinned on purpose"],
)


@pytest.fixture(autouse=True)
def no_real_installs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Installing packages is the one thing these tests must never do."""

    monkeypatch.setattr(upgrade_module, "_install", lambda _ctx: "")


def apply_upgrades(request: StageRequest) -> None:
    """The upgrade agent: rewrite the pin of each package it was told to move."""

    path = request.ctx.workspace.root / "requirements.txt"
    text = path.read_text()
    for name in re.findall(r"^- (\w+): ", request.upgrades, flags=re.M):
        text = text.replace(f"{name}==1", f"{name}==2")
    path.write_text(text)


def deps_project(tmp_path: Path) -> Path:
    return project(tmp_path, {"requirements.txt": PINS, "tests/test_pins.py": PIN_TEST})


def test_upgrades_that_pass_stay_and_the_one_that_breaks_the_tests_is_found_and_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = deps_project(tmp_path)
    fake = FakeRunner(upgrade_plan=PLAN, on_call=scripted(upgrade=apply_upgrades))

    result, fake = maintain(repo, monkeypatch, "upgrade-deps", fake=fake)

    assert result.exit_code == 0, result.output
    ref = latest()
    assert ref.manifest.recipe == "upgrade-deps" and ref.manifest.verdict == "verified"
    assert [s.name for s in ref.manifest.stages] == [
        "profile", "baseline", "plan_upgrades", "upgrade", "verify", "summary",
    ]  # fmt: skip
    assert (repo / "requirements.txt").read_text() == "alpha==2\nbeta==2\ngamma==1\ndelta==2\n"
    state = state_of(ref)
    by_package = {o.upgrade.package: o for o in state.upgrade_outcomes}
    assert {p: o.status for p, o in by_package.items()} == {
        "alpha": "upgraded", "beta": "upgraded", "gamma": "failed", "delta": "upgraded",
    }  # fmt: skip
    assert "tests" in by_package["gamma"].reason and "test_gamma" in by_package["gamma"].reason
    assert check(state, "policy:manifests_only").status == "passed"
    # The groups the bisect tried, in order: all four, then halves, then gamma alone.
    groups = [
        re.findall(r"^- (\w+): ", r.upgrades, flags=re.M)
        for r in fake.requests
        if r.stage.name == "upgrade"
    ]
    assert groups == [
        ["alpha", "beta", "gamma", "delta"], ["alpha", "beta"], ["gamma", "delta"], ["gamma"],
        ["delta"],
    ]  # fmt: skip
    subjects = git(repo, "log", "--format=%s").splitlines()
    assert "upgrade: alpha 1 -> 2, beta 1 -> 2" in subjects and "upgrade: delta 1 -> 2" in subjects
    summary = (ref.run_dir / SUMMARY_FILE).read_text()
    assert "## Dependencies" in summary and "3 upgraded, 1 could not be upgraded" in summary
    assert "| gamma | 1 -> 2 |" in summary and "epsilon: pinned on purpose" in summary
    assert "upgrade.undone" in event_types(ref)


def test_a_group_size_of_one_goes_one_by_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = deps_project(tmp_path)
    config = tmp_path / "config.toml"
    config.write_text("[maintain]\nupgrade_group_size = 1\n")
    fake = FakeRunner(upgrade_plan=PLAN, on_call=scripted(upgrade=apply_upgrades))

    result, fake = maintain(repo, monkeypatch, "upgrade-deps", "--config", str(config), fake=fake)

    assert result.exit_code == 0, result.output
    groups = [r.upgrades for r in fake.requests if r.stage.name == "upgrade"]
    assert len(groups) == 4 and all(g.count("\n") == 0 for g in groups)


def test_an_upgrade_that_touches_more_than_manifests_is_undone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = deps_project(tmp_path)
    plan = UpgradePlan(upgrades=PLAN.upgrades[:1])

    def sneaky(request: StageRequest) -> None:
        apply_upgrades(request)
        request.ctx.workspace.write_file("app/store.py", "def add(items, text):\n    return []\n")

    fake = FakeRunner(upgrade_plan=plan, on_call=scripted(upgrade=sneaky))

    result, _ = maintain(repo, monkeypatch, "upgrade-deps", fake=fake)

    assert result.exit_code == 0, result.output
    (outcome,) = state_of(latest()).upgrade_outcomes
    assert (
        outcome.status == "failed" and "not manifests or lockfiles: app/store.py" in outcome.reason
    )
    assert (repo / "requirements.txt").read_text() == PINS
    assert (repo / "app" / "store.py").read_text().endswith("[*items, text]\n")


def test_an_install_that_fails_is_the_reason_the_upgrade_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = deps_project(tmp_path)

    def install(ctx: object) -> str:
        text = (repo / "requirements.txt").read_text()
        return (
            "installing in . failed (pip): no matching distribution for beta==2"
            if "beta==2" in text
            else ""
        )

    monkeypatch.setattr(upgrade_module, "_install", install)
    fake = FakeRunner(upgrade_plan=PLAN, on_call=scripted(upgrade=apply_upgrades))

    result, _ = maintain(repo, monkeypatch, "upgrade-deps", fake=fake)

    assert result.exit_code == 0, result.output
    outcomes = {o.upgrade.package: o for o in state_of(latest()).upgrade_outcomes}
    assert "no matching distribution for beta==2" in outcomes["beta"].reason
    assert outcomes["gamma"].status == "failed"  # still the breaking one
    assert outcomes["alpha"].status == "upgraded" and outcomes["delta"].status == "upgraded"


def test_an_empty_plan_changes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = deps_project(tmp_path)

    result, fake = maintain(repo, monkeypatch, "upgrade-deps")

    assert result.exit_code == 0, result.output
    assert not any(r.stage.name == "upgrade" for r in fake.requests)
    assert "Nothing to upgrade" in state_of(latest()).summaries["upgrade"]


def test_the_upgrade_agent_may_write_manifests_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = deps_project(tmp_path)
    fake = FakeRunner(upgrade_plan=PLAN, on_call=scripted(upgrade=apply_upgrades))

    _, fake = maintain(repo, monkeypatch, "upgrade-deps", fake=fake)

    scope = next(r.write_scope for r in fake.requests if r.stage.name == "upgrade")
    assert scope is not None
    assert scope.permits("requirements.txt") and scope.permits("svc/package-lock.json")
    assert not scope.permits("app/store.py")


def test_upgrade_deps_refuses_to_start_without_the_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = deps_project(tmp_path)
    config = tmp_path / "config.toml"
    config.write_text("[tools.dev]\nallow_network = false\n")

    result, _ = maintain(repo, monkeypatch, "upgrade-deps", "--config", str(config))

    assert result.exit_code == 2 and "needs the network" in result.stderr


# -- docs -----------------------------------------------------------------------------------


def test_docs_may_write_documentation_and_nothing_else(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = project(tmp_path)

    def write_docs(request: StageRequest) -> None:
        request.ctx.workspace.write_file("docs/architecture.md", "# Architecture\n" + "text. " * 30)
        request.ctx.workspace.write_file("README.md", "# Notes\n\nNow with more words.\n")

    result, fake = maintain(
        repo, monkeypatch, "docs", fake=FakeRunner(on_call=scripted(docs=write_docs))
    )

    assert result.exit_code == 0, result.output
    ref = latest()
    assert ref.manifest.recipe == "docs"
    assert check(state_of(ref), "policy:docs_only").status == "passed"
    writer = next(r for r in fake.requests if r.stage.name == "docs")
    assert writer.teammate == "technical_writer" and writer.write_scope is not None
    assert writer.write_scope.permits("docs/architecture.md")
    assert not writer.write_scope.permits("app/store.py")
    assert (repo / "docs" / "architecture.md").is_file()


def test_docs_that_edit_code_fail_the_policy_and_exit_3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = project(tmp_path)

    def comment_the_code(request: StageRequest) -> None:
        request.ctx.workspace.write_file(
            "app/store.py", "# adds a note\ndef add(items, text):\n    return [*items, text]\n"
        )

    result, _ = maintain(
        repo, monkeypatch, "docs", fake=FakeRunner(on_call=scripted(docs=comment_the_code))
    )

    assert result.exit_code == 3, result.output
    assert check(state_of(latest()), "policy:docs_only").status == "failed"


# -- security audit -------------------------------------------------------------------------


def vulnerabilities() -> AuditReport:
    return AuditReport(
        status="failed",
        tool="pip-audit",
        vulnerabilities=[
            Vulnerability(package="requests", version="2.0", id="CVE-1", severity="critical",
                          fixed_in="2.32", title="request smuggling"),
            Vulnerability(package="flask", version="1.0", id="CVE-2", severity="moderate"),
        ],
    )  # fmt: skip


@pytest.fixture
def audit_results(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        maintain_actions.DevRunner, "audit", lambda self, directory: vulnerabilities()
    )


SQL = Finding(severity="high", summary="User input reaches a SQL string", file="app/db.py", line=9)
REVIEWS = {"security_engineer": ReviewReport(summary="One problem.", findings=[SQL])}


@pytest.mark.usefixtures("audit_results")
def test_an_audit_reports_findings_changes_nothing_and_fails_on_a_serious_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = project(tmp_path)
    base = git(repo, "rev-parse", "HEAD")

    result, fake = maintain(
        repo, monkeypatch, "security-audit", fake=FakeRunner(reviews=REVIEWS), before=("--json",)
    )

    assert result.exit_code == 3, result.output
    ref = latest()
    assert ref.manifest.recipe == "security-audit" and ref.manifest.verdict == "failed"
    ran = [s.name for s in ref.manifest.stages if s.status == "succeeded"]
    assert ran == ["profile", "map", "dependency_audit", "audit", "report"]
    assert git(repo, "diff", base, "--stat") == ""  # no fix was asked for
    assert not any(r.stage.name in ("fix", "baseline") for r in fake.requests)
    data = json.loads((ref.run_dir / "findings.json").read_text())
    assert data["kind"] == "security-audit" and data["passed"] is False
    assert data["fail_on"] == "high" and data["counts"]["critical"] == 1
    assert [f["id"] for f in data["findings"]] == ["D-1", "F-1", "D-2"]  # critical, high, medium
    assert data["findings"][0]["suggested_fix"] == "Upgrade requests to 2.32 or later."
    assert data["dependency_audit"][0]["tool"] == "pip-audit"
    markdown = (ref.run_dir / "findings.md").read_text()
    assert "**Result: FAILED.**" in markdown and "request smuggling" in markdown
    assert "## Dependency audit" in markdown
    reviewer = next(r for r in fake.requests if r.stage.name == "audit")
    assert reviewer.teammate == "security_engineer"
    from engineering_team.pipeline.stages import CrewStageRunner

    assert "CVE-1" in CrewStageRunner._inputs(reviewer)["audit"]


@pytest.mark.usefixtures("audit_results")
def test_with_fix_the_debugger_fixes_the_serious_findings_and_the_run_is_verified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = project(tmp_path)

    def fix(request: StageRequest) -> None:
        request.ctx.workspace.write_file("app/db.py", "def query(x):\n    return x\n")

    result, fake = maintain(
        repo, monkeypatch, "security-audit", "--fix",
        fake=FakeRunner(reviews=REVIEWS, on_call=scripted(fix=fix)),
    )  # fmt: skip

    assert result.exit_code == 0, result.output
    ref = latest()
    assert ref.manifest.verdict == "verified"
    assert [s.name for s in ref.manifest.stages if s.status == "succeeded"] == [
        "profile", "baseline", "map", "dependency_audit", "audit", "report", "fix", "verify",
        "summary",
    ]  # fmt: skip
    fixer = next(r for r in fake.requests if r.stage.name == "fix")
    assert fixer.teammate == "debugger"
    assert "F-1" in fixer.findings or "D-1" in fixer.findings
    assert (repo / "app" / "db.py").is_file()


@pytest.mark.usefixtures("audit_results")
def test_findings_below_the_threshold_pass_the_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = project(tmp_path)
    low = {
        "security_engineer": ReviewReport(findings=[Finding(severity="low", summary="Noisy log")])
    }
    monkeypatch.setattr(
        maintain_actions.DevRunner,
        "audit",
        lambda self, directory: AuditReport(status="passed", tool="pip-audit"),
    )

    result, _ = maintain(repo, monkeypatch, "security-audit", fake=FakeRunner(reviews=low))

    assert result.exit_code == 0, result.output
    data = json.loads((latest().run_dir / "findings.json").read_text())
    assert data["passed"] is True and len(data["findings"]) == 1


def test_an_audit_that_cannot_reach_the_network_says_so_in_its_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = project(tmp_path)

    def offline(self: object, directory: str) -> AuditReport:
        raise ToolError("Dependency Audit needs the network and tools.dev.allow_network is false.")

    monkeypatch.setattr(maintain_actions.DevRunner, "audit", offline)

    result, _ = maintain(repo, monkeypatch, "security-audit")

    assert result.exit_code == 0, result.output
    markdown = (latest().run_dir / "findings.md").read_text()
    assert "## Dependency audit" in markdown and "allow_network is false" in markdown


# -- custom ---------------------------------------------------------------------------------


def test_a_custom_task_needs_its_goal_and_then_runs_the_instructions_of_its_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = project(tmp_path)
    missing, _ = maintain(repo, monkeypatch, "custom")

    result, fake = maintain(repo, monkeypatch, "custom", "--goal", "Rename add to append_note.")

    assert missing.exit_code == 2 and "needs the goal" in missing.stderr
    assert result.exit_code == 0, result.output
    work = next(r for r in fake.requests if r.stage.name == "work")
    assert work.teammate == "generalist_engineer"
    assert work.stage.instructions and "smallest change" in work.stage.instructions
    from engineering_team.pipeline.stages import CrewStageRunner

    inputs = CrewStageRunner._inputs(work)
    assert inputs["instructions"].startswith("Carry out the goal")
    assert "Rename add to append_note." in inputs["requirements"]


def test_usage_errors_for_maintain_exit_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = project(tmp_path)

    unknown, _ = maintain(repo, monkeypatch, "polish")
    wrong_fix, _ = maintain(repo, monkeypatch, "docs", "--fix")
    none, _ = maintain(repo, monkeypatch, "docs", "--goal", "x", "--goal-file", "y")

    assert unknown.exit_code == 2 and "Unknown task 'polish'" in unknown.stderr
    assert "add-tests, refactor, upgrade-deps, docs, security-audit, custom" in unknown.stderr
    assert wrong_fix.exit_code == 2 and "security-audit only" in wrong_fix.stderr
    assert none.exit_code == 2 and "only one of --goal and --goal-file" in none.stderr
