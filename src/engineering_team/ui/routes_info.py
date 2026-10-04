"""Configuration, the machine check, the team, recipes, and inspecting a project directory."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from engineering_team.cli.doctor import run_checks
from engineering_team.cli.recipes_command import recipe_doc
from engineering_team.cli.team_commands import describe
from engineering_team.modes.repo_analyzer import analyze_repo
from engineering_team.pipeline.recipe_list import list_recipes
from engineering_team.team import TeamError, build_roster
from engineering_team.ui import missing_dependencies
from engineering_team.ui.state import state_of

router = APIRouter()


@router.get("/config")
def config(request: Request) -> dict[str, Any]:
    """Every effective setting and where it came from. Secrets and URL credentials are masked
    by the same code `config show` uses."""

    settings = state_of(request).settings
    return {
        "settings": [asdict(row) for row in settings.describe()],
        "notes": [*settings.sdk_problems(), *settings.missing_credentials()],
    }


@router.get("/doctor")
def doctor(request: Request) -> dict[str, Any]:
    checks = run_checks(state_of(request).settings)
    return {
        "ok": not any(check.status == "fail" for check in checks),
        "checks": [asdict(check) for check in checks],
        "ui_missing": missing_dependencies(),
    }


def _roster(request: Request):  # noqa: ANN202
    settings = state_of(request).settings
    try:
        return settings, build_roster(settings)
    except TeamError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/team")
def team(request: Request) -> list[dict[str, Any]]:
    settings, roster = _roster(request)
    return [describe(settings, member) for member in roster.all()]


def _directory(raw: str) -> Path:
    if not raw or "\0" in raw:
        raise HTTPException(422, "Give 'path': an absolute directory.")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise HTTPException(422, f"{raw!r} is not an absolute path.")
    if not path.is_dir():
        raise HTTPException(422, f"{raw!r} is not an existing directory.")
    return path.resolve()


@router.get("/recipes")
def recipes(request: Request, repo: str | None = None) -> list[dict[str, Any]]:
    """Bundled and user recipes; ``repo`` adds that project's own."""

    _, roster = _roster(request)
    root = _directory(repo) if repo else Path.cwd()
    return [recipe_doc(info) for info in list_recipes(root, teammates=set(roster.members))]


@router.get("/repo/inspect")
def inspect(path: str) -> dict[str, Any]:
    """Whether ``path`` is a project the team can work on, what stack it is, and its Git state."""

    root = _directory(path)
    profile = analyze_repo(root)
    return {"path": str(root), "profile": profile.model_dump(mode="json")}
