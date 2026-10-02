"""Inspect Dependencies: the direct dependencies a project declares, from its manifests.

Offline and read-only: manifests (pyproject, requirements, package.json, go.mod, Cargo.toml,
pom.xml) give the declared versions; ``uv.lock``/``poetry.lock``/``Cargo.lock``/
``package-lock.json`` next to a manifest add the locked version. Whether a version is outdated or
deprecated needs a package registry and is not answered here.
"""

from __future__ import annotations

import json
import re
import tomllib
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from engineering_team.tools.ignore import IgnoreRules, iter_files, read_text_or_none
from engineering_team.tools.workspace import ProjectWorkspace

MAX_MANIFESTS = 30
MAX_MANIFEST_BYTES = 500_000
MAX_LOCKFILE_BYTES = 5_000_000
_REQUIREMENTS_NAME = re.compile(r"^requirements(?:[-_.][\w.-]+)?\.txt$")
_REQUIREMENT = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*([<>=!~@].*)?$")


@dataclass(frozen=True)
class Dependency:
    name: str
    declared: str  # the version constraint as written; "*" when there is none
    kind: str  # runtime, dev, peer, optional, build, test, indirect, optional: <extra>, group: <g>
    locked: str | None = None


@dataclass(frozen=True)
class Manifest:
    path: str
    ecosystem: str
    dependencies: list[Dependency]
    lockfile: str | None = None


def _toml(text: str) -> dict[str, object]:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return {}


