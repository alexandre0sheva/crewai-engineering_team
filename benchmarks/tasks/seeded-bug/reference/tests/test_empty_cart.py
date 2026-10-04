import unittest

from shop.cart import Cart
from shop.report import report


class EmptyCartTests(unittest.TestCase):
    def test_average_of_an_empty_cart_is_none(self):
        self.assertIsNone(Cart().average_price())

    def test_report_of_an_empty_cart(self):
        self.assertEqual(report(Cart()), "Items: 0, total: 0.00, average: n/a")


if __name__ == "__main__":
    unittest.main()
