"""The run report built from hand-made run directories: content, escaping, bounds, ordering."""

from __future__ import annotations

import base64
import re
from pathlib import Path

import pytest
from report_helpers import (
    PATCH,
    at,
    budget_over,
    card,
    check,
    coverage,
    finding,
    lane_finished,
    lane_started,
    run_dir_in,
    screenshot_event,
    summary,
    tool_call,
    write_board,
    write_events,
    write_manifest,
    write_state,
    write_usage,
)

from engineering_team.contracts import StageRecord
from engineering_team.report import write_report, write_run_report
from engineering_team.report.collect import build_report
from engineering_team.report.diffs import MAX_FILE_LINES, parse_patch
from engineering_team.report.fmt import MAX_LOG_CHARS
from engineering_team.runtime.run_store import RunNotFound

SECTIONS = (
    "summary", "timeline", "board", "agents", "usage", "checks", "criteria", "findings", "diff",
    "environment",
)  # fmt: skip
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    """A finished, verified run with every kind of record."""

    directory = run_dir_in(tmp_path)
    write_manifest(directory, verdict="verified", summary=summary())
    write_events(
        directory,
        [
            (0, "run.started", {}),
            lane_started(21, 1, "WP-1"),
            lane_started(22, 2, "WP-2"),
            tool_call(25, "backend", "Write File", duration=0.2),
            tool_call(26, "backend", "Read File"),
            tool_call(27, "backend", "Read File", ok=False),
            tool_call(28, "qa", "Run Tests", duration=3.0),
            lane_finished(60, 2, "WP-2"),
            lane_finished(80, 1, "WP-1", "failed", "boom"),
            (121, "run.finished", {"status": "succeeded"}),
        ],
    )
    write_board(directory, [card("K-001", "Build storage"), card("K-002", "Build CLI", "failed")])
    write_usage(directory)
    write_state(
        directory,
        checks=[check()],
        findings=[finding(), finding(id="F-2", severity="low", summary="Typo")],
        verification={"verdict": "verified", "coverage": [c.model_dump() for c in coverage()]},
    )
    (directory / "request.md").write_text("Build a notes CLI.\n", encoding="utf-8")
    (directory / "changes.patch").write_text(PATCH, encoding="utf-8")
    return directory


def html_of(run_dir: Path) -> str:
    return write_report(run_dir, "html").read_text(encoding="utf-8")


# -- structure ---------------------------------------------------------------------------------


def test_the_html_has_every_section_and_is_self_contained(run_dir: Path) -> None:
    html = html_of(run_dir)

    for ident in SECTIONS:
        assert f'<section id="{ident}">' in html
    assert html.startswith("<!doctype html>")
    assert "prefers-color-scheme:dark" in html and 'data-theme="dark"' in html
    assert "Content-Security-Policy" in html
    # nothing is fetched: no external script, stylesheet, image, or link
    assert not re.search(r"""(src|href)=["']https?:""", html)
    assert "<link" not in html and "@import" not in html and "cdn" not in html.lower()


def test_the_banner_summary_and_timeline_show_the_run(run_dir: Path) -> None:
    report = build_report(run_dir)
    html = html_of(run_dir)

    assert report.banner.label == "Verified" and report.banner.tone == "good"
    assert [s.name for s in report.stages] == ["spec", "implement", "verify"]
    assert "Verified" in html and "20261003-120000-abc123" in html
    assert "Build a notes CLI." in html  # the request
    assert "lane 1 · WP-1" in html and "lane 2 · WP-2" in html  # parallel lanes
    assert report.timeline_seconds == 121  # to the last event


def test_lanes_are_paired_and_a_failed_lane_keeps_its_error(run_dir: Path) -> None:
    lanes = {(b.lane, b.unit): b for b in build_report(run_dir).lanes}

    assert lanes[("1", "WP-1")].status == "failed" and lanes[("1", "WP-1")].error == "boom"
    assert (lanes[("2", "WP-2")].start, lanes[("2", "WP-2")].end) == (22.0, 60.0)


