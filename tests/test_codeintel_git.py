from __future__ import annotations

from collections.abc import Callable

import pytest
from git_helpers import commit, init_repo

from engineering_team.codeintel.git import (
    Blame,
    FileChange,
    GitHistory,
    GitUnavailable,
    parse_blame,
    parse_log,
)
from engineering_team.codeintel.hotspots import compute_hotspots, format_hotspots
from engineering_team.codeintel.todos import (
    age_label,
    format_todos,
    scan_todos,
    with_blame,
)
from engineering_team.runtime.context import RunContext

MakeContext = Callable[..., RunContext]


def _busy(extra: int) -> str:
    branches = "".join(f"    if x == {n}:\n        y = {n}\n" for n in range(6 + extra))
    return f"def f(x):\n    y = 0\n{branches}    return y\n"


@pytest.fixture
def repo(make_context: MakeContext) -> RunContext:
    """Ana made three files 400 days ago; since then Bo and Ana changed ``big.py`` five times."""

    ctx = make_context()
    root = ctx.workspace.root
    init_repo(root)
    commit(
        root,
        {
            "src/legacy.py": _busy(4),
            "src/big.py": _busy(0),
            "src/small.py": "VALUE = 1\n",
            "README.md": "# demo\n",
            "uv.lock": "x = 1\n",
        },
        "start",
        "Ana",
        400,
    )
    for step in range(1, 6):
        commit(
            root,
            {"src/big.py": _busy(step), "uv.lock": f"x = {step + 1}\n"},
            f"change {step}",
            "Bo" if step % 2 else "Ana",
            10 - step,
        )
    commit(root, {"src/small.py": "VALUE = 2\n"}, "tweak", "Ana", 1)
    return ctx


def test_parse_log_reads_authors_times_and_per_file_line_counts() -> None:
    text = (
        "\x1eabc\x1fAna\x1f1700000000\n\n3\t1\tsrc/a.py\n-\t-\timg/logo.png\n"
        "\x1edef\x1fBo\x1f1700086400\n\n0\t5\tsrc/b.py\n"
    )

    commits = parse_log(text)

    assert [(c.author, c.when) for c in commits] == [("Ana", 1700000000), ("Bo", 1700086400)]
    assert commits[0].files == (FileChange("src/a.py", 3, 1), FileChange("img/logo.png", 0, 0))
    assert commits[1].files == (FileChange("src/b.py", 0, 5),)


def test_parse_blame_maps_final_line_numbers_to_author_and_time() -> None:
    sha = "a" * 40
    text = (
        f"{sha} 4 7 1\nauthor Ana\nauthor-mail <a@x>\nauthor-time 1700000000\nsummary s\n\tcode\n"
        f"{'0' * 40} 9 9 1\nauthor Not Committed Yet\nauthor-time 1700000500\n\tmore\n"
    )

    assert parse_blame(text) == {
        7: Blame("Ana", 1700000000),
        9: Blame("uncommitted", None),
    }


def test_history_reads_commits_inside_the_window(repo: RunContext) -> None:
    history = GitHistory(repo)

    recent = history.commits(days=365)
    everything = history.commits(days=0)

    assert len(recent) == 6 and len(everything) == 7
    assert {c.author for c in recent} == {"Ana", "Bo"}


def test_history_without_a_repository_says_so(make_context: MakeContext) -> None:
    ctx = make_context("plain")

    with pytest.raises(GitUnavailable, match="not a Git repository"):
        GitHistory(ctx).commits(days=30)


def test_hotspots_rank_files_by_churn_times_complexity(repo: RunContext) -> None:
    report = compute_hotspots(repo, days=365, top=5)

    paths = [spot.path for spot in report.spots]
    assert paths[0] == "src/big.py"
    assert "src/legacy.py" not in paths  # only touched outside the window
    assert "uv.lock" not in paths  # lock files churn without meaning
    assert report.commits == 6
    big = report.spots[0]
    assert (big.commits, big.authors) == (5, 2)
    assert big.score > report.spots[1].score
    assert "src/legacy.py" in [s.path for s in compute_hotspots(repo, days=0, top=5).spots]


def test_hotspots_can_be_limited_to_a_directory(repo: RunContext) -> None:
    report = compute_hotspots(repo, days=365, top=5, path_prefix="README.md")

    assert [spot.path for spot in report.spots] == []


def test_hotspot_report_is_compact_and_says_what_the_score_means(repo: RunContext) -> None:
    text = format_hotspots(compute_hotspots(repo, days=365, top=2))

    lines = text.splitlines()
    assert lines[0].startswith("Hotspots: top 2 of ")
    assert "src/big.py" in lines[1]
    assert "score = commits" in text


def test_scan_todos_finds_tagged_comments_in_any_text_file(make_context: MakeContext) -> None:
    ctx = make_context()
    ws = ctx.workspace
    ws.write_file("a.py", "x = 1  # TODO(ana): split this\n# FIXME: broken\nTODO_LIST = []\n")
    ws.write_file("b.ts", "// HACK: skip\n/* TODO later */\nconst todo = 1;\n")
    ws.write_file("c.md", "<!-- XXX remove -->\n")
    ws.write_file("node_modules/x/y.js", "// TODO: ignored\n")

    todos = scan_todos(ws, tags=("TODO", "FIXME", "HACK"))

    assert [(t.path, t.line, t.tag, t.owner, t.text) for t in todos] == [
        ("a.py", 1, "TODO", "ana", "split this"),
        ("a.py", 2, "FIXME", None, "broken"),
        ("b.ts", 1, "HACK", None, "skip"),
        ("b.ts", 2, "TODO", None, "later"),
    ]


def test_todos_get_the_author_and_age_from_blame(repo: RunContext) -> None:
    root = repo.workspace.root
    commit(root, {"src/notes.py": "x = 1\n# TODO: tidy\n"}, "note", "Bo", 40)
    (root / "src" / "notes.py").write_text("x = 1\n# TODO: tidy\ny = 2  # FIXME: new\n")
    (root / "src" / "fresh.py").write_text("# TODO: untracked file\n")
    todos = scan_todos(repo.workspace, tags=("TODO", "FIXME"), path_prefix="src")

    enriched = with_blame(repo, todos, now=None)
    text = format_todos(enriched, limit=10, git_note=None)

    by_text = {item.todo.text: item for item in enriched}
    assert (by_text["tidy"].author, age_label(by_text["tidy"].age_days)) == ("Bo", "40d")
    assert by_text["new"].author == "uncommitted"
    assert by_text["untracked file"].author is None
    assert "src/notes.py:2 TODO tidy  (Bo, 40d)" in text
    assert "src/fresh.py:1 TODO untracked file" in text


def test_todos_without_git_are_still_listed(make_context: MakeContext) -> None:
    ctx = make_context("plain")
    ctx.workspace.write_file("a.py", "# TODO: x\n")
    todos = scan_todos(ctx.workspace, tags=("TODO",))

    enriched = with_blame(ctx, todos, now=None)

    assert [item.author for item in enriched] == [None]


@pytest.mark.parametrize(
    ("days", "label"),
    [(0, "today"), (1, "1d"), (40, "40d"), (59, "59d"), (75, "2mo"), (400, "1y"), (None, "")],
)
def test_age_labels(days: float | None, label: str) -> None:
    assert age_label(days) == label
