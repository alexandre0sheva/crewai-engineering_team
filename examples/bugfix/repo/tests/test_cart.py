import unittest

from shop.cart import Cart
from shop.report import report


class CartTests(unittest.TestCase):
    def setUp(self):
        self.cart = Cart()
        self.cart.add("pen", 1.5, 4)
        self.cart.add("book", 12.0)

    def test_count_and_total(self):
        self.assertEqual(self.cart.count(), 5)
        self.assertEqual(self.cart.total(), 18.0)

    def test_average_price(self):
        self.assertEqual(self.cart.average_price(), 3.6)

    def test_report(self):
        self.assertEqual(report(self.cart), "Items: 5, total: 18.00, average: 3.60")


if __name__ == "__main__":
    unittest.main()
