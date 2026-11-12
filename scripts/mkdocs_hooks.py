"""MkDocs hooks for the website: nothing in ``docs/`` is copied, the build adapts instead.

- The run reports under ``examples/`` (``report.html``, and ``report-live.html`` for a live run) are
  added to the site as ``reports/<example>.html`` / ``reports/<example>-live.html``, so the examples
  page can link to real, self-contained report pages.
- A Markdown link that leaves ``docs/`` (``../README.md``, ``../examples/``, ``../benchmarks/...``)
  works on GitHub but would be a dead link on the site: it becomes a link to the same path in the
  repository.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from mkdocs.structure.files import File, Files

ROOT = Path(__file__).resolve().parent.parent
REPOSITORY = "https://github.com/alexandre0sheva/crewai-engineering_team"
REPORTS = {"report.html": "{name}.html", "report-live.html": "{name}-live.html"}
LINK = re.compile(r'(\]\()([^)\s#]*)(#[^)\s]*)?(\s+"[^"]*")?\)')
SCHEME = re.compile(r"[a-z][a-z0-9+.-]*:", re.IGNORECASE)


def example_reports() -> dict[str, Path]:
    """``reports/<name>.html`` -> the report file, for every example that has one."""

    found: dict[str, Path] = {}
    for example in sorted((ROOT / "examples").iterdir()):
        for source, published in REPORTS.items():
            path = example / source
            if path.is_file():
                found[f"reports/{published.format(name=example.name)}"] = path
    return found


def on_files(files: Files, config: Any) -> Files:
    for uri, path in example_reports().items():
        files.append(File.generated(config, uri, abs_src_path=str(path)))
    return files


def on_page_markdown(markdown: str, page: Any, config: Any, files: Any) -> str:
    here = Path(page.file.abs_src_path).parent
    docs = Path(config["docs_dir"]).resolve()

    def repoint(match: re.Match[str]) -> str:
        opening, target, fragment, title = match.groups()
        if not target or SCHEME.match(target) or target.startswith(("/", "//")):
            return match.group(0)
        resolved = (here / unquote(target)).resolve()
        if resolved.is_relative_to(docs) or not resolved.is_relative_to(ROOT):
            return match.group(0)
        if not resolved.exists():
            return match.group(0)  # left alone: the strict build reports it
        kind = "tree" if resolved.is_dir() else "blob"
        url = f"{REPOSITORY}/{kind}/main/{resolved.relative_to(ROOT).as_posix()}"
        return f"{opening}{url}{fragment or ''}{title or ''})"

    return LINK.sub(repoint, markdown)
