"""The local web UI's backend: a FastAPI app over the run directories (``engineering-team ui``).

The server is a thin layer. It never runs a team in its own process: a run is started as a CLI
subprocess (``launcher.py``), and everything the API shows is read from the run directory the
CLI already writes (manifest, ``events.jsonl``, ``board.json``, ``pipeline.json``, the report).
Steering reaches a running run the way ``note``/``pause``/``cancel`` do, through its inbox and
cancel flag, so a run neither needs the server nor stops when the server restarts.

FastAPI and uvicorn are the optional ``ui`` extra (``uv sync --extra ui``).
"""

from __future__ import annotations

import importlib.util

API_PREFIX = "/api/v1"
# (importable module, package that provides it)
REQUIRED_MODULES = (
    ("fastapi", "fastapi"),
    ("uvicorn", "uvicorn"),
    ("multipart", "python-multipart"),
)
INSTALL_HINT = "uv sync --extra ui  (or: pip install 'engineering_team[ui]')"


def missing_dependencies() -> list[str]:
    """The packages of the ``ui`` extra that are not installed (empty when it is complete)."""

    return [
        package for module, package in REQUIRED_MODULES if importlib.util.find_spec(module) is None
    ]
