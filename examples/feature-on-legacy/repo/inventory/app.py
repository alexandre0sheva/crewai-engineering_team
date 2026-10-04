"""The WSGI application. Routes are matched by hand, the way this service has always done it."""

import json
import re

from inventory import store


def respond(start_response, status, payload):
    body = json.dumps(payload).encode("utf-8")
    start_response(
        status, [("Content-Type", "application/json"), ("Content-Length", str(len(body)))]
    )
    return [body]


def read_json(environ):
    try:
        length = int(environ.get("CONTENT_LENGTH") or 0)
        return json.loads(environ["wsgi.input"].read(length) or b"null")
    except ValueError:
        return None


def valid_item(data):
    if not isinstance(data, dict):
        return False
    name, qty, price = data.get("name"), data.get("qty"), data.get("price")
    if not isinstance(name, str) or not name.strip():
        return False
    if not isinstance(qty, int) or isinstance(qty, bool) or qty < 0:
        return False
    if not isinstance(price, (int, float)) or isinstance(price, bool) or price < 0:
        return False
    return True


def application(environ, start_response):
    method = environ["REQUEST_METHOD"]
    path = environ.get("PATH_INFO", "/")

    if path == "/items" and method == "GET":
        return respond(start_response, "200 OK", store.all_items())

    if path == "/items" and method == "POST":
        data = read_json(environ)
        if not valid_item(data):
            return respond(
                start_response, "400 Bad Request", {"error": "name, qty and price are required"}
            )
        item = store.add(data["name"].strip(), data["qty"], data["price"])
        return respond(start_response, "201 Created", item)

    match = re.fullmatch(r"/items/(\d+)", path)
    if match:
        item = store.get(int(match.group(1)))
        if item is None:
            return respond(start_response, "404 Not Found", {"error": "no such item"})
        if method == "GET":
            return respond(start_response, "200 OK", item)
        if method == "DELETE":
            store.remove(item["id"])
            return respond(start_response, "200 OK", {"deleted": item["id"]})

    return respond(start_response, "404 Not Found", {"error": "not found"})
