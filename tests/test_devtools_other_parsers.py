"""Lint, type-check, build, format, coverage, and audit parsers over fixture files."""

from __future__ import annotations

from pathlib import Path

import pytest

from engineering_team.devtools.models import Diagnostic
from engineering_team.devtools.parsers import audit, coverage, diagnostics, format

FIXTURES = Path(__file__).parent / "fixtures" / "devtools"
ROOT = "/work/project"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def triples(items: list[Diagnostic]) -> list[tuple[str | None, int | None, str | None]]:
    return [(d.file, d.line, d.rule) for d in items]


# -- linters and type checkers -----------------------------------------------------------


def test_ruff_json_is_normalised() -> None:
    found = diagnostics.parse_ruff(fixture("ruff.json"), ROOT)

    assert [(d.file, d.line, d.col, d.rule) for d in found][:2] == [
        ("lint_me.py", 1, 1, "E401"),
        ("lint_me.py", 1, 8, "F401"),
    ]
    assert found[0].severity == "error" and found[0].message == "Multiple imports on one line"


def test_mypy_text_skips_notes_and_keeps_codes() -> None:
    found = diagnostics.parse_mypy(fixture("mypy.txt"))

    assert triples(found) == [
        ("typed.py", 2, "return-value"),
        ("typed.py", 6, "arg-type"),
        ("typed.py", 7, "name-defined"),
    ]
    assert found[0].col == 12 and found[0].message.startswith("Incompatible return value type")
    assert diagnostics.parse_mypy("typed.py:3:1: note: See the docs\n") == []


def test_eslint_json_maps_severity_numbers() -> None:
    found = diagnostics.parse_eslint(fixture("eslint.json"), ROOT)

    assert [(d.file, d.line, d.severity, d.rule) for d in found] == [
        ("src/app.js", 3, "error", "no-unused-vars"),
        ("src/app.js", 9, "warning", "eqeqeq"),
    ]


def test_pyright_json_converts_zero_based_positions() -> None:
    found = diagnostics.parse_pyright(fixture("pyright.json"), ROOT)

    assert [(d.file, d.line, d.col, d.severity, d.rule) for d in found] == [
        ("app.py", 2, 12, "error", "reportReturnType"),
        ("app.py", 1, 8, "warning", "reportUnusedImport"),
    ]


def test_tsc_and_msbuild_paren_style() -> None:
    ts = diagnostics.parse_paren_style(fixture("tsc.txt"))
    assert [(d.file, d.line, d.col, d.rule) for d in ts] == [
        ("src/index.ts", 12, 5, "TS2322"),
        ("src/util.ts", 3, 10, "TS2304"),
    ]
    cs = diagnostics.parse_paren_style(fixture("msbuild.txt"), ROOT)
    assert [(d.file, d.line, d.severity, d.rule) for d in cs] == [
        ("Calc/Program.cs", 7, "error", "CS0103"),
        ("Calc/Program.cs", 9, "warning", "CS0168"),
    ]
    assert cs[0].message == "The name 'nope' does not exist in the current context"


def test_go_vet_and_golangci_colon_style() -> None:
    vet = diagnostics.parse_colon_style(fixture("govet.txt"))
    assert [(d.file, d.line, d.col) for d in vet] == [
        ("bad.go", 4, 9),
        ("bad.go", 4, 9),
        ("other.go", 7, 2),
    ]
    lint = diagnostics.parse_colon_style(fixture("golangci.txt"))
    assert [(d.file, d.line, d.rule) for d in lint] == [
        ("calc.go", 12, "errcheck"),
        ("calc.go", 20, "unused"),
    ]
    assert lint[0].message == "Error return value of `f.Close` is not checked"


def test_cargo_json_uses_the_primary_span_and_skips_summaries() -> None:
    found = diagnostics.parse_cargo_json(fixture("cargo_check.jsonl"))

    assert [(d.file, d.line, d.col, d.severity, d.rule) for d in found] == [
        ("src/lib.rs", 4, 5, "error", "E0308"),
        ("src/main.rs", 2, 9, "warning", "unused_variables"),
    ]


def test_rubocop_and_phpcs_json() -> None:
    rubocop = diagnostics.parse_rubocop(fixture("rubocop.json"))
    assert [(d.line, d.severity, d.rule) for d in rubocop] == [
        (3, "warning", "Layout/IndentationWidth"),
        (8, "error", "Lint/Syntax"),
    ]
    phpcs = diagnostics.parse_phpcs(fixture("phpcs.json"), ROOT)
    assert [(d.file, d.line, d.col, d.severity) for d in phpcs] == [
        ("src/Calc.php", 3, 1, "error"),
        ("src/Calc.php", 12, 86, "warning"),
    ]


def test_maven_compile_errors() -> None:
    found = diagnostics.parse_maven(fixture("maven_errors.txt"), ROOT)

    assert [(d.file, d.line, d.col) for d in found] == [
        ("src/main/java/com/acme/Calc.java", 12, 5),
        ("src/main/java/com/acme/Calc.java", 20, 9),
    ]


@pytest.mark.parametrize(
    ("parser", "text"),
    [
        (diagnostics.parse_ruff, "{}"),
        (diagnostics.parse_ruff, "nope"),
        (diagnostics.parse_eslint, "{}"),
        (diagnostics.parse_pyright, "[]"),
        (diagnostics.parse_rubocop, "[]"),
        (diagnostics.parse_phpcs, "[]"),
    ],
)
def test_json_parsers_name_the_flag_that_produces_their_format(parser, text: str) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(diagnostics.DiagnosticsError):
        parser(text)


