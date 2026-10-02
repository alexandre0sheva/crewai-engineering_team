from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_URL = "https://github.com/alexandre0sheva/crewai-engineering_team"


@pytest.fixture(scope="module")
def pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_package_metadata_is_complete(pyproject: dict) -> None:
    project = pyproject["project"]

    for key in ("name", "version", "description", "readme", "requires-python", "authors"):
        assert project[key], key
    assert project["license"] == "MIT"
    assert project["license-files"] == ["LICENSE"]
    assert project["classifiers"]
    assert project["keywords"]
    assert set(project["urls"]) >= {"Homepage", "Repository", "Issues", "Changelog"}
    assert all(url.startswith(REPOSITORY_URL) for url in project["urls"].values())


def test_license_and_community_files_exist() -> None:
    for name in (
        "LICENSE",
        "CHANGELOG.md",
        "CONTRIBUTING.md",
        "SECURITY.md",
        "CODE_OF_CONDUCT.md",
        ".github/pull_request_template.md",
        ".github/dependabot.yml",
        ".github/ISSUE_TEMPLATE/bug.yml",
        ".github/ISSUE_TEMPLATE/feature.yml",
    ):
        assert (ROOT / name).is_file(), name
    assert "MIT License" in (ROOT / "LICENSE").read_text(encoding="utf-8")


def test_ci_workflow_runs_the_full_gate() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    jobs = workflow["jobs"]
    matrix = jobs["test"]["strategy"]["matrix"]
    steps = " ".join(str(step.get("run", "")) for job in jobs.values() for step in job["steps"])

    assert matrix["python-version"] == ["3.11", "3.12", "3.13"]
    for command in (
        "uv sync --locked --group dev",
        "ruff check",
        "ruff format --check",
        "mypy",
        "pytest",
        "uv build",
        'bin/engineering-team" --help',
    ):
        assert command in steps, command


def test_dependabot_covers_uv_and_actions() -> None:
    config = yaml.safe_load((ROOT / ".github/dependabot.yml").read_text(encoding="utf-8"))

    assert {update["package-ecosystem"] for update in config["updates"]} == {
        "uv",
        "github-actions",
    }
