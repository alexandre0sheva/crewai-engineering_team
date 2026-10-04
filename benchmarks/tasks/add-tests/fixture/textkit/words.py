"""Small text helpers."""

import re
from collections import Counter

SMALL_WORDS = {"a", "an", "the", "of", "and", "or", "in", "on", "to"}


def word_count(text):
    """How many whitespace-separated words the text has."""
    return len(text.split())


def slugify(text):
    """Lower-case, with every run of other characters replaced by one dash, no edge dashes."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower())
    return slug.strip("-")


def truncate(text, limit, suffix="…"):
    """At most `limit` characters; a longer text is cut and ends with `suffix`."""
    if limit < len(suffix):
        raise ValueError("limit is shorter than the suffix")
    if len(text) <= limit:
        return text
    return text[: limit - len(suffix)].rstrip() + suffix


def title_case(text):
    """Capitalise each word except small words that are not first."""
    out = []
    for index, word in enumerate(text.split()):
        lower = word.lower()
        if index > 0 and lower in SMALL_WORDS:
            out.append(lower)
        else:
            out.append(lower[:1].upper() + lower[1:])
    return " ".join(out)


def is_palindrome(text):
    """Ignoring case and anything that is not a letter or digit."""
    letters = [char.lower() for char in text if char.isalnum()]
    return letters == letters[::-1]


def chunk(items, size):
    """Split a list into lists of `size` (the last may be shorter)."""
    if size < 1:
        raise ValueError("size must be at least 1")
    return [items[start : start + size] for start in range(0, len(items), size)]


def most_common(text, n=3):
    """The n most frequent words as (word, count); ties are alphabetical."""
    counts = Counter(re.findall(r"[a-z']+", text.lower()))
    ranked = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
    return ranked[:n]
