"""Tools that coordinate the team: the board, notes, asking a human, and (the lead only) calling
teammates."""

from __future__ import annotations

from engineering_team.tools.board_tools import make_board_tools
from engineering_team.tools.human_tools import make_human_tools
from engineering_team.tools.notes_tools import make_notes_tools
from engineering_team.tools.spec import ToolSpec, _spec
from engineering_team.tools.team_tools import make_team_tools

TEAM_SPECS: tuple[ToolSpec, ...] = (
    _spec(
        "List Board Cards",
        "board",
        make_board_tools,
        "Compact table of cards with overall progress; filter by status, kind, or mine.",
        read_only=True,
    ),
    _spec(
        "Get Board Card",
        "board",
        make_board_tools,
        "One card in full: dependencies, comments (including the human's), history.",
        read_only=True,
    ),
    _spec(
        "Add Subtask",
        "board",
        make_board_tools,
        "Add a subtask under one of your own cards.",
        read_only=True,
    ),
    _spec(
        "Move Card",
        "board",
        make_board_tools,
        "Start or hand over your own card (never done or failed; the controller decides).",
        read_only=True,
    ),
    _spec(
        "Block Card",
        "board",
        make_board_tools,
        "Flag your own card as blocked, with a reason.",
        read_only=True,
    ),
    _spec(
        "Unblock Card",
        "board",
        make_board_tools,
        "Resume your own blocked card.",
        read_only=True,
    ),
    _spec(
        "Comment On Card",
        "board",
        make_board_tools,
        "Comment on any card; never changes its status.",
        read_only=True,
    ),
    _spec(
        "Report Progress",
        "board",
        make_board_tools,
        "One-line status on your own card, shown in the activity feed.",
        read_only=True,
    ),
    _spec(
        "Write Note",
        "notes",
        make_notes_tools,
        "Save or append to a shared note for the other agents of this run.",
        read_only=True,
    ),
    _spec(
        "Read Note",
        "notes",
        make_notes_tools,
        "Read a shared note, or the project's decision log with key 'decisions'.",
        read_only=True,
    ),
    _spec(
        "List Notes",
        "notes",
        make_notes_tools,
        "Shared notes of this run with sizes.",
        read_only=True,
    ),
    _spec(
        "Log Decision",
        "notes",
        make_notes_tools,
        "Append a dated decision to the project's append-only decision log.",
        read_only=True,
    ),
    _spec(
        "Ask Human",
        "human",
        make_human_tools,
        "Ask the person running the team a question and wait, with a timeout.",
        read_only=True,
    ),
    _spec(
        "Delegate",
        "team",
        make_team_tools,
        "Lead only: one bounded job to a teammate (brief, owned paths); returns its typed result.",
        read_only=False,
    ),
    _spec(
        "Delegate Parallel",
        "team",
        make_team_tools,
        "Lead only: independent jobs with disjoint owned paths, side by side in lanes.",
        read_only=False,
    ),
    _spec(
        "Ask Reviewer",
        "team",
        make_team_tools,
        "Lead only: the code reviewer reads the current change and returns findings.",
        read_only=False,
    ),
    _spec(
        "Ask Security Review",
        "team",
        make_team_tools,
        "Lead only: the security engineer reads the current change and returns findings.",
        read_only=False,
    ),
    _spec(
        "Ask Debugger",
        "team",
        make_team_tools,
        "Lead only: a diagnosis of a failing check and, given paths, a fix.",
        read_only=False,
    ),
    _spec(
        "Ask Architect",
        "team",
        make_team_tools,
        "Lead only: a decision on a design question or plan draft.",
        read_only=False,
    ),
    _spec(
        "Ask Analyst",
        "team",
        make_team_tools,
        "Lead only: acceptance criteria or an answer for an unclear request.",
        read_only=False,
    ),
)
