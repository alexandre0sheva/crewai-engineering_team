import re

from engineering_team.bench.checklib import expect, free_port, http_request, main, serving


def start(ws, port, *extra):
    return serving(ws, ["-m", "shortener", "--port", str(port), *extra], port)


def shorten(base, url):
    return http_request("POST", f"{base}/shorten", {"url": url})


def check_shorten_and_redirect(ws):
    port = free_port()
    with start(ws, port) as base:
        reply = shorten(base, "https://example.com/a/b?c=1")
        expect(
            reply.status == 201,
            f"first POST /shorten must be 201, was {reply.status}: {reply.text[:200]}",
        )
        body = reply.json()
        expect(re.fullmatch(r"[A-Za-z0-9]{6}", str(body.get("code"))), f"bad code in {body}")
        expect(body.get("url") == "https://example.com/a/b?c=1", f"url missing in {body}")
        expect(
            body.get("short_url") == f"http://127.0.0.1:{port}/{body['code']}",
            f"short_url was {body.get('short_url')}",
        )
        expect("json" in reply.headers.get("content-type", ""), "the response must be JSON")
        jump = http_request("GET", f"{base}/{body['code']}")
        expect(jump.status == 302, f"GET /code must be 302, was {jump.status}")
        expect(
            jump.headers.get("location") == "https://example.com/a/b?c=1",
            f"Location was {jump.headers.get('location')}",
        )


def check_same_url_same_code(ws):
    port = free_port()
    with start(ws, port) as base:
        first = shorten(base, "https://example.com/one")
        again = shorten(base, "https://example.com/one")
        other = shorten(base, "https://example.com/two")
        expect(
            (first.status, again.status, other.status) == (201, 200, 201),
            f"statuses were {(first.status, again.status, other.status)}",
        )
        expect(first.json()["code"] == again.json()["code"], "the same URL must give the same code")
        expect(
            first.json()["code"] != other.json()["code"], "different URLs must give different codes"
        )


def check_validation(ws):
    port = free_port()
    with start(ws, port) as base:
        for body in (
            {},
            {"url": 5},
            {"url": "ftp://example.com/x"},
            {"url": "not a url"},
            {"url": "http://"},
            ["https://example.com"],
        ):
            reply = http_request("POST", f"{base}/shorten", body)
            expect(reply.status == 400, f"{body!r} must be a 400, was {reply.status}")
            expect(
                "error" in reply.json(), f"the 400 body needs an 'error' key: {reply.text[:100]}"
            )
        raw = http_request("POST", f"{base}/shorten", form={"url": "https://example.com"})
        expect(raw.status == 400, f"a non-JSON body must be a 400, was {raw.status}")
        for path in ("/zzzzzz", "/stats/zzzzzz", "/nothing/here"):
            reply = http_request("GET", base + path)
            expect(
                reply.status == 404 and "error" in reply.json(),
                f"GET {path} must be a 404 JSON error, was {reply.status}",
            )


def check_stats(ws):
    port = free_port()
    with start(ws, port) as base:
        code = shorten(base, "https://example.com/stats").json()["code"]
        zero = http_request("GET", f"{base}/stats/{code}").json()
        expect(
            zero == {"code": code, "url": "https://example.com/stats", "hits": 0},
            f"fresh stats were {zero}",
        )
        for _ in range(3):
            http_request("GET", f"{base}/{code}")
        expect(
            http_request("GET", f"{base}/stats/{code}").json()["hits"] == 3,
            "three redirects must count as 3 hits",
        )
        expect(
            http_request("GET", f"{base}/stats/{code}").json()["hits"] == 3,
            "reading stats must not count as a hit",
        )


def check_persistence(ws):
    db = str(ws / "links.db")
    port = free_port()
    with start(ws, port, "--db", db) as base:
        code = shorten(base, "https://example.com/keep").json()["code"]
        http_request("GET", f"{base}/{code}")
    port = free_port()
    with start(ws, port, "--db", db) as base:
        stats = http_request("GET", f"{base}/stats/{code}")
        expect(stats.status == 200, f"the link must survive a restart, stats gave {stats.status}")
        expect(stats.json()["hits"] == 1, f"hits must survive too: {stats.json()}")
        expect(
            shorten(base, "https://example.com/keep").json()["code"] == code,
            "the same URL keeps its code",
        )
        expect(http_request("GET", f"{base}/{code}").status == 302, "the code still redirects")


if __name__ == "__main__":
    main()
