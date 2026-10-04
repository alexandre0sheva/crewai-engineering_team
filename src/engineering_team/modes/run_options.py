"""What a command line asked of a run beyond its request (``maintain --fix``, ``review --base``).

Written once into the run directory when the run is opened, and read again by the pipeline when it
starts or resumes, so a resumed run does what the first one was asked to. Agents cannot reach it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from engineering_team.atomic_io import atomic_write_json

OPTIONS_FILE = "run-options.json"


def write_run_options(run_dir: Path, options: dict[str, Any]) -> None:
    atomic_write_json(run_dir / OPTIONS_FILE, options)


def read_run_options(run_dir: Path) -> dict[str, Any]:
    try:
        data = json.loads((run_dir / OPTIONS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}
