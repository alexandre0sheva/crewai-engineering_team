"""Deterministic signals about a request, the first half of triage (``intake/triage.py``).

Everything here is computed by code from the request text and the repository, never by a model,
so it costs nothing, says the same thing every time, and is what the controller trusts when it
checks the model's reading: whether there is a repository (and Git), a stack trace or failing
test names, a diff/branch/pull-request reference, the question form, paths mentioned that exist,
and keyword groups such as "upgrade" or "refactor". Repository size, languages and stacks come
from the profiler (``modes/repo_analyzer.py``).
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import Field

from engineering_team.contracts import Contract
from engineering_team.modes.repo_profile import RepoProfile
from engineering_team.pipeline.lean import RISK

MAX_PATHS = 20
MAX_TESTS = 10
MAX_LANGUAGES = 4
MAX_SUMMARY = 12
HIDDEN_ROOTS = (".git", ".engineering-team")

# Keyword groups, named after the intent they hint at. A hint is evidence for the model and
# for the signals-only fallback, never a decision on its own.
KEYWORDS: dict[str, re.Pattern[str]] = {
    name: re.compile(pattern, re.IGNORECASE)
    for name, pattern in {
        "bugfix": r"\b(bugs?|crash\w*|broken|regression|exceptions?|fix(es|ed|ing)?|"
        r"fails?|failing|failure|wrong|incorrect|(does ?n[o']t|doesn't|no longer) work)\b",
        "feature": r"\b(add|implement|introduce|support|allow|new (endpoint|command|option|flag|"
        r"feature|field|page|route|subcommand))\b",
        "new_project": r"\b(from scratch|scaffold|bootstrap|greenfield|"
        r"(build|create|make|write|start) (me )?(a|an) (new |small |simple |tiny )?"
        r"([\w-]+ ){0,2}(app|application|project|service|cli|tool|website|site|api|library|"
        r"game|bot|server|platform|system|dashboard)(?! (reference|docs|documentation)))\b",
        "refactor": r"\b(refactor\w*|restructur\w*|clean ?up|extract|renam\w*|deduplicat\w*|"
        r"simplif(y|ies|ication)|split (up )?(the |this )?(module|file|class|function))\b",
        "tests": r"\b(unit tests?|test coverage|coverage|(add|write|missing|more|increase) "
        r"([\w-]+ ){0,3}tests?)\b",
        "docs": r"\b(document(ation|ed)?|docs|readme|docstrings?|changelog|tutorial|"
        r"user guide|api reference)\b",
        "upgrade_deps": r"\b(upgrad\w*|bump|outdated|deprecat\w*|"
        r"(update|migrate) (the |our |all )?(dependenc\w*|packages?|librar\w*|"
        r"to (version|v)\s?\d))\b",
        "security_audit": r"\b(audit|security review|vulnerab\w*|cves?|owasp|"
        r"(sql|command) injection|xss|csrf|secrets? scan\w*)\b",
        "performance": r"\b(slow(er|ly)?|faster|speed ?up|performance|latency|throughput|"
        r"memory (usage|leak)|optimi[sz]\w*|profil(e|ing) (the|this|it))\b",
        "devops": r"\b(ci|ci/cd|github actions?|gitlab[- ]ci|docker(file)?|containers?|"
        r"kubernetes|k8s|helm|terraform|deploy\w*)\b",
        "review": r"\b(review|code review|look over|feedback on)\b",
        "research": r"\b(compare|comparison|alternatives?|pros and cons|which (library|"
        r"framework|database|tool)|evaluate|research|investigate options)\b",
        "plan_only": r"\b(plan only|just (a |the )?plan|design doc\w*|proposal|roadmap|"
        r"break (it |this )?down|(do ?n[o']t|without) (writ\w+|chang\w+|implement\w*) "
        r"(any |the )?code)\b",
    }.items()
}

_TRACE = re.compile(
    r"Traceback \(most recent call last\)"  # Python
    r"|^\s*File \"[^\"]+\", line \d+"
    r"|^\s+at .+[(\s][^()\s]+:\d+(:\d+)?\)?\s*$"  # Node, Java
    r"|^panic: |^goroutine \d+ \["  # Go
    r"|Exception in thread \""
    r"|^thread '.+' panicked at",  # Rust
    re.MULTILINE,
)
_NODE_ID = re.compile(r"(?<![\w/.-])((?:[\w.-]+/)*[\w.-]+\.py::[\w\[\]:.,-]+)")
_FAILED_LINE = re.compile(r"^(?:FAILED|FAIL|--- FAIL:)\s+([\w/.:\[\]-]+)", re.MULTILINE)
_TEST_NAME = re.compile(r"(?<![/:.])\b(test_\w+|Test[A-Z]\w*)\b(?!\.\w)")
_FAIL_WORD = re.compile(r"\b(fail\w*|broken|breaks|red|errors?)\b", re.IGNORECASE)

_DIFF_REFS: dict[str, re.Pattern[str]] = {
    "diff": re.compile(r"^diff --git |^@@ -\d+|^(---|\+\+\+) [ab]/|\bgit diff\b|\bthe diff\b",
                       re.MULTILINE | re.IGNORECASE),
    "pull_request": re.compile(
        r"\b(PR|MR)\s*[#!]?\d+|\bpull request\b|\bmerge request\b|/pull/\d+|\bthis PR\b",
        re.IGNORECASE,
    ),
    "branch": re.compile(
        r"\b(on|from|in|against|the) (the )?[`'\"]?[\w./-]+[`'\"]? branch\b|\bbranch [`'\"]?"
        r"[\w./-]*[/_-][\w./-]*|\b(compared? (to|with)|against) (origin/)?(main|master)\b",
        re.IGNORECASE,
    ),
    "commit": re.compile(r"\bcommit [0-9a-f]{7,40}\b|\b[0-9a-f]{7,40}\.\.[0-9a-f]{7,40}\b"),
}  # fmt: skip

_WH = re.compile(
    r"^(how|what|why|where|which|who|when|is there|are there|does|do|did|is|are|explain|"
    r"tell me (how|what|why|where|which))\b",
    re.IGNORECASE,
)
_POLITE = re.compile(r"^(can|could|would|will) you\b|^please\b", re.IGNORECASE)
_PATH = re.compile(r"(?<![\w@:/.-])((?:\.{0,2}/)?(?:[\w.-]+/)+[\w.-]*|[\w-][\w.-]*\.[A-Za-z]{1,6})")


class Signals(Contract):
    """What code can say about a request before any model is called."""

    repo: str | None = None  # the directory the request is about (cwd or ``--repo``)
    git_repo: bool = False
    existing_code: bool = False  # the directory has project files
    repo_files: int = 0
    repo_lines: int = 0
    languages: list[str] = Field(default_factory=list)
    stacks: list[str] = Field(default_factory=list)
    test_files: int = 0
    stack_trace: bool = False
    failing_tests: list[str] = Field(default_factory=list)
    refs: list[str] = Field(default_factory=list)  # diff, pull_request, branch, commit
    question: bool = False
    paths: list[str] = Field(default_factory=list)  # mentioned and present in the repository
    keywords: dict[str, list[str]] = Field(default_factory=dict)  # group -> words matched
    risk_terms: list[str] = Field(default_factory=list)  # security-sensitive words (lean.RISK)
    summary: list[str] = Field(default_factory=list)  # manifests, entry points, test dirs
    words: int = 0

    @property
    def diff_ref(self) -> bool:
        return bool(self.refs)


def collect(
    request: str, repo: str | Path | None = None, profile: RepoProfile | None = None
) -> Signals:
    """The signals of ``request`` about the directory ``repo`` (``None``: no directory).

    ``profile`` is the repository's profile when the caller already has one; otherwise it is
    computed. Raises ``ValueError`` when ``repo`` is not a directory.
    """

    signals = Signals(
        stack_trace=bool(_TRACE.search(request)),
        failing_tests=failing_tests(request),
        refs=[name for name, pattern in _DIFF_REFS.items() if pattern.search(request)],
        question=is_question(request),
        keywords=keyword_hits(request),
        risk_terms=list(dict.fromkeys(m.group(0).lower() for m in RISK.finditer(request))),
        words=len(request.split()),
    )
    if repo is None:
        return signals
    root = Path(repo).expanduser().resolve()
    if profile is None:
        from engineering_team.modes.repo_analyzer import analyze_repo

        profile = analyze_repo(root)
    signals.repo = str(root)
    signals.git_repo = profile.git.is_repo
    signals.existing_code = profile.files > 0
    signals.repo_files, signals.repo_lines = profile.files, profile.lines
    signals.languages = [s.language for s in profile.languages[:MAX_LANGUAGES]]
    signals.stacks = sorted({f"{s.language}/{s.manager}" for s in profile.stacks})
    signals.test_files = profile.test_files
    signals.paths = mentioned_paths(request, root)
    signals.summary = _summary(profile)
    return signals


def _summary(profile: RepoProfile) -> list[str]:
    lines = [f"manifest {name}" for name in profile.manifests]
    lines += [f"{e.kind} entry point {e.path}" for e in profile.entrypoints]
    lines += [f"tests in {name}/" for name in profile.test_dirs]
    lines += [f"{c.kind} command: {c.command}" for c in profile.commands if c.kind == "test"]
    return lines[:MAX_SUMMARY]


def keyword_hits(request: str) -> dict[str, list[str]]:
    """Keyword group -> the distinct words that matched it (lower case, in order)."""

    hits: dict[str, list[str]] = {}
    for name, pattern in KEYWORDS.items():
        found = [" ".join(m.group(0).lower().split()) for m in pattern.finditer(request)]
        if found:
            hits[name] = list(dict.fromkeys(found))
    return hits


def is_question(request: str) -> bool:
    """The request asks something rather than asking for something to be done: it starts with
    a question word, or ends with ``?`` without a polite command ("Can you add ...?")."""

    text = " ".join(request.split())
    if not text:
        return False
    if _WH.match(text):
        return True
    return text.endswith("?") and not _POLITE.match(text)


def failing_tests(request: str) -> list[str]:
    """Test names the request reports as failing: pytest node ids, ``FAILED``/``--- FAIL:``
    lines, and ``test_x``/``TestX`` names on a line that says fail, broken or error."""

    names = [m.group(1) for m in _NODE_ID.finditer(request)]
    names += [m.group(1) for m in _FAILED_LINE.finditer(request)]
    for line in request.splitlines():
        if _FAIL_WORD.search(line):
            names += [
                m.group(1)
                for m in _TEST_NAME.finditer(line)
                if not any(n.endswith(f"::{m.group(1)}") for n in names)
            ]
    return list(dict.fromkeys(n.rstrip(".,:;") for n in names))[:MAX_TESTS]


def mentioned_paths(request: str, root: Path) -> list[str]:
    """Paths named in the request that exist under ``root`` (POSIX, relative, sorted)."""

    found = {existing_path(m.group(1), root) for m in _PATH.finditer(request)}
    return sorted(path for path in found if path)[:MAX_PATHS]


def existing_path(candidate: str, root: Path) -> str | None:
    """``candidate`` (a path as written, maybe quoted or with ``:line``) relative to ``root``
    when it exists inside it and is not Git's or the controller's own storage."""

    cleaned = candidate.strip().strip("`'\"").rstrip(".,:;)")
    cleaned = re.sub(r":\d+(:\d+)?$", "", cleaned).removeprefix("./")
    if not cleaned or cleaned.startswith(("/", "..")) or "://" in cleaned:
        return None
    if cleaned.startswith(("a/", "b/")) and not (root / cleaned).exists():
        cleaned = cleaned[2:]  # a diff's a/ and b/ sides
    try:
        target = (root / cleaned).resolve()
    except (OSError, ValueError):
        return None
    if target == root or not target.is_relative_to(root) or not target.exists():
        return None
    relative = target.relative_to(root)
    return None if relative.parts[0] in HIDDEN_ROOTS else relative.as_posix()
