"""Package Info: parsers on recorded registry responses, and the lookups over a fake network."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from web_fakes import FakeNet

from engineering_team.tools.support import ToolError
from engineering_team.webtools.packages import (
    PackageInfo,
    fetch_package,
    format_package,
    lookup,
    normalise_ecosystem,
    parse_crates,
    parse_go,
    parse_npm,
    parse_pypi,
)
from engineering_team.webtools.safenet import WebFetcher

FIXTURES = Path(__file__).parent / "fixtures" / "web"
PUBLIC = "93.184.216.34"
JSON = {"Content-Type": "application/json"}


def recorded(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_pypi_reports_version_date_license_repository_and_python_requirement() -> None:
    info = parse_pypi(json.loads(recorded("pypi_six.json")))

    assert (info.ecosystem, info.name, info.latest) == ("PyPI", "six", "1.17.0")
    assert info.released == "2024-12-04"
    assert info.license == "MIT"
    assert info.repository == "https://github.com/benjaminp/six"
    assert info.deprecated is None and info.yanked is False
    assert any(note.startswith("requires Python") and ">=2.7" in note for note in info.notes)


def test_pypi_flags_yanked_and_inactive_packages() -> None:
    data = json.loads(recorded("pypi_six.json"))
    data["info"]["yanked"] = True
    data["info"]["yanked_reason"] = "broken wheel"
    data["info"]["classifiers"].append("Development Status :: 7 - Inactive")

    info = parse_pypi(data)

    assert info.yanked is True
    assert info.deprecated and "Inactive" in info.deprecated
    assert any("broken wheel" in note for note in info.notes)


def test_pypi_prefers_the_spdx_expression_and_ignores_pasted_license_text() -> None:
    data = json.loads(recorded("pypi_six.json"))
    data["info"]["license_expression"] = "Apache-2.0"
    assert parse_pypi(data).license == "Apache-2.0"

    data["info"]["license_expression"] = None
    data["info"]["license"] = "Permission is hereby granted, free of charge " * 20
    assert parse_pypi(data).license == "MIT License"  # from the trove classifier


def test_npm_reports_the_deprecation_message_and_a_clean_repository_url() -> None:
    info = parse_npm(json.loads(recorded("npm_left_pad.json")))

    assert (info.ecosystem, info.name, info.latest, info.license) == (
        "npm",
        "left-pad",
        "1.3.0",
        "WTFPL",
    )
    assert info.deprecated == "use String.prototype.padStart()"
    assert info.repository == "https://github.com/stevemao/left-pad"
    assert info.released is None


def test_crates_uses_the_max_stable_version_entry() -> None:
    info = parse_crates(json.loads(recorded("crates_itoa.json")))

    assert (info.ecosystem, info.name, info.latest) == ("crates.io", "itoa", "1.0.18")
    assert info.released == "2026-03-20"
    assert info.license == "MIT OR Apache-2.0"
    assert info.repository == "https://github.com/dtolnay/itoa"
    assert info.yanked is False
    assert any("Rust 1.68" in note for note in info.notes)


def test_crates_flags_a_yanked_latest_version() -> None:
    data = json.loads(recorded("crates_itoa.json"))
    data["versions"][0]["yanked"] = True
    data["versions"][0]["yank_message"] = "UB"

    assert parse_crates(data).yanked is True


def test_go_reads_the_deprecation_comment_from_the_mod_file() -> None:
    info = parse_go(
        "github.com/golang/protobuf",
        json.loads(recorded("go_protobuf_latest.json")),
        recorded("go_protobuf.mod"),
    )

    assert (info.ecosystem, info.latest, info.released) == ("Go", "v1.5.4", "2024-03-06")
    assert info.deprecated == 'Use the "google.golang.org/protobuf" module instead.'
    assert info.repository == "https://github.com/golang/protobuf"
    assert info.license is None
    assert (
        parse_go("example.com/x", {"Version": "v1.0.0"}, "module example.com/x\n").deprecated
        is None
    )


def test_the_report_leads_with_the_version_and_names_problems() -> None:
    info = PackageInfo(
        "npm",
        "left-pad",
        "1.3.0",
        None,
        "WTFPL",
        "https://github.com/x/y",
        "use padStart",
        False,
        [],
    )

    text = format_package(info)

    lines = text.splitlines()
    assert lines[0].startswith("npm left-pad: latest 1.3.0")
    assert any(line.startswith("DEPRECATED: use padStart") for line in lines)
    assert "license: WTFPL" in text and "repository: https://github.com/x/y" in text


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("pypi", "pypi"),
        ("PyPI", "pypi"),
        ("python", "pypi"),
        ("npm", "npm"),
        ("node", "npm"),
        ("crates", "crates"),
        ("cargo", "crates"),
        ("rust", "crates"),
        ("go", "go"),
        ("golang", "go"),
    ],
)
def test_ecosystem_names_have_aliases(given: str, expected: str) -> None:
    assert normalise_ecosystem(given) == expected


def test_an_unknown_ecosystem_lists_the_valid_ones() -> None:
    with pytest.raises(ToolError, match="pypi, npm, crates, go"):
        normalise_ecosystem("maven")


@pytest.fixture
def fetcher_and_net() -> tuple[WebFetcher, FakeNet]:
    net = FakeNet(
        hosts={
            host: [PUBLIC]
            for host in ("pypi.org", "registry.npmjs.org", "crates.io", "proxy.golang.org")
        }
    )
    net.routes[("pypi.org", "/pypi/six/json")] = (200, JSON, recorded("pypi_six.json").encode())
    net.routes[("registry.npmjs.org", "/left-pad/latest")] = (
        200,
        JSON,
        recorded("npm_left_pad.json").encode(),
    )
    net.routes[("registry.npmjs.org", "/@types%2Fnode/latest")] = (
        200,
        JSON,
        json.dumps({"name": "@types/node", "version": "22.0.0", "license": "MIT"}).encode(),
    )
    net.routes[("crates.io", "/api/v1/crates/itoa")] = (
        200,
        JSON,
        recorded("crates_itoa.json").encode(),
    )
    net.routes[("proxy.golang.org", "/github.com/golang/protobuf/@latest")] = (
        200,
        JSON,
        recorded("go_protobuf_latest.json").encode(),
    )
    net.routes[("proxy.golang.org", "/github.com/golang/protobuf/@v/v1.5.4.mod")] = (
        200,
        {"Content-Type": "text/plain"},
        recorded("go_protobuf.mod").encode(),
    )
    net.routes[("proxy.golang.org", "/github.com/!azure/x/@latest")] = (
        200,
        JSON,
        b'{"Version": "v0.1.0", "Time": "2025-01-02T00:00:00Z"}',
    )
    net.routes[("proxy.golang.org", "/github.com/!azure/x/@v/v0.1.0.mod")] = (
        404,
        {"Content-Type": "text/plain"},
        b"not found",
    )
    fetcher = WebFetcher(resolver=net.resolver, connector=net.connector, wrap_tls=net.wrap_tls)
    return fetcher, net


@pytest.mark.parametrize(
    ("ecosystem", "name", "expected"),
    [
        ("pypi", "six", "PyPI six: latest 1.17.0"),
        ("npm", "left-pad", "npm left-pad: latest 1.3.0"),
        ("npm", "@types/node", "npm @types/node: latest 22.0.0"),
        ("crates", "itoa", "crates.io itoa: latest 1.0.18"),
        ("go", "github.com/golang/protobuf", "Go github.com/golang/protobuf: latest v1.5.4"),
        ("go", "github.com/Azure/x", "Go github.com/Azure/x: latest v0.1.0"),
    ],
)
def test_lookups_call_the_official_registries(
    fetcher_and_net: tuple[WebFetcher, FakeNet], ecosystem: str, name: str, expected: str
) -> None:
    fetcher, _ = fetcher_and_net

    assert lookup(fetcher, ecosystem, name).startswith(expected)


def test_a_missing_package_is_reported_with_the_registry(
    fetcher_and_net: tuple[WebFetcher, FakeNet],
) -> None:
    fetcher, _ = fetcher_and_net

    with pytest.raises(ToolError, match="No package 'nope' on PyPI"):
        fetch_package(fetcher, "pypi", "nope")


@pytest.mark.parametrize(
    ("ecosystem", "name"),
    [
        ("pypi", "../etc/passwd"),
        ("pypi", "a b"),
        ("npm", "x?y=1"),
        ("crates", "a/b"),
        ("go", "evil.com/x?y"),
        ("go", ""),
        ("npm", "UPPER case"),
    ],
)
def test_package_names_are_validated_before_any_request(
    fetcher_and_net: tuple[WebFetcher, FakeNet], ecosystem: str, name: str
) -> None:
    fetcher, net = fetcher_and_net

    with pytest.raises(ToolError, match="not a valid"):
        fetch_package(fetcher, ecosystem, name)

    assert net.connections == []
