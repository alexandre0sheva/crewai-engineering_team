"""A repository profile as plain text: for the terminal and for the analysts' prompts."""

from __future__ import annotations

from engineering_team.modes.repo_profile import CommandKind, RepoProfile

KIND_ORDER: tuple[CommandKind, ...] = (
    "setup",
    "test",
    "lint",
    "typecheck",
    "format",
    "build",
    "run",
)
MAX_COMMANDS_PER_KIND = 3


def render_profile(profile: RepoProfile) -> str:
    """Counts first, then details; every line is a fact the controller found."""

    git = profile.git
    if git.is_repo:
        state = "dirty" if git.dirty else "clean"
        detail = f"{git.changed_files} changed, {git.untracked_files} untracked"
        where = "linked worktree, " if git.linked_worktree else ""
        repo = f"Git repository ({where}branch {git.branch}, {git.head[:10] or 'no commits'}, "
        repo += f"{state}{f': {detail}' if git.dirty else ''})"
    elif not git.available:
        repo = "git is not installed"
    else:
        repo = "not a Git repository"
    approx = "at least " if profile.truncated else ""
    lines = [
        f"Project: {profile.name}",
        f"Path: {profile.root}",
        f"Size: {approx}{profile.files:,} files, {profile.lines:,} lines of code",
        f"Git: {repo}",
    ]
    if profile.languages:
        lines.append(
            "Languages: "
            + ", ".join(
                f"{s.language} {s.lines:,} lines/{s.files} files" for s in profile.languages[:8]
            )
        )
    for stack in profile.stacks:
        tools = ", ".join(
            f"{label} {value}"
            for label, value in (
                ("test", stack.test),
                ("lint", stack.lint),
                ("typecheck", stack.typecheck),
                ("format", stack.format),
            )
            if value
        )
        lines.append(
            f"Stack {stack.directory}: {stack.language} ({stack.manager})"
            + (f", {tools}" if tools else "")
        )
    if profile.commands:
        lines.append("Commands (detected, not run):")
        for kind in KIND_ORDER:
            for command in profile.commands_of(kind)[:MAX_COMMANDS_PER_KIND]:
                where = "" if command.directory == "." else f" [in {command.directory}]"
                lines.append(f"  {kind:<9} {command.command}{where}  ({command.source})")
    if profile.entrypoints:
        lines.append("Entry points:")
        lines += [f"  {e.path}  ({e.kind}: {e.evidence})" for e in profile.entrypoints]
    if profile.test_dirs or profile.test_files:
        lines.append(
            f"Tests: {profile.test_files} test file(s) in {', '.join(profile.test_dirs) or '-'}"
        )
    if profile.ci:
        lines.append(f"CI: {', '.join(profile.ci)}")
    if profile.conventions:
        lines.append(
            "Conventions: "
            + ", ".join(
                f"{c.path}{f' {c.detail}' if c.detail else ''}" for c in profile.conventions
            )
        )
    if profile.manifests:
        lines.append(f"Manifests: {', '.join(profile.manifests)}")
    lines += [f"Note: {note}" for note in profile.notes]
    return "\n".join(lines)
