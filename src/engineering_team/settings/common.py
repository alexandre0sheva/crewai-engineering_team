"""Constants and the error type every part of the settings shares."""

from __future__ import annotations

from typing import Literal

AZURE_DISABLED = (
    "Azure is disabled by default. Enable it with enable_azure = true in engineering-team.toml "
    "(or ENGINEERING_ENABLE_AZURE=true) and set your deployment names as models."
)
SMOKE_PROFILE_MARKER = "<!-- ENGINEERING_TEAM_PROFILE: smoke -->"
PROJECT_CONFIG_NAME = "engineering-team.toml"
DEFAULT = "default"

Effort = Literal["none", "minimal", "low", "medium", "high", "xhigh"]


class SettingsError(ValueError):
    """A configuration problem, phrased for the person who has to fix it."""
