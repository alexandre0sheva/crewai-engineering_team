"""The task board (now or replayed), cards, teammates, and the run's artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

from engineering_team.runtime.inbox import post_command
from engineering_team.ui.routes_runs import Text
from engineering_team.ui.state import state_of
from engineering_team.ui.views import (
    AgentView,
    BoardView,
    CardDetail,
    agents_view,
    board_at,
    board_now,
    card_detail,
    load_board,
)

router = APIRouter()

MAX_ARTIFACTS = 1000
ARTIFACT_FOLDERS = (
    ("screenshots", "*.png", "screenshot"),
    ("commands", "*.log", "log"),
    ("reports", "*.md", "report"),
    ("notes", "*.md", "note"),
)
ARTIFACT_FILES = (
    ("report.html", "report"),
    ("report.md", "report"),
    ("CHANGE_SUMMARY.md", "report"),
    ("changes.patch", "patch"),
    ("findings.md", "report"),
    ("findings.json", "data"),
    ("board.md", "report"),
    ("usage.json", "data"),
)
TEXT_TYPES = {".log": "text/plain", ".md": "text/plain", ".patch": "text/plain",
              ".html": "text/plain", ".json": "application/json", ".png": "image/png"}  # fmt: skip


class Artifact(BaseModel):
    path: str  # relative to the run directory; fetch it at /artifacts/<path>
    kind: str
    size: int


@router.get("/runs/{run_id}/board")
def board(request: Request, run_id: str, at: int | None = None) -> BoardView:
    """The board now, or (``?at=SEQ``) as it stood after event SEQ."""

    state = state_of(request)
    ref = state.need(run_id)
    log = state.log_for(ref)
    if at is None:
        return board_now(ref, log)
    if at < 0:
        raise HTTPException(422, "at must be an event sequence number (0 or more).")
    return board_at(ref, log, at)


@router.get("/runs/{run_id}/cards/{card_id}")
def card(request: Request, run_id: str, card_id: str) -> CardDetail:
    state = state_of(request)
    ref = state.need(run_id)
    detail = card_detail(ref, state.log_for(ref), card_id)
    if detail is None:
        raise HTTPException(404, f"No card {card_id!r} in run {run_id}.")
    return detail


@router.post("/runs/{run_id}/cards/{card_id}/comments", status_code=202)
def comment(request: Request, run_id: str, card_id: str, body: Text) -> dict[str, Any]:
    """Steer one card's assignee: delivered to its next prompt (like ``note --card``)."""

    state = state_of(request)
    ref = state.need(run_id)
    wanted = card_id.strip().upper()
    if not any(c.id == wanted for c in load_board(ref.run_dir).cards):
        raise HTTPException(404, f"No card {card_id!r} in run {run_id}.")
    post_command(ref.run_dir, "note", text=body.text, card=wanted)
    return {"run_id": run_id, "queued": "note", "card": wanted}


@router.get("/runs/{run_id}/agents")
def agents(request: Request, run_id: str) -> list[AgentView]:
    state = state_of(request)
    ref = state.need(run_id)
    return agents_view(ref, state.log_for(ref))


# -- artifacts -------------------------------------------------------------------------------------


def artifact_list(run_dir: Path) -> list[Artifact]:
    """The files of a run that are meant to be looked at; nothing else in the directory."""

    found: list[Artifact] = []
    for name, kind in ARTIFACT_FILES:
        _add(found, run_dir, run_dir / name, kind)
    for folder, pattern, kind in ARTIFACT_FOLDERS:
        base = run_dir / folder
        if base.is_dir():
            for path in sorted(base.glob(pattern)):
                _add(found, run_dir, path, kind)
    return found[:MAX_ARTIFACTS]


def _add(found: list[Artifact], run_dir: Path, path: Path, kind: str) -> None:
    if path.is_file() and not path.is_symlink():
        found.append(
            Artifact(path=path.relative_to(run_dir).as_posix(), kind=kind, size=path.stat().st_size)
        )


@router.get("/runs/{run_id}/artifacts")
def artifacts(request: Request, run_id: str) -> list[Artifact]:
    state = state_of(request)
    return artifact_list(state.need(run_id).run_dir)


@router.get("/runs/{run_id}/artifacts/{path:path}")
def artifact(request: Request, run_id: str, path: str) -> Response:
    state = state_of(request)
    ref = state.need(run_id)
    if path not in {item.path for item in artifact_list(ref.run_dir)}:  # only what is listed
        raise HTTPException(404, f"No artifact {path!r} in run {run_id}.")
    target = ref.run_dir / path
    kind = TEXT_TYPES.get(target.suffix, "application/octet-stream")
    return Response(
        target.read_bytes(),
        media_type=kind,
        headers={"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox"},
    )
