"""Showing setting values without showing secrets, and flattening them into rows."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

# --- masking ---------------------------------------------------------------------------------

_SECRET_KEY = re.compile(r"(key|token|secret|password|credential)", re.IGNORECASE)


def mask_url(value: str) -> str:
    """Hide credentials embedded in a URL (userinfo and query string)."""

    try:
        parts = urlsplit(value)
    except ValueError:
        return "****"
    if not parts.scheme or not parts.netloc:
        return value
    host = parts.netloc.rsplit("@", 1)[-1]
    netloc = f"***@{host}" if "@" in parts.netloc else host
    query = "***" if parts.query else ""
    return urlunsplit((parts.scheme, netloc, parts.path, query, ""))


def hide_address(value: str) -> str:
    """Keep only where a webhook points (``https://host/***``): its whole path is the secret."""

    try:
        parts = urlsplit(value)
    except ValueError:
        return "****"
    return f"{parts.scheme}://{parts.hostname}/***" if parts.hostname else "****"


def _hides_value(key: str) -> bool:
    """Settings whose values are secrets whatever they are called: a hook's address and the
    environment variables handed to an MCP server."""

    return (key.startswith("hooks.") and key.endswith(".url")) or (
        key.startswith("mcp.") and ".env." in key
    )


def mask_value(key: str, value: Any) -> str:
    if value is None or value == [] or value == "":
        return "(unset)"
    if _SECRET_KEY.search(key.rsplit(".", 1)[-1]):
        return "****"
    if _hides_value(key):
        return hide_address(str(value)) if key.endswith(".url") else "****"
    if isinstance(value, list):
        return ", ".join(mask_value(key, item) for item in value)
    if isinstance(value, str) and "://" in value:
        return mask_url(value)
    text = str(value)
    if len(text) > 80:  # e.g. a whole pasted request
        return f"{text[:60].splitlines()[0]}... ({len(text)} characters)"
    return text


def _flatten(value: Any, prefix: str = "") -> list[tuple[str, Any]]:
    if isinstance(value, dict):
        rows: list[tuple[str, Any]] = []
        for key, item in value.items():
            rows.extend(_flatten(item, f"{prefix}{key}."))
        return rows
    if isinstance(value, list) and value and all(isinstance(item, dict) for item in value):
        return [  # a list of tables (hooks): one row per field, numbered
            row for index, item in enumerate(value) for row in _flatten(item, f"{prefix}{index}.")
        ]
    return [(prefix.rstrip("."), value)]
