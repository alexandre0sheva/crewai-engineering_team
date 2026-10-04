"""The FastAPI application: assembly, error shape, and the request guard."""

from __future__ import annotations

from importlib import metadata
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from engineering_team.settings import Settings
from engineering_team.ui import (
    API_PREFIX,
    routes_app,
    routes_board,
    routes_files,
    routes_info,
    routes_runs,
)
from engineering_team.ui.launcher import RunLauncher, StartError
from engineering_team.ui.security import GuardMiddleware, Security
from engineering_team.ui.state import UiState

STATIC_DIR = Path(__file__).parent / "static"
# The app shell is same-origin scripts and styles only (no CDN, no inline code); run output is
# data the page escapes, and images are its own or ``blob:`` URLs of authenticated fetches.
PAGE_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:; "
        "connect-src 'self'; font-src 'self'; base-uri 'none'; form-action 'none'; "
        "frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-cache",
}


class ShellFiles(StaticFiles):
    """The app's own files, served with the page's security headers."""

    def file_response(self, *args: Any, **kwargs: Any) -> Any:
        response = super().file_response(*args, **kwargs)
        response.headers.update(PAGE_HEADERS)
        return response


def version() -> str:
    try:
        return metadata.version("engineering_team")
    except metadata.PackageNotFoundError:
        return "unknown"


def create_app(
    settings: Settings,
    *,
    security: Security | None = None,
    launcher: RunLauncher | None = None,
    config_file: str | None = None,
    demo_repo: str | None = None,
) -> FastAPI:
    """The API over the runs under ``settings.workspace_root``.

    ``launcher`` is how runs get started (tests pass one that runs a stand-in command).
    """

    security = security or Security(
        max_request_bytes=settings.ui.max_request_bytes,
        max_upload_bytes=settings.ui.max_upload_bytes,
    )
    launcher = launcher or RunLauncher(
        settings.workspace_root,
        max_concurrent=settings.ui.max_concurrent_runs,
        config_file=config_file,
    )
    app = FastAPI(
        title="engineering-team",
        version=version(),
        docs_url=f"{API_PREFIX}/docs",
        openapi_url=f"{API_PREFIX}/openapi.json",
        redoc_url=None,
    )
    app.state.ui = UiState(settings, launcher, security, demo_repo)
    app.add_middleware(GuardMiddleware, security=security)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def invalid(request: Request, exc: RequestValidationError) -> JSONResponse:
        problems = [
            {"where": ".".join(str(part) for part in item["loc"]), "problem": item["msg"]}
            for item in exc.errors()
        ]
        first = problems[0] if problems else {"where": "", "problem": "invalid request"}
        message = f"{first['where']}: {first['problem']}".strip(": ")
        return JSONResponse({"error": message, "details": problems}, status_code=422)

    @app.exception_handler(StartError)
    async def start_error(request: Request, exc: StartError) -> JSONResponse:
        return JSONResponse({"error": str(exc)}, status_code=exc.status)

    @app.exception_handler(ValueError)
    async def usage_error(request: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse({"error": str(exc)}, status_code=422)

    @app.get(f"{API_PREFIX}/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "version": version(), "auth": "bearer" if security.remote else "none"}

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html", headers=PAGE_HEADERS)

    app.mount("/static", ShellFiles(directory=STATIC_DIR), name="static")

    for router in (
        routes_app.router,
        routes_runs.router,
        routes_board.router,
        routes_files.router,
        routes_info.router,
    ):
        app.include_router(router, prefix=API_PREFIX)
    return app


__all__ = ["HTTPException", "create_app"]
