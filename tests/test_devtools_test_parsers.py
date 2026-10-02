"""Test-result parsers: pure functions over the files in tests/fixtures/devtools."""

from __future__ import annotations

from pathlib import Path

import pytest

from engineering_team.devtools.parsers.junit import JUnitError, parse_junit
from engineering_team.devtools.parsers.tests_json import (
    ReportError,
    parse_go_test,
    parse_jest,
    parse_rspec,
)
from engineering_team.devtools.parsers.tests_text import (
    parse_cargo_test,
    parse_minitest,
    parse_unittest,
)
from engineering_team.devtools.parsers.trx import parse_trx

FIXTURES = Path(__file__).parent / "fixtures" / "devtools"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def by_id(report):  # type: ignore[no-untyped-def]
    return {failure.test_id: failure for failure in report.failures}


# -- pytest (JUnit) ----------------------------------------------------------------------


def test_pytest_junit_counts_and_failures() -> None:
    report = parse_junit(fixture("pytest_failures.xml"), framework="pytest", flavor="pytest")

    assert (report.passed, report.failed, report.skipped, report.errors) == (2, 3, 1, 0)
    assert report.status == "failed" and report.framework == "pytest"
    assert set(by_id(report)) == {
        "tests/test_calc.py::test_add_wrong",
        "tests/test_calc.py::test_div_by_zero",
        "tests/test_calc.py::test_param[2-3]",
    }


def test_pytest_failures_carry_file_line_and_a_short_message() -> None:
    failures = by_id(
        parse_junit(fixture("pytest_failures.xml"), framework="pytest", flavor="pytest")
    )

    wrong = failures["tests/test_calc.py::test_add_wrong"]
    assert (wrong.file, wrong.line) == ("tests/test_calc.py", 11)
    assert wrong.message.startswith("AssertionError: one plus two should be three")
    assert "assert 3 == 4" in wrong.trace_excerpt
    # The error is raised in calc.py, but the failing *test* is where the agent starts reading.
    zero = failures["tests/test_calc.py::test_div_by_zero"]
    assert (zero.file, zero.line) == ("tests/test_calc.py", 15)
    assert zero.message == "ZeroDivisionError: division by zero"


def test_a_pytest_collection_error_is_an_error_not_a_failure() -> None:
    report = parse_junit(
        fixture("pytest_collection_error.xml"), framework="pytest", flavor="pytest"
    )

    assert (report.passed, report.failed, report.errors) == (0, 0, 1)
    assert report.status == "failed"
    (error,) = report.failures
    assert error.test_id == "tests/test_broken.py"
    assert "No module named 'missing_module_xyz'" in error.trace_excerpt
    assert error.file == "tests/test_broken.py" and error.line == 1


def test_trace_excerpts_are_bounded() -> None:
    long_trace = "\n".join(f"tests/test_x.py:{n}: frame" for n in range(1, 200))
    xml = (
        '<testsuite><testcase classname="tests.test_x" name="test_a">'
        f'<failure message="boom">{long_trace}</failure></testcase></testsuite>'
    )

    (failure,) = parse_junit(xml, framework="pytest", flavor="pytest").failures

    assert failure.trace_excerpt.count("\n") <= 16 and len(failure.trace_excerpt) <= 1210


def test_pytest_ids_keep_the_class() -> None:
    xml = (
        '<testsuite><testcase classname="tests.test_x.TestThing" name="test_a">'
        '<failure message="no">tests/test_x.py:4: AssertionError</failure></testcase></testsuite>'
    )

    (failure,) = parse_junit(xml, framework="pytest", flavor="pytest").failures

    assert failure.test_id == "tests/test_x.py::TestThing::test_a"


def test_maven_surefire_reports_use_class_hash_method_ids() -> None:
    report = parse_junit(fixture("surefire.xml"), framework="maven")

    assert (report.passed, report.failed, report.skipped) == (1, 1, 1)
    (failure,) = report.failures
    assert failure.test_id == "com.acme.CalcTest#addsWrong"
    assert failure.message == "expected: <4> but was: <3>"
    assert "CalcTest.java:17" in failure.trace_excerpt


