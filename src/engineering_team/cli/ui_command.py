"""``ui``: serve the local web UI's API (and nothing but localhost, unless told otherwise)."""

from __future__ import annotations

import contextlib
import secrets
from typing import Annotated, Any

import typer

from engineering_team.cli.context import fail, print_json
from engineering_team.cli.context import get as get_globals
from engineering_team.cli.info_commands import ConfigOpt, load
from engineering_team.settings import Settings
from engineering_team.ui import INSTALL_HINT, missing_dependencies
from engineering_team.ui.security import LOCAL_HOSTS

DEFAULT_PORT = 8765


def ui(
    ctx: typer.Context,
    host: Annotated[str, typer.Option(help="Address to listen on.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(min=1, max=65535, help="Port to listen on.")] = DEFAULT_PORT,
    allow_remote: Annotated[
        bool,
        typer.Option(
            help="Listen on a non-local address. Requires the bearer token printed at start."
        ),
    ] = False,
    demo: Annotated[
        bool,
        typer.Option(
            help="Demo mode: runs are scripted (no API key, no model) in a temporary workspace, "
            "with a sample project to point them at."
        ),
    ] = False,
    config: ConfigOpt = None,
) -> None:
    """Serve the web UI (and its API, /api/v1) for the runs of this workspace root."""

    g = get_globals(ctx)
    if missing := missing_dependencies():
        fail(f"The web UI needs {', '.join(missing)}. Install it with: {INSTALL_HINT}")
    local = host.lower() in LOCAL_HOSTS
    if not local and not allow_remote:
        fail(
            f"{host!r} is not a local address. To listen beyond localhost pass --allow-remote; "
            "the server then requires a bearer token (printed at start)."
        )
    settings = load(g, config)
    launcher = demo_repo = None
    if demo:
        settings, launcher, demo_repo = _demo(settings, config)
    token = None if local else secrets.token_urlsafe(32)

    import uvicorn

    from engineering_team.ui.app import create_app
    from engineering_team.ui.security import Security

    security = Security(
        remote=not local,
        token=token,
        max_request_bytes=settings.ui.max_request_bytes,
        max_upload_bytes=settings.ui.max_upload_bytes,
    )
    app = create_app(
        settings,
        security=security,
        launcher=launcher,
        config_file=config,
        demo_repo=demo_repo,
    )
    shown = f"http://{host}:{port}" if ":" not in host else f"http://[{host}]:{port}"
    if g.json:
        print_json({"url": shown, "api": f"{shown}/api/v1", "token": token})
    else:
        typer.echo(f"engineering-team UI on {shown}  (API: {shown}/api/v1, docs: /api/v1/docs)")
        if demo:
            typer.echo(f"Demo mode: scripted runs, no model. Sample project: {demo_repo}")
        if token:
            typer.echo(f"Bearer token (send as 'Authorization: Bearer <token>'): {token}")
        typer.echo(
            "Runs are separate processes: stopping the server does not stop them. Ctrl-C to quit."
        )
    with contextlib.suppress(KeyboardInterrupt):
        uvicorn.run(app, host=host, port=port, log_level="warning", access_log=False)


def _demo(settings: Settings, config: str | None) -> tuple[Settings, Any, str]:
    """Settings, launcher and sample project for ``--demo``: a temporary workspace root, and a
    launcher that starts the scripted CLI (``ui/demo.py``) instead of the model-driven one."""

    import sys
    import tempfile
    from pathlib import Path

    from engineering_team.ui.demo import DEMO_BUDGET_USD, ensure_sample_repo
    from engineering_team.ui.launcher import RunLauncher

    root = Path(tempfile.mkdtemp(prefix="engineering-team-demo-"))
    settings = settings.with_overrides(
        {"workspace_root": str(root / "runs"), "budget.max_cost_usd": DEMO_BUDGET_USD},
        source="--demo",
    )
    launcher = RunLauncher(
        settings.workspace_root,
        max_concurrent=settings.ui.max_concurrent_runs,
        command=[sys.executable, "-m", "engineering_team.ui.demo"],
        config_file=config,
        environment={
            "ENGINEERING_STRATEGY": "pipeline",  # the scripted runner is the pipeline's
            "ENGINEERING_BUDGET_MAX_COST_USD": str(DEMO_BUDGET_USD),  # so the run warns about it
        },
    )
    return settings, launcher, str(ensure_sample_repo(root))
