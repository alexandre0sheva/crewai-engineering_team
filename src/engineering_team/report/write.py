"""Rendering a run's report and writing it into the run directory."""

from __future__ import annotations

import logging
from pathlib import Path

from engineering_team.atomic_io import atomic_write_text
from engineering_team.report.collect import build_report
from engineering_team.report.render_html import render_html
from engineering_team.report.render_md import render_markdown
from engineering_team.runtime.events import Scrubber
from engineering_team.settings import secret_values

log = logging.getLogger(__name__)

FORMATS = ("html", "md")
BASENAME = "report"


def report_path(run_dir: Path, fmt: str = "html") -> Path:
    if fmt not in FORMATS:
        raise ValueError(f"Unknown report format {fmt!r}; use one of: {', '.join(FORMATS)}.")
    return run_dir / f"{BASENAME}.{fmt}"


def write_report(run_dir: Path, fmt: str = "html") -> Path:
    """Render the run's report in ``fmt`` and write ``report.<fmt>`` in ``run_dir``.

    Raises :class:`~engineering_team.runtime.run_store.RunNotFound` when ``run_dir`` is not a
    run. Known secret values (environment variables that look like credentials) are scrubbed
    from the text, in case a log or an agent's note held one.
    """

    path = report_path(run_dir, fmt)
    report = build_report(run_dir)
    text = render_html(report, run_dir) if fmt == "html" else render_markdown(report)
    scrubber = Scrubber(secret_values())
    atomic_write_text(path, scrubber.scrub(text) if scrubber else text)
    return path


def write_run_report(run_dir: Path) -> Path | None:
    """The end-of-run report (``report.html``); ``None`` (and a log line) if it cannot be made."""

    try:
        return write_report(run_dir, "html")
    except Exception:
        log.warning("Could not write the run report in %s", run_dir, exc_info=True)
        return None
