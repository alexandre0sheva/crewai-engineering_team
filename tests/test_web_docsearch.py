"""Search Docs: BM25 over repo markdown, context directories, and fetched pages (offline)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from engineering_team.tools.workspace import ProjectWorkspace
from engineering_team.webtools.docsearch import (
    Bm25Index,
    DocSources,
    chunk_document,
    collect_documents,
    format_hits,
    search_docs,
    tokenize,
)

AUTH = """# Authentication

Users sign in with a login form. The server issues a JWT token that expires after one hour.

## Refresh tokens

A refresh token lets the client obtain a new JWT without a new login.
"""
DEPLOY = """# Deployment

Build the Docker image and push it. Kubernetes pulls the image from the registry.

## Rollbacks

Roll back by redeploying the previous image tag.
"""


@pytest.fixture
def workspace(tmp_path: Path) -> ProjectWorkspace:
    ws = ProjectWorkspace.create(tmp_path / "project")
    ws.write_file("README.md", "# Demo app\n\nA small demo application for notes.\n")
    ws.write_file("docs/auth.md", AUTH)
    ws.write_file("docs/deploy.md", DEPLOY)
    ws.write_file("node_modules/pkg/README.md", "# Vendored\n\njwt jwt jwt login token\n")
    ws.write_file("src/code.py", "# jwt login token in code is not a document\n")
    hidden = ws.root / ".engineering-team" / "runs" / "x" / "notes"
    hidden.mkdir(parents=True)
    (hidden / "jwt.md").write_text("jwt login token\n", encoding="utf-8")
    return ws


@pytest.fixture
def context_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "company-docs"
    directory.mkdir()
    (directory / "payments.md").write_text(
        "# Payments API\n\nCall the invoice endpoint with an idempotency key.\n", encoding="utf-8"
    )
    return directory


def sources(ws: ProjectWorkspace, tmp_path: Path, **kwargs: object) -> DocSources:
    return DocSources(workspace=ws, cache_dir=tmp_path / "web-cache", **kwargs)  # type: ignore[arg-type]


def test_tokenize_splits_identifiers_and_drops_stop_words() -> None:
    assert tokenize("parseConfig and the parse_config() HTTPServer") == [
        "parse",
        "config",
        "parse_config",
        "parse",
        "config",
        "http",
        "server",
    ]
    assert "the" not in tokenize("the quick fox") and "quick" in tokenize("the quick fox")
    assert tokenize("v2 API 404") == ["v2", "api", "404"]


def test_rarer_terms_and_shorter_chunks_score_higher() -> None:
    chunks = [
        _chunk("a", "common common common rare"),
        _chunk("b", "common common common common common common common common rare"),
        _chunk("c", "common common"),
    ]

    hits = Bm25Index(chunks).search("rare common", 3)

    assert [hit.chunk.path for hit in hits] == ["a", "b", "c"]
    assert hits[0].score > hits[1].score > hits[2].score > 0


def _chunk(path: str, text: str):  # type: ignore[no-untyped-def]
    from engineering_team.webtools.docsearch import Chunk

    return Chunk("repo", path, 1, "", text)


def test_documents_come_from_repo_markdown_context_dirs_and_the_web_cache(
    workspace: ProjectWorkspace, context_dir: Path, tmp_path: Path
) -> None:
    cache = tmp_path / "web-cache"
    cache.mkdir()
    (cache / "abc.md").write_text(
        "source: https://example.org/page\n\n# Cached\n\nbody", encoding="utf-8"
    )

    docs, notes = collect_documents(sources(workspace, tmp_path, context_dirs=[str(context_dir)]))

    paths = {(doc.source, doc.path) for doc in docs}
    assert ("repo", "README.md") in paths and ("repo", "docs/auth.md") in paths
    assert ("context", "company-docs/payments.md") in paths
    assert ("web-cache", "web-cache/abc.md") in paths
    assert not any("node_modules" in path or ".engineering-team" in path for _, path in paths)
    assert not any(path.endswith(".py") for _, path in paths)
    assert notes == []


def test_a_missing_context_dir_is_noted_not_fatal(
    workspace: ProjectWorkspace, tmp_path: Path
) -> None:
    docs, notes = collect_documents(
        sources(workspace, tmp_path, context_dirs=[str(tmp_path / "nope")])
    )

    assert docs and notes == [f"context dir not found: {tmp_path / 'nope'}"]


def test_symlinks_and_oversized_files_in_context_dirs_are_skipped(
    workspace: ProjectWorkspace, context_dir: Path, tmp_path: Path
) -> None:
    secret = tmp_path / "secret.md"
    secret.write_text("# Secret\n\ntop secret", encoding="utf-8")
    os.symlink(secret, context_dir / "link.md")
    (context_dir / "huge.md").write_text("word " * 100_000, encoding="utf-8")

    docs, _ = collect_documents(sources(workspace, tmp_path, context_dirs=[str(context_dir)]))

    names = {doc.path for doc in docs if doc.source == "context"}
    assert names == {"company-docs/payments.md"}


def test_chunks_keep_their_heading_and_first_line() -> None:
    doc = next(iter(_docs({"docs/auth.md": AUTH})))

    chunks = chunk_document(doc)

    assert [(c.heading, c.line) for c in chunks] == [
        ("Authentication", 1),
        ("Refresh tokens", 5),
    ]
    assert "JWT token" in chunks[0].text and "refresh token" in chunks[1].text.lower()


def _docs(files: dict[str, str]):  # type: ignore[no-untyped-def]
    from engineering_team.webtools.docsearch import Document

    return [Document("repo", path, text) for path, text in files.items()]


def test_search_ranks_the_relevant_document_first(
    workspace: ProjectWorkspace, context_dir: Path, tmp_path: Path
) -> None:
    src = sources(workspace, tmp_path, context_dirs=[str(context_dir)])

    auth = search_docs(src, "jwt login token", 3)
    deploy = search_docs(src, "docker image kubernetes", 3)
    payments = search_docs(src, "idempotency key invoice", 3)
    camel = search_docs(src, "refreshToken", 3)

    assert auth.hits[0].chunk.path == "docs/auth.md"
    assert deploy.hits[0].chunk.path == "docs/deploy.md"
    assert (payments.hits[0].chunk.source, payments.hits[0].chunk.path) == (
        "context",
        "company-docs/payments.md",
    )
    assert camel.hits[0].chunk.heading == "Refresh tokens"
    assert len(auth.hits) <= 3


def test_heading_words_count_more_than_body_words(
    workspace: ProjectWorkspace, tmp_path: Path
) -> None:
    workspace.write_file("docs/a.md", "# Caching\n\nSome text about other things entirely here.\n")
    workspace.write_file("docs/b.md", "# Misc\n\nSome text about caching things entirely here.\n")

    result = search_docs(sources(workspace, tmp_path), "caching", 2)

    assert [hit.chunk.path for hit in result.hits] == ["docs/a.md", "docs/b.md"]


def test_the_source_filter_and_empty_results(
    workspace: ProjectWorkspace, context_dir: Path, tmp_path: Path
) -> None:
    src = sources(workspace, tmp_path, context_dirs=[str(context_dir)])

    only_context = search_docs(src, "payments invoice api", 5, source="context")
    nothing = search_docs(src, "zzzzqqq", 5)

    assert {hit.chunk.source for hit in only_context.hits} == {"context"}
    assert nothing.hits == []
    assert "No matching documents" in format_hits(nothing, "zzzzqqq")
    with pytest.raises(ValueError, match="source"):
        search_docs(src, "x", 5, source="elsewhere")


def test_the_report_names_source_path_line_heading_and_an_excerpt(
    workspace: ProjectWorkspace, tmp_path: Path
) -> None:
    result = search_docs(sources(workspace, tmp_path), "jwt token", 2)

    text = format_hits(result, "jwt token")

    lines = text.splitlines()
    assert lines[0].startswith("2 passage(s) for 'jwt token'") or lines[0].startswith(
        "1 passage(s)"
    )
    assert any(line.startswith("1. [repo] docs/auth.md:1 Authentication") for line in lines)
    assert "JWT token that expires" in text
    assert "scanned" in lines[0]


def test_a_query_with_only_stop_words_is_rejected(
    workspace: ProjectWorkspace, tmp_path: Path
) -> None:
    with pytest.raises(ValueError, match="searchable words"):
        search_docs(sources(workspace, tmp_path), "the and of", 3)
