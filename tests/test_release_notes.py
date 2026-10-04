"""``scripts/release_notes.py``: the changelog section a release publishes, and the release gate."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "release_notes.py"
CHANGELOG = """\
# Changelog

## [Unreleased]

## [0.2.0] — 2026-10-05

Intro.

### Added

- A thing.

### Migration from 0.1.0

- Move it.

## [0.1.0] — 2026-07-30

First.

[Unreleased]: https://example.com/compare/v0.2.0...HEAD
[0.2.0]: https://example.com/compare/v0.1.0...v0.2.0
[0.1.0]: https://example.com/releases/tag/v0.1.0
"""


@pytest.fixture
def notes() -> ModuleType:
    spec = importlib.util.spec_from_file_location("release_notes", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_section_runs_to_the_next_version_and_keeps_its_subsections(notes: ModuleType) -> None:
    body = notes.section(CHANGELOG, "0.2.0")

    assert body.startswith("Intro.") and "### Migration from 0.1.0" in body
    assert "First." not in body and "[0.2.0]: https" not in body
    assert notes.section(CHANGELOG, "0.1.0") == "First."
    assert notes.section(CHANGELOG, "9.9.9") is None


def test_the_release_gate_wants_a_dated_nonempty_section_and_a_compare_link(
    notes: ModuleType,
) -> None:
    assert notes.check(CHANGELOG, "0.2.0") == []
    assert "no dated" in notes.check(CHANGELOG, "0.3.0")[0]
    assert (
        "no dated" in notes.check(CHANGELOG, "Unreleased")[0]
    )  # an undated section is not a release
    empty = CHANGELOG.replace("Intro.", "").replace("### Added\n\n- A thing.\n\n", "")
    assert any(
        "is empty" in p
        for p in notes.check(
            empty.replace("### Migration from 0.1.0\n\n- Move it.\n\n", ""), "0.2.0"
        )
    )
    assert any(
        "compare link" in p
        for p in notes.check(CHANGELOG.replace("[0.2.0]: https", "[x]: https"), "0.2.0")
    )


def test_this_repositorys_changelog_passes_its_own_gate(notes: ModuleType) -> None:
    version = notes.project_version()
    changelog = (SCRIPT.parent.parent / "CHANGELOG.md").read_text(encoding="utf-8")

    assert notes.check(changelog, version) == []
    assert "Migration from 0.1.0" in notes.section(changelog, "0.2.0")
