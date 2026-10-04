"""The run report: ``report.html`` (and ``report.md``) written from a run directory.

``build_report`` reads the run's files into a :class:`RunReport`; ``write_report`` renders it and
writes it atomically next to them. :func:`write_run_report` is the safe entry point the run's
end uses: it never raises, because a report must not turn a finished run into a failed one.
"""

from __future__ import annotations

from engineering_team.report.write import (
    FORMATS,
    report_path,
    write_report,
    write_run_report,
)

__all__ = ["FORMATS", "report_path", "write_report", "write_run_report"]
