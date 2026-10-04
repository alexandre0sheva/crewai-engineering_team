"""Small formatting helpers shared by the HTML and Markdown renderers."""

from __future__ import annotations

from datetime import datetime

MAX_LOG_CHARS = 4000


def clock(seconds: float) -> str:
    if seconds < 10:
        return f"{seconds:.1f}s"
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h{minutes:02d}m{secs:02d}s"
    return f"{minutes}m{secs:02d}s" if minutes else f"{secs}s"


def money(value: float | None) -> str:
    return f"${value:.4f}" if value is not None else "unknown"


def when(moment: datetime | None) -> str:
    return moment.strftime("%Y-%m-%d %H:%M:%S UTC") if moment else "-"


def time_of_day(moment: datetime) -> str:
    return moment.strftime("%H:%M:%S")


def size(count: int) -> str:
    return f"{count / 1024:.0f} KB" if count >= 1024 else f"{count} B"


def tail(text: str, limit: int = MAX_LOG_CHARS, log_path: str | None = None) -> str:
    """The last ``limit`` characters of ``text``, saying how much was left out and where the
    whole log is."""

    text = text.rstrip()
    if len(text) <= limit:
        return text
    where = f"; full log: {log_path}" if log_path else ""
    return f"[… {len(text) - limit} earlier characters omitted{where}]\n{text[-limit:]}"
