"""``bench import-swe`` and SWE-bench-style tasks, on a synthetic instance: a Git repository made in
``tmp_path`` (no network, no dataset download, no Docker)."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from engineering_team.bench import swe_check
from engineering_team.bench.checklib import CheckError
from engineering_team.bench.fake_team import reference_files
from engineering_team.bench.prepare import make_repository
from engineering_team.bench.repos import checkout, checkout_dir, patch_paths, patched_files
from engineering_team.bench.results import collect_results
from engineering_team.bench.swe import ImportOptions, import_instances, parse_instance, read_ids
from engineering_team.bench.swe_runner import files_to_run, parse_statuses
from engineering_team.bench.tasks import BenchError, load_suite, load_task
from engineering_team.cli.app import app

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

INSTANCE = "synthetic__calc-1"
BUGGY = "def add(a, b):\n    return a - b\n\n\ndef sub(a, b):\n    return a - b\n"
FIXED = BUGGY.replace("return a - b\n\n\n", "return a + b\n\n\n")
TESTS = "from calc import sub\n\n\ndef test_sub():\n    assert sub(3, 1) == 2\n"
NEW_TESTS = (
    "import pytest\n"
    + TESTS.replace("import sub", "import add, sub")
    + (
        "\n\ndef test_add():\n    assert add(2, 2) == 4\n"
        '\n\n@pytest.mark.parametrize("pair", ["2 2"])\ndef test_add_pair(pair):\n'
        "    assert add(*map(int, pair.split())) == 4\n"
    )
)
# As SWE-bench stores them: an id holding a space is cut there, and a progress-bar fragment of
# pytest's output sits among the tests.
FAIL_TO_PASS = ["tests/test_calc.py::test_add", "tests/test_calc.py::test_add_pair[2"]


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
         "-c", "commit.gpgsign=false", *args],
        cwd=repo, check=True, capture_output=True, text=True,
    ).stdout  # fmt: skip


def diff_of(repo: Path, path: str, text: str) -> str:
    """The diff that changes ``path`` to ``text`` (the tree is left as it was)."""

    (repo / path).write_text(text)
    patch = git(repo, "diff")
    git(repo, "checkout", "--", path)
    return patch


@pytest.fixture
def upstream(tmp_path: Path) -> dict[str, Any]:
    """A repository with an issue (``add`` subtracts), its fix and tests, and a later commit that
    fixes it upstream (the team must not see it)."""

    repo = tmp_path / "upstream" / "synthetic" / "calc"
    (repo / "calc").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "calc" / "__init__.py").write_text(BUGGY)
    (repo / "tests" / "test_calc.py").write_text(TESTS)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    base = git(repo, "rev-parse", "HEAD").strip()
    patch = diff_of(repo, "calc/__init__.py", FIXED)
    test_patch = diff_of(repo, "tests/test_calc.py", NEW_TESTS)
    (repo / "calc" / "__init__.py").write_text(FIXED)
    git(repo, "commit", "-qam", "the fix, later")
    row = {
        "instance_id": INSTANCE, "repo": "synthetic/calc", "base_commit": base,
        "problem_statement": "add() subtracts\n\n`add(2, 2)` returns 0.", "patch": patch,
        "test_patch": test_patch, "FAIL_TO_PASS": json.dumps(FAIL_TO_PASS),
        "PASS_TO_PASS": ["tests/test_calc.py::test_sub", "[100%]"], "version": "1.0",
    }  # fmt: skip
    django = {**row, "instance_id": "django__django-1", "repo": "django/django",
              "FAIL_TO_PASS": '["test_x (admin.tests.T)"]', "PASS_TO_PASS": "[]"}  # fmt: skip
    dataset = tmp_path / "swe.jsonl"
    dataset.write_text("\n".join(json.dumps(r) for r in (row, django)) + "\n")
    url_template = f"file://{tmp_path / 'upstream'}/{{repo}}"
    return {"repo": repo, "base": base, "row": row, "dataset": dataset, "urls": url_template}


def options(upstream: dict[str, Any], **changes: Any) -> ImportOptions:
    return ImportOptions(url_template=upstream["urls"], **changes)


def test_an_instance_becomes_a_task_that_loads(upstream: dict[str, Any], tmp_path: Path) -> None:
    result = import_instances(upstream["dataset"], [INSTANCE], tmp_path / "out", options(upstream))

    ((instance, directory),) = result.written
    task = load_task(directory)
    assert task.id == "synthetic-calc-1" and directory.name == task.id
    assert (task.kind, task.mode, task.intent, task.split) == (
        "brownfield", "feature", "bugfix", "heldout"
    )  # fmt: skip
    assert task.repository is not None and task.repository.commit == upstream["base"]
    assert task.repository.url.startswith("file://") and task.repository.license == "unknown"
    assert [c.id for c in task.criteria] == ["fail-to-pass", "pass-to-pass"]
    assert task.sabotage is None and not task.fixture_dir.exists()
    assert task.title.startswith("synthetic/calc#1: add() subtracts")
    assert "`add(2, 2)` returns 0." in task.request_path.read_text()
    assert task.reference_patch.read_text() == upstream["row"]["patch"]
    spec = json.loads((directory / "acceptance" / "swe.json").read_text())
    assert spec["test_files"] == ["tests/test_calc.py"]
    assert spec["fail_to_pass"] == FAIL_TO_PASS
    assert spec["pass_to_pass"] == ["tests/test_calc.py::test_sub"]
    assert spec["ignored_tests"] == ["[100%]"]
    assert spec["image"] == "swebench/sweb.eval.x86_64.synthetic_1776_calc-1:latest"
    # the hidden tests are in the check only: never in the request or the reference
    assert "test_add" not in task.request_path.read_text() + task.reference_patch.read_text()


def test_unsupported_instances_are_skipped_and_unknown_ids_refused(
    upstream: dict[str, Any], tmp_path: Path
) -> None:
    out = tmp_path / "out"
    result = import_instances(
        upstream["dataset"], [INSTANCE, "django__django-1"], out, options(upstream)
    )
    assert [i.instance_id for i, _ in result.written] == [INSTANCE]
    assert result.skipped[0][0] == "django__django-1" and "pytest" in result.skipped[0][1]

    with pytest.raises(BenchError, match="Not in swe.jsonl: nope__nope-1"):
        import_instances(upstream["dataset"], ["nope__nope-1"], out, options(upstream))
    with pytest.raises(BenchError, match="--force"):
        import_instances(upstream["dataset"], [INSTANCE], out, options(upstream))
    again = import_instances(upstream["dataset"], [INSTANCE], out, options(upstream, force=True))
    assert len(again.written) == 1


def test_instance_fields_are_checked(upstream: dict[str, Any]) -> None:
    row = upstream["row"]
    flask = parse_instance({**row, "instance_id": "pallets__flask-4045", "repo": "pallets/flask"})
    assert flask.license == "BSD-3-Clause" and flask.task_id == "pallets-flask-4045"
    assert parse_instance({**row, "PASS_TO_PASS": ""}).pass_to_pass == []
    with pytest.raises(BenchError, match="full 40-character SHA"):
        parse_instance({**row, "base_commit": "abc123"})
    with pytest.raises(BenchError, match="missing patch"):
        parse_instance({**row, "patch": ""})
    with pytest.raises(BenchError, match="not a JSON list"):
        parse_instance({**row, "FAIL_TO_PASS": "[oops"})


def test_the_starter_list_names_supported_repositories() -> None:
    starter = Path(__file__).resolve().parents[1] / "benchmarks" / "swe-starter.txt"
    if not starter.is_file():
        pytest.skip("no benchmarks/ directory")
    ids = read_ids(starter)
    assert 12 <= len(ids) <= 20 and len(set(ids)) == len(ids)
    owners = {i.split("__")[0] for i in ids}
    assert owners <= {"pallets", "mwaskom", "pytest-dev", "pylint-dev"}


def test_a_repository_task_needs_its_patch_and_others_their_sabotage(
    upstream: dict[str, Any], tmp_path: Path
) -> None:
    (_, directory), = import_instances(
        upstream["dataset"], [INSTANCE], tmp_path / "out", options(upstream)
    ).written  # fmt: skip
    (directory / "reference" / "solution.patch").unlink()
    (directory / "reference" / "x.py").write_text("x = 1\n")
    with pytest.raises(BenchError, match="needs reference/solution.patch"):
        load_task(directory)

    data = yaml.safe_load((directory / "task.yaml").read_text())
    del data["repository"]
    (directory / "task.yaml").write_text(yaml.safe_dump(data))
    with pytest.raises(BenchError, match="needs a sabotage"):
        load_task(directory)


def test_the_repository_is_fetched_once_and_the_team_gets_no_history(
    upstream: dict[str, Any], tmp_path: Path
) -> None:
    (_, directory), = import_instances(
        upstream["dataset"], [INSTANCE], tmp_path / "out", options(upstream)
    ).written  # fmt: skip
    task = load_task(directory)
    assert task.repository is not None
    cache = tmp_path / "cache"
    url, commit = task.repository.url, task.repository.commit

    cached = checkout(url, commit, cache)
    assert cached == checkout_dir(cache, url, commit) == cache / "synthetic__calc" / commit
    shutil.rmtree(upstream["repo"])  # a second run must not need the network
    assert checkout(url, commit, cache) == cached

    repo = tmp_path / "repo"
    make_repository(task, repo, cache)
    assert (repo / "calc" / "__init__.py").read_text() == BUGGY  # the base, not the later fix
    assert git(repo, "log", "--format=%s").split() == ["fixture"]
    with pytest.raises(BenchError, match="repository cache"):
        make_repository(task, tmp_path / "other")


def test_an_unreachable_repository_is_a_setup_error(
    upstream: dict[str, Any], tmp_path: Path
) -> None:
    gone = ImportOptions(url_template=f"file://{tmp_path / 'missing'}/{{repo}}")
    (_, directory), = import_instances(
        upstream["dataset"], [INSTANCE], tmp_path / "out", gone
    ).written  # fmt: skip
    with pytest.raises(BenchError, match="cannot fetch"):
        make_repository(load_task(directory), tmp_path / "repo", tmp_path / "cache")
    assert not any((tmp_path / "cache").rglob(".git"))  # nothing half-fetched is kept


def test_the_reference_patch_is_replayed_as_files(upstream: dict[str, Any], tmp_path: Path) -> None:
    (_, directory), = import_instances(
        upstream["dataset"], [INSTANCE], tmp_path / "out", options(upstream)
    ).written  # fmt: skip
    task = load_task(directory)
    base = tmp_path / "base"
    git(tmp_path, "clone", "-q", str(upstream["repo"]), str(base))
    git(base, "checkout", "-q", upstream["base"])

    assert reference_files(task, "reference", base) == {"calc/__init__.py": FIXED}
    assert (base / "calc" / "__init__.py").read_text() == BUGGY  # the workspace is untouched
    assert reference_files(task, "fixture", base) == {}
    with pytest.raises(BenchError, match="no sabotage"):
        reference_files(task, "broken", base)
    (base / "gone.txt").write_text("x\n")
    git(base, "add", "gone.txt")
    git(base, "commit", "-qm", "gone")
    git(base, "rm", "-q", "gone.txt")
    removal = git(base, "diff", "--cached")
    git(base, "reset", "-q", "--hard", "HEAD")
    assert patch_paths(removal) == ["gone.txt"]
    with pytest.raises(Exception, match="deletes gone.txt"):
        patched_files(removal, base)


def test_pytest_statuses_are_read_from_the_summary() -> None:
    output = "\n".join(
        [
            "PASSED tests/test_a.py::test_one",
            "FAILED tests/test_a.py::test_two - AssertionError: assert 1 == 2",
            "PASSED tests/test_a.py::test_p[a - b]",
            "XFAIL tests/test_a.py::test_x - reason",
            "SKIPPED [1] tests/test_a.py:9: no network",
        ]
    )
    wanted = ["tests/test_a.py::test_one", "tests/test_a.py::test_two",
              "tests/test_a.py::test_p[a - b]", "tests/test_a.py::test_x",
              "tests/test_a.py::test_gone"]  # fmt: skip
    assert parse_statuses(output, wanted) == {
        "tests/test_a.py::test_one": "PASSED", "tests/test_a.py::test_two": "FAILED",
        "tests/test_a.py::test_p[a - b]": "PASSED", "tests/test_a.py::test_x": "XFAIL",
        "tests/test_a.py::test_gone": "MISSING",
    }  # fmt: skip


@pytest.mark.parametrize("order", [1, -1])
def test_an_id_swe_bench_cut_short_matches_and_a_failure_wins(order: int) -> None:
    lines = ["PASSED t.py::test_k[foo bar]", "FAILED t.py::test_k[foo baz] - boom",
             "PASSED t.py::test_kk"][::order]  # fmt: skip
    statuses = parse_statuses("\n".join(lines), ["t.py::test_k[foo", "t.py::test_k"])

    assert statuses == {"t.py::test_k[foo": "FAILED", "t.py::test_k": "MISSING"}


def test_the_runner_runs_the_files_the_ids_name() -> None:
    ids = ["a/test_x.py::T::test_1", "b/test_y.py::test_2[a", "a/test_x.py::test_3", "c/test_z.py"]

    assert files_to_run(ids) == ["a/test_x.py", "b/test_y.py", "c/test_z.py"]


class FakeRun:
    """Stands in for ``checklib.run`` inside ``swe_check``: records each command."""

    def __init__(self, probe: subprocess.CompletedProcess[str], result: str) -> None:
        self.commands: list[list[str]] = []
        self.probe, self.result = probe, result

    def __call__(self, argv: list[str], cwd: Path, **_: Any) -> subprocess.CompletedProcess[str]:
        self.commands.append(list(argv))
        if argv[:3] == ["docker", "image", "inspect"]:
            return self.probe
        return subprocess.CompletedProcess(argv, 0, self.result, "")


def _imported_acceptance(upstream: dict[str, Any], tmp_path: Path) -> Path:
    (_, directory), = import_instances(
        upstream["dataset"], [INSTANCE], tmp_path / "out", options(upstream)
    ).written  # fmt: skip
    return directory / "acceptance"


def test_the_docker_check_runs_the_tests_in_the_instance_image_without_network(
    upstream: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    acceptance = _imported_acceptance(upstream, tmp_path)
    statuses = {"tests/test_calc.py::test_add": "FAILED"}
    fake = FakeRun(
        subprocess.CompletedProcess([], 0, "[]", ""),
        "noise\n" + swe_check.RESULT_MARKER + json.dumps({"statuses": statuses}) + "\n",
    )
    monkeypatch.setattr(swe_check, "run", fake)
    monkeypatch.setattr(swe_check.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setenv("BENCH_ACCEPTANCE_SANDBOX", "docker")
    check = swe_check.group_check(str(acceptance / "checks.py"), "fail_to_pass")

    with pytest.raises(AssertionError, match=r"1 of 1 tests do not pass: .*test_add \(FAILED\)"):
        check(tmp_path)
    command = fake.commands[-1]
    assert command[:3] == ["docker", "run", "--rm"]
    assert command[command.index("--network") + 1] == "none"
    assert command[command.index("--platform") + 1] == "linux/amd64"
    assert f"{acceptance.resolve()}:/bench/acceptance:ro" in command
    assert "swebench/sweb.eval.x86_64.synthetic_1776_calc-1:latest" in command
    assert command[-2:] == ["--group", "fail_to_pass"]


@pytest.mark.parametrize(
    ("which", "probe", "message"),
    [
        (None, None, "docker is not on PATH"),
        ("/usr/bin/docker", "Cannot connect to the Docker daemon", "Docker is not running"),
        ("/usr/bin/docker", "No such image", "docker pull swebench/"),
    ],
)  # fmt: skip
def test_missing_docker_is_an_error_that_says_what_to_do(
    upstream: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    which: str | None, probe: str | None, message: str,
) -> None:  # fmt: skip
    acceptance = _imported_acceptance(upstream, tmp_path)
    monkeypatch.setattr(
        swe_check, "run", FakeRun(subprocess.CompletedProcess([], 1, "", probe or ""), "")
    )
    monkeypatch.setattr(swe_check.shutil, "which", lambda name: which)
    monkeypatch.setenv("BENCH_ACCEPTANCE_SANDBOX", "docker")

    with pytest.raises(CheckError, match=message):
        swe_check.group_check(str(acceptance / "checks.py"), "fail_to_pass")(tmp_path)


def _cli(*args: str):  # noqa: ANN202
    return CliRunner().invoke(app, ["--json", "bench", *args])


def test_a_synthetic_instance_runs_end_to_end_in_fake_mode(
    upstream: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tasks_dir, cache, runs = tmp_path / "tasks-swe", tmp_path / "cache", tmp_path / "runs"
    imported = _cli(
        "import-swe", "--dataset", str(upstream["dataset"]), "--ids", INSTANCE,
        "--out", str(tasks_dir), "--url-template", upstream["urls"],
    )  # fmt: skip
    assert imported.exit_code == 0, imported.output
    assert json.loads(imported.stdout)["imported"][0]["task"] == "synthetic-calc-1"
    listed = _cli("list", "--tasks-dir", str(tasks_dir))
    assert json.loads(listed.stdout)[0]["repository"]["name"] == "synthetic/calc"

    def run(batch: str, *extra: str):  # noqa: ANN202
        return _cli(
            "run", "--fake", "--suite", str(tmp_path / "suite"), "--tasks-dir", str(tasks_dir),
            "--repo-cache", str(cache), "--out", str(runs), "--batch", batch, *extra,
        )  # fmt: skip

    good = run("reference", "--acceptance-sandbox", "local")
    assert good.exit_code == 0, good.output
    (record,) = collect_results(runs / "reference")
    assert record.outcome == "passed", record.reason
    assert {c.id: c.passed for c in record.criteria} == {"fail-to-pass": True, "pass-to-pass": True}

    untouched = run("fixture", "--acceptance-sandbox", "local", "--fake-solution", "fixture")
    assert untouched.exit_code == 3, untouched.output
    (record,) = collect_results(runs / "fixture")
    assert record.outcome == "failed"
    assert {c.id: c.passed for c in record.criteria} == {
        "fail-to-pass": False,
        "pass-to-pass": True,
    }
    assert "test_add" in record.criteria[0].detail

    # The default sandbox is Docker; with its daemon down the checks cannot judge (never a fail).
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text("#!/bin/sh\necho 'Cannot connect to the Docker daemon' >&2\nexit 1\n")
    docker.chmod(docker.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    no_docker = run("no-docker")
    assert no_docker.exit_code == 3, no_docker.output
    (record,) = collect_results(runs / "no-docker")
    assert record.outcome == "error" and "could not judge" in record.reason
    assert all(c.error and "Docker is not running" in c.detail for c in record.criteria)


def test_tasks_dir_must_exist(tmp_path: Path) -> None:
    with pytest.raises(BenchError, match="No task directory"):
        load_suite(tmp_path, tmp_path / "missing")
