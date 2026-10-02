"""Which source files the code intelligence tools understand, and what language each is."""

from __future__ import annotations

from pathlib import PurePosixPath

# One name per syntax family: TypeScript is read like JavaScript, Kotlin like Java.
SUFFIX_LANGUAGES = {
    ".py": "python",
    **dict.fromkeys((".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"), "js"),
    ".go": "go",
    ".java": "java",
    ".kt": "kotlin",
    ".cs": "csharp",
    ".rs": "rust",
    ".rb": "ruby",
    ".php": "php",
}

LANGUAGE_NAMES = {
    "python": "Python",
    "js": "JavaScript/TypeScript",
    "go": "Go",
    "java": "Java",
    "kotlin": "Kotlin",
    "csharp": "C#",
    "rust": "Rust",
    "ruby": "Ruby",
    "php": "PHP",
}
SUPPORTED_LANGUAGES = ", ".join(LANGUAGE_NAMES.values())


def language_of(path: str) -> str | None:
    """The language of a source path, or ``None`` when the tools do not read it."""

    return SUFFIX_LANGUAGES.get(PurePosixPath(path).suffix.lower())
