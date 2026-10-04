import ast
import hashlib
import importlib.util
import random
import sys
from pathlib import Path

from engineering_team.bench.checklib import expect, main, python

FIXTURE = Path(__file__).resolve().parents[1] / "fixture"
MAX_LINES = 30


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def random_orders(count, seed):
    rng = random.Random(seed)
    skus = ["A", "B", "C", "D", 7]
    orders = []
    for number in range(count):
        order = {}
        if rng.random() < 0.95:
            order["id"] = number
        if rng.random() < 0.9:
            order["customer"] = rng.choice(["ann", "bob", "staff", "cy"])
        if rng.random() < 0.5:
            order["status"] = rng.choice(["new", "paid", "cancelled", "shipped"])
        if rng.random() < 0.8:
            order["country"] = rng.choice(["US", "DE", "FR", "JP"])
        if rng.random() < 0.5:
            order["coupon"] = rng.choice(["TEN", "FIVE", "HALF", "NOPE"])
        if rng.random() < 0.15:
            order["strict"] = True
        if rng.random() < 0.97:
            order["items"] = [
                {
                    "sku": rng.choice(skus),
                    "qty": rng.choice([-1, 0, 1, 1, 2, 3, 5, 10]),
                    "price": rng.choice([-2.0, 0.0, 0.99, 4.5, 19.99, 30.0, 49.95, 120.0]),
                }
                for _ in range(rng.randint(0, 4))
            ]
        orders.append(order)
    return orders


def check_behaviour_preserved(ws):
    before = load(FIXTURE / "orders" / "summary.py", "orders_before")
    sys.path.insert(0, str(ws))
    from orders import summary as after

    cases = [
        [],
        [{}],
        [{"status": "cancelled"}],
        [{"items": [{"sku": "A", "qty": 1, "price": 49.99}]}],
    ]
    cases += [random_orders(size, seed) for seed in range(40) for size in (1, 3, 8, 12)]
    for index, orders in enumerate(cases):
        expected, got = before.summarize(orders), after.summarize(orders)
        expect(got == expected, f"case {index} differs:\n  before: {expected}\n  after:  {got}")
        expect(list(got) == list(expected), f"case {index}: the result's keys must stay in order")
        expect(
            list(got["customers"]) == list(expected["customers"]),
            f"case {index}: customer order differs",
        )


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_existing_tests_intact(ws):
    for original in (FIXTURE / "tests").rglob("*.py"):
        found = ws / original.relative_to(FIXTURE)
        expect(
            found.is_file() and digest(found) == digest(original),
            f"{original.relative_to(FIXTURE)} was changed or removed",
        )
    result = python(ws, "-m", "unittest", "discover", "-s", "tests", timeout=60)
    expect(result.returncode == 0, f"the tests fail:\n{result.stderr[-500:]}")


def functions(ws):
    found = []
    for path in sorted((ws / "orders").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found.append(
                    (f"{path.relative_to(ws)}:{node.name}", node.end_lineno - node.lineno + 1)
                )
    return found


def check_functions_are_small(ws):
    found = functions(ws)
    long = [f"{name} ({size} lines)" for name, size in found if size > MAX_LINES]
    expect(not long, f"functions longer than {MAX_LINES} lines: {long}")
    expect(
        len(found) >= 5,
        f"expected the logic to be split into at least 5 functions, found {len(found)}",
    )


def check_public_api(ws):
    sys.path.insert(0, str(ws))
    import inspect

    from orders.summary import summarize

    expect(
        list(inspect.signature(summarize).parameters) == ["orders"],
        "summarize must still take one argument, `orders`",
    )
    expect(summarize([])["revenue"] == 0.0, "summarize([]) must work")


if __name__ == "__main__":
    main()
