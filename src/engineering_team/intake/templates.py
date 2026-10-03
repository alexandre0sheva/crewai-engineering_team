"""Request templates for ``init --mode``: guidance a person replaces, and the marker that makes an
unedited template refuse to run."""

from __future__ import annotations

from importlib import resources

TEMPLATE_MARKER = "<!-- ENGINEERING_TEAM_REQUEST_TEMPLATE -->"
MODES = ("new", "feature", "fix", "maintain")


def template_for(mode: str) -> str:
    """The request template for ``mode`` (every one starts with :data:`TEMPLATE_MARKER`)."""

    if mode not in MODES:
        raise ValueError(f"Unknown mode '{mode}'. Choose one of: {', '.join(MODES)}.")
    return (resources.files("engineering_team") / "intake" / "templates" / f"{mode}.md").read_text(
        encoding="utf-8"
    )
