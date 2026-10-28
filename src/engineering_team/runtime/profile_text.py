"""``engineering-team profile``: the run profile as text and as data."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from engineering_team.contracts import SessionUsage
from engineering_team.runtime.profile import Profile

TOP_SESSIONS = 10
PART_NAMES = ("system", "tools", "history", "results")


def seconds(value: float) -> str:
    if value < 10:
        return f"{value:.1f}s"
    minutes, secs = divmod(int(round(value)), 60)
    return f"{minutes}m{secs:02d}s" if minutes else f"{secs}s"


def money(value: float | None) -> str:
    return "unknown" if value is None else f"${value:.4f}"


def table(head: Sequence[str], rows: Sequence[Sequence[str]], left: int = 1) -> list[str]:
    """Columns padded to their widest cell; the first ``left`` columns left-aligned."""

    widths = [max(len(str(cell)) for cell in column) for column in zip(head, *rows, strict=True)]

    def line(cells: Sequence[str]) -> str:
        parts = [
            str(cell).ljust(width) if index < left else str(cell).rjust(width)
            for index, (cell, width) in enumerate(zip(cells, widths, strict=True))
        ]
        return "  ".join(parts).rstrip()

    return [line(head), *(line(row) for row in rows)]


def scaled_parts(session: SessionUsage) -> dict[str, int]:
    """The session's prompt parts as billed tokens: the estimates (characters over four) share out
    the billed prompt, since a character count of JSON schemas overstates their tokens."""

    estimated = {name: session.prompt_parts.get(name, 0) for name in PART_NAMES}
    total = sum(estimated.values())
    if not total or not session.prompt_tokens:
        return estimated
    return {name: round(value / total * session.prompt_tokens) for name, value in estimated.items()}


def label(session: SessionUsage) -> str:
    where = f"{session.stage or '-'}"
    return where if session.lane is None else f"{where}#{session.lane}"


def _session_rows(sessions: Sequence[SessionUsage]) -> list[list[str]]:
    rows = []
    for s in sessions:
        failed = sum(s.tool_failures.values())
        rows.append(
            [
                s.id,
                label(s),
                s.agent or "-",
                seconds(s.seconds) + ("*" if s.ended_with != "completed" else ""),
                str(s.calls),
                f"{s.prompt_tokens:,}",
                f"{s.cached_prompt_tokens:,}",
                f"{s.completion_tokens:,}",
                f"{s.tool_calls}/{failed}",
                money(s.cost_usd),
            ]
        )
    return rows


SESSION_HEAD = [
    "ID",
    "Stage",
    "Agent",
    "Time",
    "Calls",
    "Prompt",
    "Cached",
    "Output",
    "Tools/fail",
    "Cost",
]


def _by_cost(session: SessionUsage) -> tuple[float, float]:
    return (-(session.cost_usd or 0.0), -session.seconds)


def render_profile(profile: Profile) -> str:
    """The profile as text: sessions by cost and by time, prompt parts, critical path, re-reads,
    schema tokens per agent, and tool failures."""

    sessions = profile.sessions
    if not sessions:
        return f"Run {profile.run_id}: no agent sessions were recorded.\n"
    lines = [
        f"Run {profile.run_id}: {len(sessions)} session(s), {profile.calls} model call(s), "
        f"{profile.prompt_tokens:,} prompt tokens ({profile.cached_share:.0%} cached), "
        f"{money(profile.cost_usd)}",
        f"Elapsed {seconds(profile.elapsed_seconds)}; "
        f"session time {seconds(profile.session_seconds)} "
        f"(parallelism {profile.parallelism:.2f}x).",
        "",
        "Sessions by cost",
        *table(SESSION_HEAD, _session_rows(sorted(sessions, key=_by_cost)[:TOP_SESSIONS]), 3),
        "",
        "Sessions by time",
        *table(
            SESSION_HEAD,
            _session_rows(sorted(sessions, key=lambda s: -s.seconds)[:TOP_SESSIONS]),
            3,
        ),
    ]
    if any(s.ended_with != "completed" for s in sessions):
        lines.append("* did not complete (error, or still running)")
    lines += ["", "Prompt by part (the billed prompt, shared out by estimated size)"]
    if profile.recorded_prompts:
        rows = [
            [
                s.id,
                label(s),
                *(f"{scaled_parts(s)[name]:,}" for name in PART_NAMES),
                f"{s.prompt_tokens:,}",
            ]
            for s in sorted(sessions, key=_by_cost)[:TOP_SESSIONS]
        ]
        lines += table(["ID", "Stage", "System", "Tools", "History", "Results", "Billed"], rows, 2)
    else:
        lines.append("not recorded for this run (it predates prompt sizing).")
    lines += [
        "",
        f"Critical path: {' > '.join(profile.critical_path)} "
        f"({seconds(profile.critical_seconds)} of {seconds(profile.elapsed_seconds)})",
    ]
    for stage in profile.sequential_stages:
        lines.append(
            f"  stage {stage.stage} ran {stage.sessions} sessions one after another "
            f"({seconds(stage.seconds)}): a candidate for parallel lanes"
        )
    lines += ["", "Tool-schema tokens sent with every call, per agent"]
    schema = profile.schema_tokens()
    lines += (
        table(["Agent", "Tokens"], [[agent, f"{tokens:,}"] for agent, tokens in schema.items()])
        if any(schema.values())
        else ["none recorded."]
    )
    lines += ["", "Most re-read files"]
    lines += (
        table(
            ["Path", "Reads", "Sessions"],
            [[r.path, str(r.reads), str(r.sessions)] for r in profile.rereads],
        )
        if profile.rereads
        else ["none read twice (or no trajectory recorded)."]
    )
    lines += ["", "Tool failures"]
    lines += (
        table(
            ["Count", "Kind", "Tool", "Message"],
            [[str(f.count), f.kind, f.tool, f.message] for f in profile.failures],
            left=4,
        )
        if profile.failures
        else ["none recorded."]
    )
    return "\n".join(lines) + "\n"


def profile_data(profile: Profile) -> dict[str, Any]:
    """The profile as plain data (``profile --format json``)."""

    return {
        "run_id": profile.run_id,
        "elapsed_seconds": profile.elapsed_seconds,
        "session_seconds": profile.session_seconds,
        "parallelism": profile.parallelism,
        "model_calls": profile.calls,
        "prompt_tokens": profile.prompt_tokens,
        "cached_share": round(profile.cached_share, 4),
        "cost_usd": profile.cost_usd,
        "sessions": [
            s.model_dump(mode="json", exclude={"schema_version"}) for s in profile.sessions
        ],
        "critical_path": {"sessions": profile.critical_path, "seconds": profile.critical_seconds},
        "sequential_stages": [vars(item) for item in profile.sequential_stages],
        "tool_schema_tokens": profile.schema_tokens(),
        "rereads": [vars(item) for item in profile.rereads],
        "failures": [vars(item) for item in profile.failures],
    }
