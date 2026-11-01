"""Reflection and the experience library (T20): parsing a reflection, storing lessons and their
index, ranking them for a run, the controller's discard rules, the leak guard and review."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from engineering_team.cli.app import app
from engineering_team.contracts import utc_now
from engineering_team.harness.domains import Domain
from engineering_team.harness.library import resolve
from engineering_team.learning import reflect
from engineering_team.learning.evidence import RunEvidence, trace_summary
from engineering_team.learning.guard import Protected, leak_in
from engineering_team.learning.lessons import (
    BehaviourRow,
    Lesson,
    LessonEvidence,
    ReflectionAnswer,
    ReflectionError,
    lesson_id,
    parse_reflection,
)
from engineering_team.learning.library import (
    ENTRY_FILE,
    INDEX_FILE,
    LessonLibrary,
    domain_folder,
    user_library,
)
from engineering_team.learning.retrieve import (
    LABEL,
    LESSONS_FILE,
    choose_lessons,
    covering,
    lessons_section,
    rank,
)
from engineering_team.learning.vet import vet
from engineering_team.runtime.context import RunContext
from engineering_team.runtime.events import read_events
from engineering_team.runtime.trajectory import TrajectoryEntry
from engineering_team.settings import load_settings
from engineering_team.tools.workspace import ProjectWorkspace

runner = CliRunner()
DOMAIN = "bugfix/python"
SECRET = "sk-" + "a1b2c3d4e5f6a7b8c9d0e1f2"  # shaped like a provider key; not one
HIDDEN = "def check_add_keeps_the_earlier_notes():\n    assert run_cli('add b') == ['a', 'b']\n"

ROWS = [
    {
        "behaviour": "The debugger fixed the code before writing a failing test.",
        "guidance": "skills/reproduce-first",
        "effect": "The first fix left check repro failing; a repair round was needed.",
        "verdict": "hurt",
        "evidence": ["repro", "#3"],
    },
    {
        "behaviour": "The repair re-ran the reproduction before answering.",
        "guidance": "stages/repair_fix",
        "effect": "repro passed after the repair.",
        "verdict": "helped",
        "evidence": ["repro"],
    },
    {
        "behaviour": "Two files were changed in one step.",
        "guidance": "",
        "effect": "Cannot tell which change mattered.",
        "verdict": "confounded",
        "evidence": [],
    },
]
LESSON = {
    "cue": "a bugfix in python where the first fix leaves the reproduction failing",
    "lesson": "Add to stages/fix: run the reproduction command before declaring the fix done.",
    "component": "stages/fix",
    "evidence": {"rows": [1, 2], "checks": ["repro"]},
    "confidence": "medium",
}


def answer(**changes: Any) -> ReflectionAnswer:
    data: dict[str, Any] = {"rows": ROWS, "lesson": {**LESSON, **changes}}
    return parse_reflection(data)


def run_evidence(**fields: Any) -> RunEvidence:
    values: dict[str, Any] = {
        "run_id": "20261101-120000-abcdef",
        "domain": DOMAIN,
        "mode": "fix",
        "verdict": "verified",
        "check_ids": {"repro", "tests"},
        "seqs": {1, 2, 3},
        "revision": "tree-1",
    }
    return RunEvidence(**{**values, **fields})


@pytest.fixture(scope="module")
def harness() -> Any:
    return resolve(Domain("bugfix", "python"))


def lesson(cue: str, *, domain: str = DOMAIN, age: int = 0, text: str = "") -> Lesson:
    text = text or f"Add to stages/fix: think about {cue}."
    return Lesson(
        id=lesson_id(domain, cue, text),
        domain=domain,
        cue=cue,
        lesson=text,
        component="stages/fix",
        confidence="medium",
        evidence=LessonEvidence(run_id="r1", verdict="verified", check_ids=["repro"]),
        created=utc_now() - timedelta(minutes=age),
    )


# -- parsing a reflection --------------------------------------------------------------------


def test_a_reflection_with_rows_and_a_lesson_parses_from_json_text_in_a_fence() -> None:
    raw = f"Here it is:\n```json\n{json.dumps({'rows': ROWS, 'lesson': LESSON})}\n```"

    parsed = parse_reflection(raw)

    assert [r.verdict for r in parsed.rows] == ["hurt", "helped", "confounded"]
    assert parsed.lesson is not None and parsed.lesson.evidence.rows == [1, 2]
    assert parsed.lesson.component == "stages/fix" and parsed.lesson.confidence == "medium"
    assert parse_reflection(parsed) is parsed  # a structured answer passes through


@pytest.mark.parametrize(
    "raw",
    ["NO_ENTRY", "`NO_ENTRY`", {"rows": ROWS, "lesson": "NO_ENTRY"}, {"rows": [], "lesson": None}],
)
def test_no_entry_in_every_form_means_no_lesson(raw: Any) -> None:
    assert parse_reflection(raw).lesson is None


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("I learned nothing.", "no JSON object"),
        ("{not json}", "not valid JSON"),
        ({"rows": [{"behaviour": "x", "effect": "y", "verdict": "great"}]}, "rows.0.verdict"),
        ({"rows": [], "lesson": "maybe"}, "NO_ENTRY"),
    ],
)
def test_a_malformed_reflection_is_an_error_naming_the_problem(raw: Any, message: str) -> None:
    with pytest.raises(ReflectionError, match=message):
        parse_reflection(raw)


# -- storage and index -----------------------------------------------------------------------


def test_a_lesson_is_a_folder_with_experience_md_and_the_index_is_rebuilt(tmp_path: Path) -> None:
    library = LessonLibrary(tmp_path / "library")
    item = lesson("a failing reproduction after the first fix")

    folder = library.add(item)

    assert folder == tmp_path / "library" / "bugfix" / "python" / item.id
    text = (folder / ENTRY_FILE).read_text(encoding="utf-8")
    assert text.startswith("---\n") and "## Cue" in text and "## Lesson" in text
    index = (folder.parent / INDEX_FILE).read_text(encoding="utf-8")
    assert item.id in index and "a failing reproduction" in index
    [loaded], problems = library.load()
    assert problems == [] and loaded == item
    assert library.add(item) is None  # the same lesson is not stored twice


def test_a_person_may_edit_the_cue_and_a_broken_entry_is_one_line_per_problem(
    tmp_path: Path,
) -> None:
    library = LessonLibrary(tmp_path / "library")
    entry = library.add(lesson("first cue")) / ENTRY_FILE  # type: ignore[operator]
    entry.write_text(entry.read_text().replace("first cue", "an edited cue"), encoding="utf-8")
    broken = library.folder(DOMAIN) / "les-00000000" / ENTRY_FILE
    broken.parent.mkdir()
    broken.write_text("---\nid: les-00000000\nconfidence: sure\n---\n## Cue\n\nx\n")

    lessons, problems = library.load()

    assert [item.cue for item in lessons] == ["an edited cue"]
    assert problems and all(line.startswith(str(broken)) for line in problems)
    assert any("'## Lesson' section is missing" in line for line in problems)


def test_wildcard_domains_get_an_any_folder_and_each_domain_is_capped(tmp_path: Path) -> None:
    assert domain_folder("bugfix/*") == Path("bugfix/any")
    library = LessonLibrary(tmp_path / "library", max_lessons=10)
    for number in range(12):
        library.add(lesson(f"cue number {number}", age=100 - number))

    lessons, _ = library.load((DOMAIN,))

    assert len(lessons) == 10
    assert "cue number 0" not in {item.cue for item in lessons}  # the oldest went first
    assert library.remove(lessons[0].id) == lessons[0].id
    assert lessons[0].id not in (library.folder(DOMAIN) / INDEX_FILE).read_text()


# -- retrieval -------------------------------------------------------------------------------


def test_lessons_are_ranked_by_bm25_over_their_cue_and_unrelated_ones_are_left_out() -> None:
    flaky = lesson("a flaky pytest test that fails once on an unchanged tree")
    repro = lesson("a reproduction that keeps failing after the first fix")
    docs = lesson("a README section about installation")

    best = rank([docs, flaky, repro], "the fix leaves the reproduction failing on this tree", 5)

    assert best == [repro, flaky]  # docs shares no word with the query


def test_a_run_reads_its_domain_and_the_less_specific_ones_capped_and_labelled(
    tmp_path: Path,
) -> None:
    library = LessonLibrary(user_library())  # type: ignore[arg-type]
    for item in (
        lesson("a failing reproduction in python", age=3),
        lesson("a failing reproduction in any stack", domain="bugfix/*", age=2),
        lesson("a failing reproduction in typescript", domain="bugfix/typescript", age=1),
        lesson("a failing reproduction in a python feature", domain="feature/python"),
    ):
        library.add(item)
    settings = load_settings(overrides={"learning.inject_lessons": 2})
    workspace = ProjectWorkspace.create(tmp_path / "project")
    ctx = RunContext.create(settings, workspace, harness=resolve(Domain("bugfix", "python")))

    ids = choose_lessons(ctx, "The reproduction is failing in python.")

    assert covering(Domain("bugfix", "python")) == (
        "bugfix/python",
        "bugfix/*",
        "*/python",
        "*/*",
    )
    assert len(ids) == 2
    section = lessons_section(ctx)
    assert section.startswith(LABEL) and "typescript" not in section and "feature" not in section
    assert json.loads((ctx.run_dir / LESSONS_FILE).read_text())["ids"] == ids
    events = read_events(ctx.run_dir / "events.jsonl")
    injected = [e for e in events if e.type == "lessons.injected"]
    assert injected[0].data["lessons"] == ids


def test_learning_off_shows_no_lessons(tmp_path: Path) -> None:
    LessonLibrary(user_library()).add(lesson("a failing reproduction"))  # type: ignore[arg-type]
    settings = load_settings(overrides={"learning.enabled": False})
    ctx = RunContext.create(
        settings, ProjectWorkspace.create(tmp_path / "p"), harness=resolve(Domain("bugfix", "*"))
    )

    assert choose_lessons(ctx, "a failing reproduction") == [] and lessons_section(ctx) == ""


# -- the controller's discard rules ----------------------------------------------------------


def test_a_well_founded_lesson_is_kept_with_the_controllers_evidence(harness: Any) -> None:
    vetted = vet(answer(), run_evidence(), harness, Protected())

    assert vetted.lesson is not None, vetted.reason
    kept = vetted.lesson
    assert kept.domain == DOMAIN and kept.evidence.verdict == "verified"
    assert kept.evidence.check_ids == ["repro"] and [r.verdict for r in kept.evidence.rows] == [
        "hurt",
        "helped",
    ]


@pytest.mark.parametrize(
    ("changes", "evidence", "reason"),
    [
        ({}, {"verdict": "partial"}, "no verdict with checks"),
        ({}, {"check_ids": set()}, "no verdict with checks"),
        ({"evidence": {"rows": [9], "checks": ["repro"]}}, {}, "row(s) [9]"),
        ({"evidence": {"rows": [], "checks": ["repro"]}}, {}, "no behaviour row"),
        ({"evidence": {"rows": [3], "checks": ["repro"]}}, {}, "only on confounded rows"),
        ({"evidence": {"rows": [1, 2], "checks": ["hidden-1"]}}, {}, "never ran: hidden-1"),
        ({}, {"seqs": {1, 2}}, "trajectory entries that do not exist"),
        ({"component": "the fix prompt"}, {}, "not a harness path"),
        ({"component": "teammates/wizard/tool_guidance"}, {}, "not part of this run's harness"),
        ({"lesson": f"Set OPENAI_API_KEY={SECRET} before the tests."}, {}, "credential"),
    ],
)
def test_lessons_without_sound_evidence_are_discarded(
    harness: Any, changes: dict[str, Any], evidence: dict[str, Any], reason: str
) -> None:
    vetted = vet(answer(**changes), run_evidence(**evidence), harness, Protected())

    assert vetted.lesson is None and reason in vetted.reason


def test_a_lesson_resting_only_on_rows_without_an_effect_is_discarded(harness: Any) -> None:
    rows = [{**ROWS[1], "verdict": "no observed effect"}]
    raw = {"rows": rows, "lesson": {**LESSON, "evidence": {"rows": [1], "checks": ["repro"]}}}

    vetted = vet(parse_reflection(raw), run_evidence(), harness, Protected())

    assert vetted.lesson is None and "saw an effect" in vetted.reason


def test_a_new_skill_may_be_named_and_no_entry_is_not_a_discard(harness: Any) -> None:
    kept = vet(answer(component="skills/run-the-repro"), run_evidence(), harness, Protected())
    nothing = vet(ReflectionAnswer(), run_evidence(), harness, Protected())

    assert kept.lesson is not None and nothing.lesson is None and nothing.reason == "NO_ENTRY"


# -- the leak guard --------------------------------------------------------------------------


def test_no_lesson_may_quote_a_benchmarks_hidden_checks(harness: Any, tmp_path: Path) -> None:
    hidden = tmp_path / "task" / "acceptance"
    hidden.mkdir(parents=True)
    (hidden / "checks.py").write_text(HIDDEN, encoding="utf-8")
    protected = Protected.from_paths([hidden, tmp_path / "missing"])
    quoting = "Add to stages/fix: make sure assert run_cli('add b') == ['a', 'b'] holds."
    paraphrase = (
        "Add to stages/fix: check that adding a note keeps the earlier notes in order to pass "
        "the acceptance checks of the notes tool and its adding command"
    )

    assert leak_in(quoting, protected) is not None
    assert leak_in("Run the reproduction first.", protected) is None
    vetted = vet(answer(lesson=quoting), run_evidence(), harness, protected)
    assert vetted.lesson is None and "protected file" in vetted.reason
    words = " ".join(HIDDEN.split()[1:10])  # nine words in a row of the hidden file
    assert leak_in(f"{paraphrase} {words}", protected) is not None


def test_the_bench_protects_its_tasks_hidden_checks_and_reference() -> None:
    from engineering_team.bench.prepare import protected_paths
    from engineering_team.bench.tasks import load_suite

    task = load_suite(Path(__file__).resolve().parents[1] / "benchmarks")[0]

    assert protected_paths(task) == [str(task.root / "acceptance"), str(task.root / "reference")]


# -- the trace summary -----------------------------------------------------------------------


def test_the_trace_summary_keeps_the_start_and_the_end_of_a_long_run() -> None:
    entries = [
        TrajectoryEntry(
            seq=n, ts=utc_now(), kind="tool", stage="fix", teammate="debugger", tool="Read File"
        )
        for n in range(1, 400)
    ]

    text = trace_summary(entries, 2000)

    assert len(text) < 2300 and "#1 debugger" in text and "#399 debugger" in text
    assert "left out" in text


# -- skipping the stage ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "verdict", "reason"),
    [
        ({"learning.reflect": False}, "verified", "learning.reflect is off"),
        ({"learning.enabled": False}, "verified", "learning is off"),
        ({}, "partial", "verdict is partial"),
        ({"budget.max_cost_usd": 0.1}, "verified", "under learning.min_budget_usd"),
    ],
)
def test_reflection_is_skipped_when_it_cannot_teach_or_the_budget_is_very_low(
    tmp_path: Path, overrides: dict[str, Any], verdict: str, reason: str
) -> None:
    ctx = RunContext.create(
        load_settings(overrides=overrides), ProjectWorkspace.create(tmp_path / "p")
    )

    assert reason in (reflect.skip_reason(ctx, verdict) or "")  # type: ignore[arg-type]


def test_the_worst_case_of_the_call_must_fit_what_is_left(tmp_path: Path) -> None:
    ctx = RunContext.create(
        load_settings(overrides={"budget.max_tokens": 2000}),
        ProjectWorkspace.create(tmp_path / "p"),
    )

    assert reflect.budget_problem(ctx, "openai/gpt-6-luna", 30_000) is not None
    assert reflect.budget_problem(ctx, "openai/gpt-6-luna", 300) is None


# -- manual review ---------------------------------------------------------------------------


def test_in_manual_review_a_lesson_waits_until_it_is_approved(tmp_path: Path) -> None:
    settings = load_settings(overrides={"learning.review": "manual"})
    ctx = RunContext.create(
        settings, ProjectWorkspace.create(tmp_path / "p"), harness=resolve(Domain("bugfix", "*"))
    )
    item = lesson("a failing reproduction", domain="bugfix/*")

    outcome, _ = reflect.store(ctx, item)

    assert outcome == "pending"
    assert choose_lessons(ctx, "a failing reproduction") == []  # pending lessons are not shown
    listed = runner.invoke(app, ["lessons", "list", "--pending"])
    assert listed.exit_code == 0 and item.id in listed.output
    assert runner.invoke(app, ["lessons", "list"]).output.startswith("No lessons")
    approved = runner.invoke(app, ["lessons", "approve", item.id[:7]])
    assert approved.exit_code == 0, approved.output
    shown = runner.invoke(app, ["--json", "lessons", "show", item.id])
    data = json.loads(shown.output)
    assert data["review"] == "approved" and data["pending"] is False
    assert choose_lessons(ctx, "a failing reproduction") == [item.id]
    assert runner.invoke(app, ["lessons", "approve", item.id]).exit_code != 0  # not pending now
    assert runner.invoke(app, ["lessons", "forget", item.id]).exit_code == 0
    assert runner.invoke(app, ["--json", "lessons", "list"]).output.strip() == "[]"


def test_the_repository_library_is_written_when_asked_and_listed_with_repo(
    tmp_path: Path,
) -> None:
    settings = load_settings(overrides={"learning.library": "repo"})
    ctx = RunContext.create(settings, ProjectWorkspace.create(tmp_path / "p"))
    item = lesson("a failing reproduction", domain="bugfix/*")

    assert reflect.store(ctx, item)[0] == "stored"

    assert (tmp_path / "p" / ".engineering-team" / "library" / "bugfix" / "any" / item.id).is_dir()
    listed = runner.invoke(app, ["lessons", "list", "--repo", str(tmp_path / "p")])
    assert item.id in listed.output


def test_a_row_list_is_capped_and_one_line_each(harness: Any) -> None:
    long = BehaviourRow(behaviour="x\n" * 400, effect="y", verdict="helped")
    raw = ReflectionAnswer(rows=[long] * 12, lesson=None)

    from engineering_team.learning.vet import clean_rows

    rows = clean_rows(raw.rows)
    assert len(rows) == 8 and "\n" not in rows[0].behaviour and len(rows[0].behaviour) <= 300
