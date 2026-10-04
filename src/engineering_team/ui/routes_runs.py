"""Starting, listing, watching, and steering runs."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field, ValidationError

from engineering_team.board.rules import BoardError
from engineering_team.pipeline.runner import cancel_run
from engineering_team.report import write_report
from engineering_team.runtime.inbox import post_command
from engineering_team.runtime.run_index import find_runs
from engineering_team.runtime.run_store import TERMINAL_STATUSES, RunNotFound
from engineering_team.ui.launcher import RunOptions, StartError, StartRun, Upload
from engineering_team.ui.state import UiState, state_of
from engineering_team.ui.views import RunView, load_board, question_view, run_view

router = APIRouter()

MAX_FILES = 20
MAX_NOTE = 4000
EXIT_GRACE_SECONDS = 5.0
RESUMABLE = ("failed", "cancelled", "interrupted")
REPORT_HEADERS = {
    # The report is self-contained; this keeps it inert even if it were ever tampered with.
    "Content-Security-Policy": (
        "default-src 'none'; img-src data:; style-src 'unsafe-inline'; "
        "script-src 'unsafe-inline'; sandbox allow-scripts"
    ),
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "no-store",
}
SSE_HEADERS = {"Cache-Control": "no-store", "X-Accel-Buffering": "no"}


class Text(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_NOTE)


class Answer(BaseModel):
    question_id: str = Field(min_length=1, max_length=40)
    text: str = Field(default="", max_length=MAX_NOTE)  # empty: decline; the team assumes


# -- starting ------------------------------------------------------------------------------------


async def _read_start(request: Request) -> tuple[StartRun, dict[str, list[Upload]]]:
    """The start request: JSON, or ``multipart/form-data`` with a ``spec`` field (the JSON) and
    ``request_files`` / ``context_files`` parts."""

    uploads: dict[str, list[Upload]] = {}
    kind = request.headers.get("content-type", "").lower()
    try:
        if kind.startswith("multipart/form-data"):
            form = await request.form(max_files=MAX_FILES, max_fields=20)
            raw = form.get("spec")
            if not isinstance(raw, str):
                raise StartError("Send the start request as a form field named 'spec' (JSON).")
            data: Any = json.loads(raw)
            for field in ("request_files", "context_files"):
                uploads[field] = [
                    Upload(part.filename or "", await part.read())
                    for part in form.getlist(field)
                    if not isinstance(part, str)
                ]
        else:
            data = await request.json()
        return StartRun.model_validate(data), uploads
    except (ValueError, ValidationError) as exc:
        if isinstance(exc, StartError):
            raise
        raise StartError(_first_problem(exc)) from exc


def _first_problem(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        item = exc.errors()[0]
        return f"{'.'.join(str(p) for p in item['loc'])}: {item['msg']}"
    return f"The request is not valid JSON: {exc}"


@router.post("/runs", status_code=202)
async def start_run(request: Request) -> dict[str, Any]:
    state = state_of(request)
    spec, uploads = await _read_start(request)
    record = await run_in_threadpool(state.launcher.start, spec, uploads)
    return {
        "run_id": record.run_id,
        "status": "starting",
        "mode": spec.mode,
        "links": _links(record.run_id),
    }


def _links(run_id: str) -> dict[str, str]:
    base = f"/api/v1/runs/{run_id}"
    return {"self": base, "events": f"{base}/events", "board": f"{base}/board"}


# -- listing and looking -------------------------------------------------------------------------


def _starting_view(state: UiState, run_id: str) -> RunView | None:
    """A run that has no manifest yet: still starting, or it died before it could write one."""

    record = state.launcher.record(run_id)
    if record is None:
        return None
    alive = state.launcher.alive(record)
    return RunView(
        run_id=run_id,
        project="",
        workspace="",
        status="starting" if alive else "failed",
        process=state.launcher.process_info(run_id),
        error="" if alive else (state.launcher.log_tail(record) or "The process ended at once."),
    )


@router.get("/runs")
def list_runs(request: Request, project: str | None = None, limit: int = 50) -> list[RunView]:
    state = state_of(request)
    limit = max(1, min(limit, 500))
    try:
        found = find_runs(state.settings.workspace_root, project)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    views = [
        run_view(ref, state.log_for(ref), state.launcher.process_info(ref.run_id))
        for ref in found[-limit:]
    ]
    known = {view.run_id for view in views}
    for record in state.launcher.records():
        if record.run_id not in known and state.find(record.run_id) is None:
            starting = _starting_view(state, record.run_id)
            if starting is not None:
                views.append(starting)
    return sorted(views, key=lambda view: view.run_id)


@router.get("/runs/{run_id}")
def get_run(request: Request, run_id: str) -> RunView:
    state = state_of(request)
    ref = state.find(run_id)
    if ref is None:
        starting = _starting_view(state, run_id)
        if starting is None:
            raise HTTPException(404, f"No run {run_id!r}.")
        return starting
    return run_view(ref, state.log_for(ref), state.launcher.process_info(run_id))


# -- events (SSE) --------------------------------------------------------------------------------


@router.get("/runs/{run_id}/events")
async def events(
    request: Request,
    run_id: str,
    after: int | None = None,
    follow: bool = True,
    types: str | None = None,
) -> StreamingResponse:
    """The run's events as Server-Sent Events. ``id`` is the event's ``seq``; reconnect with the
    ``Last-Event-ID`` header (or ``?after=SEQ``) to continue. ``follow=false`` ends the stream
    when the file does; ``types`` is a comma-separated list of event-type prefixes."""

    state = state_of(request)
    if state.find(run_id) is None and state.launcher.record(run_id) is None:
        raise HTTPException(404, f"No run {run_id!r}.")
    last_id = request.headers.get("last-event-id", "")
    sent = after if after is not None else int(last_id) if last_id.isdigit() else 0
    prefixes = tuple(item.strip() for item in (types or "").split(",") if item.strip())
    return StreamingResponse(
        _stream(request, state, run_id, sent, follow, prefixes),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


async def _stream(
    request: Request,
    state: UiState,
    run_id: str,
    sent: int,
    follow: bool,
    prefixes: tuple[str, ...],
) -> Any:
    quiet = 0.0
    while True:
        ref = state.find(run_id)
        batch = await run_in_threadpool(state.log_for(ref).read_after, sent, 500) if ref else []
        for seq, line in batch:
            if not prefixes or _wanted(line, prefixes):
                yield f"id: {seq}\ndata: {line.decode('utf-8')}\n\n"
        if batch:
            sent, quiet = batch[-1][0], 0.0
            continue
        if not follow or await request.is_disconnected():
            return
        if _over(state, run_id, ref is not None):
            yield "event: end\ndata: {}\n\n"
            return
        await asyncio.sleep(0.25)
        quiet += 0.25
        if quiet >= 15:
            yield ": keepalive\n\n"
            quiet = 0.0


def _wanted(line: bytes, prefixes: tuple[str, ...]) -> bool:
    try:
        kind = str(json.loads(line).get("type", ""))
    except ValueError:
        return False
    return kind.startswith(prefixes)


def _over(state: UiState, run_id: str, has_manifest: bool) -> bool:
    """Nothing more will come: the run ended and no process is working on it."""

    record = state.launcher.record(run_id)
    working = record is not None and state.launcher.alive(record)
    ref = state.find(run_id)
    if ref is None:
        return not working  # never wrote a manifest and its process is gone
    return ref.manifest.status in TERMINAL_STATUSES and not working


# -- steering ------------------------------------------------------------------------------------


@router.post("/runs/{run_id}/cancel")
def cancel(request: Request, run_id: str) -> dict[str, Any]:
    state = state_of(request)
    ref = state.find(run_id)
    if ref is None:
        record = state.launcher.record(run_id)
        if record is not None and state.launcher.alive(record):  # not far enough to have a flag
            os.kill(record.pid, signal.SIGINT)
            return {"run_id": run_id, "message": "Asked the starting process to stop."}
        raise HTTPException(404, f"No run {run_id!r}.")
    try:
        message = cancel_run(ref.workspace, run_id)
    except RunNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"run_id": run_id, "message": message}


@router.post("/runs/{run_id}/resume", status_code=202)
def resume(request: Request, run_id: str, options: RunOptions | None = None) -> dict[str, Any]:
    state = state_of(request)
    ref = state.need(run_id)
    if ref.manifest.status not in RESUMABLE:
        raise HTTPException(
            409, f"Run {run_id} is {ref.manifest.status}; only a {'/'.join(RESUMABLE)} run resumes."
        )
    record = state.launcher.record(run_id)
    # A run that has just ended may still be closing down; give it a moment before refusing.
    deadline = time.monotonic() + EXIT_GRACE_SECONDS
    while record is not None and state.launcher.alive(record) and time.monotonic() < deadline:
        time.sleep(0.1)
    if record is not None and state.launcher.alive(record):
        raise HTTPException(409, f"Run {run_id} still has a process working on it.")
    started = state.launcher.resume(run_id, options or RunOptions())
    return {"run_id": run_id, "status": "starting", "pid": started.pid, "links": _links(run_id)}


@router.post("/runs/{run_id}/answer", status_code=202)
def answer(request: Request, run_id: str, body: Answer) -> dict[str, Any]:
    state = state_of(request)
    ref = state.need(run_id)
    open_ids = {q.id for q in state.log_for(ref).digest().questions.values()}
    if body.question_id not in open_ids:
        raise HTTPException(
            409,
            f"No open question {body.question_id!r}; open: "
            f"{', '.join(sorted(open_ids)) or 'none'}.",
        )
    post_command(ref.run_dir, "answer", question=body.question_id, text=body.text)
    return {"run_id": run_id, "queued": True, "declined": not body.text.strip()}


@router.get("/runs/{run_id}/questions")
def questions(request: Request, run_id: str) -> list[Any]:
    state = state_of(request)
    ref = state.need(run_id)
    return [question_view(q) for q in state.log_for(ref).digest().questions.values()]


@router.post("/runs/{run_id}/pause", status_code=202)
def pause(request: Request, run_id: str) -> dict[str, Any]:
    return _queue(request, run_id, "pause")


@router.post("/runs/{run_id}/unpause", status_code=202)
def unpause(request: Request, run_id: str) -> dict[str, Any]:
    return _queue(request, run_id, "unpause")


@router.post("/runs/{run_id}/notes", status_code=202)
def note(request: Request, run_id: str, body: Text) -> dict[str, Any]:
    return _queue(request, run_id, "note", text=body.text)


def _queue(request: Request, run_id: str, kind: str, **fields: Any) -> dict[str, Any]:
    """Drop a command in the run's inbox; the process working on it applies it (or the next
    resume does). Same mechanism as ``engineering-team note|pause|unpause``."""

    state = state_of(request)
    ref = state.need(run_id)
    try:
        post_command(ref.run_dir, kind, **fields)
    except (ValueError, BoardError) as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"run_id": run_id, "queued": kind, "status": ref.manifest.status}


# -- the report and the diff ---------------------------------------------------------------------


@router.get("/runs/{run_id}/report")
def report(request: Request, run_id: str, format: str = "html") -> Response:
    state = state_of(request)
    ref = state.need(run_id)
    if format not in ("html", "md"):
        raise HTTPException(422, "format must be html or md.")
    path = write_report(ref.run_dir, format)
    kind = "text/html; charset=utf-8" if format == "html" else "text/markdown; charset=utf-8"
    return Response(path.read_bytes(), media_type=kind, headers=REPORT_HEADERS)


@router.get("/runs/{run_id}/diff")
def diff(request: Request, run_id: str, stat: bool = False) -> dict[str, Any]:
    from engineering_team.git.port import GitError
    from engineering_team.modes.change_report import PATCH_FILE
    from engineering_team.modes.isolation import read_isolation
    from engineering_team.modes.repo_analyzer import standalone_git

    state = state_of(request)
    ref = state.need(run_id)
    isolation = read_isolation(ref.workspace)
    if isolation is None or not isolation.base_commit:
        raise HTTPException(
            404,
            f"Run {run_id} has no recorded starting commit: only feature, fix and maintain runs "
            "have a diff.",
        )
    patch = ref.run_dir / PATCH_FILE
    try:
        if patch.is_file() and not stat and ref.manifest.status in TERMINAL_STATUSES:
            text = patch.read_text(encoding="utf-8", errors="replace")
        else:
            with standalone_git(ref.workspace) as port:
                text = (
                    port.diff_stat(isolation.base_commit)
                    if stat
                    else port.diff(isolation.base_commit)
                )
    except GitError as exc:
        raise HTTPException(409, str(exc)) from exc
    limit = 2_000_000
    return {
        "run_id": run_id,
        "base": isolation.base_commit,
        "stat": stat,
        "diff": text[:limit],
        "truncated": len(text) > limit,
    }


__all__ = ["router", "load_board"]
