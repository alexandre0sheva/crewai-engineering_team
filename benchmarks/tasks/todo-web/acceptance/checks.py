import re

from engineering_team.bench.checklib import expect, free_port, http_request, main, serving


def start(ws, port):
    return serving(ws, ["app.py", "--port", str(port)], port)


def items(base):
    page = http_request("GET", base + "/").text
    return re.findall(r'<li\b([^>]*\bdata-id="(\d+)"[^>]*)>(.*?)</li>', page, flags=re.S)


def done_ids(base):
    return [int(i) for attrs, i, _ in items(base) if re.search(r'class="[^"]*\bdone\b', attrs)]


def check_page_renders(ws):
    port = free_port()
    with start(ws, port) as base:
        page = http_request("GET", base + "/")
        expect(page.status == 200, f"GET / was {page.status}")
        expect("text/html" in page.headers.get("content-type", ""), "GET / must be text/html")
        expect(
            re.search(
                r'<form[^>]*method="post"[^>]*action="/add"|<form[^>]*action="/add"[^>]*method="post"',
                page.text,
                re.I,
            ),
            "the add form is missing",
        )
        expect(
            re.search(r'<input[^>]*name="text"', page.text), "the form needs an input named 'text'"
        )
        expect(http_request("GET", base + "/nothing").status == 404, "unknown paths are 404")


def check_add_via_form(ws):
    port = free_port()
    with start(ws, port) as base:
        reply = http_request("POST", base + "/add", form={"text": "write the report"})
        expect(
            reply.status == 303 and reply.headers.get("location") == "/",
            f"POST /add gave {reply.status} {reply.headers.get('location')}",
        )
        http_request("POST", base + "/add", form={"text": "second"})
        shown = items(base)
        expect(
            [(i, t.split("<")[0].strip()) for _, i, t in shown]
            == [("1", "write the report"), ("2", "second")],
            f"page items were {shown}",
        )
        api = http_request("GET", base + "/api/todos").json()
        expect(
            api
            == [
                {"id": 1, "text": "write the report", "done": False},
                {"id": 2, "text": "second", "done": False},
            ],
            f"api was {api}",
        )
        blank = http_request("POST", base + "/add", form={"text": "   "})
        expect(blank.status == 303 and len(items(base)) == 2, "blank text must add nothing")


def check_toggle_and_delete(ws):
    port = free_port()
    with start(ws, port) as base:
        for text in ("a", "b", "c"):
            http_request("POST", base + "/add", form={"text": text})
        expect(http_request("POST", base + "/toggle/2").status == 303, "toggle must answer 303")
        expect(done_ids(base) == [2], f"after one toggle done ids were {done_ids(base)}")
        expect(
            [t["done"] for t in http_request("GET", base + "/api/todos").json()]
            == [False, True, False],
            "the API must agree",
        )
        http_request("POST", base + "/toggle/2")
        expect(done_ids(base) == [], "a second toggle must undo it")
        expect(http_request("POST", base + "/delete/1").status == 303, "delete must answer 303")
        expect([i for _, i, _ in items(base)] == ["2", "3"], "todo 1 must be gone")
        expect(
            http_request("POST", base + "/toggle/99").status == 404, "toggling an unknown id is 404"
        )
        expect(
            http_request("POST", base + "/delete/99").status == 404, "deleting an unknown id is 404"
        )


def check_json_api(ws):
    port = free_port()
    with start(ws, port) as base:
        expect(http_request("GET", base + "/api/todos").json() == [], "a fresh list is []")
        created = http_request("POST", base + "/api/todos", {"text": "via api"})
        expect(created.status == 201, f"create must be 201, was {created.status}")
        expect(
            created.json() == {"id": 1, "text": "via api", "done": False},
            f"created was {created.json()}",
        )
        expect(
            "via api" in http_request("GET", base + "/").text,
            "an API-created todo must show on the page",
        )
        for body in ({}, {"text": ""}, {"text": "  "}, {"text": 5}, {"text": None}):
            reply = http_request("POST", base + "/api/todos", body)
            expect(
                reply.status == 400 and "error" in reply.json(),
                f"{body!r} must be a 400 with an error, was {reply.status}",
            )


def check_escaping(ws):
    port = free_port()
    with start(ws, port) as base:
        http_request("POST", base + "/add", form={"text": "<script>alert(1)</script> & more"})
        page = http_request("GET", base + "/").text
        expect("<script>alert(1)</script>" not in page, "todo text must be HTML-escaped")
        expect("&lt;script&gt;" in page, "the escaped text must appear on the page")
        expect(
            http_request("GET", base + "/api/todos").json()[0]["text"]
            == "<script>alert(1)</script> & more",
            "the API returns the raw text",
        )


if __name__ == "__main__":
    main()
