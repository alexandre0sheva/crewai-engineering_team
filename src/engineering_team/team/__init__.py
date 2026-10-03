"""The team roster: built-in teammates, project overrides, and custom teammates."""

from engineering_team.team.registry import (
    Roster,
    TeamError,
    Teammate,
    build_roster,
    builtin_roster,
    group_notes,
)

__all__ = ["Roster", "TeamError", "Teammate", "build_roster", "builtin_roster", "group_notes"]
