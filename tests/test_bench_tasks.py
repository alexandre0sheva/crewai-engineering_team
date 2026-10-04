"""The task format, the loader's errors, and the integrity of the shipped suite."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
import yaml

from engineering_team.bench.fake_team import reference_files
from engineering_team.bench.tasks import (
    BenchError,
    BenchTask,
    copy_tree,
    load_suite,
    load_task,
    select_tasks,
)

SUITE = Path(__file__).resolve().parents[1] / "benchmarks"
pytestmark = pytest.mark.skipif(not (SUITE / "tasks").is_dir(), reason="no benchmarks/ directory")
MAX_FIXTURE_LINES = 330  # "each <= ~300 LOC fixtures"


@pytest.fixture(scope="module")
def tasks() -> list[BenchTask]:
    return load_suite(SUITE)


def test_the_suite_has_six_greenfield_and_four_brownfield_tasks(tasks: list[BenchTask]) -> None:
    ids = [t.id for t in tasks]

    assert ids == sorted(set(ids))
    assert sorted(t.id for t in tasks if t.kind == "greenfield") == [
        "cron-scheduler", "csv-validator", "markdown-html", "notes-cli", "todo-web",
        "url-shortener",
    ]  # fmt: skip
    assert sorted(t.mode for t in tasks if t.kind == "brownfield") == [
        "feature", "fix", "maintain", "maintain",
    ]  # fmt: skip


def test_some_tasks_are_held_out_for_tuning_honesty(tasks: list[BenchTask]) -> None:
    held = {t.id for t in tasks if t.subset == "heldout"}

    assert held
    assert len(held) < len(tasks)
    assert {t.id for t in select_tasks(tasks, None, "dev")}.isdisjoint(held)


def test_every_criterion_has_a_check_function(tasks: list[BenchTask]) -> None:
    for task in tasks:
        defined = {
            node.name
            for node in ast.walk(ast.parse(task.checks_file.read_text()))
            if isinstance(node, ast.FunctionDef)
        }
        wanted = {f"check_{c.id.replace('-', '_')}" for c in task.criteria}
        assert wanted <= defined, f"{task.id}: missing {sorted(wanted - defined)}"
        extra = {n for n in defined if n.startswith("check_")} - wanted
        assert not extra, f"{task.id}: checks without a criterion in task.yaml: {sorted(extra)}"


def test_requests_never_mention_the_hidden_checks(tasks: list[BenchTask]) -> None:
    for task in tasks:
        text = task.request_path.read_text().lower()
        for word in ("acceptance", "checks.py", "hidden", "benchmark", "criterion"):
            assert word not in text, f"{task.id}: the request mentions {word!r}"


def test_nothing_the_team_can_see_contains_the_checks(tasks: list[BenchTask]) -> None:
    for task in tasks:
        visible = [*task.fixture_dir.rglob("*"), *task.reference_dir.rglob("*")]
        for path in (p for p in visible if p.is_file() and p.suffix in (".py", ".md", ".txt")):
            assert "checklib" not in path.read_text(), f"{path} refers to the checks library"


def test_fixtures_are_small(tasks: list[BenchTask]) -> None:
    for task in tasks:
        if task.kind != "brownfield":
            continue
        lines = sum(len(p.read_text().splitlines()) for p in task.fixture_dir.rglob("*.py"))
        assert 20 < lines <= MAX_FIXTURE_LINES, f"{task.id} fixture has {lines} lines of code"


def test_every_sabotage_applies_exactly_once_and_changes_the_reference(
    tasks: list[BenchTask],
) -> None:
    for task in tasks:
        good, bad = reference_files(task), reference_files(task, "broken")

        assert good.keys() == bad.keys()
        assert {p for p in good if good[p] != bad[p]} == {e.path for e in task.sabotage.edits}


def test_a_sabotage_that_does_not_apply_is_an_error(tasks: list[BenchTask]) -> None:
    task = tasks[0]
    broken = task.model_copy(
        update={"sabotage": task.sabotage.model_copy(update={"edits": [
            task.sabotage.edits[0].model_copy(update={"old": "text that is not there"})
        ]})}
    )  # fmt: skip

    with pytest.raises(BenchError, match="must occur once"):
        reference_files(broken, "broken")
    with pytest.raises(BenchError, match="'reference' or 'broken'"):
        reference_files(task, "perfect")


def test_greenfield_references_are_complete_projects(tasks: list[BenchTask]) -> None:
    for task in (t for t in tasks if t.kind == "greenfield"):
        files = reference_files(task)
        assert any(name.startswith("tests/") for name in files), f"{task.id} has no tests"
        assert "README.md" in files


def test_only_new_projects_can_use_any_strategy(tasks: list[BenchTask]) -> None:
    for task in tasks:
        assert task.applies_to("pipeline")
        assert task.applies_to("single") == (task.mode == "new")
        assert task.applies_to("hierarchical") == (task.mode == "new")


# -- selection and loader errors ----------------------------------------------------------------


def test_select_by_id_and_subset(tasks: list[BenchTask]) -> None:
    assert [t.id for t in select_tasks(tasks, ["notes-cli,todo-web"])] == ["notes-cli", "todo-web"]
    assert [t.id for t in select_tasks(tasks, ["all"], "heldout")] == [
        t.id for t in tasks if t.subset == "heldout"
    ]
    with pytest.raises(BenchError, match="Unknown task"):
        select_tasks(tasks, ["nope"])
    with pytest.raises(BenchError, match="No task matches"):
        select_tasks(tasks, ["notes-cli"], "heldout")


def _write_task(root: Path, **changes: object) -> Path:
    directory = root / "tasks" / "demo"
    (directory / "acceptance").mkdir(parents=True)
    (directory / "reference").mkdir()
    (directory / "reference" / "a.py").write_text("x = 1\n")
    (directory / "acceptance" / "checks.py").write_text("pass\n")
    (directory / "request.md").write_text("Do it.\n")
    data: dict[str, object] = {
        "id": "demo", "title": "Demo", "kind": "greenfield", "mode": "new",
        "criteria": [{"id": "works", "text": "it works"}],
        "sabotage": {"edits": [{"path": "a.py", "old": "1", "new": "2"}], "fails": ["works"]},
    }  # fmt: skip
    data.update(changes)
    (directory / "task.yaml").write_text(yaml.safe_dump(data))
    return directory


def test_a_valid_minimal_task_loads(tmp_path: Path) -> None:
    task = load_task(_write_task(tmp_path))

    assert task.id == "demo" and task.timeout_seconds == 1800 and task.subset == "dev"
    assert load_suite(tmp_path)[0].id == "demo"


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"id": "other"}, "must equal the directory name"),
        ({"kind": "brownfield", "mode": "feature"}, "needs a fixture"),
        ({"mode": "fix"}, "greenfield tasks use mode 'new'"),
        ({"sabotage": {"edits": [{"path": "a.py", "old": "1", "new": "2"}], "fails": ["zzz"]}},
         "unknown criterion"),
        ({"criteria": [{"id": "a", "text": "x"}, {"id": "a", "text": "y"}]}, "unique"),
        ({"surprise": 1}, "surprise"),
        ({"id": "Bad_ID"}, "must match"),
    ],
)  # fmt: skip
def test_invalid_tasks_are_refused_with_the_file_named(
    tmp_path: Path, changes: dict[str, object], message: str
) -> None:
    directory = _write_task(tmp_path, **changes)
    if changes.get("id") == "Bad_ID":
        directory = directory.rename(directory.parent / "Bad_ID")

    with pytest.raises(BenchError, match=message) as caught:
        load_task(directory)
    assert "task.yaml" in str(caught.value)


def test_a_missing_suite_says_where_to_find_one(tmp_path: Path) -> None:
    with pytest.raises(BenchError, match="source checkout"):
        load_suite(tmp_path / "nothing")


def test_copy_tree_skips_caches_and_controller_state(tmp_path: Path) -> None:
    source = tmp_path / "src"
    for name in ("a.py", "pkg/b.py", "__pycache__/c.pyc", ".engineering-team/runs/x", ".git/HEAD"):
        (source / name).parent.mkdir(parents=True, exist_ok=True)
        (source / name).write_text("x")

    assert copy_tree(source, tmp_path / "dst") == ["a.py", "pkg/b.py"]
    assert not (tmp_path / "dst" / ".git").exists()
