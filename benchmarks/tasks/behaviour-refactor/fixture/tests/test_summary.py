import unittest

from orders.summary import summarize

ORDERS = [
    {"id": 1, "customer": "ann", "items": [{"sku": "A", "qty": 2, "price": 30.0}], "country": "DE"},
    {"id": 2, "customer": "bob", "items": [{"sku": "B", "qty": 1, "price": 10.0}], "coupon": "FIVE"},
    {"id": 3, "customer": "ann", "items": [{"sku": "A", "qty": 1, "price": 30.0}], "status": "cancelled"},
]


class SummaryTests(unittest.TestCase):
    def test_revenue_and_customers(self):
        result = summarize(ORDERS)
        self.assertEqual(result["customers"]["ann"], {"orders": 1, "spent": 71.4})
        self.assertEqual(result["customers"]["bob"], {"orders": 1, "spent": 10.34})
        self.assertEqual(result["revenue"], 81.74)

    def test_counts_and_top_sku(self):
        result = summarize(ORDERS)
        self.assertEqual(result["status_counts"], {"new": 2, "cancelled": 1})
        self.assertEqual(result["top_sku"], "A")
        self.assertEqual(result["best_customer"], "ann")

    def test_empty(self):
        self.assertEqual(summarize([])["revenue"], 0.0)


if __name__ == "__main__":
    unittest.main()