def test_board_snapshot_has_columns_cards_and_history(run_dir: Path) -> None:
    html = html_of(run_dir)

    assert "Done (1)" in html and "Failed (1)" in html
    assert "K-001" in html and "Build CLI" in html
    assert "backlog → in_progress" in html  # per-card history
    assert "(created)" in html


def test_teammates_get_tool_call_counts(run_dir: Path) -> None:
    agents = {a.agent: a for a in build_report(run_dir).agents}

    assert agents["backend"].tool_calls == 3 and agents["backend"].failed_calls == 1
    assert agents["backend"].tools == {"Read File": 2, "Write File": 1}  # most used first
    assert agents["backend"].cards == ["K-001", "K-002"]
    assert agents["qa"].tool_calls == 1 and agents["qa"].cards == []


def test_usage_cost_and_budget_are_shown(tmp_path: Path) -> None:
    directory = run_dir_in(tmp_path)
    write_manifest(directory, status="failed", summary=summary("failed", budget_over()))
    write_usage(directory, cost=1.2)

    report = build_report(directory)
    html = html_of(directory)

    assert report.banner.tone == "bad"
    assert "The run was stopped: max_cost_usd 1.0 exceeded." in report.banner.reasons
    assert "$1.2000" in html and "$1.0000" in html  # cost against the limit
    assert "Budget exceeded" in html and "max_tokens is not enforced" in html


def test_unknown_cost_is_shown_as_unknown_never_zero(tmp_path: Path) -> None:
    directory = run_dir_in(tmp_path)
    write_manifest(directory)
    write_usage(directory, cost=None)

    html = html_of(directory)

    assert "unknown" in html and "$0.0000" not in html
    assert any("no price for odd/model" in w.text for w in build_report(directory).warnings)


def test_checks_show_status_and_log_excerpts(tmp_path: Path) -> None:
    directory = run_dir_in(tmp_path)
    write_manifest(directory, status="failed", verdict="failed")
    write_state(
        directory,
        checks=[
            check("tests", "failed", log_tail="E   assert 1 == 2", hint="Fix the test"),
            check("lint", "unavailable", required=False, hint="ruff is not installed"),
        ],
    )

    report = build_report(directory)
    html = html_of(directory)

    assert "E   assert 1 == 2" in html and "Fix the test" in html
    assert report.banner.label == "Not verified"
    assert any("Required check tests failed: 2 failed" in r for r in report.banner.reasons)
    assert any("Check lint was unavailable" in w.text for w in report.warnings)


def test_the_criteria_matrix_flags_what_no_check_proves(run_dir: Path) -> None:
    html = html_of(run_dir)
    report = build_report(run_dir)

    assert "1 of 2 criteria are <strong>not</strong> proven" in html
    assert "not proven" in html and "AC-2" in html
    assert any("1 of 2 acceptance criteria are not proven" in w.text for w in report.warnings)


def test_criteria_of_an_unverified_spec_are_all_listed_unverified(tmp_path: Path) -> None:
    directory = run_dir_in(tmp_path)
    write_manifest(directory, status="failed")
    write_state(
        directory, spec={"title": "T", "criteria": [{"id": "AC-1", "text": "does a thing"}]}
    )

    assert [(c.id, c.status) for c in build_report(directory).coverage] == [("AC-1", "unverified")]


def test_findings_are_listed_most_severe_first(run_dir: Path) -> None:
    report = build_report(run_dir)

    assert [f.id for f in report.findings] == ["F-1", "F-2"]
    assert any("high or critical" in w.text for w in report.warnings)


