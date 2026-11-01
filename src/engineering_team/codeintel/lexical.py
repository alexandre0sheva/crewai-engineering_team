"""The words of a request against the words of the code: which files are about what it says.

A request's prose is cut into terms (code blocks, shell prompts and stack-trace lines left out,
words that start alike counted once). A file matches a term when it uses a word the term starts
or is a start of (``rabobank`` finds ``rabo``); rare terms and words used again and again count
more, and words in a file path or a definition's name count most. Comments and docstrings are
left out of the files: prose in them is how common words end up "matching" unrelated code.
"""

from __future__ import annotations

import ast
import math
import re
from collections import Counter, defaultdict
from collections.abc import Sequence

from engineering_team.codeintel.index import SourceFile, SourceIndex

MAX_TERMS = 40
STEM = 5
NAME_WEIGHT = 3  # a word in a file path or a definition's name counts as this many uses
TERM_FILES = 12  # only this many files, the best matches, are seeded by the request's words
MAX_TERM_SHARE = 0.08  # a word that is in more of the files than this says nothing...
MAX_TERM_FILES = 12  # ...nor does one in more than this many
MIN_TERM_LENGTH = 4
WORD = re.compile(r"[a-z][a-z0-9]{3,}")
SPLIT_CASE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
FENCE = re.compile(r"^\s*(```|~~~).*?^\s*\1[^\n]*$", re.MULTILINE | re.DOTALL)
CODE_LINE = re.compile(r'^(?:[ \t]{2,}\S|\$ |Traceback|.*\bFile ").*$', re.MULTILINE)
STOP_WORDS = frozenset(
    {
        "about", "after", "again", "also", "because", "been", "before", "being", "could", "does",
        "done", "each", "error", "file", "files", "from", "have", "into", "just", "like", "make",
        "makes", "more", "most", "must", "need", "needs", "only", "other", "please", "should",
        "since", "some", "still", "than", "that", "their", "them", "then", "there", "these",
        "they", "this", "those", "through", "traceback", "until", "using", "very", "want", "what",
        "when", "where", "which", "while", "will", "with", "without", "would", "your",
    }
)  # fmt: skip

COMMENTS = {
    "python": re.compile(r"#.*$", re.MULTILINE),
    "ruby": re.compile(r"#.*$", re.MULTILINE),
    "php": re.compile(r"(?://|#).*$|/\*.*?\*/", re.MULTILINE | re.DOTALL),
}
C_COMMENTS = re.compile(r"(?<!:)//.*$|/\*.*?\*/", re.MULTILINE | re.DOTALL)


def code_words(source: SourceFile) -> str:
    """A file's text without comments and docstrings: prose in them is how a request's common
    words end up "matching" files that have nothing to do with it."""

    text = source.text
    if source.language == "python":
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError):
            return COMMENTS["python"].sub("", text)
        lines = text.splitlines()
        for node in ast.walk(tree):
            if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                first = node.body[0] if node.body else None
                if (
                    isinstance(first, ast.Expr)
                    and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)
                ):
                    for number in range(first.lineno, (first.end_lineno or first.lineno) + 1):
                        lines[number - 1] = ""
        return COMMENTS["python"].sub("", "\n".join(lines))
    return (COMMENTS.get(source.language) or C_COMMENTS).sub("", text)


def tokens(text: str) -> list[str]:
    return WORD.findall(SPLIT_CASE.sub(" ", text).lower())


def prose(text: str) -> str:
    """``text`` without fenced blocks, shell prompts and stack-trace lines: what a person wrote."""

    return CODE_LINE.sub("", FENCE.sub("", text))


def request_terms(text: str, files: int) -> tuple[str, ...]:
    """The words of the request's prose worth looking for in code, the most repeated first."""

    seen: Counter[str] = Counter()
    for word in tokens(prose(text)):
        if word not in STOP_WORDS and len(word) >= MIN_TERM_LENGTH:
            seen[word] += 1
    # Repeated words first (the request dwells on them), then the order they came in; words that
    # share their first five letters (report, reports, reporting) are one term.
    order = {word: number for number, word in enumerate(seen)}
    ranked = sorted(seen, key=lambda word: (-seen[word], order[word]))
    stems: dict[str, str] = {}
    for word in ranked:
        stems.setdefault(word[:STEM], word)
    return tuple(stems.values())[:MAX_TERMS] if files else ()


def lexical(index: SourceIndex, terms: Sequence[str]) -> tuple[dict[str, float], dict[str, str]]:
    """Per file, how much the request's words matter in it, and the heaviest word it matched.
    A rare word counts more (it is in few files), one used again and again in a file counts more
    there than a passing mention. A word matches a file word it starts or is a start of
    (``rabobank`` finds ``rabo``, ``imports`` finds ``import``)."""

    if not terms:
        return {}, {}
    vocabulary: dict[str, Counter[str]] = defaultdict(Counter)
    for path, source in index.files.items():
        for word in tokens(code_words(source)):
            vocabulary[word][path] += 1
        names = path.replace("/", " ") + " " + " ".join(d.name for d in index.definitions(path))
        for word in tokens(names.replace("_", " ")):
            vocabulary[word][path] += NAME_WEIGHT  # a word in a name says more than in a body
    total = len(index.files)
    scores: dict[str, float] = {}
    best: dict[str, tuple[float, str]] = {}
    for term in terms:
        uses: Counter[str] = Counter()
        for word, counts in vocabulary.items():
            if word.startswith(term) or term.startswith(word):
                uses.update(counts)
        if not uses or len(uses) > max(3, min(MAX_TERM_FILES, MAX_TERM_SHARE * total)):
            continue
        rarity = math.log(1 + total / len(uses))
        for path, count in uses.items():
            weight = rarity * (1 + math.log(count))
            scores[path] = scores.get(path, 0.0) + weight
            if weight > best.get(path, (0.0, ""))[0]:
                best[path] = (weight, term)
    top = max(scores.values(), default=0.0)
    if top <= 0:
        return {}, {}
    chosen = sorted(scores, key=lambda path: (-scores[path], path))[:TERM_FILES]
    return (
        {path: (scores[path] / top) ** 2 for path in chosen},  # the best matches stand out
        {path: best[path][1] for path in chosen},
    )
