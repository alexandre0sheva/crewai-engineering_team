"""Modules stay small enough to read and to change without colliding: split by responsibility."""

from __future__ import annotations

from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "src"
MAX_LINES = 450
# Module path (relative to ``src/``) -> why it may be longer. Keep this empty if you can.
ALLOWED: dict[str, str] = {}


def line_count(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


def test_no_module_is_longer_than_the_limit_outside_the_allowlist() -> None:
    too_long = {
        path.relative_to(SOURCE).as_posix(): line_count(path)
        for path in sorted(SOURCE.rglob("*.py"))
        if line_count(path) > MAX_LINES
    }

    unexpected = {name: lines for name, lines in too_long.items() if name not in ALLOWED}
    assert not unexpected, f"split these modules (over {MAX_LINES} lines): {unexpected}"


def test_the_allowlist_only_names_modules_that_still_exceed_the_limit() -> None:
    stale = [
        name
        for name in ALLOWED
        if not (SOURCE / name).is_file() or line_count(SOURCE / name) <= MAX_LINES
    ]

    assert not stale, f"remove from ALLOWED, they are within the limit now: {stale}"
