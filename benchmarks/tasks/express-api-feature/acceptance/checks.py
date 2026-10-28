import json
import re

from engineering_team.bench.checklib import expect, main, toolchain

SEED = [
    {"title": "Kindred", "author": "Octavia E. Butler"},
    {"title": "Dune", "author": "Frank Herbert"},
    {"title": "Solaris", "author": "Stanislaw Lem"},
]

# Starts the team's app on a free port with SEED books, sends each request in order, and prints
# [{status, body}, ...] as JSON. Runs inside the toolchain (Docker: no network but loopback).
DRIVER = """
import { createApp } from "./src/app.ts";
import { BookStore } from "./src/store.ts";
const steps = %s;
const server = createApp(new BookStore(%s)).listen(0);
await new Promise((resolve) => server.once("listening", resolve));
const base = `http://127.0.0.1:${server.address().port}`;
const out = [];
for (const [method, path, body, raw] of steps) {
  const init = { method, headers: {} };
  if (raw !== undefined) { init.headers["content-type"] = "application/json"; init.body = raw; }
  else if (body !== undefined && body !== null) {
    init.headers["content-type"] = "application/json"; init.body = JSON.stringify(body);
  }
  const response = await fetch(base + path, init);
  const text = await response.text();
  let parsed; try { parsed = JSON.parse(text); } catch { parsed = { notJson: text.slice(0, 200) }; }
  out.push({ status: response.status, body: parsed });
}
server.close();
console.log(JSON.stringify(out));
"""


def drive(ws, steps):
    """[(method, path, body=None, raw=None), ...] -> [(status, body), ...]"""

    padded = [list(step) + [None] * (3 - len(step)) for step in steps]
    script = DRIVER % (json.dumps(padded), json.dumps(SEED))
    result = toolchain(ws, "node", "node", "--input-type=module", "-e", script, node_modules=True)
    expect(result.returncode == 0, f"the app did not run: {result.stderr[-800:]}")
    return [(r["status"], r["body"]) for r in json.loads(result.stdout.strip().splitlines()[-1])]


def book(id, title, author, on_loan_to=None):
    return {"id": id, "title": title, "author": author, "onLoanTo": on_loan_to}


def check_lend_and_return(ws):
    got = drive(ws, [
        ("POST", "/books/2/loan", {"member": " ana "}),
        ("GET", "/books/2"),
        ("POST", "/books/2/return"),
        ("GET", "/books/2"),
        ("POST", "/books/2/loan", {"member": "bo"}),
    ])  # fmt: skip
    dune = ("Dune", "Frank Herbert")
    expect(got[0] == (200, book(2, *dune, "ana")), f"loan answered {got[0]}")
    expect(got[1] == (200, book(2, *dune, "ana")), f"GET after the loan: {got[1]}")
    expect(got[2] == (200, book(2, *dune, None)), f"return answered {got[2]}")
    expect(got[3][1] == book(2, *dune, None), f"GET after the return: {got[3]}")
    expect(got[4] == (200, book(2, *dune, "bo")), f"a returned book can be lent again: {got[4]}")


def check_loan_errors(ws):
    got = drive(ws, [
        ("POST", "/books/9/loan", {"member": "ana"}),
        ("POST", "/books/1/loan", {}),
        ("POST", "/books/1/loan", {"member": "   "}),
        ("POST", "/books/1/loan", {"member": "ana"}),
        ("POST", "/books/1/loan", {"member": "bo"}),
        ("POST", "/books/3/return"),
        ("POST", "/books/9/return"),
        ("GET", "/books/1"),
    ])  # fmt: skip
    expect(got[0][0] == 404, f"loan of an unknown book: {got[0]}")
    for answer in got[1:3]:
        expect(
            answer == (400, {"error": "member must be a non-empty string"}), f"bad member: {answer}"
        )
    expect(got[4] == (409, {"error": "book 1 is already on loan"}), f"double loan: {got[4]}")
    expect(got[5] == (409, {"error": "book 3 is not on loan"}), f"return of a free book: {got[5]}")
    expect(got[6][0] == 404, f"return of an unknown book: {got[6]}")
    expect(got[7][1]["onLoanTo"] == "ana", "a refused second loan must not change the borrower")


def check_availability_filter(ws):
    got = drive(ws, [
        ("POST", "/books/3/loan", {"member": "ana"}),
        ("POST", "/books/1/loan", {"member": "bo"}),
        ("GET", "/books?available=true"),
        ("GET", "/books?available=false"),
        ("GET", "/books?available=yes"),
        ("GET", "/books"),
    ])  # fmt: skip
    expect([b["id"] for b in got[2][1]] == [2], f"available=true: {got[2]}")
    expect([b["id"] for b in got[3][1]] == [1, 3], f"available=false: {got[3]}")
    expect(got[4][0] == 400 and "error" in got[4][1], f"available=yes: {got[4]}")
    expect([b["id"] for b in got[5][1]] == [1, 2, 3], f"unfiltered: {got[5]}")


def check_existing_endpoints(ws):
    got = drive(ws, [
        ("GET", "/health"),
        ("GET", "/books"),
        ("GET", "/books/2"),
        ("GET", "/books/0"),
        ("GET", "/books/abc"),
        ("POST", "/books", {"title": " Ubik ", "author": "Philip K. Dick"}),
        ("POST", "/books", {"title": "X"}),
        ("POST", "/books", [1, 2]),
        ("POST", "/books", None, "{oops"),
        ("GET", "/nowhere"),
    ])  # fmt: skip
    expect(got[0] == (200, {"status": "ok"}), f"health: {got[0]}")
    want = [book(i + 1, b["title"], b["author"]) for i, b in enumerate(SEED)]
    expect(got[1] == (200, want), f"GET /books: {got[1]}")
    expect(got[2] == (200, want[1]), f"GET /books/2: {got[2]}")
    expect(got[3][0] == 404 and got[4][0] == 404, f"bad ids: {got[3]} {got[4]}")
    expect(got[5] == (201, book(4, "Ubik", "Philip K. Dick")), f"POST /books: {got[5]}")
    expect(got[6] == (400, {"error": "author must be a non-empty string"}), f"no author: {got[6]}")
    expect(got[7] == (400, {"error": "the body must be a JSON object"}), f"array body: {got[7]}")
    expect(got[8] == (400, {"error": "malformed JSON"}), f"malformed JSON: {got[8]}")
    expect(got[9] == (404, {"error": "not found"}), f"unknown path: {got[9]}")


def check_tests_added(ws):
    own = toolchain(ws, "node", "node", "--test", node_modules=True)
    expect(own.returncode == 0, f"the project's suite must pass:\n{own.stdout[-800:]}")
    found = re.search(r"^\S* ?tests (\d+)", own.stdout, re.MULTILINE)
    expect(found and int(found.group(1)) > 2, "the suite had 2 tests; add tests for loans")
    sources = "\n".join(
        p.read_text(errors="replace")
        for p in ws.rglob("*.[jt]s")
        if "node_modules" not in p.parts and p.match("*test*")
    )
    expect("/loan" in sources and "/return" in sources, "no test sends a loan and a return")


if __name__ == "__main__":
    main()
