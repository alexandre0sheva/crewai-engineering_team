"""Repositories a task starts from, fetched at run time into a cache (standard library only).

A task that names a ``repository`` (an imported SWE-bench instance) is a repository at one commit.
The first run that needs it fetches that one commit (``git fetch --depth 1``, else the whole
history) into ``<cache>/<owner>__<name>/<commit>/``; later runs, and the hidden checks of a local
run, read the cached checkout. The team never sees the upstream history: its repository is a copy
of the tree with one commit (``prepare.make_repository``), so a later fix cannot be looked up.

Also patches: :func:`patched_files` is how the offline team turns a reference patch into files.
"""

from __future__ import annotations

import os
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

CACHE_ENV = "BENCH_REPO_CACHE"  # where a local check finds the cached checkout
DIFF_HEADER = re.compile(r"^diff --git a/(\S+) b/(\S+)$", re.MULTILINE)
_LOCKS: dict[Path, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


class RepositoryError(RuntimeError):
    """A repository that cannot be fetched or a patch that does not apply; the message says why."""


def checkout_dir(cache: Path, url: str, commit: str) -> Path:
    """Where the checkout of ``url`` at ``commit`` is cached."""

    parts = [p for p in re.split(r"[/:]", url.removesuffix("/").removesuffix(".git")) if p]
    slug = "__".join(parts[-2:]) or "repository"
    return cache / re.sub(r"[^A-Za-z0-9_.-]", "-", slug) / commit


def _git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    return subprocess.run(
        ["git", "-c", "advice.detachedHead=false", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def _lock(path: Path) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(path, threading.Lock())


def checkout(url: str, commit: str, cache: Path) -> Path:
    """The cached checkout of ``url`` at ``commit``, fetched first if it is not there yet.

    Parallel runs in one batch fetch once (a lock per checkout); a fetch is written to a scratch
    directory and renamed into place, so an interrupted one never leaves a half checkout behind.
    """

    target = checkout_dir(cache, url, commit)
    with _lock(target):
        if (target / ".git").is_dir():
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        scratch = target.with_name(f"{target.name}.partial-{secrets.token_hex(3)}")
        try:
            _fetch(url, commit, scratch)
            try:
                scratch.rename(target)
            except OSError:  # another process got there first
                if not (target / ".git").is_dir():
                    raise
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
    return target


def _fetch(url: str, commit: str, into: Path) -> None:
    into.mkdir(parents=True)
    _git("init", "-q", str(into))
    shallow = _git("fetch", "-q", "--depth", "1", url, commit, cwd=into)
    if shallow.returncode != 0:  # a server that will not serve one commit by its SHA
        full = _git("fetch", "-q", url, cwd=into)
        if full.returncode != 0:
            raise RepositoryError(
                f"cannot fetch {url} at {commit}: {(full.stderr or shallow.stderr).strip()[-300:]}"
            )
    done = _git("checkout", "-q", "--detach", commit, cwd=into)
    if done.returncode != 0:
        raise RepositoryError(f"{url} has no commit {commit}: {done.stderr.strip()[-300:]}")


def patch_paths(patch: str) -> list[str]:
    """The files a unified Git diff touches (the new path of a rename), in order."""

    return list(dict.fromkeys(new for _, new in DIFF_HEADER.findall(patch)))


def patched_files(patch: str, base: Path) -> dict[str, str]:
    """The full new text of every file ``patch`` touches, applied over ``base`` (left as it is).

    Only the touched files are copied to a scratch directory and patched there. A patch that
    deletes a file cannot be replayed as written files, so it is refused.
    """

    with tempfile.TemporaryDirectory(prefix="bench-patch-") as scratch:
        root = Path(scratch) / "tree"
        root.mkdir()
        for name in patch_paths(patch) + [old for old, _ in DIFF_HEADER.findall(patch)]:
            source = base / name
            if source.is_file():
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, root / name)
        change = Path(scratch) / "change.patch"
        change.write_text(patch, encoding="utf-8")
        applied = _git("apply", "--whitespace=nowarn", str(change), cwd=root)
        if applied.returncode != 0:
            raise RepositoryError(f"the patch does not apply: {applied.stderr.strip()[-300:]}")
        files: dict[str, str] = {}
        for name in patch_paths(patch):
            if not (root / name).is_file():
                raise RepositoryError(f"the patch deletes {name}: it cannot be replayed")
            files[name] = (root / name).read_text(encoding="utf-8")
    return files
