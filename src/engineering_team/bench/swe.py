"""Import SWE-bench instances as benchmark tasks (``bench import-swe``).

A SWE-bench instance is a GitHub issue of a Python repository: the repository at a base commit,
the issue text, the fix that closed it (``patch``), the tests that judge a fix (``test_patch``),
and two lists of test ids, ``FAIL_TO_PASS`` (fail before the fix, pass after) and ``PASS_TO_PASS``
(pass both times). The dataset file is the user's to download (docs/BENCHMARKS.md); this module
reads a local JSON Lines (or JSON) file and writes one task directory per instance:

    task.yaml                 a ``feature`` task on the repository at the base commit
    request.md                the issue text, the only thing the team is shown
    acceptance/checks.py      ``fail-to-pass`` and ``pass-to-pass`` (bench/swe_check.py)
    acceptance/swe.json       commit, tests, Docker image and the instance's provenance
    acceptance/test.patch     the instance's tests, applied only inside the check
    reference/solution.patch  the instance's own fix (what ``--fake`` replays)

Only instances whose tests are pytest node ids are imported for now (``path.py::name``); others
(Django's and SymPy's own runners) are skipped with the reason. Entries that start with ``[`` are
fragments of a progress bar SWE-bench's log parser kept (``[100%]``): they are dropped and listed in
``swe.json`` as ``ignored_tests``.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from engineering_team.bench.repos import patch_paths
from engineering_team.bench.swe_check import SPEC_FILE, TEST_PATCH
from engineering_team.bench.tasks import COMMIT, REFERENCE_PATCH, BenchError, load_task

REQUIRED = ("instance_id", "repo", "base_commit", "problem_statement", "patch", "test_patch")
# What a dataset row says when it names its repository. Every repository of SWE-bench (full) and
# its Lite and Verified subsets; any other is imported with "unknown" and a warning.
LICENSES = {
    "astropy/astropy": "BSD-3-Clause",
    "django/django": "BSD-3-Clause",
    "matplotlib/matplotlib": "Matplotlib License (PSF-based)",
    "mwaskom/seaborn": "BSD-3-Clause",
    "pallets/flask": "BSD-3-Clause",
    "psf/requests": "Apache-2.0",
    "pydata/xarray": "Apache-2.0",
    "pylint-dev/pylint": "GPL-2.0",
    "pytest-dev/pytest": "MIT",
    "scikit-learn/scikit-learn": "BSD-3-Clause",
    "sphinx-doc/sphinx": "BSD-2-Clause",
    "sympy/sympy": "BSD-3-Clause",
}
URL_TEMPLATE = "https://github.com/{repo}.git"
# The SWE-bench harness's published evaluation images: the repository at the base commit in
# /testbed, installed into the conda environment ``testbed``.
IMAGE_TEMPLATE = "swebench/sweb.eval.x86_64.{key}:latest"
IMAGE_PYTHON = "/opt/miniconda3/envs/testbed/bin/python"
IMAGE_PLATFORM = "linux/amd64"
CHECK_TIMEOUT = 900  # seconds for one criterion: a test run, in an emulated image at worst
TASK_TIMEOUT = 3600
CHECKS = '''"""Hidden checks of an imported SWE-bench instance (written by `bench import-swe`)."""

from engineering_team.bench.checklib import main
from engineering_team.bench.swe_check import group_check

check_fail_to_pass = group_check(__file__, "fail_to_pass")
check_pass_to_pass = group_check(__file__, "pass_to_pass")

if __name__ == "__main__":
    main()
'''
REQUEST = "Resolve this issue in the repository.\n\n{issue}\n"


@dataclass(frozen=True)
class ImportOptions:
    split: str = "heldout"
    url_template: str = URL_TEMPLATE
    image_template: str = IMAGE_TEMPLATE
    python: str = IMAGE_PYTHON
    platform: str | None = IMAGE_PLATFORM
    force: bool = False  # replace a task directory that exists


@dataclass(frozen=True)
class Instance:
    instance_id: str
    repo: str
    base_commit: str
    problem_statement: str
    patch: str
    test_patch: str
    fail_to_pass: list[str]
    pass_to_pass: list[str]
    version: str = ""
    created_at: str = ""
    ignored_tests: tuple[str, ...] = ()  # log fragments the dataset lists as tests

    @property
    def task_id(self) -> str:
        """``pallets__flask-4045`` -> ``pallets-flask-4045`` (a task id slug)."""

        return re.sub(r"[^a-z0-9-]+", "-", self.instance_id.lower().replace("__", "-")).strip("-")

    @property
    def license(self) -> str:
        return LICENSES.get(self.repo, "unknown")

    def unsupported(self) -> str:
        """Why the importer cannot run this instance's tests yet; '' when it can."""

        odd = [t for t in self.fail_to_pass + self.pass_to_pass if not is_pytest_id(t)]
        if odd:
            return f"its tests are not pytest node ids (such as {odd[0]!r})"
        if not self.fail_to_pass:
            return "it lists no FAIL_TO_PASS test"
        return ""


