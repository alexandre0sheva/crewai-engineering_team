import re
import shutil
import tempfile
from pathlib import Path

from engineering_team.bench.checklib import expect, main, python

FIXTURE = Path(__file__).resolve().parents[1] / "fixture"


def report(ws, cart_json, name="cart.json"):
    (ws / name).write_text(cart_json)
    return python(ws, "-m", "shop", "report", "--file", name)


def check_empty_cart_report(ws):
    result = report(ws, "[]")
    expect(result.returncode == 0, f"exit {result.returncode}: {result.stderr[-300:]}")
    expect(
        result.stdout == "Items: 0, total: 0.00, average: n/a\n", f"output was {result.stdout!r}"
    )


def check_empty_cart_api(ws):
    import sys

    sys.path.insert(0, str(ws))
    from shop.cart import Cart

    expect(Cart().average_price() is None, "average_price() of an empty cart must be None")
    cart = Cart()
    cart.add("pen", 2.0, 2)
    expect(cart.average_price() == 2.0, "average_price() of a cart with items is unchanged")


def check_reports_unchanged(ws):
    data = (
        '[{"name": "pen", "price": 1.5, "qty": 4}, {"name": "book", "price": 12.0}, '
        '{"name": "cup", "price": 7.25, "qty": 2}]'
    )
    result = report(ws, data)
    expect(
        result.stdout == "Items: 7, total: 32.50, average: 4.64\n", f"report was {result.stdout!r}"
    )
    expect(
        report(ws, '[{"name": "x", "price": 0}]').stdout
        == "Items: 1, total: 0.00, average: 0.00\n",
        "a free item still averages 0.00",
    )
    missing = python(ws, "-m", "shop", "report", "--file", "nope.json")
    expect(
        missing.returncode == 2 and "error: cannot read nope.json" in missing.stderr,
        f"missing file: {missing.returncode} {missing.stderr!r}",
    )
    broken = report(ws, "{oops", name="bad.json")
    expect(
        broken.returncode == 2 and "is not valid JSON" in broken.stderr,
        f"invalid JSON: {broken.returncode} {broken.stderr!r}",
    )


def check_regression_test(ws):
    own = python(ws, "-m", "unittest", "discover", "-s", "tests", timeout=60)
    expect(own.returncode == 0, f"the suite must pass on the fixed code:\n{own.stderr[-500:]}")
    ran = re.search(r"Ran (\d+) tests?", own.stderr)
    expect(
        ran and int(ran.group(1)) > 3,
        "the suite had 3 tests; a regression test must add at least one",
    )
    with tempfile.TemporaryDirectory(prefix="unfixed-") as scratch:
        unfixed = Path(scratch) / "project"
        shutil.copytree(FIXTURE, unfixed)
        shutil.rmtree(unfixed / "tests")
        shutil.copytree(
            ws / "tests", unfixed / "tests", ignore=shutil.ignore_patterns("__pycache__")
        )
        before = python(unfixed, "-m", "unittest", "discover", "-s", "tests", timeout=60)
    expect(
        before.returncode != 0,
        "the team's tests also pass on the unfixed code: none reproduces the bug",
    )


if __name__ == "__main__":
    main()
