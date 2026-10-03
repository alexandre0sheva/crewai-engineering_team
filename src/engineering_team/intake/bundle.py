"""The request bundle: every way a request arrives, merged into one normalised text.

``RequestBundle.from_sources`` is the single door: the CLI (``--request``, ``--request-file``,
stdin), the library, and the web UI (a textarea plus uploaded files) all call it, so the same
request text yields the same bundle ``hash`` however it was supplied. One source is used as is;
several are joined in the order *inline text, then files in the order given* (``-`` is stdin),
each under a ``## Request: <label>`` header.
"""

from __future__ import annotations

import hashlib
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from engineering_team.intake.context_docs import ContextScan, scan_context_dir
from engineering_team.intake.errors import IntakeError
from engineering_team.intake.templates import TEMPLATE_MARKER
from engineering_team.settings import IntakeSettings

STDIN = "-"
BYTES_PER_CHAR = 4  # a UTF-8 character is at most four bytes: bounds what a file read can cost


def request_hash(requirements: str) -> str:
    """The hash recorded in the manifest, so a changed request is never mistaken for the old one."""

    return hashlib.sha256(requirements.strip().encode("utf-8")).hexdigest()


def normalise(text: str) -> str:
    """Unix line endings, no byte-order mark or NUL, no leading or trailing blank space."""

    return (
        text.removeprefix("﻿").replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "").strip()
    )


@dataclass(frozen=True)
class RequestPart:
    """One source that went into the bundle (for display and audit)."""

    label: str
    chars: int


@dataclass(frozen=True)
class RequestBundle:
    """The normalised request, where its parts came from, and the reference documents."""

    text: str
    parts: tuple[RequestPart, ...] = ()
    context: ContextScan | None = None

    @property
    def hash(self) -> str:
        return request_hash(self.text)

    @classmethod
    def from_sources(
        cls,
        *,
        text: str | None = None,
        files: Sequence[str | Path] = (),
        stdin: TextIO | None = None,
        context_dir: str | Path | None = None,
        limits: IntakeSettings | None = None,
    ) -> RequestBundle:
        """Merge ``text`` and ``files`` (a ``-`` entry reads ``stdin``, default ``sys.stdin``).

        Raises :class:`IntakeError` for no source, an empty or still-templated source, an
        unreadable file, a request over ``intake.max_request_chars``, or a bad ``context_dir``.
        """

        limits = limits or IntakeSettings()
        pieces: list[tuple[str, str]] = []
        if text is not None:
            pieces.append(("inline request", text))
        for entry in files:
            if str(entry) == STDIN:
                pieces.append(("stdin", _read_stdin(stdin, limits)))
            else:
                pieces.append((Path(entry).name, _read_file(Path(entry), limits)))
        if not pieces:
            raise IntakeError("No project request given. Pass --request or --request-file.")
        parts: list[tuple[str, str]] = []
        for label, raw in pieces:
            cleaned = normalise(raw)
            if not cleaned:
                raise IntakeError(f"The project request is empty ({label}).")
            if TEMPLATE_MARKER in cleaned:
                raise IntakeError(
                    f"The project request is still a template ({label}). Replace it with "
                    "concrete MVP requirements, or pass --request/--request-file/--example."
                )
            parts.append((label, cleaned))
        merged = parts[0][1] if len(parts) == 1 else _join(parts)
        if len(merged) > limits.max_request_chars:
            raise IntakeError(
                f"The project request is {len(merged):,} characters; the limit is "
                f"{limits.max_request_chars:,} (setting intake.max_request_chars). Keep the "
                "request short and put reference material in --context-dir."
            )
        scan = scan_context_dir(context_dir, limits) if context_dir is not None else None
        return cls(merged, tuple(RequestPart(label, len(body)) for label, body in parts), scan)


def _join(parts: list[tuple[str, str]]) -> str:
    seen: dict[str, int] = {}
    blocks: list[str] = []
    for label, body in parts:
        seen[label] = seen.get(label, 0) + 1
        shown = label if seen[label] == 1 else f"{label} ({seen[label]})"
        blocks.append(f"## Request: {shown}\n\n{body}")
    return "\n\n".join(blocks)


def _read_file(path: Path, limits: IntakeSettings) -> str:
    expanded = path.expanduser()
    if not expanded.is_file():
        raise IntakeError(
            f"Request file not found: {expanded}. Pass --request, --request-file, or --example."
        )
    try:
        with expanded.open("rb") as handle:
            raw = handle.read(limits.max_request_chars * BYTES_PER_CHAR + 1)
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise IntakeError(f"Request file {expanded} is not UTF-8 text.") from None
    except OSError as exc:
        raise IntakeError(f"Cannot read request file {expanded}: {exc}") from exc


def _read_stdin(stdin: TextIO | None, limits: IntakeSettings) -> str:
    stream = stdin if stdin is not None else sys.stdin
    try:
        return stream.read(limits.max_request_chars + 1)
    except (OSError, UnicodeDecodeError) as exc:
        raise IntakeError(f"Cannot read the request from stdin: {exc}") from exc
