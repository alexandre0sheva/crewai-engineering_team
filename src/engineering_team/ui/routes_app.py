"""What the app shell needs besides runs: the start form's choices, and a finished run's results."""

from __future__ import annotations

import shlex
import shutil
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.encoders import jsonable_encoder

from engineering_team.intake.templates import MODES as TEMPLATE_MODES
from engineering_team.intake.templates import template_for
from engineering_team.model_routing import PROFILE_NAMES, PROVIDERS
from engineering_team.modes.isolation import Isolation, read_isolation
from engineering_team.pipeline.strategies import STRATEGY_NAMES
from engineering_team.pricing import TOKENS_PER_PRICE_UNIT
from engineering_team.report.collect import build_report
from engineering_team.runtime.run_store import RunNotFound
from engineering_team.settings import SEARCH_PROVIDERS, SettingsError
from engineering_team.tools.browser_tools import available as browser_available
from engineering_team.ui.launcher import REPO_MODES
from engineering_team.ui.state import state_of

router = APIRouter()

MODE_LABELS = {
    "new": "Build new",
    "feature": "Add feature",
    "fix": "Fix bug",
    "maintain": "Maintain",
    "review": "Review",
}
TEAM_PROFILES = ("full", "minimal")
SANDBOXES = ("local", "docker")
TIERS = ("lead", "worker")


@router.get("/options")
def options(
    request: Request, provider: str | None = None, profile: str | None = None
) -> dict[str, Any]:
    """The start form's choices and defaults, the models ``provider``/``profile`` would use (with
    their prices, for the cost hint), and which optional tools this machine has."""

    state = state_of(request)
    settings = state.settings
    if provider and provider not in PROVIDERS:
        raise HTTPException(422, f"provider must be one of: {', '.join(PROVIDERS)}.")
    if profile and profile not in PROFILE_NAMES:
        raise HTTPException(422, f"profile must be one of: {', '.join(PROFILE_NAMES)}.")
    chosen = settings.with_overrides(
        {"provider": provider or settings.provider, "profile": profile or settings.profile},
        source="ui",
    )
    return {
        "modes": [
            {"key": key, "label": label, "needs_repo": key in REPO_MODES}
            for key, label in MODE_LABELS.items()
        ],
        "templates": {mode: template_for(mode) for mode in TEMPLATE_MODES},
        "demo": {"repo": state.demo_repo} if state.demo_repo else None,
        "providers": list(PROVIDERS),
        "profiles": list(PROFILE_NAMES),
        "strategies": list(STRATEGY_NAMES),
        "sandboxes": list(SANDBOXES),
        "team_profiles": list(TEAM_PROFILES),
        "defaults": {
            "provider": settings.provider,
            "profile": settings.profile,
            "strategy": settings.strategy,
            "sandbox": settings.execution.backend,
            "team_profile": settings.team_profile,
            "max_parallel_agents": settings.parallel.max_parallel_agents,
            "budget": settings.budget.model_dump(exclude={"max_repair_rounds"}),
        },
        "models": _models(chosen),
        "tools": {
            "web": {
                "enabled": settings.web.enabled,
                "search_providers": [p for p in SEARCH_PROVIDERS if settings.web_api_key(p)],
            },
            "browser": {"installed": browser_available()},
            "docker": {"installed": shutil.which("docker") is not None},
        },
        "limits": {
            "max_upload_bytes": settings.ui.max_upload_bytes,
            "max_request_chars": 200_000,
        },
    }


def _models(settings: Any) -> list[dict[str, Any]]:
    """The model of each slot under ``settings`` and what a million tokens of it cost."""

    table = settings.price_table()
    found: list[dict[str, Any]] = []
    for tier in TIERS:
        try:
            resolved = settings.resolve_model(tier, tier=tier)
        except SettingsError as exc:
            found.append({"slot": tier, "model": None, "error": str(exc)})
            continue
        price = table.lookup(resolved.model)
        found.append(
            {
                "slot": tier,
                "model": resolved.model,
                "input_per_million": price.input if price else None,
                "output_per_million": price.output if price else None,
                "tokens_per_price_unit": TOKENS_PER_PRICE_UNIT,
            }
        )
    return found


@router.get("/runs/{run_id}/results")
def results(request: Request, run_id: str) -> dict[str, Any]:
    """What the report shows, as JSON: banner, summaries, criteria coverage, checks, findings,
    changed files with their diffs, screenshots, usage and warnings."""

    ref = state_of(request).need(run_id)
    try:
        report = build_report(ref.run_dir)
    except RunNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    data: dict[str, Any] = jsonable_encoder(report)
    isolation = read_isolation(ref.workspace)
    data["merge"] = merge_guide(run_id, isolation) if isolation else None
    return data


def merge_guide(run_id: str, isolation: Isolation) -> dict[str, Any]:
    """Where the team's work is and the commands to take it (the controller never merges or
    pushes: this is for the person to run)."""

    quote = shlex.quote
    steps: list[dict[str, Any]] = [
        {
            "title": "Look at the change",
            "commands": [f"engineering-team diff {run_id}"],
        }
    ]
    if isolation.branch:
        steps.append(
            {
                "title": f"Merge the branch into {isolation.base_branch or 'your branch'}",
                "commands": [
                    f"cd {quote(str(isolation.source))}",
                    f"git switch {quote(isolation.base_branch or 'main')}",
                    f"git merge {quote(isolation.branch)}",
                ],
            }
        )
    steps.append(
        {
            "title": "Or take it as a patch" if isolation.branch else "Take the change as a patch",
            "commands": [
                f"engineering-team export-patch {run_id} --out change.patch",
                "git apply change.patch",
            ],
        }
    )
    if isolation.mode == "worktree" and isolation.branch:
        steps.append(
            {
                "title": "Clean up afterwards",
                "commands": [
                    f"git worktree remove {quote(str(isolation.workspace))}",
                    f"git branch -d {quote(isolation.branch)}",
                ],
            }
        )
    return {**isolation.to_json(), "steps": steps}
