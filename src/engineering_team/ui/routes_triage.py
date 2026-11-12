"""``POST /triage``: what ``do`` would make of a request, before anything starts."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from engineering_team.cli import do_plan
from engineering_team.harness import HarnessError
from engineering_team.model_routing import PROFILE_NAMES, PROVIDERS
from engineering_team.run_prep import _cli_overrides
from engineering_team.settings import SettingsError
from engineering_team.ui import triage_view
from engineering_team.ui.state import state_of

router = APIRouter()


class TriageBody(BaseModel):
    """What the page knows when it asks: the request and, when there is one, the project, plus
    the choices that change the plan (each is what ``do`` has an option for)."""

    model_config = ConfigDict(extra="forbid")

    request: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200_000)
    ]
    repo: str | None = None
    intent: str | None = None  # force the kind of work (``do --mode``)
    strategy: Literal["auto", "pipeline", "single", "team"] | None = None
    attempts: int | None = Field(default=None, ge=1, le=8)
    provider: str | None = None
    profile: str | None = None
    refine: bool = False  # also ask the model's cheap triage (a few cents at most; never in demo)


@router.post("/triage")
async def triage(request: Request, body: TriageBody) -> dict[str, Any]:
    """The plan ``do`` would show: understood intent, size and risk, the starting rung and how it
    may climb, the stages with their reasons, the estimate. Nothing is started or changed."""

    state = state_of(request)
    if body.provider and body.provider not in PROVIDERS:
        raise HTTPException(422, f"provider must be one of: {', '.join(PROVIDERS)}.")
    if body.profile and body.profile not in PROFILE_NAMES:
        raise HTTPException(422, f"profile must be one of: {', '.join(PROFILE_NAMES)}.")
    use_model = body.refine and state.demo_repo is None
    return await run_in_threadpool(_plan, state.settings, body, use_model)


def _plan(settings: Any, body: TriageBody, use_model: bool) -> dict[str, Any]:
    try:
        root = triage_view.project_of(body.repo)
        chosen = settings.with_overrides(
            _cli_overrides(
                SimpleNamespace(
                    provider=body.provider,
                    profile=body.profile,
                    strategy=body.strategy,
                    attempts=body.attempts,
                )
            ),
            source="ui",
        ).for_request(body.request)
        plan = do_plan.plan_request(
            body.request, root, chosen, mode=body.intent, use_model=use_model
        )
    except (ValueError, SettingsError, HarnessError) as exc:
        raise HTTPException(422, str(exc)) from exc
    result = triage_view.view(plan)
    if body.refine and not use_model:
        result["notes"].append("Demo mode calls no model: the signals decided.")
    return result
