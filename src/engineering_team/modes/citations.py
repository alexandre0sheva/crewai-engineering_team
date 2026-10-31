"""Checking an answer's citations against the project and the run (controller code, no model).

A code citation holds when its path is a project file, its line range is inside the file and the
quoted text is on those lines (whitespace-normalised). A web citation holds when a ``Fetch URL``
call of the run succeeded for that page: an agent's claim to have read a page is not evidence.
The check is for the tree the run worked on; a read-only run does not change it
(``changed_files``).
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit

from engineering_team.modes.answer_contracts import (
    Answer,
    AnswerCheck,
    Citation,
    CitationResult,
    CitationStatus,
)
from engineering_team.runtime.trajectory import read_trajectory

MAX_FILE_BYTES = 2_000_000
STATE_DIRECTORY = ".engineering-team"
FETCH_TOOL = "Fetch URL"
LINES = re.compile(r"^\s*(\d+)\s*(?:[-–]\s*(\d+))?\s*$")
FETCHED = re.compile(r"^Fetched (\S+) \(HTTP")
DETAILS: dict[str, str] = {
    "missing_file": "no such file in the project",
    "outside_project": "not a file of the project",
    "bad_lines": "give the lines as N or N-M, inside the file",
    "no_quote": "quote the text the claim rests on",
    "quote_mismatch": "the cited lines do not contain the quote",
    "not_fetched": "no Fetch URL call of this run read that page",
    "no_source": "give a path with lines and a quote, or the URL of a fetched page",
}


def normalise(text: str) -> str:
    return " ".join(text.split())


def canonical_url(url: str) -> str:
    """The page a URL names, for comparing: scheme and host in lower case, no query or fragment,
    no trailing slash."""

    parts = urlsplit(url.strip())
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}{parts.path.rstrip('/')}"


def fetched_urls(trajectory: Path) -> set[str]:
    """The pages the run read: every successful ``Fetch URL`` call, as asked and as answered
    (after redirects), from the trajectory the tools wrote."""

    found: set[str] = set()
    for entry in read_trajectory(trajectory):
        if entry.kind != "tool" or entry.tool != FETCH_TOOL or not entry.ok:
            continue
        if isinstance(asked := entry.args.get("url"), str) and asked:
            found.add(canonical_url(asked))
        if (header := FETCHED.match(entry.output)) is not None:
            found.add(canonical_url(header.group(1)))
    return found


def _result(
    number: int, citation: Citation, status: CitationStatus, detail: str = ""
) -> CitationResult:
    return CitationResult(
        number=number,
        kind=citation.kind,
        target=citation.target,
        status=status,
        detail=detail or DETAILS.get(status, ""),
    )


def project_file(root: Path, path: str) -> tuple[Path | None, CitationStatus]:
    """The project file ``path`` names, or why it is not one."""

    cleaned = path.strip().replace("\\", "/").removeprefix("./")
    if not cleaned or cleaned.startswith(("/", "~")) or ".." in cleaned.split("/"):
        return None, "outside_project"
    if cleaned.split("/")[0] == STATE_DIRECTORY:
        return None, "outside_project"
    target = root / cleaned
    try:
        resolved = target.resolve()
        resolved.relative_to(root.resolve())
    except (OSError, ValueError):
        return None, "outside_project"
    return (resolved, "verified") if resolved.is_file() else (None, "missing_file")


def check_code(root: Path, citation: Citation) -> tuple[CitationStatus, str]:
    file, status = project_file(root, citation.path)
    if file is None:
        return status, ""
    match = LINES.match(citation.lines)
    if match is None:
        return "bad_lines", ""
    if not citation.quote.strip():
        return "no_quote", ""
    try:
        with file.open("rb") as handle:
            data = handle.read(MAX_FILE_BYTES + 1)
    except OSError:
        return "missing_file", ""
    lines = data[:MAX_FILE_BYTES].decode("utf-8", errors="replace").splitlines()
    first = int(match.group(1))
    last = int(match.group(2) or first)
    if first < 1 or last < first or last > len(lines):
        return "bad_lines", f"{citation.path} has {len(lines)} lines"
    if normalise(citation.quote) not in normalise(" ".join(lines[first - 1 : last])):
        return "quote_mismatch", ""
    return "verified", ""


def verify_citations(answer: Answer, root: Path, fetched: set[str]) -> list[CitationResult]:
    """One result per citation, in order."""

    results: list[CitationResult] = []
    for number, citation in enumerate(answer.citations, 1):
        if citation.path.strip():
            status, detail = check_code(root, citation)
        elif citation.url.strip():
            ok = citation.url.startswith(("http://", "https://")) and (
                canonical_url(citation.url) in fetched
            )
            status, detail = ("verified" if ok else "not_fetched"), ""
        else:
            status, detail = "no_source", ""
        results.append(_result(number, citation, status, detail))
    return results


def verdict_of(check: AnswerCheck) -> tuple[str, list[str]]:
    """How the answer stands: ``verified`` when every citation holds, ``partial`` when some do
    not (or it cites nothing), ``failed`` when none holds. The second value says why."""

    if not check.results:
        return "partial", ["The answer cites nothing, so nothing in it is checked."]
    problems = [f"[{r.number}] {r.target}: {r.detail}" for r in check.unverified]
    if check.changed:
        return "failed", [f"The project was changed: {', '.join(check.changed[:5])}.", *problems]
    if not problems:
        return "verified", []
    return ("partial" if check.verified else "failed"), problems
