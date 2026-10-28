"""The report's "Where the time and money went" section (the sessions of ``engineering-team
profile``). Every string that did not come from this module goes through ``esc``."""

from __future__ import annotations

from engineering_team.report.html_common import code, esc, section, table
from engineering_team.report.model import RunReport
from engineering_team.runtime.profile_text import (
    PART_NAMES,
    SESSION_HEAD,
    TOP_SESSIONS,
    label,
    money,
    scaled_parts,
    seconds,
)


def profile(report: RunReport) -> str:
    found = report.profile
    if found is None or not found.sessions:
        return ""
    ranked = sorted(found.sessions, key=lambda s: (-(s.cost_usd or 0.0), -s.seconds))
    rows = [
        [
            esc(s.id), esc(label(s)), esc(s.agent or "-"),
            esc(seconds(s.seconds) + ("*" if s.ended_with != "completed" else "")),
            str(s.calls), f"{s.prompt_tokens:,}", f"{s.cached_prompt_tokens:,}",
            f"{s.completion_tokens:,}", f"{s.tool_calls}/{sum(s.tool_failures.values())}",
            esc(money(s.cost_usd)),
        ]
        for s in ranked[:TOP_SESSIONS]
    ]  # fmt: skip
    lead = (
        f"<p>{len(found.sessions)} session(s), {found.calls} model call(s), "
        f"{found.prompt_tokens:,} prompt tokens ({found.cached_share:.0%} cached), "
        f"{esc(money(found.cost_usd))}. Elapsed {esc(seconds(found.elapsed_seconds))}, "
        f"session time {esc(seconds(found.session_seconds))} "
        f"(parallelism {found.parallelism:.2f}x). "
        f"Critical path {esc(' > '.join(found.critical_path))} "
        f"({esc(seconds(found.critical_seconds))}).</p>"
    )
    body = lead + table(SESSION_HEAD, rows, tuple(range(3, 10)))
    hints = [
        f"Stage {esc(s.stage)} ran {s.sessions} sessions one after another "
        f"({esc(seconds(s.seconds))}): a candidate for parallel lanes."
        for s in found.sequential_stages
    ]
    body += "".join(f"<p>{hint}</p>" for hint in hints)
    if found.recorded_prompts:
        parts = [
            [esc(s.id), esc(label(s))]
            + [f"{scaled_parts(s)[name]:,}" for name in PART_NAMES]
            for s in ranked[:TOP_SESSIONS]
        ]  # fmt: skip
        body += "<h3>Prompt by part (billed tokens, shared out by estimated size)</h3>" + table(
            ["ID", "Stage", "System", "Tools", "History", "Results"], parts, (2, 3, 4, 5)
        )
    if found.rereads:
        body += "<h3>Most re-read files</h3>" + table(
            ["Path", "Reads", "Sessions"],
            [[code(r.path), str(r.reads), str(r.sessions)] for r in found.rereads],
            (1, 2),
        )
    if found.failures:
        body += "<h3>Tool failures</h3>" + table(
            ["Count", "Kind", "Tool", "Message"],
            [[str(f.count), esc(f.kind), esc(f.tool), esc(f.message)] for f in found.failures],
            (0,),
        )
    return section("profile", "Where the time and money went", body)
