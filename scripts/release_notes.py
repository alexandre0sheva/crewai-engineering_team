"""The release notes of one version, taken from CHANGELOG.md.

    uv run python scripts/release_notes.py 0.2.0        # print that version's section
    uv run python scripts/release_notes.py --check      # pyproject's version has a dated section

The release workflow uses the first form for the GitHub Release body and the second to refuse a tag
whose version has no changelog entry.
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HEADING = re.compile(r"^## \[(?P<version>[^\]]+)\](?: — (?P<date>\d{4}-\d{2}-\d{2}))?\s*$", re.M)


def section(changelog: str, version: str) -> str | None:
    """The body under ``## [version] — date`` (up to the next ``## [``), or ``None``."""

    headings = list(HEADING.finditer(changelog))
    for index, found in enumerate(headings):
        if found["version"] == version:
            end = headings[index + 1].start() if index + 1 < len(headings) else len(changelog)
            body = changelog[found.end() : end]
            body = re.split(r"^\[[^\]]+\]: https?://", body, maxsplit=1, flags=re.M)[0]
            return body.strip()
    return None


def project_version() -> str:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def check(changelog: str, version: str) -> list[str]:
    """What stops ``version`` from being released, as one line each."""

    problems = []
    dated = {m["version"] for m in HEADING.finditer(changelog) if m["date"]}
    if version not in dated:
        problems.append(f"CHANGELOG.md has no dated '## [{version}] — YYYY-MM-DD' section.")
    elif not (section(changelog, version) or "").strip():
        problems.append(f"The CHANGELOG.md section of {version} is empty.")
    if f"[{version}]: https://" not in changelog:
        problems.append(f"CHANGELOG.md has no compare link for [{version}] at the end.")
    return problems


def main(argv: list[str]) -> int:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    if argv == ["--check"]:
        problems = check(changelog, project_version())
        for problem in problems:
            print(problem)
        return 1 if problems else 0
    if len(argv) != 1 or argv[0].startswith("-"):
        print(__doc__)
        return 2
    version = argv[0].removeprefix("v")
    body = section(changelog, version)
    if not body:
        print(f"No CHANGELOG.md section for {version}.", file=sys.stderr)
        return 1
    print(body)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
