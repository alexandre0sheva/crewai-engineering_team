"""Write scopes: gitignore-style path ownership for agents that share a workspace."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache


@lru_cache(maxsize=512)
def compile_glob(pattern: str) -> re.Pattern[str]:
    """Translate one gitignore-style glob into a regex over POSIX relative paths.

    ``*`` and ``?`` stay inside one path segment, ``**`` crosses segments, a pattern with no
    slash (or only a trailing one) matches at any depth, a leading ``/`` anchors it to the
    workspace root, and a match on a directory covers everything below it.
    """

    body = pattern.strip()
    anchored = body.startswith("/") or "/" in body.rstrip("/")
    body = body.strip("/")
    parts: list[str] = []
    index = 0
    while index < len(body):
        if body.startswith("**/", index):
            parts.append("(?:.*/)?")
            index += 3
        elif body.startswith("**", index):
            parts.append(".*")
            index += 2
        elif body[index] == "*":
            parts.append("[^/]*")
            index += 1
        elif body[index] == "?":
            parts.append("[^/]")
            index += 1
        else:
            parts.append(re.escape(body[index]))
            index += 1
    prefix = "" if anchored else "(?:.*/)?"
    return re.compile(prefix + "".join(parts) + "(?:/.*)?")


@dataclass(frozen=True)
class WriteScope:
    """The paths an agent may change. Reads are never restricted by a scope.

    ``deny`` wins over ``allow``; an empty ``allow`` permits nothing.
    """

    allow: tuple[str, ...]
    deny: tuple[str, ...] = field(default=())

    def permits(self, relative_path: str) -> bool:
        path = relative_path.strip("/")
        if any(compile_glob(pattern).fullmatch(path) for pattern in self.deny):
            return False
        return any(compile_glob(pattern).fullmatch(path) for pattern in self.allow)

    def denial(self, relative_path: str) -> str:
        """Why ``relative_path`` was refused, naming the paths the caller owns."""

        owned = ", ".join(self.allow) or "(nothing)"
        excluded = f" (except {', '.join(self.deny)})" if self.deny else ""
        return (
            f"'{relative_path}' is outside your write scope. You may only change: "
            f"{owned}{excluded}. Reading is unrestricted; ask the engineering lead to "
            "assign changes elsewhere."
        )
