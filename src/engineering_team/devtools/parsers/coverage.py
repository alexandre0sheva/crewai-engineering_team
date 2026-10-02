"""Coverage reports: LCOV (jest, vitest, nyc, cargo-llvm-cov, coverage.py), Go profiles."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable

from engineering_team.devtools.models import FileCoverage
from engineering_team.devtools.parsers.common import rel_path


class CoverageError(ValueError):
    """The report is not in the expected format."""


def line_ranges(lines: Iterable[int]) -> list[str]:
    """``[1, 2, 3, 7, 9, 10]`` -> ``["1-3", "7", "9-10"]``."""

    ordered = sorted(set(lines))
    ranges: list[str] = []
    start = previous = None
    for number in ordered:
        if start is None:
            start = previous = number
        elif number == previous + 1:  # type: ignore[operator]
            previous = number
        else:
            ranges.append(str(start) if start == previous else f"{start}-{previous}")
            start = previous = number
    if start is not None:
        ranges.append(str(start) if start == previous else f"{start}-{previous}")
    return ranges


def parse_lcov(text: str, root: str | None = None) -> list[FileCoverage]:
    """LCOV ``SF:``/``DA:`` records; lines with zero hits are the uncovered ranges."""

    files: list[FileCoverage] = []
    path: str | None = None
    hits: dict[int, int] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("SF:"):
            path, hits = rel_path(line[3:], root), {}
        elif line.startswith("DA:") and path is not None:
            parts = line[3:].split(",")
            try:
                number, count = int(parts[0]), int(float(parts[1]))
            except (ValueError, IndexError):
                continue
            hits[number] = hits.get(number, 0) + count
        elif line == "end_of_record" and path is not None:
            missed = [number for number, count in hits.items() if count == 0]
            files.append(
                FileCoverage(
                    path=path,
                    covered=len(hits) - len(missed),
                    total=len(hits),
                    uncovered_ranges=line_ranges(missed),
                )
            )
            path = None
    if not files:
        raise CoverageError("no LCOV records (SF:/DA:/end_of_record) found")
    return files


GO_BLOCK = re.compile(
    r"^(?P<file>.+?):(?P<l1>\d+)\.\d+,(?P<l2>\d+)\.\d+ (?P<stmts>\d+) (?P<count>\d+)$"
)


def parse_go_cover(text: str, module: str | None = None) -> list[FileCoverage]:
    """A ``go test -coverprofile`` file; statements are what Go counts (not lines)."""

    covered: dict[str, int] = {}
    total: dict[str, int] = {}
    hit_lines: dict[str, set[int]] = {}
    miss_lines: dict[str, set[int]] = {}
    for raw in text.splitlines():
        match = GO_BLOCK.match(raw.strip())
        if not match:
            continue
        name = match["file"]
        if module and name.startswith(module + "/"):
            name = name[len(module) + 1 :]
        statements = int(match["stmts"])
        span = range(int(match["l1"]), int(match["l2"]) + 1)
        total[name] = total.get(name, 0) + statements
        if int(match["count"]) > 0:
            covered[name] = covered.get(name, 0) + statements
            hit_lines.setdefault(name, set()).update(span)
        else:
            miss_lines.setdefault(name, set()).update(span)
    if not total:
        raise CoverageError("no Go coverage blocks found; expected a -coverprofile file")
    return [
        FileCoverage(
            path=name,
            covered=covered.get(name, 0),
            total=total[name],
            uncovered_ranges=line_ranges(miss_lines.get(name, set()) - hit_lines.get(name, set())),
        )
        for name in sorted(total)
    ]


def parse_coverage_py_json(text: str, root: str | None = None) -> list[FileCoverage]:
    """``coverage json`` output."""

    try:
        data = json.loads(text)
        entries = data["files"]
    except (ValueError, KeyError, TypeError) as exc:
        raise CoverageError("not a coverage.py JSON report (run `coverage json`)") from exc
    return [
        FileCoverage(
            path=rel_path(name, root),
            covered=int(entry["summary"]["covered_lines"]),
            total=int(entry["summary"]["num_statements"]),
            uncovered_ranges=line_ranges(entry.get("missing_lines", [])),
        )
        for name, entry in sorted(entries.items())
    ]
