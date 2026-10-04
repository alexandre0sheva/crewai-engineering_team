"""``scripts/check_docs.py``: the repository's documentation passes, and the checker catches what
it claims to (broken files and anchors, undocumented environment variables)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_docs.py"


@pytest.fixture
def check() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_docs", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_repository_documentation_passes(
    check: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    assert check.main() == 0, capsys.readouterr().out


@pytest.mark.parametrize(
    ("heading", "expected"),
    [
        ("Web UI (`[ui]`)", "web-ui-ui"),
        ("MCP servers (`[mcp.<name>]`)", "mcp-servers-mcpname"),
        ("Tools & groups: a *quick* tour", "tools--groups-a-quick-tour"),
        ("A [link](x.md) here", "a-link-here"),
    ],
)
def test_headings_become_githubs_anchors(check: ModuleType, heading: str, expected: str) -> None:
    assert check.slug(heading) == expected


def test_repeated_headings_get_numbered_anchors_and_code_fences_are_skipped(
    check: ModuleType, tmp_path: Path
) -> None:
    page = tmp_path / "page.md"
    page.write_text("# Same\n\n## Same\n\n```\n# not a heading\n```\n", encoding="utf-8")

    assert check.anchors(page) == {"same", "same-1"}


def test_broken_links_and_anchors_are_reported_and_good_ones_are_not(
    check: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "other.md").write_text("# Target\n", encoding="utf-8")
    page = tmp_path / "page.md"
    page.write_text(
        "[ok](other.md#target) [file](missing.md) [anchor](other.md#nope) [self](#top)\n"
        "[web](https://example.com/x) [mail](mailto:a@b.c) `[code](missing.md)`\n"
        "```\n[fenced](missing.md)\n```\n# Top\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(check, "ROOT", tmp_path)
    monkeypatch.setattr(check, "markdown_files", lambda: [page])

    problems = check.link_problems()

    assert problems == [
        "page.md:1: missing.md does not exist",
        "page.md:1: other.md#nope: no such heading",
    ]


def test_an_undocumented_environment_variable_is_reported(
    check: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "mod.py").write_text(
        'A = "ENGINEERING_KNOWN"\nB = "ENGINEERING_SECRET_SWITCH"\n'
        "C = '<!-- ENGINEERING_MARKER -->'\n",
        encoding="utf-8",
    )
    doc = tmp_path / "CONFIGURATION.md"
    doc.write_text("`ENGINEERING_KNOWN`\n", encoding="utf-8")
    monkeypatch.setattr(check, "ROOT", tmp_path)
    monkeypatch.setattr(check, "SOURCE", source)
    monkeypatch.setattr(check, "CONFIGURATION", doc)

    assert check.environment_problems() == [
        "ENGINEERING_SECRET_SWITCH is read in src/mod.py but not documented in "
        "docs/CONFIGURATION.md"
    ]


def test_a_setting_with_two_rows_is_reported(
    check: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = tmp_path / "CONFIGURATION.md"
    doc.write_text(
        "| `a.b`, `a.c` | x |\n| `d` | `a.b` mentioned in a cell is fine |\n| `a.b` | again |\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(check, "CONFIGURATION", doc)

    assert check.duplicate_setting_problems() == [
        "docs/CONFIGURATION.md:3: `a.b` already has a row (line 1)"
    ]