def test_the_diff_has_stats_and_a_viewer(run_dir: Path) -> None:
    report = build_report(run_dir)
    html = html_of(run_dir)

    assert report.diff is not None
    assert [(f.path, f.status, f.added, f.removed) for f in report.diff.files] == [
        ("app/new.py", "added", 2, 0),
        ("app/store.py", "modified", 2, 1),
    ]
    assert (report.diff.added, report.diff.removed) == (4, 1)
    assert (
        '<span class="add">+NEW = 2</span>' in html and '<span class="del">-OLD = 1</span>' in html
    )


def test_a_run_without_a_patch_says_so(tmp_path: Path) -> None:
    directory = run_dir_in(tmp_path)
    write_manifest(directory)

    report = build_report(directory)

    assert report.diff is None and "changes.patch" in report.diff_note
    assert "changes.patch" in html_of(directory)


def test_the_environment_block_lists_versions_and_no_secrets(run_dir: Path) -> None:
    (run_dir / "settings.json").write_text(
        '{"provider": "openai", "profile": "smoke", "strategy": "pipeline",'
        ' "execution": {"backend": "docker"}, "web": {"api_key": "sk-leak-12345678"}}',
        encoding="utf-8",
    )

    env = dict(build_report(run_dir).environment)
    html = html_of(run_dir)

    assert env["crewai"] == "1.15.0" and env["Sandbox"] == "docker" and env["Provider"] == "openai"
    assert "sk-leak" not in html


def test_unreproduced_and_unavailable_runs_warn(tmp_path: Path) -> None:
    directory = run_dir_in(tmp_path)
    write_manifest(directory, verdict="partial", status="failed")
    write_state(
        directory,
        fix={"allow_unreproduced": True, "attempts": 3},
        needs_info=["Which version?"],
        checks=[check("tests", "unavailable", exit_code=None, hint="no runner")],
    )

    report = build_report(directory)

    assert any("not reproduced" in w.text for w in report.warnings)
    assert any("Question for you: Which version?" in r for r in report.banner.reasons)
    assert report.banner.label == "Partially verified"


# -- escaping ------------------------------------------------------------------------------------

PAYLOAD = "<script>alert('xss')</script>"


def test_everything_derived_from_agents_or_the_repo_is_escaped(tmp_path: Path) -> None:
    directory = run_dir_in(tmp_path)
    write_manifest(directory, status="failed", verdict="failed")
    write_events(
        directory,
        [
            (1, "tool.call", {"agent": PAYLOAD, "tool": PAYLOAD, "ok": True}),
            (2, "run.finished", {"status": "failed", "error": PAYLOAD}),
        ],
    )
    write_board(
        directory,
        [card("K-001", PAYLOAD, "failed", blocked_reason=PAYLOAD, progress_note=PAYLOAD)],
    )
    write_state(
        directory,
        checks=[check("tests", "failed", log_tail=f"{PAYLOAD}\n</pre>{PAYLOAD}", hint=PAYLOAD)],
        findings=[finding(summary=PAYLOAD, file=PAYLOAD, suggested_fix=PAYLOAD)],
        verification={"coverage": [{"id": "AC-1", "text": PAYLOAD, "note": PAYLOAD}]},
        needs_info=[PAYLOAD],
    )
    (directory / "request.md").write_text(PAYLOAD, encoding="utf-8")
    (directory / "changes.patch").write_text(
        f"diff --git a/{PAYLOAD} b/{PAYLOAD}\n@@ -1 +1 @@\n-{PAYLOAD}\n+{PAYLOAD}\n",
        encoding="utf-8",
    )

    html = html_of(directory)
    markdown = write_report(directory, "md").read_text(encoding="utf-8")

    assert "<script>alert" not in html
    assert html.count("<script>") == 1  # the one inline script of the theme toggle
    assert "&lt;script&gt;alert(&#x27;xss&#x27;)&lt;/script&gt;" in html
    outside_fences = re.sub(r"(`{3,}).*?\1", "", markdown, flags=re.DOTALL)
    assert "<script>" not in outside_fences and "&lt;script&gt;" in outside_fences
    assert "</pre>" not in outside_fences