def is_pytest_id(test: str) -> bool:
    """``tests/test_x.py::TestA::test_b[1]`` or ``tests/test_x.py``."""

    path = test.split("::", 1)[0]
    return path.endswith(".py") and " " not in path


def is_log_fragment(test: str) -> bool:
    """``[100%]``, ``[``: what SWE-bench's parser took from pytest's progress output."""

    return test.startswith("[")


def _tests(row: dict[str, Any], key: str) -> list[str]:
    value = row.get(key, [])
    if isinstance(value, str):  # the Hugging Face files hold a JSON-encoded list
        try:
            value = json.loads(value) if value.strip() else []
        except ValueError as exc:
            raise BenchError(f"{row.get('instance_id')}: {key} is not a JSON list: {exc}") from exc
    if not isinstance(value, list) or not all(isinstance(t, str) for t in value):
        raise BenchError(f"{row.get('instance_id')}: {key} must be a list of test ids.")
    return value


def parse_instance(row: dict[str, Any]) -> Instance:
    missing = [key for key in REQUIRED if not isinstance(row.get(key), str) or not row[key]]
    if missing:
        raise BenchError(f"{row.get('instance_id', '?')}: missing {', '.join(missing)}.")
    if not COMMIT.fullmatch(row["base_commit"]):
        raise BenchError(f"{row['instance_id']}: base_commit must be a full 40-character SHA.")
    fail, passing = _tests(row, "FAIL_TO_PASS"), _tests(row, "PASS_TO_PASS")
    return Instance(
        instance_id=row["instance_id"], repo=row["repo"], base_commit=row["base_commit"],
        problem_statement=row["problem_statement"], patch=row["patch"],
        test_patch=row["test_patch"],
        fail_to_pass=[t for t in fail if not is_log_fragment(t)],
        pass_to_pass=[t for t in passing if not is_log_fragment(t)],
        version=str(row.get("version") or ""), created_at=str(row.get("created_at") or ""),
        ignored_tests=tuple(t for t in fail + passing if is_log_fragment(t)),
    )  # fmt: skip


def read_dataset(path: Path) -> dict[str, dict[str, Any]]:
    """The dataset's rows by instance id: a JSON Lines file, or one JSON list."""

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise BenchError(f"Cannot read the dataset {path}: {exc}") from exc
    try:
        if text.lstrip().startswith("["):
            rows = json.loads(text)
        else:
            rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    except ValueError as exc:
        raise BenchError(f"{path} is not JSON Lines (one instance per line): {exc}") from exc
    found: dict[str, dict[str, Any]] = {}
    for row in rows:
        if isinstance(row, dict) and isinstance(row.get("instance_id"), str):
            found[row["instance_id"]] = row
    if not found:
        raise BenchError(f"{path} holds no SWE-bench instances.")
    return found


def read_ids(path: Path) -> list[str]:
    """Instance ids from a list file: one per line, ``#`` starts a comment."""

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise BenchError(f"Cannot read {path}: {exc}") from exc
    return [line.split("#", 1)[0].strip() for line in lines if line.split("#", 1)[0].strip()]


def _title(instance: Instance) -> str:
    lines = (line.strip() for line in instance.problem_statement.splitlines() if line.strip())
    first = next(lines, "")
    first = first.lstrip("#").strip()
    first = first if len(first) <= 70 else first[:69].rstrip() + "…"
    number = instance.instance_id.rsplit("-", 1)[-1]
    return f"{instance.repo}#{number}: {first}"


def _size(patch: str) -> str:
    changed = sum(
        1 for line in patch.splitlines() if line[:1] in "+-" and not line.startswith(("+++", "---"))
    )
    return "small" if changed <= 10 else "medium" if changed <= 60 else "large"


