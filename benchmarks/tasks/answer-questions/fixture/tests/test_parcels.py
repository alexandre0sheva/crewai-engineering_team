import unittest
from datetime import date, datetime
from decimal import Decimal

from parcels.delivery import add_working_days, delivery_date
from parcels.packing import parcel_weights
from parcels.quote import CannotShip, shipping_cost


class ParcelsTest(unittest.TestCase):
    def test_heavy_orders_are_split_evenly(self):
        self.assertEqual(parcel_weights(Decimal("70")), [Decimal("23.334")] * 3)

    def test_free_shipping_and_destinations(self):
        self.assertEqual(shipping_cost(Decimal("80"), Decimal("2"), "de"), Decimal("0.00"))
        with self.assertRaises(CannotShip):
            shipping_cost(Decimal("10"), Decimal("1"), "US")

    def test_weekends_are_skipped(self):
        self.assertEqual(add_working_days(date(2026, 3, 6), 1), date(2026, 3, 9))
        self.assertEqual(delivery_date(datetime(2026, 3, 5, 16, 0), "NL"), date(2026, 3, 10))


if __name__ == "__main__":
    unittest.main()
