"""Repo memory (T19): the fact lifecycle, invalidation, injection, secrets, the tool and the CLI."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import Toolbox
from typer.testing import CliRunner

from engineering_team.cli.app import app
from engineering_team.contracts import CheckResult
from engineering_team.memory import Evidence, Fact, MemoryFileError, MemoryStore, Source
from engineering_team.memory.facts import fact_id, hash_sources, secret_in
from engineering_team.memory.inject import LABEL, memory_section, select
from engineering_team.memory.observe import finish_run, record_baseline, record_verification
from engineering_team.memory.store import FACTS_FILE, MEMORY_DIRECTORY, VIEW_FILE
from engineering_team.modes.baseline_report import BaselineCheck, BaselineReport
from engineering_team.runtime.context import RunContext
from engineering_team.runtime.events import read_events
from engineering_team.runtime.snapshot import Snapshot, workspace_revision
from engineering_team.runtime.trajectory import read_trajectory
from engineering_team.settings import load_settings

MakeContext = Callable[..., RunContext]
MakeToolbox = Callable[..., Toolbox]
runner = CliRunner()

SECRET = "sk-" + "a1b2c3d4e5f6a7b8c9d0e1f2"  # shaped like a provider key; not one


def store_in(root: Path, **options: object) -> MemoryStore:
    root.mkdir(parents=True, exist_ok=True)
    return MemoryStore(root, **options)  # type: ignore[arg-type]


def fact(topic: str, text: str = "A fact.", **fields: object) -> Fact:
    kind = str(fields.pop("kind", "gotcha"))
    return Fact(id=fact_id(kind, topic), kind=kind, topic=topic, text=text, **fields)  # type: ignore[arg-type]


def baseline(
    *rows: BaselineCheck, revision: str = "tree-1"
) -> BaselineReport:  # the controller's record of the checks it ran before any change
    return BaselineReport(revision=revision, checks=list(rows))


def row(status: str = "passed", **fields: object) -> BaselineCheck:
    values: dict[str, object] = {
        "id": "tests",
        "name": "Tests",
        "kind": "test",
        "directory": ".",
        "status": status,
        "command": "pytest -q",
    }
    return BaselineCheck(**{**values, **fields})  # type: ignore[arg-type]


def events_of(ctx: RunContext, prefix: str = "memory.") -> list[str]:
    return [e.type for e in read_events(ctx.run_dir / "events.jsonl") if e.type.startswith(prefix)]


def files(root: Path, tree: dict[str, str]) -> None:
    for name, text in tree.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


# -- the lifecycle ----------------------------------------------------------------------------


def test_a_fact_goes_from_proposed_to_verified_to_stale_to_removed(tmp_path: Path) -> None:
    files(tmp_path, {"Makefile": "test:\n\tpytest\n"})
    store = store_in(tmp_path)
    sources = hash_sources(tmp_path, ["Makefile"])
    assert not isinstance(sources, str)
    proposed = store.upsert(
        fact("make-test", status="proposed", sources=sources, evidence=Evidence(run_id="r1"))
    )

    assert proposed.status == "proposed" and store.usable() == []  # a claim is shown to nobody

    verified = store.verify(proposed.id, Evidence(run_id="r1", check_ids=["tests"]))
    assert verified is not None and verified.status == "verified"
    assert [f.id for f in store.usable()] == [proposed.id]
    assert store.refresh() == []  # the source is as it was

    (tmp_path / "Makefile").write_text("test:\n\tpytest -x\n")
    stale = store.refresh()
    assert [f.id for f in stale] == [proposed.id]
    assert store.usable() == [] and store.facts()[0].status == "stale"

    assert store.remove(proposed.id, reason="forgotten") is True
    assert store.facts() == [] and store.remove(proposed.id) is False


def test_the_file_and_the_readable_view_are_written_together(tmp_path: Path) -> None:
    store = store_in(tmp_path)
    store.upsert(fact("one", "Run `make db` first.", evidence=Evidence(run_id="r1")))

    directory = tmp_path / MEMORY_DIRECTORY
    assert json.loads((directory / FACTS_FILE).read_text().splitlines()[0])["topic"] == "one"
    assert "Run `make db` first." in (directory / VIEW_FILE).read_text()
    assert str(MEMORY_DIRECTORY) == ".engineering-team/memory"


def test_a_newer_observation_of_a_topic_replaces_and_a_contradiction_removes_it(
    tmp_path: Path,
) -> None:
    emitted: list[str] = []
    store = store_in(tmp_path, emit=lambda event, **_: emitted.append(event))
    store.upsert(fact("t", "Old.", kind="command"))
    store.upsert(fact("t", "New.", kind="command"))

    assert [f.text for f in store.facts()] == ["New."]
    assert store.contradict("command", "t", "it no longer runs") is True
    assert store.facts() == []
    assert emitted == [
        "memory.recorded",
        "memory.replaced",
        "memory.recorded",
        "memory.removed",
    ]


def test_the_store_keeps_at_most_max_facts_dropping_stale_ones_first(tmp_path: Path) -> None:
    store = store_in(tmp_path, max_facts=3)
    store.upsert(fact("a", status="stale"))
    for topic in ("b", "c", "d"):
        store.upsert(fact(topic))

    assert sorted(f.topic for f in store.facts()) == ["b", "c", "d"]


def test_a_missing_source_makes_a_fact_stale(tmp_path: Path) -> None:
    files(tmp_path, {"setup.cfg": "[x]\n"})
    store = store_in(tmp_path)
    sources = hash_sources(tmp_path, ["setup.cfg"])
    assert not isinstance(sources, str)
    store.upsert(fact("cfg", sources=sources))

    (tmp_path / "setup.cfg").unlink()

    assert [f.status for f in (store.refresh() or store.facts())] == ["stale"]


def test_a_damaged_file_names_each_bad_line_and_leaves_the_file_alone(tmp_path: Path) -> None:
    store = store_in(tmp_path)
    store.upsert(fact("ok"))
    good = store.path.read_text()
    store.path.write_text(good + "not json\n" + '{"id": 1}\n')

    with pytest.raises(MemoryFileError) as caught:
        store.facts()

    lines = str(caught.value).splitlines()
    assert len(lines) == 2 and "line 2" in lines[0] and "line 3" in lines[1]
    assert "memory clear" in lines[0]
    assert store.path.read_text() == good + "not json\n" + '{"id": 1}\n'


def test_a_run_over_a_damaged_memory_file_starts_without_memory(make_context: MakeContext) -> None:
    first = make_context("damaged")
    directory = first.workspace.root / MEMORY_DIRECTORY
    directory.mkdir(parents=True)
    (directory / FACTS_FILE).write_text("garbage\n")

    ctx = make_context("damaged")

    assert ctx.memory.enabled is False
    assert "memory.unreadable" in events_of(ctx)
    assert (directory / FACTS_FILE).read_text() == "garbage\n"
    assert memory_section(ctx, "implement") == ("", [])


def test_memory_can_be_switched_off(make_context: MakeContext, tmp_path: Path) -> None:
    ctx = make_context("off", settings=load_settings(overrides={"memory.enabled": False}))

    record_baseline(ctx, baseline(row()))
    ctx.memory.upsert(fact("x"))

    assert ctx.memory.facts() == [] and not (ctx.workspace.root / MEMORY_DIRECTORY).exists()
    assert memory_section(ctx, "implement") == ("", [])


# -- what the controller observes -------------------------------------------------------------


def test_the_baseline_records_the_command_that_ran_and_a_later_failure_to_run_removes_it(
    make_context: MakeContext,
) -> None:
    ctx = make_context("repo")
    files(ctx.workspace.root, {"pyproject.toml": "[project]\nname = 'x'\n"})

    record_baseline(ctx, baseline(row(), row("failed", id="lint", kind="lint", command="ruff .")))

    [recorded] = (
        ctx.memory.facts()
    )  # only what passed; a failing lint says nothing about the command
    assert recorded.kind == "command" and recorded.status == "verified"
    assert "`pytest -q`" in recorded.text
    assert recorded.evidence.check_ids == ["tests"] and recorded.evidence.run_id == ctx.run_id
    assert [s.path for s in recorded.sources] == ["pyproject.toml"]

    record_baseline(ctx, baseline(row("unavailable", summary="pytest is not installed")))
    assert ctx.memory.facts() == []
    assert events_of(ctx) == ["memory.recorded", "memory.removed"]


def test_a_different_command_in_a_later_run_replaces_the_remembered_one(
    make_context: MakeContext,
) -> None:
    ctx = make_context("repo")
    record_baseline(ctx, baseline(row()))
    record_baseline(ctx, baseline(row(command="uv run pytest")))

    [only] = ctx.memory.facts()
    assert "uv run pytest" in only.text


def test_verified_checks_record_passing_commands_but_not_failing_ones(
    make_context: MakeContext,
) -> None:
    ctx = make_context("repo")
    results = [
        CheckResult(id="tests", status="passed", kind="test", command="pytest -q"),
        CheckResult(id="lint", status="failed", kind="lint", command="ruff check ."),
        CheckResult(id="plan", status="passed", kind="test", command="x", source="plan"),
    ]

    record_verification(ctx, {"tests": ".", "lint": "."}, results, verified=True)

    assert [f.topic for f in ctx.memory.facts()] == ["command:test:."]
    assert ctx.memory.verified is True


def test_a_test_that_flips_on_an_unchanged_tree_is_remembered_as_flaky(
    make_context: MakeContext,
) -> None:
    ctx = make_context("repo")
    files(ctx.workspace.root, {"tests/test_a.py": "def test_x():\n    pass\n"})
    key = "test:.:tests/test_a.py::test_x"

    record_baseline(ctx, baseline(row("failed", failing=[key])))
    assert [f.kind for f in ctx.memory.facts()] == []  # nothing to compare with yet

    record_baseline(ctx, baseline(row("passed")))  # the same tree, a different outcome
    flaky = [f for f in ctx.memory.facts() if f.kind == "flaky_test"]
    assert len(flaky) == 1 and "tests/test_a.py::test_x" in flaky[0].text
    assert [s.path for s in flaky[0].sources] == ["tests/test_a.py"]
    assert f"flaky:{key}" in ctx.memory.flipped


def test_a_changed_tree_is_not_a_flip(make_context: MakeContext) -> None:
    ctx = make_context("repo")
    key = "test:.:tests/test_a.py::test_x"

    record_baseline(ctx, baseline(row("failed", failing=[key]), revision="tree-1"))
    record_baseline(ctx, baseline(row("passed"), revision="tree-2"))

    assert [f for f in ctx.memory.facts() if f.kind == "flaky_test"] == []


def test_a_dependency_install_the_trajectory_recorded_becomes_a_setup_fact(
    make_toolbox: MakeToolbox,
) -> None:
    box = make_toolbox("repo")
    ctx = box.ctx
    for directory, output in (
        (".", "Install (uv): PASSED\nduration 3.2s"),
        ("web", "Install (npm): FAILED"),
    ):
        ctx.trajectory.tool_call(
            tool="Install Dependencies",
            args={"working_directory": directory},
            output=output,
            ok=True,
            ms=10,
        )

    finish_run(ctx)

    [setup] = ctx.memory.facts()
    assert setup.kind == "setup" and "(uv)" in setup.text
    [entry] = [e for e in read_trajectory(ctx.trajectory.path) if "uv" in e.output]
    assert setup.evidence.tool_seqs == [entry.seq]  # the controller's record of the call


# -- proposals --------------------------------------------------------------------------------


def test_remember_fact_stores_a_proposal_that_nobody_is_shown(make_toolbox: MakeToolbox) -> None:
    box = make_toolbox("repo", agent="backend_engineer")
    box.write("Makefile", "db:\n\tmigrate\n")

    result = box(
        "Remember Fact",
        kind="gotcha",
        text="Migrations run only through make db.",
        paths="Makefile",
    )

    assert result.startswith("Proposed fact gotcha-")
    [proposed] = box.ctx.memory.facts()
    assert proposed.status == "proposed" and proposed.proposed_by == "backend_engineer"
    assert [s.path for s in proposed.sources] == ["Makefile"]
    assert box.ctx.memory.usable() == [] and memory_section(box.ctx, "implement") == ("", [])


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"kind": "flaky", "text": "x"}, "Unknown kind"),
        ({"kind": "flaky_test", "text": "x"}, "recorded by the controller"),
        ({"kind": "command", "text": "Tests run with pytest."}, "needs command="),
        ({"kind": "gotcha", "text": "Careful."}, "needs paths="),
        ({"kind": "gotcha", "text": "  "}, "text is empty"),
        ({"kind": "gotcha", "text": "x" * 301, "paths": "a.txt"}, "keep it under 300"),
        ({"kind": "gotcha", "text": "Careful.", "paths": "../a.txt"}, "relative path"),
        ({"kind": "gotcha", "text": "Careful.", "paths": ".env"}, "may hold secrets"),
        ({"kind": "gotcha", "text": "Careful.", "paths": "missing.txt"}, "not a readable file"),
        ({"kind": "gotcha", "text": "Careful.", "paths": ".engineering-team/x"}, "not part of"),
    ],
)
def test_remember_fact_refuses_what_it_cannot_check(
    make_toolbox: MakeToolbox, arguments: dict[str, str], message: str
) -> None:
    box = make_toolbox("repo")
    box.write("a.txt", "x\n")
    box.write(".env", "TOKEN=1\n")

    result = box("Remember Fact", **arguments)

    assert result.startswith("ERROR:") and message in result
    assert box.ctx.memory.facts() == []


def test_a_run_may_propose_only_five_facts(make_toolbox: MakeToolbox) -> None:
    box = make_toolbox("repo")
    box.write("a.txt", "x\n")
    for number in range(5):
        assert box("Remember Fact", kind="gotcha", text=f"Fact {number}.", paths="a.txt")[0] == "P"

    assert "At most 5 proposals" in box("Remember Fact", kind="gotcha", text="Six.", paths="a.txt")


def test_memory_never_stores_a_secret(
    make_toolbox: MakeToolbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEPLOY_TOKEN", "zx81-plain-looking-value")
    box = make_toolbox("repo")
    box.write("a.txt", "x\n")

    shaped = box("Remember Fact", kind="gotcha", text=f"Use {SECRET} to deploy.", paths="a.txt")
    named = box("Remember Fact", kind="gotcha", text="Set password=hunter2hunter2.", paths="a.txt")
    known = box(
        "Remember Fact", kind="gotcha", text="Deploy with zx81-plain-looking-value.", paths="a.txt"
    )
    command = box("Remember Fact", kind="setup", text="Log in.", command=f"login --key {SECRET}")
    assert all(r.startswith("ERROR: Not stored") for r in (shaped, named, known, command))

    # the controller's own observations are checked too (a command line may carry a credential)
    record_baseline(
        box.ctx,
        baseline(row(command="curl -H 'Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123'")),
    )
    assert box.ctx.memory.facts() == []
    assert secret_in("Tests run with `pytest -q`.") is None
    assert secret_in("postgres://user:pa55word@db/x") is not None


def test_a_proposal_survives_only_with_the_controllers_evidence(make_toolbox: MakeToolbox) -> None:
    box = make_toolbox("repo")
    ctx = box.ctx
    box.write("README.md", "# Repo\n")
    box.write("docs.md", "# Docs\n")
    box("Remember Fact", kind="command", text="Tests run with pytest -q.", command="pytest -q")
    box("Remember Fact", kind="command", text="The build is make magic.", command="make magic")
    box("Remember Fact", kind="gotcha", text="README is the source of docs.", paths="README.md")
    box("Remember Fact", kind="gotcha", text="docs.md is generated.", paths="docs.md")
    box("Read Project File", path="README.md")  # the agent read one of the two files, not the other
    record_verification(
        ctx,
        {"tests": "."},
        [CheckResult(id="tests", status="passed", kind="test", command="pytest -q")],
        verified=True,
    )

    finish_run(ctx)

    kept = {f.text: f for f in ctx.memory.facts() if f.topic.startswith("proposal:")}
    assert set(kept) == {"Tests run with pytest -q.", "README is the source of docs."}
    assert all(f.status == "verified" for f in kept.values())
    assert kept["Tests run with pytest -q."].evidence.check_ids == ["tests"]
    assert kept["README is the source of docs."].evidence.tool_seqs  # the read, in the trajectory
    dropped = [e for e in read_events(ctx.run_dir / "events.jsonl") if e.type == "memory.dropped"]
    assert len(dropped) == 2


def test_a_gotcha_needs_a_verified_run(make_toolbox: MakeToolbox) -> None:
    box = make_toolbox("repo")
    box.write("README.md", "# Repo\n")
    box("Remember Fact", kind="gotcha", text="README is the source of docs.", paths="README.md")
    box("Read Project File", path="README.md")

    finish_run(box.ctx)  # the run never verified anything

    assert [f for f in box.ctx.memory.facts() if f.kind == "gotcha"] == []


def test_a_command_run_by_an_agent_with_exit_code_zero_backs_a_proposal(
    make_toolbox: MakeToolbox,
) -> None:
    box = make_toolbox("repo")
    box.write("ok.py", "print('ok')\n")
    box("Remember Fact", kind="setup", text="The script ok.py runs.", command="python ok.py")
    assert box("Run Project Command", command="python ok.py").startswith("Exit code: 0")

    finish_run(box.ctx)

    [kept] = box.ctx.memory.facts()
    assert kept.status == "verified" and kept.evidence.tool_seqs


def test_proposals_of_a_crashed_run_are_dropped_by_the_next_one(tmp_path: Path) -> None:
    store = store_in(tmp_path)
    store.upsert(fact("p", status="proposed", evidence=Evidence(run_id="old")))
    store.upsert(fact("v"))

    assert [f.topic for f in store.drop_proposals(except_run="new")] == ["p"]
    assert [f.topic for f in store.facts()] == ["v"]


# -- what the team is shown -------------------------------------------------------------------


def seeded(ctx: RunContext, count: int = 12) -> None:
    kinds = ("command", "setup", "flaky_test", "gotcha", "convention", "layout")
    for number in range(count):
        kind = kinds[number % len(kinds)]
        ctx.memory.upsert(fact(f"{kind}-{number}", f"Fact {number} about {kind}.", kind=kind))


def test_the_section_is_labelled_data_and_capped_by_count(make_context: MakeContext) -> None:
    ctx = make_context("repo")
    seeded(ctx)

    text, shown = memory_section(ctx, "implement")

    assert text.startswith(LABEL) and "not instructions" in LABEL
    assert len(shown) == 8 and text.count("\n- [") == 8
    assert [f.kind for f in shown][:2] == ["command", "command"]  # the most useful first


def test_the_section_is_capped_by_characters(make_context: MakeContext) -> None:
    ctx = make_context("repo", settings=load_settings(overrides={"memory.inject_chars": 400}))
    seeded(ctx)

    text, shown = memory_section(ctx, "implement")

    assert len(text) <= 400 and 0 < len(shown) < 8


def test_only_verified_fresh_facts_are_shown(make_context: MakeContext) -> None:
    ctx = make_context("repo")
    ctx.memory.upsert(fact("a", "Verified one."))
    ctx.memory.upsert(fact("b", "Proposed one.", status="proposed"))
    ctx.memory.upsert(fact("c", "Stale one.", status="stale"))

    text, shown = memory_section(ctx, "implement")

    assert [f.text for f in shown] == ["Verified one."] and "Stale one." not in text


def test_a_fact_whose_source_changed_is_not_shown_in_the_next_run(
    make_context: MakeContext,
) -> None:
    first = make_context("repo")
    files(first.workspace.root, {"Makefile": "test:\n\tpytest\n"})
    record_baseline(first, baseline(row()))
    assert memory_section(first, "implement")[1]

    files(first.workspace.root, {"README.md": "# not a source\n"})
    assert memory_section(make_context("repo"), "implement")[1]  # an unrelated change

    files(first.workspace.root, {"Makefile": "test:\n\tpytest -x\n"})
    assert memory_section(first, "implement") == ("", [])  # an edit during the run counts at once
    second = make_context("repo")  # opening a run marks the facts whose sources changed
    assert [f.status for f in second.memory.facts()] == ["stale"]
    assert memory_section(second, "implement") == ("", [])
    assert "memory.stale" in events_of(second)


def test_planning_and_review_stages_get_the_kinds_they_can_use() -> None:
    facts = [
        fact(kind, kind=kind)
        for kind in ("command", "setup", "flaky_test", "gotcha", "convention", "layout")
    ]

    assert {f.kind for f in select(facts, "design", (), 10)} == {
        "gotcha",
        "convention",
        "layout",
        "command",
    }
    assert {f.kind for f in select(facts, "review_change", (), 10)} == {
        "flaky_test",
        "gotcha",
        "convention",
        "layout",
    }
    assert len(select(facts, "repair_fix", (), 10)) == 6


def test_facts_about_the_packages_files_come_first() -> None:
    near = fact("near", kind="layout", sources=[Source(path="src/api/routes.py", sha256="0" * 64)])
    far = fact("far", kind="command")

    assert [f.topic for f in select([far, near], "implement", ["src/api/**"], 1)] == ["near"]
    assert [f.topic for f in select([far, near], "implement", [], 1)] == ["far"]


# -- memory is not part of the project's change ----------------------------------------------


def test_memory_files_do_not_move_the_workspace_revision_or_show_in_a_snapshot(
    make_context: MakeContext,
) -> None:
    ctx = make_context("repo")
    files(ctx.workspace.root, {"app.py": "x = 1\n"})
    before, tree = workspace_revision(ctx.workspace), Snapshot.take(ctx.workspace).files

    ctx.memory.upsert(fact("a", "A fact."))

    assert (ctx.workspace.root / MEMORY_DIRECTORY / FACTS_FILE).is_file()
    assert workspace_revision(ctx.workspace) == before
    assert Snapshot.take(ctx.workspace).files == tree


@pytest.mark.git
def test_memory_is_left_out_of_diffs_and_patches(tmp_path: Path) -> None:
    from repo_fixtures import make_repo

    from engineering_team.tools.workspace import ProjectWorkspace

    repo = make_repo(tmp_path / "repo", {"app.py": "x = 1\n"})
    ctx = RunContext.create(load_settings(), ProjectWorkspace.create(repo))
    base = ctx.git.diff()

    ctx.memory.upsert(fact("a", "A fact."))

    assert ctx.git.diff() == base == ""
    assert ".engineering-team" not in ctx.git.status()


# -- the CLI ----------------------------------------------------------------------------------


def run_memory(repo: Path, *args: str, json_output: bool = False) -> tuple[int, str]:
    command = [*(["--json"] if json_output else []), "memory", *args, "--repo", str(repo)]
    result = runner.invoke(app, command)
    return result.exit_code, result.output


def test_memory_list_show_forget_and_clear(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    files(repo, {"Makefile": "test:\n\tpytest\n"})
    store = store_in(repo)
    sources = hash_sources(repo, ["Makefile"])
    assert not isinstance(sources, str)
    first = store.upsert(fact("make", "Tests run with make test.", kind="command", sources=sources))
    store.upsert(fact("other", "A proposal.", status="proposed"))

    code, listing = run_memory(repo, "list")
    assert code == 0 and first.id in listing and "Tests run with make test." in listing
    only = run_memory(repo, "list", "--status", "proposed")[1]
    assert "A proposal." in only and first.id not in only
    assert run_memory(repo, "list", "--kind", "nope")[0] == 2

    shown = run_memory(repo, "show", first.id[:10])
    assert shown[0] == 0 and "Makefile" in shown[1] and "never used" in shown[1]
    as_json = json.loads(run_memory(repo, "show", first.id, json_output=True)[1])
    assert as_json["sources"][0]["path"] == "Makefile"

    (repo / "Makefile").write_text(
        "test:\n\tpytest -x\n"
    )  # the source changed: stale, still listed
    assert "stale" in run_memory(repo, "list")[1]

    assert run_memory(repo, "forget", first.id)[0] == 0
    assert run_memory(repo, "forget", first.id)[0] == 2
    assert run_memory(repo, "show", "nope")[0] == 2

    assert runner.invoke(app, ["memory", "clear", "--repo", str(repo)], input="n\n").exit_code == 1
    assert len(store.facts()) == 1
    cleared = runner.invoke(app, ["--json", "memory", "clear", "--yes", "--repo", str(repo)])
    assert json.loads(cleared.output) == {"cleared": 1}
    assert store.facts() == [] and not (repo / MEMORY_DIRECTORY / VIEW_FILE).exists()


def test_the_cli_says_what_is_wrong_with_a_damaged_file(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / MEMORY_DIRECTORY).mkdir(parents=True)
    (repo / MEMORY_DIRECTORY / FACTS_FILE).write_text("nonsense\n")

    code, output = run_memory(repo, "list")

    assert code == 2 and "line 1" in output and "memory clear" in output
    assert run_memory(repo, "clear", "--yes")[0] == 0  # the way out
