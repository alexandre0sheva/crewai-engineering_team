"""Options and helpers the ``bench`` commands share: where the suite is and where batches go."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

SuiteOption = Annotated[
    str | None,
    typer.Option(
        help="The benchmark suite directory (default: ./benchmarks in a source checkout)."
    ),
]
OutOption = Annotated[
    str | None, typer.Option(help="Where batches are kept (default: <suite>/.runs).")
]


def default_suite() -> Path:
    """``./benchmarks``, else the ``benchmarks`` next to this source tree (an editable install)."""

    here = Path.cwd() / "benchmarks"
    if (here / "tasks").is_dir():
        return here
    checkout = Path(__file__).resolve().parents[3] / "benchmarks"
    return checkout if (checkout / "tasks").is_dir() else here


def suite_path(suite: str | None) -> Path:
    return Path(suite).expanduser().resolve() if suite else default_suite().resolve()
