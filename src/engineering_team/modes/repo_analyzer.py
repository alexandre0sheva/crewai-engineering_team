"""The deterministic repository analysis: ``analyze_repo(root) -> RepoProfile``.

No model is involved and nothing is run or written: the analyzer walks the files (honouring the
root ``.gitignore``, skipping heavy directories) and reads manifests and a few lines of a few
files, then asks Git (read-only, through :class:`~engineering_team.git.port.GitPort`) for the
branch and whether the tree is dirty. The result is a :class:`RepoProfile`.

Stack and tool detection is ``devtools.detect`` (T11), the same one the verifier uses; the
commands it implies are in ``modes/repo_commands.py``.
"""

from __future__ import annotations

import contextlib
import re
import tempfile
import tomllib
from collections import Counter
from collections.abc import Iterator
from pathlib import Path, PurePosixPath

from engineering_team.devtools.detect import Stack, find_stacks
from engineering_team.execution.local import LocalBackend
from engineering_team.git.port import GitError, GitPort
from engineering_team.modes.repo_commands import detect_commands
from engineering_team.modes.repo_entrypoints import find_entrypoints
from engineering_team.modes.repo_profile import (
    ConventionFile,
    ConventionKind,
    GitState,
    LanguageStat,
    RepoProfile,
    StackInfo,
)
from engineering_team.settings import GitSettings
from engineering_team.tools.ignore import BINARY_SNIFF_BYTES, IgnoreRules, iter_files
from engineering_team.tools.workspace import CONTROLLER_DIRECTORY, ProjectWorkspace
from engineering_team.verification.profiles import project_roots

MAX_FILES = 20_000
MAX_LINE_COUNT_BYTES = 1_000_000
SNIFF_BYTES = 4_000
MAX_LISTED = 20

LANGUAGES = {
    ".py": "Python", ".js": "JavaScript", ".jsx": "JavaScript", ".mjs": "JavaScript",
    ".cjs": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript", ".go": "Go",
    ".java": "Java", ".kt": "Kotlin", ".scala": "Scala", ".cs": "C#", ".rs": "Rust",
    ".rb": "Ruby", ".php": "PHP", ".c": "C", ".h": "C", ".cc": "C++", ".cpp": "C++",
    ".hpp": "C++", ".swift": "Swift", ".sh": "Shell", ".sql": "SQL", ".html": "HTML",
    ".css": "CSS", ".scss": "CSS", ".vue": "Vue", ".svelte": "Svelte",
}  # fmt: skip

MANIFEST_NAMES = frozenset(
    {
        "pyproject.toml", "setup.py", "setup.cfg", "Pipfile", "package.json", "go.mod",
        "Cargo.toml", "pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle",
        "Gemfile", "composer.json", "Makefile", "justfile", "tox.ini", "noxfile.py",
        "Dockerfile", "docker-compose.yml", "docker-compose.yaml",
    }
)  # fmt: skip
MANIFEST_DEPTH = 3
TEST_DIR_NAMES = frozenset({"test", "tests", "__tests__", "spec", "specs", "e2e"})
TEST_FILE = re.compile(
    r"(?:^test_.+\.py|.+_test\.py|.+\.(?:test|spec)\.[cm]?[jt]sx?|.+_test\.go|.+Tests?\.(?:java|kt|cs|php)"
    r"|.+_spec\.rb|.+_test\.rs)$"
)
CI_FILES = (
    ".gitlab-ci.yml", ".travis.yml", "Jenkinsfile", "azure-pipelines.yml",
    "bitbucket-pipelines.yml", ".drone.yml", ".circleci/config.yml", ".buildkite/pipeline.yml",
)  # fmt: skip
CI_PREFIX = ".github/workflows/"

# File name (or prefix) -> the kind of convention it records.
CONVENTION_FILES: tuple[tuple[str, ConventionKind], ...] = (
    ("README", "docs"), ("CONTRIBUTING", "docs"), ("ARCHITECTURE", "docs"),
    ("AGENTS.md", "assistant"), ("CLAUDE.md", "assistant"), ("GEMINI.md", "assistant"),
    (".cursorrules", "assistant"), (".editorconfig", "editor"),
    (".eslintrc", "lint"), ("eslint.config.", "lint"), ("ruff.toml", "lint"),
    (".ruff.toml", "lint"),
    (".flake8", "lint"), (".pylintrc", "lint"), (".rubocop.yml", "lint"), (".golangci.", "lint"),
    ("phpcs.xml", "lint"), (".prettierrc", "format"), ("prettier.config.", "format"),
    ("rustfmt.toml", "format"), (".rustfmt.toml", "format"), (".clang-format", "format"),
    (".isort.cfg", "format"), ("mypy.ini", "typecheck"), (".mypy.ini", "typecheck"),
    ("pyrightconfig.json", "typecheck"), ("tsconfig.json", "typecheck"),
    (".pre-commit-config.yaml", "hooks"),
)  # fmt: skip
DOCUMENT_NAMES = frozenset({"README", "CONTRIBUTING", "ARCHITECTURE"})
PYPROJECT_TOOLS: dict[str, ConventionKind] = {
    "ruff": "lint", "flake8": "lint", "pylint": "lint", "black": "format", "isort": "format",
    "mypy": "typecheck", "pyright": "typecheck",
}  # fmt: skip


