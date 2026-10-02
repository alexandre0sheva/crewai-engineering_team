"""Coverage, dependency installation, and dependency audits."""

from __future__ import annotations

import shutil

from engineering_team.devtools.models import (
    AuditReport,
    CoverageReport,
    FileCoverage,
    InstallReport,
)
from engineering_team.devtools.parsers.audit import sort_and_cap
from engineering_team.devtools.parsers.common import excerpt
from engineering_team.devtools.plans_deps import audit_command, coverage_command, install_argv
from engineering_team.devtools.runner_tests import TestsMixin
from engineering_team.tools.support import ToolError

NETWORK_HINT = (
    "Needs network access; with none (or an offline sandbox) the registry cannot be reached."
)
NETWORK_ERRORS = (
    "connection",
    "enotfound",
    "getaddrinfo",
    "max retries exceeded",
    "network",
    "timed out",
    "temporary failure in name resolution",
)
TOP_FILES = 10


class DepsMixin(TestsMixin):
    def coverage(
        self,
        working_directory: str,
        file: str = "",
        paths: tuple[str, ...] = (),
        timeout: int | None = None,
    ) -> CoverageReport:
        """Run the tests under coverage; with ``file`` also list that file's uncovered lines."""

        directory, stack = self.resolve(working_directory)
        selection = self.selection(directory, paths)
        scratch = self.scratch()
        try:
            plan = coverage_command(stack, directory, selection, scratch, str(directory))
            limit = self.timeout(self.cfg.coverage_timeout, timeout)
            ex = self.execute(plan.tests.argv, directory, limit)
            tests = self.test_report(plan.tests, ex, stack)
            base = CoverageReport(tool=plan.tool, tests=tests)
            if ex.record is None or ex.tool_missing:
                return self.finish(base.model_copy(update={"status": "unavailable"}), ex, stack)
            if plan.follow_up is not None:
                follow = self.execute(plan.follow_up, directory, limit)
                if follow.tool_missing or follow.exit_code != 0:
                    return self.finish(
                        base.model_copy(update={"status": "error", "hint": follow.tail}),
                        follow,
                        stack,
                    )
            if not plan.report_file.is_file():
                problem = "The tests ran but wrote no coverage data."
                return self.finish(
                    base.model_copy(update={"status": "error", "hint": problem}), ex, stack
                )
            try:
                files = plan.parse(plan.report_file.read_text(encoding="utf-8", errors="replace"))
            except ValueError as exc:
                problem = f"The coverage report could not be read ({exc})."
                return self.finish(
                    base.model_copy(update={"status": "error", "hint": problem}), ex, stack
                )
        finally:
            shutil.rmtree(scratch.directory, ignore_errors=True)
        files = [
            f.model_copy(update={"path": self.under(stack.directory, f.path) or f.path})
            for f in files
        ]
        detail = _match(files, file)
        hint: str | None = None
        if file and detail is None:
            known = ", ".join(f.path for f in files[:6])
            hint = f"No coverage data for {file!r}. Files with data: {known}."
        lowest = sorted((f for f in files if f.total), key=lambda f: (f.percent, f.path))[
            :TOP_FILES
        ]
        report = base.model_copy(
            update={
                "status": "failed" if tests.status == "failed" else "passed",
                "covered": sum(f.covered for f in files),
                "total": sum(f.total for f in files),
                "files": [f.model_copy(update={"uncovered_ranges": []}) for f in lowest],
                "detail": detail,
                "hint": hint,
            }
        )
        return self.finish(report, ex, stack)

    def install(self, working_directory: str, timeout: int | None = None) -> InstallReport:
        """Install the project's declared dependencies (the setup phase: needs the network)."""

        self._require_network("Install Dependencies")
        directory, stack = self.resolve(working_directory)
        argv = install_argv(stack, directory)
        ex = self.execute(
            argv, directory, self.timeout(self.cfg.install_timeout, timeout), network=True
        )
        base = InstallReport(manager=stack.manager)
        if ex.record is None or ex.tool_missing:
            return self.finish(base.model_copy(update={"status": "unavailable"}), ex, stack)
        ok = ex.exit_code == 0
        report = base.model_copy(
            update={"status": "passed" if ok else "failed", "raw_tail": excerpt(ex.tail, 1500)}
        )
        if not ok and any(word in ex.text.lower() for word in NETWORK_ERRORS):
            report = report.model_copy(update={"hint": NETWORK_HINT})
        return self.finish(report, ex, stack)

    def audit(self, working_directory: str, timeout: int | None = None) -> AuditReport:
        """Check dependencies for known vulnerabilities; ``unavailable`` if no audit tool exists."""

        self._require_network("Dependency Audit")
        directory, stack = self.resolve(working_directory)
        plan = audit_command(stack, directory)
        ex = self.execute(
            plan.argv, directory, self.timeout(self.cfg.audit_timeout, timeout), network=True
        )
        base = AuditReport(tool=plan.tool)
        if ex.record is None or ex.tool_missing:
            return self.finish(base.model_copy(update={"status": "unavailable"}), ex, stack)
        try:
            found = plan.parse(ex.text)
        except ValueError as exc:
            offline = any(word in ex.text.lower() for word in NETWORK_ERRORS)
            hint = NETWORK_HINT if offline else f"The audit output could not be read ({exc})."
            report = base.model_copy(update={"status": "error", "raw_tail": ex.tail, "hint": hint})
            return self.finish(report, ex, stack)
        kept, omitted = sort_and_cap(found, self.cfg.max_diagnostics)
        report = base.model_copy(
            update={
                "status": "failed" if found else "passed",
                "vulnerabilities": kept,
                "omitted": omitted,
            }
        )
        return self.finish(report, ex, stack)

    def _require_network(self, tool: str) -> None:
        if not self.cfg.allow_network:
            raise ToolError(
                f"{tool} needs the network and tools.dev.allow_network is false. "
                "Ask the human to enable it, or install and audit by hand."
            )


def _match(files: list[FileCoverage], wanted: str) -> FileCoverage | None:
    wanted = wanted.strip().lstrip("./")
    if not wanted:
        return None
    matches = [f for f in files if f.path == wanted] or [
        f for f in files if f.path.endswith("/" + wanted)
    ]
    return matches[0] if matches else None
