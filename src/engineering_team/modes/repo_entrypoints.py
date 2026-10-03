"""Entry points of an existing project: what a manifest declares, then what the files suggest."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path, PurePosixPath
from typing import Any

from engineering_team.devtools.detect import Stack
from engineering_team.modes.repo_profile import Entrypoint, EntrypointKind

SNIFF_BYTES = 4_000
MAX_LISTED = 20

SERVER_MARK = re.compile(r"FastAPI\(|Flask\(|uvicorn|http\.server|express\(|createServer|aiohttp")
CLI_MARK = re.compile(r"argparse|click|typer|sys\.argv|commander|yargs")
MAIN_MARK = re.compile(r"__name__\s*==\s*[\"']__main__[\"']")
PYTHON_ENTRY_NAMES = frozenset({"main.py", "app.py", "cli.py", "server.py", "run.py", "manage.py"})
FIXED_ENTRYPOINTS: dict[str, tuple[EntrypointKind, str]] = {
    "manage.py": ("cli", "Django management script"),
    "wsgi.py": ("server", "WSGI application"),
    "asgi.py": ("server", "ASGI application"),
    "__main__.py": ("main", "python -m entry point"),
    "main.go": ("main", "Go main package"),
    "Program.cs": ("main", ".NET program"),
    "config.ru": ("server", "Rack application"),
    "artisan": ("cli", "Laravel console"),
    "index.html": ("web", "static page"),
}


def find_entrypoints(root: Path, names: list[str], stacks: list[Stack]) -> list[Entrypoint]:
    found: dict[str, Entrypoint] = {}

    def add(path: str, kind: EntrypointKind, evidence: str) -> None:
        found.setdefault(path, Entrypoint(path=path, kind=kind, evidence=evidence))

    # What a manifest declares is the most authoritative evidence, so it is listed first.
    for stack in stacks:
        base = root if stack.directory == "." else root / stack.directory
        for entry in _declared_entrypoints(base, stack.directory):
            add(entry.path, entry.kind, entry.evidence)
    for name in names:
        path = PurePosixPath(name)
        depth = len(path.parts)
        if depth > 4:
            continue
        if path.name in FIXED_ENTRYPOINTS:
            kind, evidence = FIXED_ENTRYPOINTS[path.name]
            if path.name == "index.html" and depth > 2:
                continue
            add(name, kind, evidence)
        elif path.name in PYTHON_ENTRY_NAMES:
            sniffed, why = _sniff_python(root / name)
            if sniffed is not None:
                add(name, sniffed, why)
        elif name == "src/main.rs" or (
            path.parent.as_posix() == "src/bin" and path.suffix == ".rs"
        ):
            add(name, "main", "Rust binary")
        elif path.parent.name == "bin" and depth == 2 and path.suffix in ("", ".rb", ".sh"):
            add(name, "cli", "bin/ script")
        elif path.parts[:1] == ("cmd",) and path.name == "main.go":
            add(name, "main", "Go command")
        elif path.suffix == ".java" and path.stem in ("Main", "Application", "App"):
            add(name, "main", "Java entry class")
        elif name in ("index.php", "public/index.php"):
            add(name, "web", "PHP front controller")
    return sorted(found.values(), key=lambda e: e.path)[:MAX_LISTED]


def _sniff_python(path: Path) -> tuple[EntrypointKind | None, str]:
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            text = handle.read(SNIFF_BYTES)
    except OSError:
        return None, ""
    if SERVER_MARK.search(text):
        return "server", "starts a web server"
    if CLI_MARK.search(text):
        return "cli", "reads command-line arguments"
    if MAIN_MARK.search(text):
        return "script", "has a __main__ guard"
    return None, ""


def _declared_entrypoints(base: Path, directory: str) -> list[Entrypoint]:
    """Entry points a manifest declares: package.json ``bin``/``main``, pyproject scripts."""

    def rel(path: str) -> str:
        cleaned = path.removeprefix("./")
        return cleaned if directory == "." else f"{directory}/{cleaned}"

    found: list[Entrypoint] = []
    package = _load_json(base / "package.json")
    bins = package.get("bin")
    if isinstance(bins, str):
        found.append(Entrypoint(path=rel(bins), kind="cli", evidence="package.json bin"))
    elif isinstance(bins, dict):
        found += [
            Entrypoint(path=rel(str(target)), kind="cli", evidence=f"package.json bin '{name}'")
            for name, target in bins.items()
        ]
    main = package.get("main")
    if isinstance(main, str):
        found.append(Entrypoint(path=rel(main), kind="main", evidence="package.json main"))
    scripts = _load_toml(base / "pyproject.toml").get("project", {}).get("scripts", {})
    if isinstance(scripts, dict):
        for name, target in scripts.items():
            module = str(target).split(":")[0]
            found.append(
                Entrypoint(
                    path=_module_path(base, directory, module),
                    kind="cli",
                    evidence=f"console script '{name}' -> {target}",
                )
            )
    return found


def _module_path(base: Path, directory: str, module: str) -> str:
    stem = module.replace(".", "/")
    for candidate in (
        f"src/{stem}.py",
        f"{stem}.py",
        f"src/{stem}/__init__.py",
        f"{stem}/__init__.py",
    ):
        if (base / candidate).is_file():
            return candidate if directory == "." else f"{directory}/{candidate}"
    return module


def _load_json(path: Path) -> dict[str, object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _load_toml(path: Path) -> dict[str, Any]:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}