def test_diagnostics_are_sorted_errors_first_and_capped() -> None:
    items = [
        Diagnostic(file="b.py", line=1, severity="warning", message="w"),
        Diagnostic(file="b.py", line=9, severity="error", message="e2"),
        Diagnostic(file="a.py", line=5, severity="error", message="e1"),
        Diagnostic(file="a.py", line=2, severity="info", message="i"),
    ]

    kept, omitted = diagnostics.sort_and_cap(items, 2)

    assert [d.message for d in kept] == ["e1", "e2"] and omitted == 2
    assert diagnostics.sort_and_cap(items, 10)[1] == 0


def test_long_messages_are_cut() -> None:
    long = "x" * 1000
    (found,) = diagnostics.parse_mypy(f"a.py:1:1: error: {long}  [misc]\n")

    assert len(found.message) < 310 and found.rule == "misc"


# -- formatters --------------------------------------------------------------------------


def test_format_check_listings() -> None:
    assert format.parse_format_check("ruff", fixture("ruff_format_check.txt")) == ["lint_me.py"]
    # ruff 0.16 prints the location of each file instead of "Would reformat".
    assert format.parse_format_check("ruff", fixture("ruff_format_check_new.txt")) == ["ugly.py"]
    assert format.parse_format_check("black", fixture("black_check.txt"), ROOT) == ["app.py"]
    assert format.parse_format_check("prettier", fixture("prettier_check.txt")) == [
        "src/app.js",
        "src/style.css",
    ]
    assert format.parse_format_check("gofmt", fixture("gofmt.txt")) == ["calc.go", "sub/util.go"]
    assert format.parse_format_check("rustfmt", fixture("cargo_fmt.txt"), ROOT) == ["src/lib.rs"]
    assert format.parse_format_check("ruff", "2 files already formatted\n") == []


# -- coverage ----------------------------------------------------------------------------


def test_line_ranges() -> None:
    assert coverage.line_ranges([9, 1, 2, 3, 7, 10, 3]) == ["1-3", "7", "9-10"]
    assert coverage.line_ranges([]) == []


def test_lcov_files_and_uncovered_ranges() -> None:
    calc, util = coverage.parse_lcov(fixture("lcov.info"), ROOT)

    assert (calc.path, calc.covered, calc.total, calc.percent) == ("src/calc.js", 3, 7, 42.9)
    assert calc.uncovered_ranges == ["3-5", "8"]
    assert (util.percent, util.uncovered_ranges) == (100.0, [])


def test_go_cover_profile_uses_statements_and_module_relative_paths() -> None:
    calc, util = coverage.parse_go_cover(fixture("go.cover"), "example.com/calc")

    assert (calc.path, calc.covered, calc.total) == ("calc.go", 1, 4)
    assert calc.uncovered_ranges == ["5-7", "9-10"]
    assert (util.path, util.percent) == ("util.go", 100.0)


def test_coverage_py_json() -> None:
    calc, util = coverage.parse_coverage_py_json(fixture("coverage_py.json"))

    assert (calc.path, calc.covered, calc.total, calc.uncovered_ranges) == (
        "calc.py",
        3,
        6,
        ["6-7", "9"],
    )
    assert util.percent == 100.0


@pytest.mark.parametrize(
    "parser", [coverage.parse_lcov, coverage.parse_go_cover, coverage.parse_coverage_py_json]
)
def test_coverage_parsers_reject_other_formats(parser) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(coverage.CoverageError):
        parser("hello")


# -- audits ------------------------------------------------------------------------------


def test_pip_audit_lists_only_vulnerable_packages() -> None:
    (vuln,) = audit.parse_pip_audit(fixture("pip_audit.json"))

    assert (vuln.package, vuln.version, vuln.id, vuln.fixed_in) == (
        "requests",
        "2.19.0",
        "PYSEC-2018-28",
        "2.20.0",
    )
    assert vuln.title.startswith("The Requests package")


def test_npm_audit_v7_and_v6() -> None:
    found = audit.parse_npm_audit(fixture("npm_audit.json"))

    lodash = next(v for v in found if v.package == "lodash")
    assert (lodash.severity, lodash.fixed_in, lodash.id) == (
        "high",
        "4.17.21",
        "GHSA-p6mc-m468-83gw",
    )
    assert lodash.title == "Prototype Pollution in lodash"
    old = '{"advisories": {"1": {"module_name": "x", "severity": "low", "title": "t", "id": 1}}}'
    assert audit.parse_npm_audit(old)[0].package == "x"
    with pytest.raises(audit.AuditError, match="registry down"):
        audit.parse_npm_audit('{"error": {"summary": "registry down"}}')


def test_cargo_audit() -> None:
    (vuln,) = audit.parse_cargo_audit(fixture("cargo_audit.json"))

    assert (vuln.package, vuln.id, vuln.fixed_in) == ("time", "RUSTSEC-2020-0071", ">=0.2.23")


def test_audits_sort_by_severity_and_cap() -> None:
    found = audit.parse_npm_audit(fixture("npm_audit.json"))

    kept, omitted = audit.sort_and_cap(found, 1)

    assert [v.package for v in kept] == ["lodash"] and omitted == 1


@pytest.mark.parametrize(
    "parser", [audit.parse_pip_audit, audit.parse_npm_audit, audit.parse_cargo_audit]
)
def test_audit_parsers_reject_garbage(parser) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(audit.AuditError):
        parser("not json")
