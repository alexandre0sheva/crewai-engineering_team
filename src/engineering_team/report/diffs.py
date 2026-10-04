"""``changes.patch`` as per-file stats and a bounded unified diff for the report viewer."""

from __future__ import annotations

import re

from engineering_team.report.model import DiffFile, DiffView

MAX_FILE_LINES = 400  # diff lines shown per file
MAX_TOTAL_LINES = 3000  # diff lines shown in all
MAX_LINE_CHARS = 400  # a minified bundle in a diff must not become one enormous line

_HEADER = re.compile(r"^diff --git a/(?P<old>.*?) b/(?P<new>.*)$")


def _clip(line: str) -> str:
    extra = len(line) - MAX_LINE_CHARS
    return line if extra <= 0 else f"{line[:MAX_LINE_CHARS]} … [{extra} more characters]"


def parse_patch(text: str, patch_name: str) -> DiffView | None:
    """The files of a ``git diff`` patch with their line counts and (bounded) diff lines;
    ``None`` when ``text`` holds no file diff."""

    sections: list[list[str]] = []
    for line in text.splitlines():
        if line.startswith("diff --git "):
            sections.append([line])
        elif sections:
            sections[-1].append(line)
    if not sections:
        return None

    files: list[DiffFile] = []
    budget = MAX_TOTAL_LINES
    for section in sections:
        head = _HEADER.match(section[0])
        path = head["new"] if head else section[0][len("diff --git ") :]
        status = "modified"
        binary = False
        added = removed = 0
        shown: list[str] = []
        in_hunk = False
        for line in section[1:]:
            if not in_hunk:
                if line.startswith("@@"):
                    in_hunk = True
                else:
                    if line.startswith("new file mode"):
                        status = "added"
                    elif line.startswith("deleted file mode"):
                        status = "deleted"
                    elif line.startswith("rename from"):
                        status = "renamed"
                    elif line.startswith(("Binary files", "GIT binary patch")):
                        binary = True
                    continue
            if line.startswith("+"):
                added += 1
            elif line.startswith("-"):
                removed += 1
            shown.append(line)
        keep = shown[: min(MAX_FILE_LINES, max(budget, 0))]
        budget -= len(keep)
        files.append(
            DiffFile(
                path=path,
                status=status,
                added=added,
                removed=removed,
                lines=[_clip(line) for line in keep],
                omitted=len(shown) - len(keep),
                binary=binary,
            )
        )
    files.sort(key=lambda item: item.path)
    return DiffView(
        files=files,
        added=sum(f.added for f in files),
        removed=sum(f.removed for f in files),
        patch_name=patch_name,
        truncated=any(f.omitted for f in files),
    )
