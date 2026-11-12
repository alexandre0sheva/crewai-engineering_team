"""Static checks of the GitHub Action (``action.yml``) and of the workflows that use it.

Nothing here runs a workflow: the files are parsed and linted against the rules that matter for
this project (no secrets in files, no untrusted text spliced into a script, versions pinned, the
permissions each workflow needs and no more, and every CLI flag a workflow passes exists).
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from engineering_team.cli.app import app

ROOT = Path(__file__).resolve().parent.parent
ACTION = ROOT / "action.yml"
OWN_WORKFLOW = ROOT / ".github" / "workflows" / "review.yml"
EXAMPLES = sorted((ROOT / "examples" / "github-actions").glob("*.yml"))
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml")) + EXAMPLES

EXPRESSION = re.compile(r"\$\{\{\s*(.*?)\s*\}\}")
PINNED = re.compile(r"^[\w./-]+@(v\d[\w.]*|[0-9a-f]{40})$")  # a version tag or a full commit SHA
# PyPA documents its publishing action's moving ``release/v1`` branch as the way to use it (trusted
# publishing, release.yml); every other action is pinned.
PINNED_BY_THE_VENDOR = {"pypa/gh-action-pypi-publish@release/v1"}
SECRET_LOOKING = re.compile(r"sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}")
# Text a stranger chooses: spliced into a shell script it becomes code (script injection).
UNTRUSTED = re.compile(
    r"github\.(head_ref|event\.(issue|pull_request|comment|review|pages|commits|head_commit|"
    r"discussion)\.(title|body|head\.ref|head\.label|message)|event\.(issue|pull_request)\.user)"
    r"|inputs\."
)


def load(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"{path.name} is not a mapping"
    if True in data:  # YAML 1.1 reads the key ``on`` as a boolean
        data["on"] = data.pop(True)
    return data


def steps_of(document: dict[str, Any]) -> list[dict[str, Any]]:
    if "runs" in document:
        return list(document["runs"]["steps"])
    return [step for job in document["jobs"].values() for step in job["steps"]]


def help_flags(*command: str) -> set[str]:
    result = CliRunner().invoke(app, [*command, "--help"], terminal_width=200)
    assert result.exit_code == 0, result.output
    return set(re.findall(r"--[a-z][a-z0-9-]*", result.output))


def flags_in(script: str) -> set[str]:
    return set(re.findall(r"(?<![\w-])--[a-z][a-z0-9-]*", script))


# -- action.yml ---------------------------------------------------------------------------


def test_the_action_is_a_composite_action_with_documented_inputs_and_outputs() -> None:
    action = load(ACTION)

    assert action["runs"]["using"] == "composite"
    assert action["name"] and action["description"]
    for name, spec in {**action["inputs"], **action["outputs"]}.items():
        assert spec.get("description"), f"{name} has no description"
    for name in ("provider", "profile", "max-cost-usd", "fail-on"):  # the task's inputs
        assert name in action["inputs"]
    assert not any(spec.get("required") for spec in action["inputs"].values())  # all have defaults
    assert all("default" in spec for spec in action["inputs"].values())
    assert action["inputs"]["fail-on"]["default"] in {"low", "medium", "high", "critical"}
    assert action["inputs"]["fake"]["default"] == "false"  # a real review unless asked
    assert action["inputs"]["max-cost-usd"]["default"]  # a budget is always set


def test_every_expression_in_the_action_refers_to_something_that_exists() -> None:
    action = load(ACTION)
    ids = {step["id"] for step in action["runs"]["steps"] if "id" in step}
    text = ACTION.read_text(encoding="utf-8")

    for expression in EXPRESSION.findall(text):
        for kind, name in re.findall(r"\b(inputs|steps)\.([\w-]+)", expression):
            known = action["inputs"] if kind == "inputs" else ids
            assert name in known, f"${{{{ {expression} }}}} refers to an unknown {kind[:-1]}"
    for name, spec in action["outputs"].items():
        assert "steps.review.outputs." in spec["value"], name
        assert "review" in ids


def test_the_action_splices_no_untrusted_text_into_a_script() -> None:
    for step in load(ACTION)["runs"]["steps"]:
        script = step.get("run", "")
        assert "${{" not in script, (
            f"step {step.get('name')!r}: pass values through env, never ${{{{ }}}} in run"
        )
        if "run" in step:
            assert step.get("shell") == "bash", step.get("name")


def test_the_action_pins_every_action_it_uses_and_holds_no_secret() -> None:
    text = ACTION.read_text(encoding="utf-8")

    assert not SECRET_LOOKING.search(text)
    uses = [step["uses"] for step in load(ACTION)["runs"]["steps"] if "uses" in step]
    assert uses and all(PINNED.match(u) for u in uses), uses


def test_the_review_step_passes_only_flags_that_review_has() -> None:
    action = load(ACTION)
    review = next(s for s in action["runs"]["steps"] if s.get("id") == "review")
    script = review["run"]
    available = help_flags("review")
    global_flags = help_flags()

    used = flags_in(script)
    assert {"--format", "--out-dir", "--fail-on", "--base", "--max-cost-usd", "--fake"} <= used
    assert used - {"--workspace-root"} <= available | {"--locked", "--no-dev", "--project"}, (
        used - available
    )
    assert "--workspace-root" in global_flags
    assert "sarif" in script  # the findings go to code scanning as SARIF


def test_the_job_fails_with_the_reviews_exit_code_after_the_findings_are_published() -> None:
    steps = load(ACTION)["runs"]["steps"]
    names = [s["name"] for s in steps]

    assert names.index("Review the change") < names.index("Comment on the pull request")
    assert names.index("Comment on the pull request") < names.index(
        "Upload findings to code scanning"
    )
    last = steps[-1]
    assert last["name"] == "Fail when the review did" and "always()" in last["if"]
    assert 'exit "$code"' in last["run"]
    review = next(s for s in steps if s.get("id") == "review")
    assert review["run"].rstrip().endswith("exit 0")  # the review step itself never stops the rest
    for step in steps:
        if "upload" in step.get("name", "").lower():
            assert step.get("continue-on-error") is True  # a missing permission must not hide it


def test_the_comment_step_updates_one_comment_of_its_own() -> None:
    comment = next(s for s in load(ACTION)["runs"]["steps"] if s["name"].startswith("Comment"))

    assert "<!-- engineering-team:review -->" in comment["run"]  # found again by its marker
    assert "PATCH" in comment["run"] and "gh pr comment" in comment["run"]
    assert comment["env"]["GH_TOKEN"] == "${{ inputs.github-token }}"
    assert "github.event_name == 'pull_request'" in comment["if"]


# -- every workflow -----------------------------------------------------------------------


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_a_workflow_has_a_trigger_least_privilege_permissions_and_runners(path: Path) -> None:
    workflow = load(path)

    assert workflow["name"] and workflow["on"]
    permissions = workflow["permissions"]  # always explicit: the default token is too broad
    assert isinstance(permissions, dict) and "write-all" not in str(permissions)
    for job in workflow["jobs"].values():
        assert job["runs-on"]


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_a_workflow_pins_its_actions_and_keeps_secrets_out_of_the_file(path: Path) -> None:
    text = path.read_text(encoding="utf-8")

    assert not SECRET_LOOKING.search(text)
    for step in steps_of(load(path)):
        uses = step.get("uses")
        if uses and not uses.startswith("./"):
            assert PINNED.match(uses) or uses in PINNED_BY_THE_VENDOR, (
                f"{path.name}: {uses} is not pinned to a version"
            )
    for expression in EXPRESSION.findall(text):
        if "secrets." in expression:
            assert re.fullmatch(r"secrets\.[A-Z][A-Z0-9_]*", expression), expression
    # A secret is only ever read through ``secrets.`` into an ``env`` entry or an input.
    for step in steps_of(load(path)):
        assert "secrets." not in step.get("run", ""), f"{path.name}: a secret inside a script"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_a_workflow_splices_no_attacker_chosen_text_into_a_script(path: Path) -> None:
    for step in steps_of(load(path)):
        for expression in EXPRESSION.findall(step.get("run", "")):
            assert not UNTRUSTED.search(expression), (
                f"{path.name}: ${{{{ {expression} }}}} in a run script (script injection); "
                "pass it through env"
            )


# -- this repository's own workflow and the examples --------------------------------------


def test_this_repository_reviews_its_own_pull_requests_offline() -> None:
    workflow = load(OWN_WORKFLOW)
    job = workflow["jobs"]["review"]
    uses = [s for s in job["steps"] if s.get("uses") == "./"]

    assert "pull_request" in workflow["on"]
    assert workflow["permissions"] == {"contents": "read"}  # fake mode posts nothing
    assert len(uses) == 1 and uses[0]["with"]["fake"] == "true"
    assert uses[0]["with"]["comment"] == "false" and uses[0]["with"]["upload-sarif"] == "false"
    checkout = next(s for s in job["steps"] if s["uses"].startswith("actions/checkout"))
    assert checkout["with"]["fetch-depth"] == 0  # the base's history, for the merge base
    assert not job.get("env") and "secrets" not in OWN_WORKFLOW.read_text()


def test_the_review_example_uses_the_action_with_the_permissions_it_needs() -> None:
    path = ROOT / "examples" / "github-actions" / "review.yml"
    workflow = load(path)
    action = load(ACTION)
    (step,) = [
        s
        for s in steps_of(workflow)
        if s.get("uses", "").startswith("alexandre0sheva/crewai-engineering_team@")
    ]

    assert workflow["permissions"] == {
        "contents": "read",
        "pull-requests": "write",
        "security-events": "write",
    }
    assert set(step["with"]) <= set(action["inputs"])  # no input the action does not know
    assert step["env"]["OPENAI_API_KEY"] == "${{ secrets.OPENAI_API_KEY }}"
    assert "max-cost-usd" in step["with"] and "fail-on" in step["with"]  # budget and threshold
    assert "pull_request" in workflow["on"]
    forks = workflow["jobs"]["review"]["if"]
    assert "head.repo.full_name == github.repository" in forks  # no secrets for fork runs


def test_the_fix_example_opens_a_draft_pull_request_on_a_label_in_the_sandbox() -> None:
    path = ROOT / "examples" / "github-actions" / "fix-on-label.yml"
    workflow = load(path)
    job = workflow["jobs"]["fix"]
    step = next(s for s in job["steps"] if "run" in s and "engineering-team" in s["run"])
    script = step["run"]

    assert workflow["on"] == {"issues": {"types": ["labeled"]}}
    assert job["if"] == "github.event.label.name == 'engineering-team:fix'"
    assert workflow["permissions"] == {
        "contents": "write",
        "pull-requests": "write",
        "issues": "read",
    }
    tokens = shlex.split(script.replace("\\\n", " "))
    assert {"do", "--mode", "fix", "--issue", "--open-pr", "--yes"} <= set(tokens)
    assert tokens[tokens.index("--sandbox") + 1] == "docker"
    assert tokens[tokens.index("--issue") + 1] == "$ISSUE"  # through env, not spliced
    assert step["env"]["ISSUE"] == "${{ github.repository }}#${{ github.event.issue.number }}"
    assert step["env"]["OPENAI_API_KEY"] == "${{ secrets.OPENAI_API_KEY }}"
    assert float(step["env"]["ENGINEERING_BUDGET_MAX_COST_USD"]) > 0  # a budget is always set
    assert flags_in(script) - {"--from", "--workspace-root"} <= help_flags("do")
    assert tokens[tokens.index("--mode") + 1] in {"fix", "feature"}
    assert "--workspace-root" in help_flags()


def test_the_documentation_names_the_action_inputs_and_permissions() -> None:
    usage = (ROOT / "docs" / "USAGE.md").read_text(encoding="utf-8")
    safety = (ROOT / "docs" / "SAFETY.md").read_text(encoding="utf-8")

    for name in load(ACTION)["inputs"]:
        assert f"`{name}`" in usage, f"docs/USAGE.md does not describe the input {name}"
    for permission in ("security-events: write", "pull-requests: write", "contents: write"):
        assert permission in safety, f"docs/SAFETY.md does not name {permission}"
