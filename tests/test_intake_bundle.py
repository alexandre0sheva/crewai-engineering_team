"""Requirements intake: merging sources, stdin, the size cap, templates, and the bundle hash."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from engineering_team.intake import (
    MODES,
    TEMPLATE_MARKER,
    IntakeError,
    RequestBundle,
    request_hash,
    template_for,
)
from engineering_team.settings import IntakeSettings

TEXT = "Build a tiny notes CLI.\n\n- add stores a note\n- list prints notes"


def write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_the_same_text_has_the_same_hash_from_a_file_stdin_and_the_api(tmp_path: Path) -> None:
    from_file = RequestBundle.from_sources(files=[write(tmp_path / "r.md", TEXT + "\n")])
    from_stdin = RequestBundle.from_sources(files=["-"], stdin=io.StringIO(TEXT))
    from_api = RequestBundle.from_sources(text=TEXT)
    crlf = RequestBundle.from_sources(files=[write(tmp_path / "w.md", TEXT.replace("\n", "\r\n"))])

    assert from_file.hash == from_stdin.hash == from_api.hash == crlf.hash == request_hash(TEXT)
    assert from_api.text == TEXT  # one source is used as is: no header, so the hash is the same


def test_normalisation_unifies_line_endings_and_drops_bom_and_nul() -> None:
    bundle = RequestBundle.from_sources(text="﻿\n  one\r\ntwo\rthree\x00\n\n")

    assert bundle.text == "one\ntwo\nthree"


def test_sources_merge_in_a_fixed_order_with_headers(tmp_path: Path) -> None:
    first = write(tmp_path / "a.md", "from file a")
    second = write(tmp_path / "b.md", "from file b")

    bundle = RequestBundle.from_sources(
        text="inline text", files=[first, "-", second], stdin=io.StringIO("piped text")
    )

    assert bundle.text == (
        "## Request: inline request\n\ninline text\n\n"
        "## Request: a.md\n\nfrom file a\n\n"
        "## Request: stdin\n\npiped text\n\n"
        "## Request: b.md\n\nfrom file b"
    )
    assert [part.label for part in bundle.parts] == ["inline request", "a.md", "stdin", "b.md"]


def test_files_with_the_same_name_get_distinct_headers(tmp_path: Path) -> None:
    (tmp_path / "x").mkdir()
    (tmp_path / "y").mkdir()
    one, two = write(tmp_path / "x" / "r.md", "one"), write(tmp_path / "y" / "r.md", "two")

    bundle = RequestBundle.from_sources(files=[one, two])

    assert "## Request: r.md\n\none" in bundle.text and "## Request: r.md (2)\n\ntwo" in bundle.text


def test_the_size_cap_is_enforced_with_a_way_out(tmp_path: Path) -> None:
    limits = IntakeSettings(max_request_chars=500)

    with pytest.raises(IntakeError, match=r"limit is 500.*intake.max_request_chars.*--context-dir"):
        RequestBundle.from_sources(text="x" * 501, limits=limits)
    with pytest.raises(IntakeError, match="limit is 500"):
        RequestBundle.from_sources(files=[write(tmp_path / "big.md", "y" * 5000)], limits=limits)
    with pytest.raises(IntakeError, match="limit is 500"):
        RequestBundle.from_sources(files=["-"], stdin=io.StringIO("z" * 5000), limits=limits)
    assert RequestBundle.from_sources(text="x" * 500, limits=limits).text == "x" * 500


def test_the_cap_applies_to_the_merged_text() -> None:
    limits = IntakeSettings(max_request_chars=500)
    part = "p" * 300

    with pytest.raises(IntakeError, match="limit is 500"):
        RequestBundle.from_sources(text=part, files=["-"], stdin=io.StringIO(part), limits=limits)


@pytest.mark.parametrize("mode", MODES)
def test_every_template_is_rejected_until_edited(mode: str, tmp_path: Path) -> None:
    template = template_for(mode)

    assert template.startswith(TEMPLATE_MARKER)
    with pytest.raises(IntakeError, match="still a template"):
        RequestBundle.from_sources(text=template)
    with pytest.raises(IntakeError, match=r"still a template \(r\.md\)"):
        RequestBundle.from_sources(files=[write(tmp_path / "r.md", template)])
    with pytest.raises(IntakeError, match="still a template"):
        RequestBundle.from_sources(text="fine", files=["-"], stdin=io.StringIO(template))


def test_templates_ask_for_the_things_an_analyst_needs() -> None:
    for mode in MODES:
        text = template_for(mode).lower()
        assert all(word in text for word in ("acceptance criteria", "non-goals", "constraints"))
        assert "problem" in text and "users" in text


def test_unknown_template_mode_is_a_usage_error() -> None:
    with pytest.raises(ValueError, match="Choose one of: new, feature, fix, maintain"):
        template_for("rewrite")


def test_nothing_empty_and_nothing_missing_is_accepted(tmp_path: Path) -> None:
    with pytest.raises(IntakeError, match="No project request"):
        RequestBundle.from_sources()
    with pytest.raises(IntakeError, match=r"empty \(inline request\)"):
        RequestBundle.from_sources(text="  \n ")
    with pytest.raises(IntakeError, match="empty"):
        RequestBundle.from_sources(files=["-"], stdin=io.StringIO(""))
    with pytest.raises(IntakeError, match="does-not-exist.md"):
        RequestBundle.from_sources(files=[tmp_path / "does-not-exist.md"])
    binary = tmp_path / "bin.md"
    binary.write_bytes(b"\xff\xfe\x00bad")
    with pytest.raises(IntakeError, match="not UTF-8"):
        RequestBundle.from_sources(files=[binary])


def test_intake_errors_are_usage_errors() -> None:
    assert issubclass(IntakeError, ValueError)


def test_a_context_dir_is_scanned_with_the_bundle(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    write(docs / "api.md", "# API\nDetails")

    bundle = RequestBundle.from_sources(text=TEXT, context_dir=docs)

    assert bundle.context is not None and [f.relative for f in bundle.context.files] == ["api.md"]
    assert bundle.hash == request_hash(TEXT)  # context documents never change the request hash
    with pytest.raises(IntakeError, match="not a directory"):
        RequestBundle.from_sources(text=TEXT, context_dir=tmp_path / "missing")
