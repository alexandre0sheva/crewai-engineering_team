import io
import json
import re
import sys

from engineering_team.bench.checklib import expect, main, python


def client(ws):
    sys.path.insert(0, str(ws))
    from wsgiref.util import setup_testing_defaults

    from inventory.app import application

    def call(method, target, body=None):
        path, _, query = target.partition("?")
        environ = {"REQUEST_METHOD": method, "PATH_INFO": path, "QUERY_STRING": query}
        setup_testing_defaults(environ)
        data = json.dumps(body).encode() if body is not None else b""
        environ["wsgi.input"] = io.BytesIO(data)
        environ["CONTENT_LENGTH"] = str(len(data))
        seen = {}

        def start_response(status, headers):
            seen["status"] = int(status.split()[0])

        raw = b"".join(application(environ, start_response))
        return seen["status"], json.loads(raw)

    return call


STOCK = (("Widget", 3), ("gadget", 3), ("Bolt", 12), ("Nut", 0), ("Screw", 9), ("Washer", 10))


def seed(call):
    for name, qty in STOCK:
        status, _ = call("POST", "/items", {"name": name, "qty": qty, "price": 1.5})
        expect(status == 201, "seeding through POST /items failed")


def names(call, target):
    status, body = call("GET", target)
    expect(status == 200, f"GET {target} was {status}: {body}")
    expect(isinstance(body, list), f"GET {target} must return a list, got {body!r}")
    return [item["name"] for item in body]


def check_low_stock_report(ws):
    call = client(ws)
    seed(call)
    expect(
        names(call, "/items/low-stock") == ["Nut", "gadget", "Widget", "Screw"],
        f"default list was {names(call, '/items/low-stock')}",
    )
    expect(
        names(call, "/items/low-stock?threshold=3") == ["Nut"], "the threshold is strict: qty < 3"
    )
    expect(names(call, "/items/low-stock?threshold=0") == [], "threshold 0 gives an empty list")
    expect(
        names(call, "/items/low-stock?threshold=100")
        == ["Nut", "gadget", "Widget", "Screw", "Washer", "Bolt"],
        "threshold 100 lists everything in order",
    )
    status, body = call("GET", "/items/low-stock?threshold=4")
    expect(
        set(body[0]) == {"id", "name", "qty", "price"},
        f"items must keep their fields, got {body[0]}",
    )


def check_low_stock_validation(ws):
    call = client(ws)
    seed(call)
    for bad in ("abc", "-1", "", "2.5", "1e2", " "):
        status, body = call("GET", f"/items/low-stock?threshold={bad}")
        expect(status == 400, f"threshold={bad!r} must be a 400, was {status}")
        expect(
            isinstance(body, dict) and body.get("error"), f"the 400 body needs an 'error': {body!r}"
        )
    expect(call("GET", "/items/low-stock?threshold=5")[0] == 200, "threshold=5 is valid")


def check_existing_behaviour(ws):
    call = client(ws)
    status, created = call("POST", "/items", {"name": " Widget ", "qty": 3, "price": 2})
    expect(
        status == 201 and created == {"id": 1, "name": "Widget", "qty": 3, "price": 2},
        f"create gave {status} {created}",
    )
    call("POST", "/items", {"name": "Bolt", "qty": 0, "price": 0})
    expect([i["id"] for i in call("GET", "/items")[1]] == [1, 2], "GET /items lists by id")
    expect(call("GET", "/items/2")[1]["name"] == "Bolt", "GET /items/<id>")
    expect(call("GET", "/items/9") == (404, {"error": "no such item"}), "an unknown item is a 404")
    expect(call("DELETE", "/items/1") == (200, {"deleted": 1}), "DELETE returns the id")
    expect(call("GET", "/items/1")[0] == 404, "a deleted item is gone")
    for bad in (
        {},
        {"name": "x"},
        {"name": "x", "qty": -1, "price": 1},
        {"name": "x", "qty": True, "price": 1},
        {"name": "x", "qty": 1, "price": "1"},
    ):
        expect(call("POST", "/items", bad)[0] == 400, f"{bad!r} must stay a 400")
    expect(call("GET", "/elsewhere") == (404, {"error": "not found"}), "unknown paths stay 404")
    expect(call("PUT", "/items") == (404, {"error": "not found"}), "unsupported methods stay 404")


def run_suite(ws):
    return python(ws, "-m", "unittest", "discover", "-s", "tests", timeout=60)


def check_existing_tests_pass(ws):
    result = run_suite(ws)
    expect(result.returncode == 0, f"the test suite fails:\n{result.stderr[-600:]}")
    for name in (
        "test_create_and_list",
        "test_rejects_bad_item",
        "test_get_and_delete",
        "test_unknown_path",
    ):
        expect(
            name in (ws / "tests" / "test_app.py").read_text(),
            f"the existing test {name} was removed",
        )


def check_tests_added(ws):
    result = run_suite(ws)
    ran = re.search(r"Ran (\d+) tests?", result.stderr)
    expect(ran and result.returncode == 0, f"the suite must run and pass:\n{result.stderr[-400:]}")
    expect(
        int(ran.group(1)) >= 6,
        f"the suite had 4 tests; it must now have at least 6, it has {ran.group(1)}",
    )


if __name__ == "__main__":
    main()
