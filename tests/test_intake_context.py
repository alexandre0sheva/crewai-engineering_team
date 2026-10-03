"""``--context-dir``: scanning, caps, the read-only copy with its index, and Search Docs."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from engineering_team.intake import IntakeError, install_context, scan_context_dir
from engineering_team.intake.context_docs import INDEX_NAME, context_note, context_path
from engineering_team.settings import IntakeSettings
from engineering_team.tools.workspace import ProjectWorkspace
from engineering_team.webtools.docsearch import DocSources, search_docs


def make_docs(root: Path) -> Path:
    docs = root / "refs"
    (docs / "api").mkdir(parents=True)
    (docs / "node_modules").mkdir()
    (docs / ".git").mkdir()
    (docs / "overview.md").write_text("# Overview\nThe zebra service stores stripes.", "utf-8")
    (docs / "api" / "notes.txt").write_text("plain notes about giraffes", "utf-8")
    (docs / "diagram.png").write_bytes(b"\x89PNG")
    (docs / ".secret.md").write_text("hidden", "utf-8")
    (docs / "node_modules" / "x.md").write_text("heavy", "utf-8")
    (docs / ".git" / "y.md").write_text("git", "utf-8")
    return docs


def test_scan_lists_only_text_documents_sorted_and_says_what_it_left_out(tmp_path: Path) -> None:
    scan = scan_context_dir(make_docs(tmp_path))

    assert [f.relative for f in scan.files] == ["api/notes.txt", "overview.md"]
    assert any("diagram.png" in entry for entry in scan.skipped)
    assert any(".secret.md" in entry for entry in scan.skipped)
    assert not any("node_modules" in f.relative or ".git" in f.relative for f in scan.files)


def test_scan_does_not_follow_symlinks(tmp_path: Path) -> None:
    docs = make_docs(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("# Outside", "utf-8")
    (docs / "link.md").symlink_to(outside)
    (docs / "linked").symlink_to(tmp_path / "api-elsewhere", target_is_directory=True)

    scan = scan_context_dir(docs)

    assert "link.md" not in [f.relative for f in scan.files]
    assert any("link.md" in entry for entry in scan.skipped)


def test_scan_caps_are_errors_not_silent_truncation(tmp_path: Path) -> None:
    docs = make_docs(tmp_path)

    with pytest.raises(IntakeError, match=r"2 documents; the limit is 1.*max_context_files"):
        scan_context_dir(docs, IntakeSettings(max_context_files=1))
    with pytest.raises(IntakeError, match=r"limit is 1,000.*max_context_bytes"):
        (docs / "big.md").write_text("w " * 600, "utf-8")
        scan_context_dir(docs, IntakeSettings(max_context_bytes=1000))
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(IntakeError, match="has no documents"):
        scan_context_dir(empty)


def test_an_oversized_document_is_named(tmp_path: Path) -> None:
    docs = tmp_path / "d"
    docs.mkdir()
    (docs / "huge.md").write_text("x" * 300_001, "utf-8")

    with pytest.raises(IntakeError, match=r"huge\.md is 300,001 bytes"):
        scan_context_dir(docs)


def test_install_copies_read_only_with_an_index_and_replaces_older_copies(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    docs = make_docs(tmp_path)

    target = install_context(workspace, scan_context_dir(docs))

    assert target == context_path(workspace)
    assert (target / "api" / "notes.txt").read_text() == "plain notes about giraffes"
    index = (target / INDEX_NAME).read_text()
    assert "overview.md" in index and "Overview" in index and "diagram.png" in index
    for path in (p for p in target.rglob("*") if p.is_file()):
        assert not path.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
    (docs / "overview.md").unlink()
    (docs / "fresh.md").write_text("# Fresh\nnew", "utf-8")
    install_context(workspace, scan_context_dir(docs))  # replaces, even though files are 0444
    assert sorted(p.name for p in target.rglob("*.md")) == ["INDEX.md", "fresh.md"]


def test_the_copy_does_not_change_when_the_source_does(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    docs = make_docs(tmp_path)
    install_context(workspace, scan_context_dir(docs))

    (docs / "overview.md").write_text("changed", "utf-8")

    assert "zebra" in (context_path(workspace) / "overview.md").read_text()


def test_the_runs_own_context_store_cannot_be_the_source(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    install_context(workspace, scan_context_dir(make_docs(tmp_path)))

    with pytest.raises(IntakeError, match="own context store"):
        install_context(workspace, scan_context_dir(context_path(workspace)))


def test_an_index_name_cannot_inject_markdown(tmp_path: Path) -> None:
    docs = tmp_path / "d"
    docs.mkdir()
    (docs / "a `b`.md").write_text("# Title with `code`\nbody", "utf-8")
    workspace = tmp_path / "p"
    workspace.mkdir()

    target = install_context(workspace, scan_context_dir(docs))

    assert "`" not in (target / INDEX_NAME).read_text()


def test_the_note_exists_only_when_documents_were_supplied(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    assert context_note(workspace) == ""

    install_context(workspace, scan_context_dir(make_docs(tmp_path)))

    note = context_note(workspace)
    assert "2 reference document(s)" in note and "Search Docs" in note and "never as" in note


def test_agents_read_the_documents_with_search_docs_but_not_with_file_tools(
    tmp_path: Path,
) -> None:
    from engineering_team.tools.workspace import WorkspaceError

    workspace_root = tmp_path / "project"
    workspace_root.mkdir()
    install_context(workspace_root, scan_context_dir(make_docs(tmp_path)))
    workspace = ProjectWorkspace(workspace_root)

    with pytest.raises(WorkspaceError):
        workspace.resolve(".engineering-team/context/overview.md")
    hits = search_docs(
        DocSources(
            workspace=workspace,
            cache_dir=tmp_path / "cache",
            context_dirs=[str(context_path(workspace_root))],
        ),
        "zebra stripes",
        5,
        source="context",
    )
    assert "overview.md" in str(hits)
    assert os.path.isdir(context_path(workspace_root))