def task_yaml(instance: Instance, options: ImportOptions) -> dict[str, Any]:
    criteria = [{
        "id": "fail-to-pass",
        "text": f"the {len(instance.fail_to_pass)} test(s) the issue's fix makes pass now pass",
    }]  # fmt: skip
    if instance.pass_to_pass:
        criteria.append({
            "id": "pass-to-pass",
            "text": f"the {len(instance.pass_to_pass)} test(s) that passed before still pass",
        })  # fmt: skip
    return {
        "id": instance.task_id, "title": _title(instance), "kind": "brownfield",
        "mode": "feature", "intent": "bugfix", "split": options.split,
        "size": _size(instance.patch), "difficulty": "hard", "languages": ["python"],
        "timeout_seconds": TASK_TIMEOUT, "check_timeout_seconds": CHECK_TIMEOUT, "scale": 2.0,
        "repository": {
            "name": instance.repo, "url": options.url_template.format(repo=instance.repo),
            "commit": instance.base_commit, "license": instance.license,
        },
        "criteria": criteria,
    }  # fmt: skip


def check_spec(instance: Instance, options: ImportOptions) -> dict[str, Any]:
    """``acceptance/swe.json``: what the hidden checks need, and where the instance came from."""

    key = instance.instance_id.lower().replace("__", "_1776_")
    return {
        "instance_id": instance.instance_id, "repo": instance.repo,
        "url": options.url_template.format(repo=instance.repo), "commit": instance.base_commit,
        "version": instance.version, "created_at": instance.created_at,
        "image": options.image_template.format(key=key, instance_id=instance.instance_id),
        "platform": options.platform, "python": options.python, "testbed": "/testbed",
        "timeout_seconds": CHECK_TIMEOUT, "test_files": patch_paths(instance.test_patch),
        "fail_to_pass": instance.fail_to_pass, "pass_to_pass": instance.pass_to_pass,
        "ignored_tests": list(instance.ignored_tests),
    }  # fmt: skip


def write_task(instance: Instance, out: Path, options: ImportOptions) -> Path:
    """Write the task directory ``<out>/<task id>`` for one instance."""

    directory = out / instance.task_id
    if directory.exists():
        if not options.force:
            raise BenchError(f"{directory} exists: pass --force to replace it.")
        shutil.rmtree(directory)
    (directory / "acceptance").mkdir(parents=True)
    (directory / "reference").mkdir()
    data = yaml.safe_dump(task_yaml(instance, options), sort_keys=False, allow_unicode=True)
    files = {
        "task.yaml": data,
        "request.md": REQUEST.format(issue=instance.problem_statement.strip()),
        "acceptance/checks.py": CHECKS,
        f"acceptance/{SPEC_FILE}": json.dumps(check_spec(instance, options), indent=2) + "\n",
        f"acceptance/{TEST_PATCH}": instance.test_patch,
        f"reference/{REFERENCE_PATCH}": instance.patch,
    }
    for name, text in files.items():
        (directory / name).write_text(text, encoding="utf-8")
    load_task(directory)  # what was written must load as a task
    return directory


@dataclass(frozen=True)
class ImportResult:
    written: list[tuple[Instance, Path]]
    skipped: list[tuple[str, str]]  # (instance id, why)


def import_instances(
    dataset: Path, ids: list[str], out: Path, options: ImportOptions | None = None
) -> ImportResult:
    """Import ``ids`` from ``dataset`` into ``out``; unknown ids stop it before anything is
    written, unsupported instances are skipped with the reason."""

    options = options or ImportOptions()
    rows = read_dataset(dataset)
    wanted = list(dict.fromkeys(ids))
    if not wanted:
        raise BenchError("Name the instances to import: --ids ID,... or --ids-file FILE.")
    unknown = [i for i in wanted if i not in rows]
    if unknown:
        raise BenchError(f"Not in {dataset.name}: {', '.join(unknown)}.")
    instances = [parse_instance(rows[i]) for i in wanted]
    written: list[tuple[Instance, Path]] = []
    skipped: list[tuple[str, str]] = []
    out.mkdir(parents=True, exist_ok=True)
    for instance in instances:
        reason = instance.unsupported()
        if reason:
            skipped.append((instance.instance_id, reason))
            continue
        written.append((instance, write_task(instance, out, options)))
    return ImportResult(written, skipped)
