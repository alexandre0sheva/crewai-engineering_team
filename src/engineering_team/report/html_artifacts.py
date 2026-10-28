"""The report's artifacts: screenshots, the diff, and the environment.

Every string that did not come from this module goes through ``esc``.
"""

from __future__ import annotations

import base64
from pathlib import Path

from engineering_team.report.fmt import size
from engineering_team.report.html_common import (
    MAX_EMBED_BYTES,
    MAX_EMBED_EACH,
    code,
    esc,
    section,
    table,
)
from engineering_team.report.model import (
    DiffFile,
    DiffView,
    RunReport,
    Screenshot,
)

# -- screenshots -----------------------------------------------------------------------------


def _embedded(run_dir: Path, shot: Screenshot, spent: int) -> str | None:
    """The screenshot as a ``data:`` URI, or ``None`` (too big, over budget, not a real file)."""

    folder = (run_dir / "screenshots").resolve()
    path = run_dir / shot.path
    try:
        if path.is_symlink() or not path.is_file() or path.resolve().parent != folder:
            return None
        if path.stat().st_size > MAX_EMBED_EACH or spent + path.stat().st_size > MAX_EMBED_BYTES:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    return "data:image/png;base64," + base64.b64encode(data).decode("ascii")


def screenshots(report: RunReport, run_dir: Path | None) -> str:
    if not report.screenshots:
        return ""
    spent = 0
    figures: list[str] = []
    for shot in report.screenshots:
        uri = _embedded(run_dir, shot, spent) if run_dir is not None else None
        caption = esc(shot.name) + (f" · {esc(shot.agent)}" if shot.agent else "")
        caption += f" · {esc(shot.url)}" if shot.url else ""
        if uri is None:
            figures.append(
                f'<figure><figcaption>{caption} · <span class="muted">not embedded '
                f"({esc(size(shot.size))}); see {esc(shot.path)} in the run directory</span>"
                "</figcaption></figure>"
            )
            continue
        spent += len(uri) * 3 // 4
        figures.append(
            f'<figure><img src="{uri}" alt="{esc(shot.name)}" loading="lazy">'
            f"<figcaption>{caption}</figcaption></figure>"
        )
    return section("screenshots", "Screenshots", "".join(figures))


# -- diff, environment -----------------------------------------------------------------------


def _diff_file(item: DiffFile, open_: bool) -> str:
    lines = []
    for line in item.lines:
        kind = (
            "add"
            if line.startswith("+")
            else "del"
            if line.startswith("-")
            else "hunk"
            if line.startswith("@@")
            else ""
        )
        lines.append(f'<span class="{kind}">{esc(line) or "&nbsp;"}</span>')
    more = (
        f'<span class="hunk">… {item.omitted} more line(s) not shown</span>' if item.omitted else ""
    )
    note = "binary file" if item.binary else ""
    body = (
        f'<pre class="diff">{"".join(lines)}{more}</pre>'
        if lines or more
        else f"<p class=muted>{note or 'no content change'}</p>"
    )
    label = f"{esc(item.path)} · {esc(item.status)} · +{item.added} −{item.removed}"
    return f"<details{' open' if open_ else ''}><summary>{label}</summary>{body}</details>"


def diff(report: RunReport) -> str:
    view: DiffView | None = report.diff
    if view is None:
        return section("diff", "Changes", f'<p class="muted">{esc(report.diff_note)}</p>')
    stats = table(
        ["File", "Status", "Added", "Removed"],
        [[code(f.path), esc(f.status), f"+{f.added}", f"−{f.removed}"] for f in view.files],
        (2, 3),
    )
    shown = "".join(_diff_file(f, len(view.files) <= 3) for f in view.files)
    note = (
        f'<p class="muted">Large diff: lines beyond the limits are not shown; the whole change is '
        f"{esc(view.patch_name)} in the run directory.</p>"
        if view.truncated
        else ""
    )
    lead = f"<p>{len(view.files)} file(s), +{view.added} −{view.removed}.</p>"
    return section("diff", "Changes", f"{lead}{stats}<h3>Diff</h3>{note}{shown}")


def trace(report: RunReport) -> str:
    info = report.trace
    if info is None:
        return ""
    notes = "".join(f'<p class="muted">{esc(note)}</p>' for note in info.notices)
    body = (
        f"<p>What the agents did and saw: {info.entries} entries ({esc(size(info.size))}) in "
        f'<a href="trajectory.jsonl">trajectory.jsonl</a> in the run directory. Read it with '
        f"{code('engineering-team trace ' + report.run_id)}.</p>{notes}"
    )
    return section("trace", "Trace", body)


def environment(report: RunReport) -> str:
    rows = "".join(f"<div><dt>{esc(k)}</dt><dd>{esc(v)}</dd></div>" for k, v in report.environment)
    return section("environment", "Environment", f'<dl class="facts">{rows}</dl>')