def _table(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _requirement(spec: str, kind: str) -> Dependency | None:
    """One PEP 508 string such as ``"requests[socks]>=2.31 ; python_version>'3.9'"``."""

    match = _REQUIREMENT.match(spec.split(";", 1)[0].split(" #", 1)[0].strip())
    if match is None:
        return None
    return Dependency(match.group(1), (match.group(2) or "*").strip(), kind)


def _requirements(specs: object, kind: str) -> list[Dependency]:
    items = specs if isinstance(specs, list) else []
    found = (_requirement(item, kind) for item in items if isinstance(item, str))
    return [dep for dep in found if dep is not None]


def _poetry(table: dict[str, object], kind: str) -> list[Dependency]:
    found: list[Dependency] = []
    for name, value in table.items():
        if name.lower() != "python":
            declared = value if isinstance(value, str) else str(_table(value).get("version", "*"))
            found.append(Dependency(name, declared, kind))
    return found


def parse_pyproject(text: str) -> list[Dependency]:
    data = _toml(text)
    project = _table(data.get("project"))
    found = _requirements(project.get("dependencies"), "runtime")
    for extra, specs in _table(project.get("optional-dependencies")).items():
        found += _requirements(specs, f"optional: {extra}")
    for group, specs in _table(data.get("dependency-groups")).items():
        found += _requirements(specs, f"group: {group}")  # {include-group = ...} is skipped
    poetry = _table(_table(data.get("tool")).get("poetry"))
    found += _poetry(_table(poetry.get("dependencies")), "runtime")
    for group, body in _table(poetry.get("group")).items():
        found += _poetry(_table(_table(body).get("dependencies")), f"group: {group}")
    return found


def parse_requirements(text: str) -> list[Dependency]:
    found = []
    for raw in text.splitlines():
        line = raw.strip()
        if line and not line.startswith(("#", "-")) and (dep := _requirement(line, "runtime")):
            found.append(dep)
    return found


def parse_package_json(text: str) -> list[Dependency]:
    try:
        data = _table(json.loads(text))
    except json.JSONDecodeError:
        return []
    kinds = (
        ("dependencies", "runtime"),
        ("devDependencies", "dev"),
        ("peerDependencies", "peer"),
        ("optionalDependencies", "optional"),
    )
    found = []
    for key, kind in kinds:
        found += [Dependency(name, str(v), kind) for name, v in _table(data.get(key)).items()]
    return found


def parse_go_mod(text: str) -> list[Dependency]:
    found = []
    in_block = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("require ("):
            in_block = True
            continue
        if in_block and line == ")":
            in_block = False
            continue
        if not in_block and not line.startswith("require "):
            continue
        parts = line.removeprefix("require ").split("//")[0].split()
        if len(parts) == 2:
            kind = "indirect" if "// indirect" in line else "runtime"
            found.append(Dependency(parts[0], parts[1], kind))
    return found


def _cargo_version(value: object) -> str:
    if isinstance(value, str):
        return value
    body = _table(value)
    if "version" in body:
        return str(body["version"])
    for key in ("path", "git"):
        if key in body:
            return f"{key} {body[key]}"
    return "workspace" if body.get("workspace") else "*"


def parse_cargo_toml(text: str) -> list[Dependency]:
    data = _toml(text)
    tables = (
        ("dependencies", "runtime"),
        ("dev-dependencies", "dev"),
        ("build-dependencies", "build"),
    )
    return [
        Dependency(name, _cargo_version(value), kind)
        for key, kind in tables
        for name, value in _table(data.get(key)).items()
    ]


def parse_pom(text: str) -> list[Dependency]:
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return []

    def local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    def child(node: ET.Element, name: str) -> str:
        return next((c.text or "" for c in node if local(c.tag) == name), "").strip()

    found = []
    for block in (c for c in root if local(c.tag) == "dependencies"):
        for node in (d for d in block if local(d.tag) == "dependency"):
            name = f"{child(node, 'groupId')}:{child(node, 'artifactId')}"
            found.append(
                Dependency(name, child(node, "version") or "*", child(node, "scope") or "runtime")
            )
    return found


def _normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _locked_toml(text: str) -> dict[str, str]:
    packages = _toml(text).get("package")
    found: dict[str, str] = {}
    for package in packages if isinstance(packages, list) else []:
        if isinstance(package, dict) and "name" in package and "version" in package:
            found.setdefault(_normalise(str(package["name"])), str(package["version"]))
    return found


def _locked_package_lock(text: str) -> dict[str, str]:
    try:
        data = _table(json.loads(text))
    except json.JSONDecodeError:
        return {}
    found: dict[str, str] = {}
    for key, value in _table(data.get("packages")).items():
        if key.startswith("node_modules/") and key.count("node_modules/") == 1:
            found.setdefault(
                key.removeprefix("node_modules/"), str(_table(value).get("version", ""))
            )
    for key, value in _table(data.get("dependencies")).items():  # lockfile v1
        found.setdefault(key, str(_table(value).get("version", "")))
    return {name: version for name, version in found.items() if version}


Parser = Callable[[str], list[Dependency]]
LockReader = Callable[[str], dict[str, str]]
# manifest file name -> (ecosystem, parser, lock file names tried in order, lock reader)
_FILES: dict[str, tuple[str, Parser, tuple[str, ...], LockReader]] = {
    "pyproject.toml": ("Python", parse_pyproject, ("uv.lock", "poetry.lock"), _locked_toml),
    "package.json": ("npm", parse_package_json, ("package-lock.json",), _locked_package_lock),
    "go.mod": ("Go", parse_go_mod, (), _locked_toml),
    "Cargo.toml": ("Rust", parse_cargo_toml, ("Cargo.lock",), _locked_toml),
    "pom.xml": ("Maven", parse_pom, (), _locked_toml),
}


def _read_lock(
    directory: Path, names: tuple[str, ...], reader: LockReader
) -> tuple[str | None, dict[str, str]]:
    for name in names:
        text = read_text_or_none(directory / name, limit=MAX_LOCKFILE_BYTES)
        if text is not None:
            return name, reader(text)
    return None, {}


def inspect_dependencies(workspace: ProjectWorkspace, path: str = ".") -> list[Manifest]:
    """The manifests at or below ``path`` (at most 30), with locked versions where known."""

    base = workspace.resolve(path, must_exist=True)
    files = (
        [base]
        if base.is_file()
        else iter_files(workspace, base, rules=IgnoreRules.for_workspace(workspace))
    )
    manifests: list[Manifest] = []
    for file in files:
        is_requirements = bool(_REQUIREMENTS_NAME.match(file.name))
        if file.name not in _FILES and not is_requirements:
            continue
        if len(manifests) >= MAX_MANIFESTS:
            break
        text = read_text_or_none(file, limit=MAX_MANIFEST_BYTES)
        if text is None:
            continue
        relative = workspace.relative_name(file)
        if is_requirements:
            manifests.append(Manifest(relative, "Python", parse_requirements(text)))
            continue
        ecosystem, parser, lock_names, read_lock = _FILES[file.name]
        lockfile, locked = _read_lock(file.parent, lock_names, read_lock)
        normalise = _normalise if ecosystem == "Python" else str
        found = [
            Dependency(dep.name, dep.declared, dep.kind, locked.get(normalise(dep.name)))
            for dep in parser(text)
        ]
        manifests.append(Manifest(relative, ecosystem, found, lockfile))
    return manifests


def format_dependencies(manifests: list[Manifest], *, limit: int = 60) -> str:
    if not manifests:
        return (
            "No dependency manifests found (looked for pyproject.toml, requirements*.txt, "
            "package.json, go.mod, Cargo.toml, pom.xml). Pass a subdirectory path if the "
            "project is nested."
        )
    total = sum(len(manifest.dependencies) for manifest in manifests)
    lines = [f"{len(manifests)} manifest(s), {total} declared dependencies"]
    for manifest in manifests:
        lock = f", lock: {manifest.lockfile}" if manifest.lockfile else ""
        count = len(manifest.dependencies)
        lines.append(f"{manifest.path} ({manifest.ecosystem}{lock}): {count} dependencies")
        for dep in manifest.dependencies[:limit]:
            note = f"  ({dep.kind})" if dep.kind != "runtime" else ""
            locked = f"  [locked {dep.locked}]" if dep.locked else ""
            lines.append(f"  {dep.name} {dep.declared}{note}{locked}")
        if count > limit:
            lines.append(f"  ... {count - limit} more")
    lines.append("Outdated and deprecated status is not checked offline.")
    return "\n".join(lines)
