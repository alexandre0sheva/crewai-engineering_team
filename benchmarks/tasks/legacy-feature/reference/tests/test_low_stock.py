import unittest

from inventory import store
from tests.test_app import call


class LowStockTests(unittest.TestCase):
    def setUp(self):
        store.reset()
        for name, qty in (("Widget", 3), ("gadget", 3), ("Bolt", 12), ("Nut", 0)):
            call("POST", "/items", {"name": name, "qty": qty, "price": 1})

    def test_default_threshold_and_order(self):
        status, items = call("GET", "/items/low-stock")
        self.assertEqual(status, 200)
        self.assertEqual([i["name"] for i in items], ["Nut", "gadget", "Widget"])

    def test_threshold_is_strict(self):
        self.assertEqual([i["name"] for i in call("GET", "/items/low-stock?threshold=3")[1]], ["Nut"])

    def test_bad_threshold(self):
        for bad in ("abc", "-1", "", "2.5"):
            self.assertEqual(call("GET", f"/items/low-stock?threshold={bad}")[0], 400)


if __name__ == "__main__":
    unittest.main()