def test_phpunit_junit_has_file_and_line_attributes() -> None:
    report = parse_junit(fixture("phpunit.xml"), framework="phpunit")

    (failure,) = report.failures
    assert failure.test_id == "CalcTest#testAddWrong"
    assert (failure.file, failure.line) == ("/work/project/tests/CalcTest.php", 13)
    assert (report.passed, report.failed) == (1, 1)


@pytest.mark.parametrize("text", ["not xml", "<html></html>", ""])
def test_junit_rejects_things_that_are_not_junit(text: str) -> None:
    with pytest.raises(JUnitError):
        parse_junit(text, framework="x")


def test_an_empty_suite_passes_with_zero_tests() -> None:
    report = parse_junit('<testsuite name="x" tests="0"/>', framework="x")

    assert report.total == 0 and report.status == "passed"


# -- unittest ----------------------------------------------------------------------------


def test_unittest_counts_and_blocks() -> None:
    report = parse_unittest(fixture("unittest.txt"), root="/work/project")

    assert (report.passed, report.failed, report.errors, report.skipped) == (1, 1, 1, 1)
    assert report.status == "failed" and report.duration == 0.0
    failures = by_id(report)
    wrong = failures["tests.test_ut.CalcTests.test_add_wrong"]
    assert (wrong.file, wrong.line) == ("tests/test_ut.py", 11)
    assert wrong.message == "AssertionError: 3 != 4 : one plus two should be three"
    zero = failures["tests.test_ut.CalcTests.test_div_zero"]
    assert (zero.file, zero.line) == ("tests/test_ut.py", 14)
    assert zero.message == "ZeroDivisionError: division by zero"
    assert "calc.py" in zero.trace_excerpt


def test_unittest_ok_run() -> None:
    report = parse_unittest(".\n---\nRan 1 test in 0.001s\n\nOK\n")

    assert (report.passed, report.status, report.failures) == (1, "passed", [])


def test_unittest_import_errors_are_failures_named_by_module() -> None:
    text = (
        "ERROR: tests.test_x (unittest.loader._FailedTest.tests.test_x)\n"
        "----------------------------------------------------------------------\n"
        "ImportError: Failed to import test module: tests.test_x\n"
        "Traceback (most recent call last):\n"
        '  File "/work/project/tests/test_x.py", line 1, in <module>\n'
        "    import nothing\n"
        "ModuleNotFoundError: No module named 'nothing'\n\n"
        "----------------------------------------------------------------------\n"
        "Ran 1 test in 0.000s\n\nFAILED (errors=1)\n"
    )

    report = parse_unittest(text, root="/work/project")

    assert report.errors == 1
    assert "No module named 'nothing'" in report.failures[0].message


# -- jest and vitest ---------------------------------------------------------------------


def test_jest_json_counts_failures_and_suites_that_did_not_load() -> None:
    report = parse_jest(fixture("jest.json"), root="/work/project")

    assert (report.passed, report.failed, report.skipped, report.errors) == (2, 1, 1, 1)
    failures = by_id(report)
    wrong = failures["src/calc.test.js::calc adds wrong"]
    assert (wrong.file, wrong.line) == ("src/calc.test.js", 9)  # not the node_modules frame
    assert wrong.message == "Error: expect(received).toBe(expected)"  # ANSI codes stripped
    broken = failures["src/broken.test.js"]
    assert "Cannot find module './missing'" in broken.trace_excerpt
    assert report.duration == pytest.approx(0.016)


def test_vitest_reports_use_the_same_parser_with_their_own_name() -> None:
    assert parse_jest(fixture("jest.json"), framework="vitest").framework == "vitest"


@pytest.mark.parametrize("text", ["nope", "{}", "[]"])
def test_jest_rejects_other_json(text: str) -> None:
    with pytest.raises(ReportError):
        parse_jest(text)


# -- go test -----------------------------------------------------------------------------