@contextlib.contextmanager
def standalone_git(root: Path) -> Iterator[GitPort]:
    """A :class:`GitPort` for reading ``root`` with no run around it: its command logs, home, and
    caches go to a throw-away directory, so nothing is written into ``root``. Use it for status
    and history only (never for diffs, which keep a temporary index in the project)."""

    with tempfile.TemporaryDirectory(prefix="engineering-team-git-") as scratch:
        base = Path(scratch)
        yield GitPort(
            ProjectWorkspace.create(root),
            LocalBackend(base / "logs"),
            GitSettings(),
            state_dir=base / "state",
        )


def analyze_repo(root: str | Path, git: GitPort | None = None) -> RepoProfile:
    """Profile the project at ``root``. ``git`` (a run's port) is used for the Git state; without
    one a standalone port is made. Raises ``ValueError`` if ``root`` is not a directory."""

    directory = Path(root).expanduser().resolve()
    if not directory.is_dir():
        raise ValueError(f"{directory} is not a directory.")
    workspace = ProjectWorkspace.create(directory)
    files, stats, truncated = _walk(workspace)
    stacks = find_stacks(directory)
    roots = project_roots(stacks)
    commands, notes = detect_commands(directory, roots)
    profile = RepoProfile(
        name=directory.name,
        root=str(directory),
        files=len(files),
        lines=sum(s.lines for s in stats),
        bytes=sum(size for _, size in files),
        truncated=truncated,
        languages=sorted(stats, key=lambda s: (-s.lines, -s.files, s.language)),
        stacks=[_stack_info(stack) for stack in roots],
        manifests=_manifests([name for name, _ in files]),
        commands=commands,
        entrypoints=find_entrypoints(directory, [name for name, _ in files], roots),
        ci=_ci([name for name, _ in files]),
        conventions=_conventions(directory, [name for name, _ in files]),
        notes=notes,
    )
    profile.test_dirs, profile.test_files = _tests([name for name, _ in files])
    profile.git = _git_state(directory, git)
    profile.notes.extend(_observations(profile))
    return profile


# -- the walk -------------------------------------------------------------------------------


def _walk(workspace: ProjectWorkspace) -> tuple[list[tuple[str, int]], list[LanguageStat], bool]:
    """``([(relative path, size)], per-language stats, truncated)`` for the non-ignored files."""

    files: list[tuple[str, int]] = []
    counts: dict[str, LanguageStat] = {}
    truncated = False
    for path in iter_files(workspace, rules=IgnoreRules.for_workspace(workspace)):
        if len(files) >= MAX_FILES:
            truncated = True
            break
        try:
            size = path.stat().st_size
        except OSError:
            continue
        name = workspace.relative_name(path)
        files.append((name, size))
        language = LANGUAGES.get(path.suffix.lower())
        if language is None:
            continue
        stat = counts.setdefault(language, LanguageStat(language=language))
        stat.files += 1
        stat.bytes += size
        stat.lines += _lines(path, size)
    return files, list(counts.values()), truncated


def _lines(path: Path, size: int) -> int:
    if size == 0 or size > MAX_LINE_COUNT_BYTES:
        return 0
    try:
        data = path.read_bytes()
    except OSError:
        return 0
    if b"\x00" in data[:BINARY_SNIFF_BYTES]:
        return 0
    return data.count(b"\n") + (0 if data.endswith(b"\n") else 1)


# -- what is in it --------------------------------------------------------------------------


def _stack_info(stack: Stack) -> StackInfo:
    return StackInfo(
        directory=stack.directory,
        language=stack.language,
        manager=stack.manager,
        test=stack.test,
        lint=stack.lint,
        typecheck=stack.typecheck,
        format=stack.format,
        build=list(stack.build or ()),
        notes=list(stack.notes),
    )


def _manifests(names: list[str]) -> list[str]:
    found = []
    for name in names:
        path = PurePosixPath(name)
        if len(path.parts) > MANIFEST_DEPTH:
            continue
        requirements = path.name.startswith("requirements") and path.suffix == ".txt"
        if path.name in MANIFEST_NAMES or path.suffix in (".csproj", ".sln") or requirements:
            found.append(name)
    return found


def _ci(names: list[str]) -> list[str]:
    return [
        name
        for name in names
        if name in CI_FILES
        or (name.startswith(CI_PREFIX) and name.endswith((".yml", ".yaml")))
        or PurePosixPath(name).name == "Jenkinsfile"
    ]


