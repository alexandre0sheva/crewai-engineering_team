"""Checking an answer's citations: files and lines, quotes, pages the run fetched, the verdict."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from engineering_team.modes.answer_contracts import Answer, AnswerCheck, Citation
from engineering_team.modes.citations import (
    canonical_url,
    fetched_urls,
    verdict_of,
    verify_citations,
)

SOURCE = (
    "def add(items, text):\n    return [*items,\n            text]\n\n\n"
    "def size(items):\n    return len(items)\n"
)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / "app").mkdir(parents=True)
    (root / "app" / "store.py").write_text(SOURCE, encoding="utf-8")
    (root / ".engineering-team").mkdir()
    (root / ".engineering-team" / "note.md").write_text("my own notes\n", encoding="utf-8")
    return root


def check(project: Path, *citations: Citation, fetched: set[str] | None = None) -> list[str]:
    answer = Answer(summary="s", citations=list(citations))
    return [r.status for r in verify_citations(answer, project, fetched or set())]


def code(path: str = "app/store.py", lines: str = "1-2", quote: str = "def add") -> Citation:
    return Citation(path=path, lines=lines, quote=quote)


def test_a_quote_on_the_cited_lines_holds(project: Path) -> None:
    assert check(project, code(), code(lines="6", quote="def size(items):")) == ["verified"] * 2


def test_the_quote_is_compared_without_regard_to_whitespace(project: Path) -> None:
    spread = code(lines="1-3", quote="return [*items,\n  text]")
    assert check(project, spread) == ["verified"]
    assert check(project, code(lines="2-3", quote="return   [*items,    text]")) == ["verified"]


def test_a_quote_that_is_on_other_lines_does_not_hold(project: Path) -> None:
    assert check(project, code(lines="6-7", quote="def add")) == ["quote_mismatch"]
    assert check(project, code(quote="def remove")) == ["quote_mismatch"]


@pytest.mark.parametrize("lines", ["", "abc", "0", "5-3", "1-99", "99"])
def test_lines_must_be_a_range_inside_the_file(project: Path, lines: str) -> None:
    assert check(project, code(lines=lines)) == ["bad_lines"]


def test_a_missing_file_and_a_citation_without_a_quote_are_flagged(project: Path) -> None:
    assert check(project, code(path="app/gone.py"), code(path="app")) == ["missing_file"] * 2
    assert check(project, code(quote="  ")) == ["no_quote"]
    assert check(project, Citation()) == ["no_source"]


@pytest.mark.parametrize(
    "path", ["../outside.py", "/etc/passwd", "~/x", "app/../../x", ".engineering-team/note.md"]
)
def test_only_files_of_the_project_can_be_cited(project: Path, path: str) -> None:
    assert check(project, code(path=path, lines="1", quote="notes")) == ["outside_project"]


def test_a_link_out_of_the_project_is_not_followed(project: Path, tmp_path: Path) -> None:
    outside = tmp_path / "secret.py"
    outside.write_text("TOKEN = 1\n", encoding="utf-8")
    (project / "app" / "link.py").symlink_to(outside)

    assert check(project, code(path="app/link.py", lines="1", quote="TOKEN")) == ["outside_project"]


def test_a_dot_slash_path_is_the_same_file(project: Path) -> None:
    assert check(project, code(path="./app/store.py")) == ["verified"]


def test_a_page_counts_only_when_a_fetch_url_call_read_it(project: Path) -> None:
    page = Citation(url="https://Docs.Example.com/guide/?q=1#top", title="Guide")
    other = Citation(url="https://docs.example.com/other")
    fetched = {canonical_url("https://docs.example.com/guide")}

    assert check(project, page, other, fetched=fetched) == ["verified", "not_fetched"]
    assert check(project, Citation(url="ftp://docs.example.com/guide"), fetched=fetched) == [
        "not_fetched"
    ]


def write_trajectory(path: Path, *entries: dict[str, object]) -> None:
    lines = []
    for seq, entry in enumerate(entries):
        base = {"seq": seq, "ts": "2026-10-31T10:00:00Z", "kind": "tool", "ok": True}
        lines.append(json.dumps({**base, **entry}))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_fetched_pages_come_from_the_trajectory_of_the_run(tmp_path: Path) -> None:
    path = tmp_path / "trajectory.jsonl"
    write_trajectory(
        path,
        {"tool": "Fetch URL", "args": {"url": "https://a.example/one"}},
        {"tool": "Fetch URL", "args": {"url": "https://b.example/two"}, "ok": False},
        {
            "tool": "Fetch URL",
            "args": {"url": "https://c.example/short"},
            "output": "Fetched https://c.example/long (HTTP 200, text/html, 10 characters)\nbody",
        },
        {"tool": "Web Search", "args": {"query": "https://d.example/three"}},
        {"kind": "message", "role": "assistant", "text": "I read https://e.example/four"},
    )

    assert fetched_urls(path) == {
        "https://a.example/one",
        "https://c.example/short",
        "https://c.example/long",
    }
    assert fetched_urls(tmp_path / "missing.jsonl") == set()


def test_the_verdict_follows_how_many_citations_hold(project: Path) -> None:
    def verdict(*citations: Citation, changed: list[str] | None = None) -> tuple[str, list[str]]:
        answer = Answer(summary="s", citations=list(citations))
        results = verify_citations(answer, project, set())
        return verdict_of(AnswerCheck(results=results, changed=changed or []))

    assert verdict(code()) == ("verified", [])
    partial, problems = verdict(code(), code(path="app/gone.py"))
    assert partial == "partial" and problems == ["[2] app/gone.py:1-2: no such file in the project"]
    assert verdict(code(path="app/gone.py"))[0] == "failed"
    assert verdict()[0] == "partial" and "cites nothing" in verdict()[1][0]
    failed, problems = verdict(code(), changed=["app/store.py"])
    assert failed == "failed" and "The project was changed" in problems[0]
