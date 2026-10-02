"""Package Info: the current version of a package from its official registry.

PyPI (JSON API), the npm registry, crates.io, and the Go module proxy. Only the facts an agent
needs to pick a current, supported version are reported: latest version, release date (where
the registry's small "latest" document has one), license, repository, and deprecation or yank
flags. Registry text (descriptions, deprecation messages) is untrusted data.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

from engineering_team.tools.support import ToolError
from engineering_team.webtools.safenet import WebFetcher

ECOSYSTEMS = ("pypi", "npm", "crates", "go")
ALIASES = {
    "python": "pypi",
    "pip": "pypi",
    "node": "npm",
    "nodejs": "npm",
    "javascript": "npm",
    "cargo": "crates",
    "crates.io": "crates",
    "rust": "crates",
    "golang": "go",
}
LABELS = {"pypi": "PyPI", "npm": "npm", "crates": "crates.io", "go": "Go"}
NAME_PATTERNS = {
    "pypi": re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,98}[A-Za-z0-9])?"),
    "npm": re.compile(r"(?:@[a-z0-9~][a-z0-9._~-]*/)?[a-z0-9~][a-z0-9._~-]*"),
    "crates": re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}"),
    "go": re.compile(r"[A-Za-z0-9][A-Za-z0-9._~/-]{0,199}"),
}
MAX_NOTE_CHARS = 200


@dataclass(frozen=True)
class PackageInfo:
    ecosystem: str
    name: str
    latest: str
    released: str | None
    license: str | None
    repository: str | None
    deprecated: str | None
    yanked: bool
    notes: list[str] = field(default_factory=list)


def normalise_ecosystem(value: str) -> str:
    key = value.strip().lower()
    key = ALIASES.get(key, key)
    if key not in ECOSYSTEMS:
        raise ToolError(f"Unknown ecosystem {value!r}. Use one of: {', '.join(ECOSYSTEMS)}.")
    return key


def _short(text: object) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    return value if len(value) <= MAX_NOTE_CHARS else value[: MAX_NOTE_CHARS - 3] + "..."


def _date(value: object) -> str | None:
    match = re.match(r"\d{4}-\d{2}-\d{2}", str(value or ""))
    return match.group(0) if match else None


def _repo_url(url: object) -> str | None:
    text = str(url or "").strip()
    if not text:
        return None
    text = re.sub(r"^git\+", "", text)
    text = re.sub(r"^(?:ssh://)?git@([^:/]+)[:/]", r"https://\1/", text)
    text = re.sub(r"^git://", "https://", text).removesuffix(".git")
    return text if text.startswith(("http://", "https://")) else None


# -- parsers (pure; tested on recorded responses) ----------------------------------------------


def parse_pypi(data: dict[str, Any]) -> PackageInfo:
    info = data.get("info") or {}
    classifiers = info.get("classifiers") or []
    license_text = str(info.get("license_expression") or "").strip()
    if not license_text:
        raw = str(info.get("license") or "").strip()
        license_text = raw if 0 < len(raw) <= 60 and "\n" not in raw else ""
    if not license_text:
        trove = [c.rsplit("::", 1)[-1].strip() for c in classifiers if c.startswith("License ::")]
        license_text = trove[-1] if trove else ""
    urls = info.get("project_urls") or {}
    repo = next(
        (
            _repo_url(url)
            for label, url in urls.items()
            if label.lower() in ("source", "source code", "repository", "code", "github")
        ),
        None,
    ) or next(
        (
            _repo_url(url)
            for url in [*urls.values(), info.get("home_page")]
            if "github.com" in str(url)
        ),
        None,
    )
    files = data.get("urls") or []
    yanked = bool(info.get("yanked"))
    notes = []
    if info.get("requires_python"):
        notes.append(f"requires Python {info['requires_python']}")
    if yanked and info.get("yanked_reason"):
        notes.append(f"yanked because: {_short(info['yanked_reason'])}")
    inactive = any("Development Status :: 7 - Inactive" in c for c in classifiers)
    return PackageInfo(
        "PyPI",
        str(info.get("name") or ""),
        str(info.get("version") or "unknown"),
        _date(files[0].get("upload_time_iso_8601")) if files else None,
        license_text or None,
        repo,
        "marked Inactive by its maintainers (classifier Development Status :: 7)"
        if inactive
        else None,
        yanked,
        notes,
    )


def parse_npm(data: dict[str, Any]) -> PackageInfo:
    license_value = data.get("license")
    if isinstance(license_value, dict):
        license_value = license_value.get("type")
    repository = data.get("repository")
    url = repository.get("url") if isinstance(repository, dict) else repository
    deprecated = data.get("deprecated")
    return PackageInfo(
        "npm",
        str(data.get("name") or ""),
        str(data.get("version") or "unknown"),
        None,
        str(license_value) if license_value else None,
        _repo_url(url),
        _short(deprecated) if deprecated else None,
        False,
        ["the registry's latest document carries no release date"],
    )


def parse_crates(data: dict[str, Any]) -> PackageInfo:
    crate = data.get("crate") or {}
    latest = str(crate.get("max_stable_version") or crate.get("max_version") or "unknown")
    version: dict[str, Any] = next(
        (v for v in data.get("versions") or [] if v.get("num") == latest), {}
    )
    notes = []
    if version.get("rust_version"):
        notes.append(f"requires Rust {version['rust_version']}")
    if version.get("yanked") and version.get("yank_message"):
        notes.append(f"yanked because: {_short(version['yank_message'])}")
    return PackageInfo(
        "crates.io",
        str(crate.get("name") or ""),
        latest,
        _date(version.get("created_at")),
        version.get("license"),
        _repo_url(crate.get("repository")),
        None,
        bool(version.get("yanked")),
        notes,
    )


def parse_go(module: str, latest: dict[str, Any], mod_file: str | None) -> PackageInfo:
    deprecated = None
    for line in (mod_file or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("module "):
            break
        if match := re.match(r"//\s*Deprecated:\s*(.*)", stripped):
            deprecated = _short(match.group(1)) or "deprecated"
    origin = latest.get("Origin") or {}
    return PackageInfo(
        "Go",
        module,
        str(latest.get("Version") or "unknown"),
        _date(latest.get("Time")),
        None,
        _repo_url(origin.get("URL")),
        deprecated,
        False,
        ["the module proxy does not report a license"],
    )


# -- network -----------------------------------------------------------------------------------


def _get_json(fetcher: WebFetcher, url: str, label: str, name: str) -> Any:
    response = fetcher.request(
        "GET",
        url,
        headers={"Accept": "application/json"},
        follow_redirects=False,
        enforce_rules=False,  # fixed registry hosts, still checked for public addresses
        accept_types=("application/json", "text/plain"),
    )
    if response.status in (404, 410):
        raise ToolError(f"No package '{name}' on {label}. Check the spelling and the ecosystem.")
    if response.status >= 400:
        raise ToolError(f"{label} answered HTTP {response.status} for '{name}'.")
    try:
        return json.loads(response.body)
    except ValueError as exc:
        raise ToolError(f"{label} returned something that is not valid JSON for '{name}'.") from exc


def _escape_go(module: str) -> str:
    return re.sub(r"[A-Z]", lambda m: "!" + m.group(0).lower(), module)


def fetch_package(fetcher: WebFetcher, ecosystem: str, name: str) -> PackageInfo:
    """Look ``name`` up in the registry of ``ecosystem`` (after validating both)."""

    kind = normalise_ecosystem(ecosystem)
    package = name.strip()
    if not NAME_PATTERNS[kind].fullmatch(package) or ".." in package:
        raise ToolError(f"'{name}' is not a valid {LABELS[kind]} package name.")
    label = LABELS[kind]
    if kind == "pypi":
        return parse_pypi(
            _get_json(fetcher, f"https://pypi.org/pypi/{package}/json", label, package)
        )
    if kind == "npm":
        url = f"https://registry.npmjs.org/{quote(package, safe='@')}/latest"
        return parse_npm(_get_json(fetcher, url, label, package))
    if kind == "crates":
        data = _get_json(fetcher, f"https://crates.io/api/v1/crates/{package}", label, package)
        return parse_crates(data)
    base = f"https://proxy.golang.org/{_escape_go(package)}"
    latest = _get_json(fetcher, f"{base}/@latest", label, package)
    mod_file = None
    version = str(latest.get("Version") or "")
    if re.fullmatch(r"v[\w.+-]+", version):
        response = fetcher.request(
            "GET",
            f"{base}/@v/{version}.mod",
            follow_redirects=False,
            enforce_rules=False,
            accept_types=("text/",),
        )
        mod_file = (
            response.body.decode("utf-8", errors="replace") if response.status == 200 else None
        )
    return parse_go(package, latest, mod_file)


def format_package(info: PackageInfo) -> str:
    released = f" ({info.released})" if info.released else ""
    lines = [f"{info.ecosystem} {info.name}: latest {info.latest}{released}"]
    if info.deprecated:
        lines.append(f"DEPRECATED: {info.deprecated}")
    if info.yanked:
        lines.append("YANKED: the latest version was withdrawn; choose another version.")
    lines.append(f"license: {info.license or 'not reported'}")
    lines.append(f"repository: {info.repository or 'not reported'}")
    lines.extend(f"note: {note}" for note in info.notes)
    return "\n".join(lines)


def lookup(fetcher: WebFetcher, ecosystem: str, name: str) -> str:
    return format_package(fetch_package(fetcher, ecosystem, name))
