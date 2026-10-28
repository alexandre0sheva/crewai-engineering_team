import ast
import re
from pathlib import Path

from engineering_team.bench.checklib import expect, main, python

TASK = Path(__file__).resolve().parents[1]
FIXTURE = TASK / "fixture"
RELEASE = FIXTURE / "vendor-updates" / "moneyfmt-2.0.0" / "moneyfmt" / "__init__.py"
# Inputs whose totals are the same in float and in Decimal arithmetic.
CASES = [
    ["Design=1,200.00", "Hosting=49.99"],
    ["Coffee=€3.40", "Cake=4.15", "Tip=0.45"],
    ["Refund=-12.50", "Fee=2.00"],
    ["Big=1,234,567.891"],
    ["Half=0.125"],
    ["One=1"],
    ["Odd=1.005"],
]
EXPECTED = [
    ["Design                       €1,200.00", "Hosting                         €49.99",
     "Total                        €1,249.99"],
    ["Coffee                           €3.40", "Cake                             €4.15",
     "Tip                              €0.45", "Total                            €8.00"],
    ["Refund                         -€12.50", "Fee                              €2.00",
     "Total                          -€10.50"],
    ["Big                      €1,234,567.89", "Total                    €1,234,567.89"],
    ["Half                             €0.13", "Total                            €0.13"],
    ["One                              €1.00", "Total                            €1.00"],
    ["Odd                              €1.01", "Total                            €1.01"],
]  # fmt: skip


def check_upgraded(ws):
    vendored = ws / "vendor" / "moneyfmt" / "__init__.py"
    expect(vendored.is_file(), "vendor/moneyfmt/__init__.py is missing")
    expect(
        vendored.read_bytes() == RELEASE.read_bytes(), "vendor/moneyfmt is not the 2.0.0 release"
    )
    pins = re.findall(r"^\s*moneyfmt\s*==\s*([\w.]+)", (ws / "requirements.txt").read_text(), re.M)
    expect(pins == ["2.0.0"], f"requirements.txt pins {pins or 'nothing'}, not moneyfmt==2.0.0")
    probe = python(
        ws, "-c", "import invoicer, moneyfmt; print(moneyfmt.__version__, moneyfmt.__file__)"
    )
    expect(probe.stdout.startswith("2.0.0 "), f"the project imports {probe.stdout.strip()!r}")
    expect(str((ws / "vendor").resolve()) in probe.stdout, "moneyfmt is not imported from vendor/")


def check_invoices_unchanged(ws):
    for args, want in zip(CASES, EXPECTED, strict=True):
        result = python(ws, "-m", "invoicer", *args)
        expect(result.returncode == 0, f"{args}: exit {result.returncode}: {result.stderr[-400:]}")
        expect(result.stdout.splitlines() == want, f"{args} printed:\n{result.stdout}")


def check_errors_unchanged(ws):
    for args, message in (
        (["Design=lots"], "error: cannot read amount 'lots'"),
        (["no amount"], "error: a line is DESCRIPTION=AMOUNT, not 'no amount'"),
        (["=5"], "error: a line is DESCRIPTION=AMOUNT, not '=5'"),
        ([], 'usage: python -m invoicer "DESCRIPTION=AMOUNT" ...'),
    ):
        result = python(ws, "-m", "invoicer", *args)
        expect(
            result.returncode == 2 and result.stderr.strip() == message and not result.stdout,
            f"{args}: exit {result.returncode}, stderr {result.stderr.strip()!r}",
        )


def check_no_removed_api(ws):
    for path in (ws / "invoicer").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name | ast.Attribute | ast.alias):
                name = (
                    getattr(node, "id", None)
                    or getattr(node, "attr", None)
                    or getattr(node, "name", None)
                )
                expect(name != "MoneyError", f"{path.name} still uses MoneyError")
            if (
                isinstance(node, ast.Call)
                and getattr(node.func, "id", getattr(node.func, "attr", "")) == "format_money"
            ):
                expect(
                    len(node.args) <= 1,
                    f"{path.name}:{node.lineno} passes the currency positionally",
                )


def check_tests_pass(ws):
    for path in (FIXTURE / "tests").rglob("*.py"):
        mine = ws / "tests" / path.relative_to(FIXTURE / "tests")
        expect(mine.is_file() and mine.read_text() == path.read_text(), f"{mine.name} was changed")
    result = python(ws, "-m", "unittest", "discover", "-s", "tests", timeout=60)
    expect(result.returncode == 0, f"the tests fail:\n{result.stderr[-600:]}")
    ran = re.search(r"Ran (\d+) tests?", result.stderr)
    expect(ran and int(ran.group(1)) > 2, "no test was added")


if __name__ == "__main__":
    main()
