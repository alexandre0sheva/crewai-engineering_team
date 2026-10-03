"""Requirements intake: turning what the user supplied into one normalised request bundle."""

from engineering_team.intake.bundle import RequestBundle, RequestPart, normalise, request_hash
from engineering_team.intake.context_docs import ContextScan, install_context, scan_context_dir
from engineering_team.intake.errors import IntakeError
from engineering_team.intake.templates import MODES, TEMPLATE_MARKER, template_for

__all__ = [
    "MODES",
    "TEMPLATE_MARKER",
    "ContextScan",
    "IntakeError",
    "RequestBundle",
    "RequestPart",
    "install_context",
    "normalise",
    "request_hash",
    "scan_context_dir",
    "template_for",
]
