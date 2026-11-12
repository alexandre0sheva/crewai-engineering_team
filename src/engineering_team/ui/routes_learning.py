"""The learning screens' data: a run's harness and lessons, repository memory, the experience
library with approve and reject."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field

from engineering_team.learning.library import LibraryError
from engineering_team.ui import learning_views as views
from engineering_team.ui.routes_info import _directory
from engineering_team.ui.state import state_of

router = APIRouter()


class Target(BaseModel):
    """Which project (and library) a change is about."""

    model_config = ConfigDict(extra="forbid")

    repo: str | None = Field(default=None, max_length=4096)
    library: str | None = Field(default=None, pattern="^(user|repository)$")


def _root(raw: str | None) -> Path | None:
    return _directory(raw) if raw else None


@router.get("/runs/{run_id}/learning")
async def run_learning(request: Request, run_id: str) -> dict[str, Any]:
    """The harness and skills the run used, the lessons it was given, and its project's memory."""

    state = state_of(request)
    ref = state.need(run_id)
    return await run_in_threadpool(views.run_learning, ref, state.settings)


@router.get("/memory")
async def memory(request: Request, repo: str) -> dict[str, Any]:
    """The remembered facts of the project at ``repo`` (stale ones marked first)."""

    return await run_in_threadpool(views.memory_view, _directory(repo), state_of(request).settings)


@router.post("/memory/{fact_id}/forget")
async def forget_fact(request: Request, fact_id: str, body: Target) -> dict[str, Any]:
    root = _root(body.repo)
    if root is None:
        raise HTTPException(422, "Give 'repo': the project the fact belongs to.")
    store = views.memory_store(root, state_of(request).settings)
    fact = await run_in_threadpool(store.get, fact_id)
    if fact is None:
        raise HTTPException(404, f"No fact {fact_id!r} in {root}.")
    await run_in_threadpool(store.remove, fact.id, reason="forgotten in the web UI")
    return {"forgotten": fact.id}


@router.get("/lessons")
async def lessons(request: Request, repo: str | None = None) -> dict[str, Any]:
    """The experience library: the user's, plus the project's when ``repo`` is given."""

    return await run_in_threadpool(views.lessons_view, _root(repo), state_of(request).settings)


def _found(request: Request, lesson_id: str, body: Target) -> Any:
    settings = state_of(request).settings
    library = views.find_library(_root(body.repo), settings, lesson_id, body.library)
    if library is None:
        raise HTTPException(404, f"No lesson {lesson_id!r}; the library may have changed.")
    return library


@router.post("/lessons/{lesson_id}/approve")
async def approve_lesson(request: Request, lesson_id: str, body: Target) -> dict[str, Any]:
    """Let later runs receive a pending lesson (``learning.review = "manual"``)."""

    library = _found(request, lesson_id, body)
    try:
        lesson = await run_in_threadpool(library.approve, lesson_id)
    except LibraryError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"approved": lesson.id, "domain": lesson.domain, "library": library.name}


@router.post("/lessons/{lesson_id}/reject")
async def reject_lesson(request: Request, lesson_id: str, body: Target) -> dict[str, Any]:
    """Delete a lesson, pending or not."""

    library = _found(request, lesson_id, body)
    removed = await run_in_threadpool(library.remove, lesson_id)
    if removed is None:
        raise HTTPException(404, f"No lesson {lesson_id!r}.")
    return {"removed": removed, "library": library.name}
