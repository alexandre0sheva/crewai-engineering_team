"""Linters, type checkers, formatters, and builds as ``DiagnosticReport``s."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from engineering_team.devtools.detect import Stack
from engineering_team.devtools.models import Diagnostic, DiagnosticKind, DiagnosticReport
from engineering_team.devtools.parsers.diagnostics import sort_and_cap
from engineering_team.devtools.plans_static import (
    SUPPORTED_FORMATTERS,
    SUPPORTED_LINTERS,
    SUPPORTED_TYPECHECKERS,
    build_argv,
    build_parser,
    format_command,
    lint_command,
    typecheck_command,
)
from engineering_team.devtools.runner_base import BaseRunner, Execution
from engineering_team.tools.support import ToolError


class StaticMixin(BaseRunner):
    def lint(
        self,
        working_directory: str,
        paths: Sequence[str] = (),
        tool: str = "",
        timeout: int | None = None,
    ) -> DiagnosticReport:
        directory, stack = self.resolve(working_directory)
        name = tool or stack.lint
        if not name:
            raise ToolError(_none_detected("linter", stack, SUPPORTED_LINTERS))
        targets = self.checked_paths(directory, paths)
        plan = lint_command(name, directory, targets, str(directory))
        ex = self.execute(plan.argv, directory, self.timeout(self.cfg.lint_timeout, timeout))
        return self._diagnostics("lint", name, ex, stack, plan.parse)

    def typecheck(
        self,
        working_directory: str,
        paths: Sequence[str] = (),
        tool: str = "",
        timeout: int | None = None,
    ) -> DiagnosticReport:
        directory, stack = self.resolve(working_directory)
        name = tool or stack.typecheck
        if not name:
            raise ToolError(_none_detected("type checker", stack, SUPPORTED_TYPECHECKERS))
        targets = self.checked_paths(directory, paths)
        plan = typecheck_command(name, directory, targets, str(directory))
        ex = self.execute(plan.argv, directory, self.timeout(self.cfg.typecheck_timeout, timeout))
        return self._diagnostics("typecheck", name, ex, stack, plan.parse)

    def build(self, working_directory: str, timeout: int | None = None) -> DiagnosticReport:
        directory, stack = self.resolve(working_directory)
        parse = build_parser(stack, str(directory))
        ex = self.execute(
            build_argv(stack), directory, self.timeout(self.cfg.build_timeout, timeout)
        )
        return self._diagnostics("build", " ".join(stack.build or ()), ex, stack, parse)

    def format_code(
        self,
        working_directory: str,
        paths: Sequence[str] = (),
        tool: str = "",
        *,
        write: bool = False,
        timeout: int | None = None,
    ) -> DiagnosticReport:
        directory, stack = self.resolve(working_directory)
        name = tool or stack.format
        if not name:
            raise ToolError(_none_detected("formatter", stack, SUPPORTED_FORMATTERS))
        targets = self.checked_paths(directory, paths)
        plan = format_command(name, directory, targets, write=write, root=str(directory))
        ex = self.execute(plan.argv, directory, self.timeout(self.cfg.format_timeout, timeout))
        base = DiagnosticReport(kind="format", tool=name)
        if ex.record is None or ex.tool_missing:
            return self.finish(base.model_copy(update={"status": "unavailable"}), ex, stack)
        if write:
            ok = ex.exit_code == 0
            summary = (ex.text.strip().splitlines() or ["formatted"])[-1]
            report = base.model_copy(
                update={
                    "status": "passed" if ok else "error",
                    "raw_tail": "" if ok else ex.tail,
                    "hint": f"{summary}. Use Workspace Changes to see what changed."
                    if ok
                    else "The formatter failed; see the output.",
                }
            )
            return self.finish(report, ex, stack)
        files = [self.under(stack.directory, f) or f for f in plan.parse(ex.text)]
        crashed = ex.exit_code not in (0, 1) and not files
        unparsed = ex.exit_code == 1 and not files  # it says changes are needed, not which
        report = base.model_copy(
            update={
                "status": "error" if crashed else "failed" if files or unparsed else "passed",
                "files": files,
                "raw_tail": ex.tail if crashed or unparsed else "",
                "hint": f"Fix with Format Code mode='write' (changes {len(files)} file(s))."
                if files
                else "Changes are needed but the formatter's file list was not recognised."
                if unparsed
                else None,
            }
        )
        return self.finish(report, ex, stack)

    def _diagnostics(
        self,
        kind: DiagnosticKind,
        tool: str,
        ex: Execution,
        stack: Stack,
        parse: Callable[[str], list[Diagnostic]],
    ) -> DiagnosticReport:
        base = DiagnosticReport(kind=kind, tool=tool)
        if ex.record is None or ex.tool_missing:
            return self.finish(base.model_copy(update={"status": "unavailable"}), ex, stack)
        try:
            found = parse(ex.text)
        except ValueError as exc:
            report = base.model_copy(
                update={
                    "status": "error",
                    "raw_tail": ex.tail,
                    "hint": f"Output unreadable ({exc}).",
                }
            )
            return self.finish(report, ex, stack)
        found = [d.model_copy(update={"file": self.under(stack.directory, d.file)}) for d in found]
        errors = sum(d.severity == "error" for d in found)
        warnings = sum(d.severity == "warning" for d in found)
        kept, omitted = sort_and_cap(found, self.cfg.max_diagnostics)
        crashed = ex.exit_code != 0 and not found  # failed without saying why: show the output
        status = "error" if crashed else "failed" if errors else "passed"
        report = base.model_copy(
            update={
                "status": status,
                "errors": errors,
                "warnings": warnings,
                "diagnostics": kept,
                "omitted": omitted,
                "raw_tail": ex.tail if crashed else "",
                "hint": "The tool failed without reporting a problem; see the output."
                if crashed
                else None,
            }
        )
        return self.finish(report, ex, stack)


def _none_detected(what: str, stack: Stack, supported: Sequence[str]) -> str:
    return f"No {what} detected for {stack.describe()}. Pass tool= one of: {', '.join(supported)}."
