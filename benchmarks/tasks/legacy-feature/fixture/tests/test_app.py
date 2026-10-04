import io
import json
import unittest
from wsgiref.util import setup_testing_defaults

from inventory import store
from inventory.app import application


def call(method, path, body=None):
    environ = {"REQUEST_METHOD": method, "PATH_INFO": path}
    setup_testing_defaults(environ)
    data = json.dumps(body).encode() if body is not None else b""
    environ["wsgi.input"] = io.BytesIO(data)
    environ["CONTENT_LENGTH"] = str(len(data))
    seen = {}

    def start_response(status, headers):
        seen["status"] = int(status.split()[0])

    payload = b"".join(application(environ, start_response))
    return seen["status"], json.loads(payload)


class ItemTests(unittest.TestCase):
    def setUp(self):
        store.reset()

    def test_create_and_list(self):
        status, item = call("POST", "/items", {"name": "Widget", "qty": 3, "price": 2.5})
        self.assertEqual(status, 201)
        self.assertEqual(item["id"], 1)
        self.assertEqual(call("GET", "/items")[1], [item])

    def test_rejects_bad_item(self):
        self.assertEqual(call("POST", "/items", {"name": "", "qty": 1, "price": 1})[0], 400)

    def test_get_and_delete(self):
        call("POST", "/items", {"name": "Bolt", "qty": 1, "price": 0.1})
        self.assertEqual(call("GET", "/items/1")[0], 200)
        self.assertEqual(call("DELETE", "/items/1")[1], {"deleted": 1})
        self.assertEqual(call("GET", "/items/1")[0], 404)

    def test_unknown_path(self):
        self.assertEqual(call("GET", "/nope")[0], 404)


if __name__ == "__main__":
    unittest.main()