def test_go_test_counts_leaf_tests_and_locates_failures() -> None:
    report = parse_go_test(fixture("gotest.jsonl"), module="example.com")

    # TestAdd passed, TestAddWrong and TestSub/case failed, TestSkip skipped; TestSub is a parent.
    assert (report.passed, report.failed, report.skipped, report.errors) == (1, 2, 1, 1)
    failures = by_id(report)
    wrong = failures["example.com/calc::TestAddWrong"]
    assert (wrong.file, wrong.line) == ("calc/calc_test.go", 13)
    assert wrong.message == "Add(1, 2) = 3, want 4"
    assert "example.com/calc::TestSub/case" in failures
    assert "example.com/calc::TestSub" not in failures


def test_a_go_package_that_does_not_build_is_one_error() -> None:
    broken = by_id(parse_go_test(fixture("gotest.jsonl")))["example.com/broken"]

    assert broken.message == "# example.com/broken"
    assert "undefined: nope" in broken.trace_excerpt


def test_go_output_without_events_is_rejected() -> None:
    with pytest.raises(ReportError, match="-json"):
        parse_go_test("ok  \texample.com/calc\t0.1s\n")


# -- cargo test --------------------------------------------------------------------------


def test_cargo_test_text() -> None:
    report = parse_cargo_test(fixture("cargo_test.txt"))

    assert (report.passed, report.failed, report.skipped) == (1, 1, 1)
    (failure,) = report.failures
    assert failure.test_id == "tests::test_wrong"
    assert (failure.file, failure.line) == ("src/lib.rs", 12)
    assert failure.message == "assertion `left == right` failed"
    assert "right: 4" in failure.trace_excerpt


def test_cargo_test_old_panic_format() -> None:
    (failure,) = parse_cargo_test(fixture("cargo_test_old.txt")).failures

    assert (failure.file, failure.line, failure.message) == ("src/lib.rs", 5, "explicit failure")


def test_cargo_results_from_several_binaries_add_up() -> None:
    text = (
        "test result: ok. 3 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; "
        "finished in 0.01s\n"
        "test result: ok. 2 passed; 0 failed; 1 ignored; 0 measured; 0 filtered out; "
        "finished in 0.02s\n"
    )

    report = parse_cargo_test(text)

    assert (report.passed, report.skipped, report.duration) == (5, 1, 0.03)


# -- minitest ----------------------------------------------------------------------------


def test_minitest_text() -> None:
    report = parse_minitest(fixture("minitest.txt"))

    assert (report.passed, report.failed, report.errors, report.skipped) == (1, 1, 1, 1)
    failures = by_id(report)
    wrong = failures["CalcTest#test_add_wrong"]
    assert (wrong.file, wrong.line) == ("calc_test.rb", 10)
    assert wrong.message == "Expected: 4"
    boom = failures["CalcTest#test_boom"]
    assert (boom.file, boom.line) == ("calc_test.rb", 14)
    assert boom.message == "ArgumentError: boom"


# -- dotnet and rspec --------------------------------------------------------------------


def test_trx_results() -> None:
    report = parse_trx(fixture("dotnet.trx"), root="/work/project")

    assert (report.passed, report.failed, report.skipped) == (1, 1, 1)
    (failure,) = report.failures
    assert failure.test_id == "Calc.Tests.AddTests.AddsWrong"
    assert (failure.file, failure.line) == ("Calc.Tests/AddTests.cs", 14)
    assert failure.message == "Assert.Equal() Failure"
    assert report.duration == 0.011


def test_rspec_json() -> None:
    report = parse_rspec(fixture("rspec.json"))

    assert (report.passed, report.failed, report.skipped) == (1, 1, 1)
    (failure,) = report.failures
    assert failure.test_id == "./spec/calc_spec.rb[1:2]"
    assert (failure.file, failure.line) == ("spec/calc_spec.rb", 9)
    assert failure.message.startswith("RSpec::Expectations::ExpectationNotMetError")
    assert report.duration == 0.012


def test_rspec_rejects_other_json() -> None:
    with pytest.raises(ReportError):
        parse_rspec('{"tests": []}')
