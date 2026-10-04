import unittest

from shortener.store import Store


class StoreTests(unittest.TestCase):
    def test_same_url_same_code(self):
        store = Store()
        code, created = store.shorten("https://example.com")
        self.assertTrue(created)
        self.assertEqual(store.shorten("https://example.com"), (code, False))

    def test_visit_counts_hits(self):
        store = Store()
        code, _ = store.shorten("https://example.com")
        store.visit(code)
        store.visit(code)
        self.assertEqual(store.stats(code)["hits"], 2)
        self.assertIsNone(store.visit("nope00"))


if __name__ == "__main__":
    unittest.main()
