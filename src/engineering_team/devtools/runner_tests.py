"""Running tests: any detected framework, reruns of the last failures, and single tests."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from engineering_team.atomic_io import atomic_write_json
from engineering_team.devtools.detect import Stack
from engineering_team.devtools.models import DevStatus, TestFailure, TestReport
from engineering_team.devtools.parsers.common import excerpt
from engineering_team.devtools.plans import Selection, TestCommand, build_test_command
from engineering_team.devtools.runner_base import BaseRunner, Execution
from engineering_team.tools.scripts import resolve_script
from engineering_team.tools.support import ToolError

LAST_TESTS_FILE = "last_tests.json"


class TestsMixin(BaseRunner):
    __test__ = False

    def run_tests(
        self,
        working_directory: str,
        selection: Selection,
        *,
        framework: str | None = None,
        timeout: int | None = None,
    ) -> TestReport:
        directory, stack = self.resolve(working_directory)
        limit = self.timeout(self.cfg.test_timeout, timeout)
        if stack.test is None and framework is None:
            return self._run_test_script(directory, stack, limit)
        paths = self.scratch()
        try:
            plan = build_test_command(stack, directory, selection, paths, framework=framework)
            ex = self.execute(plan.argv, directory, limit)
            report = self.test_report(plan, ex, stack)
        finally:
            shutil.rmtree(paths.directory, ignore_errors=True)
        self._remember(stack.directory, report)
        return report

    def test_report(self, plan: TestCommand, ex: Execution, stack: Stack) -> TestReport:
        """Parse a finished test command; anything that is not a clear result is an ``error``."""

        if ex.record is None or ex.tool_missing:
            return self.finish(
                TestReport(framework=plan.framework, status="unavailable"), ex, stack
            )
        existing = [path for path in plan.reports if path.exists()]
        text = None
        if existing and existing[0].is_file():
            text = existing[0].read_text(encoding="utf-8", errors="replace")
        try:
            report = plan.parse(ex.text, text)
        except (ValueError, KeyError, TypeError) as exc:
            report = TestReport(
                status="error", hint=f"The test report could not be read ({exc}).", raw_tail=ex.tail
            )
        report = report.model_copy(update={"framework": plan.framework})
        report = self._anchored(report, stack.directory)
        if report.status == "error":
            report = report.model_copy(update={"raw_tail": report.raw_tail[-1500:] or ex.tail})
        elif report.total == 0:
            report = report.model_copy(
                update={
                    "status": "error",
                    "raw_tail": ex.tail,
                    "hint": "No tests ran; check the path, the filter, and that tests exist.",
                }
            )
        elif report.status == "passed" and ex.exit_code != 0:
            report = report.model_copy(
                update={
                    "status": "error",
                    "raw_tail": ex.tail,
                    "hint": f"Exit code {ex.exit_code} although no test failed; see the log.",
                }
            )
        cap = self.cfg.max_failures
        if len(report.failures) > cap:
            omitted = len(report.failures) - cap
            report = report.model_copy(
                update={"failures": report.failures[:cap], "failures_omitted": omitted}
            )
        return self.finish(report, ex, stack)

    def _run_test_script(self, directory: Path, stack: Stack, limit: int) -> TestReport:
        """No known framework: run the project's own ``test`` script and return the raw log."""

        script = resolve_script(directory, "test", stack.directory)
        ex = self.execute(script.argv, directory, limit)
        status: DevStatus = "passed" if ex.exit_code == 0 else "failed"
        report = TestReport(
            framework="unknown",
            status="unavailable" if ex.tool_missing else status,
            raw_tail=excerpt(ex.text[-3000:], 3000),
            hint="Unrecognised test framework: this is the raw output of the project's script.",
        )
        return self.finish(report, ex, stack)

    def rerun_failed(self, working_directory: str, timeout: int | None = None) -> TestReport:
        """Run exactly the tests that failed in this project's last run."""

        _, stack = self.resolve(working_directory)
        last = self._recall(stack.directory)
        if last is None:
            raise ToolError("No earlier test run to repeat. Call Run Tests first.")
        if not last.failures:
            raise ToolError("Nothing to rerun: the last test run had no failing tests.")
        directory, _ = self.resolve(working_directory)
        selection = self.selection(directory, failures=last.failures)
        return self.run_tests(
            working_directory, selection, framework=last.framework or None, timeout=timeout
        )

    def run_single(
        self, working_directory: str, test_id: str, timeout: int | None = None
    ) -> TestReport:
        """Run one test by the id a test report lists."""

        directory, _ = self.resolve(working_directory)
        test_id = test_id.strip()
        if not test_id or test_id.startswith("-"):
            raise ToolError(
                "Pass a test id exactly as a test report lists it, e.g. 'tests/test_x.py::test_y'."
            )
        selection = self.selection(directory, failures=[TestFailure(test_id=test_id)])
        return self.run_tests(working_directory, selection, timeout=timeout)

    # -- the last run, per project directory -------------------------------------------

    def _remember(self, key: str, report: TestReport) -> None:
        path = self.ctx.run_dir / "devtools" / LAST_TESTS_FILE
        with self._lock:
            try:
                data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
            except (OSError, ValueError):
                data = {}
            data[key] = report.model_dump(mode="json")
            atomic_write_json(path, data)

    def _recall(self, key: str) -> TestReport | None:
        path = self.ctx.run_dir / "devtools" / LAST_TESTS_FILE
        with self._lock:
            try:
                return TestReport.model_validate(json.loads(path.read_text(encoding="utf-8"))[key])
            except (OSError, ValueError, KeyError):
                return None

    def _anchored(self, report: TestReport, directory: str) -> TestReport:
        """Failure files relative to the project root rather than the tool's working directory."""

        failures = [
            f.model_copy(update={"file": self.under(directory, f.file)}) for f in report.failures
        ]
        return report.model_copy(update={"failures": failures})
