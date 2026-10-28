"""Text clean-up for bank statement fields."""

from __future__ import annotations

import re
import unicodedata

_SPACES = re.compile(r"\s+")
_REFERENCE = re.compile(r"\b(?:ref|kenmerk|reference)[:\s]+\S+", re.IGNORECASE)


def squash(text: str) -> str:
    """Collapse runs of white space (banks pad fields) and strip the ends."""

    return _SPACES.sub(" ", text.replace(" ", " ")).strip()


def fold(text: str) -> str:
    """Lower case without accents, for matching: ``"Café Noël"`` -> ``"cafe noel"``."""

    decomposed = unicodedata.normalize("NFKD", squash(text))
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()


def strip_reference(text: str) -> str:
    """Drop payment references (``Ref: 12345``) that make otherwise equal descriptions differ."""

    return squash(_REFERENCE.sub("", text))


def shorten(text: str, width: int) -> str:
    """``text`` cut to ``width`` characters with an ellipsis when it is longer."""

    if width < 2:
        raise ValueError("width must be at least 2")
    text = squash(text)
    return text if len(text) <= width else text[: width - 1] + "…"


def slug(text: str) -> str:
    """``"Eating out!"`` -> ``"eating-out"``."""

    return re.sub(r"[^a-z0-9]+", "-", fold(text)).strip("-")
