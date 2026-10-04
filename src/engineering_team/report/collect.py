"""Reading a run directory into a :class:`RunReport`.

Works on any run, finished or not, in this process or another: it only reads files. Every file
is optional except the manifest; a missing or unreadable one leaves its section empty rather
than failing the report (a report is most needed for a run that went wrong).
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from engineering_team.board.models import BoardState
from engineering_team.board.progress import compute_progress
from engineering_team.contracts import (
    CriterionCoverage,
    Finding,
    RunManifest,
    UsageReport,
)
from engineering_team.pipeline.state import PipelineState
from engineering_team.pricing import PriceTable, default_prices
from engineering_team.report import analysis
from engineering_team.report.diffs import parse_patch
from engineering_team.report.digest import EventDigest, digest_events
from engineering_team.report.model import (
    AgentActivity,
    DiffView,
    LaneBar,
    RunReport,
    Screenshot,
    StageRow,
)
from engineering_team.runtime.events import read_events
from engineering_team.runtime.run_store import EVENTS_FILENAME, MANIFEST_FILENAME, RunNotFound
from engineering_team.runtime.usage import UsageTracker

BOARD_FILENAME = "board.json"
USAGE_FILENAME = "usage.json"
PATCH_FILENAME = "changes.patch"
MAX_PATCH_BYTES = 8 * 1024 * 1024
MAX_REQUEST_CHARS = 2000
SETTING_KEYS = ("provider", "profile", "strategy")


def build_report(run_dir: Path) -> RunReport:
    """Everything the report shows about the run in ``run_dir``.

    Raises :class:`RunNotFound` when ``run_dir`` has no readable manifest.
    """

    manifest = _manifest(run_dir)
    digest = digest_events(run_dir / EVENTS_FILENAME)
    state = PipelineState.load(run_dir)
    board = _board(run_dir, manifest.run_id)
    usage = _usage(run_dir)
    budget = manifest.summary.budget_status if manifest.summary else None
    checks = list(state.checks) if state else []
    coverage = _coverage(state)
    findings = _findings(state)
    finished = manifest.finished or digest.last_ts
    end = _end_of_timeline(manifest, digest)
    report = RunReport(
        run_id=manifest.run_id,
        project=manifest.project_name,
        mode=manifest.mode,
        strategy=manifest.strategy,
        recipe=manifest.recipe,
        status=manifest.status,
        verdict=manifest.verdict,
        created=manifest.created,
        finished=finished,
        duration_seconds=_seconds(manifest.created, finished or manifest.created),
        banner=analysis.make_banner(manifest, state, checks, board.cards, digest, budget),
        request=_request(run_dir),
        resumes=manifest.resumes,
        timeline_seconds=_seconds(manifest.created, end),
        cards=board.cards,
        progress=compute_progress(board.cards, end) if board.cards else None,
        paused=board.paused,
        usage=usage,
        budget=budget,
        tool_calls=sum(stats.calls for stats in digest.tools.values()) or usage.tool_calls,
        checks=checks,
        coverage=coverage,
        findings=findings,
        questions=list(state.needs_info) if state else [],
        summaries=dict(state.summaries) if state else {},
        environment=_environment(run_dir, manifest),
    )
    report.stages = _stages(manifest, end)
    report.lanes = _lanes(manifest, digest, end)
    report.agents = _agents(board, digest)
    report.screenshots = _screenshots(run_dir, digest)
    report.diff, report.diff_note = _diff(run_dir)
    report.warnings = analysis.make_notices(
        manifest, state, checks, coverage, findings, digest, budget, usage.unpriced_models
    )
    return report


# -- the files ---------------------------------------------------------------------------


def _manifest(run_dir: Path) -> RunManifest:
    try:
        return RunManifest.model_validate_json(
            (run_dir / MANIFEST_FILENAME).read_text(encoding="utf-8")
        )
    except (OSError, ValidationError, ValueError):
        raise RunNotFound(f"No readable run manifest in {run_dir}.") from None


def _board(run_dir: Path, run_id: str) -> BoardState:
    try:
        return BoardState.model_validate_json((run_dir / BOARD_FILENAME).read_text("utf-8"))
    except (OSError, ValidationError, ValueError):
        return BoardState(run_id=run_id)


def _usage(run_dir: Path) -> UsageReport:
    try:
        return UsageReport.model_validate_json((run_dir / USAGE_FILENAME).read_text("utf-8"))
    except (OSError, ValidationError, ValueError):
        # A run that has not ended (or crashed) has no usage.json: rebuild it from the events.
        events = read_events(run_dir / EVENTS_FILENAME)
        return UsageTracker.from_events(events).report(PriceTable(default_prices()))


def _request(run_dir: Path) -> str:
    try:
        text = (run_dir / "request.md").read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if len(text) > MAX_REQUEST_CHARS:
        return f"{text[:MAX_REQUEST_CHARS]}\n… [{len(text) - MAX_REQUEST_CHARS} more characters]"
    return text


def _coverage(state: PipelineState | None) -> list[CriterionCoverage]:
    if state is None:
        return []
    if state.verification.coverage:
        return list(state.verification.coverage)
    if state.spec is not None:  # never verified: every criterion is unproven
        return [
            CriterionCoverage(id=c.id, text=c.text, note="no check ran for this criterion")
            for c in state.spec.criteria
        ]
    return []


def _findings(state: PipelineState | None) -> list[Finding]:
    if state is None:
        return []
    every = [*state.findings, *state.audit_findings]
    return sorted(
        every,
        key=lambda f: (analysis.SEVERITY_ORDER.get(f.severity, 9), f.id, f.file or "", f.line or 0),
    )


def _environment(run_dir: Path, manifest: RunManifest) -> list[tuple[str, str]]:
    rows = [("Mode", manifest.mode), ("Strategy", manifest.strategy)]
    if manifest.recipe:
        rows.append(("Recipe", manifest.recipe))
    rows += sorted(manifest.versions.items())
    try:
        settings = json.loads((run_dir / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        settings = {}
    if isinstance(settings, dict):  # a whitelist: settings.json is not a place to print blindly
        rows += [(key.capitalize(), str(settings[key])) for key in SETTING_KEYS if key in settings]
        backend = (settings.get("execution") or {}).get("backend")
        if isinstance(backend, str):
            rows.append(("Sandbox", backend))
    rows.append(("Settings hash", manifest.settings_hash[:12]))
    rows.append(("Request hash", manifest.request_hash[:12]))
    rows.append(("Workspace", str(run_dir.parents[2]) if len(run_dir.parents) > 2 else ""))
    return [(key, value) for key, value in rows if value]


# -- timeline ------------------------------------------------------------------------------


def _seconds(start: datetime, end: datetime) -> float:
    return round(max(0.0, (end - start).total_seconds()), 3)


def _end_of_timeline(manifest: RunManifest, digest: EventDigest) -> datetime:
    moments = [manifest.created, manifest.finished, digest.last_ts]
    moments += [stage.finished or stage.started for stage in manifest.stages]
    return max(moment for moment in moments if moment is not None)


def _stages(manifest: RunManifest, end: datetime) -> list[StageRow]:
    rows: list[StageRow] = []
    for stage in manifest.stages:
        began = stage.started
        stop = stage.finished or (end if stage.status == "running" else began)
        rows.append(
            StageRow(
                name=stage.name,
                status=stage.status,
                attempts=stage.attempts,
                start=_seconds(manifest.created, began) if began else None,
                end=_seconds(manifest.created, stop) if stop else None,
                detail=stage.detail,
            )
        )
    return rows


def _lanes(manifest: RunManifest, digest: EventDigest, end: datetime) -> list[LaneBar]:
    order = {stage.name: index for index, stage in enumerate(manifest.stages)}
    bars = [
        LaneBar(
            stage=span.stage,
            lane=span.lane,
            unit=span.unit,
            status=span.status,
            start=_seconds(manifest.created, span.start),
            end=_seconds(manifest.created, span.end or end),
            error=span.error,
        )
        for span in digest.lanes
    ]
    return sorted(bars, key=lambda b: (order.get(b.stage, len(order)), b.start, b.lane, b.unit))


# -- teammates, screenshots, diff ------------------------------------------------------------


def _agents(board: BoardState, digest: EventDigest) -> list[AgentActivity]:
    assigned: dict[str, list[str]] = {}
    for card in board.cards:
        if card.assignee and card.kind != "user_note":
            assigned.setdefault(card.assignee, []).append(card.id)
    names = sorted(set(digest.tools) | set(assigned))
    rows: list[AgentActivity] = []
    for name in names:
        stats = digest.tools.get(name)
        rows.append(
            AgentActivity(
                agent=name,
                tool_calls=stats.calls if stats else 0,
                failed_calls=stats.failed if stats else 0,
                seconds=round(stats.seconds, 2) if stats else 0.0,
                cards=assigned.get(name, []),
                tools=(
                    dict(sorted(stats.by_tool.items(), key=lambda kv: (-kv[1], kv[0])))
                    if stats
                    else {}
                ),
            )
        )
    return rows


def _screenshots(run_dir: Path, digest: EventDigest) -> list[Screenshot]:
    found: dict[str, Screenshot] = {}
    for record in digest.screenshots:
        found.setdefault(
            record.path,
            Screenshot(Path(record.path).name, record.path, record.agent, record.url, record.size),
        )
    folder = run_dir / "screenshots"
    if folder.is_dir():  # also those whose event was lost; never anything that is not a real file
        for file in sorted(folder.glob("*.png")):
            relative = f"screenshots/{file.name}"
            if file.is_file() and not file.is_symlink():
                size = file.stat().st_size
                found.setdefault(relative, Screenshot(file.name, relative, size=size))
    return [shot for _, shot in sorted(found.items())]


def _diff(run_dir: Path) -> tuple[DiffView | None, str]:
    path = run_dir / PATCH_FILENAME
    try:
        with path.open("rb") as handle:
            raw = handle.read(MAX_PATCH_BYTES + 1)
    except OSError:
        return None, (
            "No change was recorded for this run: only runs that work in an existing project "
            "(feature, fix, maintain) write changes.patch."
        )
    text = raw[:MAX_PATCH_BYTES].decode("utf-8", errors="replace")
    view = parse_patch(text, PATCH_FILENAME)
    if view is None:
        return None, "The run changed no files."
    if len(raw) > MAX_PATCH_BYTES:
        view = replace(view, truncated=True)
    return view, ""