def _tests(names: list[str]) -> tuple[list[str], int]:
    """``(directories that hold tests, number of test files)``."""

    dirs: Counter[str] = Counter()
    count = 0
    for name in names:
        path = PurePosixPath(name)
        in_test_dir = any(part in TEST_DIR_NAMES for part in path.parts[:-1]) or (
            path.parts[:3][-3:-1] == ("src", "test")
        )
        if TEST_FILE.match(path.name):
            count += 1
            dirs[_test_dir_of(path)] += 1
        elif in_test_dir and path.suffix in LANGUAGES:
            dirs[_test_dir_of(path)] += 1
    return sorted(dirs)[:MAX_LISTED], count


def _test_dir_of(path: PurePosixPath) -> str:
    """The outermost test-named directory above ``path``, else the file's own directory."""

    parts = path.parts[:-1]
    for index, part in enumerate(parts):
        if part in TEST_DIR_NAMES:
            return "/".join(parts[: index + 1])
    return "/".join(parts) or "."


def _convention_kind(name: str) -> ConventionKind | None:
    upper = name.upper()
    for prefix, kind in CONVENTION_FILES:
        if prefix in DOCUMENT_NAMES:
            matched = upper == prefix or upper.startswith(prefix + ".")
        elif prefix.endswith("."):
            matched = name.startswith(prefix)
        else:
            matched = name == prefix or name.startswith(prefix + ".")
        if matched:
            return kind
    return None


def _conventions(root: Path, names: list[str]) -> list[ConventionFile]:
    found: list[ConventionFile] = []
    for name in names:
        path = PurePosixPath(name)
        if len(path.parts) > 2 or (
            len(path.parts) == 2 and path.parts[0] not in ("docs", ".github")
        ):
            continue
        if (kind := _convention_kind(path.name)) is not None:
            found.append(ConventionFile(path=name, kind=kind))
    found.extend(_pyproject_conventions(root, names))
    return sorted(found, key=lambda c: (c.kind, c.path, c.detail))


def _pyproject_conventions(root: Path, names: list[str]) -> list[ConventionFile]:
    if "pyproject.toml" not in names:
        return []
    try:
        data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return []
    tool = data.get("tool")
    if not isinstance(tool, dict):
        return []
    return [
        ConventionFile(path="pyproject.toml", kind=kind, detail=f"[tool.{table}]")
        for table, kind in PYPROJECT_TOOLS.items()
        if table in tool
    ]


# -- Git ------------------------------------------------------------------------------------


def _git_state(root: Path, git: GitPort | None) -> GitState:
    try:
        with contextlib.ExitStack() as stack:
            port = git or stack.enter_context(standalone_git(root))
            if not port.available():
                return GitState(available=False)
            if not port.is_repo():
                return GitState(enclosing=_enclosing_repository(root))
            changed, untracked = status_counts(port.status())
            return GitState(
                is_repo=True,
                branch=port.current_branch(),
                head=(port.head() or "")[:12],
                dirty=bool(changed or untracked),
                changed_files=changed,
                untracked_files=untracked,
                linked_worktree=(root / ".git").is_file(),
            )
    except (GitError, OSError):
        return GitState(enclosing=_enclosing_repository(root))


def status_counts(status: str) -> tuple[int, int]:
    """``(tracked changes, untracked files)`` from porcelain status, controller state left out."""

    changed = untracked = 0
    for line in status.splitlines():
        path = line[3:].strip().strip('"')
        if (
            not line.strip()
            or path == CONTROLLER_DIRECTORY
            or path.startswith(CONTROLLER_DIRECTORY + "/")
        ):
            continue
        if line.startswith("??"):
            untracked += 1
        else:
            changed += 1
    return changed, untracked


def enclosing_repository(root: Path) -> str | None:
    """The directory of a Git repository that contains ``root`` (but ``root`` is not its top
    level), found by looking for a ``.git`` entry above it."""

    return _enclosing_repository(root)


def _enclosing_repository(root: Path) -> str | None:
    if (root / ".git").exists():
        return None
    for parent in root.parents:
        if (parent / ".git").exists():
            return str(parent)
    return None


# -- what the profile suggests --------------------------------------------------------------


def _observations(profile: RepoProfile) -> list[str]:
    notes: list[str] = []
    if profile.truncated:
        notes.append(f"Only the first {MAX_FILES} files were counted; the totals are lower bounds.")
    if not profile.files:
        notes.append("The directory holds no files (after .gitignore and heavy directories).")
    if not profile.stacks:
        notes.append("No project manifest was found, so no stack or test command was detected.")
    elif not profile.commands_of("test"):
        notes.append("No test command could be detected.")
    if profile.stacks and not profile.test_files:
        notes.append("No test files were found.")
    if profile.git.enclosing:
        notes.append(
            f"This directory is inside the Git repository at {profile.git.enclosing}; it is "
            "treated as a plain directory. Pass the repository's top level to use Git."
        )
    if not profile.git.available:
        notes.append("git is not installed, so the Git state is unknown.")
    if len(profile.stacks) > 1:
        notes.append(f"{len(profile.stacks)} projects found; commands are listed per directory.")
    return notes