def test_a_closing_tag_in_a_log_cannot_end_the_page_structure(tmp_path: Path) -> None:
    directory = run_dir_in(tmp_path)
    write_manifest(directory, status="failed")
    write_state(
        directory, checks=[check("t", "failed", log_tail="</pre></details></section><h1>x")]
    )

    html = html_of(directory)

    assert "&lt;/pre&gt;&lt;/details&gt;" in html and "<h1>x" not in html


# -- bounds ------------------------------------------------------------------------------------


def test_a_huge_log_is_cut_with_a_note_that_names_the_full_log(tmp_path: Path) -> None:
    directory = run_dir_in(tmp_path)
    write_manifest(directory, status="failed")
    log = "".join(f"line {i}\n" for i in range(200_000))  # about 1.9 MB
    write_state(directory, checks=[check("t", "failed", log_tail=log, log_path="commands/t.log")])

    html = html_of(directory)

    assert len(html) < 200_000
    assert "earlier characters omitted; full log: commands/t.log" in html
    assert "line 199999" in html and "line 100 " not in html  # the end is kept, not the start
    assert len(log) > MAX_LOG_CHARS


def test_a_huge_diff_is_bounded_per_file_and_names_the_patch() -> None:
    body = "".join(f"+line {i}\n" for i in range(MAX_FILE_LINES * 3))
    view = parse_patch(f"diff --git a/big.py b/big.py\n@@ -0,0 +1,9 @@\n{body}", "changes.patch")

    assert view is not None
    big = view.files[0]
    assert big.added == MAX_FILE_LINES * 3 + 0 and len(big.lines) == MAX_FILE_LINES
    assert big.omitted == MAX_FILE_LINES * 2 + 1 and view.truncated  # the @@ line is shown too


def test_a_very_long_diff_line_is_clipped() -> None:
    view = parse_patch("diff --git a/m.js b/m.js\n@@ -0,0 +1 @@\n+" + "x" * 50_000, "p")

    assert view is not None and len(view.files[0].lines[1]) < 1000


def test_the_patch_parser_handles_binary_deleted_and_renamed_files() -> None:
    patch = (
        "diff --git a/i.png b/i.png\nnew file mode 100644\nGIT binary patch\nliteral 3\nabc\n\n"
        "diff --git a/gone.py b/gone.py\ndeleted file mode 100644\n@@ -1 +0,0 @@\n-x\n"
        "diff --git a/a.py b/b.py\nsimilarity index 90%\nrename from a.py\nrename to b.py\n"
    )

    view = parse_patch(patch, "p")

    assert view is not None
    by_path = {f.path: f for f in view.files}
    assert by_path["i.png"].binary and by_path["i.png"].status == "added"
    assert by_path["gone.py"].status == "deleted" and by_path["gone.py"].removed == 1
    assert by_path["b.py"].status == "renamed"


def test_text_that_is_not_a_patch_is_no_diff() -> None:
    assert parse_patch("just some text\n", "p") is None


# -- screenshots -------------------------------------------------------------------------------


def test_screenshots_are_embedded_without_any_url(run_dir: Path) -> None:
    (run_dir / "screenshots").mkdir()
    (run_dir / "screenshots" / "qa-001-home.png").write_bytes(PNG)
    write_events(
        run_dir,
        [
            screenshot_event(
                30, "screenshots/qa-001-home.png", url="http://localhost:3000/", agent="qa"
            )
        ],
    )

    html = html_of(run_dir)

    assert 'src="data:image/png;base64,' in html and "qa-001-home.png" in html
    assert 'src="http' not in html


def test_a_screenshot_event_cannot_point_outside_the_screenshots_folder(run_dir: Path) -> None:
    (run_dir.parent / "secret.png").write_bytes(PNG)
    write_events(
        run_dir,
        [
            screenshot_event(30, "../secret.png"),
            screenshot_event(31, "/etc/passwd"),
            screenshot_event(32, "screenshots/../../x.png"),
        ],
    )

    assert build_report(run_dir).screenshots == []


