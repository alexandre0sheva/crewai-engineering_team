import unittest
from datetime import datetime

from cronlite import next_run


class ScheduleTests(unittest.TestCase):
    def test_every_quarter_hour(self):
        self.assertEqual(next_run("*/15 * * * *", datetime(2025, 3, 10, 10, 7)), datetime(2025, 3, 10, 10, 15))

    def test_leap_day(self):
        self.assertEqual(next_run("0 0 29 2 *", datetime(2025, 3, 1)), datetime(2028, 2, 29))

    def test_day_of_month_or_weekday(self):
        self.assertEqual(next_run("0 0 15 * 0", datetime(2025, 3, 10)), datetime(2025, 3, 15))

    def test_invalid(self):
        with self.assertRaises(ValueError):
            next_run("61 * * * *", datetime(2025, 1, 1))


if __name__ == "__main__":
    unittest.main()
