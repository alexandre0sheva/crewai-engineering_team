"""Documentation checks CI runs: every environment variable the code reads is documented, every
setting has exactly one row in ``docs/CONFIGURATION.md``, and every relative link in the Markdown
files points at something that exists.

    uv run python scripts/check_docs.py

Exit status 1 lists each problem. Links are checked as files, and, for ``.md`` targets with a
``#fragment``, against the headings of the target (GitHub's slug rules). External links (``http``,
``mailto``) are not fetched.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "src" / "engineering_team"
CONFIGURATION = ROOT / "docs" / "CONFIGURATION.md"
ENV_NAME = re.compile(r"\bENGINEERING_[A-Z][A-Z0-9_]*\b")
COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)  # HTML comment markers are not variables
LINK = re.compile(r"(?<!\!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)|!\[[^\]]*\]\(([^)\s]+)\)")
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
FENCE = re.compile(r"^\s*(```|~~~)")
SKIP_DIRECTORIES = {".git", ".venv", "node_modules", "dist", "build", "workspace", "sandbox"}
# Run records and generated or third-party trees whose Markdown is not ours to check.
SKIP_PARTS = {"fixture", "reference", ".engineering-team", "results", "knowledge", ".runs"}


def markdown_files() -> list[Path]:
    found = []
    for path in sorted(ROOT.rglob("*.md")):
        parts = set(path.relative_to(ROOT).parts)
        if parts & SKIP_DIRECTORIES or (parts & SKIP_PARTS and "examples" not in parts):
            continue
        if "repo" in parts and "examples" in parts:
            continue  # the example projects' own READMEs describe those projects, not this one
        found.append(path)
    return found


def slug(heading: str) -> str:
    """GitHub's anchor for a heading: lowercase, markup and punctuation dropped, spaces to ``-``."""

    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading)
    # Code spans keep their text as it is; HTML tags outside them are dropped.
    pieces = text.split("`")
    text = (
        "".join(
            piece if index % 2 else re.sub(r"<[^>]+>", "", piece)
            for index, piece in enumerate(pieces)
        )
        .strip()
        .lower()
    )
    text = re.sub(r"[^\w\- ]", "", text, flags=re.UNICODE)
    return text.replace(" ", "-")


def anchors(path: Path) -> set[str]:
    found: set[str] = set()
    counts: dict[str, int] = {}
    in_fence = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if FENCE.match(line):
            in_fence = not in_fence
        match = None if in_fence else HEADING.match(line)
        if match:
            base = slug(match.group(2))
            seen = counts.get(base, 0)
            counts[base] = seen + 1
            found.add(base if seen == 0 else f"{base}-{seen}")
    return found


def link_problems() -> list[str]:
    problems: list[str] = []
    cache: dict[Path, set[str]] = {}
    for path in markdown_files():
        in_fence = False
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if FENCE.match(line):
                in_fence = not in_fence
            if in_fence:
                continue
            for match in LINK.finditer(re.sub(r"`[^`]*`", "", line)):
                target = match.group(1) or match.group(2)
                if re.match(r"[a-z][a-z0-9+.-]*:", target) or target.startswith("//"):
                    continue
                where = f"{path.relative_to(ROOT)}:{number}"
                name, _, fragment = target.partition("#")
                destination = (path.parent / unquote(name)).resolve() if name else path
                if not destination.exists():
                    problems.append(f"{where}: {target} does not exist")
                elif fragment and destination.suffix == ".md":
                    known = cache.setdefault(destination, anchors(destination))
                    if unquote(fragment).lower() not in known:
                        problems.append(f"{where}: {target}: no such heading")
    return problems


def environment_problems() -> list[str]:
    documented = set(ENV_NAME.findall(CONFIGURATION.read_text(encoding="utf-8")))
    used: dict[str, str] = {}
    for path in sorted(SOURCE.rglob("*.py")):
        for name in ENV_NAME.findall(COMMENT.sub("", path.read_text(encoding="utf-8"))):
            used.setdefault(name, path.relative_to(ROOT).as_posix())
    return [
        f"{name} is read in {used[name]} but not documented in docs/CONFIGURATION.md"
        for name in sorted(set(used) - documented)
    ]


SETTING_ROW = re.compile(r"^\|\s*((?:`[^`]+`(?:,\s*)?)+)\s*\|")


def duplicate_setting_problems() -> list[str]:
    """A setting is documented once: a second table row for the same key would drift."""

    seen: dict[str, int] = {}
    problems: list[str] = []
    for number, line in enumerate(CONFIGURATION.read_text(encoding="utf-8").splitlines(), 1):
        match = SETTING_ROW.match(line)
        for key in re.findall(r"`([^`]+)`", match.group(1)) if match else []:
            if key in seen:
                problems.append(
                    f"docs/CONFIGURATION.md:{number}: `{key}` already has a row (line {seen[key]})"
                )
            seen.setdefault(key, number)
    return problems


def main() -> int:
    problems = [*environment_problems(), *duplicate_setting_problems(), *link_problems()]
    for problem in problems:
        print(problem)
    if problems:
        print(f"{len(problems)} documentation problem(s).")
        return 1
    print(f"Documentation checks passed ({len(markdown_files())} Markdown files).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
