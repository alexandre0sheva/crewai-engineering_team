import unittest
from datetime import date

from ledgerly.util.dates import looks_like_date, months_between, parse_bank_date
from ledgerly.util.text import fold, shorten, slug, squash, strip_reference


class TextTest(unittest.TestCase):
    def test_squash_and_fold(self):
        self.assertEqual(squash("  Albert  Heijn   1234 "), "Albert Heijn 1234")
        self.assertEqual(fold("Café  Noël"), "cafe noel")

    def test_references_are_stripped(self):
        self.assertEqual(strip_reference("Huur maart Kenmerk: 99812 "), "Huur maart")
        self.assertEqual(strip_reference("Spotify ref 7781"), "Spotify")

    def test_shorten_and_slug(self):
        self.assertEqual(shorten("A rather long name", 8), "A rathe…")
        self.assertEqual(shorten("short", 8), "short")
        self.assertEqual(slug("Eating out!"), "eating-out")
        with self.assertRaises(ValueError):
            shorten("x", 1)


class DatesTest(unittest.TestCase):
    def test_bank_dates(self):
        self.assertEqual(parse_bank_date("20260304"), date(2026, 3, 4))
        self.assertEqual(parse_bank_date("04-03-2026"), date(2026, 3, 4))
        self.assertEqual(parse_bank_date("04.03.2026"), date(2026, 3, 4))
        with self.assertRaises(ValueError):
            parse_bank_date("March 4")

    def test_months_between(self):
        self.assertEqual(
            months_between(date(2025, 11, 30), date(2026, 2, 1)),
            ["2025-11", "2025-12", "2026-01", "2026-02"],
        )
        self.assertEqual(months_between(date(2026, 2, 1), date(2026, 1, 1)), [])

    def test_looks_like_date(self):
        self.assertTrue(looks_like_date("2026-03-04"))
        self.assertTrue(looks_like_date("04/03/2026"))
        self.assertFalse(looks_like_date("12,50"))


if __name__ == "__main__":
    unittest.main()
