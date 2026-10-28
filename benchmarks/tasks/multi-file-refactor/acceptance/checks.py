import ast
import inspect
import json
import shutil
import sys
import tempfile
from pathlib import Path

from engineering_team.bench.checklib import expect, main, python

FIXTURE = Path(__file__).resolve().parents[1] / "fixture"
MODULES = ("quote", "invoice", "receipt")

# Runs in a project directory: evaluates generated cases and prints the results as JSON.
PROBE = r"""
import json, random, sys
from datetime import date, timedelta
from decimal import Decimal
from billing.invoice import build_invoice
from billing.quote import build_quote
from billing.receipt import build_receipt

def show(value):
    return json.loads(json.dumps(value.__dict__, default=str))

rng = random.Random(7)
results = []
for n in range(600):
    count = rng.randint(0 if n % 50 == 0 else 1, 4)
    items = [(f"item {i}", Decimal(rng.randint(1, 99999)) / Decimal(rng.choice([10, 100, 1000])),
              rng.choice([1, 1, 2, 3, 7, 0 if n % 37 == 0 else 1, -1 if n % 91 == 0 else 2]))
             for i in range(count)]
    region = rng.choice(["DE", "FR", "NL", "NL", "DE", "XX" if n % 23 == 0 else "FR"])
    discount = rng.choice(
        [0, 0, 5, 10, 15, 33, 50, 51 if n % 29 == 0 else 12, -1 if n % 31 == 0 else 3]
    )
    paid = Decimal(rng.randint(0, 2000)) / 4
    issued = date(2026, 1, 1) + timedelta(days=rng.randint(0, 400))
    number = "INV-%d" % n if n % 41 else "BAD-%d" % n
    row = []
    for build in (
        lambda: build_quote(items, region, discount),
        lambda: build_invoice(number, issued, items, region, discount),
        lambda: build_receipt(items, region, paid, discount),
    ):
        try:
            row.append(show(build()))
        except Exception as exc:
            row.append(f"{type(exc).__name__}: {exc}")
    results.append(row)
print(json.dumps(results))
"""


def probe(project):
    result = python(project, "-c", PROBE, timeout=60)
    expect(result.returncode == 0, f"the probe failed in {project.name}: {result.stderr[-600:]}")
    return json.loads(result.stdout)


def check_behaviour_preserved(ws):
    with tempfile.TemporaryDirectory(prefix="original-") as scratch:
        original = Path(scratch) / "original"
        shutil.copytree(FIXTURE, original)
        want = probe(original)
    got = probe(ws)
    expect(any(isinstance(cell, str) for row in want for cell in row), "the probe covers errors")
    for n, (a, b) in enumerate(zip(want, got, strict=True)):
        for kind, x, y in zip(MODULES, a, b, strict=True):
            expect(x == y, f"case {n}, {kind}: was {x!r}, now {y!r}")
    cli = python(
        ws, "-m", "billing", "quote", "DE", "--discount", "10", "Pads:34.995:2", "Lab:60:1"
    )
    expect(cli.returncode == 0 and "139.22" in cli.stdout, f"the CLI quote: {cli.stdout!r}")


def _source(ws, name):
    return (ws / "billing" / f"{name}.py").read_text(encoding="utf-8")


def check_one_home(ws):
    pricing = ws / "billing" / "pricing.py"
    expect(pricing.is_file(), "billing/pricing.py does not exist")
    shared = pricing.read_text(encoding="utf-8")
    expect("ROUND_HALF_UP" in shared, "pricing.py does not round half up")
    expect('"0.19"' in shared or "'0.19'" in shared, "pricing.py does not hold the tax rates")
    for name in MODULES:
        text = _source(ws, name)
        tree = ast.parse(text)
        imported = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        } | {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        expect(
            any(m in ("billing.pricing", "pricing") or m.endswith(".pricing") for m in imported)
            or "from . import pricing" in text
            or "from .pricing" in text,
            f"billing/{name}.py does not use billing.pricing",
        )
        for copy in ("ROUND_HALF_UP", "quantize(", "0.19", "0.21"):
            expect(copy not in text, f"billing/{name}.py still has its own copy ({copy})")


def check_public_api(ws):
    sys.path.insert(0, str(ws))
    from billing import invoice, quote, receipt

    expect(invoice.PAYMENT_DAYS == 14, "invoice.PAYMENT_DAYS changed")
    for module, function, cls, params in (
        (quote, "build_quote", "Quote", ["items", "region", "discount_percent"]),
        (
            invoice,
            "build_invoice",
            "Invoice",
            ["number", "issued", "items", "region", "discount_percent"],
        ),
        (receipt, "build_receipt", "Receipt", ["items", "region", "paid", "discount_percent"]),
    ):
        expect(hasattr(module, cls), f"{module.__name__}.{cls} is gone")
        build = getattr(module, function, None)
        expect(callable(build), f"{module.__name__}.{function} is gone")
        signature = inspect.signature(build)
        expect(list(signature.parameters) == params, f"{function}{signature} changed")
        expect(signature.parameters["discount_percent"].default == 0, f"{function} default changed")


def check_tests_intact(ws):
    for path in (FIXTURE / "tests").rglob("*.py"):
        mine = ws / "tests" / path.relative_to(FIXTURE / "tests")
        expect(mine.is_file() and mine.read_text() == path.read_text(), f"{mine.name} was changed")
    result = python(ws, "-m", "unittest", "discover", "-s", "tests", timeout=60)
    expect(result.returncode == 0, f"the tests fail:\n{result.stderr[-600:]}")


if __name__ == "__main__":
    main()