def test_a_symlinked_screenshot_is_not_followed(run_dir: Path) -> None:
    (run_dir / "screenshots").mkdir()
    target = run_dir.parent / "outside.png"
    target.write_bytes(PNG)
    (run_dir / "screenshots" / "link.png").symlink_to(target)

    assert build_report(run_dir).screenshots == []
    assert "data:image/png" not in html_of(run_dir)


# -- partial runs, ordering, safety --------------------------------------------------------------


def test_a_run_that_has_only_a_manifest_still_gets_a_report(tmp_path: Path) -> None:
    directory = run_dir_in(tmp_path)
    write_manifest(directory, status="running", finished=None, stages=[], summary=None)

    html = html_of(directory)

    assert "Running" in html and "No stages ran." in html


def test_a_cancelled_run_is_a_warning_with_the_stage_that_stopped(tmp_path: Path) -> None:
    directory = run_dir_in(tmp_path)
    stage = StageRecord(
        name="implement", status="cancelled", started=at(5), finished=at(9), detail="cancelled"
    )
    write_manifest(directory, status="cancelled", stages=[stage])

    report = build_report(directory)

    assert (report.banner.label, report.banner.tone) == ("Cancelled", "warn")
    assert report.banner.reasons == ["Stage implement cancelled: cancelled"]


def test_a_run_with_unreadable_files_still_reports(tmp_path: Path) -> None:
    directory = run_dir_in(tmp_path)
    write_manifest(directory)
    for name in ("board.json", "pipeline.json", "usage.json", "events.jsonl"):
        (directory / name).write_text("{ not json", encoding="utf-8")

    html = html_of(directory)

    assert 'id="timeline"' in html


def test_a_directory_that_is_not_a_run_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(RunNotFound):
        write_report(tmp_path, "html")
    assert write_run_report(tmp_path) is None  # the run's end never raises


def test_the_report_is_deterministic(run_dir: Path) -> None:
    first = html_of(run_dir)
    second = html_of(run_dir)

    assert first == second
    assert write_report(run_dir, "md").read_text() == write_report(run_dir, "md").read_text()


def test_ordering_does_not_depend_on_event_order(tmp_path: Path) -> None:
    """Lanes finishing in a different order, teammates appearing in a different order: the same
    report."""

    def make(name: str, order: list[int]) -> str:
        directory = run_dir_in(tmp_path / name)
        write_manifest(directory)
        events = [
            lane_started(30, 1, "WP-1"),
            lane_started(30, 2, "WP-2"),
            lane_finished(40, 1, "WP-1"),
            lane_finished(40, 2, "WP-2"),
            tool_call(41, "qa", "A"),
            tool_call(41, "backend", "B"),
        ]
        write_events(directory, [events[i] for i in order])
        return html_of(directory).replace(str(tmp_path / name), "")  # only the path differs

    forward = make("a", [0, 1, 2, 3, 4, 5])
    shuffled = make("b", [1, 0, 3, 2, 5, 4])

    assert forward == shuffled


def test_a_failing_report_never_fails_the_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from engineering_team.report import write as write_module

    directory = run_dir_in(tmp_path)
    write_manifest(directory)
    monkeypatch.setattr(write_module, "build_report", lambda _: 1 / 0)

    assert write_module.write_run_report(directory) is None


def test_known_secret_values_are_scrubbed_from_the_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SOME_API_KEY", "supersecretvalue123")
    directory = run_dir_in(tmp_path)
    write_manifest(directory, status="failed")
    write_state(directory, checks=[check("t", "failed", log_tail="token=supersecretvalue123")])

    for fmt in ("html", "md"):
        text = write_report(directory, fmt).read_text(encoding="utf-8")
        assert "supersecretvalue123" not in text and "[REDACTED]" in text


def test_an_unknown_format_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="html, md"):
        write_report(tmp_path, "pdf")
